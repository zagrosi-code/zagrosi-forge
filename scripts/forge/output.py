"""Forge output."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from . import session as _session

def plain_status(success: Any) -> str:
    return "PASS" if bool(success) else "FAIL"


def pretty_path(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value)
    return None


def summarize_gate(gate: dict[str, Any]) -> str:
    payload = gate.get("payload", {}) if isinstance(gate.get("payload"), dict) else {}
    details: list[str] = []
    if "score" in payload:
        details.append(f"score {payload['score']}")
    if "forge_score" in payload:
        details.append(f"forge {payload['forge_score']}")
    if payload.get("finding_count"):
        details.append(f"{payload['finding_count']} finding(s)")
    if gate.get("required") is False:
        details.append("advisory")
    detail_text = f" ({', '.join(details)})" if details else ""
    return f"  [{plain_status(gate.get('success'))}] {gate.get('name', 'gate')}{detail_text}"


def pretty_findings(findings: list[dict[str, Any]], limit: int = 8) -> list[str]:
    lines: list[str] = []
    for item in findings[:limit]:
        location = f" - {item['path']}" if item.get("path") else ""
        lines.append(f"  - {item.get('severity', 'unknown')}: {item.get('code', 'finding')}: {item.get('message', '')}{location}")
    if len(findings) > limit:
        lines.append(f"  - ... {len(findings) - limit} more finding(s)")
    return lines


def format_flight(payload: dict[str, Any], indent: str = "") -> list[str]:
    title = f"ZAGROSI FORGE {payload.get('stage', 'flight').upper()}: {str(payload.get('phase', 'workflow')).upper()}"
    lines = [
        f"{indent}{title}",
        f"{indent}Status: {plain_status(payload.get('success'))}   Mode: {payload.get('mode', 'auto')}",
    ]
    for label, key in (
        ("Planning dir", "planning_dir"),
        ("Target dir", "target_dir"),
        ("Plugin root", "plugin_root"),
    ):
        value = payload.get(key)
        if value:
            lines.append(f"{indent}{label}: {value}")
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append(f"{indent}Warnings:")
        lines.extend(f"{indent}  - {warning}" for warning in warnings)
    gates = payload.get("gates") or []
    if gates:
        lines.append(f"{indent}Gates:")
        lines.extend(f"{indent}{summarize_gate(gate)}" for gate in gates)
    blocking = payload.get("blocking_gates") or []
    if blocking:
        lines.append(f"{indent}Blocking: {', '.join(blocking)}")
    return lines


def format_quality(payload: dict[str, Any]) -> list[str]:
    lines = [
        f"ZAGROSI FORGE GATE: {str(payload.get('gate', 'quality')).upper()}",
        f"Status: {plain_status(payload.get('success'))}   Score: {payload.get('score', 'n/a')}   Strict: {payload.get('strict', False)}",
    ]
    path = pretty_path(payload, "planning_dir", "plugin_root", "path")
    if path:
        lines.append(f"Path: {path}")
    findings = payload.get("findings") or []
    if findings:
        lines.append("Findings:")
        lines.extend(pretty_findings(findings))
    else:
        lines.append("Findings: none")
    return lines


def format_setup(payload: dict[str, Any]) -> list[str]:
    phase = "workflow"
    if "split_directories" in payload or "specs_complete" in payload:
        phase = "project"
    elif "review_mode" in payload or "section_progress" in payload and "files_found" in payload:
        phase = "plan"
    elif "sections_dir" in payload and "target_dir" in payload:
        phase = "implement"
    lines = [
        f"ZAGROSI FORGE: {phase.upper()}",
        f"Status: {plain_status(payload.get('success'))}   Mode: {payload.get('mode', 'n/a')}",
    ]
    for label, key in (
        ("Planning dir", "planning_dir"),
        ("Sections dir", "sections_dir"),
        ("Target dir", "target_dir"),
        ("State dir", "state_dir"),
        ("Config", "config_path"),
    ):
        value = payload.get(key)
        if value:
            lines.append(f"{label}: {value}")
    if "resume_label" in payload:
        resume_step = payload.get("resume_step")
        suffix = f" (step {resume_step})" if resume_step is not None else ""
        lines.append(f"Resume: {payload.get('resume_label')}{suffix}")
    if "next_section" in payload:
        lines.append(f"Next section: {payload.get('next_section') or 'none'}")
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in warnings)
    if isinstance(payload.get("preflight"), dict):
        lines.append("")
        lines.extend(format_flight(payload["preflight"]))
    if isinstance(payload.get("postflight"), dict):
        lines.append("")
        lines.extend(format_flight(payload["postflight"]))
    return lines


def format_pretty(payload: dict[str, Any]) -> str:
    if {"phase", "stage", "gates"}.issubset(payload):
        lines = format_flight(payload)
    elif payload.get("operation") == "update-check":
        lines = [
            "ZAGROSI FORGE UPDATE CHECK",
            f"Status: {plain_status(payload.get('success'))}   Restart required: {payload.get('restart_required', False)}",
            f"Config: {payload.get('config_path')}",
            f"Codex home: {payload.get('codex_home')}",
            f"Plugin root: {payload.get('plugin_root')}",
            f"Network policy: {payload.get('network_policy')}",
        ]
        cache = payload.get("cache") or {}
        if cache:
            lines.append(f"Cache: {cache.get('path')}   Current: {cache.get('current')}")
        config = payload.get("config") or {}
        if config:
            lines.append(f"Config current: {config.get('current')}")
        next_steps = payload.get("next_steps") or []
        if next_steps:
            lines.append("Next:")
            lines.extend(f"  - {step}" for step in next_steps)
    elif payload.get("operation") in {"install-codex", "self-update"}:
        lines = [
            "ZAGROSI FORGE SELF UPDATE" if payload.get("operation") == "self-update" else "ZAGROSI FORGE INSTALL",
            f"Status: {plain_status(payload.get('success'))}   Changed: {payload.get('changed', False)}",
            f"Config: {payload.get('config_path')}",
            f"Codex home: {payload.get('codex_home')}",
            f"Plugin root: {payload.get('plugin_root')}",
            f"Plugin: {payload.get('plugin')}",
        ]
        cache = payload.get("cache") or {}
        if cache:
            lines.append(f"Cache: {cache.get('path')}   Changed: {cache.get('changed')}")
        verification = payload.get("verification") or {}
        if verification:
            lines.append(f"Verification: {verification.get('status')}")
        if payload.get("backup_path"):
            lines.append(f"Backup: {payload.get('backup_path')}")
        if payload.get("dry_run"):
            lines.append("Mode: dry run")
        next_steps = payload.get("next_steps") or []
        if next_steps:
            lines.append("Next:")
            lines.extend(f"  - {step}" for step in next_steps)
    elif "forge_score" in payload:
        lines = [
            "ZAGROSI FORGE SCORE",
            f"Status: {plain_status(payload.get('success'))}   Score: {payload.get('forge_score')}   Grade: {payload.get('grade', 'n/a')}",
            f"Planning dir: {payload.get('planning_dir')}",
        ]
        components = payload.get("components") or {}
        if components:
            lines.append("Components:")
            lines.extend(f"  - {key}: {value}" for key, value in components.items())
    elif "commands" in payload and isinstance(payload.get("commands"), list):
        phase_filter = payload.get("phase_filter") or "all"
        lines = [
            "ZAGROSI FORGE COMMANDS",
            f"Phase: {phase_filter}",
        ]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in payload.get("commands", []):
            grouped.setdefault(str(item.get("phase", "utility")), []).append(item)
        for phase in sorted(grouped):
            lines.append("")
            lines.append(phase.upper())
            for item in grouped[phase]:
                aliases = item.get("aliases") or []
                alias_text = f" (aliases: {', '.join(aliases)})" if aliases else ""
                lines.append(f"  - {item.get('name')}{alias_text}: {item.get('summary', '')}")
    elif "gate" in payload:
        lines = format_quality(payload)
    elif "results" in payload and "plugin_root" in payload:
        lines = [
            "ZAGROSI FORGE RELEASE CHECK",
            f"Status: {plain_status(payload.get('success'))}",
            f"Plugin root: {payload.get('plugin_root')}",
            "Commands:",
        ]
        for result in payload.get("results", []):
            lines.append(f"  [{plain_status(result.get('returncode') == 0)}] {result.get('command')}")
    elif any(key in payload for key in ("preflight", "postflight", "resume_label", "next_section")):
        lines = format_setup(payload)
    elif "next_action" in payload:
        lines = [
            "ZAGROSI FORGE STATUS",
            f"Status: {plain_status(payload.get('success'))}",
            f"Planning dir: {payload.get('planning_dir')}",
            f"Next action: {payload.get('next_action')}",
        ]
        progress = payload.get("section_progress", {})
        if progress:
            lines.append(f"Sections: {progress.get('progress', 'n/a')} ({progress.get('state', 'unknown')})")
    else:
        lines = ["ZAGROSI FORGE", f"Status: {plain_status(payload.get('success', True))}"]
        for key in ("planning_dir", "output", "state_path", "path", "error"):
            if payload.get(key):
                lines.append(f"{key.replace('_', ' ').title()}: {payload[key]}")
    return "\n".join(lines)


def print_json(payload: dict[str, Any], exit_code: int = 0) -> int:
    streams = _session._GATE_STREAMS.get()
    context = _session._CLI_CONTEXT.get()
    pretty = context["pretty"] if context is not None else _session.PRETTY_OUTPUT
    if streams is None and pretty:
        print(format_pretty(payload))
    else:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")), file=streams[0] if streams else None)
    return exit_code


def compact_values(values: list[str], label: str, limit: int = 2) -> str | None:
    if not values:
        return None
    shown = values[:limit]
    suffix = f", +{len(values) - limit} more" if len(values) > limit else ""
    rendered = ", ".join(f"`{Path(item).name if label == 'review' else item}`" for item in shown)
    return f"{label}: {rendered}{suffix}"
