"""Forge handoff."""

from __future__ import annotations

from pathlib import Path
import argparse
import os
import subprocess

from . import detached_authority as _detached_authority
from . import detached_context as _detached_context
from . import detached_contract as _detached_contract
from . import handoff_host as _handoff_host
from . import handoff_wire as _handoff_wire
from . import models as _models
from . import processes as _processes
from . import secure_io as _secure_io

def detached_implement_evidence_handoff(args: argparse.Namespace) -> int:
    implementation_root: Path | None = None
    planning_dir: Path | None = None
    section_token = getattr(args, "section", None)
    failure_stage = "internal"
    try:
        contract = _detached_contract.HANDOFF_SECTION_CONTRACTS.get(section_token)
        if contract is None:
            raise _models.DetachedImplementationError(
                "invalid-handoff-section",
                "Privileged evidence handoff supports only the exact Section 26 or Section 28 owner.",
                section=section_token,
            )
        section = contract["section"]
        failure_stage = "authority"
        with _detached_context.open_detached_context(
            None,
            args.implementation_root,
        ) as (implementation_root, root_fd, config, guard, require_lock_authority):
            planning_dir = Path(config["planning_dir"])
            _handoff_host.verify_handoff_readiness(planning_dir, root_fd, config, section)
            _handoff_wire.verify_handoff_command_identities(contract)
            failure_stage = "platform"
            _handoff_host.require_handoff_platform(root_fd)
            failure_stage = "fixed_dependency"
            _handoff_host.require_fixed_handoff_dependencies(contract)
            failure_stage = "authority"
            _, target_fd, source_observation = _handoff_host.open_handoff_target(config, contract)
            target_identity = _secure_io._fd_identity(target_fd)
            try:
                _, request_raw, request_final_wire_digest = _handoff_wire.build_handoff_request(config, contract)
                _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
                _handoff_host.verify_handoff_readiness(planning_dir, root_fd, config, section)
                _handoff_host.recheck_handoff_target(config, contract, target_identity, source_observation)
                failure_stage = "root"
                return_code, receipt_raw, stderr = _processes.run_bounded_child(
                    _handoff_wire.handoff_root_argv(contract),
                    request_raw,
                    cwd_fd=target_fd,
                    timeout_seconds=30.0,
                    stdout_cap=_detached_contract.HANDOFF_RECEIPT_CAP,
                    stderr_cap=_detached_contract.HANDOFF_STDERR_CAP,
                )
                if return_code < 0:
                    raise _models.DetachedImplementationError(
                        "handoff-root-terminated",
                        "Privileged handoff root arm was terminated.",
                    )
                if return_code != 0 or stderr or not receipt_raw or not receipt_raw.endswith(b"\n"):
                    raise _models.DetachedImplementationError(
                        "handoff-root-output-invalid",
                        "Privileged handoff root arm did not return one exact empty-stderr receipt frame.",
                    )
                receipt, receipt_final_wire_digest = _handoff_wire.parse_handoff_receipt(
                    receipt_raw,
                    config,
                    contract,
                    request_final_wire_digest,
                )
                _handoff_host.require_receipt_source_observation(receipt, source_observation)
                failure_stage = "authority"
                if _handoff_host.derive_protected_source_observation(target_fd, contract) != source_observation:
                    raise _models.DetachedImplementationError(
                        "handoff-source-observation-drift",
                        "Protected source changed between root handoff and unprivileged verification.",
                    )
                _handoff_host.recheck_handoff_target(config, contract, target_identity, source_observation)
                failure_stage = "fixed_dependency"
                _handoff_host.require_fixed_handoff_dependencies(contract, target_fd=target_fd)
                failure_stage = "verifier"
                _handoff_host.verify_handoff_with_unprivileged_test(
                    config,
                    contract,
                    request_raw,
                    receipt_raw,
                    request_final_wire_digest,
                    receipt_final_wire_digest,
                    target_fd=target_fd,
                )
                failure_stage = "authority"
                if _handoff_host.derive_protected_source_observation(target_fd, contract) != source_observation:
                    raise _models.DetachedImplementationError(
                        "handoff-source-observation-drift",
                        "Protected source changed during unprivileged handoff verification.",
                    )
                _handoff_host.recheck_handoff_target(config, contract, target_identity, source_observation)
            finally:
                os.close(target_fd)
            failure_stage = "authority"
            _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
            _handoff_host.verify_handoff_readiness(planning_dir, root_fd, config, section)
            _handoff_host.recheck_handoff_target(config, contract, target_identity, source_observation)
            evidence_path = contract["evidence_path"]
            failure_stage = "evidence"
            evidence_preexisted = True
            try:
                _secure_io.read_single_link_regular_at(root_fd, evidence_path, cap=_detached_contract.HANDOFF_RECEIPT_CAP, require_mode=0o600)
            except _models.DetachedImplementationError as exc:
                if exc.code != "unsafe-detached-file":
                    raise
                evidence_preexisted = False
            try:
                written_sha256, written_size, created = _secure_io.write_canonical_json_at(
                    root_fd,
                    evidence_path,
                    receipt,
                    immutable=True,
                    report_created=True,
                )
                reopened, reopened_raw = _secure_io.load_canonical_json_at(root_fd, evidence_path)
                if reopened != receipt or reopened_raw != receipt_raw:
                    raise _models.DetachedImplementationError(
                        "handoff-receipt-drift",
                        "User-owned handoff receipt changed after create-once persistence.",
                    )
                failure_stage = "authority"
                _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
                _handoff_host.verify_handoff_readiness(planning_dir, root_fd, config, section)
                _handoff_host.recheck_handoff_target(config, contract, target_identity, source_observation)
            except Exception:
                if not evidence_preexisted:
                    try:
                        _secure_io.unlink_immutable_json_if_exact_at(root_fd, evidence_path, receipt_raw)
                    except _models.DetachedImplementationError as cleanup_exc:
                        if cleanup_exc.code != "unsafe-detached-file":
                            raise
                raise
            result = {
                "schema": _detached_contract.HANDOFF_RESULT_SCHEMA,
                "section": section_token,
                "evidence_name": contract["evidence_name"],
                "evidence_path": contract["evidence_path"],
                "sha256": written_sha256,
                "size": written_size,
                "status": "created" if created else "reopened",
            }
            if set(result) != _detached_contract.HANDOFF_RESULT_FIELDS:
                raise _models.DetachedImplementationError("invalid-handoff-result", "Handoff result fields are not exact.")
            _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
            require_lock_authority()
            return _handoff_host.emit_canonical_json(result)
    except _models.DetachedImplementationError as exc:
        closed_error_code = _handoff_host.classify_handoff_error(exc, failure_stage)
        error_result, exit_code = _handoff_host.handoff_error_result(section_token, closed_error_code)
        return _handoff_host.emit_canonical_json(error_result, exit_code)
    except (OSError, subprocess.SubprocessError) as exc:
        closed_error_code = _handoff_host.classify_handoff_error(exc, failure_stage)
        error_result, exit_code = _handoff_host.handoff_error_result(section_token, closed_error_code)
        return _handoff_host.emit_canonical_json(error_result, exit_code)
    except Exception:
        error_result, exit_code = _handoff_host.handoff_error_result(section_token, "HANDOFF_INTERNAL_FAILURE")
        return _handoff_host.emit_canonical_json(error_result, exit_code)
