from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from detached_test_support import (
    admission_pinner_payload,
    assert_canonical_json_file,
    canonical_json_bytes_for_test,
    copy_implementation_plugin,
    detached_root_identity_digest_for_test,
    file_sha256,
    implementation_source_args,
    make_detached_record_fixture,
    planning_tree_snapshot,
    replace_file,
    target_root_identity_digest_for_test,
    write_test_admission_pinner,
)
from forge_test_helpers import (
    DETACHED_CONTRACT_RELATIVE_PATH,
    IMPLEMENTATION_SOURCE_RELATIVE_PATHS,
    ROOT,
    load_zagrosi_module,
    run_cmd,
    run_raw,
    run_script_raw,
    write_non_topological_section_fixture,
)


def test_detached_implementation_mode_uses_dependency_ready_order_and_preserves_planning_bytes(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    expected_planning = planning_tree_snapshot(planning)

    setup = run_cmd(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )

    assert setup["mode"] == "detached-frozen"
    assert setup["next_section"] == "section-03-storage"
    assert setup["ready_sections"] == ["section-03-storage"]
    assert setup["implementation_root"] == str(implementation_root)
    assert planning_tree_snapshot(planning) == expected_planning


    assert not (planning / "implementation").exists()
    config = assert_canonical_json_file(implementation_root / "zagrosi_implement_config.json")
    assert config["schema"] == "zagrosi-detached-implementation-config-v2"
    assert config["planning_tree_sha256"] == setup["planning_tree_sha256"]
    assert config["admission_state_sha256"] == admission_pinner_payload(planning)["start"]["a_sha256"]
    assert config["detached_implementation_root_identity_digest"] == detached_root_identity_digest_for_test(
        implementation_root
    )
    assert config["target_root_identity_digest"] == target_root_identity_digest_for_test(target)
    for source, relative in IMPLEMENTATION_SOURCE_RELATIVE_PATHS.items():
        source_path = ROOT / relative
        assert config[f"implement_{source}_path"] == str(source_path)
        assert config[f"implement_{source}_sha256"] == file_sha256(source_path)
        assert config[f"implement_{source}_size"] == source_path.stat().st_size
    assert_canonical_json_file(implementation_root / "zagrosi_implement_state.json")

    progress = run_cmd(
        "implement-progress",
        "--planning-dir",
        str(planning),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-03-storage",
        "--stage",
        "red",
        "--result",
        "focused test failed as expected",
    )
    assert progress["mode"] == "detached-frozen"
    assert progress["planning_tree_sha256"] == setup["planning_tree_sha256"]
    assert planning_tree_snapshot(planning) == expected_planning
    assert_canonical_json_file(implementation_root / "forge-progress.json")

    review_dir = implementation_root / "code_review"
    (review_dir / "section-03-storage-review.md").write_text("# Review\n\nNo blockers.\n")
    (review_dir / "section-03-storage-decisions.md").write_text("# Decisions\n\nAccepted.\n")
    evidence_path = implementation_root / "evidence" / "storage-gate.json"
    evidence_path.write_bytes(b'{"schema":"test-storage-gate-v1","verdict":"PASS"}\n')
    evidence_path.chmod(0o600)
    record = run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-03-storage",
        "--commit",
        "abc123",
        "--review-artifact",
        "code_review/section-03-storage-review.md",
        "--review-artifact",
        "code_review/section-03-storage-decisions.md",
        "--evidence-row",
        "storage_gate=evidence/storage-gate.json",
        "--verification",
        "uv run pytest tests/test_storage.py",
        "--flight",
        "off",
    )
    assert record["mode"] == "detached-frozen"
    assert record["traceability_matrix"] is None
    assert record["planning_tree_sha256"] == setup["planning_tree_sha256"]
    assert planning_tree_snapshot(planning) == expected_planning
    pinner_path = Path(record["pinner_path"])
    pinner = assert_canonical_json_file(pinner_path)
    assert pinner["schema"] == "zagrosi-implementation-section-pinner-v2"
    assert pinner["section"] == "section-03-storage"
    assert pinner["predecessor_pinners"] == []
    assert "receipt_sha256" not in pinner
    assert pinner["admission_pinner_sha256"] == setup["admission_pinner_sha256"]
    assert pinner["admission_state_sha256"] == config["admission_state_sha256"]
    assert pinner["detached_implementation_root_identity_digest"] == config[
        "detached_implementation_root_identity_digest"
    ]
    assert pinner["target_root_identity_digest"] == config["target_root_identity_digest"]
    assert pinner["implement_tool_sha256"] == config["implement_tool_sha256"]
    assert pinner["implement_skill_sha256"] == config["implement_skill_sha256"]
    assert pinner["implement_test_sha256"] == config["implement_test_sha256"]
    assert pinner["evidence_rows"] == [
        {
            "name": "storage_gate",
            "path": "evidence/storage-gate.json",
            "sha256": "sha256:" + hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            "size": len(evidence_path.read_bytes()),
        }
    ]
    state = assert_canonical_json_file(implementation_root / "zagrosi_implement_state.json")
    assert state["admission_state_sha256"] == config["admission_state_sha256"]
    assert state["detached_implementation_root_identity_digest"] == config[
        "detached_implementation_root_identity_digest"
    ]
    assert state["target_root_identity_digest"] == config["target_root_identity_digest"]
    assert state["completed_sections"]["section-03-storage"]["pinner_file_sha256"] == record["pinner_file_sha256"]

    next_ready = run_cmd(
        "next-section",
        "--planning-dir",
        str(planning),
        "--implementation-root",
        str(implementation_root),
    )
    assert next_ready["mode"] == "detached-frozen"
    assert next_ready["next_section"] == "section-01-foundation"
    assert next_ready["ready_sections"] == ["section-01-foundation"]
    assert next_ready["planning_tree_sha256"] == setup["planning_tree_sha256"]
    assert planning_tree_snapshot(planning) == expected_planning

    (review_dir / "section-01-foundation-review.md").write_text("# Review\n\nNo blockers.\n")
    (review_dir / "section-01-foundation-decisions.md").write_text("# Decisions\n\nAccepted.\n")
    dependent = run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-01-foundation",
        "--commit",
        "def456",
        "--review-artifact",
        "code_review/section-01-foundation-review.md",
        "--review-artifact",
        "code_review/section-01-foundation-decisions.md",
        "--verification",
        "uv run pytest tests/test_foundation.py",
        "--flight",
        "off",
    )
    dependent_pinner = assert_canonical_json_file(Path(dependent["pinner_path"]))
    predecessor_state = state["completed_sections"]["section-03-storage"]
    assert dependent_pinner["predecessor_pinners"] == [
        {
            "section": "section-03-storage",
            "pinner_path": predecessor_state["pinner_path"],
            "pinner_file_sha256": predecessor_state["pinner_file_sha256"],
        }
    ]
    assert dependent["next_section"] == "section-02-api"
    assert planning_tree_snapshot(planning) == expected_planning


