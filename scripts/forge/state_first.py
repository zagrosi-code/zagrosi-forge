"""State-first lean plans: plan.md, status.json and append-only notes.md.

A later session needs the goal, each section's state, the reasons behind earlier
choices and the last verification more than per-section assurance records.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any

from . import markdown, mutable_inputs, output, policy, sections, storage, verification
from .command_args import command

PLAN = "plan.md"
STATUS = "status.json"
NOTES = "notes.md"
SNAPSHOT = "implementation/state-snapshot.json"
MODE_MARKER = '"state-first"'
STATES = ("pending", "active", "done")
FILE_COLUMNS = {"files", "owns", "owned files"}
PLACEHOLDER_RE = re.compile(r"\b(?:TODO|TBD)\b")
ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(\S.*)$")
ROW_EXAMPLE = "| section-01-labels | src/labels.py, tests/test_labels.py | `pytest tests/test_labels.py` passes |"
NOTES_HEADER = "# Notes\n\nAppend-only: decisions, clarifications, verification results and the next step. Newest last.\n"
NOTE_LIMIT, NOTE_CHARACTERS = 3, 2000
# A tag's reason ends at sentence punctuation or code, so "risk: low. `pytest` passes" stays quiet.
RISK_TAG_RE = re.compile(r"\brisk:\s*([^;.,|`]+)", re.I)
NO_RISK_RE = re.compile(r"(?:none|no|n/?a|-|low|minimal|negligible)(?:\s+risk)?", re.I)
INTERFACE_RE = re.compile(
    r"\b(?:public|published|exported)\s+(?:api|interface|signature|schema|cli|command|flag|option|endpoint|format)s?\b"
    r"|\bbreaking\s+changes?\b|\bbackwards?[- ]compatib(?:le|ility)\b", re.I)
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
FINISH_ACTION = "capture the full suite with implement-verify, which also runs postflight"


def active(planning_dir: Path) -> bool:
    """A status.json naming this mode routes every later command; nothing converts in place.

    The marker survives hand edits that break the JSON, so load errors stay visible.
    """
    path = planning_dir / STATUS
    try:
        return path.is_file() and not path.is_symlink() and MODE_MARKER in storage.read_text(path)
    except (OSError, UnicodeDecodeError):
        return False


def host() -> str:
    # Codex can run inside Claude Code, so its variables are checked first.
    if os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SANDBOX"):
        return "codex"
    return "claude" if os.environ.get("CLAUDECODE") else "unknown"


def _regular_text(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{path} must be a regular file.")
    return storage.read_text(path)


def parse_plan(planning_dir: Path) -> dict[str, Any]:
    plan: dict[str, Any] = {"goal": "", "acceptance": [], "sections": [], "req_ids": [], "errors": []}
    try:
        text = markdown.visible_markdown(_regular_text(planning_dir / PLAN))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        plan["errors"].append(f"{PLAN} is missing or unreadable ({exc}); rerun plan-setup to draft it from the brief.")
        return plan
    headings: dict[str, str] = {}
    for title, body in markdown.markdown_h2_sections(text):
        headings.setdefault(title.casefold(), body)
    errors = plan["errors"]
    plan["req_ids"] = markdown.requirement_ids(text)
    plan["goal"] = headings.get("goal", "").strip()
    if not plan["goal"]:
        errors.append("Add `## Goal` with one paragraph, e.g. `Trim label edges without changing case.`")
    plan["acceptance"] = [match[1].strip() for line in headings.get("acceptance", "").splitlines()
                          if (match := ITEM_RE.match(line))]
    if not plan["acceptance"]:
        errors.append("Add `## Acceptance` with at least one bullet, e.g. `- Labels keep internal spaces.`")
    plan["sections"], section_errors = _section_rows(headings.get("sections"))
    errors.extend(section_errors)
    fields = [("Goal", plan["goal"]), ("Acceptance", "\n".join(plan["acceptance"]))]
    fields.extend((row["section"], " ".join([*row["cells"], *row["files"]])) for row in plan["sections"])
    # Code spans may name TODO markers as subject matter, as in "remove stale `TODO` comments".
    errors.extend(f"Replace TODO in {label} with the inspected fact." for label, value in fields
                  if PLACEHOLDER_RE.search(CODE_SPAN_RE.sub(" ", value)))
    return plan


def _section_rows(body: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    tables = [table for table in markdown.markdown_tables(body or "") if table]
    headers = [[cell.strip("`* ").casefold() for cell in table[0]] for table in tables]
    candidates = [(table, header) for table, header in zip(tables, headers)
                  if "section" in header and FILE_COLUMNS & set(header)]
    if len(candidates) != 1:
        return [], [f"`## Sections` needs one table whose header names `Section` and `Files`, with rows like `{ROW_EXAMPLE}`."]
    table, header = candidates[0]
    name_column = header.index("section")
    file_column = next(index for index, label in enumerate(header) if label in FILE_COLUMNS)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for cells in table[1:]:
        if len(cells) != len(header):
            errors.append(f"Row `| {' | '.join(cells)} |` has {len(cells)} cells; the header has {len(header)}.")
            continue
        name = cells[name_column].strip("`* ")
        files = [item.strip().strip("`") for item in cells[file_column].split(",") if item.strip().strip("`")]
        if not policy.SECTION_RE.fullmatch(name):
            errors.append(f"Section `{name}` must look like `section-01-labels`.")
        elif name in seen:
            errors.append(f"Section `{name}` appears twice; give each row its own name.")
        elif not files:
            errors.append(f"`{name}` lists no files; name repo-relative paths, e.g. `src/labels.py, tests/test_labels.py`.")
        elif invalid := _invalid_paths(files):
            errors.append(f"`{name}` has invalid paths {', '.join(invalid)}; use repo-relative paths without `..`.")
        else:
            rows.append({"section": name, "files": files,
                         "cells": [cell for index, cell in enumerate(cells) if index not in {name_column, file_column}]})
        seen.add(name)
    if not rows and not errors:
        errors.append(f"`## Sections` has no rows; add rows like `{ROW_EXAMPLE}`.")
    return rows, errors


def _invalid_paths(paths: list[str]) -> list[str]:
    return [path for path in paths if not policy.OWNED_PATH_RE.fullmatch(path)]


def draft(spec_file: Path) -> str:
    brief = storage.read_text(spec_file)
    heading = re.search(r"(?m)^#[ \t]+(.+?)[ \t]*$", markdown.visible_markdown(brief))
    acceptance = [f"- {req_id}: TODO observable result" for req_id in markdown.requirement_ids(brief)]
    return (f"# Plan: {heading[1] if heading else spec_file.stem}\n\n"
            f"Brief: `{spec_file.name}`. Replace every TODO, then rerun plan-setup.\n\n"
            "## Goal\nTODO: what changes and why, in one paragraph.\n\n"
            "## Acceptance\n" + "\n".join(acceptance or ["- TODO: observable result"]) + "\n\n"
            "## Sections\n| Section | Files | Done when |\n|---|---|---|\n"
            "| section-01-TODO | TODO | TODO: the targeted check that proves it |\n\n"
            "Add `risk: <reason>` to a row when that section needs standard evidence.\n")


def load_status(planning_dir: Path) -> dict[str, Any]:
    path = planning_dir / STATUS
    status = storage.load_json(path) if path.is_file() and not path.is_symlink() else None
    sections = status.get("sections") if isinstance(status, dict) else None
    if (not isinstance(status, dict) or status.get("version") != 1 or status.get("mode") != "state-first"
            or not all(isinstance(status.get(key), str) and status[key] for key in ("spec", "target_dir"))
            or not isinstance(sections, dict)
            or any(not isinstance(entry, dict) or entry.get("state") not in STATES for entry in sections.values())):
        raise ValueError(f'{path} must be a regular file with version 1, mode "state-first", spec, target_dir '
                         f"and sections whose state is {', '.join(STATES)}.")
    return status


def target_dir(planning_dir: Path, status: dict[str, Any]) -> Path:
    return (planning_dir / status["target_dir"]).resolve()


def observe_tree(planning_dir: Path, target: Path, spec: str) -> dict[str, str]:
    """Digest each source entry outside this plan's own state, which edits never stale."""
    paths = mutable_inputs.source_paths(target)
    own = [planning_dir / name for name in (PLAN, STATUS, NOTES, spec, "implementation")]
    observed = [path.relative_to(target).as_posix() for path in paths
                if not any(path == item or path.is_relative_to(item) for item in own)]
    return {name: mutable_inputs.digest(identity)
            for name, identity in mutable_inputs.source_identities(target, paths, observed).items()}


