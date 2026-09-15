from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest
from detached_test_support import (
    assert_canonical_json_file,
    canonical_json_bytes_for_test,
    copy_implementation_plugin,
    handoff_receipt_for_test,
    handoff_verification_for_test,
    make_detached_record_fixture,
    protected_source_observation_for_test,
    replace_file,
)
from forge_test_helpers import (
    load_zagrosi_module,
)


@pytest.mark.parametrize(
    ("failure", "expected_exit", "closed_error_code"),
    (
        ("root_stderr", 5, "HANDOFF_ROOT_OUTPUT_INVALID"),
        ("root_partial", 5, "HANDOFF_ROOT_OUTPUT_INVALID"),
        ("root_extra_stdout", 5, "HANDOFF_ROOT_OUTPUT_INVALID"),
        ("root_timeout", 3, "HANDOFF_ROOT_UNAVAILABLE"),
        ("verifier_stderr", 5, "HANDOFF_VERIFIER_OUTPUT_INVALID"),
        ("verifier_partial", 5, "HANDOFF_VERIFIER_OUTPUT_INVALID"),
    ),
)
def test_implement_evidence_handoff_transport_failures_leave_no_user_receipt(
    tmp_path: Path,
    monkeypatch,
    failure: str,
    expected_exit: int,
    closed_error_code: str,
) -> None:
    section_token = "S26"
    section = "section-26-publication-wire-and-decision-store"
    plugin_root = copy_implementation_plugin(tmp_path / failure)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-{failure}",
        section=section,
        plugin_root=plugin_root,
    )
    module = load_zagrosi_module(fixture.script)
    contract = module.detached_contract.HANDOFF_SECTION_CONTRACTS[section_token]
    source_observation = protected_source_observation_for_test(module)
    test_path = fixture.target / contract["test"]
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text("# fixed privileged handoff verifier fixture\n")
    config = assert_canonical_json_file(fixture.implementation_root / "zagrosi_implement_config.json")

    def fake_bounded_child(argv: list[str], input_bytes: bytes, **kwargs) -> tuple[int, bytes, bytes]:
        if argv == module.handoff_wire.handoff_root_argv(contract):
            receipt = handoff_receipt_for_test(
                module,
                config,
                contract,
                input_bytes,
                source_observation,
            )
            if failure == "root_stderr":
                return 0, receipt, b"closed-error\n"
            if failure == "root_partial":
                return 0, receipt[:-1], b""
            if failure == "root_extra_stdout":
                return 0, receipt + b"{}\n", b""
            if failure == "root_timeout":
                raise module.models.DetachedImplementationError(
                    "handoff-child-timeout",
                    "Privileged handoff child exceeded its fixed deadline.",
                )
            return 0, receipt, b""
        request_size = int.from_bytes(input_bytes[:4], "big")
        request_raw = input_bytes[4 : 4 + request_size]
        receipt_offset = 4 + request_size
        receipt_size = int.from_bytes(input_bytes[receipt_offset : receipt_offset + 4], "big")
        receipt_raw = input_bytes[receipt_offset + 4 : receipt_offset + 4 + receipt_size]
        verification = handoff_verification_for_test(module, config, contract, request_raw, receipt_raw)
        if failure == "verifier_stderr":
            return 0, verification, b"closed-error\n"
        if failure == "verifier_partial":
            return 0, verification[:-1], b""
        return 0, verification, b""

    emitted: list[tuple[dict, int]] = []
    monkeypatch.setattr(module.handoff_host, "require_handoff_platform", lambda root_fd: None)
    monkeypatch.setattr(module.handoff_host, "require_fixed_handoff_dependencies", lambda *args, **kwargs: None)

    monkeypatch.setattr(
        module.handoff_host,
        "derive_protected_source_observation",
        lambda target_fd, selected_contract: source_observation,
    )
    monkeypatch.setattr(module.processes, "run_bounded_child", fake_bounded_child)
    monkeypatch.setattr(
        module.handoff_host,
        "emit_canonical_json",
        lambda payload, exit_code=0: emitted.append((payload, exit_code)) or exit_code,
    )
    parsed = module.cli.build_parser().parse_args(
        [
            "implement-evidence-handoff",
            "--implementation-root",
            str(fixture.implementation_root),
            "--section",
            section_token,
        ]
    )

    assert parsed.func(parsed) == expected_exit
    assert emitted[-1][1] == expected_exit
    assert emitted[-1][0] == {
        "schema": "zagrosi-privileged-evidence-handoff-error-v1",
        "purpose": "zagrosi_privileged_evidence_handoff_error",
        "section": section_token,
        "status": "failed",
        "closed_error_code": closed_error_code,
    }
    assert not (fixture.implementation_root / contract["evidence_path"]).exists()


