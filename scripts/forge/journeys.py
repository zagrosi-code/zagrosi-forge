"""Declared acceptance journeys and their independently captured checks."""
from __future__ import annotations

import json
from pathlib import Path
import re

from . import markdown, mutable_inputs, output, sections, storage


def declarations(text: str) -> dict[str, dict]:
    if "acceptance journeys" not in text.lower():
        return {}
    headings = markdown.markdown_h2_sections(markdown.visible_markdown(text))
    bodies = [body for title, body in headings if title.casefold() == "acceptance journeys"]
    if not bodies:
        return {}
    blocks, _ = markdown.split_markdown_fences_with_closure(bodies[0])
    if len(bodies) != 1 or len(blocks) != 1 or blocks[0][0] != "json" or not blocks[0][2]:
        raise ValueError("Acceptance journeys need one heading with one closed JSON array.")
    rows = json.loads("\n".join(blocks[0][1]))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Acceptance journeys must list at least one case.")
    _, prose = markdown.split_markdown_fences_with_closure(markdown.visible_markdown(text))
    requirements = set(markdown.requirement_ids("\n".join(prose)))
    cases = {}
    commands = set()
    fields = {"id", "requirement", "actor", "starting_state", "action", "expected", "check", "argv"}
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError(f"Each acceptance journey needs {', '.join(sorted(fields))}.")
        for field in fields - {"argv"}:
            value = row[field]
            if not isinstance(value, str) or value.strip().lower() in {"", "todo", "tbd", "pending"}:
                raise ValueError(f"Acceptance journey needs a concrete {field}.")
        case_id, argv = row["id"], row["argv"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", case_id) or case_id in cases:
            raise ValueError(f"Invalid or duplicate acceptance journey ID: {case_id}")
        if row["requirement"] not in requirements:
            raise ValueError(f"{case_id}: name a requirement owned by this section.")
        if (not isinstance(argv, list) or len(argv) < 2
                or not all(isinstance(arg, str) and arg and "\0" not in arg for arg in argv)
                or row["check"] not in argv[1:] or tuple(argv) in commands):
            raise ValueError(f"{case_id}: name a distinct check selector and include it as an argument in argv.")
        cases[case_id] = row
        commands.add(tuple(argv))
    return cases


def for_section(planning: Path, section: str) -> dict[str, dict]:
    if section not in sections.check_section_progress(planning).get("sections", []):
        raise ValueError("Acceptance journeys require a section in the plan manifest.")
    return declarations(storage.read_text(planning / "sections" / f"{section}.md"))


def receipt_path(planning: Path, section: str, case_id: str) -> Path:
    # All three public callers derive both names from validated plan declarations.
    return planning / "implementation" / "verification" / "journeys" / section / f"case-{case_id}.json"


def observed(planning: Path, target: Path, section: str, cases: dict) -> list[dict]:
    from .verification import _check_managed_path, result_error

    rows = []
    current = None
    for name, case in cases.items():
        path = receipt_path(planning, section, name)
        receipt = storage.load_json(path) if _check_managed_path(planning, path, single_link=True) else None
        state = "missing"
        if receipt is not None:
            if (not isinstance(receipt, dict) or receipt.get("source") != "captured"
                    or receipt.get("journey") != name or receipt.get("command") != case["argv"]):
                state = "invalid"
            else:
                if current is None:
                    current = mutable_inputs.verification_snapshot(planning, target, section)
                if receipt.get("snapshot") != current:
                    state = "stale"
                elif receipt.get("outcome") in {"pending", "timed_out", "failed"}:
                    state = receipt["outcome"]
                else:
                    state = "invalid" if result_error(receipt) else "passed"
        rows.append({**case, "status": state, "receipt_path": str(path),
                     "receipt_digest": mutable_inputs.digest(receipt) if isinstance(receipt, dict) else None})
    return rows


def completion(planning: Path, target: Path, section: str) -> dict:
    rows = observed(planning, target, section, for_section(planning, section))
    gaps = [f"{row['id']} ({row['status']})" for row in rows if row["status"] != "passed"]
    if gaps:
        raise ValueError("Acceptance journeys still need current captured checks: " + ", ".join(gaps))
    return {row["id"]: {key: row[key] for key in ("requirement", "check", "status", "receipt_digest")}
            for row in rows}


def record_error(text: str, evidence: dict | None) -> str | None:
    """Historical completion uses its bound contract, so later unrelated source edits stay independent."""
    cases = declarations(text)
    if not cases and evidence is None:
        return None
    if not isinstance(evidence, dict) or evidence.keys() != cases.keys():
        return "Recorded evidence does not cover each declared acceptance journey."
    for name, case in cases.items():
        row = evidence[name]
        if (not isinstance(row, dict) or row.get("status") != "passed"
                or row.get("check") != case["check"] or row.get("requirement") != case["requirement"]
                or not isinstance(row.get("receipt_digest"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", row["receipt_digest"])):
            return f"Missing or mismatched captured evidence for acceptance journey {name}."
    return None


def status(args):
    planning = storage.resolve_path(args.planning_dir)
    try:
        target = mutable_inputs.target_directory(planning, args.target_dir)
        rows = observed(planning, target, args.section, for_section(planning, args.section))
        return output.print_json({"success": True, "section": args.section, "journeys": rows})
    except (ValueError, OSError) as exc:
        return output.print_json({"success": False, "error": str(exc)}, 1)
