"""Readable completion separates retained attempts from present findings."""

import copy
import json
from pathlib import Path
import re
import sys

import pytest

from test_compact_plan import SECTION
from test_compatibility_workflows import setup, verify, workspace, write_checks
from test_pretty_workflows import forge, invoke


def assert_history_count(pretty, count):
    lines = [line for line in pretty.splitlines()
             if re.search(r"\b(history|historical|retained|previous|prior)\b", line, re.I)]
    assert any(re.search(rf"\b{count}\b", line) for line in lines), pretty


@pytest.fixture
def completed(forge, tmp_path, capsys, monkeypatch):
    """One real retry/record flow; fixture review status is supplied, not independently judged."""
    planning, target = workspace(tmp_path / "workspace with spaces")
    assert setup(forge, capsys, planning, target)[0] == 0
    write_checks(target)
    assert verify(forge, capsys, planning, target, "baseline")[0] == 0
    source = target / "src/labels.py"
    source.write_text("def normalize(value):\n    return value.strip().lower()\n")
    failed_code, failed = verify(forge, capsys, planning, target, "candidate")
    assert failed_code == 1 and failed["outcome"] == "failed"
    source.write_text("def normalize(value):\n    return value.strip()\n")
    assert verify(forge, capsys, planning, target, "candidate")[0] == 0
    feature = ("import sys; sys.path.insert(0, 'src'); from labels import normalize; "
               "assert normalize(' Ada  Lovelace ') == 'Ada  Lovelace'; assert normalize('  ') == ''")
    code, raw = invoke(forge, capsys, "implement-verify", "--planning-dir", str(planning),
                       "--target-dir", str(target), "--section", SECTION, "--integration",
                       "--", sys.executable, "-B", "-c", feature)
    assert code == 0, raw
    receipt = json.loads(raw)
    captured = []
    original = forge.output.print_json

    def observe(payload, *args, **kwargs):
        if payload.get("section") == SECTION and "record" in payload:
            captured.append(copy.deepcopy(payload))
        return original(payload, *args, **kwargs)

    # Observe the real payload without replacing printing or recording behavior.
    with monkeypatch.context() as observation:
        observation.setattr(forge.output, "print_json", observe)
        code, pretty = invoke(forge, capsys, "implement-record-section", "--sections-dir", str(planning / "sections"),
                              "--target-dir", str(target), "--section", SECTION, "--review-status", "pass",
                              "--verification-receipt", receipt["receipt_path"], "--file", "src/labels.py",
                              "--flight", "off", "--pretty")
    assert code == 0 and len(captured) == 1, pretty
    return forge, captured[0], pretty


def test_successful_retry_names_saved_evidence_without_relabeling_history(completed, capsys):
    forge, payload, pretty = completed
    assert payload["success"] and payload["recorded"] and payload["integration_verification"]["success"]
    assert payload["section"] == SECTION and SECTION in pretty
    assert payload["state_path"] in pretty
    state_path = Path(payload["state_path"])
    saved = state_path.read_bytes()
    state = json.loads(saved)
    assert state["completed_sections"][SECTION] == payload["record"]
    compatibility = payload["record"]["compatibility"]
    history = compatibility["prior_attempts"]
    assert len(history) == 1 and history[0]["result"]["outcome"] == "failed"
    assert compatibility["candidate"]["outcome"] == "passed"
    assert history[0]["result"]["error"] not in pretty
    assert_history_count(pretty, len(history))
    assert payload["next_action"] in pretty

    before = copy.deepcopy(payload)
    assert forge.output.format_pretty(payload) + "\n" == pretty
    assert payload == before
    assert forge.output.print_json(payload, 0) == 0
    raw = capsys.readouterr().out
    assert raw == json.dumps(before, sort_keys=True, separators=(",", ":")) + "\n"
    assert json.loads(raw)["record"]["compatibility"]["prior_attempts"] == history
    assert payload == before and state_path.read_bytes() == saved


