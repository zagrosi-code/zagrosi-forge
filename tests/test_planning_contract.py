"""Compact contracts bind unchanged briefs without admitting unfinished scaffolds."""
from pathlib import Path

import pytest

from test_compact_plan import SECTION, forge as forge, invoke, make_plan


CONTRACT = """## Contract
### REQ-001
Source: spec.md#L1
Behavior: Normalize label edges using the existing helper; preserve input types and errors.
Expected: `test_trim_edges` returns `Ada Lovelace` from ` Ada Lovelace `; internal spaces and case remain unchanged.
Command: uv run pytest tests/test_labels.py

## Owned files
This section owns exactly these files:
- src/labels.py
- tests/test_labels.py

## Evidence
Verified src/labels.py and existing tests/test_labels.py with rg --files. Runtime: pyproject.toml.
Assumption: callers supply strings. Rationale: reuse the existing function rather than add an abstraction.
Compatibility risk: case drift; assert exact output. Security/privacy: no I/O. Rollback: revert these files.

## Review
Verdict: pass
Reviewed: REQ-001 source mapping, behavior, errors, ownership, verification and rollback; no material findings.
"""


def contract_plan(path: Path, depth="lean"):
    planning = make_plan(path, depth)
    (planning / "spec.md").write_text("Strip label edges; preserve internal whitespace and case.\n")
    (planning / "sections" / f"{SECTION}.md").write_text(CONTRACT)
    return planning


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_small_contract_passes_all_gates_without_rewriting_brief(forge, tmp_path, capsys, depth):
    planning = contract_plan(tmp_path / "plan", depth)
    source = (planning / "spec.md").read_bytes()
    for gate in ("lint-plan", "lint-sections", "lint-plan-artifacts", "traceability", "lint-implementation-readiness", "lint-evidence"):
        code, payload = invoke(forge, capsys, gate, "--planning-dir", str(planning), "--strict")
        assert code == 0, (gate, payload)
    assert (planning / "spec.md").read_bytes() == source


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_packet_uses_valid_source_mapping_without_rewriting_brief(forge, tmp_path, capsys, depth):
    planning = contract_plan(tmp_path / "plan", depth)
    source = (planning / "spec.md").read_bytes()
    code, payload = invoke(forge, capsys, "implementation-packet", "--planning-dir", str(planning), "--section", SECTION)
    assert code == 0, payload
    assert payload["requirements"] == ["REQ-001"]
    content = Path(payload["output"]).read_text()
    assert "Source: spec.md#L1" in content
    assert str(planning / "spec.md") in content
    assert (planning / "spec.md").read_bytes() == source


@pytest.mark.parametrize("mutation", ["hidden-source", "fenced-contract", "invalid-line", "explicit-id"])
def test_packet_rejects_invalid_source_mapping_without_writing(forge, tmp_path, capsys, mutation):
    planning = contract_plan(tmp_path / "plan")
    source = planning / "spec.md"
    section = planning / "sections" / f"{SECTION}.md"
    if mutation == "hidden-source":
        source.write_text("<!-- REQ-001: Strip label edges. -->\n")
    elif mutation == "fenced-contract":
        section.write_text(CONTRACT.replace("### REQ-001", "```text\n### REQ-001").replace("## Owned files", "```\n## Owned files"))
    elif mutation == "invalid-line":
        section.write_text(CONTRACT.replace("#L1", "#L99"))
    else:
        source.write_text("REQ-OTHER: Strip label edges.\n")
    code, payload = invoke(forge, capsys, "implementation-packet", "--planning-dir", str(planning), "--section", SECTION)
    assert code == 1, payload
    assert not (planning / ".forge/packets").exists()


