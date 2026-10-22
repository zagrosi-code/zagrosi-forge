"""Host-neutral plan.md is read after the legacy host-prefixed plan names."""

import pytest

from forge_test_helpers import ROOT, run_cmd
from runtime_support import load_runtime
from test_compact_plan import SECTION, make_plan


@pytest.fixture(scope="module")
def forge():
    return load_runtime(ROOT / "scripts/zagrosi_skills.py")


def physical_plan(planning, name="plan.md"):
    """Move the canonical section into a named plan, as a multi-section compact plan does."""
    planning = make_plan(planning)
    index = planning / "sections/index.md"
    marker, rest = index.read_text().split("END_FORGE_META -->\n", 1)
    section = planning / "sections" / f"{SECTION}.md"
    (planning / name).write_text(marker + "END_FORGE_META -->\n" + section.read_text())
    index.write_text(rest)
    return planning


def test_plan_md_is_a_canonical_plan_name(forge, tmp_path):
    planning = physical_plan(tmp_path / "planning")
    assert forge.artifacts.implementation_plan_path(planning) == planning / "plan.md"
    assert forge.artifacts.plan_artifact_state(planning)["plan"] == planning / "plan.md"
    assert forge.artifacts.planning_depth(planning, "deep") == "lean"
    checked = run_cmd("planning-consistency", "--planning-dir", str(planning))["checked_artifacts"]
    assert checked["plan.md"]["requirement_ids"] == ["REQ-001"]


@pytest.mark.parametrize("legacy", ["codex-plan.md", "claude-plan.md"])
def test_legacy_plan_names_win_over_plan_md(forge, tmp_path, legacy):
    planning = physical_plan(tmp_path / "planning", legacy)
    (planning / "plan.md").write_text("# Unrelated notes\n")
    assert forge.artifacts.implementation_plan_path(planning) == planning / legacy


@pytest.mark.parametrize(("text", "scaffold"), [
    ("<!-- FORGE_META\n{\"artifact_type\":\"compact_plan\"}\nEND_FORGE_META -->\n# Plan\n", False),
    ("# Scratch notes\n", True),
])
def test_plan_setup_skips_the_scaffold_only_beside_a_forge_plan_md(tmp_path, text, scaffold):
    planning = tmp_path / "planning"
    planning.mkdir()
    (planning / "spec.md").write_text("REQ-001: Trim labels.\n")
    (planning / "plan.md").write_text(text)
    payload = run_cmd("plan-setup", "--file", str(planning / "spec.md"), "--target-dir", str(tmp_path), "--flight", "off")
    assert bool(payload["scaffold"]["created"]) is scaffold


def test_scratch_plan_md_leaves_a_compact_plan_unchanged(forge, tmp_path):
    planning = make_plan(tmp_path / "planning")
    before = run_cmd("lint-plan", "--planning-dir", str(planning), "--strict")
    (planning / "plan.md").write_text("# Scratch notes\n\nNo requirements here.\n")
    assert forge.artifacts.implementation_plan_path(planning) == planning / "sections" / f"{SECTION}.md"
    assert run_cmd("lint-plan", "--planning-dir", str(planning), "--strict")["findings"] == before["findings"]
    checked = run_cmd("planning-consistency", "--planning-dir", str(planning))["checked_artifacts"]
    assert "plan.md" not in checked


def test_unreadable_scratch_plan_md_is_not_a_plan(forge, tmp_path):
    planning = make_plan(tmp_path / "planning")
    (planning / "plan.md").write_text("# Notes\n", encoding="utf-16")
    assert forge.artifacts.plan_file(planning) is None
    assert run_cmd("lint-plan", "--planning-dir", str(planning), "--strict")["success"]


def test_team_preparation_accepts_plan_md_after_legacy_names(forge):
    plan, selected = forge.team_plans._seeds({"plan.md": {}, "sections/index.md": {}, "spec.md": {}})
    assert plan == "plan.md" and {"plan.md", "sections/index.md"} <= selected
    plan, selected = forge.team_plans._seeds({"codex-plan.md": {}, "plan.md": {}, "sections/index.md": {}})
    assert plan == "codex-plan.md" and "plan.md" not in selected


def test_eval_discovery_reads_only_forge_plan_md(forge, tmp_path):
    physical_plan(tmp_path / "examples/labels")
    for name, text in (("scratch", "# Notes\n"), ("state", "# Plan: x\n\n## Goal\nX.\n")):
        (tmp_path / "examples" / name).mkdir(parents=True)
        (tmp_path / "examples" / name / "plan.md").write_text(text)
    mode, _, _, benchmarks, errors = forge.evaluations.eval_suite_benchmarks(tmp_path / "examples", "lean")
    assert (mode, errors) == ("glob", [])
    assert [item["name"] for item in benchmarks] == ["labels"]
