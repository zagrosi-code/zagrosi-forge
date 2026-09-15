"""Forge authority."""

from __future__ import annotations

from pathlib import Path
import hashlib
import os
import re
import stat

from . import detached_contract as _detached_contract
from . import handoff_wire as _handoff_wire
from . import markdown as _markdown
from . import models as _models
from . import policy as _policy
from . import processes as _processes
from . import secure_io as _secure_io
from . import storage as _storage

def target_root_identity_digest(target_fd: int) -> str:
    observed = os.fstat(target_fd)
    if not stat.S_ISDIR(observed.st_mode):
        raise _models.DetachedImplementationError(
            "unsafe-target-root-identity",
            "Protected target root descriptor must name a directory.",
        )
    identity = {
        "device": observed.st_dev,
        "gid": observed.st_gid,
        "inode": observed.st_ino,
        "link_count": observed.st_nlink,
        "mode": stat.S_IMODE(observed.st_mode),
        "uid": observed.st_uid,
    }
    digest = hashlib.sha256(b"zagrosi-detached-target-root-identity-v1\0")
    digest.update(_handoff_wire.handoff_canonical_json_body(identity))
    return "sha256:" + digest.hexdigest()


def _fd_ancestry_contains(file_fd: int, expected_identity: tuple[int, int]) -> bool:
    current_fd = os.dup(file_fd)
    try:
        for _ in range(4096):
            current_identity = _secure_io._fd_identity(current_fd)
            if current_identity == expected_identity:
                return True
            parent_fd = os.open("..", _processes._directory_open_flags(), dir_fd=current_fd)
            parent_identity = _secure_io._fd_identity(parent_fd)
            os.close(current_fd)
            current_fd = parent_fd
            if parent_identity == current_identity:
                return False
        raise _models.DetachedImplementationError(
            "unsafe-detached-path",
            "Detached directory ancestry exceeded its bounded traversal limit.",
        )
    finally:
        os.close(current_fd)


def _nearest_existing_directory_no_follow(path: Path) -> tuple[int, bool]:
    candidate = _storage.absolute_path_no_follow(path)
    while True:
        try:
            return _secure_io.open_directory_chain_no_follow(candidate), candidate == path
        except _models.DetachedImplementationError as exc:
            if exc.code != "unsafe-detached-path" or candidate.parent == candidate:
                raise
            candidate = candidate.parent


def ensure_detached_root(
    planning_dir: Path,
    raw_root: str,
    *,
    create: bool,
    planning_root_fd: int | None = None,
) -> tuple[Path, int]:
    root = _storage.absolute_path_no_follow(raw_root)
    planning = _storage.absolute_path_no_follow(planning_dir)
    overlaps = False
    try:
        root.relative_to(planning)
        overlaps = True
    except ValueError:
        pass
    try:
        planning.relative_to(root)
        overlaps = True
    except ValueError:
        pass
    if overlaps:
        raise _models.DetachedImplementationError(
            "detached-root-overlap",
            "Detached implementation root must be disjoint from the frozen planning root.",
            planning_dir=str(planning),
            implementation_root=str(root),
        )
    owned_planning_fd: int | None = None
    nearest_fd: int | None = None
    root_fd: int | None = None
    try:
        if planning_root_fd is None:
            owned_planning_fd = _secure_io.open_directory_chain_no_follow(planning)
            planning_root_fd = owned_planning_fd
        planning_identity = _secure_io._fd_identity(planning_root_fd)
        nearest_fd, root_exists = _nearest_existing_directory_no_follow(root)
        nearest_identity = _secure_io._fd_identity(nearest_fd)
        if _fd_ancestry_contains(nearest_fd, planning_identity) or (
            root_exists and _fd_ancestry_contains(planning_root_fd, nearest_identity)
        ):
            raise _models.DetachedImplementationError(
                "detached-root-overlap",
                "Detached implementation root resolves within, aliases, or contains the frozen planning root.",
                planning_dir=str(planning),
                implementation_root=str(root),
            )
        root_fd = _secure_io.open_directory_chain_no_follow(root, create=create)
        root_identity = _secure_io._fd_identity(root_fd)
        if _fd_ancestry_contains(root_fd, planning_identity) or _fd_ancestry_contains(
            planning_root_fd, root_identity
        ):
            raise _models.DetachedImplementationError(
                "detached-root-overlap",
                "Detached implementation root resolves within, aliases, or contains the frozen planning root.",
                planning_dir=str(planning),
                implementation_root=str(root),
            )
        result_fd = root_fd
        root_fd = None
        return root, result_fd
    finally:
        if root_fd is not None:
            os.close(root_fd)
        if nearest_fd is not None:
            os.close(nearest_fd)
        if owned_planning_fd is not None:
            os.close(owned_planning_fd)


