"""Install status, update guidance and team records name the host that actually runs Forge."""
import json
import os
from pathlib import Path

import pytest

from forge_test_helpers import PLUGIN_VERSION, ROOT, run_cmd, run_raw
from test_compact_plan import make_plan
from test_team_collaboration import join_pair, repositories, team

STALE = "0.0.1"
UPDATE = "claude plugin marketplace update zagrosi"
PLUGIN_UPDATE = "claude plugin update zagrosi-forge@zagrosi"


def claude_config(tmp_path: Path, *versions: str, registry: str | None = None) -> Path:
    config = tmp_path / "claude"
    path = config / "plugins/installed_plugins.json"
    path.parent.mkdir(parents=True)
    rows = [{"scope": "user", "version": version, "installPath": str(config / "plugins/cache" / version)}
            for version in versions]
    path.write_text(registry or json.dumps({"version": 2, "plugins": {"zagrosi-forge@zagrosi": rows}}))
    return config


def host_env(config: Path, host: str | None = None) -> dict[str, str]:
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(config)}
    if host == "claude":
        env["CLAUDECODE"] = "1"
    elif host == "codex":
        env["CODEX_THREAD_ID"] = "019a-thread"
    return env


def update_check(tmp_path: Path, env: dict[str, str], *extra: str) -> dict:
    return run_cmd("update-check", "--plugin-root", str(ROOT), "--config", str(tmp_path / "codex/config.toml"),
                   *extra, env=env)


def test_update_check_reports_stale_claude_plugin(tmp_path):
    config = claude_config(tmp_path, STALE)

    status = update_check(tmp_path, host_env(config, "claude"))

    assert status["success"] is True and status["host"] == "claude"
    claude = status["claude"]
    assert claude["installed"] is True and claude["current"] is False
    assert claude["registry_path"] == str(config / "plugins/installed_plugins.json")
    assert [row["version"] for row in claude["installs"]] == [STALE]
    assert status["restart_required"] is True
    steps = status["next_steps"]
    # Codex steps keep their text and position; Claude Code steps follow, then the local-only note.
    assert steps[0].startswith("Run ") and "self-update" in steps[0]
    assert steps[1] == "Restart Codex after self-update reports changed cache or config."
    assert steps[-1].startswith("This check is local-only")
    claude_step = steps[2]
    assert STALE in claude_step and PLUGIN_VERSION in claude_step and "Codex" not in claude_step
    assert claude_step.index(UPDATE) < claude_step.index(PLUGIN_UPDATE)

    assert update_check(tmp_path, host_env(config))["host"] == "other"


def test_update_check_reports_current_claude_plugin(tmp_path):
    config = claude_config(tmp_path, PLUGIN_VERSION)
    env = host_env(config, "codex")
    run_cmd("self-update", "--plugin-root", str(ROOT), "--config", str(tmp_path / "codex/config.toml"),
            "--no-verify-codex", env=env)

    status = update_check(tmp_path, env)

    assert status["host"] == "codex" and status["claude"]["current"] is True
    assert status["restart_required"] is False
    assert status["next_steps"][0] == "Codex config and Zagrosi Forge plugin cache are already current."
    assert any(step.startswith("Claude Code:") and "already current" in step for step in status["next_steps"])
    assert not any(UPDATE in step for step in status["next_steps"])


def test_stale_claude_plugin_alone_requires_restart(tmp_path):
    config = claude_config(tmp_path, STALE)
    env = host_env(config)
    run_cmd("self-update", "--plugin-root", str(ROOT), "--config", str(tmp_path / "codex/config.toml"),
            "--no-verify-codex", env=env)

    status = update_check(tmp_path, env)

    assert status["cache"]["current"] is True and status["config"]["current"] is True
    assert status["restart_required"] is True
    assert status["next_steps"][0] == "Codex config and Zagrosi Forge plugin cache are already current."
    assert status["next_steps"][1].startswith("Claude Code: installed 0.0.1 (user) differs")


