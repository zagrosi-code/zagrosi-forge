from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from detached_test_support import (
    assert_canonical_json_file,
    assert_no_detached_section_record,
    canonical_json_bytes_for_test,
    copy_implementation_plugin,
    detached_record_arguments,
    domain_sha256_for_test,
    file_sha256,
    handoff_receipt_for_test,
    handoff_verification_for_test,
    make_detached_record_fixture,
    protected_source_observation_for_test,
)
from forge_test_helpers import (
    load_zagrosi_module,
    run_raw,
    run_script_raw,
)


def test_detached_record_derives_and_reverifies_exact_privileged_evidence_name_and_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contracts = {
        "section-26-publication-wire-and-decision-store": (
            "s26_privileged_darwin_apfs_gate",
            "evidence/s26-privileged-darwin-apfs-gate-handoff-receipt-v1.json",
        ),
        "section-28-scoped-native-and-external-composition": (
            "s28_privileged_darwin_apfs_gate",
            "evidence/s28-privileged-darwin-apfs-gate-handoff-receipt-v1.json",
        ),
    }
    for section, (name, relative) in contracts.items():
        fixture = make_detached_record_fixture(tmp_path / section, section=section)
        required = fixture.implementation_root / relative
        required.parent.mkdir(parents=True, exist_ok=True)
        required.write_bytes(
            b'{"schema":"unit12-privileged-darwin-apfs-gate-handoff-receipt-v1"}\n'
        )
        required.chmod(0o600)
        alternate = fixture.implementation_root / "evidence" / "alternate-result.json"
        alternate.write_bytes(required.read_bytes())
        alternate.chmod(0o600)

        module = load_zagrosi_module(fixture.script)
        verified: list[str] = []

        def verify_stored(*args):
            verified.append(args[-1])
            raw = required.read_bytes()
            return json.loads(raw), raw

        captured: list[tuple[dict, int]] = []
        monkeypatch.setattr(module.handoff_host, "verify_stored_privileged_handoff", verify_stored)
        monkeypatch.setattr(
            module.output,
            "print_json",
            lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
        )
        args = module.cli.build_parser().parse_args(detached_record_arguments(fixture))
        assert args.func(args) == 0
        assert verified == [section, section, section, section, section, section]
        pinner = assert_canonical_json_file(Path(captured[-1][0]["pinner_path"]))
        assert pinner["evidence_rows"] == [
            {
                "name": name,
                "path": relative,
                "sha256": file_sha256(required),
                "size": required.stat().st_size,
            }
        ]

        wrong_name = run_script_raw(
            fixture.script,
            *detached_record_arguments(fixture, "--evidence-row", f"wrong_name={relative}"),
        )
        assert wrong_name.returncode != 0
        assert json.loads(wrong_name.stdout)["error_code"] == "reserved-evidence-row"

        wrong_path = run_script_raw(
            fixture.script,
            *detached_record_arguments(fixture, "--evidence-row", f"{name}=evidence/alternate-result.json"),
        )
        assert wrong_path.returncode != 0
        assert json.loads(wrong_path.stdout)["error_code"] == "reserved-evidence-row"

        caller_exact = run_script_raw(
            fixture.script,
            *detached_record_arguments(fixture, "--evidence-row", f"{name}={relative}"),
        )
        assert caller_exact.returncode != 0
        assert json.loads(caller_exact.stdout)["error_code"] == "reserved-evidence-row"


def test_detached_record_rejects_copied_raw_privileged_result(tmp_path: Path) -> None:
    fixture = make_detached_record_fixture(tmp_path)
    raw_result = fixture.implementation_root / "evidence" / "copied-root-result.json"
    raw_result.write_bytes(
        b'{"schema":"unit12-privileged-darwin-apfs-gate-result-v1","verdict":"PASS"}\n'
    )
    raw_result.chmod(0o600)

    rejected = run_script_raw(
        fixture.script,
        *detached_record_arguments(
            fixture,
            "--evidence-row",
            "copied_root_result=evidence/copied-root-result.json",
        ),
    )

    assert rejected.returncode != 0
    assert json.loads(rejected.stdout)["error_code"] == "raw-privileged-evidence-forbidden"
    assert_no_detached_section_record(fixture)


