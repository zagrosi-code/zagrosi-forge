from __future__ import annotations

import json
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
    run_script_raw,
)


def test_implement_evidence_handoff_failure_writes_only_safe_canonical_stdout(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "plugin-copy")
    fixture = make_detached_record_fixture(
        tmp_path / "fixture",
        section="section-26-publication-wire-and-decision-store",
        plugin_root=plugin_root,
    )
    config_path = fixture.implementation_root / "zagrosi_implement_config.json"
    config = json.loads(config_path.read_bytes())
    config["private_path_mutant"] = str(fixture.planning)
    replace_file(config_path, canonical_json_bytes_for_test(config), mode=0o600)

    result = run_script_raw(
        fixture.script,
        "implement-evidence-handoff",
        "--implementation-root",
        str(fixture.implementation_root),
        "--section",
        "S26",
    )

    assert result.returncode == 5
    assert result.stderr == ""
    assert result.stdout.encode() == canonical_json_bytes_for_test(json.loads(result.stdout))
    assert json.loads(result.stdout) == {
        "schema": "zagrosi-privileged-evidence-handoff-error-v1",
        "purpose": "zagrosi_privileged_evidence_handoff_error",
        "section": "S26",
        "status": "failed",
        "closed_error_code": "HANDOFF_AUTHORITY_INVALID",
    }
    assert len(result.stdout.encode()) <= 4096
    assert str(fixture.planning) not in result.stdout
    assert str(fixture.implementation_root) not in result.stdout


@pytest.mark.parametrize("drift_timing", ("post_verifier", "post_write"))
def test_implement_evidence_handoff_removes_new_receipt_on_late_authority_drift(
    tmp_path: Path,
    monkeypatch,
    drift_timing: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / drift_timing)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-{drift_timing}",
        section="section-26-publication-wire-and-decision-store",
        plugin_root=plugin_root,
    )
    module = load_zagrosi_module(fixture.script)
    contract = module.detached_contract.HANDOFF_SECTION_CONTRACTS["S26"]
    source_observation = protected_source_observation_for_test(module)
    test_path = fixture.target / contract["test"]
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text("# fixed privileged handoff verifier fixture\n")
    config = assert_canonical_json_file(fixture.implementation_root / "zagrosi_implement_config.json")
    mutated = False

    def fake_bounded_child(argv: list[str], input_bytes: bytes, **kwargs) -> tuple[int, bytes, bytes]:
        nonlocal mutated
        if argv == module.handoff_wire.handoff_root_argv(contract):
            return 0, handoff_receipt_for_test(
                module,
                config,
                contract,
                input_bytes,
                source_observation,
            ), b""
        request_size = int.from_bytes(input_bytes[:4], "big")
        request_raw = input_bytes[4 : 4 + request_size]
        receipt_offset = 4 + request_size
        receipt_size = int.from_bytes(input_bytes[receipt_offset : receipt_offset + 4], "big")
        receipt_raw = input_bytes[receipt_offset + 4 : receipt_offset + 4 + receipt_size]
        verification = handoff_verification_for_test(module, config, contract, request_raw, receipt_raw)
        if drift_timing == "post_verifier":
            plan = fixture.planning / "codex-plan.md"
            replace_file(plan, plan.read_bytes() + b"\npost-verifier planning drift\n")
            mutated = True
        return 0, verification, b""

    original_write = module.secure_io.write_canonical_json_at

    def write_then_drift(root_fd, relative, payload, **kwargs):
        nonlocal mutated
        result = original_write(root_fd, relative, payload, **kwargs)
        if drift_timing == "post_write" and relative == contract["evidence_path"]:
            replace_file(
                test_path,
                test_path.read_bytes() + b"# post-write source drift\n",
                mode=test_path.stat().st_mode & 0o777,
            )
            mutated = True
        return result

    emitted: list[tuple[dict, int]] = []
    monkeypatch.setattr(module.handoff_host, "require_handoff_platform", lambda root_fd: None)
    monkeypatch.setattr(module.handoff_host, "require_fixed_handoff_dependencies", lambda *args, **kwargs: None)

    def derive_observation(target_fd, selected_contract):
        if drift_timing == "post_write" and mutated:
            return module.handoff_host.ProtectedSourceObservation(
                protected_source_root_identity_digest=source_observation.protected_source_root_identity_digest,
                source_commit=source_observation.source_commit,
                source_tree_sha256=source_observation.source_tree_sha256,
                implementation_source_sha256=source_observation.implementation_source_sha256,
                test_source_sha256="sha256:" + "cd" * 32,
            )
        return source_observation

    monkeypatch.setattr(
        module.handoff_host,
        "derive_protected_source_observation",
        derive_observation,
    )
    monkeypatch.setattr(module.processes, "run_bounded_child", fake_bounded_child)
    monkeypatch.setattr(module.secure_io, "write_canonical_json_at", write_then_drift)
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
    assert mutated is True
    assert emitted[-1][0] == {
        "schema": "zagrosi-privileged-evidence-handoff-error-v1",
        "purpose": "zagrosi_privileged_evidence_handoff_error",
        "section": "S26",
        "status": "failed",
        "closed_error_code": "HANDOFF_AUTHORITY_INVALID",
    }
    assert not (fixture.implementation_root / contract["evidence_path"]).exists()


