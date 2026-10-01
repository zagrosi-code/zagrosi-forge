"""Saved mutable evidence is validated before mutation and bound to its target."""

import json

import pytest

from forge_test_helpers import run_cmd, run_raw
from test_compact_plan import SECTION, make_plan


def setup_args(planning, target=None):
    args = ["implement-setup", "--sections-dir", str(planning / "sections"), "--flight", "off"]
    return [*args, "--target-dir", str(target)] if target is not None else args


def record_args(planning, target=None, section=SECTION):
    args = ["implement-record-section", "--sections-dir", str(planning / "sections"),
            "--section", section, "--review-status", "pass", "--verification-source", "attestation",
            "--verification-outcome", "passed", "--verification", "Targeted label checks passed",
            "--flight", "off"]
    return [*args, "--target-dir", str(target)] if target is not None else args


@pytest.fixture
def workspace(tmp_path):
    planning = make_plan(tmp_path / "plan")
    target = tmp_path / "target"
    target.mkdir()
    implementation = planning / "implementation"
    implementation.mkdir()
    (implementation / "zagrosi_implement_config.json").write_text(json.dumps({"target_dir": str(target)}))
    (implementation / "zagrosi_implement_state.json").write_text('{"completed_sections":{}}\n')
    (implementation / "forge-progress.json").write_text('{"events":[]}\n')
    return planning, target


def assert_rejected_without_mutation(planning, command):
    paths = [planning / "implementation" / name for name in (
        "zagrosi_implement_config.json", "zagrosi_implement_state.json", "forge-progress.json",
    )]
    before = {path: path.read_bytes() for path in paths}
    result = run_raw(*command)
    assert result.returncode != 0, result.stdout
    assert "Traceback" not in result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("saved", [
    "null", "[]", '{"completed_sections":["preserve this evidence"]}',
    '{"completed_sections":{},"pending_sections":null}',
    '{"completed_sections":{},"pending_sections":[]}', '{"completed_sections":',
])
@pytest.mark.parametrize("command", ["setup", "record", "next", "status", "postflight"])
def test_malformed_state_is_rejected_before_any_saved_file_changes(workspace, command, saved):
    planning, target = workspace
    (planning / "implementation/zagrosi_implement_state.json").write_text(saved)
    commands = {
        "setup": setup_args(planning, target),
        "record": record_args(planning, target),
        "next": ["next-section", "--planning-dir", str(planning)],
        "status": ["status", "--path", str(planning)],
        "postflight": ["postflight", "--phase", "implement", "--planning-dir", str(planning), "--strict"],
    }
    assert_rejected_without_mutation(planning, commands[command])


@pytest.mark.parametrize("saved", ["[]", '{"events":null}', '{"events":{}}', '{"events":'])
def test_malformed_progress_is_rejected_without_losing_saved_events(workspace, saved):
    planning, _ = workspace
    (planning / "implementation/forge-progress.json").write_text(saved)
    assert_rejected_without_mutation(planning, [
        "implement-progress", "--planning-dir", str(planning), "--section", SECTION, "--stage", "green",
    ])


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_retargeting_reopens_bound_completion_without_deleting_evidence(tmp_path, depth):
    planning = make_plan(tmp_path / "plan", depth)
    original, replacement = tmp_path / "original", tmp_path / "replacement"
    original.mkdir()
    replacement.mkdir()
    run_cmd(*setup_args(planning, original))
    run_cmd(*record_args(planning))
    state = planning / "implementation/zagrosi_implement_state.json"
    before = state.read_bytes()

    result = run_cmd(*setup_args(planning, replacement))

    assert result["target_dir"] == str(replacement)
    assert result["completed_sections"] == []
    assert result["remaining_sections"] == [SECTION]
    assert result["next_section"] == SECTION
    assert state.read_bytes() == before
    assert run_cmd("next-section", "--planning-dir", str(planning))["next_section"] == SECTION


def test_setup_without_override_retains_saved_target_from_another_working_directory(workspace, tmp_path):
    planning, target = workspace
    run_cmd(*record_args(planning))
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()

    result = run_cmd(*setup_args(planning), cwd=unrelated)

    assert result["target_dir"] == str(target)
    assert result["completed_sections"] == [SECTION]
    assert result["remaining_sections"] == []


