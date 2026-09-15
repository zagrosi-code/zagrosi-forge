from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from runtime_support import load_entrypoint
from test_compact_plan import SECTION, make_plan


@pytest.fixture
def runtime():
    entrypoint = load_entrypoint(Path(__file__).resolve().parents[1] / "scripts/zagrosi_skills.py")
    package = entrypoint.load_runtime()
    return SimpleNamespace(entrypoint=entrypoint, **{
        name: importlib.import_module(f"{package.__name__}.{name}")
        for name in ("flights", "state", "traceability")
    })


def invoke(runtime, capsys, *args):
    result = runtime.entrypoint.main(list(args))
    return result, json.loads(capsys.readouterr().out)


def record_args(planning, *extra):
    return ("implement-record-section", "--sections-dir", str(planning / "sections"),
            "--section", SECTION, *extra)


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("evidence", [
    ("--review-status", "blocked", "--verification", "pytest -q"),
    ("--review-status", "pass"),
    ("--verification", "pytest -q"),
    ("--review-status", "pass", "--verification", "pending"),
])
def test_incomplete_evidence_never_records_completion(runtime, tmp_path, capsys, depth, evidence):
    planning = make_plan(tmp_path / "planning", depth)
    code, payload = invoke(runtime, capsys, *record_args(planning, *evidence, "--flight", "off"))
    assert code == 1 and not payload["success"]
    assert not runtime.state.completed_sections(planning)
    assert not runtime.state.implementation_state_path(planning).exists()


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("flight", ["off", "strict"])
def test_structured_evidence_needs_no_duplicate_review_files(runtime, tmp_path, capsys, depth, flight):
    planning = make_plan(tmp_path / "planning", depth)
    code, payload = invoke(runtime, capsys, *record_args(
        planning, "--review-status", "pass", "--verification", "pytest -q", "--flight", flight))
    assert code == 0 and payload["success"]
    code, payload = invoke(runtime, capsys, "lint-implementation-state", "--sections-dir",
                           str(planning / "sections"), "--strict")
    assert code == 0 and payload["success"], payload


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("legacy_review,passing", [
    ("# Review\nNo blocking findings.\nVerification: `pytest -q` passed (18 tests).\n", True),
    ("", False),
    ("# Review\nVerdict: pass\nVerification: pending\n", False),
    ("```text\nNo blocking findings.\nVerification: `pytest -q` passed.\n```\n", False),
])
def test_legacy_evidence_must_be_substantive(runtime, tmp_path, capsys, depth, legacy_review, passing):
    planning = make_plan(tmp_path / "planning", depth)
    path = runtime.state.implementation_state_path(planning)
    path.parent.mkdir()
    path.write_text(json.dumps({"completed_sections": {SECTION: {"completed_at": "2026-09-15T00:00:00Z"}}}))
    review_dir = path.parent / "code_review"
    review_dir.mkdir()
    (review_dir / f"{SECTION}-review.md").write_text(legacy_review)
    code, payload = invoke(runtime, capsys, "lint-implementation-state", "--sections-dir",
                           str(planning / "sections"), "--strict")
    assert (code == 0) is passing, payload
    assert (SECTION in runtime.state.completed_sections(planning)) is passing


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_failed_postflight_stays_pending_and_can_resume(runtime, tmp_path, capsys, monkeypatch, depth):
    planning = make_plan(tmp_path / "planning", depth)
    original = runtime.flights.implement_postflight_report
    failure = {"success": False, "blocking_gates": ["regression-check"], "gates": []}

    def fail(*args, **kwargs):
        assert SECTION not in runtime.state.completed_sections(planning)
        return failure

    monkeypatch.setattr(runtime.flights, "implement_postflight_report", fail)
    args = record_args(planning, "--review-status", "pass", "--verification", "pytest -q", "--flight", "strict")
    code, payload = invoke(runtime, capsys, *args)
    assert code == 1 and not payload["success"]
    assert payload["postflight"] == failure
    assert payload["next_section"] == SECTION
    state = runtime.state.load_implementation_state(planning)
    assert SECTION not in state["completed_sections"]
    assert state["pending_sections"][SECTION]["verification"] == ["pytest -q"]
    assert state["pending_sections"][SECTION]["failed_postflight"] == failure
    code, payload = invoke(runtime, capsys, *args[:-1], "off")
    assert code == 1 and payload["error_code"] == "pending-completion"
    code, payload = invoke(runtime, capsys, *args[:-1], "auto")
    assert code == 1 and payload["postflight"] == failure

    monkeypatch.setattr(runtime.flights, "implement_postflight_report", original)
    code, payload = invoke(runtime, capsys, "postflight", "--phase", "implement", "--planning-dir", str(planning))
    assert code == 1 and "pending-completion" in payload["blocking_gates"]

    monkeypatch.setattr(runtime.flights, "implement_postflight_report", lambda *a, **k: {"success": True})
    code, payload = invoke(runtime, capsys, *args[:-1], "auto")
    assert code == 0 and payload["success"]
    assert SECTION in runtime.state.completed_sections(planning)
    assert not runtime.state.load_implementation_state(planning).get("pending_sections")


def test_failed_rerecord_keeps_previous_completion(runtime, tmp_path, capsys, monkeypatch):
    planning = make_plan(tmp_path / "planning", "standard")
    args = record_args(planning, "--review-status", "pass", "--verification", "pytest -q")
    invoke(runtime, capsys, *args, "--commit", "previous", "--flight", "off")
    monkeypatch.setattr(runtime.flights, "implement_postflight_report",
                        lambda *a, **k: {"success": False, "blocking_gates": ["regression-check"]})
    code, _ = invoke(runtime, capsys, *args, "--commit", "candidate", "--flight", "strict")
    assert code == 1
    state = runtime.state.load_implementation_state(planning)
    assert state["completed_sections"][SECTION]["commit"] == "previous"
    assert state["pending_sections"][SECTION]["commit"] == "candidate"
    assert SECTION not in runtime.state.completed_sections(planning)


