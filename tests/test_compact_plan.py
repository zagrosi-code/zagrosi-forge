from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SECTION = "section-01-normalize"


@pytest.fixture
def forge():
    from runtime_support import load_runtime
    return load_runtime(ROOT / "scripts/zagrosi_skills.py")


def make_plan(path: Path, depth: str = "lean") -> Path:
    (path / "sections").mkdir(parents=True)
    (path / "spec.md").write_text("REQ-001: Strip label edge whitespace; preserve internal whitespace and case.\n")
    marker = {"artifact_type": "compact_plan", "depth_mode": depth, "source": "spec.md"}
    (path / "sections/index.md").write_text(
        "<!-- FORGE_META\n" + json.dumps(marker) + "\nEND_FORGE_META -->\n"
        "<!-- PROJECT_CONFIG\nruntime: python-uv\ntest_command: uv run pytest tests/test_labels.py\nEND_PROJECT_CONFIG -->\n"
        f"<!-- SECTION_MANIFEST\n{SECTION}\nEND_MANIFEST -->\n"
        "Dependencies: none. Execution order: one section. Parallel: no siblings.\n"
    )
    (path / "sections" / f"{SECTION}.md").write_text(
        f"# {SECTION}\n\n## Goal\nREQ-001 strips label edges. Non-goals: case changes or new input types.\n\n"
        "## Dependencies\nNone.\n\n## Owned files\nThis section owns exactly these files:\n\n"
        "```text\nsrc/labels.py\ntests/test_labels.py\n```\n\n"
        "## Tests first\nREQ-001: `test_trim_edges` expects `\" Ada  Lovelace \" -> \"Ada  Lovelace\"`. "
        "Expected failure: current helper preserves edges. Run `uv run pytest tests/test_labels.py`.\n\n"
        "## Implementation contract\nModify `normalize(value: str) -> str` to return `value.strip()`. "
        "Empty input returns empty; repeated normalization is idempotent.\n\n"
        "## Evidence\nVerified `src/labels.py`, existing tests in `tests/test_labels.py`, and runtime `pyproject.toml` "
        "with `rg --files`. Assumption: callers supply strings.\n\n"
        "## Decisions\nDEC-001: reuse the function. Rationale: its ownership fits; reject a new abstraction.\n\n"
        "## Risks\nRISK-001: case drift; test exact result. Security/privacy: no I/O. Migration: none. "
        "Rollback: revert this section's files.\n\n"
        "## Review\nVerdict: pass\nReviewed: REQ-001 contract, edge cases, ownership, tests, and rollback; no material findings.\n\n"
        "## Acceptance\nDone when the targeted command passes and edge whitespace is removed without case changes.\n"
    )
    return path


def invoke(forge, capsys, *args: str) -> tuple[int, dict]:
    code = forge.main(list(args))
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("depth", ["lean", "fast", "standard", "deep"])
def test_two_file_plan_passes_semantic_gates_at_each_depth(forge, tmp_path, capsys, depth):
    planning = make_plan(tmp_path / "planning", depth)
    initial_files = {p.relative_to(planning) for p in planning.rglob("*") if p.is_file()}
    source = (planning / "spec.md").read_bytes()
    for command in (
        "lint-plan", "lint-plan-artifacts", "lint-sections", "traceability",
        "lint-implementation-readiness", "lint-artifact-schema", "lint-review-integration",
    ):
        code, payload = invoke(forge, capsys, command, "--planning-dir", str(planning), "--strict")
        assert code == 0, (command, payload)
        assert payload["success"] is True
    assert forge.planning_depth(planning) == depth
    assert forge.implementation_plan_path(planning) == planning / "sections" / f"{SECTION}.md"
    assert (planning / "spec.md").read_bytes() == source
    assert {p.relative_to(planning) for p in planning.rglob("*") if p.is_file()} == initial_files


@pytest.mark.parametrize("mutation", ["no-marker", "empty-legacy", "two-sections", "bad-depth", "malformed-depth", "source-is-section", "source-is-index", "missing-review", "blocked-review", "missing-tests", "missing-contract", "missing-owned-files"])
def test_malformed_compact_shape_cannot_admit_implementation(forge, tmp_path, capsys, mutation):
    planning = make_plan(tmp_path / "planning")
    index = planning / "sections/index.md"
    section = planning / "sections" / f"{SECTION}.md"
    if mutation == "no-marker":
        index.write_text(index.read_text().split("END_FORGE_META -->\n", 1)[1])
    elif mutation == "empty-legacy":
        (planning / "codex-plan.md").write_text("")
    elif mutation == "two-sections":
        index.write_text(index.read_text().replace("END_MANIFEST", "section-02-other\nEND_MANIFEST"))
        (planning / "sections/section-02-other.md").write_text("REQ-001: Other work.\n")
    elif mutation == "bad-depth":
        index.write_text(index.read_text().replace('"lean"', '"unknown"'))
    elif mutation == "malformed-depth":
        index.write_text(index.read_text().replace('"lean"', '["lean"]'))
    elif mutation.startswith("source-is-"):
        source = f"sections/{SECTION}.md" if mutation.endswith("section") else "sections/index.md"
        index.write_text(index.read_text().replace('"spec.md"', json.dumps(source)))
    elif mutation == "blocked-review":
        section.write_text(section.read_text().replace("Verdict: pass", "Verdict: blocked"))
    else:
        heading = {"missing-review": "Review", "missing-tests": "Tests first", "missing-contract": "Implementation contract", "missing-owned-files": "Owned files"}[mutation]
        before, body = section.read_text().split(f"## {heading}\n", 1)
        rest = body.split("\n## ", 1)
        section.write_text(before + ("## " + rest[1] if len(rest) == 2 else ""))
    code, payload = invoke(forge, capsys, "lint-plan-artifacts", "--planning-dir", str(planning), "--strict")
    assert code != 0, payload
    assert payload["success"] is False


