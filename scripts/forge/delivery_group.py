"""Import a verified section-group report with explicit PR provenance."""
from __future__ import annotations

from . import group_evidence, team_git


def read_test(path, repo, pr):
    report = group_evidence.read_report(path)
    identity = group_evidence.validate_identity(team_git.Repository.discover(repo.target), report["handoff"])
    if identity["pull_request"] != pr:
        raise ValueError("A group verification report must identify this PR.")
    run = group_evidence.validated_run(report)
    if run is None:
        raise ValueError("Supply a group verification report containing a completed check.")
    group = {key: identity[key] for key in ("plan_path", "plan_digest", "target_path", "sections", "owned_paths")}
    group.update({key: value for key, value in run.items() if key != "outcome"})
    return identity["commit_sha"], run["outcome"], group
