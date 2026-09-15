from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from detached_test_support import (
    admission_pinner_payload,
    canonical_json_bytes_for_test,
    copy_implementation_plugin,
    detached_record_arguments,
    file_sha256,
    implementation_source_args,
    make_detached_record_fixture,
    planning_tree_snapshot,
    replace_file,
    write_test_admission_pinner,
)
from forge_test_helpers import (
    load_zagrosi_module,
    run_raw,
    write_single_section_fixture,
)


def test_detached_packet_and_skeleton_writers_require_external_output(tmp_path: Path) -> None:
    fixture = make_detached_record_fixture(tmp_path)
    expected_planning = planning_tree_snapshot(fixture.planning)
    commands = (
        (
            "implementation-packet",
            "--section",
            fixture.section,
            "packets",
            f"{fixture.section}-packet.md",
        ),
        ("tdd-skeletons", "--framework", "pytest", "tdd-skeletons", "test_skeleton.py"),
    )
    for command, option, value, directory, filename in commands:
        missing = run_raw(
            command,
            "--planning-dir",
            str(fixture.planning),
            option,
            value,
            "--implementation-root",
            str(fixture.implementation_root),
        )
        assert missing.returncode != 0
        assert json.loads(missing.stdout)["error_code"] == "missing-detached-output-dir"

        planning_output = fixture.planning / ".forge" / directory
        refused = run_raw(
            command,
            "--planning-dir",
            str(fixture.planning),
            option,
            value,
            "--implementation-root",
            str(fixture.implementation_root),
            "--output-dir",
            str(planning_output),
        )
        assert refused.returncode != 0
        assert json.loads(refused.stdout)["error_code"] == "external-artifact-outside-root"
        assert not planning_output.exists()

        external_output = fixture.implementation_root / "code_review" / "generated" / directory
        accepted = run_raw(
            command,
            "--planning-dir",
            str(fixture.planning),
            option,
            value,
            "--implementation-root",
            str(fixture.implementation_root),
            "--output-dir",
            str(external_output),
        )
        assert accepted.returncode == 0, accepted.stderr + accepted.stdout
        assert Path(json.loads(accepted.stdout)["output"]) == external_output / filename
        assert planning_tree_snapshot(fixture.planning) == expected_planning