def test_interrupted_postflight_keeps_evidence_pending(runtime, tmp_path, capsys, monkeypatch):
    planning = make_plan(tmp_path / "planning", "standard")

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime.flights, "implement_postflight_report", interrupt)
    with pytest.raises(KeyboardInterrupt):
        invoke(runtime, capsys, *record_args(planning, "--review-status", "pass", "--verification", "pytest -q"))
    state = runtime.state.load_implementation_state(planning)
    assert not state["completed_sections"]
    assert state["pending_sections"][SECTION]["verification"] == ["pytest -q"]
    assert not runtime.state.completed_sections(planning)


def test_explicit_blocked_review_overrides_legacy_pass(runtime, tmp_path):
    planning = make_plan(tmp_path / "planning", "deep")
    review = planning / "implementation" / "code_review" / f"{SECTION}-review.md"
    review.parent.mkdir(parents=True)
    review.write_text("No blocking findings.\nVerification: `pytest -q` passed.\n")
    findings = runtime.state.completion_evidence_findings(planning, SECTION, {"review_status": "blocked"})
    assert "missing-review-status" in {finding.code for finding in findings}


def test_failed_predecessor_does_not_unlock_successor(runtime, tmp_path, capsys, monkeypatch):
    planning = make_plan(tmp_path / "planning", "standard")
    index = planning / "sections" / "index.md"
    metadata, body = index.read_text().split("END_FORGE_META -->\n", 1)
    section = planning / "sections" / f"{SECTION}.md"
    (planning / "codex-plan.md").write_text(metadata + "END_FORGE_META -->\n" + section.read_text())
    successor = "section-02-successor"
    index.write_text(body.replace("END_MANIFEST", successor + "\nEND_MANIFEST")
                     + f"\n{successor} depends on {SECTION}\n")
    (planning / "sections" / f"{successor}.md").write_text(section.read_text())
    monkeypatch.setattr(runtime.flights, "implement_postflight_report",
                        lambda *a, **k: {"success": False, "blocking_gates": ["regression-check"]})
    args = record_args(planning, "--review-status", "pass", "--verification", "pytest -q", "--flight", "strict")
    code, payload = invoke(runtime, capsys, *args)
    assert code == 1 and successor not in payload["ready_sections"]
    successor_args = tuple(successor if value == SECTION else value for value in args)
    code, payload = invoke(runtime, capsys, *successor_args)
    assert code == 1 and payload["incomplete_predecessors"] == [SECTION]


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("command", ["traceability", "lint-plan", "lint-sections"])
def test_requirement_prefix_is_not_coverage(runtime, tmp_path, capsys, depth, command):
    planning = make_plan(tmp_path / "planning", depth)
    (planning / "spec.md").write_text("REQ-001: Validate account permissions.\nREQ-0010: Trim labels.\n")
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace("REQ-001", "REQ-0010"))
    code, payload = invoke(runtime, capsys, command, "--planning-dir", str(planning), "--strict")
    assert code == 1 and not payload["success"]
    assert any("traceability-gap" in finding["code"] and "REQ-001" in finding["message"]
               for finding in payload["findings"])
    if command == "traceability":
        assert not payload["coverage"]["REQ-001"]["covered"]
        assert payload["coverage"]["REQ-0010"]["covered"]


@pytest.mark.parametrize("initial", ["pending", "invalid-completed"])
def test_independent_sections_can_resolve_failed_records_in_either_order(runtime, tmp_path, capsys, initial):
    planning = make_plan(tmp_path / "planning", "standard")
    second = "section-02-other"
    index = planning / "sections/index.md"
    index.write_text(index.read_text().replace(f"{SECTION}\nEND_MANIFEST", f"{SECTION}\n{second}\nEND_MANIFEST"))
    section = planning / "sections" / f"{SECTION}.md"
    (planning / "sections" / f"{second}.md").write_text(section.read_text().replace(SECTION, second))
    (planning / "codex-plan.md").write_text(index.read_text().split("END_FORGE_META -->", 1)[0] + "END_FORGE_META -->\n" + section.read_text())
    state_path = runtime.state.implementation_state_path(planning)
    state_path.parent.mkdir()
    key = "pending_sections" if initial == "pending" else "completed_sections"
    state_path.write_text(json.dumps({key: {name: {"review_status": "blocked"} for name in (SECTION, second)}}))

    for name in (second, SECTION):
        code, payload = invoke(runtime, capsys, "implement-record-section", "--sections-dir", str(planning / "sections"),
                               "--section", name, "--review-status", "pass", "--verification", "pytest -q", "--flight", "strict")
        assert code == 0, payload
        assert name in runtime.state.completed_sections(planning)
    assert runtime.state.completed_sections(planning) == {SECTION, second}


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_canonical_completion_does_not_create_duplicate_traceability(runtime, tmp_path, capsys, depth):
    planning = make_plan(tmp_path / "planning", depth)
    code, payload = invoke(runtime, capsys, *record_args(planning, "--review-status", "pass",
                                                       "--verification", "pytest -q", "--flight", "off"))
    assert code == 0, payload
    assert payload["traceability_matrix"] is None
    assert not (planning / "traceability.md").exists()
