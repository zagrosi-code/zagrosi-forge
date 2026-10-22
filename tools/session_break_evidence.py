"""What two session-break sessions did: their logged actions, re-orientation and regressions after the break."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re

from coding_trial_outcomes import known_total

EDIT_TOOLS = {"Edit", "Write"}
PARENT = re.compile(r"(?:^|[\s'\"=:;&|(/\\])\.\.(?:$|[\s'\";&|)/\\])")


def assertion_counts(tests: Path) -> dict:
    """Assert statements and assert*/fail* calls per unittest method ID, for methods defined in top-level classes."""
    counts = {}
    for path in sorted(tests.rglob("*.py")) if tests.is_dir() else []:
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError, UnicodeError, ValueError):
            continue
        module = ".".join(path.relative_to(tests).with_suffix("").parts)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test"):
                    counts[f"{module}.{node.name}.{method.name}"] = sum(
                        isinstance(item, ast.Assert) or isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
                        and item.func.attr.startswith(("assert", "fail")) for item in ast.walk(method))
    return counts


def event_rows(path: Path) -> list[tuple[int, dict]]:
    """Parsed events with the byte offset at which each line ends."""
    rows, offset = [], 0
    if not path.is_file():
        return rows
    with path.open("rb") as handle:
        for line in handle:
            offset += len(line)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                rows.append((offset, event))
    return rows


def actions(event: dict) -> list[tuple[str, str]]:
    """Commands, other tool inputs and edited paths an event starts, for Codex and Claude logs."""
    item = event.get("item")
    if isinstance(item, dict):
        if event.get("type") != "item.started":
            return []
        if item.get("type") == "command_execution" and isinstance(item.get("command"), str):
            return [("command", item["command"])]
        if item.get("type") == "file_change" and isinstance(item.get("changes"), list):
            return [("edit", change["path"]) for change in item["changes"]
                    if isinstance(change, dict) and isinstance(change.get("path"), str)]
        return []
    message = event.get("message")
    content = message.get("content") if event.get("type") == "assistant" and isinstance(message, dict) else None
    rows = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "tool_use" or not isinstance(block.get("input"), dict):
            continue
        name, data = block.get("name"), block["input"]
        if name == "Bash" and isinstance(data.get("command"), str):
            rows.append(("command", data["command"]))
        elif name in EDIT_TOOLS and isinstance(data.get("file_path"), str):
            rows.append(("edit", data["file_path"]))
        else:
            rows.append(("tool", json.dumps(data, sort_keys=True)))
    return rows


def spellings(path: Path) -> list[str]:
    """A path as given and resolved; hosts may log either, for example /tmp or /private/tmp on macOS."""
    return list(dict.fromkeys((os.path.normpath(path), os.path.normpath(path.resolve()))))


def workspace_path(path: str, workspace: Path) -> str | None:
    """The workspace-relative form of a logged path, or None outside the workspace."""
    candidate = os.path.normpath(path if os.path.isabs(path) else workspace / path)
    for root in spellings(workspace):
        if os.path.commonpath([candidate, root]) == root:
            return Path(os.path.relpath(candidate, root)).as_posix()
    return None


def code_edit(path: str, workspace: Path) -> bool:
    relative = workspace_path(path, workspace)
    return relative is not None and relative.split("/", 1)[0] in ("src", "tests")


def outside_reference(kind: str, text: str, workspace: Path) -> bool:
    """An edit outside the workspace, or a command or tool input naming `..` or the matrix beyond it."""
    if kind == "edit":
        return workspace_path(text, workspace) is None
    for root in spellings(workspace):
        text = text.replace(root, "")
    return bool(PARENT.search(text)) or any(root in text for root in spellings(workspace.parent.parent))


def main_loop_usage(rows, boundary: int) -> dict | None:
    """Claude top-level assistant usage through the boundary, counted once per message ID."""
    messages = {}
    for offset, event in rows:
        message = event.get("message")
        if offset > boundary:
            break
        if (event.get("type") == "assistant" and not event.get("parent_tool_use_id") and isinstance(message, dict)
                and isinstance(message.get("usage"), dict)):
            messages[message.get("id") or offset] = message["usage"]
    if not messages:
        return None
    return {key: known_total([usage.get(key) for usage in messages.values()]) for key in
            ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")}


def session_evidence(path: Path, workspace: Path, first_edit: dict | None = None) -> dict:
    rows = event_rows(path)
    started = [(offset, kind, text) for offset, event in rows for kind, text in actions(event)]
    commands = [text for _, kind, text in started if kind == "command"]
    evidence = {"events": len(rows), "commands": len(commands),
                "helper_calls": sum("zagrosi_skills.py" in text for text in commands),
                "code_edits": sum(kind == "edit" and code_edit(text, workspace) for _, kind, text in started),
                "outside_references": [text[:240] for _, kind, text in started
                                       if outside_reference(kind, text, workspace)][:20]}
    if first_edit is None:
        return evidence
    boundary, source = first_edit.get("event_bytes"), "poll"
    if boundary is None:  # Unpolled: use the first logged code edit instead.
        boundary = next((offset for offset, kind, text in started if kind == "edit" and code_edit(text, workspace)), None)
        source = None if boundary is None else "event"
    before = [(kind, text) for offset, kind, text in started if boundary is not None and offset <= boundary]
    evidence["reorientation"] = {
        "seconds": first_edit.get("seconds"), "boundary": source,
        "commands": sum(kind == "command" for kind, _ in before) if boundary is not None else None,
        "planning_read": any(kind != "edit" and ".planning" in text for kind, text in before) if boundary is not None else None,
        "tokens": main_loop_usage(rows, boundary) if boundary is not None else None}
    return evidence


def regressions(checkpoint: dict, final: dict, on_final: dict | None, before: dict, after: dict, probe: bool) -> dict:
    """(a) compatibility lost after the checkpoint; (b) checkpoint tests failing, deleted or weakened; (c) clauses.

    A part whose observation is missing stays None instead of reading as a regression or as none.
    """
    outcomes, final_tests = checkpoint.get("tests"), final.get("tests") or {}
    tests = None
    if outcomes is not None and on_final is not None:
        passed = sorted(name for name, outcome in outcomes.items() if outcome == "passed")
        tests = {"failing": [name for name in passed if on_final.get(name) != "passed"],
                 # Absent both from the final run and from the final source; an import failure is not a deletion.
                 "deleted": [name for name in passed if name not in final_tests and name not in after],
                 "weakened": [name for name in passed if final_tests.get(name) == "skipped"
                              or name in after and after[name] < before.get(name, 0)]}
    probes = final.get("probes")
    result = {"compatibility": checkpoint["compatibility"]["passed"] and not final["compatibility"]["passed"],
              "tests": tests,
              "withheld_clauses": None if probe and probes is None else
              [name for name, status in (probes or {}).items() if status == "fail"] if probe else []}
    parts = [result["compatibility"], None if tests is None else any(tests.values()),
             None if result["withheld_clauses"] is None else bool(result["withheld_clauses"])]
    result["any"] = True if True in parts else None if None in parts else False
    return result
