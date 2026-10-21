"""Plan gates say how to fix what they reject."""
import json

import pytest

from forge_test_helpers import load_zagrosi_module
from test_compact_plan import make_plan

VERIFIED = [
    "Case: `test_trim_edges` trims edges.\nExpected: 'Ada'.\nCommand: uv run pytest tests/test_labels.py\n",
    "verification_mode: inspection\nInspection: Read the README links.\nExpected: All resolve.\n"
    "test_rationale: documentation-only change.\n",
    "Run `uv run pytest tests/test_labels.py`; `test_trim_edges` expects trimmed labels.\n",
    "verification_mode: inspection\nExpected: Links resolve.\nBehavior: Read the docs.\nInspection: Read them.\n"
    "test_rationale: documentation-only change.\n",
]
UNVERIFIED = {
    "Case: `test_trim_edges` trims edges.\nExpected failure now: edges remain.\nCommand: uv run pytest tests/test_labels.py\n":
        "`Expected:`",
    "Case: `test_first` covers one path.\nExpected: passes.\n"
    "Case: `test_second` covers another.\nExpected: passes.\nCommand: uv run pytest tests/test_x.py\n":
        "Case `test_first` has no `Command:`",
    "verification_mode: inspection\nInspection: Read the log.\nExpected: Complete.\n"
    "test_rationale: process-only requirement.\n": "whole section",
    "Behavior: TODO\n": "has no actual value",
    "```python\nunclosed\n": "fence",
    "verification_mode: manual\n": "verification_mode",
    "Nothing to verify.\n": "Name a test case",
    "verification_mode: inspection\nBehavior: Read the docs.\nExpected: Links resolve.\nInspection: Read them.\n"
    "test_rationale: documentation-only change.\n": "before the first",
    "verification_mode: inspection\nverification_mode: test\n": "keep one",
}


@pytest.fixture(scope="module")
def forge():
    return load_zagrosi_module()


def invoke(forge, capsys, *args):
    code = forge.entrypoint.main(list(args))
    return code, json.loads(capsys.readouterr().out)


def compact_plan(tmp_path, contract):
    planning = tmp_path / "planning"
    (planning / "sections").mkdir(parents=True)
    (planning / "spec.md").write_text("REQ-001: Trim label edges.\n")
    (planning / "sections/index.md").write_text(
        '<!-- FORGE_META\n{"artifact_type": "compact_plan", "depth_mode": "lean", "source": "spec.md"}\nEND_FORGE_META -->\n'
        "<!-- PROJECT_CONFIG\nruntime: python\ntest_command: uv run pytest\nEND_PROJECT_CONFIG -->\n"
        "<!-- SECTION_MANIFEST\nsection-01-labels\nEND_MANIFEST -->\n"
        "Dependencies: none. Execution order: section-01-labels. Parallel: no siblings.\n")
    (planning / "sections/section-01-labels.md").write_text(
        f"## Contract\n### REQ-001\nSource: spec.md#L1\n{contract}\n"
        "## Owned files\n- src/labels.py\n- tests/test_labels.py\n\n"
        "## Evidence\nInspected `src/labels.py` and its tests; rollback reverts both files.\n\n"
        "## Review\nVerdict: pass\nReviewed: the contract, ownership, risks and rollback.\n")
    return planning


def test_unread_expected_label_is_named(forge):
    text = next(iter(UNVERIFIED))
    gaps = forge.markdown.verification_gaps(text)
    assert not forge.markdown.has_verification(text)
    assert len(gaps) == 1 and "`Expected:`" in gaps[0] and "Expected failure now" in gaps[0]


def test_gaps_match_has_verification(forge, tmp_path):
    planning = make_plan(tmp_path / "planning")
    for text in [*VERIFIED, *(path.read_text() for path in (planning / "sections").glob("section-*.md"))]:
        assert forge.markdown.verification_gaps(text) == [], text
        assert forge.markdown.has_verification(text)
    for text, fragment in UNVERIFIED.items():
        gaps = forge.markdown.verification_gaps(text)
        assert gaps and any(fragment in gap for gap in gaps), (text, gaps)
        assert not forge.markdown.has_verification(text)


def test_contract_findings_name_the_gap(forge, tmp_path, capsys):
    planning = compact_plan(tmp_path, "Behavior: Trim label edges.\nExpected: 'Ada'.\n")
    code, payload = invoke(forge, capsys, "lint-plan", "--planning-dir", str(planning), "--strict")
    assert code == 1
    findings = {item["code"]: item for item in payload["findings"]}
    assert "`Command:`" in findings["invalid-requirement-contract"]["message"]
    assert "`Command:`" in findings["compact-plan-no-tests"]["recommendation"]


SECTION = """# {name}

{req}.

## Contract
{contract}
## Owned files
- {source}
- {test}

## Compatibility
```json
{{"version": 1, "mode": "not_required", "reason": "New behavior only."}}
```

## Review
Verdict: pass
Reviewed: the case and ownership.
"""
TERM_CODES = {f"missing-{term}" for term in (
    "goal", "dependencies", "tests-first", "implementation", "risks", "acceptance", "rollback", "ownership")}


