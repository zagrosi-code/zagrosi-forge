"""Resume uses actual saved progress without recycling evidence after input drift."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

from test_compact_plan import SECTION, make_plan


@pytest.fixture
def forge():
    from runtime_support import load_runtime
    return load_runtime(Path(__file__).resolve().parents[1] / "scripts/zagrosi_skills.py")


@pytest.fixture
def workspace(tmp_path):
    planning = make_plan(tmp_path / ".planning")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/labels.py").write_text("def normalize(value):\n    return value.strip()\n")
    (tmp_path / "tests/test_labels.py").write_text("def test_trim_edges():\n    assert True\n")
    implementation = planning / "implementation"
    implementation.mkdir()
    (implementation / "zagrosi_implement_config.json").write_text(json.dumps({"target_dir": str(tmp_path)}))
    (implementation / "zagrosi_implement_state.json").write_text(json.dumps({"completed_sections": {}}))
    return tmp_path, planning


def invoke(forge, capsys, *args):
    code = forge.entrypoint.main(list(args))
    return code, json.loads(capsys.readouterr().out)


def save_progress(forge, capsys, planning, stage="green"):
    code, payload = invoke(forge, capsys, "implement-progress", "--planning-dir", str(planning),
                           "--section", SECTION, "--stage", stage, "--command", "pytest tests/test_labels.py",
                           "--result", "13 passed", "--notes", "Only review and final integration remain.")
    assert code == 0, payload
    assert "snapshot" in payload["event"], payload
    return payload


@pytest.mark.parametrize("stage,action", [
    ("red", "implement section-01-normalize until the recorded regression passes"),
    ("green", "review section-01-normalize"),
    ("refactor", "rerun targeted regressions for section-01-normalize"),
    ("verified", "record section-01-normalize"),
])
def test_saved_progress_guides_status_and_next_section(forge, workspace, capsys, stage, action):
    _, planning = workspace
    save_progress(forge, capsys, planning, stage)
    for command in (("status", "--path"), ("next-section", "--planning-dir")):
        code, payload = invoke(forge, capsys, *command, str(planning))
        assert code == 0, payload
        assert payload["next_action"] == action
        assert payload["resume"]["evidence_current"] is True
        assert payload["resume"]["result"] == "13 passed"
        assert payload["resume"]["notes"] == "Only review and final integration remain."
        if command[0] == "next-section":
            assert payload["packet"]["success"]
            assert "REQ-001" in payload["packet"]["content"]


@pytest.mark.parametrize("changed", ["code", "test", "section", "source", "decision", "deleted-code"])
def test_changed_inputs_require_revalidation_and_keep_notes(forge, workspace, capsys, changed):
    root, planning = workspace
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + "\nUse [rounding](../decisions.md#rounding).\n")
    (planning / "decisions.md").write_text("## Rounding\n\nPreserve exact whitespace behavior.\n")
    save_progress(forge, capsys, planning)
    paths = {"code": root / "src/labels.py", "test": root / "tests/test_labels.py",
             "section": section, "source": planning / "spec.md", "decision": planning / "decisions.md"}
    if changed == "deleted-code":
        (root / "src/labels.py").unlink()
    else:
        path = paths[changed]
        path.write_text(path.read_text() + "\n# Changed observed input\n")
    code, result = invoke(forge, capsys, "next-section", "--planning-dir", str(planning))
    assert code == 0, result
    assert result["resume"]["evidence_current"] is False
    assert result["resume"]["changed_inputs"]
    assert result["next_action"].startswith("revalidate changed inputs")
    assert result["resume"]["notes"] == "Only review and final integration remain."


def test_unrelated_file_changes_do_not_discard_current_progress(forge, workspace, capsys):
    root, planning = workspace
    save_progress(forge, capsys, planning)
    unrelated = root / "user-notes.txt"
    unrelated.write_text("Unrelated work must stay intact.\n")
    before = unrelated.read_bytes()
    _, result = invoke(forge, capsys, "next-section", "--planning-dir", str(planning))
    assert result["resume"]["evidence_current"]
    assert result["next_action"] == f"review {SECTION}"
    assert unrelated.read_bytes() == before


def test_legacy_notes_survive_but_cannot_establish_fresh_evidence(forge, workspace, capsys):
    _, planning = workspace
    event = {"section": SECTION, "stage": "verified", "result": "passed", "notes": "Review preserved."}
    (planning / "implementation/forge-progress.json").write_text(json.dumps({"events": [event]}))
    _, result = invoke(forge, capsys, "next-section", "--planning-dir", str(planning))
    assert result["resume"]["evidence_current"] is False
    assert result["next_action"].startswith("revalidate ")
    assert result["resume"]["notes"] == "Review preserved."


@pytest.mark.parametrize("command", ["next-section", "parallel-plan"])
def test_scheduling_rejects_a_blocked_plan_review(forge, workspace, capsys, command):
    _, planning = workspace
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace("Verdict: pass", "Verdict: blocked"))
    code, result = invoke(forge, capsys, command, "--planning-dir", str(planning))
    assert code == 1
    assert not result["success"]
    assert result["ready_sections"] == []
    assert result["next_section"] is None
    if command == "parallel-plan":
        assert result["layers"] == []


def test_next_section_reports_a_broken_contract_before_coding(forge, workspace, capsys):
    _, planning = workspace
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + "\n[decision](../missing.md#rule)\n")
    code, result = invoke(forge, capsys, "next-section", "--planning-dir", str(planning))
    assert code == 1
    assert not result["packet"]["success"]
    assert "missing.md" in result["next_action"]


def test_mutable_setup_exposes_selected_packet_and_resume(forge, workspace, capsys):
    root, planning = workspace
    save_progress(forge, capsys, planning)
    code, result = invoke(forge, capsys, "implement-setup", "--sections-dir", str(planning / "sections"),
                          "--target-dir", str(root), "--flight", "off")
    assert code == 0, result
    assert result["packet"]["success"]
    assert result["next_action"] == f"review {SECTION}"


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("profile", ["solo", "enterprise"])
def test_returned_record_command_preserves_paths_depth_and_evidence(forge, tmp_path, capsys, depth, profile):
    root = tmp_path / "repo with spaces"
    planning = make_plan(root / ".planning", depth)
    code, result = invoke(forge, capsys, "implement-setup", "--sections-dir", str(planning / "sections"),
                          "--target-dir", str(root), "--depth", depth, "--profile", profile, "--flight", "off")
    assert code == 0, result
    command = result["commands"]["record"]
    values = {"<review-status>": "pass", "<verification>": "pytest tests/test_labels.py",
              "<changed-file>": "src/labels.py"}
    args = forge.cli.build_parser().parse_args([values.get(arg, arg) for arg in command[2:]])
    assert args.sections_dir == str(planning / "sections")
    assert args.target_dir == str(root)
    assert args.depth == depth
    assert args.profile == profile
    assert args.files_changed == ["src/labels.py"]
    assert args.verification == ["pytest tests/test_labels.py"]
    assert result["record_options"]["--test-file"]
    _, resumed = invoke(forge, capsys, "next-section", "--planning-dir", str(planning))
    assert forge.cli.build_parser().parse_args([values.get(arg, arg) for arg in resumed["commands"]["record"][2:]]).profile == profile


@pytest.mark.parametrize("operation", ["implement-setup", "implement-record-section"])
@pytest.mark.parametrize("override", [None, "solo"])
def test_mutable_profile_resume_preserves_saved_value_and_allows_override(forge, workspace, capsys, monkeypatch, operation, override):
    root, planning = workspace
    common = ["--sections-dir", str(planning / "sections"), "--target-dir", str(root), "--flight", "off"]
    code, result = invoke(forge, capsys, "implement-setup", *common, "--profile", "enterprise")
    assert code == 0, result
    checked_profiles = []
    validate = forge.validation.plan_artifacts_payload

    def checked(path, args):
        checked_profiles.append(args.profile)
        return validate(path, args)

    monkeypatch.setattr(forge.validation, "plan_artifacts_payload", checked)
    arguments = ["--profile", override] if override else []
    if operation == "implement-record-section":
        arguments += ["--section", SECTION, "--review-status", "pass", "--verification", "pytest passed"]
    code, result = invoke(forge, capsys, operation, *common, *arguments)
    assert code == 0, result
    expected = override or "enterprise"
    assert checked_profiles and all(profile == expected for profile in checked_profiles)
    command = result["commands"]["postflight"]
    assert command[command.index("--profile") + 1] == expected
    if operation == "implement-setup":
        assert json.loads((planning / "implementation/zagrosi_implement_config.json").read_text())["profile"] == expected


@pytest.mark.parametrize(("operation", "module", "handler"), [
    ("implement-setup", "detached_setup", "detached_implement_setup"),
    ("implement-record-section", "detached_record", "detached_implement_record_section"),
])
@pytest.mark.parametrize("override", [None, "enterprise"])
def test_detached_profile_default_remains_solo(forge, workspace, monkeypatch, operation, module, handler, override):
    root, planning = workspace
    (planning / "implementation/zagrosi_implement_config.json").write_text('{"profile":"enterprise"}')
    profiles = []

    def dispatch(args):
        profiles.append(args.profile)
        return 0

    # Exercise real profile dispatch without importing the Unix-only implementation.
    monkeypatch.setitem(sys.modules, "fcntl", None)
    detached = ModuleType(forge.package.MODULE_NAMES[f"forge/{module}.py"])
    setattr(detached, handler, dispatch)
    monkeypatch.setitem(sys.modules, detached.__name__, detached)
    arguments = [operation, "--sections-dir", str(planning / "sections"), "--implementation-root", str(root / "detached")]
    if operation == "implement-record-section":
        arguments += ["--section", SECTION]
    if override:
        arguments += ["--profile", override]
    assert forge.entrypoint.main(arguments) == 0
    assert profiles == [override or "solo"]


def test_record_returns_next_packet_after_publication(forge, tmp_path, capsys):
    from test_workflow_admission import two_sections
    planning = two_sections(tmp_path / "plan")
    code, result = invoke(forge, capsys, "implement-record-section", "--sections-dir", str(planning / "sections"),
                          "--section", SECTION, "--review-status", "pass", "--verification", "pytest -q", "--flight", "off")
    assert code == 0, result
    assert result["recorded"] is True
    assert result["next_section"] == "section-02-consumer"
    assert result["entry"]["success"] is True
    assert "section-02-consumer" in result["entry"]["commands"]["record"]
    assert result["entry"]["packet"]["success"] is True


def test_next_packet_failure_does_not_report_record_failure(forge, tmp_path, capsys, monkeypatch):
    from test_workflow_admission import two_sections
    planning = two_sections(tmp_path / "plan")

    def fail_entry(*args, **kwargs):
        raise OSError("Cannot read next contract")

    monkeypatch.setattr(forge.resume, "section_entry", fail_entry)
    code, result = invoke(forge, capsys, "implement-record-section", "--sections-dir", str(planning / "sections"),
                          "--section", SECTION, "--review-status", "pass", "--verification", "pytest -q", "--flight", "off")
    assert code == 0, result
    assert result["success"] and result["recorded"]
    assert not result["entry"]["success"]
    assert result["entry"]["error"] == "Cannot read next contract"
    assert SECTION in forge.state.load_implementation_state(planning)["completed_sections"]


def test_mutable_setup_ignores_commented_contract_links(forge, workspace, capsys):
    root, planning = workspace
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + "\n<!-- [obsolete](../missing.md) -->\n")
    code, result = invoke(forge, capsys, "implement-setup", "--sections-dir", str(planning / "sections"),
                          "--target-dir", str(root), "--flight", "off")
    assert code == 0, result
    assert result["packet"]["success"]
    save_progress(forge, capsys, planning)


def test_escaped_comment_opener_keeps_tracked_contract(forge, workspace, capsys):
    _, planning = workspace
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + "\n\\<!-- [decision](../decisions.md) -->\n")
    decision = planning / "decisions.md"
    decision.write_text("# Decision\n\nPreserve Unicode exactly.\n")
    save_progress(forge, capsys, planning)
    _, result = invoke(forge, capsys, "next-section", "--planning-dir", str(planning))
    assert "Preserve Unicode exactly." in result["packet"]["content"]
    assert result["resume"]["evidence_current"]
    decision.write_text("# Decision\n\nReject non-ASCII input.\n")
    _, result = invoke(forge, capsys, "next-section", "--planning-dir", str(planning))
    assert not result["resume"]["evidence_current"]
    assert result["resume"]["changed_inputs"]


@pytest.mark.parametrize("saved_progress", [False, True])
def test_pending_postflight_overrides_verified_stage_at_every_entry(forge, workspace, capsys, saved_progress):
    root, planning = workspace
    if saved_progress:
        save_progress(forge, capsys, planning, "verified")
    state = planning / "implementation/zagrosi_implement_state.json"
    state.write_text(json.dumps({"completed_sections": {}, "pending_sections": {
        SECTION: {"failed_postflight": {"success": False, "blocking_gates": ["implementation-drift"]}},
    }}))
    commands = [
        ["status", "--path", str(planning)],
        ["next-section", "--planning-dir", str(planning)],
        ["implement-setup", "--sections-dir", str(planning / "sections"),
         "--target-dir", str(root), "--flight", "off"],
    ]
    for command in commands:
        code, result = invoke(forge, capsys, *command)
        assert code == 0, result
        assert result["next_action"] == f"resolve pending verification and retry recording {SECTION} with --flight strict"
        assert result["resume"]["verification_pending"] is True
        assert result["resume"]["evidence_current"] is False
        assert result["resume"]["blocking_gates"] == ["implementation-drift"]
        if "commands" in result:
            command = result["commands"]["record"]
            assert command[command.index("--flight") + 1] == "strict"


def test_final_record_returns_verification_without_repeating_tests(forge, workspace, capsys):
    root, planning = workspace
    code, result = invoke(forge, capsys, "implement-record-section", "--sections-dir", str(planning / "sections"),
                          "--target-dir", str(root), "--section", SECTION, "--review-status", "pass",
                          "--verification", "pytest tests/test_labels.py", "--flight", "off")
    assert code == 0, result
    assert result["recorded"]
    assert result["next_section"] is None
    assert result["test_command"] == "uv run pytest tests/test_labels.py"
    assert "--run-tests" not in result["commands"]["postflight"]
    assert "record" not in result["commands"]


def test_plan_setup_returns_valid_next_command_arguments(forge, tmp_path, capsys):
    (tmp_path / "spec.md").write_text("Keep existing invoice amounts correct.\n")
    code, result = invoke(forge, capsys, "plan-setup", "--file", str(tmp_path / "spec.md"),
                          "--target-dir", str(tmp_path), "--depth", "deep", "--flight", "off")
    assert code == 0, result
    parser = forge.cli.build_parser()
    verify = parser.parse_args(result["commands"]["verify_plan"][2:])
    implement = parser.parse_args(result["commands"]["implement_after_pass"][2:])
    assert verify.phase == "plan" and verify.depth == "deep"
    assert implement.sections_dir == str(tmp_path / "sections")
    assert implement.target_dir == str(tmp_path)


def test_returned_plan_gate_enforces_strict_admission(forge, tmp_path, capsys):
    from test_planning_contract import contract_plan
    planning = contract_plan(tmp_path / "plan", "standard")
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace("existing tests/test_labels.py", "callers"))
    code, setup = invoke(forge, capsys, "plan-setup", "--file", str(planning / "spec.md"),
                         "--target-dir", str(tmp_path), "--depth", "standard", "--flight", "off")
    assert code == 0, setup
    code, result = invoke(forge, capsys, *setup["commands"]["verify_plan"][2:])
    assert code == 1, result
    assert not result["success"]
