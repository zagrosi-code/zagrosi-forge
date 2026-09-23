"""Small, change-aware handoff from saved progress to the next useful action."""

from __future__ import annotations

from pathlib import Path

from . import context as _context
from . import state as _state
from . import storage as _storage


NEXT_ACTION = {
    "started": "implement {section}",
    "red": "implement {section} until the recorded regression passes",
    "green": "review {section}",
    "refactor": "rerun targeted regressions for {section}",
    "review": "resolve review findings and verify {section}",
    "verified": "record {section}",
    "recorded": "revalidate {section} before recording completion",
}


def resume_brief(planning_dir: Path, section: str, *, target_dir: Path | None = None) -> dict | None:
    path = planning_dir / "implementation" / "forge-progress.json"
    try:
        pending = _state.load_implementation_state(planning_dir).get("pending_sections", {})
        if section in pending:
            record = pending[section]
            report = record.get("failed_postflight", {}) if isinstance(record, dict) else {}
            return {"section": section, "evidence_current": False, "verification_pending": True,
                    "blocking_gates": report.get("blocking_gates", []) if isinstance(report, dict) else [],
                    "next_action": f"resolve pending verification and retry recording {section} with --flight strict",
                    "path": str(_state.implementation_state_path(planning_dir))}
        if not path.exists():
            return None
        events = _storage.load_json(path).get("events", [])
        if not isinstance(events, list):
            raise ValueError("Progress events must be a list.")
        event = next((item for item in reversed(events) if isinstance(item, dict) and item.get("section") == section), None)
        if event is None:
            return None
        previous = event.get("snapshot")
        current = _state.contract_snapshot(planning_dir, section, target_dir=target_dir)
        fresh = isinstance(previous, dict) and previous == current
        changed = sorted(
            f"{group}:{name}" for group in ("contract", "code")
            for name in set((previous or {}).get(group, {})) | set(current.get(group, {}))
            if (previous or {}).get(group, {}).get(name) != current.get(group, {}).get(name)
        ) if isinstance(previous, dict) else []
        action = NEXT_ACTION.get(event.get("stage"), "revalidate {section} before reusing saved verification")
        if not fresh:
            action = "revalidate changed inputs for {section}; rerun targeted checks and review" if changed else "revalidate {section} before reusing saved verification"
        return {"section": section, "stage": event.get("stage"), "evidence_current": fresh,
                "changed_inputs": changed, "command": event.get("command"), "result": event.get("result"),
                "notes": event.get("notes"), "next_action": action.format(section=section), "path": str(path)}
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        return {"section": section, "evidence_current": False, "error": str(exc),
                "next_action": f"repair saved progress and revalidate {section}", "path": str(path)}


def section_entry(planning_dir: Path, section: str, *, target_dir: Path | None = None) -> dict:
    packet = _context.build_context(planning_dir, section, 2000)
    brief = resume_brief(planning_dir, section, target_dir=target_dir)
    action = brief["next_action"] if brief else f"implement {section}"
    if not packet["success"]:
        action = f"repair context for {section}: {packet['error']}"
    return {"success": packet["success"], "packet": packet, "resume": brief, "next_action": action}
