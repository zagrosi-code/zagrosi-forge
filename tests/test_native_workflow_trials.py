"""Native harness oracles and real process interruption, without model calls."""
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

from forge_test_helpers import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import native_plugin_smoke as native
import native_trial_session as sessions
import native_workflow_checks as checks
import native_workflow_trials as trials


@pytest.fixture
def workspace(tmp_path):
    destination = tmp_path / "workspace"
    shutil.copytree(ROOT / "examples/first-task", destination)
    (destination / "unrelated.txt").write_text("user work")
    return destination


def session(workspace, tmp_path, source, **kwargs):
    return sessions.run_session([sys.executable, "-c", source], workspace, tmp_path / "attempt", "task", host="codex",
                                plugin_root=tmp_path / "plugin", timeout=3, **kwargs)


def test_matrix_covers_each_host_depth_trigger_and_explicit_continuation_direction():
    rows = trials.matrix()
    workflow = [row for row in rows if row["trigger"] != "unsupported"]
    assert len(rows) == 14 and len({row["id"] for row in rows}) == 14
    assert {(row["host"], row["depth"], row["trigger"]) for row in workflow} == {
        (host, depth, trigger) for host in ("codex", "claude")
        for depth in ("lean", "standard", "deep") for trigger in ("direct", "indirect")}
    for row in workflow:
        assert (row["host"] == row["resume_host"]) is (row["trigger"] == "indirect")
        prompt = trials.prompt(row, "plan")
        assert ("zagrosi-forge" in prompt) is (row["trigger"] == "direct")
        assert "SKILL.md" not in prompt and "Plan only" in prompt
        assert "Use .planning itself as the planning root" in prompt


def test_actual_process_is_killed_after_new_checkpoint_and_retained(workspace, tmp_path):
    event = {"stage": "red", "section": "section-01-labels", "snapshot": {"version": 1}}
    code = ("import json,pathlib,time; p=pathlib.Path(" + repr(sessions.PROGRESS) + "); "
            "p.parent.mkdir(parents=True); p.write_text(" + repr(json.dumps({"events": [event]})) + "); time.sleep(30)")
    report = session(workspace, tmp_path, code, interrupt=True)
    assert report["stop"] == "checkpoint" and report["returncode"] != 0
    assert report["checkpoint"] == event and report["termination_error"] is None
    assert not report["success"]
    assert (tmp_path / "attempt/session.json").is_file()
    with pytest.raises(FileExistsError):
        session(workspace, tmp_path, "pass")


def test_existing_checkpoint_cannot_masquerade_as_new_interruption(workspace, tmp_path):
    path = workspace / sessions.PROGRESS
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"events": [{"stage": "red", "snapshot": {}}]}))
    with pytest.raises(ValueError, match="new model-written"):
        session(workspace, tmp_path, "pass", interrupt=True)


def test_deliberate_kill_preserves_partial_event_without_hiding_terminal_errors(workspace, tmp_path):
    event = {"stage": "red", "snapshot": {"version": 1}}
    code = ("import json,pathlib,sys,time; sys.stdout.write('{\\\"type\\\":'); sys.stdout.flush(); "
            "p=pathlib.Path(" + repr(sessions.PROGRESS) + "); p.parent.mkdir(parents=True); "
            "p.write_text(" + repr(json.dumps({"events": [event]})) + "); time.sleep(30)")
    report = session(workspace, tmp_path, code, interrupt=True)
    assert report["stop"] == "checkpoint" and report["partial_event"]
    assert not report["invalid_events"] and not report["reader_error"] and not report["terminal_error"]


@pytest.mark.parametrize("source", ["pass", "print('red checkpoint saved')"])
def test_early_exit_or_prose_is_neither_model_completion_nor_interruption(workspace, tmp_path, source):
    report = session(workspace, tmp_path, source, interrupt=True)
    assert not report["success"] and report["stop"] is None and report["checkpoint"] is None


def test_timeout_does_not_claim_checkpoint(workspace, tmp_path):
    report = sessions.run_session([sys.executable, "-c", "import time; time.sleep(30)"], workspace,
        tmp_path / "attempt", "task", host="codex", plugin_root=ROOT, timeout=.1, interrupt=True)
    assert report["stop"] == "timeout" and report["checkpoint"] is None and not report["success"]


def test_output_budget_stops_process_and_caps_retained_log(workspace, tmp_path):
    report = session(workspace, tmp_path, "import sys,time; print('x'*20000, flush=True); time.sleep(30)", output_limit=1000)
    assert report["stop"] == "output_limit" and not report["success"]
    assert (tmp_path / "attempt/events.jsonl").stat().st_size == 1000