def test_embedded_review_requires_real_scope_and_unambiguous_heading(forge, tmp_path, capsys):
    planning = make_plan(tmp_path / "planning")
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace("Reviewed:", "Unreviewed:") + "\n## Review\nVerdict: pass\n")
    code, payload = invoke(forge, capsys, "lint-plan", "--planning-dir", str(planning), "--strict")
    assert code == 1
    assert not payload["success"]


def test_packet_and_context_do_not_duplicate_the_canonical_section(forge, tmp_path, capsys):
    planning = make_plan(tmp_path / "planning")
    for command in ("context-brief", "implementation-packet"):
        code, payload = invoke(forge, capsys, command, "--planning-dir", str(planning), "--section", SECTION)
        assert code == 0, payload
        content = payload.get("content") or Path(payload["output"]).read_text()
        assert content.count(f"# {SECTION}") == 1
        assert content.count("DEC-001:") == 1
    assert payload["requirements"] == ["REQ-001"]
    assert payload["files"] == ["src/labels.py", "tests/test_labels.py"]
    assert payload["tests"] == ["test_trim_edges"]


def test_compact_views_preserve_physical_multisection_plans(forge, tmp_path, capsys):
    planning = make_plan(tmp_path / "planning", "deep")
    index = planning / "sections/index.md"
    marker, rest = index.read_text().split("END_FORGE_META -->\n", 1)
    section = planning / "sections" / f"{SECTION}.md"
    (planning / "codex-plan.md").write_text(marker + "END_FORGE_META -->\n" + section.read_text())
    index.write_text(rest.replace("END_MANIFEST", "section-02-other\nEND_MANIFEST"))
    (planning / "sections/section-02-other.md").write_text(section.read_text())
    code, payload = invoke(forge, capsys, "lint-plan-artifacts", "--planning-dir", str(planning), "--strict")
    assert code == 0, payload
    assert forge.implementation_plan_path(planning) == planning / "codex-plan.md"
    assert forge.planning_artifact_text(planning, "review").startswith("Verdict: pass")


def test_detached_artifact_gate_does_not_admit_the_new_compact_shape(forge, tmp_path):
    planning = make_plan(tmp_path / "planning")
    payload = forge.plan_artifacts_payload(planning, SimpleNamespace(profile="solo", strict=True, allow_compact=False))
    assert not payload["success"]
    assert "compact-plan-not-supported" in {item["code"] for item in payload["findings"]}


@pytest.mark.parametrize("source", ["/tmp/spec.md", "missing.md", "other-plan.md"])
def test_compact_source_must_be_an_existing_relative_spec(forge, tmp_path, capsys, source):
    planning = make_plan(tmp_path / "planning")
    index = planning / "sections/index.md"
    (planning / "other-plan.md").write_text(index.read_text())
    index.write_text(index.read_text().replace('"spec.md"', json.dumps(source)))
    code, payload = invoke(forge, capsys, "lint-plan-artifacts", "--planning-dir", str(planning), "--strict")
    assert code == 1, payload
    assert "invalid-compact-plan" in {item["code"] for item in payload["findings"]}


def test_explicit_compact_source_wins_over_stale_normalized_copy(forge, tmp_path, capsys):
    planning = make_plan(tmp_path / "planning")
    (planning / "codex-spec.md").write_text("REQ-999: Obsolete normalized requirements.\n")
    code, payload = invoke(forge, capsys, "traceability", "--planning-dir", str(planning), "--strict")
    assert code == 0, payload
    assert forge.requirement_source_spec(planning) == planning / "spec.md"
    assert "REQ-999" not in payload["coverage"]


def test_compact_depth_cannot_be_downgraded_by_lint_flag(forge, tmp_path, capsys):
    planning = make_plan(tmp_path / "planning", "deep")
    for command in ("lint-plan", "lint-sections"):
        code, payload = invoke(forge, capsys, command, "--planning-dir", str(planning), "--depth", "lean", "--strict")
        assert code == 1, payload
        assert "compact-depth-mismatch" in {item["code"] for item in payload["findings"]}