@pytest.mark.parametrize("mutation", ["missing-source", "wrong-source", "invalid-line", "hidden-source", "uncovered-source", "hidden-contract", "fenced-contract", "duplicate-id", "missing-expected", "unfinished", "explicit-id", "missing-owned", "blocked-review"])
def test_invalid_mapping_cannot_admit(forge, tmp_path, capsys, mutation):
    planning = contract_plan(tmp_path / "plan")
    section = planning / "sections" / f"{SECTION}.md"
    text = section.read_text()
    source = planning / "spec.md"
    if mutation == "missing-source":
        text = text.replace("Source: spec.md#L1\n", "")
    elif mutation == "wrong-source":
        (planning / "other.md").write_text(source.read_text())
        text = text.replace("Source: spec.md", "Source: other.md")
    elif mutation == "invalid-line":
        text = text.replace("#L1", "#L2-L999")
    elif mutation == "hidden-source":
        source.write_text("<!-- Hidden implementation permission -->\n")
    elif mutation == "uncovered-source":
        source.write_text(source.read_text() + "Preserve empty input behavior.\n")
    elif mutation == "hidden-contract":
        text = text.replace("## Contract", "<!--\n## Contract").replace("## Owned files", "-->\n## Owned files")
    elif mutation == "fenced-contract":
        text = text.replace("### REQ-001", "```text\n### REQ-001").replace("## Owned files", "```\n## Owned files")
    elif mutation == "duplicate-id":
        text = text.replace("## Owned files", text.split("## Owned files")[0].replace("## Contract\n", "") + "## Owned files")
    elif mutation == "missing-expected":
        text = "\n".join(line for line in text.splitlines() if not line.startswith("Expected:"))
    elif mutation == "unfinished":
        text = text.replace("Behavior: Normalize label edges using the existing helper; preserve input types and errors.", "Behavior: TODO")
    elif mutation == "explicit-id":
        source.write_text("REQ-OTHER: " + source.read_text())
    elif mutation == "missing-owned":
        text = text.replace("## Owned files", "## References")
    elif mutation == "blocked-review":
        text = text.replace("Verdict: pass", "Verdict: blocked")
    section.write_text(text)
    for gate in ("lint-plan-artifacts", "traceability"):
        code, payload = invoke(forge, capsys, gate, "--planning-dir", str(planning), "--strict")
        assert code == 1, (mutation, gate, payload)


def test_source_code_example_ids_do_not_become_requirements(forge, tmp_path, capsys):
    planning = contract_plan(tmp_path / "plan")
    source = planning / "spec.md"
    source.write_text(source.read_text() + "```python\nexample = 'REQ-FAKE'\n```\n<!-- REQ-HIDDEN -->\n")
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace("#L1", "#L1-L5"))
    code, payload = invoke(forge, capsys, "traceability", "--planning-dir", str(planning), "--strict")
    assert code == 0, payload
    assert payload["requirement_ids"] == ["REQ-001"]


@pytest.mark.parametrize(("line", "passes"), [(1, True), (2, False), (3, False), (5, False)])
def test_explicit_requirement_link_must_include_its_visible_id(forge, tmp_path, capsys, line, passes):
    planning = contract_plan(tmp_path / "plan")
    (planning / "spec.md").write_text(
        "REQ-001: Strip label edges.\nREQ-002: Preserve errors.\n"
        "Reference <!-- REQ-001 -->\n```text\nREQ-001 is an example.\n```\n"
    )
    first = CONTRACT.split("## Owned files")[0].replace("#L1", f"#L{line}")
    second = first.replace("## Contract\n", "").replace("REQ-001", "REQ-002").replace(f"#L{line}", "#L2")
    (planning / "sections" / f"{SECTION}.md").write_text(first + second + "## Owned files" + CONTRACT.split("## Owned files")[1])
    code, payload = invoke(forge, capsys, "traceability", "--planning-dir", str(planning), "--strict")
    assert code == (0 if passes else 1), payload


def test_requirement_mapping_cannot_skip_heading_constraints(forge, tmp_path, capsys):
    planning = contract_plan(tmp_path / "plan")
    (planning / "spec.md").write_text("## Preserve all existing error behavior\nStrip label edges.\n")
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(CONTRACT.replace("#L1", "#L2"))
    code, payload = invoke(forge, capsys, "traceability", "--planning-dir", str(planning), "--strict")
    assert code == 1, payload
    section.write_text(CONTRACT.replace("#L1", "#L1-L2"))
    code, payload = invoke(forge, capsys, "traceability", "--planning-dir", str(planning), "--strict")
    assert code == 0, payload


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_scaffold_preserves_source_and_stays_unfinished_on_resume(forge, tmp_path, depth):
    source = tmp_path / "brief.md"
    source.write_text("Add preview without changing existing errors.\n")
    original = source.read_bytes()
    first = forge.planning_contract.create_plan_scaffold(source, depth)
    assert len(first["created"]) == 2 and first["unfinished"]
    state = {path: Path(path).read_bytes() for path in first["created"]}
    resumed = forge.planning_contract.create_plan_scaffold(source, depth)
    assert resumed == {"created": [], "unfinished": True}
    assert {path: Path(path).read_bytes() for path in first["created"]} == state
    assert source.read_bytes() == original
    assert forge.artifacts.compact_plan_findings(tmp_path)


