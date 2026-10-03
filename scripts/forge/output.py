"""Forge output."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import shlex

from . import session as _session

def plain_status(success: Any) -> str:
    return "PASS" if bool(success) else "FAIL"


def pretty_command(command: str | list[str]) -> str:
    if isinstance(command, str):
        return command
    if os.name == "nt":
        from subprocess import list2cmdline

        return list2cmdline(command)
    return shlex.join(command)


def format_actions(payload: dict[str, Any]) -> list[str]:
    lines = []
    for label, key in (("Error", "error"), ("Next action", "next_action"), ("Record inputs", "record_inputs")):
        if payload.get(key):
            lines.append(f"{label}: {payload[key]}")
    packet = payload.get("packet") or {}
    if packet.get("error"):
        lines.append(f"Context: {packet['error']}")
    if payload.get("next_command"):
        lines.append(f"Next command: {pretty_command(payload['next_command'])}")
    commands = payload.get("commands")
    if isinstance(commands, dict):
        lines.extend(f"  {name}: {pretty_command(argv)}" for name, argv in commands.items())
    return lines


def format_workflow_details(payload: dict[str, Any]) -> list[str]:
    lines = []
    if "recorded" in payload:
        lines.append(f"Recorded: {'yes' if payload['recorded'] else 'no'}")
    admission = payload.get("admission")
    if isinstance(admission, dict):
        lines.append(f"Admission: {'READY' if admission.get('success') else 'BLOCKED'}")
        if admission.get("blocking_gates"):
            lines.append(f"Blocking: {', '.join(admission['blocking_gates'])}")
    resume = payload.get("resume") or {}
    if resume.get("stage"):
        lines.append(f"Checkpoint: {resume['stage']}")
    for label, key in (("Evidence current", "evidence_current"), ("Verification pending", "verification_pending")):
        if key in resume:
            lines.append(f"{label}: {'yes' if resume[key] else 'no'}")
    for label, key in (("Changed inputs", "changed_inputs"), ("Blocking", "blocking_gates")):
        if resume.get(key):
            lines.append(f"{label}: {', '.join(resume[key])}")
    if resume.get("notes"):
        lines.append(f"Notes: {resume['notes']}")
    lines.extend(format_actions(payload))
    entry = payload.get("entry")
    if isinstance(entry, dict):
        lines.append(f"Next entry: {'READY' if entry.get('success') else 'BLOCKED'}")
        lines.extend(format_actions(entry))
    return lines


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


def pretty_findings(findings: list[dict[str, Any]], limit: int | None = 8) -> list[str]:
    lines: list[str] = []
    for item in findings[:limit]:
        location = f" - {item['path']}" if item.get("path") else ""
        lines.append(f"  - {item.get('severity', 'unknown')}: {item.get('code', 'finding')}: {item.get('message', '')}{location}")
        for key in ("recommendation", "next_action", "next_command", "commands"):
            if item.get(key):
                lines.append(f"    {key}: {item[key]}")
    if limit is not None and len(findings) > limit:
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
    status = "BLOCKED" if (payload.get("admission") or {}).get("success") is False else plain_status(payload.get("success"))
    lines = [
        f"ZAGROSI FORGE: {phase.upper()}",
        f"Status: {status}   Mode: {payload.get('mode', 'n/a')}",
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
    elif isinstance(payload.get("providers"), list):
        lines = ["ZAGROSI FORGE REVIEWERS"]
        for provider in payload["providers"]:
            available = "available" if provider.get("available") else "unavailable"
            lines.append(f"  {provider['provider']}: {available}; authentication: {provider.get('authentication', 'unknown')}")
            cli = provider.get("cli") or {}
            if cli:
                version = f" ({cli['version']})" if cli.get("version") else ""
                lines.append(f"    CLI: {cli.get('status', 'unchecked')}{version}")
                if cli.get("reason"):
                    lines.append(f"    {cli['reason']}")
            if provider.get("login_argv"):
                lines.append(f"    Login: {pretty_command(provider['login_argv'])}")
        if payload.get("note"):
            lines.append(payload["note"])
    elif payload.get("schema") == "forge-provider-review-v1":
        lines = ["ZAGROSI FORGE PROVIDER REVIEW", f"Request: {plain_status(payload.get('success'))}",
                 f"Provider: {payload.get('provider')}",
                 f"Requested model: {payload.get('requested_model') or 'native default'}",
                 f"Observed models: {', '.join(payload.get('observed_models') or []) or 'unreported'}",
                 f"Model identity: {payload.get('model_identity', 'unreported')}"]
        for key, label in (("failure_kind", "Failure"), ("recovery", "Recovery"), ("termination_error", "Process cleanup")):
            if payload.get(key):
                lines.append(f"{label}: {payload[key]}")
        if payload.get("output"):
            lines.append(f"Review: {payload['output']}")
        if payload.get("login_argv"):
            lines.append(f"Login: {pretty_command(payload['login_argv'])}")
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
                lines.extend(f"      {example}" for example in item.get("examples", []))
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
        ]
        progress = payload.get("section_progress", {})
        if progress:
            lines.append(f"Sections: {progress.get('progress', 'n/a')} ({progress.get('state', 'unknown')})")
    else:
        lines = ["ZAGROSI FORGE", f"Status: {plain_status(payload.get('success', True))}"]
        for key in ("planning_dir", "output", "state_path", "path"):
            if payload.get(key):
                lines.append(f"{key.replace('_', ' ').title()}: {payload[key]}")
    lines.extend(format_workflow_details(payload))
    findings = list(payload.get("diagnostics") or [])
    admission = payload.get("admission") or {}
    findings.extend(admission.get("diagnostics") or [])
    for gate in admission.get("gates", []):
        findings.extend((gate.get("payload") or {}).get("findings") or [])
    if findings:
        findings = list({json.dumps(item, sort_keys=True): item for item in findings}.values())
        lines.append("Diagnostics:")
        standalone_flight = {"phase", "stage", "gates"}.issubset(payload)
        limit = 8 if payload.get("full_report") and not standalone_flight else None
        lines.extend(pretty_findings(findings, limit=limit))
    if payload.get("full_report"):
        lines.append(f"Full report: {payload['full_report']}")
    if payload.get("full_report_error"):
        lines.append(payload["full_report_error"])
    return "\n".join(lines)


def failure_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Deduplicate findings only after saving the complete machine report."""
    import tempfile

    report = None
    try:
        descriptor, name = tempfile.mkstemp(prefix="forge-report-", suffix=".json")
        report = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
    except OSError:
        if report is not None:
            try:
                report.unlink(missing_ok=True)
            except OSError:
                pass
        return {**payload, "full_report_error": "Could not save the full report; all evidence is included inline."}
    diagnostics, indexes = [], {}

    def remember(finding):
        key = json.dumps(finding, sort_keys=True)
        if key not in indexes:
            indexes[key] = len(diagnostics)
            diagnostics.append(finding)
        return indexes[key]

    def gate_summary(gate):
        if gate.get("success") is not False or not isinstance(gate.get("payload"), dict):
            return gate
        body = gate["payload"]
        # Keep actions and operative scope inline; derived metrics remain in the report.
        keep = {"error", "error_code", "errors", "message", "recommendation", "path", "files", "owned_paths",
                "content", "next_action", "next_command", "commands", "pending_sections", "unknown_predecessors",
                "incomplete_predecessors", "score", "forge_score", "finding_count", "finding_refs", "output"}
        summary = {key: value for key, value in body.items() if key in keep}
        pending, refs = [body], []
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                refs.extend(item.get("finding_refs", []))
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        if refs:
            summary["finding_refs"] = sorted(set(refs))
        # Unknown non-quality failures retain their payload instead of guessing its schema.
        return {**gate, "payload": summary if refs else body}

    def compact(value):
        if isinstance(value, list):
            return [compact(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: compact(item) for key, item in value.items() if key != "findings"}
        if isinstance(value.get("findings"), list):
            result["finding_refs"] = list(dict.fromkeys(remember(finding) for finding in value["findings"]))
        elif "findings" in value:
            result["findings"] = value["findings"]
        errors = value.get("errors", [])
        errors = errors if isinstance(errors, list) else [errors]
        errors = [*errors, value.get("error")]
        if value.get("success") is False:
            errors.append(value.get("message"))
        for error in errors:
            if not isinstance(error, str) or not error.strip():
                continue
            context = {key: value[key] for key in ("path", "recommendation", "next_action", "next_command", "commands") if key in value}
            reference = remember({"severity": "high", "code": value.get("error_code", "gate-error"), "message": error, **context})
            result.setdefault("finding_refs", []).append(reference)
        if isinstance(result.get("gates"), list):
            result["gates"] = [gate_summary(gate) for gate in result["gates"]]
        return result

    return {**compact(payload), "diagnostics": diagnostics, "full_report": str(report),
            "output_schema": "forge-flight-summary-v1"}


def print_json(payload: dict[str, Any], exit_code: int = 0) -> int:
    streams = _session._GATE_STREAMS.get()
    context = _session._CLI_CONTEXT.get()
    pretty = context["pretty"] if context is not None else _session.PRETTY_OUTPUT
    flights = [payload, payload.get("preflight"), payload.get("postflight")]
    if pretty:
        flights.append(payload.get("admission"))
    if (streams is None and not (context or {}).get("full_output")
            and any(isinstance(item, dict) and item.get("success") is False and "gates" in item for item in flights)):
        payload = failure_summary(payload)
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