def test_fast_successful_output_cannot_race_the_budget_check(workspace, tmp_path):
    code = "import json; print(json.dumps({'type':'turn.completed', 'padding':'x'*5000}))"
    report = session(workspace, tmp_path, code, output_limit=100)
    assert report["output_exceeded"] and report["stdout_bytes"] > 100 and not report["success"]
    assert (tmp_path / "attempt/events.jsonl").stat().st_size == 100


def test_claude_registration_and_skill_use_come_from_native_events(workspace, tmp_path):
    events = [{"type": "system", "subtype": "init", "skills": sorted(sessions.EXPECTED_SKILLS)},
              {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": "zagrosi-forge:zagrosi-forge"}}]}},
              {"type": "result", "result": "complete", "is_error": False}]
    code = "import json; events=" + repr(events) + "; [print(json.dumps(event)) for event in events]"
    report = sessions.run_session([sys.executable, "-c", code], workspace, tmp_path / "attempt", "task",
        host="claude", plugin_root=ROOT, timeout=3)
    assert report["success"] and set(report["registered_skills"]) == sessions.EXPECTED_SKILLS
    assert report["plugin_actions"] == [{"kind": "skill", "name": "zagrosi-forge:zagrosi-forge"}]


@pytest.mark.parametrize("events", [
    [{"type": "result", "is_error": False}],
    [{"type": "result", "is_error": True}],
    [{"type": "turn.completed"}, {"type": "turn.failed"}],
    [{"type": "turn.completed"}, {"type": "error"}],
    [{"type": "turn.completed"}, {"type": "item.completed", "item": {"type": "command_execution", "command": 123}}],
], ids=["foreign-result", "foreign-error", "failed-after-complete", "error-after-complete", "parser-thread-error"])
def test_successful_exit_does_not_hide_failed_or_invalid_native_stream(workspace, tmp_path, events):
    code = "import json; events=" + repr(events) + "; [print(json.dumps(event)) for event in events]"
    report = session(workspace, tmp_path, code)
    assert not report["success"]


@pytest.mark.parametrize("mutation", [None, "fixed", "syntax", "removed-test", "changed-user-file"])
def test_red_checkpoint_requires_actual_original_failure_and_unchanged_inputs(workspace, monkeypatch, mutation):
    baseline = trials.files(workspace)
    event = {"stage": "red", "section": "section-01-labels", "snapshot": {"bound": True}}
    monkeypatch.setattr(checks, "runtime_module", lambda *args: SimpleNamespace(contract_snapshot=lambda *a, **kw: {"bound": True}))
    monkeypatch.setattr(checks, "admission", lambda *args: {"success": True})
    if mutation == "fixed":
        path = workspace / "labels.py"
        path.write_text(path.read_text().replace("return value", "return value.strip()"))
    elif mutation == "syntax":
        (workspace / "labels.py").write_text("invalid !")
    elif mutation == "removed-test":
        (workspace / "tests/test_labels.py").write_text("")
    elif mutation == "changed-user-file":
        (workspace / "unrelated.txt").write_text("changed")
    # Extra characterization is allowed; it does not replace the original red oracle.
    (workspace / "tests/test_more.py").write_text("import unittest\n")
    report = checks.interrupted(ROOT, workspace, "lean", baseline,
        {"stop": "checkpoint", "returncode": -9, "termination_error": None, "checkpoint": event})
    assert report["success"] is (mutation is None), report


def test_planning_only_rejects_added_test_even_with_admitted_plan(workspace, monkeypatch):
    baseline = trials.files(workspace)
    monkeypatch.setattr(checks, "admission", lambda *args: {"success": True})
    (workspace / "tests/extra.py").write_text("pass\n")
    report = checks.planned(ROOT, workspace, "lean", baseline)
    assert not report["success"] and report["outside_planning"] == ["tests/extra.py"]


def test_admitted_multisection_plan_still_violates_first_task_compact_contract(workspace):
    from forge_test_helpers import write_non_topological_section_fixture
    sections = write_non_topological_section_fixture(workspace / ".planning")
    index = sections / "index.md"
    text = index.read_text().replace("section-02-endpoints\n", "").replace("| section-02-endpoints | section-01-foundation |\n", "").replace(", section-02-endpoints", "")
    index.write_text(text.replace("section-03-storage", "section-02-storage"))
    (sections / "section-02-endpoints.md").unlink()
    storage = sections / "section-03-storage.md"
    (sections / "section-02-storage.md").write_text(storage.read_text().replace("section-03-storage", "section-02-storage"))
    storage.unlink()
    result = checks.admission(ROOT, workspace, "lean")
    assert result["report"]["success"], result
    assert result["section_shape"]["section_count"] == 2
    assert not result["success"]


@pytest.mark.parametrize("body,passes", [
    ('if not isinstance(value, str): raise TypeError("label must be a string")\n    return value.strip()', True),
    ('return str(value).strip()', False), ('return value.strip().lower()', False),
    ('return " ".join(value.split())', False), ('return value', False),
])
def test_behavior_oracle_rejects_contract_regressions(workspace, body, passes):
    (workspace / "labels.py").write_text("def normalize(value):\n    " + body + "\n")
    result = checks.execute([sys.executable, "-B", "-c", checks.ORACLE], workspace)
    assert (result["returncode"] == 0) is passes


@pytest.mark.parametrize("signature", ["value, /", "value=''"])
def test_behavior_oracle_preserves_required_keyword_capable_argument(workspace, signature):
    (workspace / "labels.py").write_text(f'def normalize({signature}):\n    if not isinstance(value, str): raise TypeError("label must be a string")\n    return value.strip()\n')
    result = checks.execute([sys.executable, "-B", "-c", checks.ORACLE], workspace)
    assert result["returncode"] != 0


def test_native_cli_overrides_preserve_literal_plugin_identity():
    install = {"marketplace_name": "fixture", "marketplace": "/a path", "codex_plugin": "/plugin", "claude_plugin": "/plugin"}
    command = sessions.native_command("codex", "pinned-model", "medium", install)
    assert "plugins.zagrosi-forge@fixture.enabled=true" in command
    assert "plugins.zagrosi-forge@zagrosi.enabled=false" in command
    assert command[-1] == "-" and "--ignore-user-config" not in command


def test_staging_never_copies_auth_or_modifies_user_settings(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / ".codex-plugin").mkdir(parents=True)
    (source / ".codex-plugin/package-files.json").write_text('["README.md"]')
    (source / "README.md").write_text("package bytes")
    user_home = tmp_path / "existing-home"
    user_home.mkdir()
    (user_home / "config.toml").write_text("existing user settings")
    (user_home / "auth.json").write_text("must not be copied")

    def install(command, cwd, env):
        settings = Path(env["CODEX_HOME"])
        assert settings != user_home
        if "--json" not in command:
            return "added"
        name = command[3].split("@", 1)[1]
        target = settings / "plugins/cache" / name / "zagrosi-forge/0.3.0"
        target.mkdir(parents=True)
        shutil.copy2(source / "README.md", target / "README.md")
        (settings / "auth.json").write_text("disposable auth must not be copied either")
        return json.dumps({"installedPath": str(target)})

    monkeypatch.setattr(native, "run", install)
    result = native.prepare_live(source, tmp_path / "staging", codex_home=user_home)
    assert (user_home / "config.toml").read_text() == "existing user settings"
    assert (user_home / "auth.json").read_text() == "must not be copied"
    assert [path.name for path in Path(result["temporary_cache"]).rglob("*") if path.is_file()] == ["README.md"]
    assert native.mismatches(source, Path(result["codex_plugin"]), ["README.md"]) == []


def test_pending_and_failed_rows_never_become_passed(tmp_path):
    trials.write(tmp_path / "matrix.json", {"rows": trials.matrix(), "models": {"codex": "pinned", "claude": None}})
    case = tmp_path / "codex-lean-direct"
    case.mkdir()
    trials.write(case / "result.json", {"success": None, "status": "partial", "cross_host_pending": True, "sessions": [{}, {}, {}]})
    report = trials.report(tmp_path)
    assert not report["success"] and report["model_sessions"] == 3
    assert report["rows"][0]["status"] == "partial"
    assert all(row["status"] == "blocked" for row in report["rows"] if row["host"] == "claude")
    trials.write(case / "cross-host-result.json", {"success": False, "status": "failed"})
    assert trials.report(tmp_path)["rows"][0]["status"] == "failed"


@pytest.fixture
def unsupported_matrix(tmp_path, workspace, monkeypatch):
    directory = tmp_path / "matrix"
    trial = directory / "codex-unsupported"
    trial.mkdir(parents=True)
    workspace.rename(trial / "workspace")
    package = tmp_path / "plugin"
    package.mkdir()
    installation = {"codex_plugin": str(package), "claude_plugin": str(package), "marketplace_name": "test", "marketplace": str(package)}
    trials.write(directory / "matrix.json", {"rows": [{"id": "codex-unsupported", "host": "codex", "trigger": "unsupported"}],
        "models": {"codex": "pinned"}, "efforts": {"codex": "medium"}, "plugin_root": str(package),
        "plugin_sha256": {}, "installation": installation, "total_timeout": 10, "timeout": 5,
        "evaluator_sha256": trials.evaluator_identity()})
    trials.write(trial / "baseline.json", trials.files(trial / "workspace"))
    monkeypatch.setattr(trials, "authenticated", lambda host: True)
    return directory


@pytest.mark.parametrize("reply", ["Bonjour", "", "Hello", "I completed the software workflow"])
def test_unsupported_case_requires_the_actual_correct_reply(unsupported_matrix, monkeypatch, reply):
    monkeypatch.setattr(trials, "run_session", lambda *a, **kw: {"success": True, "final_text": reply, "plugin_actions": [], "registered_skills": []})
    result = trials.run(unsupported_matrix, "codex-unsupported")
    assert result["success"] is (reply == "Bonjour")


def test_auth_block_is_retained_and_host_cli_can_resume_without_replaying_attempts(unsupported_matrix, monkeypatch, capsys):
    monkeypatch.setattr(trials, "authenticated", lambda host: False)
    assert trials.run(unsupported_matrix, "codex-unsupported")["status"] == "blocked"
    monkeypatch.setattr(trials, "authenticated", lambda host: True)
    monkeypatch.setattr(trials, "run_session", lambda *a, **kw: {"success": True, "final_text": "Bonjour", "plugin_actions": [], "registered_skills": []})
    monkeypatch.setattr(sys, "argv", ["native_workflow_trials.py", "run", str(unsupported_matrix), "--host", "codex"])
    assert trials.main() == 0
    assert json.loads(capsys.readouterr().out)["success"]
    assert (unsupported_matrix / "codex-unsupported/blocked-001.json").is_file()
    with pytest.raises(ValueError, match="already retained"):
        trials.run(unsupported_matrix, "codex-unsupported")


def test_evaluator_drift_blocks_the_model_before_running(unsupported_matrix, monkeypatch):
    monkeypatch.setattr(trials, "evaluator_identity", lambda: {"changed": "source"})
    monkeypatch.setattr(trials, "run_session", lambda *a, **kw: pytest.fail("model must not run"))
    result = trials.run(unsupported_matrix, "codex-unsupported")
    assert not result["success"] and result["sessions"] == [] and "Evaluator source changed" in result["error"]


def test_later_cross_host_resume_restores_exact_checkpoint_and_retains_previous_completion(unsupported_matrix, monkeypatch):
    directory = unsupported_matrix
    trial = directory / "codex-unsupported"
    workspace = trial / "workspace"
    original = (workspace / "labels.py").read_text()
    event = {"stage": "red", "snapshot": {"original": True}}
    path = workspace / sessions.PROGRESS
    path.parent.mkdir(parents=True)
    trials.write(path, {"events": [event]})
    saved = trial / "checkpoint/workspace"
    shutil.copytree(workspace, saved)
    hashes = trials.files(saved)
    trials.write(trial / "checkpoint/evidence.json", {"checkpoint": event, "workspace_sha256": hashes})
    (workspace / "labels.py").write_text("previous completion")
    record = trials.read_json(directory / "matrix.json")
    record["rows"][0].update(trigger="direct", depth="lean", resume_host="claude")
    record["models"]["claude"] = "pinned-claude"
    record["efforts"]["claude"] = "medium"
    trials.write(directory / "matrix.json", record)
    trials.write(trial / "result.json", {"success": None, "cross_host_pending": True,
        "sessions": [{"phase": "implement", "checkpoint": event}]})

    def restored(*args):
        assert (workspace / "labels.py").read_text() == original
        assert trials.files(workspace) == hashes
        return {"success": True}

    def continue_model(*args, **kwargs):
        assert kwargs["host"] == "claude"
        (workspace / "labels.py").write_text("fresh continuation")
        return {"success": True, "registered_skills": sorted(sessions.EXPECTED_SKILLS)}

    monkeypatch.setattr(trials, "interrupted", restored)
    monkeypatch.setattr(trials, "run_session", continue_model)
    monkeypatch.setattr(trials, "completed", lambda *args: {"success": args[-1] == event})
    result = trials.resume(directory, "codex-unsupported")
    assert result["success"] and result["restored_checkpoint"]["success"]
    assert (trial / "previous-completion/labels.py").read_text() == "previous completion"
    assert (workspace / "labels.py").read_text() == "fresh continuation"
    assert trials.files(saved) == hashes
    with pytest.raises(ValueError, match="No unattempted"):
        trials.resume(directory, "codex-unsupported")