def test_history_does_not_hide_simultaneous_current_errors(forge):
    payload = {
        "success": False, "recorded": False, "section": "section-07-caller", "next_section": None,
        "state_path": "/work/plan with spaces/implementation/state.json",
        "record": {"compatibility": {
            "prior_attempts": [
                {"stage": "candidate", "result": {"outcome": "failed", "error": "OLD CANDIDATE ERROR"}},
                {"stage": "baseline", "result": {"outcome": "failed", "error": "OLD BASELINE ERROR"}},
            ],
            "candidate": {"outcome": "failed", "error": "CURRENT CANDIDATE ERROR"},
        }},
        "postflight": {"phase": "implement", "stage": "postflight", "success": False,
                       "gates": [{"name": "current-check", "success": False,
                                  "payload": {"error": "CURRENT POSTFLIGHT ERROR"}}]},
        "entry": {"success": False, "error": "CURRENT ENTRY ERROR",
                  "next_action": "repair current entry", "next_command": ["python", "forge.py", "next-section"]},
        "diagnostics": [{"severity": "high", "code": "current-extra", "message": "CURRENT OTHER ERROR"}],
    }
    before = copy.deepcopy(payload)
    pretty = forge.output.format_pretty(payload)
    for text in ("CURRENT CANDIDATE ERROR", "CURRENT POSTFLIGHT ERROR", "CURRENT ENTRY ERROR",
                 "CURRENT OTHER ERROR", "repair current entry", "next-section", payload["section"], payload["state_path"]):
        assert text in pretty
    assert "OLD CANDIDATE ERROR" not in pretty and "OLD BASELINE ERROR" not in pretty
    assert_history_count(pretty, 2)
    assert payload == before


def test_only_the_known_record_compatibility_history_is_historical(forge):
    payload = {
        "success": False, "recorded": False, "next_section": None,
        "record": {
            "compatibility": {"prior_attempts": [{"result": {"error": "KNOWN OLD ERROR"}}]},
            "other": {"prior_attempts": [{"error": "CURRENT RECORD SIBLING ERROR"}]},
        },
        "prior_attempts": [{"error": "CURRENT ROOT ERROR"}],
        "other": {"compatibility": {"prior_attempts": [{"error": "CURRENT UNRELATED ERROR"}]}},
    }
    before = copy.deepcopy(payload)
    pretty = forge.output.format_pretty(payload)
    assert "KNOWN OLD ERROR" not in pretty
    for text in ("CURRENT RECORD SIBLING ERROR", "CURRENT ROOT ERROR", "CURRENT UNRELATED ERROR"):
        assert text in pretty
    assert_history_count(pretty, 1)
    assert payload == before


def test_absent_optional_completion_details_are_not_invented(forge):
    minimal = {"success": True, "recorded": True, "next_section": None}
    explicit_nulls = {**minimal, "section": None, "state_path": None, "record": None}
    before = copy.deepcopy(explicit_nulls)
    pretty = forge.output.format_pretty(minimal)
    assert forge.output.format_pretty(explicit_nulls) == pretty
    assert not re.search(r"\b(history|historical|retained|previous|prior)\b", pretty, re.I)
    assert explicit_nulls == before


def test_failed_postflight_prints_current_findings_and_saves_complete_history(forge, tmp_path, capsys, monkeypatch):
    payload = {
        "success": False, "recorded": True, "section": SECTION, "next_section": None,
        "state_path": str(tmp_path / "state.json"),
        "record": {"compatibility": {"prior_attempts": [
            {"result": {"error": "OLD ERROR", "findings": [
                {"severity": "high", "code": "old-finding", "message": "OLD FINDING"}]}},
            {"result": {"error": "SHARED ERROR"}},
        ]}},
        "postflight": {"phase": "implement", "stage": "postflight", "success": False,
                       "gates": [{"name": "current-check", "success": False,
                                  "payload": {"error": "SHARED ERROR", "path": "current.py"}}]},
    }
    before = copy.deepcopy(payload)
    monkeypatch.setattr(forge.context, "build_context", lambda *args, **kwargs: payload)
    arguments = ["context-brief", "--planning-dir", str(tmp_path)]
    reports = []
    try:
        code, raw = invoke(forge, capsys, *arguments)
        assert code == 1
        machine = json.loads(raw)
        reports.append(Path(machine["full_report"]))
        assert {"OLD ERROR", "OLD FINDING", "SHARED ERROR"} <= {
            item["message"] for item in machine["diagnostics"]}
        assert len(machine["record"]["compatibility"]["prior_attempts"]) == 2

        code, pretty = invoke(forge, capsys, *arguments, "--pretty")
        assert code == 1
        reports.append(Path(pretty.split("Full report: ", 1)[1].splitlines()[0]))
        assert all(json.loads(report.read_text()) == before for report in reports)
        assert reports[0].read_bytes() == reports[1].read_bytes()
        assert "OLD ERROR" not in pretty and "OLD FINDING" not in pretty
        assert pretty.count("SHARED ERROR") == 1 and "current.py" in pretty
        assert_history_count(pretty, 2)
        assert SECTION in pretty and payload["state_path"] in pretty
        assert payload == before
    finally:
        for report in reports:
            report.unlink(missing_ok=True)