@pytest.mark.parametrize("existing", ["codex-plan.md", "claude-plan.md", "sections/index.md"])
def test_scaffold_does_not_overwrite_existing_plans(forge, tmp_path, existing):
    source = tmp_path / "brief.md"
    source.write_text("Original brief")
    path = tmp_path / existing
    path.parent.mkdir(exist_ok=True)
    path.write_text("Existing user artifact")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert forge.planning_contract.create_plan_scaffold(source, "deep") == {"created": [], "unfinished": False}
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_detached_setup_does_not_create_compact_artifacts(forge, tmp_path):
    source = tmp_path / "brief.md"
    source.write_text("Original brief")
    assert forge.planning_contract.create_plan_scaffold(source, "deep", detached=True) == {"created": [], "unfinished": False}
    assert not (tmp_path / "sections").exists()


def test_finished_renamed_scaffold_can_resume_but_incomplete_one_cannot(forge, tmp_path):
    source = tmp_path / "brief.md"
    source.write_text("Strip label edges; preserve internal whitespace and case.\n")
    forge.planning_contract.create_plan_scaffold(source, "lean")
    index = tmp_path / "sections/index.md"
    index.write_text(index.read_text().replace("section-01-contract", SECTION).replace("runtime: TODO", "runtime: python").replace("test_command: TODO", "test_command: uv run pytest tests/test_labels.py"))
    original = tmp_path / "sections/section-01-contract.md"
    renamed = original.with_name(SECTION + ".md")
    original.rename(renamed)
    assert forge.planning_contract.create_plan_scaffold(source, "lean")["unfinished"]
    renamed.write_text(CONTRACT.replace("Source: spec.md", "Source: brief.md"))
    assert not forge.planning_contract.create_plan_scaffold(source, "lean")["unfinished"]


def test_new_contract_keeps_detached_and_depth_boundaries(forge, tmp_path, capsys):
    planning = contract_plan(tmp_path / "plan", "deep")
    for gate, options in (("lint-plan-artifacts", ["--for-detached"]), ("lint-plan", ["--depth", "lean"])):
        code, payload = invoke(forge, capsys, gate, "--planning-dir", str(planning), "--strict", *options)
        assert code == 1, payload


def test_source_change_invalidates_requirement_mapping(forge, tmp_path, capsys):
    planning = contract_plan(tmp_path / "plan")
    code, payload = invoke(forge, capsys, "traceability", "--planning-dir", str(planning), "--strict")
    assert code == 0, payload
    source = planning / "spec.md"
    source.write_text(source.read_text() + "Reject non-string inputs without changing their error.\n")
    code, payload = invoke(forge, capsys, "traceability", "--planning-dir", str(planning), "--strict")
    assert code == 1, payload
    assert "invalid-requirement-contract" in {item["code"] for item in payload["findings"]}


@pytest.mark.parametrize("source", [None, "", "<!-- REQ-HIDDEN -->\n"])
def test_legacy_plan_id_fallback_requires_absent_source(forge, tmp_path, source):
    planning = make_plan(tmp_path / "plan")
    (planning / "codex-plan.md").write_text((planning / "sections" / f"{SECTION}.md").read_text() + "\n```text\nREQ-EXAMPLE\n```\n<!-- REQ-HIDDEN -->\n")
    spec = planning / "spec.md"
    if source is None:
        spec.unlink()
    else:
        spec.write_text(source)
    ids, errors = forge.planning_contract.requirements(planning)
    assert ids == (["REQ-001"] if source is None else [])
    assert not errors