def require_candidate_root_disjoint_from_directory(
    candidate_root: Path,
    protected_path: Path,
    protected_fd: int,
) -> None:
    nearest_fd: int | None = None
    try:
        nearest_fd, candidate_exists = _nearest_existing_directory_no_follow(candidate_root)
        nearest_identity = _secure_io._fd_identity(nearest_fd)
        protected_identity = _secure_io._fd_identity(protected_fd)
        if _fd_ancestry_contains(nearest_fd, protected_identity) or (
            candidate_exists and _fd_ancestry_contains(protected_fd, nearest_identity)
        ):
            raise _models.DetachedImplementationError(
                "detached-root-target-overlap",
                "Detached implementation root must be descriptor-disjoint from the protected target root.",
                implementation_root=str(candidate_root),
                target_dir=str(protected_path),
            )
    finally:
        if nearest_fd is not None:
            os.close(nearest_fd)


def require_open_roots_disjoint(
    implementation_root: Path,
    root_fd: int,
    target_dir: Path,
    target_fd: int,
) -> None:
    if _fd_ancestry_contains(root_fd, _secure_io._fd_identity(target_fd)) or _fd_ancestry_contains(target_fd, _secure_io._fd_identity(root_fd)):
        raise _models.DetachedImplementationError(
            "detached-root-target-overlap",
            "Detached implementation root aliases, contains, or is contained by the protected target root.",
            implementation_root=str(implementation_root),
            target_dir=str(target_dir),
        )


def require_planning_target_disjoint(
    planning_dir: Path,
    planning_fd: int,
    target_dir: Path,
    target_fd: int,
) -> None:
    if _fd_ancestry_contains(planning_fd, _secure_io._fd_identity(target_fd)) or _fd_ancestry_contains(
        target_fd,
        _secure_io._fd_identity(planning_fd),
    ):
        raise _models.DetachedImplementationError(
            "planning-target-overlap",
            "Frozen planning root aliases, contains, or is contained by the protected target root.",
            planning_dir=str(planning_dir),
            target_dir=str(target_dir),
        )


