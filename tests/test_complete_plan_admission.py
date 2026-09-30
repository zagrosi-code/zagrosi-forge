"""Artifact presence and passing tests cannot bypass selected-depth plan admission."""

import json
import sys

import pytest

from forge_test_helpers import ROOT, run_cmd, run_raw
from runtime_support import load_runtime
from test_compact_plan import SECTION, make_plan


def incomplete_plan(path, depth):
    planning = make_plan(path, depth)
    section = planning / "sections" / f"{SECTION}.md"
    text = section.read_text()
    for phrase in (
        "Rationale: its ownership fits; reject a new abstraction.", "Security/privacy: no I/O.",
        "and runtime `pyproject.toml` ", "Assumption: callers supply strings.",
    ):
        text = text.replace(phrase, "")
    section.write_text(text)
    return planning


@pytest.mark.parametrize("depth", ["standard", "deep"])
def test_every_mutable_entry_requires_complete_selected_depth_admission(tmp_path, depth):
    planning = incomplete_plan(tmp_path / "plan", depth)
    assert run_cmd("lint-plan-artifacts", "--planning-dir", str(planning), "--strict")["success"]
    rejected = run_raw("postflight", "--phase", "plan", "--planning-dir", str(planning), "--depth", depth, "--strict")
    assert rejected.returncode == 1
    assert "lint-plan" in json.loads(rejected.stdout)["blocking_gates"]
    for command in (
        ("implement-setup", "--sections-dir", str(planning / "sections"), "--target-dir", str(tmp_path), "--depth", depth, "--flight", "off"),
        ("next-section", "--planning-dir", str(planning)),
        ("parallel-plan", "--planning-dir", str(planning)),
        ("implement-record-section", "--sections-dir", str(planning / "sections"), "--section", SECTION,
         "--target-dir", str(tmp_path), "--depth", depth, "--flight", "off", "--review-status", "pass",
         "--verification-source", "attestation", "--verification-outcome", "passed", "--verification", "All targeted checks passed"),
        ("postflight", "--phase", "implement", "--planning-dir", str(planning), "--target-dir", str(tmp_path), "--depth", depth),
    ):
        result = run_raw(*command)
        payload = json.loads(result.stdout)
        assert result.returncode == 1 and not payload["success"], (command, payload)
        assert not payload.get("ready_sections")
    assert not (planning / "implementation/zagrosi_implement_state.json").exists()


@pytest.mark.parametrize("depth", ["standard", "deep"])
def test_preexisting_completion_and_fresh_tests_do_not_hide_incomplete_plan(tmp_path, depth):
    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    planning = incomplete_plan(tmp_path / "plan", depth)
    # Represents a record accepted by the older artifact-only admission contract.
    record = {"completed_at": "2026-09-30T00:00:00Z", "review_status": "pass",
              "verification_result": {"version": 1, "source": "attestation", "outcome": "passed", "evidence": ["Targeted checks passed"]},
              "input_snapshot": forge.state.contract_snapshot(planning, SECTION, target_dir=tmp_path)}
    forge.storage.write_json(forge.state.implementation_state_path(planning), {"completed_sections": {SECTION: record}})
    run_cmd("implement-verify", "--planning-dir", str(planning), "--target-dir", str(tmp_path), "--", sys.executable, "-c", "pass")
    result = run_raw("postflight", "--phase", "implement", "--planning-dir", str(planning), "--target-dir", str(tmp_path), "--depth", depth)
    payload = json.loads(result.stdout)
    assert payload["integration_verification"]["success"]
    assert result.returncode == 1 and "lint-plan" in payload["blocking_gates"]


def test_lean_admission_keeps_its_selected_requirements(tmp_path):
    planning = incomplete_plan(tmp_path / "plan", "lean")
    assert run_cmd("postflight", "--phase", "plan", "--planning-dir", str(planning), "--strict")["success"]
    assert run_cmd("implement-setup", "--sections-dir", str(planning / "sections"), "--target-dir", str(tmp_path), "--flight", "off")["success"]


@pytest.mark.parametrize("artifact", ["section", "source"])
def test_portable_admission_rechecks_edited_contract_in_same_phase(tmp_path, monkeypatch, artifact):
    import signal

    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    planning = make_plan(tmp_path / "plan", "standard")
    monkeypatch.delattr(signal, "setitimer", raising=False)
    token = forge.session._CLI_CONTEXT.set({"texts": None, "owned_paths": None})
    try:
        with forge.session.read_phase():
            assert forge.flights.plan_admission_report(planning)["success"]
            if artifact == "section":
                section = planning / "sections" / f"{SECTION}.md"
                section.write_text(section.read_text().replace("Security/privacy: no I/O.", ""))
            else:
                (planning / "spec.md").write_text("REQ-OTHER: Change the approved behavior.\n")
            rejected = forge.flights.plan_admission_report(planning)
            assert not rejected["success"], rejected
    finally:
        forge.session._CLI_CONTEXT.reset(token)


