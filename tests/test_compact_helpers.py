"""Auxiliary commands follow the same canonical source and plan as admission."""

from __future__ import annotations

import json
import re

import pytest

from runtime_support import load_runtime
from test_compact_plan import ROOT, SECTION, make_plan


@pytest.fixture
def forge():
    return load_runtime(ROOT / "scripts/zagrosi_skills.py")


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_consistency_checks_custom_source_and_canonical_section(forge, tmp_path, capsys, depth):
    planning = make_plan(tmp_path / "planning", depth)
    source = planning / "source-requirements.md"
    (planning / "spec.md").rename(source)
    index = planning / "sections/index.md"
    index.write_text(index.read_text().replace('"source": "spec.md"', '"source": "source-requirements.md"'))
    command = ["planning-consistency", "--planning-dir", str(planning), "--strict"]
    assert forge.main(command) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == str(source)
    assert f"sections/{SECTION}.md" in payload["checked_artifacts"]

    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace("REQ-001", "REQ-002"))
    assert forge.main(command) == 1
    payload = json.loads(capsys.readouterr().out)
    assert "missing-requirement-reference" in {item["code"] for item in payload["findings"]}


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_generated_traceability_points_to_real_canonical_plan_and_tests(forge, tmp_path, depth):
    planning = make_plan(tmp_path / "planning", depth)
    content = forge.traceability_matrix_content(planning)
    row = next(line for line in content.splitlines() if line.startswith("| REQ-001 |"))
    cells = row.strip("|").split("|")
    for cell in (cells[1], cells[3]):
        references = re.findall(r"`([^`]+)`", cell)
        assert references
        assert all((planning / reference).is_file() for reference in references)
    assert "codex-plan.md" not in row
    assert "codex-plan-tdd.md" not in row