def write_status(planning_dir: Path, status: dict[str, Any], tree: dict[str, str] | None = None) -> None:
    """Save state with the tree it describes, so status can list later edits."""
    tree = tree or observe_tree(planning_dir, target_dir(planning_dir, status), status["spec"])
    status["updated_at"] = storage.now_iso()
    storage.write_json(planning_dir / STATUS, status)
    storage.write_json(planning_dir / SNAPSHOT, {"version": 1, "written_at": status["updated_at"], "files": tree})


def append_note(planning_dir: Path, label: str, event: str, text: str) -> None:
    """Create the log on first use; empty text adds no entry."""
    path = planning_dir / NOTES
    if path.is_symlink():
        raise ValueError(f"{path} must be a regular file.")
    # Headings inside a note would split it when status reads the log back.
    text = re.sub(r"(?m)^#{1,2}(?=\s)", "###", text.strip())
    with path.open("a", encoding="utf-8") as handle:
        if handle.tell() == 0:
            handle.write(NOTES_HEADER)
        if text:
            handle.write(f"\n## {storage.now_iso()} | {label} | {event} | {host()}\n\n{text}\n")


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= NOTE_CHARACTERS else "..." + text[-NOTE_CHARACTERS:]


def latest_notes(planning_dir: Path) -> list[dict[str, str | None]]:
    path = planning_dir / NOTES
    text = _regular_text(path) if path.exists() or path.is_symlink() else ""
    entries = re.split(r"(?m)^## ", text)[1:]
    if not entries:
        body = text.replace(NOTES_HEADER, "").strip()
        return [{"heading": None, "text": _clip(body)}] if body else []
    notes = []
    for entry in entries[-NOTE_LIMIT:]:
        heading, _, body = entry.partition("\n")
        notes.append({"heading": heading.strip(), "text": _clip(body)})
    return notes