@pytest.mark.parametrize(
    ("drift_kind", "expected_code"),
    (
        ("planning", "planning-tree-changed"),
        ("tool", "implement-source-drift"),
        ("admission", "admission-pinner-drift"),
        ("evidence", "detached-evidence-drift"),
        ("root_identity", "unsafe-detached-root-identity"),
    ),
)
def test_detached_record_rechecks_every_late_input_before_pinner(
    tmp_path: Path,
    monkeypatch,
    drift_kind: str,
    expected_code: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / drift_kind)
    fixture = make_detached_record_fixture(tmp_path / f"fixture-{drift_kind}", plugin_root=plugin_root)
    module = load_zagrosi_module(fixture.script)
    original_evidence_rows = module.detached_state.detached_evidence_rows
    evidence_calls = 0
    initial_state = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    initial_pinners = list((fixture.implementation_root / "pinners").glob("*.json"))

    def replace_late_input() -> None:
        if drift_kind == "planning":
            plan = fixture.planning / "codex-plan.md"
            replace_file(plan, plan.read_bytes() + b"\nlate planning replacement\n")
        elif drift_kind == "tool":
            replace_file(
                fixture.script,
                fixture.script.read_bytes() + b"\n# late tool replacement\n",
                mode=fixture.script.stat().st_mode & 0o777,
            )
        elif drift_kind == "admission":
            payload = admission_pinner_payload(fixture.planning)
            payload["o_sha256"] = "sha256:" + "44" * 32
            replace_file(
                fixture.admission_pinner,
                (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
                mode=0o600,
            )
        elif drift_kind == "root_identity":
            fixture.implementation_root.chmod(0o755)
        else:
            replace_file(
                fixture.evidence_path,
                b'{"schema":"late-replacement-evidence-v1","verdict":"PASS"}\n',
                mode=0o600,
            )

    def mutate_before_final_evidence_reopen(*args, **kwargs):
        nonlocal evidence_calls
        evidence_calls += 1
        if evidence_calls == 2:
            replace_late_input()
        return original_evidence_rows(*args, **kwargs)

    captured: list[tuple[dict, int]] = []

    def capture_json(payload: dict, exit_code: int = 0) -> int:
        captured.append((payload, exit_code))
        return exit_code

    monkeypatch.setattr(module.detached_state, "detached_evidence_rows", mutate_before_final_evidence_reopen)
    monkeypatch.setattr(module.output, "print_json", capture_json)
    args = module.cli.build_parser().parse_args(
        detached_record_arguments(fixture, "--evidence-row", "record_gate=evidence/record-gate.json")
    )
    result = args.func(args)

    assert result == 1
    assert captured[-1][1] == 1
    assert captured[-1][0]["error_code"] == expected_code
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == initial_state
    assert list((fixture.implementation_root / "pinners").glob("*.json")) == initial_pinners


@pytest.mark.parametrize(
    ("drift_kind", "expected_code"),
    (
        ("planning", "section-record-recovery-required"),
        ("tool", "section-record-recovery-required"),
        ("admission", "section-record-recovery-required"),
        ("evidence", "detached-evidence-drift"),
    ),
)
def test_detached_record_rolls_back_new_pinner_on_post_pinner_authority_drift(
    tmp_path: Path,
    monkeypatch,
    drift_kind: str,
    expected_code: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / drift_kind)
    fixture = make_detached_record_fixture(tmp_path / f"fixture-{drift_kind}", plugin_root=plugin_root)
    module = load_zagrosi_module(fixture.script)
    original_install = module.transaction_io.install_staged_section_pinner
    initial_state = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    initial_pinners = list((fixture.implementation_root / "pinners").glob("*.json"))
    mutated = False

    def replace_after_pinner_install(root_fd, transaction_fd, pinner_path, pinner_raw):
        nonlocal mutated
        result = original_install(root_fd, transaction_fd, pinner_path, pinner_raw)
        if not mutated:
            mutated = True
            if drift_kind == "planning":
                plan = fixture.planning / "codex-plan.md"
                replace_file(plan, plan.read_bytes() + b"\npost-pinner planning drift\n")
            elif drift_kind == "tool":
                replace_file(
                    fixture.script,
                    fixture.script.read_bytes() + b"\n# post-pinner tool drift\n",
                    mode=fixture.script.stat().st_mode & 0o777,
                )
            elif drift_kind == "admission":
                payload = admission_pinner_payload(fixture.planning)
                payload["o_sha256"] = "sha256:" + "77" * 32
                replace_file(
                    fixture.admission_pinner,
                    canonical_json_bytes_for_test(payload),
                    mode=0o600,
                )
            else:
                replace_file(
                    fixture.evidence_path,
                    b'{"schema":"post-pinner-mutant-v1","verdict":"PASS"}\n',
                    mode=0o600,
                )
        return result

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module.transaction_io, "install_staged_section_pinner", replace_after_pinner_install)
    monkeypatch.setattr(
        module.output,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.cli.build_parser().parse_args(
        detached_record_arguments(fixture, "--evidence-row", "record_gate=evidence/record-gate.json")
    )

    assert parsed.func(parsed) == 1
    assert mutated is True
    assert captured[-1][0]["error_code"] == expected_code
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == initial_state
    assert list((fixture.implementation_root / "pinners").glob("*.json")) == initial_pinners


def test_detached_record_rolls_back_state_and_pinner_on_post_state_evidence_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "plugin-copy")
    fixture = make_detached_record_fixture(tmp_path / "fixture", plugin_root=plugin_root)
    module = load_zagrosi_module(fixture.script)
    original_replace_state = module.transaction_io.replace_state_from_transaction
    initial_state = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    initial_pinners = list((fixture.implementation_root / "pinners").glob("*.json"))
    mutated = False

    def promote_then_replace_evidence(root_fd, transaction_fd, expected_raw, replacement):
        nonlocal mutated
        replacement_raw = original_replace_state(root_fd, transaction_fd, expected_raw, replacement)
        if not mutated and replacement.get("completed_sections", {}).get(fixture.section) is not None:
            mutated = True
            replace_file(
                fixture.evidence_path,
                b'{"schema":"post-state-mutant-v1","verdict":"PASS"}\n',
                mode=0o600,
            )
        return replacement_raw

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module.transaction_io, "replace_state_from_transaction", promote_then_replace_evidence)
    monkeypatch.setattr(
        module.output,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.cli.build_parser().parse_args(
        detached_record_arguments(fixture, "--evidence-row", "record_gate=evidence/record-gate.json")
    )

    assert parsed.func(parsed) == 1
    assert captured[-1][0]["error_code"] == "detached-evidence-drift"
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == initial_state
    assert list((fixture.implementation_root / "pinners").glob("*.json")) == initial_pinners


def test_detached_identity_checks_reject_case_aliases_before_any_create(tmp_path: Path) -> None:
    planning = tmp_path / "Planning"
    sections = write_single_section_fixture(planning)
    alias = tmp_path / "pLANNING"
    try:
        case_alias_available = alias.exists() and os.path.samefile(planning, alias)
    except OSError:
        case_alias_available = False
    if not case_alias_available:
        pytest.skip("case-insensitive filesystem alias is unavailable")

    target = tmp_path / "target"
    target.mkdir()
    external_pinner = write_test_admission_pinner(
        tmp_path / "external-admission-pinner.json",
        planning_dir=planning,
    )
    aliased_root = alias / "detached-implementation"
    root_overlap = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(aliased_root),
        "--admission-pinner",
        str(external_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(external_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )
    assert root_overlap.returncode != 0
    assert json.loads(root_overlap.stdout)["error_code"] == "detached-root-overlap"
    assert not aliased_root.exists()

    internal_pinner = write_test_admission_pinner(
        planning / "internal-admission-pinner.json",
        planning_dir=planning,
    )
    external_root = tmp_path / "external-implementation"
    admission_overlap = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(external_root),
        "--admission-pinner",
        str(alias / internal_pinner.name),
        "--expected-admission-pinner-sha256",
        file_sha256(internal_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )
    assert admission_overlap.returncode != 0
    assert json.loads(admission_overlap.stdout)["error_code"] == "admission-pinner-overlap"
    assert not external_root.exists()

    planning_target_root = tmp_path / "planning-target-implementation"
    planning_target_overlap = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(alias),
        "--implementation-root",
        str(planning_target_root),
        "--admission-pinner",
        str(external_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(external_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )
    assert planning_target_overlap.returncode != 0
    assert json.loads(planning_target_overlap.stdout)["error_code"] == "planning-target-overlap"
    assert not planning_target_root.exists()

    protected_target = tmp_path / "ProtectedTarget"
    protected_target.mkdir()
    target_alias = tmp_path / "pROTECTEDtARGET"
    assert target_alias.exists() and os.path.samefile(protected_target, target_alias)
    target_overlap = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(protected_target),
        "--implementation-root",
        str(target_alias),
        "--admission-pinner",
        str(external_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(external_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )
    assert target_overlap.returncode != 0
    assert json.loads(target_overlap.stdout)["error_code"] == "detached-root-target-overlap"
    assert list(protected_target.iterdir()) == []