def test_update_check_without_claude_install_keeps_codex_guidance(tmp_path):
    status = update_check(tmp_path, host_env(tmp_path / "no-claude"))

    assert status["claude"]["installed"] is False and status["claude"]["current"] is None
    assert not any(step.startswith("Claude Code:") for step in status["next_steps"])
    assert status["restart_required"] is True  # Codex cache and config are still missing.


def doctor(env: dict[str, str], *extra: str) -> dict:
    return run_cmd("doctor", "--plugin-root", str(ROOT), "--strict", *extra, env=env)


def test_doctor_reports_claude_plugin_without_findings(tmp_path):
    baseline = doctor(host_env(tmp_path / "no-claude"))
    config = claude_config(tmp_path, STALE)

    stale = doctor(host_env(config, "claude"))

    for key in ("success", "score", "finding_count", "findings"):
        assert stale[key] == baseline[key], key
    assert stale["host"] == "claude" and baseline["host"] == "other"
    assert stale["claude_plugin"]["current"] is False
    assert len(stale["next_steps"]) == 1 and UPDATE in stale["next_steps"][0]
    assert baseline["claude_plugin"]["installed"] is False and baseline["next_steps"] == []


def test_malformed_claude_registry_reports_error(tmp_path):
    config = claude_config(tmp_path, registry="{")

    status = update_check(tmp_path, host_env(config, "claude"))
    checked = doctor(host_env(config))

    for payload, key in ((status, "claude"), (checked, "claude_plugin")):
        assert payload["success"] is True
        assert payload[key]["installed"] is None and payload[key]["registry_error"]
    assert any(step == f"Claude Code: {status['claude']['registry_error']}" for step in status["next_steps"])
    assert checked["next_steps"] == [f"Claude Code: {checked['claude_plugin']['registry_error']}"]
    pretty = run_raw("doctor", "--plugin-root", str(ROOT), "--pretty", env=host_env(config))
    assert pretty.returncode == 0 and "Findings: none" in pretty.stdout and "gate-error" not in pretty.stdout
    assert "Claude Code plugin: unreadable registry" in pretty.stdout


def test_install_codex_under_claude_points_to_claude_install(tmp_path):
    def install(host):
        return run_cmd("install", "--plugin-root", str(ROOT), "--config", str(tmp_path / "config.toml"),
                       "--dry-run", "--no-verify-codex", env=host_env(tmp_path / "no-claude", host))

    pointer = "claude plugin install zagrosi-forge@zagrosi"
    assert any(pointer in step and "Codex only" in step for step in install("claude")["next_steps"])
    assert not any(pointer in step for step in install(None)["next_steps"])


def test_pretty_update_check_and_doctor_show_claude_steps(tmp_path):
    config = claude_config(tmp_path, STALE)
    env = host_env(config, "claude")
    update = run_raw("update-check", "--plugin-root", str(ROOT), "--config", str(tmp_path / "codex/config.toml"),
                     "--pretty", env=env)
    checked = run_raw("doctor", "--plugin-root", str(ROOT), "--pretty", env=env)

    for result in (update, checked):
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"Claude Code plugin: {STALE} (user)   Current: False" in result.stdout
        assert "Host: claude" in result.stdout
        assert UPDATE in result.stdout and PLUGIN_UPDATE in result.stdout


@pytest.mark.parametrize(("marker", "host"), [("CLAUDECODE", "claude"), ("CODEX_THREAD_ID", "codex"), (None, "other")])
def test_team_start_command_uses_detected_host(team, repositories, tmp_path, monkeypatch, capsys, marker, host):
    first, _, _ = repositories
    team(first, "init", "--remote", "origin", "--name", "Alex")
    planning = make_plan(tmp_path / "plan")
    if marker:
        monkeypatch.setenv(marker, "1")

    code = team.forge.entrypoint.main(["next-section", "--planning-dir", str(planning), "--target-dir", str(first)])

    result = json.loads(capsys.readouterr().out)
    assert code == 1 and not result["success"]
    command = result["commands"]["team_start"]
    assert command[command.index("--host") + 1] == host


