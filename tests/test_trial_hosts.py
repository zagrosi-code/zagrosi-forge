"""Offline native stream contracts; no live model results are implied."""
import pytest
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys

from test_coding_trials import trials

from coding_trial_runner import ClaudeTelemetry, writer_command, writer_environment
from trial_matrix import accepted_outcomes

FAKE_CLAUDE = r'''
import json, os, sys
if sys.argv[1:] == ["--version"]:
    sys.exit(print("2.1.304 (Claude Code)"))
sys.stdin.read()
with open(os.environ["FAKE_CLAUDE_ENV"], "w") as handle:
    json.dump({"names": sorted(os.environ), "auto_memory": os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY")}, handle)
print(json.dumps({"type": "result", "is_error": False, "result": "done"}), flush=True)
'''
# Variables a writer started from inside Claude Code would inherit.
HOST_SESSION = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "parent", "CLAUDE_CODE_EFFORT_LEVEL": "low",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "older-model"}


def fake_claude(directory):
    """Write a POSIX `claude` into directory; return it and the path to pass as FAKE_CLAUDE_ENV."""
    directory.mkdir(parents=True, exist_ok=True)
    script, executable = directory / "fake_claude.py", directory / "claude"
    script.write_text(FAKE_CLAUDE)
    executable.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    executable.chmod(0o755)
    return executable, directory / "environment.json"


def assert_started_without_host_session(seen, removed):
    observed = json.loads(seen.read_text())
    assert observed["auto_memory"] == "1" and "CLAUDE_CONFIG_DIR" in observed["names"]
    assert set(HOST_SESSION).isdisjoint(observed["names"]) and set(HOST_SESSION) <= set(removed)


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
    # --restricted confines Read and Grep to working directories; the skills read their own references.
    assert argv[argv.index("--add-dir") + 1] == "/plugin with spaces" and "--restricted" in argv
    assert "--bare" not in argv and "--safe-mode" not in argv
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    # A later session cannot find this one's transcript on disk.
    assert "--no-session-persistence" in argv
    plain = writer_command("claude", "claude", "model", "high", {"plain_agent": True, "plugin_root": "/plugin"})
    assert "--plugin-dir" not in plain and "--add-dir" not in plain


