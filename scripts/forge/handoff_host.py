"""Forge handoff host."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib
import os
import platform
import re
import stat
import subprocess
import sys

from . import authority as _authority
from . import detached_authority as _detached_authority
from . import detached_contract as _detached_contract
from . import handoff_wire as _handoff_wire
from . import models as _models
from . import pinners as _pinners
from . import planning_snapshot as _planning_snapshot
from . import processes as _processes
from . import sections as _sections
from . import secure_io as _secure_io
from . import storage as _storage

def require_handoff_platform(root_fd: int) -> None:
    if os.geteuid() == 0:
        raise _models.DetachedImplementationError(
            "unsafe-handoff-caller",
            "Privileged evidence handoff must be initiated by the owning non-root user.",
        )
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise _models.DetachedImplementationError(
            "unsupported-handoff-platform",
            "Privileged evidence handoff requires a Darwin arm64 host.",
        )
    if _detached_contract.HANDOFF_STAT != "/usr/bin/stat":
        raise _models.DetachedImplementationError(
            "handoff-command-drift",
            "The fixed APFS probe path does not match its frozen literal.",
        )
    require_fixed_handoff_executable(_detached_contract.HANDOFF_STAT, allow_multiple_links=True)
    return_code, stdout, stderr = _processes.run_bounded_child(
        [_detached_contract.HANDOFF_STAT, "-f", "%T", "."],
        b"",
        cwd_fd=root_fd,
        timeout_seconds=5.0,
        stdout_cap=64,
        stderr_cap=64,
    )
    if return_code != 0 or stderr or stdout != b"apfs\n":
        raise _models.DetachedImplementationError(
            "unsupported-handoff-platform",
            "Privileged evidence handoff requires an APFS detached root.",
        )


def open_root_owned_nonwritable_directory_chain(path: Path) -> int:
    absolute = _storage.absolute_path_no_follow(path)
    current_fd = os.open(os.sep, _processes._directory_open_flags())
    try:
        root_observed = os.fstat(current_fd)
        if (
            not stat.S_ISDIR(root_observed.st_mode)
            or root_observed.st_uid != 0
            or root_observed.st_gid != 0
            or stat.S_IMODE(root_observed.st_mode) & 0o022
        ):
            raise _models.DetachedImplementationError(
                "unsafe-handoff-dependency",
                "The fixed privileged handoff filesystem root is not root-owned and non-writable.",
            )
        for component in absolute.parts[1:]:
            next_fd = os.open(component, _processes._directory_open_flags(), dir_fd=current_fd)
            observed = os.fstat(next_fd)
            if (
                not stat.S_ISDIR(observed.st_mode)
                or observed.st_uid != 0
                or observed.st_gid != 0
                or stat.S_IMODE(observed.st_mode) & 0o022
            ):
                raise _models.DetachedImplementationError(
                    "unsafe-handoff-dependency",
                    "A fixed privileged handoff dependency ancestor is not root-owned and non-writable.",
                )
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def require_fixed_handoff_executable(raw_path: str, *, allow_multiple_links: bool = False) -> None:
    path = Path(raw_path)
    parent_fd: int | None = None
    executable_fd: int | None = None
    try:
        parent_fd = open_root_owned_nonwritable_directory_chain(path.parent)
        executable_fd = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        observed = os.fstat(executable_fd)
        if (
            not stat.S_ISREG(observed.st_mode)
            or type(observed.st_nlink) is not int
            or observed.st_nlink < 1
            or (not allow_multiple_links and observed.st_nlink != 1)
            or observed.st_uid != 0
            or observed.st_gid != 0
            or stat.S_IMODE(observed.st_mode) & 0o022
            or not (observed.st_mode & 0o111)
        ):
            raise _models.DetachedImplementationError(
                "unsafe-handoff-dependency",
                "A fixed privileged handoff executable is outside its frozen metadata contract.",
            )
    except FileNotFoundError as exc:
        raise _models.DetachedImplementationError(
            "missing-handoff-dependency",
            "A fixed privileged handoff executable is missing.",
        ) from exc
    except OSError as exc:
        raise _models.DetachedImplementationError(
            "unsafe-handoff-dependency",
            "A fixed privileged handoff executable path is unsafe.",
        ) from exc
    finally:
        if executable_fd is not None:
            os.close(executable_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def read_stable_fd(file_fd: int, *, cap: int, label: str) -> bytes:
    before = os.fstat(file_fd)
    if before.st_size < 0 or before.st_size > cap:
        raise _models.DetachedImplementationError(
            "unsafe-handoff-dependency",
            f"{label} exceeds its frozen byte cap.",
        )
    chunks: list[bytes] = []
    remaining = cap + 1
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
            value.st_uid,
            value.st_gid,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
    if len(raw) > cap or len(raw) != before.st_size or stable(before) != stable(after):
        raise _models.DetachedImplementationError(
            "unsafe-handoff-dependency",
            f"{label} changed while its complete bytes were read.",
        )
    return raw


def read_fixed_gate_runner(contract: dict[str, Any]) -> bytes:
    runner = Path(contract["runner"])
    parent_fd: int | None = None
    runner_fd: int | None = None
    try:
        parent_fd = open_root_owned_nonwritable_directory_chain(runner.parent)
        runner_fd = os.open(
            runner.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        observed = os.fstat(runner_fd)
        if (
            not stat.S_ISREG(observed.st_mode)
            or stat.S_IMODE(observed.st_mode) != 0o555
            or observed.st_nlink != 1
            or observed.st_uid != 0
            or observed.st_gid != 0
        ):
            raise _models.DetachedImplementationError(
                "unsafe-handoff-dependency",
                "The fixed privileged gate runner metadata is outside the frozen contract.",
            )
        return read_stable_fd(
            runner_fd,
            cap=_detached_contract.IMPLEMENTATION_SOURCE_CAP,
            label="Fixed privileged gate runner",
        )
    except FileNotFoundError as exc:
        raise _models.DetachedImplementationError(
            "missing-handoff-dependency",
            "The fixed privileged gate runner is missing.",
        ) from exc
    except OSError as exc:
        raise _models.DetachedImplementationError(
            "unsafe-handoff-dependency",
            "The fixed privileged gate runner path is unsafe.",
        ) from exc
    finally:
        if runner_fd is not None:
            os.close(runner_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def require_fixed_handoff_dependencies(contract: dict[str, Any], *, target_fd: int | None = None) -> None:
    _handoff_wire.verify_handoff_command_identities(contract)
    if (
        _detached_contract.HANDOFF_SUDO != "/usr/bin/sudo"
        or _detached_contract.HANDOFF_STAT != "/usr/bin/stat"
        or _detached_contract.HANDOFF_PYTHON != "/usr/local/libexec/santander-unit12-prereqs/python-3.12.13/bin/python3.12"
        or _detached_contract.HANDOFF_GIT != "/usr/local/libexec/santander-unit12-prereqs/git-2.50.1-apple-155"
    ):
        raise _models.DetachedImplementationError(
            "handoff-command-drift",
            "A fixed privileged handoff executable path does not match its frozen literal.",
        )
    for raw_path in (_detached_contract.HANDOFF_SUDO, _detached_contract.HANDOFF_PYTHON, _detached_contract.HANDOFF_GIT):
        require_fixed_handoff_executable(raw_path)
    require_fixed_handoff_executable(_detached_contract.HANDOFF_STAT, allow_multiple_links=True)
    for raw_path in (_detached_contract.HANDOFF_PREREQUISITE_RECEIPT, _detached_contract.HANDOFF_HOST_PROVISIONING_RECEIPT):
        path = Path(raw_path)
        try:
            parent_fd = open_root_owned_nonwritable_directory_chain(path.parent)
        except _models.DetachedImplementationError as exc:
            if not path.parent.exists():
                raise _models.DetachedImplementationError(
                    "missing-handoff-dependency",
                    "A fixed privileged handoff trust-receipt parent is missing.",
                ) from exc
            raise
        try:
            try:
                _secure_io.read_single_link_regular_at(parent_fd, path.name, cap=_detached_contract.HANDOFF_RECEIPT_CAP, require_mode=0o644)
            except _models.DetachedImplementationError as exc:
                try:
                    os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    raise _models.DetachedImplementationError(
                        "missing-handoff-dependency",
                        "A fixed privileged handoff trust receipt is missing.",
                    ) from exc
                raise
            observed = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if observed.st_uid != 0 or observed.st_gid != 0:
                raise _models.DetachedImplementationError(
                    "missing-handoff-dependency",
                    "A fixed privileged handoff trust receipt has unsafe ownership.",
                )
        finally:
            os.close(parent_fd)
    runner_raw = read_fixed_gate_runner(contract)
    if target_fd is not None:
        require_gate_runner_matches_source(contract, target_fd, runner_raw)


def require_gate_runner_matches_source(
    contract: dict[str, Any],
    target_fd: int,
    runner_raw: bytes,
) -> None:
    runner_source_raw = _secure_io.read_single_link_regular_at(
        target_fd,
        contract["runner_source"],
        cap=_detached_contract.IMPLEMENTATION_SOURCE_CAP,
    )
    if (
        runner_raw != runner_source_raw
        or hashlib.sha256(runner_raw).digest() != hashlib.sha256(runner_source_raw).digest()
    ):
        raise _models.DetachedImplementationError(
            "handoff-runner-source-drift",
            "The fixed root-owned gate runner is not byte-identical to its current admitted source.",
        )


@dataclass(frozen=True)
class ProtectedSourceObservation:
    protected_source_root_identity_digest: str
    source_commit: str
    source_tree_sha256: str
    implementation_source_sha256: str
    test_source_sha256: str


def run_protected_source_probe(
    target_fd: int,
    argv: list[str],
    *,
    timeout_seconds: float,
    stdout_cap: int,
) -> bytes:
    try:
        return_code, stdout, stderr = _processes.run_bounded_child(
            argv,
            b"",
            cwd_fd=target_fd,
            timeout_seconds=timeout_seconds,
            stdout_cap=stdout_cap,
            stderr_cap=_detached_contract.HANDOFF_STDERR_CAP,
            child_env=_detached_contract.HANDOFF_GIT_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _models.DetachedImplementationError(
            "handoff-source-probe-unavailable",
            "The fixed protected-source probe could not be spawned or completed.",
        ) from exc
    except _models.DetachedImplementationError as exc:
        if exc.code == "handoff-source-dirty":
            raise
        if exc.code in {
            "handoff-child-timeout",
            "handoff-child-output-cap",
            "handoff-child-termination-unproven",
            "handoff-child-residual-process-group",
        }:
            raise _models.DetachedImplementationError(
                "handoff-source-probe-unavailable",
                "The fixed protected-source probe could not be boundedly completed.",
            ) from exc
        raise _models.DetachedImplementationError(
            "handoff-source-probe-invalid",
            "The fixed protected-source probe exceeded its frozen output contract.",
        ) from exc
    if return_code < 0:
        raise _models.DetachedImplementationError(
            "handoff-source-probe-unavailable",
            "The fixed protected-source probe was terminated.",
        )
    if return_code != 0 or stderr:
        raise _models.DetachedImplementationError(
            "handoff-source-probe-invalid",
            "The fixed protected-source probe did not close with empty-stderr exit zero.",
        )
    return stdout


def derive_protected_source_observation(
    target_fd: int,
    contract: dict[str, Any],
) -> ProtectedSourceObservation:
    before = os.fstat(target_fd)
    root_identity = {
        "device": before.st_dev,
        "gid": before.st_gid,
        "inode": before.st_ino,
        "link_count": before.st_nlink,
        "mode": stat.S_IMODE(before.st_mode),
        "uid": before.st_uid,
    }
    root_identity_digest = _handoff_wire.domain_sha256(
        b"unit12-protected-source-root-identity-v1\0",
        _handoff_wire.handoff_canonical_json_body(root_identity),
    )
    status = run_protected_source_probe(
        target_fd,
        [_detached_contract.HANDOFF_GIT, *_detached_contract.HANDOFF_GIT_STATUS_ARGS],
        timeout_seconds=10.0,
        stdout_cap=1,
    )
    if status != b"":
        raise _models.DetachedImplementationError(
            "handoff-source-dirty",
            "Protected source contains tracked or untracked worktree changes.",
        )
    revision = run_protected_source_probe(
        target_fd,
        [_detached_contract.HANDOFF_GIT, "rev-parse", "--verify", "HEAD^{commit}"],
        timeout_seconds=10.0,
        stdout_cap=41,
    )
    if not re.fullmatch(rb"[0-9a-f]{40}\n", revision):
        raise _models.DetachedImplementationError(
            "handoff-source-revision-invalid",
            "Protected source HEAD is not the exact committed 40-lowerhex revision frame.",
        )
    tree = run_protected_source_probe(
        target_fd,
        [_detached_contract.HANDOFF_GIT, "ls-tree", "-r", "-z", "--full-tree", "HEAD"],
        timeout_seconds=30.0,
        stdout_cap=16_777_216,
    )
    tree_digest = hashlib.sha256(b"unit12-protected-source-tree-v1\0")
    tree_digest.update(len(tree).to_bytes(8, "big"))
    tree_digest.update(tree)

    implementation_digest = hashlib.sha256(contract["implementation_source_domain"])
    implementation_paths = tuple(contract["implementation_sources"])
    if implementation_paths != tuple(sorted(implementation_paths, key=lambda value: value.encode("ascii"))):
        raise _models.DetachedImplementationError(
            "handoff-source-contract-invalid",
            "Protected implementation source paths are not in exact ASCII order.",
        )
    for relative in implementation_paths:
        path_bytes = relative.encode("ascii", errors="strict")
        raw = _secure_io.read_single_link_regular_at(target_fd, relative, cap=_detached_contract.IMPLEMENTATION_SOURCE_CAP)
        implementation_digest.update(len(path_bytes).to_bytes(4, "big"))
        implementation_digest.update(path_bytes)
        implementation_digest.update(hashlib.sha256(raw).digest())
    test_raw = _secure_io.read_single_link_regular_at(target_fd, contract["test"], cap=_detached_contract.IMPLEMENTATION_SOURCE_CAP)
    after = os.fstat(target_fd)
    if (
        before.st_dev,
        before.st_ino,
        before.st_uid,
        before.st_gid,
        before.st_mode,
        before.st_nlink,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_uid,
        after.st_gid,
        after.st_mode,
        after.st_nlink,
    ):
        raise _models.DetachedImplementationError(
            "handoff-source-root-drift",
            "Protected source root metadata changed during source derivation.",
        )
    return ProtectedSourceObservation(
        protected_source_root_identity_digest=root_identity_digest,
        source_commit=revision[:-1].decode("ascii"),
        source_tree_sha256="sha256:" + tree_digest.hexdigest(),
        implementation_source_sha256="sha256:" + implementation_digest.hexdigest(),
        test_source_sha256=_handoff_wire.sha256_digest(test_raw),
    )


def require_receipt_source_observation(
    receipt: dict[str, Any],
    expected: ProtectedSourceObservation,
) -> None:
    mismatches = {
        field
        for field in (
            "protected_source_root_identity_digest",
            "source_commit",
            "source_tree_sha256",
            "implementation_source_sha256",
            "test_source_sha256",
        )
        if receipt.get(field) != getattr(expected, field)
    }
    if mismatches:
        raise _models.DetachedImplementationError(
            "handoff-source-observation-drift",
            "Handoff receipt protected-source fields do not equal the current independent derivation.",
        )


def open_handoff_target(
    config: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[Path, int, ProtectedSourceObservation]:
    target_dir = _storage.absolute_path_no_follow(config["target_dir"])
    if str(target_dir) != config["target_dir"]:
        raise _models.DetachedImplementationError(
            "invalid-detached-config",
            "Detached config target root is not an exact absolute no-follow path.",
        )
    target_fd = _secure_io.open_directory_chain_no_follow(target_dir)
    try:
        actual_target_digest = _authority.target_root_identity_digest(target_fd)
        if actual_target_digest != config.get("target_root_identity_digest"):
            raise _models.DetachedImplementationError(
                "target-root-identity-drift",
                "Protected target root identity no longer matches implement-setup.",
                expected_target_root_identity_digest=config.get("target_root_identity_digest"),
                actual_target_root_identity_digest=actual_target_digest,
            )
        observation = derive_protected_source_observation(target_fd, contract)
        reopened_fd = _secure_io.open_directory_chain_no_follow(target_dir)
        try:
            if (
                _secure_io._fd_identity(reopened_fd) != _secure_io._fd_identity(target_fd)
                or _authority.target_root_identity_digest(reopened_fd) != actual_target_digest
            ):
                raise _models.DetachedImplementationError(
                    "handoff-source-root-drift",
                    "Protected source root changed before privileged evidence handoff.",
                )
        finally:
            os.close(reopened_fd)
        return target_dir, target_fd, observation
    except Exception:
        os.close(target_fd)
        raise


def verify_handoff_with_unprivileged_test(
    config: dict[str, Any],
    contract: dict[str, Any],
    request_raw: bytes,
    receipt_raw: bytes,
    request_final_wire_digest: str,
    receipt_final_wire_digest: str,
    *,
    target_fd: int,
) -> dict[str, Any]:
    verifier_argv = _handoff_wire.handoff_verifier_argv(contract)
    framed_input = (
        len(request_raw).to_bytes(4, "big")
        + request_raw
        + len(receipt_raw).to_bytes(4, "big")
        + receipt_raw
    )
    return_code, stdout, stderr = _processes.run_bounded_child(
        verifier_argv,
        framed_input,
        cwd_fd=target_fd,
        timeout_seconds=10.0,
        stdout_cap=_detached_contract.HANDOFF_VERIFICATION_CAP,
        stderr_cap=_detached_contract.HANDOFF_STDERR_CAP,
    )
    if return_code < 0:
        raise _models.DetachedImplementationError(
            "handoff-verifier-terminated",
            "Unprivileged handoff verifier was terminated.",
        )
    if return_code != 0 or stderr or not stdout or not stdout.endswith(b"\n"):
        raise _models.DetachedImplementationError(
            "handoff-verifier-output-invalid",
            "Unprivileged handoff verifier did not return one exact empty-stderr PASS frame.",
        )
    return _handoff_wire.parse_handoff_verification(
        stdout,
        config,
        contract,
        request_final_wire_digest,
        receipt_final_wire_digest,
    )


def verify_stored_privileged_handoff(
    planning_dir: Path,
    implementation_root: Path,
    root_fd: int,
    config: dict[str, Any],
    guard: _planning_snapshot.FrozenPlanningTree,
    section: str,
) -> tuple[dict[str, Any], bytes]:
    contract = _detached_contract.HANDOFF_CONTRACT_BY_SECTION[section]
    _handoff_wire.verify_handoff_command_identities(contract)
    require_handoff_platform(root_fd)
    require_fixed_handoff_dependencies(contract)
    _, request_raw, request_final_wire_digest = _handoff_wire.build_handoff_request(config, contract)
    receipt, receipt_raw = _secure_io.load_canonical_json_at(root_fd, contract["evidence_path"])
    parsed_receipt, receipt_final_wire_digest = _handoff_wire.parse_handoff_receipt(
        receipt_raw,
        config,
        contract,
        request_final_wire_digest,
    )
    if receipt != parsed_receipt:
        raise _models.DetachedImplementationError(
            "invalid-handoff-receipt",
            "Stored handoff receipt parse changed its canonical object.",
        )
    _, target_fd, source_observation = open_handoff_target(config, contract)
    target_identity = _secure_io._fd_identity(target_fd)
    try:
        require_receipt_source_observation(parsed_receipt, source_observation)
        if derive_protected_source_observation(target_fd, contract) != source_observation:
            raise _models.DetachedImplementationError(
                "handoff-source-observation-drift",
                "Protected source changed immediately before stored-receipt verification.",
            )
        require_fixed_handoff_dependencies(contract, target_fd=target_fd)
        verify_handoff_with_unprivileged_test(
            config,
            contract,
            request_raw,
            receipt_raw,
            request_final_wire_digest,
            receipt_final_wire_digest,
            target_fd=target_fd,
        )
        if derive_protected_source_observation(target_fd, contract) != source_observation:
            raise _models.DetachedImplementationError(
                "handoff-source-observation-drift",
                "Protected source changed during stored-receipt verification.",
            )
    finally:
        os.close(target_fd)
    recheck_handoff_target(config, contract, target_identity, source_observation)
    _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
    return receipt, receipt_raw


def verify_handoff_readiness(
    planning_dir: Path,
    root_fd: int,
    config: dict[str, Any],
    section: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    progress = _sections.check_section_progress(planning_dir)
    if progress["state"] in {"invalid_index", "no_index"} or section not in progress.get("sections", []):
        raise _models.DetachedImplementationError(
            "invalid-handoff-section",
            "Privileged evidence handoff section is absent from the admitted manifest.",
            section=section,
        )
    dependencies = _sections.dependency_graph(planning_dir, progress)
    known = set(progress["sections"])
    unknown_predecessors = sorted(
        {
            predecessor
            for candidate in progress["sections"]
            for predecessor in dependencies.get(candidate, [])
            if predecessor not in known
        }
    )
    if unknown_predecessors:
        raise _models.DetachedImplementationError(
            "unknown-predecessors",
            "Privileged handoff refuses a dependency graph with predecessors absent from the manifest.",
            unknown_predecessors=unknown_predecessors,
        )
    completed_records = _pinners.detached_completed_records(root_fd, config, progress)
    if section in completed_records:
        raise _models.DetachedImplementationError(
            "completed-handoff-section",
            "Privileged handoff is only valid for an incomplete dependency-ready section.",
            section=section,
        )
    incomplete_predecessors = [
        predecessor for predecessor in dependencies.get(section, []) if predecessor not in completed_records
    ]
    if incomplete_predecessors:
        raise _models.DetachedImplementationError(
            "incomplete-handoff-predecessors",
            "Privileged handoff cannot run before every requested-section predecessor pinner closes.",
            section=section,
            incomplete_predecessors=incomplete_predecessors,
        )
    ready = _sections.ready_sections(progress, dependencies, set(completed_records))
    if section not in ready:
        raise _models.DetachedImplementationError(
            "handoff-section-not-ready",
            "Privileged handoff requires the requested section to be dependency-ready and incomplete.",
            section=section,
            ready_sections=ready,
        )
    return progress, completed_records


def recheck_handoff_target(
    config: dict[str, Any],
    contract: dict[str, Any],
    expected_identity: tuple[int, int],
    expected_observation: ProtectedSourceObservation,
) -> None:
    _, target_fd, observation = open_handoff_target(config, contract)
    try:
        if _secure_io._fd_identity(target_fd) != expected_identity or observation != expected_observation:
            raise _models.DetachedImplementationError(
                "handoff-source-root-drift",
                "Protected source root, commit, tree, implementation or test bytes changed during handoff.",
            )
        require_fixed_handoff_dependencies(contract, target_fd=target_fd)
    finally:
        os.close(target_fd)


def emit_canonical_json(payload: dict[str, Any], exit_code: int = 0) -> int:
    sys.stdout.buffer.write(_handoff_wire.canonical_json_bytes(payload))
    sys.stdout.buffer.flush()
    return exit_code


def handoff_error_result(section_token: Any, closed_error_code: str) -> tuple[dict[str, Any], int]:
    if section_token not in _detached_contract.HANDOFF_SECTION_CONTRACTS:
        raise AssertionError("Privileged evidence handoff error requires an admitted public section token.")
    exit_code = _detached_contract.HANDOFF_CLOSED_ERROR_CODES.get(closed_error_code)
    if exit_code is None:
        raise AssertionError("Privileged evidence handoff error code is outside the frozen closed set.")
    result = {
        "schema": _detached_contract.HANDOFF_ERROR_SCHEMA,
        "purpose": _detached_contract.HANDOFF_ERROR_PURPOSE,
        "section": section_token,
        "status": "failed",
        "closed_error_code": closed_error_code,
    }
    if set(result) != _detached_contract.HANDOFF_ERROR_FIELDS or len(_handoff_wire.handoff_canonical_json_bytes(result)) > 4096:
        raise AssertionError("Privileged evidence handoff error fields are not exact.")
    return result, exit_code


def classify_handoff_error(exc: BaseException, stage: str) -> str:
    code = exc.code if isinstance(exc, _models.DetachedImplementationError) else None
    if code == "unsafe-handoff-caller":
        return "HANDOFF_CALLER_REFUSED"
    if code in {
        "invalid-handoff-section",
        "unknown-predecessors",
        "completed-handoff-section",
        "incomplete-handoff-predecessors",
        "handoff-section-not-ready",
    }:
        return "HANDOFF_SECTION_NOT_READY"
    if code == "unsupported-handoff-platform" or stage == "platform":
        return "HANDOFF_PLATFORM_UNAVAILABLE"
    if code in {"missing-handoff-dependency", "handoff-source-probe-unavailable"} or stage == "fixed_dependency":
        return "HANDOFF_FIXED_DEPENDENCY_UNAVAILABLE"
    if stage == "root":
        if code in {
            "handoff-child-timeout",
            "handoff-child-termination-unproven",
            "handoff-child-residual-process-group",
            "handoff-root-terminated",
        } or not isinstance(
            exc, _models.DetachedImplementationError
        ):
            return "HANDOFF_ROOT_UNAVAILABLE"
        return "HANDOFF_ROOT_OUTPUT_INVALID"
    if stage == "verifier":
        if code in {
            "handoff-child-timeout",
            "handoff-child-termination-unproven",
            "handoff-child-residual-process-group",
            "handoff-verifier-terminated",
        } or not isinstance(
            exc, _models.DetachedImplementationError
        ):
            return "HANDOFF_VERIFIER_UNAVAILABLE"
        return "HANDOFF_VERIFIER_OUTPUT_INVALID"
    if stage == "evidence":
        return "HANDOFF_EVIDENCE_CONFLICT"
    if stage == "authority":
        return "HANDOFF_AUTHORITY_INVALID"
    return "HANDOFF_INTERNAL_FAILURE"
