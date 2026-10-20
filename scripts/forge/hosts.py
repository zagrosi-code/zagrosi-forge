"""Native host identity and installed Claude Code plugin state."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
import os

from . import storage as _storage

PLUGIN_ID = "zagrosi-forge@zagrosi"
CLAUDE_INSTALL_STEP = ("Claude Code: this command configures Codex only; install Forge there with "
                       f"`claude plugin marketplace add zagrosi-code/zagrosi-forge`, then `claude plugin install {PLUGIN_ID}`.")


def detect_host(environ: Mapping[str, str] | None = None) -> str:
    """Name the agent host running this helper from the markers each host sets for its shell commands."""
    environ = os.environ if environ is None else environ
    claude, codex = environ.get("CLAUDECODE") == "1", bool(environ.get("CODEX_THREAD_ID"))
    # Nested hosts inherit each other's markers, so both together identify neither.
    return "claude" if claude and not codex else "codex" if codex and not claude else "other"


def claude_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def claude_global_config() -> Path:
    """User-scope MCP servers live in .claude.json, beside the default config directory rather than in it."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() / ".claude.json" if configured else Path.home() / ".claude.json"


def claude_plugins_root() -> Path:
    """Claude Code keeps install records, marketplaces and its cache under one plugins root."""
    configured = os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR")
    return Path(configured).expanduser() if configured else claude_config_dir() / "plugins"


def _applies_here(project: Any) -> bool:
    """User and managed installs apply everywhere; project and local ones only inside their project."""
    if not project:
        return True
    try:
        here, root = Path.cwd().resolve(), Path(str(project)).resolve()
    except (OSError, RuntimeError):
        return False
    return here == root or here.is_relative_to(root)


def claude_plugin_status(source_version: str) -> dict[str, Any]:
    """Compare Claude Code installs of Forge that apply here with the source release; never guess a format."""
    registry = claude_plugins_root() / "installed_plugins.json"
    status: dict[str, Any] = {"registry_path": str(registry), "source_version": source_version}
    unknown = {**status, "installed": None, "current": None, "installs": [], "other_installs": []}
    try:
        data = _storage.load_json(registry)
    except FileNotFoundError:
        return {**status, "installed": False, "current": None, "installs": [], "other_installs": []}
    except (OSError, ValueError) as exc:
        # Not `error`: pretty output treats any nested `error` string as a failed gate.
        return {**unknown, "registry_error": f"Could not read the Claude Code plugin registry: {exc}"}
    plugins = data.get("plugins") if isinstance(data, dict) else None
    rows = plugins.get(PLUGIN_ID, []) if isinstance(plugins, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("version"), str)
                                         for row in rows):
        return {**unknown, "registry_error": "The Claude Code plugin registry has an unrecognized format."}
    installs: dict[bool, list[dict[str, Any]]] = {True: [], False: []}
    for row in rows:
        installs[_applies_here(row.get("projectPath"))].append(
            {"scope": row.get("scope"), "version": row["version"], "install_path": row.get("installPath"),
             **({"project_path": row["projectPath"]} if row.get("projectPath") else {})})
    here = installs[True]
    return {**status, "installed": bool(here), "installs": here, "other_installs": installs[False],
            "current": all(item["version"] == source_version for item in here) if here else None}


def claude_plugin_steps(status: dict[str, Any]) -> list[str]:
    """Actions for an unreadable or stale Claude Code install; none when current or absent."""
    if status.get("registry_error"):
        return [f"Claude Code: {status['registry_error']}"]
    if status["current"] is not False:
        return []
    stale = ", ".join(dict.fromkeys(f"{item['version']} ({item['scope']})" for item in status["installs"]
                                    if item["version"] != status["source_version"]))
    return [f"Claude Code: installed {stale} differs from source {status['source_version']}; run "
            f"`claude plugin marketplace update zagrosi`, then `claude plugin update {PLUGIN_ID}`, and restart Claude Code."]