def test_fenced_review_example_cannot_count_as_review(forge, tmp_path, capsys):
    planning = make_plan(tmp_path / "planning")
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace("## Review\n", "## Review\n```text\n").replace("\n## Acceptance", "\n```\n\n## Acceptance"))
    for command in ("lint-plan-artifacts", "context-brief"):
        code, payload = invoke(forge, capsys, command, "--planning-dir", str(planning))
        assert code == 1, payload
        assert not payload["success"]


def test_context_budget_counts_canonical_section_once(forge, tmp_path, capsys):
    planning = make_plan(tmp_path / "planning")
    code, payload = invoke(forge, capsys, "context-budget", "--planning-dir", str(planning))
    assert code == 0, payload
    expected = sum(forge.word_count(path.read_text()) for path in (planning / "sections").glob("*.md"))
    assert payload["total_words"] == expected


def test_optional_physical_governance_artifacts_keep_their_schema(forge, tmp_path, capsys):
    planning = make_plan(tmp_path / "planning")
    (planning / "decisions.md").write_text("# Decisions\n\nDEC-001: No table.\n")
    code, payload = invoke(forge, capsys, "lint-artifact-schema", "--planning-dir", str(planning), "--strict")
    assert code == 1, payload
    assert "invalid-decisions-table" in {item["code"] for item in payload["findings"]}


def test_full_context_does_not_repeat_embedded_artifacts(forge, tmp_path, capsys):
    planning = make_plan(tmp_path / "planning", "deep")
    code, payload = invoke(forge, capsys, "context-brief", "--planning-dir", str(planning), "--lines", "1000")
    assert code == 0, payload
    assert payload["content"].count("DEC-001:") == 1
    assert payload["content"].count("test_trim_edges") == 1


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_only_canonical_section_receives_the_plan_word_budget(forge, tmp_path, capsys, depth):
    planning = make_plan(tmp_path / "planning", depth)
    section = planning / "sections" / f"{SECTION}.md"
    budgets = forge.word_budgets(depth)
    text = section.read_text()
    case = 1
    while forge.word_count(text) <= budgets["section"]:
        text += f"\nREQ-001 fixture {case}: preserve exactly {case} internal spaces while removing edge whitespace.\n"
        case += 1
    assert budgets["section"] < forge.word_count(text) < budgets["plan"]
    section.write_text(text)
    code, payload = invoke(forge, capsys, "lint-sections", "--planning-dir", str(planning), "--strict")
    assert code == 0, payload
    assert "section-too-large" not in {item["code"] for item in payload["findings"]}

    index = planning / "sections/index.md"
    marker, rest = index.read_text().split("END_FORGE_META -->\n", 1)
    (planning / "codex-plan.md").write_text(marker + "END_FORGE_META -->\n" + text)
    index.write_text(rest)
    code, payload = invoke(forge, capsys, "lint-sections", "--planning-dir", str(planning), "--strict")
    assert code == 1, payload
    assert "section-too-large" in {item["code"] for item in payload["findings"]}


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_canonical_section_prompts_include_budget_format_and_engineering(forge, tmp_path, capsys, depth):
    planning = make_plan(tmp_path / "planning", depth)
    code, payload = invoke(forge, capsys, "plan-generate-section-prompts", "--planning-dir", str(planning), "--all")
    assert code == 0, payload
    text = Path(payload["prompt_files"][0]).read_text()
    assert f'{forge.word_budgets(depth)["plan"]} words max' in text
    assert str(ROOT / "skills/zagrosi-implement/references/engineering.md") in text
    assert str(ROOT / "skills/zagrosi-plan/references/section-format.md") in text
    assert all(f"## {heading}" in text for heading in ("Evidence", "Decisions", "Review"))
    assert "Reviewed:" in text
    assert forge.word_count(text) <= 300


def test_agent_prompts_link_the_engineering_standard(forge, tmp_path, capsys):
    code, payload = invoke(forge, capsys, "agent-prompts", "--planning-dir", str(tmp_path), "--type", "all")
    assert code == 0, payload
    for path in payload["prompt_files"]:
        text = Path(path).read_text()
        assert str(ROOT / "skills/zagrosi-implement/references/engineering.md") in text
        assert forge.word_count(text) <= 300


@pytest.mark.parametrize("source_name", ["spec.md", "custom-source.md"])
def test_first_canonical_section_prompt_uses_selected_path_before_file_exists(forge, tmp_path, capsys, source_name):
    planning = make_plan(tmp_path / "planning")
    if source_name != "spec.md":
        (planning / "spec.md").rename(planning / source_name)
        index = planning / "sections/index.md"
        index.write_text(index.read_text().replace('"spec.md"', json.dumps(source_name)))
    section = planning / "sections" / f"{SECTION}.md"
    section.unlink()
    code, payload = invoke(forge, capsys, "plan-generate-section-prompts", "--planning-dir", str(planning))
    assert code == 0, payload
    text = Path(payload["prompt_files"][0]).read_text()
    assert "800 words max" in text
    assert str(section) in text
    assert str(planning / source_name) in text
    assert "codex-plan.md" not in text
