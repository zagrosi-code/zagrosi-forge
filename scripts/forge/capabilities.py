"""Forge capabilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import re
import shutil
import tomllib

from . import output as _output
from . import policy as _policy
from . import storage as _storage

def local_tool_status(names: list[str] | None = None) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "available": bool(path := shutil.which(name)),
            "path": path,
        }
        for name in (names or _policy.LOCAL_TOOL_NAMES)
    }


def load_toml_config(path: Path | None) -> tuple[dict[str, Any], list[str], str | None]:
    if path is None:
        default = Path.home() / ".codex" / "config.toml"
        path = default if default.exists() else None
    if path is None:
        return {}, ["No Codex config file found."], None
    if not path.exists():
        return {}, [f"Config file not found: {path}"], str(path)
    try:
        data = tomllib.loads(_storage.read_text(path))
    except Exception as exc:  # pragma: no cover - exact parser errors vary by Python version.
        return {}, [f"Config file could not be parsed: {exc.__class__.__name__}"], str(path)
    return data if isinstance(data, dict) else {}, [], str(path)


def summarize_plugins(config: dict[str, Any]) -> list[dict[str, Any]]:
    plugins = config.get("plugins", {})
    if not isinstance(plugins, dict):
        return []
    rows: list[dict[str, Any]] = []
    for plugin_id, settings in sorted(plugins.items()):
        enabled = True
        if isinstance(settings, dict) and "enabled" in settings:
            enabled = bool(settings.get("enabled"))
        rows.append({"id": str(plugin_id), "enabled": enabled})
    return rows


def mcp_transport(settings: dict[str, Any]) -> str:
    if settings.get("url"):
        return "http"
    if settings.get("command"):
        return "stdio"
    return "unknown"


def has_sensitive_key(mapping: dict[str, Any]) -> bool:
    return any(_policy.SENSITIVE_KEY_RE.search(str(key)) for key in mapping)


def summarize_mcp_servers(config: dict[str, Any]) -> list[dict[str, Any]]:
    servers = config.get("mcp_servers", {})
    if not isinstance(servers, dict):
        return []
    rows: list[dict[str, Any]] = []
    for name, settings in sorted(servers.items()):
        server = settings if isinstance(settings, dict) else {}
        env = server.get("env", {})
        env_vars = server.get("env_vars", {})
        headers = server.get("http_headers", {})
        has_env = isinstance(env, dict) and bool(env)
        has_env_vars = isinstance(env_vars, (dict, list)) and bool(env_vars)
        has_http_headers = isinstance(headers, dict) and bool(headers)
        has_auth = (
            bool(server.get("bearer_token"))
            or bool(server.get("bearer_token_env_var"))
            or has_http_headers
            or (isinstance(env, dict) and has_sensitive_key(env))
            or (isinstance(env_vars, dict) and has_sensitive_key(env_vars))
        )
        rows.append(
            {
                "name": str(name),
                "transport": mcp_transport(server),
                "enabled": bool(server.get("enabled", True)),
                "has_env": has_env,
                "has_env_vars": has_env_vars,
                "has_http_headers": has_http_headers,
                "has_auth": has_auth,
            }
        )
    return rows


def capability_inventory(args: argparse.Namespace) -> int:
    plugin_root = _storage.resolve_path(args.plugin_root or ".")
    config_path = _storage.resolve_path(args.config) if args.config else None
    config, warnings, loaded_config = load_toml_config(config_path)
    tools = local_tool_status()
    plugins = summarize_plugins(config)
    mcp_servers = summarize_mcp_servers(config)
    recommendations: list[str] = []
    if any(server["name"] == "context7" and server["enabled"] for server in mcp_servers):
        recommendations.append("Use Context7 or configured documentation MCP for current library/API documentation when relevant.")
    if tools["gh"]["available"]:
        recommendations.append("GitHub CLI is available for opt-in PR and CI inspection workflows.")
    if tools["claude"]["available"]:
        recommendations.append("Claude CLI appears available as a possible external review candidate after explicit consent.")
    if not tools["gemini"]["available"]:
        recommendations.append("Gemini CLI was not detected; do not assume Gemini-based review is available.")
    payload = {
        "success": True,
        "gate": "capability-inventory",
        "plugin_root": str(plugin_root),
        "config_path": loaded_config,
        "warnings": warnings,
        "plugins": {"configured": plugins},
        "mcp_servers": {"configured": mcp_servers},
        "local_tools": tools,
        "recommendations": recommendations,
    }
    return _output.print_json(payload)


def matched_workflow_terms(text: str) -> list[str]:
    haystack = text.lower()
    return [term for term in _policy.WORKFLOW_AMBIGUITY_TERMS if re.search(rf"\b{re.escape(term)}\b", haystack)]


def recommended_option(label: str, recommended: bool, rationale: str) -> dict[str, Any]:
    payload = {"label": label, "recommended": recommended, "rationale": rationale}
    if recommended:
        payload["recommended_label"] = f"{label} (Recommended)"
    return payload


def workflow_options(args: argparse.Namespace) -> int:
    brief_parts = [args.brief or ""]
    if args.spec_file:
        spec_path = _storage.resolve_path(args.spec_file)
        if not spec_path.exists():
            return _output.print_json({"success": False, "gate": "workflow-options", "error": f"Spec file not found: {spec_path}"}, 1)
        brief_parts.append(_storage.read_text(spec_path))
    text = "\n".join(part for part in brief_parts if part)
    matched = matched_workflow_terms(text)
    explicit_depth = args.depth
    recommended_depth = explicit_depth or _policy.DEFAULT_DEPTH
    depth_available = [
        {"value": "lean", "description": "Default: compact artifacts, semantic gates, minimal turns."},
        {"value": "standard", "description": "More durable context for complex work."},
        {"value": "deep", "description": "Explicit audit mode with multi-perspective review."},
        {"value": "fast", "description": "Compatibility alias for lean."},
    ]
    depth_rationale = (
        "Material choices may need one concise question: " + ", ".join(matched)
        if matched
        else "Lean is the default; depth never escalates from generic prose."
    )
    depth_options = [
        recommended_option("Lean", recommended_depth in {"lean", "fast"}, depth_rationale),
        recommended_option("Standard", recommended_depth == "standard", "Use when durable extra context materially reduces implementation risk."),
        recommended_option("Deep", recommended_depth == "deep", "Use only when explicitly requested for audit-grade review."),
    ]
    privacy_options = [
        recommended_option(
            "Local ignored planning",
            True,
            "Conservative default: planning artifacts stay local/ignored unless the user opts into publishing them.",
        ),
        recommended_option("Commit planning records", False, "Use only when the team wants planning records in repository history."),
    ]
    autonomy_options = [
        recommended_option("Manual", True, "Push, PR, CI watch, and fix loops require explicit opt-in."),
        recommended_option("Auto commit", False, "Only enable after user approval for local commit automation."),
        recommended_option("Auto PR and CI watch", False, "Requires remote credentials, branch policy, and explicit user approval."),
    ]
    payload = {
        "success": True,
        "gate": "workflow-options",
        "matched_terms": matched,
        "depth": {
            "selected": explicit_depth,
            "recommended": recommended_depth,
            "requires_confirmation": False,
            "available": depth_available,
            "reason": depth_rationale,
        },
        "interview": {
            "required": bool(matched),
            "use_structured_input_when_available": True,
            "fallback": "chat",
            "option_sets": (
                [
                    {"id": "depth", "question": "What Forge depth should this run use?", "options": depth_options},
                    {"id": "planning_privacy", "question": "How should Forge planning artifacts be handled?", "options": privacy_options},
                    {"id": "autonomy", "question": "How much git/PR/CI autonomy should Forge use?", "options": autonomy_options},
                ]
                if matched
                else []
            ),
        },
        "git_privacy": {
            "planning_artifacts": "local_ignored",
            "mention_planning_docs": False,
            "offer_gitignore": True,
            "commit_style": "ask",
        },
        "autonomy": {
            "auto_commit": False,
            "auto_pr": False,
            "ci_watch": False,
            "fix_watch_loop": False,
            "requires_explicit_opt_in": True,
        },
        "recommendations": (
            ["Ask one decision-changing question; record only its answer."]
            if matched
            else []
        ),
    }
    return _output.print_json(payload)


def review_capabilities(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir) if args.planning_dir else None
    config: dict[str, Any] = {}
    warnings: list[str] = []
    config_path = _storage.resolve_path(args.config) if getattr(args, "config", None) else None
    if config_path:
        if config_path.exists():
            try:
                loaded = _storage.load_json(config_path)
                config = loaded if isinstance(loaded, dict) else {}
            except Exception as exc:  # pragma: no cover - exact JSON errors vary by Python version.
                warnings.append(f"Review config could not be parsed: {exc.__class__.__name__}")
        else:
            warnings.append(f"Review config file not found: {config_path}")
    elif planning_dir and (planning_dir / "zagrosi_plan_config.json").exists():
        config_path = planning_dir / "zagrosi_plan_config.json"
        config = _storage.load_json(config_path)
    configured_mode = config.get("review_mode", "codex_review")
    tools = local_tool_status(["claude", "gemini"])
    external = {
        name: {"available": item["available"], "path": item["path"], "execution": "opt_in" if item["available"] else "not_configured"}
        for name, item in tools.items()
    }
    recommendations = ["Run Codex review for every non-trivial plan and implementation section."]
    if configured_mode == "skip":
        recommendations.append("Review mode is skip; do not skip review for non-trivial or deep Forge work.")
    if configured_mode == "external_llm" and not any(item["available"] for item in external.values()):
        recommendations.append("External review mode is configured but no external CLI candidate was detected; use Codex review fallback.")
    elif configured_mode == "external_llm":
        recommendations.append("External review candidates are opt-in; run them only after explicit user consent.")
    payload = {
        "success": True,
        "gate": "review-capabilities",
        "planning_dir": str(planning_dir) if planning_dir else None,
        "config_path": str(config_path) if config_path else None,
        "configured_mode": configured_mode,
        "warnings": warnings,
        "baseline": {
            "codex_review": {
                "available": True,
                "mandatory": True,
                "execution": "agent_review",
            }
        },
        "external": external,
        "recommendations": recommendations,
    }
    return _output.print_json(payload)
