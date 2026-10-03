"""Readable workflow output must retain the decisions available in JSON."""

from __future__ import annotations

import json
from pathlib import Path
import shlex

import pytest

from runtime_support import load_runtime
from test_compact_plan import SECTION, make_plan


@pytest.fixture
def forge():
    return load_runtime(Path(__file__).resolve().parents[1] / "scripts/zagrosi_skills.py")


def invoke(forge, capsys, *args):
    code = forge.entrypoint.main(list(args))
    return code, capsys.readouterr().out


@pytest.mark.parametrize("command,flag,exit_code", [
    ("status", "--path", 0), ("next-section", "--planning-dir", 1),
])
def test_blocked_admission_is_actionable_in_readable_cli(forge, tmp_path, capsys, command, flag, exit_code):
    planning = make_plan(tmp_path / "plan with spaces")
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace("Verdict: pass", "Verdict: blocked"))
    arguments = [command, flag, str(planning)]
    code, raw = invoke(forge, capsys, *arguments)
    payload = json.loads(raw)
    assert code == exit_code
    assert payload["admission"]["success"] is False

    pretty_code, pretty = invoke(forge, capsys, *arguments, "--pretty")
    assert pretty_code == code
    assert "Admission: BLOCKED" in pretty
    assert "compact-review-incomplete" in pretty
    assert "blocked findings must be resolved" in pretty
    assert str(section.resolve()) in pretty
    assert pretty.count("compact-review-incomplete") == 1
    if payload.get("next_action"):
        assert payload["next_action"] in pretty


def test_resume_retains_changed_inputs_and_pending_verification(forge):
    payload = {
        "success": True, "planning_dir": "/work/plan", "next_section": SECTION,
        "next_action": "resolve pending verification and retry recording",
        "resume": {"stage": "verified", "evidence_current": False,
                   "verification_pending": True, "blocking_gates": ["implementation-drift"],
                   "changed_inputs": ["code:src/labels.py"],
                   "notes": "Preserve the public error message."},
        "commands": {"record": ["python", "/plugin with spaces/forge.py", "implement-record-section",
                                "--section", SECTION, "--flight", "strict"]},
        "packet": {"content": "FULL-CONTEXT-DO-NOT-ECHO" * 500},
    }
    pretty = forge.output.format_pretty(payload)
    for detail in (payload["next_action"], "implementation-drift", "code:src/labels.py",
                   "Preserve the public error message.", "implement-record-section", "--flight", "strict"):
        assert detail in pretty
    assert "FULL-CONTEXT-DO-NOT-ECHO" not in pretty
    assert "Evidence current: no" in pretty
    assert "Verification pending: yes" in pretty


def test_context_retry_keeps_error_and_complete_command(forge):
    payload = {
        "success": False, "planning_dir": "/work/plan", "next_section": SECTION,
        "next_action": "repair context before implementation",
        "packet": {"success": False, "error": "Complete section requires 3150 words.", "required_words": 3150},
        "commands": {"retry_context": ["python", "/plugin with spaces/forge.py", "next-section",
                                       "--planning-dir", "/work/plan with spaces", "--max-words", "3150"]},
    }
    pretty = forge.output.format_pretty(payload)
    assert payload["packet"]["error"] in pretty
    assert payload["next_action"] in pretty
    for value in ("next-section", "/plugin with spaces/forge.py", "/work/plan with spaces", "--max-words", "3150"):
        assert value in pretty


def test_saved_record_and_failed_next_entry_stay_distinct(forge):
    payload = {
        "success": True, "recorded": True, "section": SECTION, "next_section": "section-02-consumer",
        "entry": {"success": False, "next_action": "repair next-section context; preceding record is saved",
                  "error": "Cannot read linked contract.",
                  "commands": {"retry_context": ["python", "forge.py", "next-section", "--planning-dir", "/work/plan"]}},
    }
    pretty = forge.output.format_pretty(payload)
    assert "Recorded: yes" in pretty
    assert "Next entry: BLOCKED" in pretty
    assert payload["entry"]["next_action"] in pretty
    assert payload["entry"]["error"] in pretty
    assert "--planning-dir" in pretty


