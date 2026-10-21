"""Native host markers and installed Claude Code plugin rows are read without guessing."""
import json
from pathlib import Path

import pytest

from forge_test_helpers import load_zagrosi_module


@pytest.fixture
def hosts():
    return load_zagrosi_module().hosts


@pytest.mark.parametrize(("environ", "expected"), [
    ({"CLAUDECODE": "1"}, "claude"),
    ({"CODEX_THREAD_ID": "019a-thread"}, "codex"),
    ({}, "other"),
    ({"CLAUDECODE": "1", "CODEX_THREAD_ID": "019a-thread"}, "other"),
    ({"CLAUDECODE": "0", "CODEX_THREAD_ID": ""}, "other"),
    # The Codex plugin for Claude Code exports companion variables into Claude sessions.
    ({"CLAUDECODE": "1", "CODEX_COMPANION_SESSION_ID": "abc"}, "claude"),
])
def test_detect_host_reads_native_markers(hosts, environ, expected):
    assert hosts.detect_host(environ) == expected


def test_detect_host_defaults_to_process_environment(hosts, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "019a-thread")
    assert hosts.detect_host() == "codex"


def write_registry(config: Path, value) -> Path:
    path = config / "plugins/installed_plugins.json"
    path.parent.mkdir(parents=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return path


def install(version, scope="user", **extra):
    return {"scope": scope, "version": version, "installPath": f"/cache/zagrosi/zagrosi-forge/{version}",
            "installedAt": "2026-10-20T11:22:28.554Z", **extra}


def test_claude_plugin_status_reads_registry_rows(hosts, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    project = tmp_path / "work/app"
    (project / "src").mkdir(parents=True)
    monkeypatch.chdir(project / "src")
    registry = write_registry(tmp_path, {"version": 2, "plugins": {
        "other@market": [install("9.9.9")],
        "zagrosi-forge@zagrosi": [install("0.4.0"), install("1.2.3", "project", projectPath=str(project)),
                                  install("0.3.0", "local", projectPath=str(tmp_path / "elsewhere"))],
    }})

    status = hosts.claude_plugin_status("1.2.3")

    assert status["registry_path"] == str(registry)
    assert status["installed"] is True and status["current"] is False
    assert status["installs"] == [
        {"scope": "user", "version": "0.4.0", "install_path": "/cache/zagrosi/zagrosi-forge/0.4.0"},
        {"scope": "project", "version": "1.2.3", "install_path": "/cache/zagrosi/zagrosi-forge/1.2.3",
         "project_path": str(project)},
    ]
    assert [row["version"] for row in status["other_installs"]] == ["0.3.0"]
    steps = hosts.claude_plugin_steps(status)
    assert len(steps) == 1 and steps[0].startswith("Claude Code: installed 0.4.0 (user) differs from source 1.2.3;")
    assert steps[0].index("claude plugin marketplace update zagrosi") < steps[0].index("claude plugin update zagrosi-forge@zagrosi")
    assert "restart Claude Code" in steps[0] and "0.3.0" not in steps[0] and "(project)" not in steps[0]


def test_other_projects_do_not_make_an_install_stale(hosts, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    write_registry(tmp_path, {"version": 2, "plugins": {"zagrosi-forge@zagrosi": [
        install("1.2.3"), install("0.3.0", "local", projectPath=str(tmp_path.parent / "other-project"))]}})
    status = hosts.claude_plugin_status("1.2.3")
    assert status["installed"] is True and status["current"] is True
    assert hosts.claude_plugin_steps(status) == []

    write_registry(tmp_path / "elsewhere-only", {"version": 2, "plugins": {"zagrosi-forge@zagrosi": [
        install("0.3.0", "project", projectPath=str(tmp_path.parent / "other-project"))]}})
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere-only"))
    elsewhere = hosts.claude_plugin_status("1.2.3")
    assert elsewhere["installed"] is False and elsewhere["current"] is None and len(elsewhere["other_installs"]) == 1


def test_plugins_root_override_moves_the_registry(hosts, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CLAUDE_CODE_PLUGIN_CACHE_DIR", str(tmp_path / "plugins-root"))
    root = tmp_path / "plugins-root"
    root.mkdir()
    (root / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {"zagrosi-forge@zagrosi": [install("0.4.0")]}}))
    status = hosts.claude_plugin_status("1.2.3")
    assert status["registry_path"] == str(root / "installed_plugins.json") and status["current"] is False


def test_missing_claude_registry_means_not_installed(hosts, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "absent"))
    status = hosts.claude_plugin_status("1.2.3")
    assert status["installed"] is False and status["current"] is None and status["installs"] == []
    assert "registry_error" not in status and hosts.claude_plugin_steps(status) == []


def test_claude_config_paths_default_to_home(hosts, tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert hosts.claude_config_dir() == tmp_path / ".claude"
    assert hosts.claude_plugins_root() == tmp_path / ".claude/plugins"
    # User-scope MCP servers sit beside the default directory, but inside an overridden one.
    assert hosts.claude_global_config() == tmp_path / ".claude.json"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "custom"))
    assert hosts.claude_global_config() == tmp_path / "custom/.claude.json"


@pytest.mark.parametrize("registry", [
    "{", "[]", {"version": 2, "plugins": []}, {"version": 2, "plugins": {"zagrosi-forge@zagrosi": {}}},
    {"version": 2, "plugins": {"zagrosi-forge@zagrosi": ["0.4.0"]}},
    {"version": 2, "plugins": {"zagrosi-forge@zagrosi": [{"scope": "user", "installPath": "/cache"}]}},
    {"version": 2, "plugins": {"zagrosi-forge@zagrosi": [install("0.4.0", "local", projectPath="bad\0path")]}},
    {"version": 2, "plugins": {"zagrosi-forge@zagrosi": [install("0.4.0", "local", projectPath=["/work"])]}},
], ids=["invalid-json", "list", "plugins-list", "rows-object", "row-string", "row-without-version",
        "project-path-nul", "project-path-list"])
def test_unrecognized_claude_registry_reports_error(hosts, tmp_path, monkeypatch, registry):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    write_registry(tmp_path, registry)
    status = hosts.claude_plugin_status("1.2.3")
    assert status["installed"] is None and status["current"] is None and status["registry_error"]
    assert "error" not in status  # Pretty output would render a nested `error` as a failed gate.
    assert hosts.claude_plugin_steps(status) == [f"Claude Code: {status['registry_error']}"]