def two_section_plan(tmp_path, *, second_depends="section-01-first", second_contract=None, filler="",
                     planning_name="planning"):
    planning = tmp_path / planning_name
    (planning / "sections").mkdir(parents=True)
    (planning / "spec.md").write_text("REQ-001: Trim label edges.\nREQ-002: Lowercase labels.\n")
    (planning / "codex-plan.md").write_text(
        '<!-- FORGE_META\n{"artifact_type": "compact_plan", "depth_mode": "lean", "source": "spec.md"}\nEND_FORGE_META -->\n'
        "# Labels\n\n## Goal\nGoal: trim and lowercase labels. Non-goals: other fields.\n\n## Contract\n"
        "### REQ-001\nSource: spec.md#L1\nBehavior: Trim label edges.\nExpected: `test_trim` maps ' a ' to 'a'.\n"
        "Command: uv run pytest tests/test_labels.py\n\n"
        "### REQ-002\nSource: spec.md#L2\nBehavior: Lowercase labels.\nExpected: `test_lower` maps 'A' to 'a'.\n"
        "Command: uv run pytest tests/test_case.py\n\n"
        "## Owned files\n- src/labels.py\n- src/case.py\n\n"
        "## Evidence\nInspected `src/labels.py`; modify it and create `src/case.py`, red tests first. "
        "Risk: case drift. Rollback: revert both files. Acceptance: done when both tests pass.\n\n"
        "## Review\nVerdict: pass\nReviewed: contract, risks, rollback and ownership.\n")
    (planning / "sections/index.md").write_text(
        "<!-- PROJECT_CONFIG\nruntime: python\ntest_command: uv run pytest\nEND_PROJECT_CONFIG -->\n"
        "<!-- SECTION_MANIFEST\nsection-01-first\nsection-02-second\nEND_MANIFEST -->\n"
        "| Section | Depends on | REQ | Owns | Tests |\n|---|---|---|---|---|\n"
        "| section-01-first | none | REQ-001 | src/labels.py | tests/test_labels.py |\n"
        f"| section-02-second | {second_depends} | REQ-002 | src/case.py | tests/test_case.py |\n\n"
        f"Execution order: manifest order. Parallel: none.{filler}\n")
    contracts = [
        "Case: `test_trim` maps ' a ' to 'a'.\nExpected: 'a'.\nCommand: uv run pytest tests/test_labels.py\n",
        second_contract or "Case: `test_lower` maps 'A' to 'a'.\nExpected: 'a'.\nCommand: uv run pytest tests/test_case.py\n",
    ]
    for (name, req, source, test), contract in zip(
            [("section-01-first", "REQ-001", "src/labels.py", "tests/test_labels.py"),
             ("section-02-second", "REQ-002", "src/case.py", "tests/test_case.py")], contracts):
        (planning / "sections" / f"{name}.md").write_text(
            SECTION.format(name=name, req=req, contract=contract, source=source, test=test))
    return planning


def finding_codes(forge, capsys, planning):
    findings = []
    for command in ("lint-sections", "lint-implementation-readiness"):
        findings += invoke(forge, capsys, command, "--planning-dir", str(planning), "--strict")[1]["findings"]
    return findings


def test_compact_sections_inherit_shared_terms(forge, tmp_path, capsys):
    findings = finding_codes(forge, capsys, two_section_plan(tmp_path))
    assert TERM_CODES.isdisjoint(item["code"] for item in findings), findings


def test_missing_verification_names_the_gap(forge, tmp_path, capsys):
    planning = two_section_plan(tmp_path, second_contract="Case: `test_lower` maps 'A' to 'a'.\nExpected: 'a'.\n")
    findings = {item["code"]: item for item in finding_codes(forge, capsys, planning)}
    assert "`Command:`" in findings["missing-verification"]["recommendation"]


@pytest.mark.parametrize("shorthand", ["01", "section-01", "1", "Section 1"])
def test_dependency_shorthand_names_full_sections(forge, tmp_path, capsys, shorthand):
    findings = finding_codes(forge, capsys, two_section_plan(tmp_path, second_depends=shorthand))
    messages = [item["message"] for item in findings if "Unrecognized dependencies" in item["message"]]
    assert messages and messages[0].endswith("name full sections such as section-01-first")


def test_shared_text_only_for_valid_multi_section_compact_plans(forge, tmp_path):
    shared = forge.artifacts.shared_contract_text
    compact = two_section_plan(tmp_path / "compact")
    assert "Goal: trim" in shared(compact) and "Depends on" in shared(compact)
    assert shared(make_plan(tmp_path / "single")) == ""
    assert "Goal: trim" in shared(two_section_plan(tmp_path / "nested", planning_name="sections"))
    singleton = two_section_plan(tmp_path / "singleton")
    index = singleton / "sections/index.md"
    index.write_text(index.read_text().replace("section-02-second\nEND_MANIFEST", "END_MANIFEST").replace(
        "| section-02-second | section-01-first | REQ-002 | src/case.py | tests/test_case.py |\n", ""))
    (singleton / "sections/section-02-second.md").unlink()
    assert shared(singleton) == ""
    for name, change in [("legacy", lambda text: text.split("END_FORGE_META -->\n", 1)[1]),
                         ("unknown depth", lambda text: text.replace('"lean"', '"unknown"'))]:
        planning = two_section_plan(tmp_path / name)
        plan = planning / "codex-plan.md"
        plan.write_text(change(plan.read_text()))
        assert shared(planning) == "", name


def test_legacy_sections_still_need_their_own_terms(forge, tmp_path, capsys):
    planning = two_section_plan(tmp_path)
    plan = planning / "codex-plan.md"
    plan.write_text(plan.read_text().split("END_FORGE_META -->\n", 1)[1])
    assert "missing-goal" in {item["code"] for item in finding_codes(forge, capsys, planning)}


def test_budget_findings_explain_counting(forge, tmp_path, capsys):
    planning = two_section_plan(tmp_path, filler=" Shared notes." * 80)
    findings = {item["code"]: item for item in finding_codes(forge, capsys, planning)}
    assert "letters, digits or underscores" in findings["section-index-too-large"]["recommendation"]