def _snapshot(planning_dir: Path) -> dict[str, str] | None:
    """The tree at the last status write; it only advises, so a damaged copy reads as absent."""
    path = planning_dir / SNAPSHOT
    try:
        snapshot = storage.load_json(path) if path.is_file() and not path.is_symlink() else None
    except (OSError, ValueError):
        return None
    files = snapshot.get("files") if isinstance(snapshot, dict) else None
    return files if isinstance(files, dict) and all(isinstance(value, str) for value in files.values()) else None


def changed_files(planning_dir: Path, current: dict[str, str]) -> dict[str, list[str]] | None:
    saved = _snapshot(planning_dir)
    if saved is None:
        return None
    return {"changed": sorted(name for name in saved.keys() & current.keys() if saved[name] != current[name]),
            "added": sorted(current.keys() - saved.keys()), "removed": sorted(saved.keys() - current.keys())}


def last_verification(status: dict[str, Any], current: dict[str, str]) -> dict[str, Any] | None:
    checks = [{"section": name, **entry["check"]} for name, entry in status["sections"].items()
              if isinstance(entry.get("check"), dict)]
    if isinstance(status.get("final"), dict):
        checks.append({"section": None, **status["final"]})
    if not checks:
        return None
    latest = max(checks, key=lambda check: str(check.get("at", "")))
    # Reported checks bind no tree, so their freshness is unknown.
    return {**latest, "fresh": latest["tree"] == mutable_inputs.digest(current) if "tree" in latest else None}


def commands(planning_dir: Path, target: Path, section: str | None) -> dict[str, list[str]]:
    selected = ["--planning-dir", str(planning_dir)]
    result = {"status": command("status", "--path", str(planning_dir))}
    if section:
        result["start"] = command("implement-progress", *selected, "--section", section, "--stage", "started")
        result["done"] = command("implement-progress", *selected, "--section", section, "--stage", "done",
                                 "--notes", "<what changed, decisions, next step>", "--", "<check>", "<argument>")
    result["finish"] = command("implement-verify", *selected, "--target-dir", str(target), "--", "<full suite>", "<argument>")
    return result


