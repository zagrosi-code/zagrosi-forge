"""State-first plans keep plan.md, status.json and notes.md for the next session."""

import json

import pytest

from forge_test_helpers import ROOT
from runtime_support import load_runtime


PLAN = """# Plan: labels

## Goal
Trim label edges without changing case.

## Acceptance
- REQ-001: labels lose edge whitespace.
- Internal spaces stay.

## Sections
| Section | Files | Done when |
|---|---|---|
| section-01-trim | src/labels.py, tests/test_labels.py | `pytest tests/test_labels.py` passes |
| section-02-docs | README.md | README shows trimming |
"""


def cli(forge, capsys):
    def invoke(*args):
        code = forge.entrypoint.main(list(args))
        return code, json.loads(capsys.readouterr().out)
    return invoke


def write_workspace(root):
    (root / "src").mkdir()
    (root / "src/labels.py").write_text("def trim(value):\n    return value\n")
    planning = root / ".planning"
    planning.mkdir()
    (planning / "spec.md").write_text("# Labels\n\nREQ-001: Trim label edges.\n")
    return root


@pytest.fixture(scope="module")
def forge():
    return load_runtime(ROOT / "scripts/zagrosi_skills.py")


@pytest.fixture
def run(forge, capsys):
    return cli(forge, capsys)


@pytest.fixture
def workspace(tmp_path):
    return write_workspace(tmp_path)


def setup(run, workspace, *extra):
    return run("plan-setup", "--file", str(workspace / ".planning/spec.md"), "--target-dir", str(workspace), *extra)


def ready(run, workspace):
    (workspace / ".planning/plan.md").write_text(PLAN)
    code, payload = setup(run, workspace, "--state-first")
    assert code == 0, payload
    return workspace / ".planning"


def test_state_first_setup_writes_plan_status_notes(run, workspace):
    planning = workspace / ".planning"
    code, payload = setup(run, workspace, "--state-first")
    assert code == 1 and not payload["success"]
    assert payload["created"] == ["plan.md", "status.json", "notes.md"]
    draft = (planning / "plan.md").read_text()
    assert "# Plan: Labels" in draft and "- REQ-001: TODO" in draft
    assert any("TODO" in error for error in payload["errors"])
    assert json.loads((planning / "status.json").read_text())["sections"] == {}

    (planning / "plan.md").write_text(PLAN)
    code, payload = setup(run, workspace)  # status.json routes the rerun without the flag
    assert code == 0, payload
    assert payload["mode"] == "state-first"
    assert [item["section"] for item in payload["sections"]] == ["section-01-trim", "section-02-docs"]
    status = json.loads((planning / "status.json").read_text())
    assert status["mode"] == "state-first" and status["target_dir"] == ".."
    assert {name: entry["state"] for name, entry in status["sections"].items()} == {
        "section-01-trim": "pending", "section-02-docs": "pending"}
    assert "| plan | ready |" in (planning / "notes.md").read_text()
    assert payload["next_action"] == "start section-01-trim"
    assert payload["commands"]["start"][-4:] == ["--section", "section-01-trim", "--stage", "started"]
    assert not (planning / "sections").exists() and not (planning / "zagrosi_plan_config.json").exists()


@pytest.mark.parametrize(("old", "new", "message"), [
    ("## Goal\nTrim label edges without changing case.\n", "", "## Goal"),
    ("- REQ-001: labels lose edge whitespace.\n- Internal spaces stay.\n", "", "## Acceptance"),
    ("| Section | Files | Done when |", "| Name | Files | Done when |", "`Section` and `Files`"),
    ("section-01-trim |", "Trim |", "section-01-labels"),
    ("src/labels.py, tests/test_labels.py", " ", "lists no files"),
    ("src/labels.py, tests/test_labels.py", "../labels.py", "repo-relative"),
    ("section-02-docs |", "section-01-trim |", "twice"),
    ("README shows trimming", "TODO", "TODO"),
])
def test_plan_errors_name_the_missing_field(run, workspace, old, new, message):
    (workspace / ".planning/plan.md").write_text(PLAN.replace(old, new))
    code, payload = setup(run, workspace, "--state-first")
    assert code == 1
    assert any(message in error for error in payload["errors"]), payload["errors"]


