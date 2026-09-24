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
    return SimpleNamespace(**{name: importlib.import_module(f"{package.__name__}.{name}") for name in ("markdown", "artifacts", "scoring", "validation", "quality", "traceability")})


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
    findings, extras = modules.validation.plan_analysis(planning)
    payload = modules.quality.quality_payload("plan", findings, extras, strict=True)
    assert payload["success"], payload



@pytest.mark.parametrize("plan,expected", [(None, "write the canonical implementation plan"), (Path("section.md"), "review plan and record the verdict")])
def test_next_action_does_not_require_a_duplicate_file(modules, plan, expected):
    action = modules.artifacts.next_plan_action({"plan": plan, "review": None, "section_index": Path("index.md")}, {"state": "complete"}, {})
    assert action == expected


def test_display_names_expand_without_changing_exact_owned_test_metadata(modules):
    text = "`TestTrimEdges`, `trims_edges`, `test_trim_edges`, `go test ./labels`."
    assert {"TestTrimEdges", "trims_edges", "test_trim_edges"}.issubset(modules.markdown.test_names(text))
    assert modules.markdown.section_owned_test_names(text) == ["test_trim_edges"]


REGRESSION = "REQ-001\nCase: trim label edges\nExpected: internal spaces remain.\nCommand: ./scripts/check-labels\n"


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("verification", [
    REGRESSION.replace("Case:", "Test case:"),
    REGRESSION.replace("Case:", "**Case:**").replace("Expected:", "**Expected:**").replace("Command:", "**Command:**"),
    REGRESSION.replace("Case:", "- **Case**:").replace("Expected:", "- **Expected**:").replace("Command:", "- **Command**:"),
    INSPECTION.replace("verification_mode:", "**verification_mode:**").replace("Inspection:", "**Inspection:**")
    .replace("Expected:", "**Expected:**").replace("test_rationale:", "**test_rationale:**"),
])
def test_equivalent_labels_share_all_verification_gates(modules, tmp_path, depth, verification):
    planning = make_plan(tmp_path / "planning", depth)
    replace_verification(planning, verification)
    assert modules.markdown.has_verification(verification)
    assert not modules.artifacts.compact_plan_findings(planning)
    assert not modules.scoring.implementation_readiness_analysis(planning, 12)[0]
    assert not modules.validation.plan_analysis(planning)[0]
    findings, trace = modules.traceability.traceability_analysis(planning)
    assert not findings
    assert trace["coverage"]["REQ-001"]["section_tests"] == [f"{SECTION}.md"]


@pytest.mark.parametrize("verification", [
    "<!--\nREQ-001: `test_trim_edges`. Run `pytest -q`.\n-->",
    "<!--\n" + REGRESSION + "\n-->",
    "<!--\n" + INSPECTION + "\n-->",
    "<!--\n" + REGRESSION,
    "```markdown\n" + REGRESSION + "```",
    "```text\nREQ-001: `test_trim_edges`. Run `pytest -q`.\n```",
    "```python\ndef test_trim_edges():\n    assert trim(' a ') == 'a'\nRun `pytest -q`.\n",
    "REQ-001: `test_trim_edges`. This project uses pytest, but no command is selected.",
    "REQ-001: `business_logic`. Run `pytest -q`.",
    "REQ-001: `tests/test_labels.py`. Run `pytest -q`.",
    "REQ-001\ntest_command: pytest -q\n",
    REGRESSION + "Command: pending\n",
    REGRESSION + "test_command: ./scripts/different-check\n",
    INSPECTION + "verification_mode: automated\n",
    INSPECTION + "Expected: pending\n",
])
def test_hidden_incomplete_or_conflicting_evidence_is_not_verification(modules, tmp_path, verification):
    planning = make_plan(tmp_path / "planning")
    replace_verification(planning, verification)
    assert not modules.markdown.has_verification(verification)
    assert "compact-plan-no-tests" in {finding.code for finding in modules.artifacts.compact_plan_findings(planning)}
    assert "missing-verification" in {finding.code for finding in modules.scoring.implementation_readiness_analysis(planning, 12)[0]}
    findings, trace = modules.traceability.traceability_analysis(planning)
    assert "traceability-gap" in {finding.code for finding in findings}
    assert trace["coverage"]["REQ-001"]["section_tests"] == []


