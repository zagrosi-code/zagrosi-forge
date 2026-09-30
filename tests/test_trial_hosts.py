"""Offline native stream contracts; no live model results are implied."""
import pytest
import json
import shutil
import subprocess
from pathlib import Path
import sys

from test_coding_trials import trials

from coding_trial_runner import ClaudeTelemetry, writer_command
from trial_matrix import accepted_outcomes


def test_claude_final_usage_is_counted_once_and_missing_stays_unknown():
    telemetry = ClaudeTelemetry()
    assert telemetry.summary()["totals"]["input_tokens"] is None
    telemetry.observe({"type": "assistant", "message": {"model": "reported-model", "usage": {"input_tokens": 99},
                       "content": [{"type": "tool_use", "id": "one", "name": "Bash", "input": {"command": "python -m unittest"}}]}}, 1)
    telemetry.observe({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "one",
                       "content": "failure", "is_error": True}]}}, 3)
    telemetry.observe({"type": "result", "usage": {"input_tokens": 10, "cache_read_input_tokens": 20,
                       "cache_creation_input_tokens": 5, "output_tokens": 4}, "total_cost_usd": 0.03}, 4)
    report = telemetry.summary()
    assert report["totals"]["input_tokens"] == 35
    assert report["totals"]["uncached_input_tokens"] == 10
    assert report["reported_cost_usd"] == 0.03
    assert report["observed_models"] == ["reported-model"]
    assert report["commands_by_phase"]["verification"]["failed_commands"] == 1
    assert report["commands_by_phase"]["verification"]["observed_command_seconds"] == 2


def test_claude_writer_loads_only_explicit_trial_plugin_and_preserves_login():
    argv = writer_command("claude", "exact-claude", "selected-model", "high", {"plugin_root": "/plugin with spaces"})
    assert argv[0] == "exact-claude"
    assert argv[argv.index("--plugin-dir") + 1] == "/plugin with spaces"
    assert "--bare" not in argv and "--safe-mode" not in argv
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert "--plugin-dir" not in writer_command("claude", "claude", "model", "high", {"plain_agent": True, "plugin_root": "/plugin"})
    with pytest.raises(ValueError, match="xhigh"):
        writer_command("claude", "claude", "model", "xhigh", {})


def test_claude_malformed_optional_events_and_cost_remain_unknown():
    telemetry = ClaudeTelemetry()
    for message in ("unexpected", {"model": [], "content": [None, {"type": "tool_use", "name": "Bash", "input": []}]}):
        telemetry.observe({"type": "assistant", "message": message}, 1)
    for cost in (float("inf"), float("nan"), -1, True, "unknown"):
        telemetry.observe({"type": "result", "total_cost_usd": cost}, 2)
        assert telemetry.summary()["reported_cost_usd"] is None
    assert telemetry.summary()["commands_by_phase"] == {}


def test_cost_per_accepted_includes_failed_attempts_and_never_hides_unknowns():
    rows = [{"status": status, "attempt_seconds": seconds, "reported_telemetry": {
             "reported_cost_usd": cost, "totals": {"input_tokens": 100, "output_tokens": 20}}}
            for status, seconds, cost in (("passed", 10, 1), ("failed", 30, 2))]
    result = accepted_outcomes(rows)
    assert result["accepted_rate"] == .5
    assert result["elapsed_seconds"]["per_accepted"] == 40
    assert result["reported_cost_usd"]["per_accepted"] == 3
    assert result["tokens"]["input_tokens"]["per_accepted"] == 200
    assert result["interventions"]["total"] is None
    rows[0]["status"] = "failed"
    assert accepted_outcomes(rows)["reported_cost_usd"]["per_accepted"] is None
    rows.append({"status": "pending"})
    result = accepted_outcomes(rows)
    assert result["elapsed_seconds"]["total"] is None
    assert result["elapsed_seconds"]["observed"] == 40
    rows[0]["reported_telemetry"]["reported_cost_usd"] = float("inf")
    assert accepted_outcomes(rows)["reported_cost_usd"]["total"] is None


@pytest.mark.skipif(shutil.which("node") is None, reason="Node24 is needed for the TypeScript fixture")
@pytest.mark.parametrize("mutation", [None, "tenant", "transaction", "audit-order", "error-order", "exports"])
def test_typescript_oracle_preserves_auth_rollback_order_and_exports(tmp_path, mutation):
    workspace = tmp_path / "workspace"
    shutil.copytree(trials.PACK / "typescript-access", workspace)
    path = workspace / "src/access.ts"
    text = path.read_text()
    if mutation == "tenant":
        text = text.replace("!member || member.tenant !== actor.tenant", "!member")
    elif mutation == "transaction":
        text = text.replace("return store.transaction(work)", "return work()")
    elif mutation == "audit-order":
        text = text.replace("    store.write(result);\n", "").replace("    return { ...result };", "    store.write(result);\n    return { ...result };")
    elif mutation == "error-order":
        text = text.replace("throw new Error('unauthenticated')", "throw new Error('invalid member')")
    elif mutation == "exports":
        text += "\nexport const accidental = true;\n"
    path.write_text(text)
    result = subprocess.run(["node", str(trials.ROOT / "tools/typescript_trial_checks.mjs"), str(workspace), "typescript-access"],
                            capture_output=True, text=True, timeout=20)
    assert (result.returncode == 0) is (mutation is None), result.stderr
    if mutation is None:
        assert json.loads(result.stdout) == {"case": "typescript-access", "assertions": 394}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node24 is needed for the TypeScript fixture")
def test_typescript_trial_demands_independent_cleanup_evidence(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "typescript-access", plain_agent=True)
    result = trials.check(trial)
    assert result["behavior"]["success"], result
    assert not result["cleanup"]["success"]
    assert result["cleanup"]["source_changes"] == []


def test_real_interrupted_runner_preserves_checkpoint_and_retains_unknown_usage(tmp_path):
    trial = tmp_path / "trial"
    workspace = trial / "workspace"
    workspace.mkdir(parents=True)
    (trial / "prompt.md").write_text("Continue the admitted task")
    first = [sys.executable, "-c", "import pathlib,time;pathlib.Path('saved-checkpoint').write_text('red evidence');time.sleep(60)"]
    second = [sys.executable, "-c", "import pathlib;assert pathlib.Path('saved-checkpoint').read_text()=='red evidence';pathlib.Path('continued').write_text('fresh session')"]
    result = trials.run_sessions(trial, first, 5, resume_runner=second, interrupt_after=1)
    assert result["returncode"] == 0 and result["interruption"]["success"]
    assert result["sessions"][0]["process"]["timed_out"]
    assert (workspace / "continued").read_text() == "fresh session"
    telemetry = json.loads((trial / "telemetry.json").read_text())
    assert telemetry["totals"]["input_tokens"] is None
    assert telemetry["reported_cost_usd"] is None
    assert telemetry["interventions"] == 1


def test_session_finishing_early_does_not_claim_interruption(tmp_path):
    trial = tmp_path / "trial"; (trial / "workspace").mkdir(parents=True)
    (trial / "prompt.md").write_text("task")
    argv = [sys.executable, "-c", "pass"]
    result = trials.run_sessions(trial, argv, 5, resume_runner=argv, interrupt_after=1)
    assert result["returncode"] == 1 and not result["interruption"]["success"]
    assert len(result["sessions"]) == 1