def require_planning_implementation_disjoint(
    planning_dir: Path,
    planning_fd: int,
    implementation_root: Path,
    root_fd: int,
) -> None:
    if _fd_ancestry_contains(planning_fd, _secure_io._fd_identity(root_fd)) or _fd_ancestry_contains(
        root_fd,
        _secure_io._fd_identity(planning_fd),
    ):
        raise _models.DetachedImplementationError(
            "detached-root-overlap",
            "Detached implementation root aliases, contains, or is contained by the frozen planning root.",
            planning_dir=str(planning_dir),
            implementation_root=str(implementation_root),
        )


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def reopen_admission_pinner(
    planning_dir: Path,
    implementation_root: Path,
    raw_path: str,
    *,
    expected_sha256: str,
    planning_root_fd: int,
    implementation_root_fd: int | None = None,
) -> tuple[Path, str, int, str]:
    path = _storage.absolute_path_no_follow(raw_path)
    planning = _storage.absolute_path_no_follow(planning_dir)
    root = _storage.absolute_path_no_follow(implementation_root)
    if _path_is_within(path, planning) or _path_is_within(path, root):
        raise _models.DetachedImplementationError(
            "admission-pinner-overlap",
            "Admission pinner must be a distinct external file outside both planning and implementation roots.",
            admission_pinner_path=str(path),
        )
    parent_fd = _secure_io.open_directory_chain_no_follow(path.parent)
    try:
        if _fd_ancestry_contains(parent_fd, _secure_io._fd_identity(planning_root_fd)) or (
            implementation_root_fd is not None
            and _fd_ancestry_contains(parent_fd, _secure_io._fd_identity(implementation_root_fd))
        ):
            raise _models.DetachedImplementationError(
                "admission-pinner-overlap",
                "Admission pinner resolves inside or aliases the planning or implementation root.",
                admission_pinner_path=str(path),
            )
        payload, raw = _secure_io.load_canonical_json_at(parent_fd, path.name)
    finally:
        os.close(parent_fd)
    actual_sha256 = _handoff_wire.sha256_digest(raw)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_sha256) or actual_sha256 != expected_sha256:
        raise _models.DetachedImplementationError(
            "admission-pinner-drift",
            "Admission pinner bytes no longer match implement-setup.",
            admission_pinner_path=str(path),
            expected_admission_pinner_sha256=expected_sha256,
            actual_admission_pinner_sha256=actual_sha256,
        )
    if set(payload) != _detached_contract.FINAL_ADMISSION_PINNER_FIELDS:
        raise _models.DetachedImplementationError(
            "invalid-admission-pinner",
            "Admission pinner fields do not match dec075-final-pinner-receipt-v1 exactly.",
            admission_pinner_path=str(path),
            missing_fields=sorted(_detached_contract.FINAL_ADMISSION_PINNER_FIELDS - set(payload)),
            extra_fields=sorted(set(payload) - _detached_contract.FINAL_ADMISSION_PINNER_FIELDS),
        )
    if (
        type(payload.get("schema")) is not str
        or payload["schema"] != _detached_contract.FINAL_ADMISSION_PINNER_SCHEMA
        or type(payload.get("verdict")) is not str
        or payload["verdict"] != "PASS"
        or type(payload.get("o_sha256")) is not str
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", payload["o_sha256"])
    ):
        raise _models.DetachedImplementationError(
            "invalid-admission-pinner",
            "Admission pinner schema, verdict, or O digest is invalid.",
            admission_pinner_path=str(path),
        )
    start = payload.get("start")
    end = payload.get("end")
    if type(start) is not dict or type(end) is not dict or start != end:
        raise _models.DetachedImplementationError(
            "invalid-admission-pinner",
            "Admission pinner START and END must be identical admission-state objects.",
            admission_pinner_path=str(path),
        )
    if set(start) != _detached_contract.ADMISSION_STATE_FIELDS:
        raise _models.DetachedImplementationError(
            "invalid-admission-pinner",
            "Admission pinner state fields do not match dec075-admission-state-v1 exactly.",
            admission_pinner_path=str(path),
            missing_fields=sorted(_detached_contract.ADMISSION_STATE_FIELDS - set(start)),
            extra_fields=sorted(set(start) - _detached_contract.ADMISSION_STATE_FIELDS),
        )
    if type(start.get("schema")) is not str or start["schema"] != _detached_contract.ADMISSION_STATE_SCHEMA:
        raise _models.DetachedImplementationError(
            "invalid-admission-pinner",
            "Admission pinner state schema is invalid.",
            admission_pinner_path=str(path),
        )
    digest_fields = ("r_sha256", "p_sha256", "d_sha256", "a_sha256")
    if any(type(start.get(field)) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", start[field]) for field in digest_fields):
        raise _models.DetachedImplementationError(
            "invalid-admission-pinner",
            "Admission pinner state digests must be exact lowercase sha256 values.",
            admission_pinner_path=str(path),
        )
    a_digest = hashlib.sha256(b"dec075-a-v1\0")
    for field in ("r_sha256", "p_sha256", "d_sha256"):
        a_digest.update(bytes.fromhex(start[field].removeprefix("sha256:")))
    expected_a_sha256 = "sha256:" + a_digest.hexdigest()
    if start["a_sha256"] != expected_a_sha256:
        raise _models.DetachedImplementationError(
            "invalid-admission-pinner",
            "Admission pinner A digest does not bind its R, P, and D digests.",
            admission_pinner_path=str(path),
            expected_a_sha256=expected_a_sha256,
            actual_a_sha256=start["a_sha256"],
        )
    try:
        index_raw = _secure_io.read_single_link_regular_at(
            planning_root_fd,
            "sections/index.md",
            cap=_detached_contract.FROZEN_PLANNING_FILE_CAP,
        )
        index_text = index_raw.decode("utf-8", errors="strict")
    except (UnicodeDecodeError, _models.DetachedImplementationError) as exc:
        raise _models.DetachedImplementationError(
            "invalid-admission-pinner",
            "Current planning SECTION_MANIFEST cannot be reopened for admission binding.",
            admission_pinner_path=str(path),
        ) from exc
    sections, manifest_errors = _markdown.parse_numbered_manifest(index_text, "SECTION_MANIFEST", _policy.SECTION_RE, prefix="section-")
    if manifest_errors or not sections:
        raise _models.DetachedImplementationError(
            "invalid-admission-pinner",
            "Current planning SECTION_MANIFEST is invalid for admission binding.",
            admission_pinner_path=str(path),
            manifest_errors=manifest_errors,
        )
    d_digest = hashlib.sha256()
    for section in sections:
        relative = f"sections/{section}.md"
        body = _secure_io.read_single_link_regular_at(planning_root_fd, relative, cap=_detached_contract.FROZEN_PLANNING_FILE_CAP)
        path_bytes = relative.encode("utf-8", errors="strict")
        d_digest.update(len(path_bytes).to_bytes(4, "big"))
        d_digest.update(path_bytes)
        d_digest.update(len(body).to_bytes(8, "big"))
        d_digest.update(body)
    current_d_sha256 = "sha256:" + d_digest.hexdigest()
    if start["d_sha256"] != current_d_sha256:
        raise _models.DetachedImplementationError(
            "invalid-admission-pinner",
            "Admission pinner D digest does not bind the current section corpus.",
            admission_pinner_path=str(path),
            expected_d_sha256=current_d_sha256,
            actual_d_sha256=start["d_sha256"],
        )
    return path, actual_sha256, len(raw), start["a_sha256"]