@pytest.mark.parametrize(
    "selector_args",
    (
        (),
        ("--section", "section-26-publication-wire-and-decision-store"),
        ("--section", "26"),
        ("--section", "s26"),
        ("--section", "S26", "S28"),
        ("--section", "S26", "--section", "S28"),
    ),
)
def test_implement_evidence_handoff_selector_errors_are_silent_exit_two(
    tmp_path: Path,
    selector_args: tuple[str, ...],
) -> None:
    result = run_raw(
        "implement-evidence-handoff",
        "--implementation-root",
        str(tmp_path / "private-detached-root"),
        *selector_args,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("section_token", "section"),
    (
        ("S26", "section-26-publication-wire-and-decision-store"),
        ("S28", "section-28-scoped-native-and-external-composition"),
    ),
)
def test_implement_evidence_handoff_uses_exact_transport_and_create_once_replay(
    tmp_path: Path,
    monkeypatch,
    section_token: str,
    section: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / section)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-{section}",
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
    calls: list[tuple[list[str], bytes, float, int, int]] = []

    def fake_bounded_child(
        argv: list[str],
        input_bytes: bytes,
        *,
        cwd_fd: int,
        timeout_seconds: float,
        stdout_cap: int,
        stderr_cap: int,
    ) -> tuple[int, bytes, bytes]:
        calls.append((argv, input_bytes, timeout_seconds, stdout_cap, stderr_cap))
        assert (os.fstat(cwd_fd).st_dev, os.fstat(cwd_fd).st_ino) == (
            fixture.target.stat().st_dev,
            fixture.target.stat().st_ino,
        )
        if argv == module.handoff_wire.handoff_root_argv(contract):
            request = json.loads(input_bytes)
            assert set(request) == module.detached_contract.HANDOFF_REQUEST_FIELDS
            assert input_bytes == canonical_json_bytes_for_test(request)
            assert len(input_bytes) <= 16 * 1024
            request_without_self = {key: value for key, value in request.items() if key != "self_digest"}
            assert request["self_digest"] == domain_sha256_for_test(
                b"unit12-privileged-darwin-apfs-gate-handoff-request-v1-self\0",
                canonical_json_bytes_for_test(request_without_self)[:-1],
            )
            request_final_wire_digest = domain_sha256_for_test(
                b"unit12-privileged-darwin-apfs-gate-handoff-request-v1-final-wire\0",
                input_bytes[:-1],
            )
            assert request_final_wire_digest != "sha256:" + hashlib.sha256(input_bytes).hexdigest()
            receipt_raw = handoff_receipt_for_test(
                module,
                config,
                contract,
                input_bytes,
                source_observation,
            )
            receipt = json.loads(receipt_raw)
            assert receipt["handoff_request_final_wire_digest"] == request_final_wire_digest
            receipt_without_self_and_signature = {
                key: value for key, value in receipt.items() if key not in {"self_digest", "signature_b64u"}
            }
            assert receipt["self_digest"] == domain_sha256_for_test(
                b"unit12-privileged-darwin-apfs-gate-handoff-receipt-v1-self\0",
                canonical_json_bytes_for_test(receipt_without_self_and_signature)[:-1],
            )
            receipt_final_wire_digest = domain_sha256_for_test(
                b"unit12-privileged-darwin-apfs-gate-handoff-receipt-v1-final-wire\0",
                receipt_raw[:-1],
            )
            assert receipt_final_wire_digest != "sha256:" + hashlib.sha256(receipt_raw).hexdigest()
            return 0, receipt_raw, b""
        assert argv == module.handoff_wire.handoff_verifier_argv(contract)
        request_size = int.from_bytes(input_bytes[:4], "big")
        request_raw = input_bytes[4 : 4 + request_size]
        receipt_offset = 4 + request_size
        receipt_size = int.from_bytes(input_bytes[receipt_offset : receipt_offset + 4], "big")
        receipt_raw = input_bytes[receipt_offset + 4 : receipt_offset + 4 + receipt_size]
        assert receipt_offset + 4 + receipt_size == len(input_bytes)
        return 0, handoff_verification_for_test(module, config, contract, request_raw, receipt_raw), b""

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

    assert parsed.func(parsed) == 0
    assert emitted[-1][1] == 0
    assert emitted[-1][0] == {
        "schema": "zagrosi-privileged-evidence-handoff-result-v1",
        "section": section_token,
        "evidence_name": contract["evidence_name"],
        "evidence_path": contract["evidence_path"],
        "sha256": file_sha256(fixture.implementation_root / contract["evidence_path"]),
        "size": (fixture.implementation_root / contract["evidence_path"]).stat().st_size,
        "status": "created",
    }
    assert parsed.func(parsed) == 0
    assert emitted[-1][0]["status"] == "reopened"
    assert [call[0] for call in calls] == [
        module.handoff_wire.handoff_root_argv(contract),
        module.handoff_wire.handoff_verifier_argv(contract),
        module.handoff_wire.handoff_root_argv(contract),
        module.handoff_wire.handoff_verifier_argv(contract),
    ]
    assert calls[0][2:] == (30.0, 64 * 1024, 64 * 1024)
    assert calls[1][2:] == (10.0, 4 * 1024, 64 * 1024)
    module.handoff_wire.verify_handoff_command_identities(contract)
    assert_canonical_json_file(fixture.implementation_root / contract["evidence_path"])