def test_claude_writer_environment_drops_host_session_variables_and_auto_memory():
    provider = {"CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock", "AWS_REGION": "us-east-1",
                "CLAUDE_CODE_USE_VERTEX": "1", "ANTHROPIC_VERTEX_PROJECT_ID": "project", "CLAUDE_CODE_SKIP_VERTEX_AUTH": "1",
                "CLAUDE_CODE_CLIENT_CERT": "/cert.pem", "ANTHROPIC_CUSTOM_HEADERS": "X-Gateway: trial"}
    inherited = {"PATH": "/bin", "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "parent", "CLAUDE_PLUGIN_DATA": "/data",
                 "CLAUDE_CODE_EFFORT_LEVEL": "low", "ANTHROPIC_DEFAULT_OPUS_MODEL": "older-model",
                 "CLAUDE_CODE_USE_POWERSHELL_TOOL": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0",
                 "CLAUDE_CONFIG_DIR": "/config", "ANTHROPIC_API_KEY": "key", **provider}
    environment, removed = writer_environment("claude", inherited)
    assert removed == ["ANTHROPIC_DEFAULT_OPUS_MODEL", "CLAUDECODE", "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
                       "CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_USE_POWERSHELL_TOOL",
                       "CLAUDE_PLUGIN_DATA"]
    # Provider routing stays, so Bedrock, Vertex AI and gateway writers reach the backend they are configured for.
    assert environment == {"PATH": "/bin", "CLAUDE_CONFIG_DIR": "/config", "ANTHROPIC_API_KEY": "key", **provider,
                           "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    assert writer_environment("codex", inherited) == (inherited, [])


@pytest.mark.skipif(os.name == "nt", reason="The fake writer uses a POSIX executable script")
def test_claude_writer_starts_without_the_launching_session_environment(tmp_path):
    workspace = tmp_path / "trial/workspace"
    workspace.mkdir(parents=True)
    executable, seen = fake_claude(tmp_path)
    inherited = {**os.environ, **HOST_SESSION, "CLAUDE_CONFIG_DIR": str(tmp_path / "config"), "FAKE_CLAUDE_ENV": str(seen)}
    result = subprocess.run([sys.executable, str(trials.ROOT / "tools/coding_trial_runner.py"), "--host", "claude",
                             "--model", "model", "--effort", "max", "--claude", str(executable)],
                            cwd=workspace, input="task", env=inherited, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    telemetry = json.loads((tmp_path / "trial/telemetry.json").read_text())
    assert_started_without_host_session(seen, telemetry["removed_environment"])


@pytest.mark.parametrize("host,effort,accepted", [
    ("codex", "xhigh", True), ("codex", "max", False), ("codex", "ultra", False),
    ("claude", "xhigh", True), ("claude", "max", True), ("claude", "ultra", False),
])
def test_writer_accepts_only_efforts_each_host_supports(host, effort, accepted):
    # Claude Code ignores an unknown --effort with a warning, so only the harness can reject it.
    if not accepted:
        with pytest.raises(ValueError, match=f"{host} .*{effort}"):
            writer_command(host, host, "model", effort, {})
        return
    argv = writer_command(host, host, "model", effort, {})
    if host == "codex":
        assert f'model_reasoning_effort="{effort}"' in argv
    else:
        assert argv[argv.index("--effort") + 1] == effort


def test_claude_writer_gives_every_arm_subagent_and_task_tools():
    forge = writer_command("claude", "claude", "model", "high", {"plugin_root": "/plugin"})
    plain = writer_command("claude", "claude", "model", "high", {"plain_agent": True, "plugin_root": "/plugin"})
    for argv in (forge, plain):
        tools = argv[argv.index("--tools") + 1].split(",")
        assert argv[argv.index("--allowedTools") + 1].split(",") == tools
        assert {"Agent", "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "Skill", "Bash"} <= set(tools)
        assert "TodoWrite" not in tools  # Off by default in Claude Code 2.1.304; disabled names are dropped silently.
    assert forge[forge.index("--tools") + 1] == plain[plain.index("--tools") + 1]


def test_claude_model_usage_counts_subagents_and_keeps_unknowns():
    telemetry = ClaudeTelemetry()
    assert telemetry.summary()["totals_source"] is None
    stats = {"spawned": 1, "completed": 1}
    usage = {"input_tokens": 10, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 5, "output_tokens": 4}
    telemetry.observe({"type": "result", "subagent_stats": stats, "usage": usage, "modelUsage": {
        "lead-model": {"inputTokens": 10, "outputTokens": 4, "cacheReadInputTokens": 20, "cacheCreationInputTokens": 5, "costUSD": .1},
        "subagent-model": {"inputTokens": 7, "outputTokens": 3, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 2}}}, 1)
    report = telemetry.summary()
    assert report["totals_source"] == "modelUsage" and report["subagent_stats"] == stats
    assert {key: report["totals"][key] for key in ("input_tokens", "cached_input_tokens", "cache_creation_input_tokens",
                                                   "uncached_input_tokens", "output_tokens")} == {
        "input_tokens": 44, "cached_input_tokens": 20, "cache_creation_input_tokens": 7, "uncached_input_tokens": 17, "output_tokens": 7}
    assert report["top_level_totals"]["input_tokens"] == 35 and report["top_level_totals"]["output_tokens"] == 4
    assert set(report["model_usage"]) == {"lead-model", "subagent-model"}
    telemetry.observe({"type": "result", "usage": usage, "modelUsage": {}}, 2)
    report = telemetry.summary()
    assert report["totals_source"] == "result usage" and report["totals"]["input_tokens"] == 35
    for model_usage in ({"model": "bad"}, {"model": {"inputTokens": -1, "outputTokens": True}}):
        telemetry.observe({"type": "result", "usage": usage, "modelUsage": model_usage, "subagent_stats": "bad"}, 3)
        report = telemetry.summary()
        # A malformed whole-tree count stays unknown instead of falling back to the smaller top-level figure.
        assert report["totals"]["input_tokens"] is None and report["totals"]["output_tokens"] is None
        assert report["top_level_totals"]["input_tokens"] == 35 and report["subagent_stats"] is None


def test_claude_telemetry_reports_requested_tools_missing_from_init():
    registered = ["Task", "Bash", "Edit", "Glob", "Grep", "Read", "Skill", "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "Write"]
    telemetry = ClaudeTelemetry()
    assert telemetry.summary()["missing_tools"] is None
    telemetry.observe({"type": "system", "subtype": "init", "tools": registered}, 0)
    assert telemetry.summary()["missing_tools"] == [] and telemetry.summary()["registered_tools"] == sorted(registered)
    telemetry.observe({"type": "system", "subtype": "init", "tools": [name for name in registered if name not in {"Task", "TaskCreate"}]}, 0)
    assert telemetry.summary()["missing_tools"] == ["Task", "TaskCreate"]


def test_runner_cli_rejects_unsupported_effort_before_launch(monkeypatch):
    import coding_trial_runner as runner
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--host", "codex", "--model", "model", "--effort", "max"])
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: pytest.fail("no writer may start"))
    with pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == 2


def test_compare_rejects_unsupported_effort_before_scheduling(tmp_path, monkeypatch):
    import trial_matrix as matrix
    destination = tmp_path / "comparison"
    monkeypatch.setattr(matrix, "run_trial", lambda *args: pytest.fail("no attempt may start"))
    monkeypatch.setattr(matrix.sys, "argv", ["trial_matrix", "compare", str(destination), "--model", "model",
                                             "--effort", "max", "--cases", "godfile", "--repeats", "1"])
    with pytest.raises(SystemExit) as stopped:
        matrix.main()
    assert stopped.value.code == 2 and not destination.exists()
    calls = []
    monkeypatch.setattr(matrix, "run_trial", lambda *args: calls.append(args))
    monkeypatch.setattr(matrix.sys, "argv", [*matrix.sys.argv, "--host", "claude"])
    assert matrix.main() == 1  # Pending attempts cannot claim success.
    assert len(calls) == 2 and calls[0][3][calls[0][3].index("--effort") + 1] == "max"


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
