"""Resume uses actual saved progress without recycling evidence after input drift."""

from __future__ import annotations

import json
from pathlib import Path

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


def test_mutable_setup_ignores_commented_contract_links(forge, workspace, capsys):
    root, planning = workspace
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + "\n<!-- [obsolete](../missing.md) -->\n")
    code, result = invoke(forge, capsys, "implement-setup", "--sections-dir", str(planning / "sections"),
                          "--target-dir", str(root), "--flight", "off")
    assert code == 0, result
    assert result["packet"]["success"]
    save_progress(forge, capsys, planning)


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