@pytest.mark.parametrize(
    ("requested_token", "completed", "expected_code"),
    (
        (
            "S28",
            {"section-27-placeholder"},
            "incomplete-handoff-predecessors",
        ),
        (
            "S26",
            {"section-01-foundation"},
            "incomplete-handoff-predecessors",
        ),
        (
            "S26",
            {"section-01-foundation", "section-27-placeholder"},
            "handoff-section-not-ready",
        ),
    ),
)
def test_implement_evidence_handoff_requires_current_readiness_before_any_child(
    tmp_path: Path,
    monkeypatch,
    requested_token: str,
    completed: set[str],
    expected_code: str,
) -> None:
    fixture = make_detached_record_fixture(
        tmp_path,
        section="section-28-scoped-native-and-external-composition",
        manifest_sections=(
            ["section-01-foundation"]
            + [f"section-{number:02d}-placeholder" for number in range(2, 26)]
            + [
                "section-26-publication-wire-and-decision-store",
                "section-27-placeholder",
                "section-28-scoped-native-and-external-composition",
            ]
        ),
    )
    module = load_zagrosi_module(fixture.script)
    progress = module.sections.check_section_progress(fixture.planning)
    s26 = "section-26-publication-wire-and-decision-store"
    s28 = "section-28-scoped-native-and-external-composition"
    s01 = "section-01-foundation"
    s27 = "section-27-placeholder"
    dependencies = {candidate: [s28] for candidate in progress["sections"]}
    dependencies[s01] = []
    dependencies[s27] = []
    dependencies[s26] = [s01, s27]
    dependencies[s28] = [s26, s27]
    child_calls = 0

    def forbidden_child(*args, **kwargs):
        nonlocal child_calls
        child_calls += 1
        raise AssertionError("readiness failure must occur before any child process")

    emitted: list[tuple[dict, int]] = []
    monkeypatch.setattr(module.sections, "dependency_graph", lambda planning_dir, current: dependencies)
    monkeypatch.setattr(
        module.pinners,
        "detached_completed_records",
        lambda root_fd, config, current: {section: {} for section in completed},
    )
    if expected_code == "handoff-section-not-ready":
        monkeypatch.setattr(module.sections, "ready_sections", lambda current, graph, done: [s28])
    monkeypatch.setattr(module.processes, "run_bounded_child", forbidden_child)
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
            requested_token,
        ]
    )

    assert parsed.func(parsed) == 5
    assert emitted[-1][0] == {
        "schema": "zagrosi-privileged-evidence-handoff-error-v1",
        "purpose": "zagrosi_privileged_evidence_handoff_error",
        "section": requested_token,
        "status": "failed",
        "closed_error_code": "HANDOFF_SECTION_NOT_READY",
    }
    assert child_calls == 0
    contract = module.detached_contract.HANDOFF_SECTION_CONTRACTS[requested_token]
    assert not (fixture.implementation_root / contract["evidence_path"]).exists()
