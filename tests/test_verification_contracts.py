from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_compact_plan import SECTION, make_plan


@pytest.fixture(scope="module")
def modules():
    root = Path(__file__).resolve().parents[1]
    from runtime_support import load_entrypoint
    package = load_entrypoint(root / "scripts/zagrosi_skills.py").load_runtime()
    return SimpleNamespace(**{name: importlib.import_module(f"{package.__name__}.{name}") for name in ("markdown", "artifacts", "scoring", "validation", "session")})


INSPECTION = (
    "REQ-001\nverification_mode: inspection\n"
    "Inspection: render docs/guide.md and check heading order and wrapping.\n"
    "Expected: one readable heading and no clipped lines.\n"
    "test_rationale: documentation-only wording; runtime behavior is unchanged.\n"
)


def replace_verification(planning: Path, verification: str) -> None:
    path = planning / "sections" / f"{SECTION}.md"
    before, after = path.read_text().split("## Tests first\n", 1)
    after = after.split("\n## Implementation contract", 1)[1]
    path.write_text(before + "## Tests first\n" + verification + "\n## Implementation contract" + after)


@pytest.mark.parametrize("text", [
    "REQ-001: `TestTrimEdges` expects edge whitespace removed. Run `go test ./labels`.",
    "REQ-001: `trims_label_edges` expects edge whitespace removed. Run `cargo test trims_label_edges`.",
    "REQ-001\nCase: label whitespace\nExpected: trimmed edges with internal whitespace preserved.\nCommand: ./scripts/check-labels\n",
    "REQ-001\nBehavior: trim label edges\nExpected: internal whitespace preserved.\nRun `dotnet test Labels.Tests`.\n",
])
def test_language_neutral_regression_contract_passes_compact_and_readiness(modules, tmp_path, text):
    planning = make_plan(tmp_path / "planning", "deep")
    replace_verification(planning, text)
    assert modules.markdown.has_verification(text)
    assert not modules.artifacts.compact_plan_findings(planning)
    findings, _ = modules.scoring.implementation_readiness_analysis(planning, 12)
    assert not findings


@pytest.mark.parametrize("text", [
    "TestTrimEdges", "Run `go test ./labels`.", "Case: label whitespace\nExpected: trim edges.",
    "verification_mode: inspection\nInspection: view the page.",
    INSPECTION.replace("Expected: one readable heading and no clipped lines.\n", ""),
    INSPECTION.replace("documentation-only wording; runtime behavior is unchanged.", "behavior change requires no tests"),
    "```text\n" + INSPECTION + "```\n",
])
def test_incomplete_verification_cannot_admit_compact_plan(modules, tmp_path, text):
    planning = make_plan(tmp_path / "planning")
    replace_verification(planning, text)
    assert not modules.markdown.has_verification(text)
    assert "compact-plan-no-tests" in {finding.code for finding in modules.artifacts.compact_plan_findings(planning)}
    findings, _ = modules.scoring.implementation_readiness_analysis(planning, 12)
    assert "missing-verification" in {finding.code for finding in findings}


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_recorded_inspection_passes_readiness_and_tdd_at_every_depth(modules, tmp_path, depth):
    planning = make_plan(tmp_path / "planning", depth)
    replace_verification(planning, INSPECTION)
    (planning / "spec.md").write_text("REQ-001: Clarify the guide heading without changing the documented behavior.\n")
    section = planning / "sections" / f"{SECTION}.md"
    text = section.read_text().replace("src/labels.py\ntests/test_labels.py", "docs/guide.md")
    before, after = text.split("## Implementation contract\n", 1)
    after = after.split("\n## Evidence", 1)[1]
    section.write_text(before + "## Implementation contract\nClarify the guide heading; preserve its meaning and anchor.\n\n## Evidence" + after)
    assert modules.markdown.has_verification(INSPECTION)
    findings, readiness = modules.scoring.implementation_readiness_analysis(planning, 12)
    assert not findings
    assert readiness["sections"][0]["files"] == ["docs/guide.md"]
    args = SimpleNamespace(planning_dir=str(planning), depth=None, profile="solo", strict=True, export=None, export_format="jsonl")
    captured = {}
    token = modules.session._QUALITY_CAPTURE.set(captured)
    try:
        modules.validation.lint_plan(args)
    finally:
        modules.session._QUALITY_CAPTURE.reset(token)
    assert captured["success"], captured


@pytest.mark.parametrize("plan,expected", [(None, "write the canonical implementation plan"), (Path("section.md"), "review plan and record the verdict")])
def test_next_action_does_not_require_a_duplicate_file(modules, plan, expected):
    action = modules.artifacts.next_plan_action({"plan": plan, "review": None, "section_index": Path("index.md")}, {"state": "complete"}, {})
    assert action == expected


def test_display_names_expand_without_changing_exact_owned_test_metadata(modules):
    text = "`TestTrimEdges`, `trims_edges`, `test_trim_edges`, `go test ./labels`."
    assert {"TestTrimEdges", "trims_edges", "test_trim_edges"}.issubset(modules.markdown.test_names(text))
    assert modules.markdown.section_owned_test_names(text) == ["test_trim_edges"]
