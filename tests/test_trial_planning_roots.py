"""Trials grade the admitted plan where the prompt permits it, without guessing."""
import pytest

from test_coding_trials import trials, write_cleanup, write_review, write_workflow
from coding_trial_evidence import planning_root


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_nested_plan_passes_the_same_completion_checks(tmp_path, depth):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup", depth=depth)
    write_cleanup(trial)
    workspace = trial / "workspace"
    planning = workspace / ".planning/feature"
    write_workflow(workspace, depth, planning=planning)
    result = trials.check(trial, review=write_review(trial))
    assert result["success"], result
    assert result["workflow"]["report"]["planning_dir"] == str(planning)
    assert result["workflow"]["report"]["planning_depth"] == depth
    (planning / "spec.md").unlink()
    assert not trials.check(trial, review=write_review(trial))["workflow"]["success"]


@pytest.mark.parametrize("marker", ["codex-plan.md", "claude-plan.md", "sections/index.md"])
def test_conflicting_plan_roots_are_rejected(tmp_path, marker):
    for location in (".planning", ".planning/feature"):
        path = tmp_path / location / marker
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("plan")
    with pytest.raises(ValueError, match="multiple planning roots"):
        planning_root(tmp_path)


@pytest.mark.parametrize("link", [".planning", ".planning/codex-plan.md"])
def test_planning_links_cannot_escape_workspace(tmp_path, link):
    workspace, external = tmp_path / "workspace", tmp_path / "external"
    workspace.mkdir()
    external.mkdir()
    destination = external if link == ".planning" else external / "plan.md"
    if destination.suffix:
        destination.write_text("plan")
    path = workspace / link
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(destination, target_is_directory=destination.is_dir())
    except OSError:
        pytest.skip("Symlinks are unavailable")
    with pytest.raises(ValueError, match="within the workspace|inside .planning"):
        planning_root(workspace)
