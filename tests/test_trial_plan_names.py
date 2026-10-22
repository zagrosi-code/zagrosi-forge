"""Trial tools find the host-neutral plan.md like the legacy plan names."""
import sys

from forge_test_helpers import ROOT

sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_evidence import planning_root
from coding_trial_workflow import _plan


def test_trial_tools_accept_plan_md(tmp_path):
    plan = tmp_path / ".planning/feature/plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# Plan\n")
    (plan.parent / "status.json").write_text("{}\n")
    assert planning_root(tmp_path) == plan.parent
    workflow = {".planning/feature/plan.md": {"type": "file"}, ".planning/feature/status.json": {"type": "file"}}
    assert _plan(workflow) == (".planning/feature", ".planning/feature/plan.md")


def test_trial_tools_ignore_scratch_plan_md(tmp_path):
    index = tmp_path / ".planning/feature/sections/index.md"
    index.parent.mkdir(parents=True)
    index.write_text("<!-- SECTION_MANIFEST -->\n")
    (tmp_path / ".planning/plan.md").write_text("# Scratch\n")
    assert planning_root(tmp_path) == tmp_path / ".planning/feature"
    workflow = {".planning/plan.md": {"type": "file"}, ".planning/feature/sections/index.md": {"type": "file"}}
    assert _plan(workflow) == (".planning/feature", ".planning/feature/sections/index.md")