@pytest.mark.parametrize("verification", [
    "REQ-001: `test_trim_edges` preserves inner spaces. Run `pytest -q`.\n<!-- obsolete `test_other`: run jest -->",
    "<!--\n```python\n-->\n" + REGRESSION,
    "REQ-001: `test_html_comment` returns literal `<!--`. Run `pytest -q`.",
    "REQ-001\n```python\ndef test_trim_edges():\n    assert trim(' a ') == 'a'\n```\nRun `pytest -q`.",
    "REQ-001: `test_trim_edges` removes edges.\n```bash\npytest -q\n```",
    "REQ-001: Write red Vitest cases: `valid_callback_creates_session`. Run `npm test`.",
    "REQ-001: Write red `unauthenticated_user_cannot_update`. Run `npm test`.",
    REGRESSION + "test_command: ./scripts/check-labels\n",
])
def test_visible_and_executable_contracts_remain_valid(modules, verification):
    assert modules.markdown.has_verification(verification)


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("verification", ["REQ-001\n## Tests first\npytest is installed.\n", "<!--\n" + REGRESSION + "-->\n"])
def test_physical_traceability_requires_real_section_or_tdd_verification(modules, tmp_path, depth, verification):
    planning = make_plan(tmp_path / "planning", depth)
    (planning / "codex-plan.md").write_text("REQ-001: trim label edges.\n")
    (planning / "codex-plan-tdd.md").write_text(verification)
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text("REQ-001: trim label edges.\n" + verification)
    findings, trace = modules.traceability.traceability_analysis(planning)
    assert "traceability-gap" in {finding.code for finding in findings}
    assert not trace["coverage"]["REQ-001"]["covered"]


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_packet_cannot_use_a_tests_heading_as_verification(tmp_path, capsys, depth):
    from runtime_support import load_runtime
    runtime = load_runtime(Path(__file__).resolve().parents[1] / "scripts/zagrosi_skills.py")
    planning = make_plan(tmp_path / "planning", depth)
    (planning / "codex-plan.md").write_text("REQ-001: trim label edges.\n")
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text("REQ-001: trim label edges.\n## Tests first\npytest is installed.\n")
    assert runtime.entrypoint.main(["implementation-packet", "--planning-dir", str(planning), "--section", SECTION]) == 1
    assert '"tdd"' in capsys.readouterr().out
    assert not (planning / ".forge/packets").exists()


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("separator", ["", "\n", "\n### Empty labels\n\n"])
def test_multiple_complete_cases_remain_valid(modules, tmp_path, depth, separator):
    verification = REGRESSION + separator + "Case: empty label\nExpected: empty string.\nCommand: ./scripts/check-empty\n"
    planning = make_plan(tmp_path / "planning", depth)
    replace_verification(planning, verification)
    assert modules.markdown.has_verification(verification)
    assert not modules.artifacts.compact_plan_findings(planning)
    assert not modules.scoring.implementation_readiness_analysis(planning, 12)[0]
    assert not modules.traceability.traceability_analysis(planning)[0]


@pytest.mark.parametrize("suffix", [
    "Case: empty label\nCommand: ./scripts/check-empty\n",
    "Case: empty label\nExpected: empty string.\n",
    "Case: empty label\nExpected: empty string.\nCommand: ./scripts/check-empty\nExpected: pending\n",
])
def test_complete_case_does_not_hide_an_incomplete_or_conflicting_case(modules, suffix):
    assert not modules.markdown.has_verification(REGRESSION + "\n### Empty labels\n" + suffix)


def test_multiple_cases_can_share_a_command(modules):
    assert modules.markdown.has_verification(
        "Command: ./scripts/check-labels\nCase: trim edges\nExpected: interior spaces remain.\n"
        "Case: empty labels\nExpected: empty string.\n"
    )


@pytest.mark.parametrize("prefix", ["\\`", "\\\\\\`", "`literal\\`"])
def test_literal_or_closed_backtick_cannot_hide_a_comment_opener(modules, prefix):
    verification = prefix + "<!--\nCase: hidden\nExpected: hidden result\nCommand: ./hidden-test\n` -->\n"
    assert "Case: hidden" not in modules.markdown.visible_markdown(verification)
    assert not modules.markdown.has_verification(verification)


@pytest.mark.parametrize("slashes", [1, 2, 3, 4])
def test_opening_backtick_respects_prose_escape_parity(modules, slashes):
    source = "\\" * slashes + "`<!-- literal -->`"
    visible = modules.markdown.visible_markdown(source)
    assert ("<!-- literal -->" in visible) is (slashes % 2 == 0)


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("hidden_artifact", ["spec.md", "codex-plan.md"])
def test_hidden_requirement_cannot_disagree_between_traceability_and_packet(tmp_path, capsys, depth, hidden_artifact):
    from runtime_support import load_runtime
    runtime = load_runtime(Path(__file__).resolve().parents[1] / "scripts/zagrosi_skills.py")
    planning = make_plan(tmp_path / "planning", depth)
    (planning / "codex-plan.md").write_text("REQ-001: trim label edges.\n")
    (planning / "codex-plan-tdd.md").write_text(REGRESSION)
    (planning / hidden_artifact).write_text("<!-- REQ-001: hidden requirement -->\n")
    findings, trace = runtime.traceability.traceability_analysis(planning)
    assert findings
    assert not trace["coverage"].get("REQ-001", {}).get("covered", False)
    assert runtime.entrypoint.main(["implementation-packet", "--planning-dir", str(planning), "--section", SECTION]) == 1
    assert '"coverage_gaps"' in capsys.readouterr().out
