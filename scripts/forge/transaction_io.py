"""Forge transaction io."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import ctypes
import errno
import os
import stat
import sys

from . import detached_contract as _detached_contract
from . import handoff_wire as _handoff_wire
from . import models as _models
from . import processes as _processes
from . import secure_io as _secure_io

def section_record_transaction_dir(root_fd: int, *, create: bool = False) -> int | None:
    pinners_fd = _secure_io.open_relative_directory(root_fd, "pinners")
    name = Path(_detached_contract.SECTION_RECORD_TRANSACTION_DIR).name
    try:
        try:
            transaction_fd = os.open(name, _processes._directory_open_flags(), dir_fd=pinners_fd)
        except FileNotFoundError:
            if not create:
                return None
            os.mkdir(name, 0o700, dir_fd=pinners_fd)
            os.fsync(pinners_fd)
            transaction_fd = os.open(name, _processes._directory_open_flags(), dir_fd=pinners_fd)
        observed = os.fstat(transaction_fd)
        if (
            not stat.S_ISDIR(observed.st_mode)
            or stat.S_IMODE(observed.st_mode) != 0o700
            or observed.st_uid != os.getuid()
        ):
            os.close(transaction_fd)
            raise _models.DetachedImplementationError(
                "unsafe-section-record-transaction",
                "Section-record transaction path must be an owner-0700 directory.",
            )
        reopened_fd = os.open(name, _processes._directory_open_flags(), dir_fd=pinners_fd)
        try:
            if _secure_io._fd_identity(reopened_fd) != _secure_io._fd_identity(transaction_fd):
                os.close(transaction_fd)
                raise _models.DetachedImplementationError(
                    "unsafe-section-record-transaction",
                    "Section-record transaction directory changed while it was opened.",
                )
        finally:
            os.close(reopened_fd)
        return transaction_fd
    finally:
        os.close(pinners_fd)


def remove_section_record_transaction_dir(root_fd: int) -> None:
    pinners_fd = _secure_io.open_relative_directory(root_fd, "pinners")
    try:
        os.rmdir(Path(_detached_contract.SECTION_RECORD_TRANSACTION_DIR).name, dir_fd=pinners_fd)
        os.fsync(pinners_fd)
    finally:
        os.close(pinners_fd)


def write_new_fixed_file_at(root_fd: int, name: str, raw: bytes) -> None:
    if "/" in name or name in {"", ".", ".."}:
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            "Section-record transaction file name is not fixed.",
        )
    if len(raw) > _detached_contract.DETACHED_JSON_CAP:
        raise _models.DetachedImplementationError(
            "detached-file-too-large",
            f"Section-record transaction file exceeds its {_detached_contract.DETACHED_JSON_CAP}-byte cap: {name}",
        )
    file_fd: int | None = None
    try:
        file_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=root_fd,
        )
        _secure_io._write_all(file_fd, raw)
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = None
        os.fsync(root_fd)
        reopened = _secure_io.read_single_link_regular_at(root_fd, name, cap=_detached_contract.DETACHED_JSON_CAP, require_mode=0o600)
        if reopened != raw:
            raise _models.DetachedImplementationError(
                "detached-write-mismatch",
                f"Section-record transaction file changed after durable write: {name}",
            )
    finally:
        if file_fd is not None:
            os.close(file_fd)


def rename_fixed_file_no_replace_at(root_fd: int, source: str, destination: str) -> None:
    for name in (source, destination):
        if "/" in name or name in {"", ".", ".."}:
            raise _models.DetachedImplementationError(
                "invalid-section-record-transaction",
                "Section-record publication file name is not fixed.",
            )
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    ctypes.set_errno(0)
    if sys.platform == "darwin":
        rename_exclusive = getattr(libc, "renameatx_np", None)
        if rename_exclusive is None:
            raise _models.DetachedImplementationError(
                "unsupported-section-record-publication",
                "Atomic no-replace section-record publication is unavailable on this host.",
            )
        rename_exclusive.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(
            root_fd,
            source_bytes,
            root_fd,
            destination_bytes,
            0x00000004,  # Darwin RENAME_EXCL.
        )
    elif sys.platform.startswith("linux"):
        rename_exclusive = getattr(libc, "renameat2", None)
        if rename_exclusive is None:
            raise _models.DetachedImplementationError(
                "unsupported-section-record-publication",
                "Atomic no-replace section-record publication is unavailable on this host.",
            )
        rename_exclusive.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(
            root_fd,
            source_bytes,
            root_fd,
            destination_bytes,
            0x00000001,  # Linux RENAME_NOREPLACE.
        )
    else:
        raise _models.DetachedImplementationError(
            "unsupported-section-record-publication",
            "Atomic no-replace section-record publication is unavailable on this host.",
        )
    if result != 0:
        observed_errno = ctypes.get_errno()
        if observed_errno == errno.EEXIST:
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                f"Section-record publication target already exists: {destination}",
                transaction_member=destination,
            )
        raise _models.DetachedImplementationError(
            "unsupported-section-record-publication"
            if observed_errno in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}
            else "unsafe-section-record-transaction",
            f"Atomic no-replace section-record publication failed: {source} -> {destination}",
            transaction_member=destination,
            errno=observed_errno,
        )


def publish_section_record_staged_pinner(transaction_fd: int, pinner_raw: bytes) -> None:
    _secure_io.load_canonical_json_bytes(pinner_raw, "Section-record staged pinner")
    write_new_fixed_file_at(transaction_fd, "pinner.tmp", pinner_raw)
    rename_fixed_file_no_replace_at(transaction_fd, "pinner.tmp", "pinner.json")
    os.fsync(transaction_fd)
    reopened = _secure_io.read_single_link_regular_at(
        transaction_fd,
        "pinner.json",
        cap=_detached_contract.DETACHED_JSON_CAP,
        require_mode=0o600,
    )
    if reopened != pinner_raw:
        raise _models.DetachedImplementationError(
            "detached-write-mismatch",
            "Section-record staged pinner changed after atomic publication.",
        )


def unlink_fixed_file_at(root_fd: int, name: str, *, missing_ok: bool = False) -> None:
    try:
        os.unlink(name, dir_fd=root_fd)
    except FileNotFoundError:
        if not missing_ok:
            raise
        return
    os.fsync(root_fd)


def publish_section_record_transaction(
    root_fd: int,
    transaction_fd: int,
    payload: dict[str, Any],
    expected_base_raw: bytes,
) -> bytes:
    _handoff_wire.require_exact_fields(payload, _detached_contract.SECTION_RECORD_TRANSACTION_FIELDS, "Section-record transaction")
    raw = _handoff_wire.canonical_json_bytes(payload)
    if payload.get("base_state_sha256") != _handoff_wire.sha256_digest(expected_base_raw):
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            "Section-record journal base digest does not match its exact publication base.",
        )
    write_new_fixed_file_at(transaction_fd, "transaction.write.tmp", raw)
    rename_fixed_file_no_replace_at(
        transaction_fd,
        "transaction.write.tmp",
        "transaction.tmp",
    )
    os.fsync(transaction_fd)
    transaction, reopened_tmp = _secure_io.load_canonical_json_at(transaction_fd, "transaction.tmp")
    _handoff_wire.require_exact_fields(transaction, _detached_contract.SECTION_RECORD_TRANSACTION_FIELDS, "Section-record transaction")
    _, current_state_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    if reopened_tmp != raw or current_state_raw != expected_base_raw:
        raise _models.DetachedImplementationError(
            "section-record-state-conflict",
            "Section-record journal or exact base state changed before publication.",
        )
    rename_fixed_file_no_replace_at(
        transaction_fd,
        "transaction.tmp",
        "transaction.json",
    )
    os.fsync(transaction_fd)
    _, reopened = _secure_io.load_canonical_json_at(transaction_fd, "transaction.json")
    if reopened != raw:
        raise _models.DetachedImplementationError(
            "detached-write-mismatch",
            "Section-record transaction journal changed after publication.",
        )
    return raw


def publish_section_record_rollback(
    transaction_fd: int,
    expected_transaction_raw: bytes,
) -> None:
    if section_record_entry_stat(transaction_fd, "rollback.json") is not None:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Section-record transaction already contains a rollback marker.",
        )
    _, observed_raw = _secure_io.load_canonical_json_at(transaction_fd, "transaction.json")
    if observed_raw != expected_transaction_raw:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Section-record transaction changed before durable rollback publication.",
        )
    os.replace(
        "transaction.json",
        "rollback.json",
        src_dir_fd=transaction_fd,
        dst_dir_fd=transaction_fd,
    )
    os.fsync(transaction_fd)
    _, reopened_raw = _secure_io.load_canonical_json_at(transaction_fd, "rollback.json")
    if reopened_raw != expected_transaction_raw:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Section-record rollback marker changed after durable publication.",
        )


def read_regular_at_allow_links(
    root_fd: int,
    relative: str,
    *,
    allowed_link_counts: set[int],
) -> tuple[bytes, os.stat_result]:
    parent_fd, name = _secure_io.open_relative_parent(root_fd, relative)
    file_fd: int | None = None
    try:
        file_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
        before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.getuid()
            or before.st_nlink not in allowed_link_counts
            or before.st_size > _detached_contract.DETACHED_JSON_CAP
        ):
            raise _models.DetachedImplementationError(
                "unsafe-section-record-transaction",
                f"Section-record staged file metadata is unsafe: {relative}",
                path=relative,
            )
        chunks: list[bytes] = []
        remaining = _detached_contract.DETACHED_JSON_CAP + 1
        while remaining:
            chunk = os.read(file_fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(file_fd)
        def stable(value):
            return (
                    value.st_dev,
                    value.st_ino,
                    value.st_mode,
                    value.st_nlink,
                    value.st_size,
                    value.st_mtime_ns,
                    value.st_ctime_ns,
                )
        if len(raw) > _detached_contract.DETACHED_JSON_CAP or len(raw) != before.st_size or stable(before) != stable(after):
            raise _models.DetachedImplementationError(
                "unsafe-section-record-transaction",
                f"Section-record staged file changed while read: {relative}",
                path=relative,
            )
        return raw, after
    except OSError as exc:
        raise _models.DetachedImplementationError(
            "unsafe-section-record-transaction",
            f"Section-record staged file is missing or unsafe: {relative}",
            path=relative,
        ) from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def replace_state_from_transaction(
    root_fd: int,
    transaction_fd: int,
    expected_raw: bytes,
    replacement: dict[str, Any],
) -> bytes:
    _, current_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    if current_raw != expected_raw:
        raise _models.DetachedImplementationError(
            "section-record-state-conflict",
            "Section-record state no longer equals the transaction's exact compare-and-swap base.",
        )
    replacement_raw = _handoff_wire.canonical_json_bytes(replacement)
    if section_record_entry_stat(transaction_fd, "state.json") is None:
        write_new_fixed_file_at(transaction_fd, "state.json", replacement_raw)
    else:
        staged_replacement = _secure_io.read_single_link_regular_at(
            transaction_fd,
            "state.json",
            cap=_detached_contract.DETACHED_JSON_CAP,
            require_mode=0o600,
        )
        if staged_replacement != replacement_raw:
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Forward state temp is not the exact transaction candidate projection.",
            )
    _, current_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    if current_raw != expected_raw:
        raise _models.DetachedImplementationError(
            "section-record-state-conflict",
            "Section-record state changed before atomic promotion.",
        )
    os.replace("state.json", "zagrosi_implement_state.json", src_dir_fd=transaction_fd, dst_dir_fd=root_fd)
    os.fsync(root_fd)
    _, reopened_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    if reopened_raw != replacement_raw:
        raise _models.DetachedImplementationError(
            "detached-write-mismatch",
            "Section-record state changed after atomic promotion.",
        )
    return replacement_raw


def replace_state_from_rollback(
    root_fd: int,
    transaction_fd: int,
    expected_candidate_raw: bytes,
    base_state: dict[str, Any],
    base_raw: bytes,
) -> None:
    _, current_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    if current_raw == base_raw:
        if section_record_entry_stat(transaction_fd, "state.json") is not None:
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Rollback state temp remained after the exact base state was already published.",
            )
        return
    if current_raw != expected_candidate_raw:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Rollback root state is neither the exact transaction candidate nor exact base.",
        )
    state_entry = section_record_entry_stat(transaction_fd, "state.json")
    if state_entry is None:
        write_new_fixed_file_at(transaction_fd, "state.json", base_raw)
    else:
        staged_base = _secure_io.read_single_link_regular_at(
            transaction_fd,
            "state.json",
            cap=_detached_contract.DETACHED_JSON_CAP,
            require_mode=0o600,
        )
        if staged_base != base_raw:
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Rollback state temp is not the exact transaction base projection.",
            )
    _, current_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    if current_raw != expected_candidate_raw:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Rollback root state changed before exact base replacement.",
        )
    os.replace(
        "state.json",
        "zagrosi_implement_state.json",
        src_dir_fd=transaction_fd,
        dst_dir_fd=root_fd,
    )
    os.fsync(root_fd)
    _, reopened_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    if reopened_raw != base_raw:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Rollback base state changed after atomic replacement.",
        )


def install_staged_section_pinner(
    root_fd: int,
    transaction_fd: int,
    pinner_path: str,
    pinner_raw: bytes,
) -> bool:
    parts = _secure_io._relative_parts(pinner_path)
    if len(parts) != 2 or parts[0] != "pinners":
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            "Section-record pinner path must be an immediate child of pinners/.",
        )
    staged = _secure_io.read_single_link_regular_at(transaction_fd, "pinner.json", cap=_detached_contract.DETACHED_JSON_CAP, require_mode=0o600)
    if staged != pinner_raw:
        raise _models.DetachedImplementationError(
            "section-record-pinner-drift",
            "Staged section pinner bytes do not match the transaction.",
        )
    pinners_fd = _secure_io.open_relative_directory(root_fd, "pinners")
    created = False
    try:
        try:
            os.link(
                "pinner.json",
                parts[1],
                src_dir_fd=transaction_fd,
                dst_dir_fd=pinners_fd,
                follow_symlinks=False,
            )
            created = True
            os.fsync(pinners_fd)
        except FileExistsError:
            existing = _secure_io.read_single_link_regular_at(root_fd, pinner_path, cap=_detached_contract.DETACHED_JSON_CAP, require_mode=0o600)
            if existing != pinner_raw:
                raise _models.DetachedImplementationError(
                    "pinner-conflict",
                    f"Immutable detached receipt already exists with different bytes: {pinner_path}",
                    path=pinner_path,
                )
        reopened, _ = read_regular_at_allow_links(
            root_fd,
            pinner_path,
            allowed_link_counts={1, 2},
        )
        if reopened != pinner_raw:
            raise _models.DetachedImplementationError(
                "detached-write-mismatch",
                "Section pinner changed after atomic link installation.",
            )
        return created
    finally:
        os.close(pinners_fd)


def section_record_entry_stat(root_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def require_safe_section_record_file(
    root_fd: int,
    name: str,
    *,
    allowed_link_counts: set[int],
) -> os.stat_result:
    observed = section_record_entry_stat(root_fd, name)
    if observed is None or (
        not stat.S_ISREG(observed.st_mode)
        or stat.S_IMODE(observed.st_mode) != 0o600
        or observed.st_uid != os.getuid()
        or observed.st_nlink not in allowed_link_counts
        or observed.st_size > _detached_contract.DETACHED_JSON_CAP
    ):
        raise _models.DetachedImplementationError(
            "unsafe-section-record-transaction",
            f"Section-record transaction member metadata is unsafe: {name}",
            transaction_member=name,
        )
    return observed


def section_record_pinner_relation(
    root_fd: int,
    transaction_fd: int,
    pinner_path: str,
    expected_raw: bytes,
) -> tuple[bool, os.stat_result, os.stat_result]:
    staged_raw, staged_stat = read_regular_at_allow_links(
        transaction_fd,
        "pinner.json",
        allowed_link_counts={1, 2},
    )
    final_raw, final_stat = read_regular_at_allow_links(
        root_fd,
        pinner_path,
        allowed_link_counts={1, 2},
    )
    if staged_raw != expected_raw or final_raw != expected_raw:
        raise _models.DetachedImplementationError(
            "section-record-pinner-drift",
            "Staged and final section pinner bytes are not the exact transaction pinner.",
        )
    same_inode = _secure_io._fd_identity_from_stat(staged_stat) == _secure_io._fd_identity_from_stat(final_stat)
    if same_inode:
        if staged_stat.st_nlink != 2 or final_stat.st_nlink != 2:
            raise _models.DetachedImplementationError(
                "invalid-section-record-transaction",
                "Invocation-created staged and final pinners must be the same exact two-link inode.",
            )
        return True, staged_stat, final_stat
    if staged_stat.st_nlink != 1 or final_stat.st_nlink != 1:
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            "Adopted staged and final pinners must be distinct exact single-link files.",
        )
    return False, staged_stat, final_stat


def verify_section_record_commit_closure(
    root_fd: int,
    transaction_fd: int,
    expected_transaction_raw: bytes,
    pinner_path: str,
    expected_pinner_raw: bytes,
    expected_candidate_raw: bytes,
) -> None:
    _, transaction_raw = _secure_io.load_canonical_json_at(transaction_fd, "transaction.json")
    if transaction_raw != expected_transaction_raw:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Section-record journal changed before its commit point.",
        )
    _, current_state_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    if current_state_raw != expected_candidate_raw:
        raise _models.DetachedImplementationError(
            "section-record-state-conflict",
            "Section-record state changed before its commit point.",
        )
    staged_raw, _ = read_regular_at_allow_links(
        transaction_fd,
        "pinner.json",
        allowed_link_counts={1, 2},
    )
    if staged_raw != expected_pinner_raw:
        raise _models.DetachedImplementationError(
            "section-record-pinner-drift",
            "Published staged pinner changed before its commit point.",
        )
    section_record_pinner_relation(
        root_fd,
        transaction_fd,
        pinner_path,
        expected_pinner_raw,
    )


def unlink_invocation_created_section_pinner(
    root_fd: int,
    transaction_fd: int,
    pinner_path: str,
    pinner_raw: bytes,
) -> None:
    created, _, _ = section_record_pinner_relation(
        root_fd,
        transaction_fd,
        pinner_path,
        pinner_raw,
    )
    if not created:
        return
    parts = _secure_io._relative_parts(pinner_path)
    pinners_fd = _secure_io.open_relative_directory(root_fd, "pinners")
    try:
        os.unlink(parts[1], dir_fd=pinners_fd)
        os.fsync(pinners_fd)
    finally:
        os.close(pinners_fd)
    staged_raw, staged_stat = read_regular_at_allow_links(
        transaction_fd,
        "pinner.json",
        allowed_link_counts={1},
    )
    if staged_raw != pinner_raw or staged_stat.st_nlink != 1:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Invocation-created final pinner unlink did not leave its exact single-link stage.",
        )


def execute_section_record_rollback(
    root_fd: int,
    transaction_fd: int,
    transaction_raw: bytes,
    pinner_path: str,
    pinner_raw: bytes,
    candidate_raw: bytes,
    base_state: dict[str, Any],
    base_raw: bytes,
    validate_base_closure,
) -> bool:
    transaction_present = section_record_entry_stat(transaction_fd, "transaction.json") is not None
    rollback_present = section_record_entry_stat(transaction_fd, "rollback.json") is not None
    if transaction_present == rollback_present:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Section-record rollback requires exactly one forward or rollback journal name.",
        )
    _, current_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    if current_raw not in {candidate_raw, base_raw}:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Section-record rollback root state is neither its exact candidate nor exact base.",
        )
    pinner_parts = _secure_io._relative_parts(pinner_path)
    pinners_fd = _secure_io.open_relative_directory(root_fd, "pinners")
    try:
        final_present = section_record_entry_stat(pinners_fd, pinner_parts[1]) is not None
    finally:
        os.close(pinners_fd)
    if transaction_present:
        if final_present:
            section_record_pinner_relation(root_fd, transaction_fd, pinner_path, pinner_raw)
        else:
            staged_raw, staged_stat = read_regular_at_allow_links(
                transaction_fd,
                "pinner.json",
                allowed_link_counts={1},
            )
            if staged_raw != pinner_raw or staged_stat.st_nlink != 1:
                raise _models.DetachedImplementationError(
                    "section-record-recovery-required",
                    "Rollback without a final pinner requires its exact single-link stage.",
                )
        publish_section_record_rollback(transaction_fd, transaction_raw)
        if section_record_entry_stat(transaction_fd, "state.json") is not None:
            staged_candidate = _secure_io.read_single_link_regular_at(
                transaction_fd,
                "state.json",
                cap=_detached_contract.DETACHED_JSON_CAP,
                require_mode=0o600,
            )
            if current_raw != base_raw or staged_candidate != candidate_raw:
                raise _models.DetachedImplementationError(
                    "section-record-recovery-required",
                    "Forward state temp is not the exact candidate staged against the exact rollback base.",
                )
            unlink_fixed_file_at(transaction_fd, "state.json")
    else:
        _, reopened_rollback_raw = _secure_io.load_canonical_json_at(transaction_fd, "rollback.json")
        if reopened_rollback_raw != transaction_raw:
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Section-record rollback marker bytes changed during recovery.",
            )

    pinners_fd = _secure_io.open_relative_directory(root_fd, "pinners")
    try:
        final_present = section_record_entry_stat(pinners_fd, pinner_parts[1]) is not None
    finally:
        os.close(pinners_fd)
    if final_present:
        created_by_invocation, _, _ = section_record_pinner_relation(
            root_fd,
            transaction_fd,
            pinner_path,
            pinner_raw,
        )
        if created_by_invocation:
            unlink_invocation_created_section_pinner(
                root_fd,
                transaction_fd,
                pinner_path,
                pinner_raw,
            )
    else:
        staged_raw, staged_stat = read_regular_at_allow_links(
            transaction_fd,
            "pinner.json",
            allowed_link_counts={1},
        )
        if staged_raw != pinner_raw or staged_stat.st_nlink != 1:
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Rollback without a final pinner requires its exact retained single-link stage.",
            )

    replace_state_from_rollback(
        root_fd,
        transaction_fd,
        candidate_raw,
        base_state,
        base_raw,
    )
    _, rolled_back_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    if rolled_back_raw != base_raw:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Section-record rollback did not close on its exact base state.",
        )
    validate_base_closure()
    _, reopened_rollback_raw = _secure_io.load_canonical_json_at(transaction_fd, "rollback.json")
    if reopened_rollback_raw != transaction_raw:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Section-record rollback marker changed before closure cleanup.",
        )
    staged_raw, staged_stat = read_regular_at_allow_links(
        transaction_fd,
        "pinner.json",
        allowed_link_counts={1},
    )
    if staged_raw != pinner_raw or staged_stat.st_nlink != 1:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Rollback staged pinner changed before closure cleanup.",
        )
    pinners_fd = _secure_io.open_relative_directory(root_fd, "pinners")
    try:
        final_present = section_record_entry_stat(pinners_fd, pinner_parts[1]) is not None
    finally:
        os.close(pinners_fd)
    if final_present:
        created_by_invocation, _, _ = section_record_pinner_relation(
            root_fd,
            transaction_fd,
            pinner_path,
            pinner_raw,
        )
        if created_by_invocation:
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Invocation-created final pinner remained after rollback closure.",
            )
    os.unlink("rollback.json", dir_fd=transaction_fd)
    os.fsync(transaction_fd)
    return cleanup_section_record_transaction_after_commit(root_fd, transaction_fd)


def abort_section_record_transaction(
    root_fd: int,
    transaction_fd: int,
) -> bool:
    journal_removed = section_record_entry_stat(transaction_fd, "transaction.json") is None
    try:
        if not journal_removed:
            os.unlink("transaction.json", dir_fd=transaction_fd)
            journal_removed = True
            os.fsync(transaction_fd)
        return cleanup_section_record_transaction_after_commit(root_fd, transaction_fd)
    except (_models.DetachedImplementationError, OSError):
        try:
            os.close(transaction_fd)
        except OSError:
            pass
        return False


def cleanup_section_record_transaction_after_commit(root_fd: int, transaction_fd: int) -> bool:
    try:
        for name in (
            "state.json",
            "transaction.write.tmp",
            "transaction.tmp",
            "pinner.tmp",
            "pinner.json",
        ):
            unlink_fixed_file_at(transaction_fd, name, missing_ok=True)
        os.close(transaction_fd)
        transaction_fd = -1
        remove_section_record_transaction_dir(root_fd)
        return True
    except (_models.DetachedImplementationError, OSError):
        return False
    finally:
        if transaction_fd >= 0:
            os.close(transaction_fd)


def commit_section_record_transaction(root_fd: int, transaction_fd: int) -> bool:
    journal_removed = False
    try:
        os.unlink("transaction.json", dir_fd=transaction_fd)
        journal_removed = True
        os.fsync(transaction_fd)
    except OSError:
        if not journal_removed:
            raise
        os.close(transaction_fd)
        return False
    return cleanup_section_record_transaction_after_commit(root_fd, transaction_fd)
