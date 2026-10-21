"""One call per section, one finish call, and risk-triggered standard evidence."""

import argparse
import json
import sys

import pytest

from forge_test_helpers import ROOT
from runtime_support import load_runtime
from test_compact_plan import make_plan
from test_state_first import PLAN, cli, ready, write_workspace

sys.path.insert(0, str(ROOT / "tools"))
from coding_trials import workflow_verdict  # noqa: E402


PASS = [sys.executable, "-c", "print('3 passed')"]
FAIL = [sys.executable, "-c", "import sys; print('1 failed'); sys.exit(1)"]


@pytest.fixture(scope="module")
def forge():
    return load_runtime(ROOT / "scripts/zagrosi_skills.py")


@pytest.fixture
def run(forge, capsys):
    return cli(forge, capsys)


@pytest.fixture
def workspace(tmp_path):
    return write_workspace(tmp_path)


@pytest.fixture(autouse=True)
def claude_host(monkeypatch):
    for name in ("CODEX_THREAD_ID", "CODEX_SANDBOX"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")


def progress(run, planning, section, stage, *extra):
    return run("implement-progress", "--planning-dir", str(planning), "--section", section, "--stage", stage, *extra)


def entry(planning, section):
    return json.loads((planning / "status.json").read_text())["sections"][section]


def finish_sections(run, planning):
    for section in ("section-01-trim", "section-02-docs"):
        code, payload = progress(run, planning, section, "done", "--notes", f"{section} complete.")
        assert code == 0, payload


def test_done_records_note_files_and_check(run, workspace):
    planning = ready(run, workspace)
    code, payload = progress(run, planning, "section-01-trim", "started")
    assert code == 0, payload
    assert entry(planning, "section-01-trim")["state"] == "active"
    (workspace / "src/labels.py").write_text("def trim(value):\n    return value.strip()\n")

    code, payload = progress(run, planning, "section-01-trim", "done", "--notes", "Trimmed edges; kept case.", "--", *PASS)
    assert code == 0, payload
    saved = entry(planning, "section-01-trim")
    assert (saved["state"], saved["files"], saved["host"]) == ("done", ["src/labels.py"], "claude")
    assert saved["completed_at"] and saved["check"]["outcome"] == "passed"
    assert saved["check"]["result"] == "3 passed" and saved["check"]["command"] == PASS
    notes = (planning / "notes.md").read_text()
    assert "| section-01-trim | done | claude\n" in notes and "Trimmed edges; kept case." in notes

    code, status = run("status", "--path", str(planning))
    assert status["next_section"] == "section-02-docs"
    assert status["last_verification"]["section"] == "section-01-trim" and status["last_verification"]["fresh"]
    assert status["changed_since_status"] == {"changed": [], "added": [], "removed": []}


def test_done_needs_a_note_and_reports_agent_checks(run, workspace):
    planning = ready(run, workspace)
    code, payload = progress(run, planning, "section-02-docs", "done")
    assert code == 1 and "--notes" in payload["error"]
    code, payload = progress(run, planning, "section-02-docs", "done", "--notes", "README updated.",
                             "--command", "markdown preview", "--result", "renders")
    assert code == 0, payload
    assert entry(planning, "section-02-docs")["check"]["source"] == "reported"


def test_failing_check_keeps_section_active(run, workspace):
    planning = ready(run, workspace)
    code, payload = progress(run, planning, "section-01-trim", "done", "--notes", "Trimmed.", "--", *FAIL)
    assert code == 1 and not payload["success"]
    assert payload["check"]["outcome"] == "failed" and payload["stdout_tail"].strip() == "1 failed"
    saved = entry(planning, "section-01-trim")
    assert saved["state"] == "active" and saved["check"]["outcome"] == "failed"
    assert "| done |" not in (planning / "notes.md").read_text()


def test_finish_captures_suite_and_postflight(run, workspace):
    planning = ready(run, workspace)
    finish_sections(run, planning)
    code, payload = run("implement-verify", "--planning-dir", str(planning), "--target-dir", str(workspace), "--", *PASS)
    assert code == 0, payload
    assert payload["final"]["outcome"] == "passed" and payload["postflight"]["success"]
    assert payload["postflight"]["sections_recorded_complete"] is True
    assert json.loads((planning / "status.json").read_text())["final"]["result"] == "3 passed"
    assert "| plan | finish |" in (planning / "notes.md").read_text()

    code, payload = run("postflight", "--phase", "implement", "--planning-dir", str(planning))
    assert code == 0, payload
    (planning / "notes.md").write_text((planning / "notes.md").read_text() + "\nLater thoughts.\n")
    assert run("postflight", "--phase", "implement", "--planning-dir", str(planning))[0] == 0
    (workspace / "src/labels.py").write_text("changed = True\n")
    code, payload = run("postflight", "--phase", "implement", "--planning-dir", str(planning))
    assert code == 1 and payload["blocking_gates"] == ["final-verification"]
    assert run("status", "--path", str(planning))[1]["last_verification"]["fresh"] is False

    code, payload = run("implement-verify", "--planning-dir", str(planning), "--", *FAIL)
    assert code == 1 and payload["final"]["outcome"] == "failed"
    assert payload["postflight"]["blocking_gates"] == ["final-verification"]


def test_trial_workflow_verdict_accepts_a_finished_plan(run, workspace):
    planning = ready(run, workspace)
    finish_sections(run, planning)
    code, payload = run("implement-verify", "--planning-dir", str(planning), "--target-dir", str(workspace), "--", *PASS)
    assert code == 0, payload
    verdict = workflow_verdict(workspace, "lean")
    assert verdict["success"] and verdict["report"]["sections_recorded_complete"], verdict["report"]


def test_postflight_blocks_only_structural_gaps(run, workspace):
    planning = ready(run, workspace)
    code, payload = run("postflight", "--phase", "plan", "--planning-dir", str(planning), "--strict")
    assert code == 0, payload
    assert {gate["name"] for gate in payload["gates"]} == {"state-plan", "state-status"}

    code, payload = run("postflight", "--phase", "implement", "--planning-dir", str(planning))
    assert code == 1 and payload["blocking_gates"] == ["state-sections", "final-verification"]
    assert payload["sections_recorded_complete"] is False

    (planning / "plan.md").write_text(PLAN + "| section-03-more | src/more.py | more works |\n")
    code, payload = run("postflight", "--phase", "plan", "--planning-dir", str(planning))
    assert code == 1 and payload["blocking_gates"] == ["state-status"]

    (planning / "plan.md").write_text(PLAN.replace("## Goal\n", "## Aim\n"))
    code, payload = run("postflight", "--phase", "plan", "--planning-dir", str(planning))
    assert code == 1 and payload["blocking_gates"] == ["state-plan"]


@pytest.mark.parametrize(("text", "reason"), [
    ("risk: touches release scripts", "risk: touches release scripts"),
    ("rotate auth tokens", "risk term: auth"),
    ("rename the public CLI flags", "public interface: public cli"),
    ("satisfies REQ-002", "requirement IDs: REQ-002"),
])
def test_risk_triggers_name_their_reason(forge, text, reason):
    assert reason in forge.state_first.triggers({"cells": [text]}, team=False)


def test_plain_rows_and_paths_do_not_trigger(forge):
    assert forge.state_first.triggers({"cells": ["the author keeps metadata; `pytest tests/auth/test_login.py` passes"]},
                                      team=False) == []
    assert forge.state_first.triggers({"cells": ["plain"]}, team=True) == ["team handoff: team coordination is configured"]


@pytest.mark.parametrize("cells", [["risk: low", "`pytest tests/test_labels.py` passes"], ["risk: none, docs only"],
                                   ["risk: low. `pytest tests/test_labels.py` passes"], ["`risk: n/a`", "README updated"]])
def test_no_risk_tags_stay_within_their_cell(forge, cells):
    assert forge.state_first.triggers({"cells": cells}, team=False) == []


def test_triggered_section_requires_standard_evidence(run, workspace):
    (workspace / ".planning/plan.md").write_text(PLAN.replace(
        "| README shows trimming |", "| the public CLI format and auth tokens stay documented |"))
    code, payload = run("plan-setup", "--file", str(workspace / ".planning/spec.md"), "--target-dir", str(workspace),
                        "--state-first")
    assert code == 0, payload
    planning = workspace / ".planning"
    assert "risk term: auth" in payload["sections"][1]["risk"]

    code, payload = progress(run, planning, "section-02-docs", "done", "--notes", "Docs.")
    assert code == 1 and payload["error_code"] == "state-first-evidence"
    assert all(word in payload["error"] for word in ("--review-status", "after --", "baseline"))

    code, payload = progress(run, planning, "section-02-docs", "baseline", "--", *PASS)
    assert code == 0, payload
    code, payload = progress(run, planning, "section-02-docs", "done", "--notes", "Docs.", "--review-status", "pass",
                             "--", *PASS)
    assert code == 0, payload
    saved = entry(planning, "section-02-docs")
    assert saved["review"] == "pass" and saved["candidate"]["command"] == PASS
    assert saved["baseline"]["outcome"] == saved["candidate"]["outcome"] == "passed"


def test_standard_progress_rejects_state_first_options(run, tmp_path):
    planning = make_plan(tmp_path / "planning")
    for extra in (("--stage", "done"), ("--stage", "red", "--", *PASS), ("--stage", "red", "--file", "src/a.py")):
        code, payload = run("implement-progress", "--planning-dir", str(planning), "--section", "section-01-normalize", *extra)
        assert code == 1 and "state-first" in payload["error"]
    assert not (planning / "implementation/forge-progress.json").exists()


def test_next_section_and_allowance_use_state_first_status(run, workspace):
    planning = ready(run, workspace)
    code, payload = run("next-section", "--planning-dir", str(planning))
    assert code == 0 and payload["next_section"] == "section-01-trim"
    code, payload = progress(run, planning, "section-01-trim", "done", "--notes", "Trimmed.",
                             "--allowance-seconds", "0", "--", *PASS)
    assert code == 1 and "exhausted" in payload["error"]
    assert entry(planning, "section-01-trim")["state"] == "pending"


def test_done_credits_files_across_red_and_failed_checks(run, workspace):
    planning = ready(run, workspace)
    (workspace / "tests").mkdir()
    (workspace / "tests/test_labels.py").write_text("assert True\n")
    code, payload = progress(run, planning, "section-01-trim", "red", "--command", "pytest", "--result", "1 failed")
    assert code == 0, payload
    (workspace / "src/labels.py").write_text("def trim(value):\n    return value.strip()\n")
    assert progress(run, planning, "section-01-trim", "done", "--notes", "Trimmed.", "--", *FAIL)[0] == 1
    code, payload = progress(run, planning, "section-01-trim", "done", "--notes", "## Decision\nKeep case.", "--", *PASS)
    assert code == 0, payload
    assert entry(planning, "section-01-trim")["files"] == ["src/labels.py", "tests/test_labels.py"]
    assert "### Decision" in (planning / "notes.md").read_text()
    assert run("status", "--path", str(planning))[1]["notes"][-1]["text"] == "### Decision\nKeep case."


def test_allowance_is_untouched_when_the_tree_cannot_be_observed(forge, run, workspace, monkeypatch):
    planning = ready(run, workspace)
    observe = forge.state_first.observe_tree

    def unreadable(*args):
        raise PermissionError("unreadable source")

    monkeypatch.setattr(forge.state_first, "observe_tree", unreadable)
    code, payload = progress(run, planning, "section-01-trim", "done", "--notes", "x", "--allowance-seconds", "100", "--", *PASS)
    assert code == 1 and "unreadable source" in payload["error"]
    assert not (planning / "implementation/verification/allowance.json").exists()
    monkeypatch.setattr(forge.state_first, "observe_tree", observe)
    code, payload = progress(run, planning, "section-01-trim", "done", "--notes", "x", "--allowance-seconds", "100", "--", *PASS)
    assert code == 0, payload


def test_baseline_binds_original_source_and_reruns_only_explicit_argv(run, workspace):
    (workspace / ".planning/plan.md").write_text(PLAN.replace("`pytest tests/test_labels.py` passes", "the public API stays"))
    planning = ready(run, workspace)
    assert progress(run, planning, "section-01-trim", "started")[0] == 0
    labels = workspace / "src/labels.py"
    original = labels.read_text()
    labels.write_text("edited = True\n")
    code, payload = progress(run, planning, "section-01-trim", "baseline", "--", *PASS)
    assert code == 1 and payload["error_code"] == "state-first-baseline" and "src/labels.py" in payload["error"]
    labels.write_text(original)
    assert progress(run, planning, "section-01-trim", "baseline", "--", *PASS)[0] == 0
    labels.write_text("def trim(value):\n    return value.strip()\n")

    status = json.loads((planning / "status.json").read_text())
    marker = workspace / "pwned.txt"
    status["sections"]["section-01-trim"]["baseline"]["command"] = [sys.executable, "-c", f"open({str(marker)!r}, 'w')"]
    (planning / "status.json").write_text(json.dumps(status))
    for extra in ((), ("--", *PASS)):
        code, payload = progress(run, planning, "section-01-trim", "done", "--notes", "x", "--review-status", "pass", *extra)
        assert code == 1 and payload["error_code"] == "state-first-candidate"
    assert not marker.exists()


def test_finish_rejects_another_target(run, workspace, tmp_path_factory):
    planning = ready(run, workspace)
    other = tmp_path_factory.mktemp("other")
    code, payload = run("implement-verify", "--planning-dir", str(planning), "--target-dir", str(other), "--", *PASS)
    assert code == 1 and payload["error_code"] == "state-first-target"
    assert "final" not in json.loads((planning / "status.json").read_text())


@pytest.mark.parametrize("text", ["risk: low", "`risk: none`", "risk: minimal risk", "risk: n/a", "risk: -"])
def test_risk_tags_that_mean_no_risk_do_not_trigger(forge, text):
    assert forge.state_first.triggers({"cells": [text]}, team=False) == []


@pytest.mark.parametrize("text", ["risk: no tests cover the parser", "risk: low-level socket handling",
                                  "risk: minimal downtime window required"])
def test_risk_tags_with_a_reason_trigger(forge, text):
    assert forge.state_first.triggers({"cells": [text]}, team=False) == [text]


def test_baseline_origin_counts_edits_before_the_first_call(run, workspace):
    planning = ready(run, workspace)
    (workspace / "src/labels.py").write_text("edited = True\n")
    code, payload = progress(run, planning, "section-01-trim", "baseline", "--", *PASS)
    assert code == 1 and payload["error_code"] == "state-first-baseline"


def test_baseline_origin_follows_row_changes_and_reactivation(run, workspace):
    planning = ready(run, workspace)
    (workspace / "src/helpers.py").write_text("HELP = 1\n")
    assert progress(run, planning, "section-01-trim", "started")[0] == 0
    (planning / "plan.md").write_text(PLAN.replace("src/labels.py, tests", "src/labels.py, src/helpers.py, tests"))
    assert progress(run, planning, "section-01-trim", "baseline", "--", *PASS)[0] == 0
    assert progress(run, planning, "section-01-trim", "done", "--notes", "x", "--", *PASS)[0] == 0
    (workspace / "src/labels.py").write_text("def trim(value):\n    return value.strip()\n")
    assert progress(run, planning, "section-01-trim", "done", "--notes", "Edited after done.", "--", *PASS)[0] == 0
    assert progress(run, planning, "section-01-trim", "started")[0] == 0
    assert progress(run, planning, "section-01-trim", "baseline", "--", *PASS)[0] == 0


def test_damaged_snapshot_only_loses_change_listing(run, workspace):
    planning = ready(run, workspace)
    (planning / "implementation/state-snapshot.json").write_text("[]")
    code, payload = run("status", "--path", str(planning))
    assert code == 0 and payload["changed_since_status"] is None
    assert progress(run, planning, "section-01-trim", "started")[0] == 0
    assert run("status", "--path", str(planning))[1]["changed_since_status"] == {"changed": [], "added": [], "removed": []}


def test_programmatic_progress_callers_need_no_state_first_options(forge):
    args = argparse.Namespace(planning_dir="plan", section="section-01-a", stage="red", command="pytest", result="1 failed")
    assert forge.state_first.options_used(args) is False


def test_started_sections_cannot_silently_leave_the_plan(run, workspace):
    planning = ready(run, workspace)
    assert progress(run, planning, "section-01-trim", "done", "--notes", "Trimmed.")[0] == 0
    assert progress(run, planning, "section-02-docs", "started")[0] == 0
    (planning / "plan.md").write_text("".join(line for line in PLAN.splitlines(keepends=True) if "section-02-docs" not in line))
    code, payload = run("implement-verify", "--planning-dir", str(planning), "--", *PASS)
    assert code == 1 and payload["postflight"]["blocking_gates"] == ["state-status"]
    status = run("status", "--path", str(planning))[1]
    assert status["orphaned_sections"] == ["section-02-docs"] and "restore" in status["next_action"]


def test_pretty_status_shows_plan_repairs(forge, run, workspace):
    planning = ready(run, workspace)
    (planning / "plan.md").write_text(PLAN.replace("## Goal\n", "## Aim\n"))
    code, payload = run("status", "--path", str(planning))
    assert code == 0 and payload["plan_errors"]
    pretty = forge.output.format_pretty(payload)
    assert "Plan: needs repair" in pretty and "## Goal" in pretty and "Next action: fix plan.md" in pretty