def test_detached_implementation_root_rejects_symlink_components(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(real_root, target_is_directory=True)

    result = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(linked_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "unsafe-detached-path"
    assert "symbolic link" in payload["error"]


def test_detached_next_section_rejects_planning_or_admission_drift(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    run_cmd(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )

    (planning / "unexpected.md").write_text("changed membership\n")
    planning_drift = run_raw(
        "next-section",
        "--planning-dir",
        str(planning),
        "--implementation-root",
        str(implementation_root),
    )
    assert planning_drift.returncode != 0
    assert json.loads(planning_drift.stdout)["error_code"] == "planning-tree-drift"
    (planning / "unexpected.md").unlink()

    write_test_admission_pinner(admission_pinner, authority="REPLACED")
    admission_drift = run_raw(
        "next-section",
        "--planning-dir",
        str(planning),
        "--implementation-root",
        str(implementation_root),
    )
    assert admission_drift.returncode != 0
    assert json.loads(admission_drift.stdout)["error_code"] == "admission-pinner-drift"


def test_detached_next_section_rejects_target_root_replacement_without_u_mutation(tmp_path: Path) -> None:
    fixture = make_detached_record_fixture(tmp_path)
    before = planning_tree_snapshot(fixture.implementation_root)
    displaced_target = tmp_path / "displaced-target"
    fixture.target.rename(displaced_target)
    fixture.target.mkdir()
    try:
        result = run_script_raw(
            fixture.script,
            "next-section",
            "--planning-dir",
            str(fixture.planning),
            "--implementation-root",
            str(fixture.implementation_root),
        )
        assert result.returncode == 1
        assert json.loads(result.stdout)["error_code"] == "target-root-identity-drift"
        assert planning_tree_snapshot(fixture.implementation_root) == before
    finally:
        fixture.target.rmdir()
        displaced_target.rename(fixture.target)


@pytest.mark.parametrize("drift_kind", ("planning", "admission", "source"))
def test_authenticated_root_temp_is_retained_when_external_authority_drift_refuses_context(
    tmp_path: Path,
    drift_kind: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / drift_kind)
    fixture = make_detached_record_fixture(tmp_path / f"fixture-{drift_kind}", plugin_root=plugin_root)
    root_temp = fixture.implementation_root / ".forge-progress.json.tmp"
    root_temp.write_bytes(b'{"uncommitted":"retain"}\n')
    root_temp.chmod(0o600)
    if drift_kind == "planning":
        (fixture.planning / "post-setup-drift.md").write_text("drift\n")
        expected_error = "planning-tree-drift"
    elif drift_kind == "admission":
        write_test_admission_pinner(fixture.admission_pinner, authority="REPLACED")
        expected_error = "admission-pinner-drift"
    else:
        skill_path = plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["skill"]
        skill_path.write_bytes(skill_path.read_bytes() + b"\nsource drift\n")
        expected_error = "implement-source-drift"
    before = planning_tree_snapshot(fixture.implementation_root)

    result = run_script_raw(
        fixture.script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == expected_error
    assert planning_tree_snapshot(fixture.implementation_root) == before
    assert root_temp.is_file()


def test_detached_next_section_reopens_exact_config_after_mid_command_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = make_detached_record_fixture(tmp_path)
    module = load_zagrosi_module(fixture.script)
    original_completed = module.pinners.detached_completed_records
    state_before = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    replaced = False

    def completed_then_replace_config(root_fd, config, progress, **kwargs):
        nonlocal replaced
        completed = original_completed(root_fd, config, progress, **kwargs)
        if not replaced:
            replaced = True
            changed = dict(config)
            changed["runtime"] = "mid-command-replacement"
            replace_file(
                fixture.implementation_root / "zagrosi_implement_config.json",
                canonical_json_bytes_for_test(changed),
                mode=0o600,
            )
        return completed

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module.pinners, "detached_completed_records", completed_then_replace_config)
    monkeypatch.setattr(
        module.output,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.cli.build_parser().parse_args(
        [
            "next-section",
            "--planning-dir",
            str(fixture.planning),
            "--implementation-root",
            str(fixture.implementation_root),
        ]
    )
    assert parsed.func(parsed) == 1
    assert replaced is True
    assert captured[-1][0]["error_code"] == "detached-config-drift"
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == state_before


def test_detached_setup_requires_all_implementation_source_hashes_and_rejects_wrong_hash(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    expected_planning = planning_tree_snapshot(planning)
    base_args = (
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(tmp_path / "detached-implementation"),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        "--flight",
        "off",
    )

    all_source_args = implementation_source_args()
    for index, source in enumerate(IMPLEMENTATION_SOURCE_RELATIVE_PATHS):
        supplied_args = all_source_args[: index * 2] + all_source_args[index * 2 + 2 :]
        missing = run_raw(*base_args[:-2], *supplied_args, *base_args[-2:])
        assert missing.returncode != 0
        missing_payload = json.loads(missing.stdout)
        assert missing_payload["error_code"] == "missing-implement-source-hash"
        assert missing_payload["implement_source"] == source
        assert missing_payload["required_argument"] == f"--expected-implement-{source}-sha256"

    for source in IMPLEMENTATION_SOURCE_RELATIVE_PATHS:
        wrong = run_raw(
            *base_args[:-2],
            *implementation_source_args(**{source: "sha256:" + "0" * 64}),
            *base_args[-2:],
        )
        assert wrong.returncode != 0
        wrong_payload = json.loads(wrong.stdout)
        assert wrong_payload["error_code"] == "implement-source-drift"
        assert wrong_payload["implement_source"] == source
        assert wrong_payload["expected_implement_source_sha256"] == "sha256:" + "0" * 64
    assert planning_tree_snapshot(planning) == expected_planning


def test_detached_contract_reference_is_transitively_tool_pinned(tmp_path: Path) -> None:
    module = load_zagrosi_module()
    contract = ROOT / DETACHED_CONTRACT_RELATIVE_PATH
    assert file_sha256(contract) == module.detached_contract.DETACHED_CONTRACT_SHA256
    assert module.detached_contract.DETACHED_CONTRACT_SHA256.removeprefix("sha256:") in (
        ROOT / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["skill"]
    ).read_text()

    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    plugin_root = copy_implementation_plugin(tmp_path / "tampered-contract")
    copied_contract = plugin_root / DETACHED_CONTRACT_RELATIVE_PATH
    copied_contract.write_text(copied_contract.read_text() + "\nTampered.\n")
    implementation_root = tmp_path / "detached-implementation"

    result = run_script_raw(
        plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"],
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(plugin_root),
        "--flight",
        "off",
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "implement-contract-drift"
    assert payload["implement_source"] == "contract"
    assert not implementation_root.exists()


def test_detached_setup_rejects_missing_symlinked_and_hardlinked_implementation_sources(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")

    def setup_with(plugin_root: Path, implementation_root: Path, source_args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return run_script_raw(
            plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"],
            "implement-setup",
            "--sections-dir",
            str(sections),
            "--target-dir",
            str(target),
            "--implementation-root",
            str(implementation_root),
            "--admission-pinner",
            str(admission_pinner),
            "--expected-admission-pinner-sha256",
            file_sha256(admission_pinner),
            *source_args,
            "--flight",
            "off",
        )

    missing_root = copy_implementation_plugin(tmp_path / "missing-source")
    missing_args = implementation_source_args(missing_root)
    (missing_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["test"]).unlink()
    missing = setup_with(missing_root, tmp_path / "missing-state", missing_args)
    assert missing.returncode != 0
    missing_payload = json.loads(missing.stdout)
    assert missing_payload["error_code"] == "unsafe-implement-source"
    assert missing_payload["implement_source"] == "test"

    symlink_root = copy_implementation_plugin(tmp_path / "symlink-source")
    symlink_args = implementation_source_args(symlink_root)
    skill_path = symlink_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["skill"]
    replacement = symlink_root / "replacement-skill.md"
    replacement.write_bytes(skill_path.read_bytes())
    skill_path.unlink()
    skill_path.symlink_to(replacement)
    symlinked = setup_with(symlink_root, tmp_path / "symlink-state", symlink_args)
    assert symlinked.returncode != 0
    symlink_payload = json.loads(symlinked.stdout)
    assert symlink_payload["error_code"] == "unsafe-implement-source"
    assert symlink_payload["implement_source"] == "skill"

    hardlink_root = copy_implementation_plugin(tmp_path / "hardlink-source")
    hardlink_args = implementation_source_args(hardlink_root)
    tool_path = hardlink_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"]
    os.link(tool_path, hardlink_root / "tool-alias.py")
    hardlinked = setup_with(hardlink_root, tmp_path / "hardlink-state", hardlink_args)
    assert hardlinked.returncode != 0
    hardlink_payload = json.loads(hardlinked.stdout)
    assert hardlink_payload["error_code"] == "unsafe-implement-source"
    assert hardlink_payload["implement_source"] == "tool"


def test_every_detached_command_rejects_implementation_source_changed_after_setup(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "copied-plugin")
    script = plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"]
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    expected_planning = planning_tree_snapshot(planning)
    setup = run_script_raw(
        script,
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(plugin_root),
        "--flight",
        "off",
    )
    assert setup.returncode == 0, setup.stderr + setup.stdout
    config = assert_canonical_json_file(implementation_root / "zagrosi_implement_config.json")
    assert config["implement_tool_path"] == str(script)
    assert config["implement_skill_path"] == str(plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["skill"])
    assert config["implement_test_path"] == str(plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["test"])

    test_source = plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["test"]
    test_source.write_bytes(test_source.read_bytes() + b"\n# changed after detached setup\n")
    commands = (
        ("next-section", "--planning-dir", str(planning), "--implementation-root", str(implementation_root)),
        (
            "implement-progress",
            "--planning-dir",
            str(planning),
            "--implementation-root",
            str(implementation_root),
            "--section",
            "section-03-storage",
            "--stage",
            "red",
        ),
        (
            "implement-record-section",
            "--sections-dir",
            str(sections),
            "--implementation-root",
            str(implementation_root),
            "--section",
            "section-03-storage",
            "--flight",
            "off",
        ),
    )
    for command in commands:
        result = run_script_raw(script, *command)
        assert result.returncode != 0
        payload = json.loads(result.stdout)
        assert payload["error_code"] == "implement-source-drift"
        assert payload["implement_source"] == "test"
    assert planning_tree_snapshot(planning) == expected_planning


def test_detached_next_section_rejects_mid_command_tool_source_drift(tmp_path: Path, monkeypatch) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "copied-plugin")
    script = plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"]
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    expected_planning = planning_tree_snapshot(planning)
    setup = run_script_raw(
        script,
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(plugin_root),
        "--flight",
        "off",
    )
    assert setup.returncode == 0, setup.stderr + setup.stdout

    module = load_zagrosi_module(script)
    original_completed_records = module.pinners.detached_completed_records
    changed = False

    def change_tool_after_initial_verification(*args, **kwargs):
        nonlocal changed
        result = original_completed_records(*args, **kwargs)
        if not changed:
            script.write_bytes(script.read_bytes() + b"\n# changed during detached command\n")
            changed = True
        return result

    captured: list[tuple[dict, int]] = []

    def capture_json(payload: dict, exit_code: int = 0) -> int:
        captured.append((payload, exit_code))
        return exit_code

    monkeypatch.setattr(module.pinners, "detached_completed_records", change_tool_after_initial_verification)
    monkeypatch.setattr(module.output, "print_json", capture_json)
    result = module.detached_progress.detached_next_section(
        SimpleNamespace(planning_dir=str(planning), implementation_root=str(implementation_root))
    )

    assert result == 1
    payload, exit_code = captured[-1]
    assert exit_code == 1
    assert payload["error_code"] == "implement-source-drift"
    assert payload["implement_source"] == "tool"
    assert planning_tree_snapshot(planning) == expected_planning
