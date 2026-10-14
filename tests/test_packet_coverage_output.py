"""Readable packet failures retain the producer's existing coverage details."""

import copy
import json

import pytest

from forge_test_helpers import ROOT, write_lean_plan_fixture
from runtime_support import load_runtime


ERROR = "Selected section has incomplete requirement coverage."


@pytest.fixture
def forge():
    return load_runtime(ROOT / "scripts/zagrosi_skills.py")


def invoke(forge, capsys, *args):
    code = forge.entrypoint.main(list(args))
    captured = capsys.readouterr()
    assert not captured.err, captured.err
    return code, captured.out


@pytest.mark.parametrize("full_output", [False, True])
def test_packet_coverage_failure_is_actionable_in_readable_cli(forge, tmp_path, capsys, full_output):
    planning = write_lean_plan_fixture(tmp_path / "plan with spaces")
    (planning / "spec.md").write_text("REQ-002: Unrelated source requirement.\n")
    before = {path.relative_to(planning): path.read_bytes() for path in planning.rglob("*") if path.is_file()}
    output = tmp_path / "never written"
    args = ["implementation-packet", "--planning-dir", str(planning),
            "--section", "section-01-lean-default", "--output-dir", str(output)]
    if full_output:
        args.append("--full-output")

    code, raw = invoke(forge, capsys, *args)
    assert code == 1
    assert json.loads(raw) == {"success": False, "error": ERROR, "coverage_gaps": {"REQ-001": ["spec"]}}

    pretty_code, pretty = invoke(forge, capsys, *args, "--pretty")
    assert pretty_code == code
    assert "Status: FAIL" in pretty and pretty.count(ERROR) == 1
    assert pretty.count("REQ-001") == 1
    assert any("REQ-001" in line and "spec" in line for line in pretty.splitlines())
    assert "REQ-002" not in pretty
    assert "retry_context" not in pretty and "--max-words" not in pretty
    assert not output.exists()
    assert {path.relative_to(planning): path.read_bytes() for path in planning.rglob("*") if path.is_file()} == before


def test_readable_coverage_keeps_each_requirement_with_its_missing_artifacts(forge):
    payload = {"success": False, "error": ERROR,
               "coverage_gaps": {"REQ-002": ["tdd"], "REQ-001": ["spec", "plan"]}}
    before = copy.deepcopy(payload)
    pretty = forge.output.format_pretty(payload)

    assert pretty.count(ERROR) == 1
    for requirement, missing, other in (("REQ-001", ("spec", "plan"), ("tdd", "REQ-002")),
                                        ("REQ-002", ("tdd",), ("spec", "plan", "REQ-001"))):
        rows = [line for line in pretty.splitlines() if requirement in line]
        assert len(rows) == 1
        assert all(artifact in rows[0] for artifact in missing)
        assert all(artifact not in rows[0] for artifact in other)
    assert payload == before


def test_empty_coverage_map_preserves_failure_without_inventing_details(forge):
    # The producer also uses an empty map when the selected section has no IDs.
    payload = {"success": False, "error": ERROR, "coverage_gaps": {}}
    assert forge.output.format_pretty(payload) == f"ZAGROSI FORGE\nStatus: FAIL\nError: {ERROR}"
    assert payload == {"success": False, "error": ERROR, "coverage_gaps": {}}