def add_dependent_section(planning):
    second = "section-02-export"
    index = planning / "sections/index.md"
    marker, body = index.read_text().split("END_FORGE_META -->\n", 1)
    section = planning / "sections" / f"{SECTION}.md"
    (planning / "codex-plan.md").write_text(marker + "END_FORGE_META -->\n" + section.read_text())
    index.write_text(body.replace("END_MANIFEST", second + "\nEND_MANIFEST")
                     + f"\n{second} depends on {SECTION}\n")
    (section.parent / f"{second}.md").write_text(section.read_text().replace(SECTION, second).replace("labels.py", "exports.py"))
    return second


def test_explicit_record_target_cannot_borrow_another_targets_predecessor(workspace, tmp_path):
    planning, target = workspace
    second = add_dependent_section(planning)
    run_cmd(*record_args(planning, target))
    other = tmp_path / "other"
    other.mkdir()
    state = planning / "implementation/zagrosi_implement_state.json"
    before = state.read_bytes()

    result = run_raw(*record_args(planning, other, second))

    assert result.returncode != 0, result.stdout
    assert json.loads(result.stdout)["incomplete_predecessors"] == [SECTION]
    assert state.read_bytes() == before


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("same_cwd,saved_target", [(False, False), (True, False), (False, True)])
def test_parallel_readiness_uses_effective_target(tmp_path, depth, same_cwd, saved_target):
    planning = make_plan(tmp_path / "plan", depth)
    second = add_dependent_section(planning)
    original, other = tmp_path / "original", tmp_path / "other"
    original.mkdir()
    other.mkdir()
    run_cmd(*record_args(planning, original))
    config = planning / "implementation/zagrosi_implement_config.json"
    assert not config.exists()
    if saved_target:
        config.write_text(json.dumps({"target_dir": str(original)}))
    files = {path: path.read_bytes() for path in planning.rglob("*") if path.is_file()}

    result = run_cmd("parallel-plan", "--planning-dir", str(planning), cwd=original if same_cwd else other)

    retained = same_cwd or saved_target
    assert result["completed_sections"] == ([SECTION] if retained else [])
    assert result["layers"] == ([[second]] if retained else [[SECTION], [second]])
    assert {path: path.read_bytes() for path in planning.rglob("*") if path.is_file()} == files


def test_valid_legacy_state_remains_readable_and_preserved(workspace):
    planning, target = workspace
    state = planning / "implementation/zagrosi_implement_state.json"
    legacy = {"completed_sections": {SECTION: {
        "review_status": "pass", "verification": ["Checked existing labels"],
        "verification_result": {"version": 1, "source": "inspection", "outcome": "passed",
                                "evidence": ["Checked existing labels"]},
    }}, "historical_note": "Preserve optional legacy metadata"}
    state.write_text(json.dumps(legacy) + "\n")
    before = state.read_bytes()

    result = run_cmd(*setup_args(planning, target))

    assert result["completed_sections"] == [SECTION]
    assert state.read_bytes() == before


def test_setup_expands_explicit_home_directory(workspace, tmp_path, monkeypatch):
    planning, target = workspace
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    result = run_cmd(*setup_args(planning, "~/target"))

    assert result["target_dir"] == str(target)


def test_status_and_next_section_use_same_target_without_saved_config(workspace, tmp_path):
    planning, target = workspace
    (planning / "implementation/zagrosi_implement_config.json").unlink()
    run_cmd(*record_args(planning, target))
    state = planning / "implementation/zagrosi_implement_state.json"
    before = state.read_bytes()
    other = tmp_path / "other"
    other.mkdir()

    status = run_cmd("status", "--path", str(planning), cwd=other)
    next_section = run_cmd("next-section", "--planning-dir", str(planning), cwd=other)

    assert status["next_section"] == next_section["next_section"] == SECTION
    assert status["remaining_sections"] == [SECTION]
    assert "integration_verification" not in status
    assert state.read_bytes() == before