@pytest.mark.parametrize(
    ("failure", "expected_exit", "closed_error_code"),
    (
        ("config", 5, "HANDOFF_AUTHORITY_INVALID"),
        ("source", 5, "HANDOFF_AUTHORITY_INVALID"),
        ("caller", 5, "HANDOFF_CALLER_REFUSED"),
        ("platform", 3, "HANDOFF_PLATFORM_UNAVAILABLE"),
        ("fixed_dependency", 3, "HANDOFF_FIXED_DEPENDENCY_UNAVAILABLE"),
        ("git_output_cap", 3, "HANDOFF_FIXED_DEPENDENCY_UNAVAILABLE"),
        ("internal", 5, "HANDOFF_INTERNAL_FAILURE"),
    ),
)
def test_implement_evidence_handoff_public_failures_are_exact_bounded_and_private(
    tmp_path: Path,
    monkeypatch,
    failure: str,
    expected_exit: int,
    closed_error_code: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / failure)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-{failure}",
        section="section-26-publication-wire-and-decision-store",
        plugin_root=plugin_root,
    )
    module = load_zagrosi_module(fixture.script)
    if failure == "config":
        config_path = fixture.implementation_root / "zagrosi_implement_config.json"
        config = json.loads(config_path.read_bytes())
        config["private_path_mutant"] = str(fixture.planning)
        replace_file(config_path, canonical_json_bytes_for_test(config), mode=0o600)
    elif failure == "source":
        replace_file(
            fixture.script,
            fixture.script.read_bytes() + b"\n# private source mutant\n",
            mode=fixture.script.stat().st_mode & 0o777,
        )
    elif failure == "caller":
        monkeypatch.setattr(
            module.handoff_host,
            "require_handoff_platform",
            lambda root_fd: (_ for _ in ()).throw(
                module.models.DetachedImplementationError("unsafe-handoff-caller", str(fixture.implementation_root))
            ),
        )
    elif failure == "platform":
        monkeypatch.setattr(
            module.handoff_host,
            "require_handoff_platform",
            lambda root_fd: (_ for _ in ()).throw(
                module.models.DetachedImplementationError("unsupported-handoff-platform", str(fixture.target))
            ),
        )
    elif failure == "fixed_dependency":
        monkeypatch.setattr(module.handoff_host, "require_handoff_platform", lambda root_fd: None)
        monkeypatch.setattr(
            module.handoff_host,
            "require_fixed_handoff_dependencies",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                module.models.DetachedImplementationError("missing-handoff-dependency", str(fixture.admission_pinner))
            ),
        )
    elif failure == "git_output_cap":
        monkeypatch.setattr(module.handoff_host, "require_handoff_platform", lambda root_fd: None)
        monkeypatch.setattr(module.handoff_host, "require_fixed_handoff_dependencies", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            module.processes,
            "run_bounded_child",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                module.models.DetachedImplementationError(
                    "handoff-child-output-cap",
                    str(fixture.target / "private-source-cap-detail"),
                )
            ),
        )
    else:
        monkeypatch.setattr(module.handoff_host, "require_handoff_platform", lambda root_fd: None)
        monkeypatch.setattr(
            module.handoff_wire,
            "verify_handoff_command_identities",
            lambda contract: (_ for _ in ()).throw(RuntimeError(str(fixture.planning))),
        )
    emitted: list[tuple[dict, int]] = []
    monkeypatch.setattr(
        module.handoff_host,
        "emit_canonical_json",
        lambda payload, exit_code=0: emitted.append((payload, exit_code)) or exit_code,
    )
    parsed = module.cli.build_parser().parse_args(
        [
            "implement-evidence-handoff",
            "--implementation-root",
            str(fixture.implementation_root),
            "--section",
            "S26",
        ]
    )

    assert parsed.func(parsed) == expected_exit
    assert emitted == [
        (
            {
                "schema": "zagrosi-privileged-evidence-handoff-error-v1",
                "purpose": "zagrosi_privileged_evidence_handoff_error",
                "section": "S26",
                "status": "failed",
                "closed_error_code": closed_error_code,
            },
            expected_exit,
        )
    ]
    wire = module.handoff_wire.handoff_canonical_json_bytes(emitted[0][0])
    assert len(wire) <= 4096
    assert str(fixture.planning).encode() not in wire
    assert str(fixture.implementation_root).encode() not in wire
    assert str(fixture.target).encode() not in wire
    assert str(fixture.admission_pinner).encode() not in wire


def test_implement_evidence_handoff_real_dirty_status_maps_to_authority_invalid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "plugin-copy")
    fixture = make_detached_record_fixture(
        tmp_path / "fixture",
        section="section-26-publication-wire-and-decision-store",
        plugin_root=plugin_root,
    )
    module = load_zagrosi_module(fixture.script)
    fake_git = tmp_path / "git-dirty"
    fake_git.write_text("#!/bin/sh\nprintf '?? dirty\\000'\nsleep 30\n")
    fake_git.chmod(0o755)

    monkeypatch.setattr(module.detached_contract, "HANDOFF_GIT", str(fake_git))
    monkeypatch.setattr(module.handoff_host, "require_handoff_platform", lambda root_fd: None)
    monkeypatch.setattr(module.handoff_host, "require_fixed_handoff_dependencies", lambda *args, **kwargs: None)
    emitted: list[tuple[dict, int]] = []
    monkeypatch.setattr(
        module.handoff_host,
        "emit_canonical_json",
        lambda payload, exit_code=0: emitted.append((payload, exit_code)) or exit_code,
    )
    parsed = module.cli.build_parser().parse_args(
        [
            "implement-evidence-handoff",
            "--implementation-root",
            str(fixture.implementation_root),
            "--section",
            "S26",
        ]
    )

    started = time.monotonic()
    assert parsed.func(parsed) == 5
    assert time.monotonic() - started < 5.0
    assert emitted == [
        (
            {
                "schema": "zagrosi-privileged-evidence-handoff-error-v1",
                "purpose": "zagrosi_privileged_evidence_handoff_error",
                "section": "S26",
                "status": "failed",
                "closed_error_code": "HANDOFF_AUTHORITY_INVALID",
            },
            5,
        )
    ]
    assert not (
        fixture.implementation_root
        / module.detached_contract.HANDOFF_SECTION_CONTRACTS["S26"]["evidence_path"]
    ).exists()


