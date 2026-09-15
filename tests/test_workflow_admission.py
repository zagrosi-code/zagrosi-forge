"""Planning prose must neither bypass admission nor reject valid authoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge_test_helpers import run_cmd, run_raw
from test_compact_plan import SECTION, make_plan
from test_resume_guidance import documented_detached_plan


def reviewed_plan(path: Path, depth: str, form: str, review: str) -> Path:
    if form == "physical":
        plan = documented_detached_plan(path, depth)
        (plan / "reviews/codex.md").write_text(review, encoding="utf-8")
    else:
        plan = make_plan(path, depth)
        section = plan / "sections" / f"{SECTION}.md"
        before, rest = section.read_text().split("## Review\n", 1)
        _, after = rest.split("## Acceptance", 1)
        section.write_text(before + "## Review\n" + review + "\n## Acceptance" + after, encoding="utf-8")
    return plan


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("form", ["physical", "compact"])
@pytest.mark.parametrize("review", [
    "Verdict: blocked\nReviewed: REQ-001 has an unresolved data loss bug.\n",
    "Verdict: pass\nReviewed: REQ-001 edge cases.\nVerdict: blocked\n",
    "Verdict: pass\nReviewed: REQ-001 validation and rollback.\nBLOCKED: unresolved data loss.\n",
    "Verdict: pass / blocked\nReviewed: REQ-001 validation and rollback.\n",
    "Verdict: pass\nReviewed: REQ-001 validation.\n```text\nVerdict: blocked\n",
    "PASS: verified REQ-001.\n- BLOCKED: unresolved data loss.\n",
    "No blocking findings.\n+ **BLOCKED:** unresolved data loss.\n",
    "Verdict: pass\n",
    "Verdict: pass\nReviewed: pending\n",
    "```text\nVerdict: pass\nReviewed: REQ-001 edge cases.\n```\n",
])
def test_unresolved_review_blocks_planning_and_implementation(tmp_path, depth, form, review):
    plan = reviewed_plan(tmp_path / "plan", depth, form, review)
    for command in (
        ("postflight", "--phase", "plan", "--planning-dir", str(plan), "--strict"),
        ("lint-plan-artifacts", "--planning-dir", str(plan), "--strict"),
        ("implement-record-section", "--sections-dir", str(plan / "sections"), "--section", SECTION,
         "--review-status", "pass", "--verification", "pytest -q", "--flight", "off"),
    ):
        result = run_raw(*command)
        assert result.returncode == 1, result.stdout + result.stderr
        assert not json.loads(result.stdout)["success"]
    status = run_cmd("status", "--path", str(plan))
    assert status["admission"]["success"] is False
    assert status["next_action"] == "repair planning admission findings before implementation"


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("review", [
    "No blocking findings.\n",
    "No material findings. Reviewed the contract and edge cases.\n",
    "Reviewed: REQ-001 validation and rollback.\nNo blocking findings.\n",
    "PASS: no material findings.\n",
    "PASS — semantic checks, TDD, ownership, traceability, and rollback remain required.\n",
    "Verdict: pass; no unresolved findings.\nReviewed: REQ-001 contract and edge cases.\n",
    "Verdict: pass after fixes.\n- Fixed: preserve REQ-001 caller compatibility.\n",
    "Verdict: fixed\nReviewed: REQ-001, ownership, tests, and rollback.\n",
    "Verdict: pass\nReviewed: REQ-001 validation.\n```text\nVerdict: blocked\n```\n",
])
def test_legacy_physical_review_forms_remain_admitted(tmp_path, depth, review):
    plan = reviewed_plan(tmp_path / "plan", depth, "physical", review)
    assert run_cmd("postflight", "--phase", "plan", "--planning-dir", str(plan), "--strict")["success"]


@pytest.mark.parametrize("review,valid", [
    ("Verdict: pass; no unresolved findings.\nReviewed: REQ-001 validation and rollback.\n", True),
    ("Verdict: pass\nReviewed: REQ-001 validation.\n```text\nVerdict: blocked\n```\n", True),
    ("PASS: verified REQ-001.\nReviewed: REQ-001 validation and rollback.\n", False),
])
def test_compact_review_retains_structured_verdict_requirement(tmp_path, review, valid):
    plan = reviewed_plan(tmp_path / "plan", "lean", "compact", review)
    result = run_raw("lint-plan-artifacts", "--planning-dir", str(plan), "--strict")
    assert (result.returncode == 0) is valid, result.stdout


def two_sections(path: Path, reference=lambda name: name, dependent: str | None = None) -> Path:
    plan = documented_detached_plan(path, "lean")
    second = "section-02-consumer"
    index = plan / "sections/index.md"
    index.write_text(index.read_text().replace("END_MANIFEST", second + "\nEND_MANIFEST") +
                     "\n| Section | Depends on |\n| --- | --- |\n" +
                     f"| {reference(SECTION)} | none |\n| {dependent or reference(second)} | {reference(SECTION)} |\n")
    (plan / "sections" / f"{second}.md").write_text((plan / "sections" / f"{SECTION}.md").read_text().replace(SECTION, second))
    return plan


@pytest.mark.parametrize("reference", [
    lambda name: name,
    lambda name: f"`{name}`",
    lambda name: f"**{name}**",
    lambda name: f"[{name}]({name}.md)",
])
@pytest.mark.parametrize("header", [
    "Depends on", "Depends on (blocking)", "Dependencies (required first)",
    "Predecessor sections", "After", "Requires",
])
def test_markdown_dependency_references_preserve_execution_order(tmp_path, reference, header):
    plan = two_sections(tmp_path / "plan", reference)
    index = plan / "sections/index.md"
    index.write_text(index.read_text().replace("| Section | Depends on |", f"| Section | {header} |"))
    assert run_cmd("lint-plan-artifacts", "--planning-dir", str(plan), "--strict")["success"]
    assert run_cmd("next-section", "--planning-dir", str(plan))["ready_sections"] == [SECTION]
    record = ("implement-record-section", "--sections-dir", str(plan / "sections"),
              "--review-status", "pass", "--verification", "pytest -q", "--flight", "off")
    premature = run_raw(*record, "--section", "section-02-consumer")
    assert premature.returncode == 1
    assert json.loads(premature.stdout)["incomplete_predecessors"] == [SECTION]
    run_cmd(*record, "--section", SECTION)
    assert run_cmd("next-section", "--planning-dir", str(plan))["ready_sections"] == ["section-02-consumer"]
    assert run_cmd(*record, "--section", "section-02-consumer")["remaining_sections"] == []


def test_distinct_incoming_and_outgoing_dependency_columns_remain_supported(tmp_path):
    plan = two_sections(tmp_path / "plan")
    index = plan / "sections/index.md"
    index.write_text(index.read_text()
                     .replace("| Section | Depends on |", "| Section | Depends On | Blocks |")
                     .replace("| --- | --- |", "| --- | --- | --- |")
                     .replace(f"| {SECTION} | none |", f"| {SECTION} | none | section-02-consumer |")
                     .replace(f"| section-02-consumer | {SECTION} |", f"| section-02-consumer | {SECTION} | none |"))
    assert run_cmd("lint-plan-artifacts", "--planning-dir", str(plan), "--strict")["success"]
    assert run_cmd("next-section", "--planning-dir", str(plan))["ready_sections"] == [SECTION]
    result = run_raw("implement-record-section", "--sections-dir", str(plan / "sections"),
                     "--section", "section-02-consumer", "--review-status", "pass",
                     "--verification", "pytest -q", "--flight", "off")
    assert result.returncode == 1
    assert json.loads(result.stdout)["incomplete_predecessors"] == [SECTION]


@pytest.mark.parametrize("dependent", [
    f"`{SECTION}` / `section-02-consumer`", "section-99-unknown", "section-2-consumer",
])
def test_ambiguous_dependency_rows_fail_closed(tmp_path, dependent):
    plan = two_sections(tmp_path / "plan", dependent=dependent)
    for command in (
        ("next-section", "--planning-dir", str(plan)),
        ("lint-plan-artifacts", "--planning-dir", str(plan), "--strict"),
        ("implement-record-section", "--sections-dir", str(plan / "sections"), "--section", SECTION,
         "--review-status", "pass", "--verification", "pytest -q", "--flight", "off"),
    ):
        result = run_raw(*command)
        assert result.returncode == 1
        assert "Dependency row must name exactly one known section" in result.stdout


@pytest.mark.parametrize("mutation,error", [
    ("unclosed-fence", "Unclosed Markdown fence"),
    ("Depends", "Ambiguous dependency table header"),
    ("Blocks", "Ambiguous dependency table header"),
    ("Depends on / Blocks", "Ambiguous dependency table header"),
    ("Depends on | Predecessors", "Ambiguous dependency table header"),
])
def test_incomplete_or_ambiguous_dependency_structure_fails_closed(tmp_path, mutation, error):
    plan = two_sections(tmp_path / "plan")
    index = plan / "sections/index.md"
    header = "```markdown\n| Section | Depends on |" if mutation == "unclosed-fence" else f"| Section | {mutation} |"
    index.write_text(index.read_text().replace("| Section | Depends on |", header))
    for command in (
        ("next-section", "--planning-dir", str(plan)),
        ("lint-plan-artifacts", "--planning-dir", str(plan), "--strict"),
        ("implement-record-section", "--sections-dir", str(plan / "sections"), "--section", "section-02-consumer",
         "--review-status", "pass", "--verification", "pytest -q", "--flight", "off"),
    ):
        result = run_raw(*command)
        assert result.returncode == 1, result.stdout
        assert error in result.stdout


@pytest.mark.parametrize("header", ["Purpose", "Notes"])
def test_dependency_examples_and_non_dependency_tables_do_not_add_edges(tmp_path, header):
    plan = two_sections(tmp_path / "plan")
    index = plan / "sections/index.md"
    index.write_text(index.read_text() +
                     f"\n```markdown\n{SECTION} depends on section-02-consumer\n```\n" +
                     f"\n| Section | {header} |\n| --- | --- |\n| {SECTION} | Normalize labels |\n")
    assert run_cmd("next-section", "--planning-dir", str(plan))["ready_sections"] == [SECTION]


def test_valid_reference_does_not_hide_malformed_predecessor(tmp_path):
    plan = two_sections(tmp_path / "plan")
    index = plan / "sections/index.md"
    index.write_text(index.read_text().replace(f"| section-02-consumer | {SECTION} |",
                                              f"| section-02-consumer | {SECTION}, section-3-validation |"))
    result = run_raw("next-section", "--planning-dir", str(plan))
    assert result.returncode == 1
    assert "Malformed predecessor reference" in result.stdout


@pytest.mark.parametrize("fence", ["```", "~~~~"])
@pytest.mark.parametrize("artifact", ["spec.md", "codex-plan.md", "reviews/codex.md"])
def test_fenced_literals_are_not_authoring_placeholders(tmp_path, fence, artifact):
    plan = documented_detached_plan(tmp_path / "plan", "lean")
    path = plan / artifact
    path.write_text(path.read_text() + f"\nRequired literal example:\n{fence}text\nTODO\n| label | TBD |\n{fence}\n")
    assert run_cmd("lint-plan-artifacts", "--planning-dir", str(plan), "--strict")["success"]


@pytest.mark.parametrize("placeholder", ["TODO", "| Contract | TBD |", "[TODO: complete the contract]", "```text\nTODO"])
def test_unfinished_authoring_remains_blocked(tmp_path, placeholder):
    plan = documented_detached_plan(tmp_path / "plan", "lean")
    source = plan / "spec.md"
    source.write_text(source.read_text() + "\n" + placeholder + "\n")
    result = run_raw("lint-plan-artifacts", "--planning-dir", str(plan), "--strict")
    assert result.returncode == 1
    assert "placeholder-source_spec" in result.stdout


@pytest.mark.parametrize("slug,valid", [("invoice-api", True), ("frontend-accessibility", True), ("cleanup", False), ("misc", False)])
def test_compound_capability_names_are_not_generic(tmp_path, slug, valid):
    plan = documented_detached_plan(tmp_path / "plan", "lean")
    section = f"section-01-{slug}"
    for path in plan.rglob("*.md"):
        path.write_text(path.read_text().replace(SECTION, section))
    (plan / "sections" / f"{SECTION}.md").rename(plan / "sections" / f"{section}.md")
    result = run_raw("lint-sections", "--planning-dir", str(plan), "--strict")
    assert (result.returncode == 0) is valid, result.stdout
    assert ("vague-section-name" not in result.stdout) is valid
