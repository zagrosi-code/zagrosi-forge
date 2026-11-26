"""Validate portable, reported group checks against the locally available Git commit."""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path, PurePosixPath
import re

from . import markdown, mutable_inputs, ownership, policy, team_git, team_plans, team_state


def read_test(path, repo, pr):
    report = team_git.decode_json(team_plans._read(Path(path), team_plans.BUNDLE_LIMIT))
    identity = report.get("handoff", {})
    run = report.get("verification", {})
    if identity.get("schema") != "forge-section-group-v1" or identity.get("pull_request") != pr:
        raise ValueError("A group verification report must identify this PR.")
    tested = repo.commit(identity["commit_sha"])
    if identity["tree_sha"] != repo.tree(tested):
        raise ValueError("The reported group tree differs from its local Git commit.")
    plan, target = identity["plan_path"], identity["target_path"]
    if any(team_state.normalize_paths([name]) != [name] for name in (plan, target)):
        raise ValueError("Use canonical repository-relative group paths.")
    selected = identity["sections"]
    if (not isinstance(selected, list) or not 1 <= len(selected) <= 99
            or any(not isinstance(s, str) or not policy.SECTION_RE.fullmatch(s) for s in selected)
            or sorted(set(selected)) != selected):
        raise ValueError("The report must identify the distinct selected sections in canonical order.")
    committed = replace(team_git.Repository.discover(repo.target), head=tested)
    files, content = team_plans._bundle(committed, plan, committed_only=True)
    if team_plans._digest(files) != identity["plan_digest"]:
        raise ValueError("The reported plan digest differs from its committed contract.")
    index = content["sections/index.md"].decode("utf-8")
    known, errors = markdown.parse_numbered_manifest(index, "SECTION_MANIFEST", policy.SECTION_RE, prefix="section-")
    if errors or not set(selected).issubset(known):
        raise ValueError("A reported section is absent from the committed plan manifest.")
    paths = set()
    for section in selected:
        contract = content[f"sections/{section}.md"].decode("utf-8")
        owned = ownership.extract_section_owned_paths(contract)
        if not owned:
            raise ValueError(f"No ownership recorded for group section {section}.")
        paths.update((PurePosixPath(target) / p).as_posix() for p in team_state.normalize_paths(owned))
    if team_state.normalize_paths(sorted(paths)) != identity["owned_paths"]:
        raise ValueError("Group ownership differs from the selected committed sections.")
    if not isinstance(run.get("run_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", run["run_id"]):
        raise ValueError("The section-group report needs a completed verification run identifier.")
    command = run.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(v, str) for v in command) or not command[0]:
        raise ValueError("The group run must identify the command that was checked.")
    code, timeout, seconds = run.get("returncode"), run.get("timed_out"), run.get("seconds")
    if (type(code) is not int or type(timeout) is not bool or type(seconds) not in (int, float)
            or not math.isfinite(seconds) or seconds < 0 or timeout and code != 124):
        raise ValueError("The group run has inconsistent process metadata.")
    failures = {name: run[name] for name in ("snapshot_error", "termination_error") if run.get(name)}
    if any(not isinstance(message, str) or not message.strip() for message in failures.values()):
        raise ValueError("A group verification failure must describe what went wrong.")
    passed = code == 0 and not timeout and not failures
    if report.get("success") is not passed:
        raise ValueError("The report success flag disagrees with the process result.")
    group_check = report.get("history", {}).get("group_check")
    expected_check = {"run_id": run["run_id"], "seconds": seconds, "returncode": code, "passed": passed,
                      "command_sha256": mutable_inputs.digest(command)}
    if group_check != expected_check:
        raise ValueError("The recorded group check disagrees with the reported command or outcome.")
    group = {k: identity[k] for k in ("plan_path", "plan_digest", "target_path", "sections", "owned_paths")}
    group.update(run_id=run["run_id"], command=command, seconds=seconds, returncode=code, timed_out=timeout, **failures)
    return tested, "timed_out" if timeout else ("passed" if passed else "failed"), group