def test_provider_status_displays_readiness_without_auth_calls(forge, monkeypatch, capsys):
    monkeypatch.setattr(forge.providers.shutil, "which", lambda name: "/native/" + name if name != "claude" else None)

    def forbidden(*args, **kwargs):
        pytest.fail("Listing availability must not call a native CLI.")

    monkeypatch.setattr(forge.providers, "execute", forbidden)
    code, pretty = invoke(forge, capsys, "provider-status", "--pretty")
    assert code == 0
    assert "codex" in pretty and "gemini" in pretty and "claude" in pretty
    assert "available" in pretty.lower() and "unavailable" in pretty.lower()
    assert "unchecked" in pretty
    assert "codex login" in pretty and "claude auth login" in pretty
    assert "do not prove model access" in pretty


@pytest.mark.parametrize("identity,observed", [
    ("unreported", []), ("mismatch_or_alias", ["reported-exact-model"]),
])
def test_provider_review_preserves_model_identity_and_result_location(forge, identity, observed):
    payload = {
        "schema": "forge-provider-review-v1", "success": identity == "unreported", "provider": "claude",
        "requested_model": "requested-exact-model", "observed_models": observed, "model_identity": identity,
        "output": "/work/reviews/claude review.json", "review": "REVIEW-BODY-DO-NOT-ECHO" * 500,
        "error": "Reported model differs." if observed else None,
    }
    pretty = forge.output.format_pretty(payload)
    for value in ("claude", "requested-exact-model", identity, payload["output"], *observed):
        assert value in pretty
    if payload["error"]:
        assert payload["error"] in pretty
    assert "REVIEW-BODY-DO-NOT-ECHO" not in pretty


def test_bounded_readable_diagnostics_retain_full_report(forge, tmp_path):
    findings = [{"severity": "high", "code": f"failure-{number:02d}",
                 "message": f"Repair contract {number}."} for number in range(20)]
    report = tmp_path / "full report.json"
    report.write_text(json.dumps({"findings": findings}))
    payload = {"success": False, "next_section": None, "next_action": "repair the listed contracts",
               "diagnostics": findings, "full_report": str(report)}
    pretty = forge.output.format_pretty(payload)
    assert "failure-00" in pretty
    assert "more finding(s)" in pretty
    assert "failure-19" not in pretty
    assert str(report) in pretty
    assert json.loads(report.read_text())["findings"] == findings


def test_recovery_catalog_and_help_expose_parseable_examples(forge, capsys):
    names = {"next-section", "implement-progress", "context-brief", "implementation-packet", "parallel-plan"}
    code, raw = invoke(forge, capsys, "commands", "--verbose")
    assert code == 0
    catalog = {row["name"]: row for row in json.loads(raw)["commands"]}
    assert names <= catalog.keys()
    parser = forge.cli.build_parser()
    help_text = " ".join(parser.format_help().split())
    for name in sorted(names):
        row = catalog[name]
        assert row["summary"] in help_text
        assert row["examples"], name
        for example in row["examples"]:
            argv = shlex.split(example)
            assert argv[2] == name
            assert parser.parse_args(argv[2:]).func


def test_verbose_readable_catalog_adds_examples_only_when_requested(forge, capsys):
    code, compact = invoke(forge, capsys, "commands", "--phase", "implement", "--pretty")
    assert code == 0
    assert "python3 scripts/zagrosi_skills.py" not in compact
    code, verbose = invoke(forge, capsys, "commands", "--phase", "implement", "--verbose", "--pretty")
    assert code == 0
    assert "python3 scripts/zagrosi_skills.py next-section --planning-dir" in verbose
    assert "python3 scripts/zagrosi_skills.py implement-progress --planning-dir" in verbose


def test_provider_recovery_keeps_reason_and_safe_cleanup_diagnostic(forge):
    payload = {"schema": "forge-provider-review-v1", "success": False, "provider": "codex",
               "failure_kind": "timeout", "recovery": "Use a smaller packet or a longer deadline.",
               "termination_error": "process group termination unconfirmed"}
    pretty = forge.output.format_pretty(payload)
    assert "Failure: timeout" in pretty
    assert payload["recovery"] in pretty
    assert payload["termination_error"] in pretty


def test_provider_preflight_keeps_uncertainty_visible(forge):
    payload = {"success": True, "providers": [{"provider": "claude", "available": True,
               "authentication": "unchecked", "cli": {"status": "unknown", "version": "2.1.285",
               "reason": "Help does not validate arguments."}}]}
    pretty = forge.output.format_pretty(payload)
    assert "CLI: unknown (2.1.285)" in pretty
    assert "Help does not validate arguments." in pretty
    assert "authentication: unchecked" in pretty
