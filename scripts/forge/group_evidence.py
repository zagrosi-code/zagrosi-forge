"""Derive portable section-group identities and validate reported check results."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re

from . import markdown, mutable_inputs, ownership, policy, team_git, team_plans, team_state, team_workflow


def read_report(path):
    report = team_git.decode_json(team_plans._read(Path(path), team_plans.BUNDLE_LIMIT))
    if not isinstance(report, dict) or type(report.get("success")) is not bool or not isinstance(report.get("handoff"), dict):
        raise ValueError("Expected a Forge section-group handoff or verification report.")
    return report


def describe(repo, plan, target, selected, pr, *, check_checkout=False):
    """Derive the wire identity exclusively from the named commit; optionally also check the live plan."""
    if any(team_state.normalize_paths([path]) != [path] for path in (plan, target)):
        raise ValueError("Use canonical repository-relative paths for the group plan and target.")
    if pr is not None and not team_state.is_plain_text(pr, 200):
        raise ValueError("Use a nonempty PR identifier of at most 200 characters.")
    if team_workflow.group_selection(selected) != selected:
        raise ValueError("Use distinct section names in canonical order.")
    if target != ".":
        obj = team_git._run(repo.root, "cat-file", "-t", f"{repo.head}:{target}", check=False)
        if obj["returncode"] or obj["stdout"].strip() != "tree":
            raise ValueError("The group target must be a directory in the recorded Git commit.")
    files, content = team_plans._bundle(repo, plan, committed_only=not check_checkout)
    if check_checkout:
        team_plans._contract(repo.root / plan)
    manifest = content["sections/index.md"].decode("utf-8")
    known, errors = markdown.parse_numbered_manifest(manifest, "SECTION_MANIFEST", policy.SECTION_RE, prefix="section-")
    if errors or not set(selected).issubset(known):
        raise ValueError("A reported section is absent from the committed plan manifest.")
    paths = set()
    for section in selected:
        text = content[f"sections/{section}.md"].decode("utf-8")
        declared = ownership.extract_section_owned_paths(text)
        if not declared:
            raise ValueError(f"Section {section} must declare its owned paths.")
        paths.update((PurePosixPath(target) / name).as_posix() for name in team_state.normalize_paths(declared))
    return {"schema": "forge-section-group-v1", "commit_sha": repo.head,
            "tree_sha": team_git._output(repo.root, "rev-parse", "--verify", repo.head + "^{tree}"),
            "plan_path": plan, "plan_digest": team_plans._digest(files), "target_path": target,
            "sections": selected, "owned_paths": team_state.normalize_paths(sorted(paths)), "pull_request": pr}


def validate_identity(repo, identity, *, checkout_target=None):
    if not isinstance(identity, dict) or identity.get("schema") != "forge-section-group-v1":
        raise ValueError("Expected a Forge section-group handoff.")
    sha = team_git._oid(identity.get("commit_sha"))
    if checkout_target is not None:
        if sha != repo.head:
            raise ValueError("The receiving checkout must be at the exact handoff commit.")
        if identity.get("target_path") != checkout_target.relative_to(repo.root).as_posix():
            raise ValueError("The handoff belongs to a different target.")
    elif team_git._output(repo.root, "rev-parse", "--verify", sha + "^{commit}") != sha:
        raise ValueError("The group must identify a commit object directly.")
    current = describe(replace(repo, head=sha), identity.get("plan_path"), identity.get("target_path"),
                       identity.get("sections"), identity.get("pull_request"), check_checkout=checkout_target is not None)
    if identity.get("tree_sha") != current["tree_sha"]:
        raise ValueError("The group tree differs from its recorded Git commit.")
    if identity.get("plan_digest") != current["plan_digest"]:
        raise ValueError("The group plan digest differs from its committed contract.")
    if identity != current:
        raise ValueError("The handoff differs from the committed section ownership or group schema.")
    return current


def utc_time(value):
    try:
        moment = datetime.fromisoformat(value)
        if moment.utcoffset() is not None:
            return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (OverflowError, TypeError, ValueError):
        pass
    raise ValueError("Use an ISO 8601 timestamp with a timezone that can be represented in UTC.")


def run_summary(record):
    """The compact history item accompanies a full execution report and never establishes one on its own."""
    run_id = record.get("run_id")
    command = record.get("command")
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("The group report needs a completed verification run identifier.")
    if (not isinstance(command, list) or not command or not command[0]
            or any(not isinstance(arg, str) or "\0" in arg for arg in command)):
        raise ValueError("The group run must identify its actual command arguments.")
    code, seconds, timeout = record.get("returncode"), record.get("seconds"), record.get("timed_out")
    cancelled = record.get("cancelled", False)
    if (type(code) is not int or not -(2**31) <= code < 2**32 or type(seconds) not in (int, float)
            or not 0 <= seconds <= 172800 or type(timeout) is not bool or type(cancelled) is not bool
            or timeout and code != 124 or cancelled and code != 130):
        raise ValueError("The group run has inconsistent process metadata.")
    failures = {name: record[name] for name in ("snapshot_error", "termination_error") if name in record}
    if any(not isinstance(reason, str) or not reason.strip() for reason in failures.values()):
        raise ValueError("A group verification failure must describe what went wrong.")
    passed = code == 0 and not failures and not timeout and not cancelled
    summary = {"run_id": run_id, "seconds": seconds, "returncode": code, "passed": passed,
               "command_sha256": mutable_inputs.digest(command)}
    return summary


def validated_run(report):
    """Return only a complete measured run. A checked handoff without a run supplies historical context."""
    if "verification" not in report:
        if not report["success"]:
            raise ValueError("A failed group report must include its observed verification.")
        return None
    run = report["verification"]
    if not isinstance(run, dict):
        raise ValueError("The group verification must be a recorded run.")
    summary = run_summary(run)
    if report["success"] is not summary["passed"]:
        raise ValueError("The report success flag disagrees with the process result.")
    history = report.get("history")
    saved = history.get("group_check") if isinstance(history, dict) else None
    if (not isinstance(saved, dict) or type(saved.get("passed")) is not bool
            or type(saved.get("returncode")) is not int or type(saved.get("seconds")) not in (int, float)
            or saved != summary):
        raise ValueError("The recorded group check disagrees with the reported command or outcome.")
    fields = ("run_id", "command", "seconds", "returncode", "timed_out", "snapshot_error", "termination_error", "cancelled")
    checked = {key: run[key] for key in fields if key in run}
    if "started_at" in run or "finished_at" in run:
        for field in ("started_at", "finished_at"):
            checked[field] = utc_time(run.get(field))
        if checked["finished_at"] < checked["started_at"]:
            raise ValueError("The group run finished before its recorded start time.")
    checked["outcome"] = "timed_out" if run["timed_out"] else ("passed" if summary["passed"] else "failed")
    return checked