def test_implement_evidence_handoff_immediate_dirty_status_reads_one_byte_and_preserves_authority_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "plugin-copy")
    fixture = make_detached_record_fixture(
        tmp_path / "fixture",
        section="section-26-publication-wire-and-decision-store",
        plugin_root=plugin_root,
    )
    module = load_zagrosi_module(fixture.script)
    fake_git = tmp_path / "git-dirty-immediate"
    fake_git.write_text("#!/bin/sh\nprintf '?? dirty\\000'\n")
    fake_git.chmod(0o755)

    real_poll = subprocess.Popen.poll
    real_read = os.read
    real_killpg = os.killpg
    read_calls: list[tuple[int, bytes]] = []
    signal_attempts: list[int] = []
    observed_processes: list[subprocess.Popen[bytes]] = []
    dirty_byte_seen = False
    transient_poll_remaining = 2
    transient_zero_probe_remaining = 2
    transient_poll_count = 0
    transient_zero_probe_count = 0
    eventual_absence_proofs = 0

    def transient_poll(process: subprocess.Popen[bytes]):
        nonlocal transient_poll_remaining, transient_poll_count
        if process not in observed_processes:
            observed_processes.append(process)
        if dirty_byte_seen and transient_poll_remaining:
            transient_poll_remaining -= 1
            transient_poll_count += 1
            return None
        return real_poll(process)

    def recording_read(fd: int, requested: int) -> bytes:
        nonlocal dirty_byte_seen
        chunk = real_read(fd, requested)
        if chunk:
            read_calls.append((requested, chunk))
        if chunk == b"?":
            dirty_byte_seen = True
        return chunk

    def transient_apple_killpg(process_group: int, sig: int) -> None:
        nonlocal transient_zero_probe_remaining, transient_zero_probe_count, eventual_absence_proofs
        if dirty_byte_seen and sig == 0 and transient_zero_probe_remaining:
            transient_zero_probe_remaining -= 1
            transient_zero_probe_count += 1
            raise PermissionError("transient Apple process-group probe race")
        if dirty_byte_seen and sig == signal.SIGTERM:
            signal_attempts.append(sig)
            raise PermissionError("transient Apple process-group signal race")
        try:
            real_killpg(process_group, sig)
        except ProcessLookupError:
            if dirty_byte_seen and sig == 0:
                eventual_absence_proofs += 1
            raise

    monkeypatch.setattr(module.detached_contract, "HANDOFF_GIT", str(fake_git))
    monkeypatch.setattr(subprocess.Popen, "poll", transient_poll)
    monkeypatch.setattr(os, "read", recording_read)
    monkeypatch.setattr(os, "killpg", transient_apple_killpg)
    monkeypatch.setattr(module.handoff_host, "require_handoff_platform", lambda root_fd: None)
    monkeypatch.setattr(module.handoff_host, "require_fixed_handoff_dependencies", lambda *args, **kwargs: None)
    emitted: list[tuple[dict, int]] = []
    monkeypatch.setattr(
        module.handoff_host,
        "emit_canonical_json",
        lambda payload, exit_code=0: emitted.append((payload, exit_code)) or exit_code,
    )
    parsed = module.cli.build_parser().parse_args(
        [
            "implement-evidence-handoff",
            "--implementation-root",
            str(fixture.implementation_root),
            "--section",
            "S26",
        ]
    )

    assert parsed.func(parsed) == 5
    assert [(requested, chunk) for requested, chunk in read_calls if chunk == b"?"] == [(1, b"?")]
    assert transient_poll_count == 2
    assert transient_zero_probe_count == 2
    assert signal_attempts == [signal.SIGTERM]
    assert eventual_absence_proofs >= 1
    assert observed_processes and observed_processes[0].returncode is not None
    assert emitted == [
        (
            {
                "schema": "zagrosi-privileged-evidence-handoff-error-v1",
                "purpose": "zagrosi_privileged_evidence_handoff_error",
                "section": "S26",
                "status": "failed",
                "closed_error_code": "HANDOFF_AUTHORITY_INVALID",
            },
            5,
        )
    ]
    assert not (
        fixture.implementation_root
        / module.detached_contract.HANDOFF_SECTION_CONTRACTS["S26"]["evidence_path"]
    ).exists()