def test_team_start_without_host_records_detected_host(team, repositories, monkeypatch):
    first, second = join_pair(team, repositories)
    monkeypatch.setenv("CLAUDECODE", "1")
    detected = team(first, "start", "--task", "Detected host", "--path", "src/auth")["session"]
    explicit = team(first, "start", "--task", "Explicit host", "--host", "other")["session"]
    assert (detected["host"], explicit["host"]) == ("claude", "other")

    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setenv("CODEX_THREAD_ID", "019a-thread")
    updated = team(first, "update", "--session", detected["id"], "--state", "handoff", "--note", "Ready for review.")
    assert updated["session"]["host"] == "claude"  # An update without --host keeps the recorded host.
    revision = team(second, "status")["revision"]
    taken = team(second, "recover", "--session", detected["id"], "--expect", revision, "--reason", "Agreed handoff")
    assert taken["session"]["host"] == "codex"


def claude_settings(tmp_path: Path) -> Path:
    config = tmp_path / "claude-settings"
    config.mkdir()
    (config / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"zagrosi-forge@zagrosi": True, "other@market": False},
        "env": {"API_TOKEN": "SECRET-SETTINGS"}}))
    (config / ".claude.json").write_text(json.dumps({
        "oauthAccount": {"emailAddress": "SECRET-ACCOUNT"},
        "mcpServers": {
            "context7": {"type": "http", "url": "https://SECRET-URL.example/mcp",
                         "headers": {"CONTEXT7_API_KEY": "SECRET-HEADER"}},
            "local": {"command": "SECRET-COMMAND", "args": ["SECRET-ARG"], "env": {"DB_PASSWORD": "SECRET-ENV"}},
            "odd": {"type": "SECRET-TYPE", "command": "SECRET-ODD"},
            "listed": {"type": ["http"], "url": "https://SECRET-LISTED.example/mcp"},
        }}))
    return config


def inventory(tmp_path: Path, config: Path) -> dict:
    return run_cmd("capability-inventory", "--plugin-root", str(ROOT), "--config", str(tmp_path / "missing.toml"),
                   env={**os.environ, "CLAUDE_CONFIG_DIR": str(config)})


def test_capability_inventory_summarizes_claude_settings(tmp_path):
    payload = inventory(tmp_path, claude_settings(tmp_path))

    assert "SECRET-" not in json.dumps(payload)
    claude = payload["claude"]
    assert claude["warnings"] == []
    assert claude["plugins"]["configured"] == [{"id": "other@market", "enabled": False},
                                               {"id": "zagrosi-forge@zagrosi", "enabled": True}]
    servers = {row["name"]: row for row in claude["mcp_servers"]["configured"]}
    assert servers["context7"]["transport"] == "http"
    assert servers["context7"]["has_http_headers"] and servers["context7"]["has_auth"]
    assert servers["local"]["transport"] == "stdio" and servers["local"]["has_env"] and servers["local"]["has_auth"]
    # Unknown or malformed transport names are inferred, never echoed.
    assert (servers["odd"]["transport"], servers["listed"]["transport"]) == ("stdio", "http")
    assert payload["config_scope"] == "codex" and payload["warnings"]  # Codex fields are unchanged.
    assert "Codex and Claude Code" in payload["recommendations"][0]
    assert any("Context7" in item for item in payload["recommendations"])


@pytest.mark.parametrize("broken", [None, "settings.json", ".claude.json"])
def test_capability_inventory_warns_without_claude_settings(tmp_path, broken):
    config = claude_settings(tmp_path) if broken else tmp_path / "empty"
    if broken:
        (config / broken).write_text("{")

    claude = inventory(tmp_path, config)["claude"]

    assert claude["warnings"]
    if broken == "settings.json":
        assert claude["plugins"]["configured"] == [] and claude["mcp_servers"]["configured"]
    elif broken == ".claude.json":
        assert claude["plugins"]["configured"] and claude["mcp_servers"]["configured"] == []
    else:
        assert claude["plugins"]["configured"] == [] and claude["mcp_servers"]["configured"] == []