def triggers(row: dict[str, Any], *, team: bool) -> list[str]:
    """Reasons a section needs standard evidence; code spans name paths and commands, not intent."""
    text = " ".join(row["cells"])
    prose = CODE_SPAN_RE.sub(" ", text)
    # Tags are read per cell, so a "risk: low" column never runs into the next one.
    reasons = [f"risk: {reason}" for reason in (match.strip(" *_!") for cell in row["cells"] for match in RISK_TAG_RE.findall(cell))
               if reason and not NO_RISK_RE.fullmatch(reason)]
    reasons.extend(f"risk term: {term}" for term in sections.risk_terms(prose))
    reasons.extend(f"public interface: {' '.join(match[0].lower().split())}" for match in INTERFACE_RE.finditer(prose))
    if ids := markdown.requirement_ids(text):
        reasons.append(f"requirement IDs: {', '.join(ids)}")
    if team:
        reasons.append("team handoff: team coordination is configured")
    return reasons


def _team(target: Path) -> bool:
    from . import team

    return team.configured(target)


def orphaned(status: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    """Started or finished sections whose rows left the plan; plan-setup refuses to drop them."""
    rows = {row["section"] for row in plan["sections"]}
    return [name for name, entry in status["sections"].items() if name not in rows and entry["state"] != "pending"]


def summary(planning_dir: Path, status: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    rows = {row["section"]: row for row in plan["sections"]}
    names = [*rows, *(name for name in status["sections"] if name not in rows)]
    target = target_dir(planning_dir, status)
    team = _team(target)
    sections = [{"section": name, "state": None, **status["sections"].get(name, {}),
                 "owns": rows[name]["files"] if name in rows else None,
                 "risk": triggers(rows[name], team=team) if name in rows else []} for name in names]
    missing = [name for name in rows if name not in status["sections"]]
    removed = orphaned(status, plan)
    next_section = next((name for name in rows if status["sections"].get(name, {}).get("state") != "done"), None)
    if plan["errors"]:
        action = f"fix {PLAN}: {plan['errors'][0]} Then rerun plan-setup."
    elif removed:
        action = f"restore the {PLAN} rows of {', '.join(removed)}; started sections cannot leave the plan"
    elif missing:
        action = f"rerun plan-setup so {', '.join(missing)} get a status"
    elif next_section:
        verb = "start" if status["sections"][next_section]["state"] == "pending" else "finish"
        action = f"{verb} {next_section}"
    else:
        action = FINISH_ACTION
    return {"mode": "state-first", "planning_dir": str(planning_dir), "target_dir": str(target),
            "goal": plan["goal"], "acceptance": plan["acceptance"], "sections": sections,
            "next_section": next_section, "plan_errors": plan["errors"],
            "unsynced_sections": missing, "orphaned_sections": removed, "next_action": action,
            "commands": commands(planning_dir, target, next_section)}


def status_payload(planning_dir: Path) -> dict[str, Any]:
    """Everything a resumed session needs, without writing."""
    status = load_status(planning_dir)
    target = target_dir(planning_dir, status)
    current = observe_tree(planning_dir, target, status["spec"])
    payload = {"success": True, **summary(planning_dir, status, parse_plan(planning_dir)),
               "notes": latest_notes(planning_dir), "last_verification": last_verification(status, current),
               "changed_since_status": changed_files(planning_dir, current)}
    if payload["next_action"] == FINISH_ACTION and final_error(status, current) is None:
        payload["next_action"] = "complete: the final suite passed on the current tree"
    return payload


def final_error(status: dict[str, Any], current: dict[str, str]) -> str | None:
    final = status.get("final")
    if not isinstance(final, dict):
        return "No final receipt; capture the full suite with implement-verify -- <full suite>."
    if final.get("source") != "captured" or final.get("outcome") != "passed":
        return f"The final suite {final.get('outcome', 'is unknown')}: {final.get('result', '')}"
    if final.get("tree") != mutable_inputs.digest(current):
        return "Files changed after the final suite; rerun it."
    return None


def capture(planning_dir: Path, target: Path, spec: str, argv: list[str], timeout: float,
            allowance_seconds: float | None, before: dict[str, str] | None = None,
            ) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Run one explicit check under the state lock; edits during the run fail it, as receipts do.

    Returns the record, output tails and the tree observed afterwards.
    """
    from . import verification_allowance

    # Observe first: a failed observation must not leave reserved, unmeasured allowance.
    before = before or observe_tree(planning_dir, target, spec)
    allowance = verification_allowance.Allowance(planning_dir, target, allowance_seconds)
    result = verification._capture(argv, target, allowance.reserve(timeout))
    try:
        allowance.settle(result)
    except (OSError, ValueError) as exc:
        result["error"] = str(exc)
    after = observe_tree(planning_dir, target, spec)
    if result["outcome"] == "passed" and (result.get("error") or after != before):
        result["outcome"] = "failed"
        result.setdefault("error", "Files changed while the check ran; rerun it.")
    lines = [line for text in (result["stderr_tail"], result["stdout_tail"]) for line in text.splitlines() if line.strip()]
    record = {"source": "captured", "command": argv, "outcome": result["outcome"], "exit_code": result["exit_code"],
              "seconds": result.get("seconds"), "result": lines[-1].strip() if lines else "",
              "at": storage.now_iso(), "tree": mutable_inputs.digest(before),
              **({"error": result["error"]} if result.get("error") else {})}
    return record, {"stdout_tail": result["stdout_tail"], "stderr_tail": result["stderr_tail"], **allowance.report()}, after


def _passed(entry: dict[str, Any], key: str) -> dict[str, Any] | None:
    record = entry.get(key)
    return record if isinstance(record, dict) and record.get("outcome") == "passed" else None


def _evidence_gaps(entry: dict[str, Any], reasons: list[str], review: str | None, argv: list[str]) -> list[str]:
    gaps = []
    if review not in {"pass", "fixed"}:
        gaps.append("--review-status pass (or fixed) after reviewing the change")
    if not argv:
        gaps.append("a targeted check after --, e.g. -- python -m pytest tests/test_labels.py")
    if any(reason.startswith("public interface") for reason in reasons) and not _passed(entry, "baseline"):
        gaps.append("a passing baseline before editing: --stage baseline -- <preservation check>")
    elif not (entry.get("red") or _passed(entry, "baseline")):
        gaps.append("a red checkpoint (--stage red --command ... --result ...) or a passing baseline")
    return gaps


def _after_separator(argv: list[str]) -> list[str]:
    return argv[1:] if argv[:1] == ["--"] else argv


def _timeout_problem(timeout: float) -> str | None:
    return None if 0 < timeout <= 86400 else "Check timeout must be positive and at most 86400 seconds."


def _progress_problem(args: argparse.Namespace, argv: list[str], example: str) -> str | None:
    done = args.stage == "done"
    if args.command_argv and args.command_argv[0] != "--":
        return "Separate the check from the options with --."
    if done and not (args.notes or "").strip():
        return f"done needs --notes with what changed, decisions and the next step, e.g. {example} --stage done --notes 'Trimmed labels; kept case.'"
    if args.stage == "red" and not (args.command and args.result):
        return f"red needs --command and --result, e.g. {example} --stage red --command 'pytest tests/test_labels.py' --result '1 failed'"
    if args.stage == "baseline" and not argv:
        return f"baseline needs the preservation check after --, e.g. {example} --stage baseline -- python -m pytest tests/test_labels.py"
    if argv and args.stage not in {"baseline", "done"}:
        return "Only baseline and done capture a check after --."
    if argv and (args.command or args.result):
        return "A captured check reports its own result; omit --command and --result."
    if not done and (args.files or args.review_status):
        return "--file and --review-status describe a finished section; pass them with --stage done."
    if invalid := _invalid_paths(args.files):
        return f"--file takes repo-relative paths without `..`: {', '.join(invalid)}"
    if done and args.review_status == "blocked":
        return "The review is blocked; fix its findings before marking the section done."
    if args.allowance_seconds is not None and not argv:
        return "--allowance-seconds applies to a captured check after --."
    return _timeout_problem(args.timeout)


def _busy(exc: TimeoutError) -> str:
    return f"{exc}. Another Forge check or status change holds the plan; retry when it ends."


def _row_tree(tree: dict[str, str], files: list[str]) -> dict[str, str]:
    """Tree entries that a row's files or directories cover."""
    return {name: digest for name, digest in tree.items()
            if any(name == path or name.startswith(path.rstrip("/") + "/") for path in files)}


def _edited_sources(row: dict[str, Any], entry: dict[str, Any], tree: dict[str, str]) -> list[str]:
    """Non-test files the row covered when the section became active that have changed since."""
    current = _row_tree(tree, row["files"])
    return sorted(name for name, digest in entry.get("origin", {}).items()
                  if not markdown.is_test_path(name) and current.get(name) != digest)


def _attribute(planning_dir: Path, entry: dict[str, Any], tree: dict[str, str]) -> None:
    """Credit files changed since the last status write to the section being updated."""
    changed = changed_files(planning_dir, tree) or {}
    entry["files"] = sorted(set(entry.get("files", [])).union(*changed.values()))


def progress(args: argparse.Namespace, planning_dir: Path) -> int:
    """One call per state change; done saves files, host, time, a note and an optional check."""
    argv = _after_separator(args.command_argv)
    example = f"implement-progress --planning-dir {planning_dir} --section {args.section}"
    if problem := _progress_problem(args, argv, example):
        return failure(planning_dir, "state-first-progress", problem)
    try:
        with verification._verification_lock(planning_dir):
            return _progress(args, planning_dir, argv)
    except TimeoutError as exc:
        return failure(planning_dir, "state-first-busy", _busy(exc))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return failure(planning_dir, "state-first-progress", str(exc))


def _progress(args: argparse.Namespace, planning_dir: Path, argv: list[str]) -> int:
    section, stage = args.section, args.stage
    status = load_status(planning_dir)
    plan = parse_plan(planning_dir)
    rows = {row["section"]: row for row in plan["sections"]}
    if plan["errors"]:
        return failure(planning_dir, "state-first-plan", f"Fix {PLAN} first: {plan['errors'][0]}")
    if section not in rows or section not in status["sections"]:
        return failure(planning_dir, "state-first-section", f"{section} is not a synced row of {PLAN}; add it and rerun plan-setup.")
    target = target_dir(planning_dir, status)
    row, entry = rows[section], status["sections"][section]
    tree = None
    if entry["state"] != "active":
        # Like standard activation: the tree at the last status write is the source a baseline must match,
        # so edits made before this first call still count.
        origin = _snapshot(planning_dir)
        if origin is None:
            origin = tree = observe_tree(planning_dir, target, status["spec"])
        entry["origin"] = _row_tree(origin, row["files"])
    if stage == "baseline":
        tree = tree or observe_tree(planning_dir, target, status["spec"])
        if edited := _edited_sources(row, entry, tree):
            return failure(planning_dir, "state-first-baseline", f"{section} source changed since it started "
                           f"({', '.join(edited)}); capture the baseline against the original source.")
    baseline = _passed(entry, "baseline") if stage == "done" else None
    if baseline and argv != baseline.get("command"):
        return failure(planning_dir, "state-first-candidate", f"{section} has a passing baseline; rerun the same check "
                       f"after -- as its candidate: -- {shlex.join(baseline.get('command') or [])}")
    reasons = triggers(row, team=_team(target)) if stage == "done" else []
    if reasons and (gaps := _evidence_gaps(entry, reasons, args.review_status, argv)):
        return failure(planning_dir, "state-first-evidence", f"{section} needs standard evidence "
                       f"({'; '.join(reasons)}). Missing: {'; '.join(gaps)}.")
    now = storage.now_iso()
    update: dict[str, Any] = {"state": "active", "stage": stage, "host": host(), "updated_at": now}
    tails: dict[str, Any] = {}
    if argv:
        record, tails, tree = capture(planning_dir, target, status["spec"], argv, args.timeout, args.allowance_seconds,
                                      before=tree)
        update["baseline" if stage == "baseline" else "check"] = record
        if baseline:
            update["candidate"] = record
        if record["outcome"] != "passed":
            _attribute(planning_dir, entry, tree)
            entry.update(update)
            write_status(planning_dir, status, tree)
            return failure(planning_dir, "state-first-check", f"{section} check {record['outcome']}; the section stays active.",
                           check=record, **tails)
    if stage == "red":
        update["red"] = {"command": args.command, "result": args.result, "at": now}
    elif args.command or args.result:
        update["check"] = {"source": "reported", "command": args.command, "result": args.result, "at": now}
    tree = tree or observe_tree(planning_dir, target, status["spec"])
    _attribute(planning_dir, entry, tree)
    if stage == "done":
        update.update(state="done", completed_at=now)
        if args.files:
            update["files"] = sorted(set(args.files))
        if args.review_status:
            update["review"] = args.review_status
    entry.update(update)
    write_status(planning_dir, status, tree)
    append_note(planning_dir, section, stage, args.notes or "")
    overview = summary(planning_dir, status, plan)
    return output.print_json({"success": True, "section": section, "stage": stage, "entry": entry, **tails,
                              **{key: overview[key] for key in ("next_section", "next_action", "commands")}})


def finish(args: argparse.Namespace, planning_dir: Path) -> int:
    """Capture the full suite, then run the structural postflight, in one call."""
    argv = _after_separator(args.command_argv)
    if args.section or args.stage or args.integration or args.source or args.outcome or args.evidence:
        return failure(planning_dir, "state-first-finish", "State-first plans capture section checks with implement-progress "
                       "--stage baseline or done; implement-verify takes only the full suite after --.")
    if args.command_argv[:1] != ["--"] or not argv:
        return failure(planning_dir, "state-first-finish", "Finish needs the full suite after --, e.g. implement-verify "
                       f"--planning-dir {planning_dir} -- python -m pytest")
    if problem := _timeout_problem(args.timeout):
        return failure(planning_dir, "state-first-finish", problem)
    try:
        with verification._verification_lock(planning_dir):
            status = load_status(planning_dir)
            target = target_dir(planning_dir, status)
            if args.target_dir and storage.resolve_path(args.target_dir) != target:
                return failure(planning_dir, "state-first-target", f"This plan targets {target}; omit --target-dir or pass it.")
            final, tails, tree = capture(planning_dir, target, status["spec"], argv, args.timeout, args.allowance_seconds)
            status["final"] = final
            write_status(planning_dir, status, tree)
            append_note(planning_dir, "plan", "finish", f"Full suite {final['outcome']}: {final['result']}")
    except TimeoutError as exc:
        return failure(planning_dir, "state-first-busy", _busy(exc))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return failure(planning_dir, "state-first-finish", str(exc))
    report = postflight_report(planning_dir, "implement")
    success = final["outcome"] == "passed" and report["success"]
    return output.print_json({"success": success, "mode": "state-first", "planning_dir": str(planning_dir), "final": final,
                              **tails, "postflight": report}, 0 if success else 1)


def postflight_report(planning_dir: Path, phase: str) -> dict[str, Any]:
    """Block only on structure and the final receipt; assurance gates belong to standard plans."""
    from . import gates

    plan = parse_plan(planning_dir)
    rows = [row["section"] for row in plan["sections"]]
    checks = [gates.direct_gate("state-plan", not plan["errors"], {"errors": plan["errors"]})]
    extras = {"planning_dir": str(planning_dir), "workflow": "state-first"}
    try:
        status = load_status(planning_dir)
    except (OSError, ValueError) as exc:
        checks.append(gates.direct_gate("state-status", False, {"error": str(exc)}))
        return gates.flight_payload(phase=phase, stage="postflight", mode="strict", gates=checks, extras=extras)
    missing = [name for name in rows if name not in status["sections"]]
    removed = orphaned(status, plan)
    checks.append(gates.direct_gate("state-status", not missing and not removed, {
        "sections_without_status": missing, "sections_without_row": removed,
        "message": "Rerun plan-setup so every plan section has a status, and restore the rows of started sections."}))
    if phase == "implement":
        unfinished = [name for name in rows if name in status["sections"] and status["sections"][name]["state"] != "done"]
        checks.append(gates.direct_gate("state-sections", not unfinished, {
            "unfinished_sections": unfinished, "message": "Mark each finished section done with implement-progress --stage done."}))
        try:
            error = final_error(status, observe_tree(planning_dir, target_dir(planning_dir, status), status["spec"]))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            error = f"Cannot observe the target to check the final receipt: {exc}"
        checks.append(gates.direct_gate("final-verification", error is None, {"error": error, "final": status.get("final")}))
    return gates.flight_payload(phase=phase, stage="postflight", mode="strict", gates=checks, extras=extras)


def options_used(args: argparse.Namespace) -> bool:
    """Programmatic callers may pass a namespace without the state-first options."""
    return bool(getattr(args, "stage", None) in {"baseline", "done"} or any(
        getattr(args, name, None) for name in ("files", "review_status", "command_argv"))
        or getattr(args, "allowance_seconds", None) is not None)


def _existing_plan(planning_dir: Path) -> str | None:
    from . import artifacts

    for name in ("sections", STATUS, *policy.LEGACY_PLAN_FILES, "zagrosi_plan_config.json", "deep_plan_config.json"):
        if (planning_dir / name).exists() or (planning_dir / name).is_symlink():
            return name
    plan = artifacts.plan_file(planning_dir)
    return plan.name if plan else None


def _sync(status: dict[str, Any], plan: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """Add new sections as pending; only pending sections may leave the plan."""
    names = [row["section"] for row in plan["sections"]]
    sections = status["sections"]
    errors = [f"{name} is {sections[name]['state']} but missing from {PLAN}; restore its row." for name in orphaned(status, plan)]
    if errors:
        return errors, [], []
    removed = [name for name in sections if name not in names]
    added = [name for name in names if name not in sections]
    for name in removed:
        del sections[name]
    sections.update((name, {"state": "pending"}) for name in added)
    return [], added, removed


def plan_setup(args: argparse.Namespace, spec_file: Path) -> int:
    planning_dir = spec_file.parent
    target = storage.resolve_path(args.target_dir or os.getcwd())
    first = not active(planning_dir)
    if not markdown.is_lean_depth(args.depth):
        return failure(planning_dir, "state-first-depth", "State-first plans use lean depth; use the standard plan for "
                       "standard or deep work.")
    if getattr(args, "for_detached", False):
        return failure(planning_dir, "state-first-detached", "Detached execution needs the physical standard plan.")
    if first and (existing := _existing_plan(planning_dir)):
        return failure(planning_dir, "state-first-existing-plan", f"{planning_dir} already holds {existing}; nothing "
                       "converts in place, so use a new planning directory.")
    # Relative when the plan lives inside the target, so a moved checkout still resolves.
    stored_target = (Path(os.path.relpath(target, planning_dir)).as_posix() if planning_dir.is_relative_to(target)
                     else str(target))
    try:
        with verification._verification_lock(planning_dir):
            created = []
            status = {"version": 1, "mode": "state-first", "spec": spec_file.name, "target_dir": stored_target,
                      "created_at": storage.now_iso(), "sections": {}} if first else load_status(planning_dir)
            if args.target_dir and target != target_dir(planning_dir, status):
                return failure(planning_dir, "state-first-target", f"This plan targets {target_dir(planning_dir, status)}; "
                               "omit --target-dir or pass it.")
            if not (planning_dir / PLAN).exists():
                with (planning_dir / PLAN).open("x", encoding="utf-8") as handle:
                    handle.write(draft(spec_file))
                created.append(PLAN)
            plan = parse_plan(planning_dir)
            previous = bool(status["sections"])
            errors, added, removed = (list(plan["errors"]), [], []) if plan["errors"] else _sync(status, plan)
            if first or added or removed:
                if first:
                    created.append(STATUS)
                write_status(planning_dir, status)
            if not (planning_dir / NOTES).exists():
                created.append(NOTES)
            changes = [f"{verb} {', '.join(names)}" for verb, names in (("added", added), ("removed", removed)) if names]
            note = ""
            if changes:
                listed = f"Sections: {', '.join(row['section'] for row in plan['sections'])}"
                note = f"{listed} ({'; '.join(changes)})." if previous else f"{listed}."
            append_note(planning_dir, "plan", "ready", note)
    except TimeoutError as exc:
        return failure(planning_dir, "state-first-busy", _busy(exc))
    except (OSError, ValueError) as exc:
        return failure(planning_dir, "state-first-setup", str(exc))
    warnings = [f"{req_id} from the brief is not in {PLAN}; add it to Acceptance or a section row."
                for req_id in markdown.requirement_ids(storage.read_text(spec_file)) if req_id not in plan["req_ids"]]
    payload = {"success": not errors, "created": created, "errors": errors, "warnings": warnings,
               **summary(planning_dir, status, plan)}
    if errors:
        payload["next_action"] = f"fix {PLAN}: {errors[0]} Then rerun plan-setup."
    return output.print_json(payload, 0 if payload["success"] else 1)


def failure(planning_dir: Path, code: str, message: str, **details: Any) -> int:
    return output.print_json({"success": False, "error_code": code, "error": message,
                              "planning_dir": str(planning_dir), **details}, 1)


def redirect(planning_dir: Path) -> int:
    return failure(planning_dir, "state-first-plan", "This is a state-first plan: mark sections with implement-progress "
                   "--stage started or done, finish with implement-verify -- <full suite>, and resume with status.")