@pytest.mark.parametrize("artifact", ["quality-gates.md", "linked.md"])
def test_portable_admission_cannot_hide_changed_final_contract(tmp_path, monkeypatch, artifact):
    import signal

    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    planning = make_plan(tmp_path / "plan", "standard")
    contract = planning / artifact
    contract.write_text("REQ-001: Preserve approved behavior.\n")
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + f"\n[Contract](../{artifact})\n")
    run_cmd("implement-verify", "--planning-dir", str(planning), "--target-dir", str(tmp_path),
            "--source", "attestation", "--outcome", "passed", "--evidence", "Integration suite passed")
    monkeypatch.delattr(signal, "setitimer", raising=False)
    token = forge.session._CLI_CONTEXT.set({"texts": None, "owned_paths": None})
    try:
        with forge.session.read_phase():
            assert forge.flights.plan_admission_report(planning)["success"]
            assert forge.verification.integration_report(planning, tmp_path)["success"]
            contract.write_text("REQ-001: Add security integration coverage.\n")
            assert not forge.verification.integration_report(planning, tmp_path)["success"]
    finally:
        forge.session._CLI_CONTEXT.reset(token)


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("phase", ["preflight", "postflight"])
def test_portable_implementation_flight_uses_one_plan_batch(tmp_path, monkeypatch, capsys, depth, phase):
    import signal

    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    planning = make_plan(tmp_path / "plan", depth)
    monkeypatch.delattr(signal, "setitimer", raising=False)
    calls, execute = [], forge.child_process.execute

    def counted(argv, *args, **kwargs):
        calls.append(argv)
        return execute(argv, *args, **kwargs)

    monkeypatch.setattr(forge.child_process, "execute", counted)
    assert forge.entrypoint.main([phase, "--phase", "implement", "--planning-dir", str(planning),
                                  "--sections-dir", str(planning / "sections"),
                                  "--target-dir", str(tmp_path), "--depth", depth]) == 0
    payload = json.loads(capsys.readouterr().out)
    names = [gate["name"] for gate in payload["gates"]]
    assert names.count("lint-plan") == 1
    assert names.count("forge-score") == (0 if depth == "lean" else 1)
    assert len(calls) == 1 and calls[0][2] == "gate-batch"


def test_legacy_physical_plan_preserves_admitted_depth_across_resume(tmp_path):
    import re
    from test_resume_guidance import documented_detached_plan

    planning = documented_detached_plan(tmp_path / "plan", "standard")
    plan = planning / "codex-plan.md"
    plan.write_text(re.sub(r"<!-- FORGE_META.*?END_FORGE_META -->\n", "", plan.read_text(), flags=re.S))
    run_cmd("implement-setup", "--sections-dir", str(planning / "sections"), "--target-dir", str(tmp_path),
            "--depth", "standard", "--flight", "off")
    saved = json.loads((planning / "implementation/zagrosi_implement_config.json").read_text())
    assert saved["depth_mode"] == "standard"
    next_ready = run_cmd("next-section", "--planning-dir", str(planning))
    assert next_ready["next_section"] == SECTION
    assert next_ready["admission"]["depth_mode"] == "standard"
    plan.write_text(plan.read_text().replace("Security/privacy: no I/O.", ""))
    for command in (
        ("implement-setup", "--sections-dir", str(planning / "sections"), "--target-dir", str(tmp_path), "--flight", "off"),
        ("next-section", "--planning-dir", str(planning)),
        ("implement-record-section", "--sections-dir", str(planning / "sections"), "--section", SECTION,
         "--target-dir", str(tmp_path), "--flight", "off", "--review-status", "pass",
         "--verification-source", "attestation", "--verification-outcome", "passed", "--verification", "Targeted checks passed"),
        ("postflight", "--phase", "implement", "--planning-dir", str(planning), "--target-dir", str(tmp_path)),
    ):
        result = run_raw(*command)
        assert result.returncode == 1, result.stdout
        payload = json.loads(result.stdout)
        assert not payload["success"]
    status = run_cmd("status", "--path", str(planning))
    assert not status["admission"]["success"] and status["admission"]["depth_mode"] == "standard"


@pytest.mark.parametrize("config", ["[]", "null", '"standard"', '{"depth_mode": []}', '{"depth_mode": "invalid"}', '{invalid'])
def test_malformed_saved_depth_cannot_bypass_final_admission(tmp_path, config):
    from test_resume_guidance import documented_detached_plan

    planning = documented_detached_plan(tmp_path / "plan", "standard")
    plan = planning / "codex-plan.md"
    plan.write_text(plan.read_text().split("END_FORGE_META -->", 1)[1])
    saved = planning / "implementation/zagrosi_implement_config.json"
    saved.parent.mkdir()
    saved.write_text(config)
    result = run_raw("postflight", "--phase", "implement", "--planning-dir", str(planning),
                     "--target-dir", str(tmp_path), "--profile", "solo")
    assert result.returncode == 1 and not result.stderr
    assert json.loads(result.stdout)["error_code"] == "invalid-planning-depth"


def test_legacy_valid_config_without_depth_retains_fallback(tmp_path):
    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    saved = tmp_path / "implementation/zagrosi_implement_config.json"
    saved.parent.mkdir()
    saved.write_text('{"profile":"solo"}')
    assert forge.artifacts.planning_depth(tmp_path, "standard") == "standard"


def test_warm_admission_rechecks_newly_created_configured_source(tmp_path):
    from test_resume_guidance import documented_detached_plan

    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    planning = documented_detached_plan(tmp_path / "plan", "standard")
    external = tmp_path / "late-spec.md"
    (planning / "zagrosi_plan_config.json").write_text(json.dumps({"initial_file": str(external), "depth_mode": "standard"}))
    token = forge.session._CLI_CONTEXT.set({"parser": forge.cli.build_parser(), "texts": None, "owned_paths": None})
    try:
        with forge.session.read_phase():
            assert forge.flights.plan_admission_report(planning)["success"]
            external.write_text("REQ-OTHER: Unplanned requirement.\n")
            assert not forge.flights.plan_admission_report(planning)["success"]
    finally:
        forge.session._CLI_CONTEXT.reset(token)