def test_status_sync_keeps_progress_and_rejects_orphans(run, workspace):
    planning = ready(run, workspace)
    status = json.loads((planning / "status.json").read_text())
    status["sections"]["section-01-trim"] = {"state": "done", "host": "claude", "updated_at": "2026-10-20T00:00:00+00:00"}
    (planning / "status.json").write_text(json.dumps(status))
    (planning / "plan.md").write_text(PLAN.replace("section-02-docs", "section-03-docs"))
    code, payload = setup(run, workspace)
    assert code == 0, payload
    sections = json.loads((planning / "status.json").read_text())["sections"]
    assert sections["section-01-trim"]["state"] == "done"
    assert set(sections) == {"section-01-trim", "section-03-docs"}
    (planning / "plan.md").write_text(PLAN.replace("section-01-trim", "section-04-trim"))
    code, payload = setup(run, workspace)
    assert code == 1
    assert any("section-01-trim is done" in error for error in payload["errors"])


def test_status_reports_resume_state(run, workspace):
    planning = ready(run, workspace)
    with (planning / "notes.md").open("a") as notes:
        for index in range(4):
            notes.write(f"\n## 2026-10-20T0{index}:00:00+00:00 | section-01-trim | note | claude\n\nDecision {index}.\n")
    (workspace / "src/labels.py").write_text("def trim(value):\n    return value.strip()\n")
    (workspace / "src/extra.py").write_text("")
    before = {name: (planning / name).read_bytes() for name in ("status.json", "notes.md", "plan.md")}

    code, payload = run("status", "--path", str(planning))
    assert code == 0, payload
    assert payload["goal"] == "Trim label edges without changing case."
    assert [(item["section"], item["state"]) for item in payload["sections"]] == [
        ("section-01-trim", "pending"), ("section-02-docs", "pending")]
    assert [note["text"] for note in payload["notes"]] == ["Decision 1.", "Decision 2.", "Decision 3."]
    assert payload["last_verification"] is None
    assert payload["changed_since_status"] == {"changed": ["src/labels.py"], "added": ["src/extra.py"], "removed": []}
    assert payload["next_section"] == "section-01-trim"
    assert before == {name: (planning / name).read_bytes() for name in before}
    assert run("status", "--path", str(planning / "status.json"))[1]["goal"] == payload["goal"]


def test_default_plan_setup_keeps_scaffold(run, workspace):
    planning = workspace / ".planning"
    code, payload = setup(run, workspace)
    assert code == 0, payload
    assert payload["scaffold"]["created"]
    assert not any((planning / name).exists() for name in ("plan.md", "status.json", "notes.md"))


@pytest.mark.parametrize("prepare", ["depth", "sections", "config"])
def test_state_first_requires_lean_and_an_unplanned_directory(run, workspace, prepare):
    planning = workspace / ".planning"
    extra = ["--depth", "standard"] if prepare == "depth" else []
    if prepare == "sections":
        (planning / "sections").mkdir()
    elif prepare == "config":
        (planning / "zagrosi_plan_config.json").write_text("{}")
    code, payload = setup(run, workspace, "--state-first", *extra)
    assert code == 1 and not payload["success"]
    assert not (planning / "status.json").exists()


@pytest.mark.parametrize("command", ["implement-setup", "implement-record-section"])
def test_standard_implementation_commands_point_to_state_first_commands(run, workspace, command):
    planning = ready(run, workspace)
    args = ["--sections-dir", str(planning / "sections")]
    if command == "implement-record-section":
        args += ["--section", "section-01-trim"]
    code, payload = run(command, *args)
    assert code == 1 and payload["error_code"] == "state-first-plan"
    assert "implement-progress" in payload["error"]
    assert not (planning / "implementation/zagrosi_implement_state.json").exists()


def test_unrelated_status_json_keeps_default_workflows(run, workspace):
    planning = workspace / ".planning"
    (planning / "status.json").write_text('{"build": "green"}\n')
    code, payload = setup(run, workspace, "--state-first")
    assert code == 1 and "status.json" in payload["error"]
    code, payload = setup(run, workspace)
    assert code == 0 and payload["scaffold"]["created"] and payload["mode"] == "new"
    assert run("status", "--path", str(planning))[1].get("mode") != "state-first"


def test_plans_at_the_target_root_ignore_their_own_files(run, tmp_path):
    (tmp_path / "spec.md").write_text("# Labels\n")
    (tmp_path / "plan.md").write_text(PLAN.replace("README shows trimming", "`TODO` markers are removed"))
    code, payload = run("plan-setup", "--file", str(tmp_path / "spec.md"), "--target-dir", str(tmp_path), "--state-first")
    assert code == 0, payload
    assert json.loads((tmp_path / "status.json").read_text())["target_dir"] == "."
    with (tmp_path / "notes.md").open("a") as notes:
        notes.write("\nA later thought.\n")
    assert run("status", "--path", str(tmp_path))[1]["changed_since_status"] == {"changed": [], "added": [], "removed": []}
