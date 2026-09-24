from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from forge_test_helpers import (
    load_zagrosi_module,
    run_cmd,
    run_raw,
    write_compact_project_fixture,
    write_lean_plan_fixture,
)


def test_successful_project_and_plan_postflights_have_compact_gate_records(tmp_path: Path) -> None:
    project = write_compact_project_fixture(tmp_path / "project")
    plan = write_lean_plan_fixture(tmp_path / "plan")

    project_result = run_raw(
        "postflight",
        "--phase",
        "project",
        "--planning-dir",
        str(project),
        "--depth",
        "lean",
        "--flight",
        "strict",
    )
    plan_result = run_raw(
        "postflight",
        "--phase",
        "plan",
        "--planning-dir",
        str(plan),
        "--depth",
        "lean",
        "--flight",
        "strict",
    )

    assert project_result.returncode == 0, project_result.stderr + project_result.stdout
    assert plan_result.returncode == 0, plan_result.stderr + plan_result.stdout
    assert len(project_result.stdout.encode()) <= 700
    assert len(plan_result.stdout.encode()) <= 1_500
    for result in (project_result, plan_result):
        payload = json.loads(result.stdout)
        for gate in payload["gates"]:
            assert "command" not in gate
            assert "returncode" not in gate
            assert "stderr_tail" not in gate
            assert gate.get("payload") != {}


def test_opt_in_plan_report_retains_its_output_path(tmp_path: Path) -> None:
    planning = write_lean_plan_fixture(tmp_path / "plan")

    payload = run_cmd(
        "postflight",
        "--phase",
        "plan",
        "--planning-dir",
        str(planning),
        "--depth",
        "lean",
        "--flight",
        "strict",
        "--write-report",
    )

    report_gate = next(gate for gate in payload["gates"] if gate["name"] == "report")
    assert Path(report_gate["payload"]["output"]).exists()


def test_gate_compaction_preserves_full_failure_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_zagrosi_module()
    completed = SimpleNamespace(
        returncode=2,
        stdout='{"success":false,"error":"broken gate"}\n',
        stderr="diagnostic trace\n",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)

    failed = module.gates.run_internal_gate("broken", ["lint-plan", "--strict"])
    malformed_completed = SimpleNamespace(returncode=0, stdout="not-json\n", stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: malformed_completed)
    malformed = module.gates.run_internal_gate("malformed", ["lint-plan"])

    def time_out(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(
            cmd=["lint-plan"],
            timeout=9,
            output=b"partial progress\n",
            stderr=b"timeout detail\n",
        )

    monkeypatch.setattr(subprocess, "run", time_out)
    timed_out = module.gates.run_internal_gate("slow", ["lint-plan"], timeout_seconds=9)
    passed_direct = module.gates.direct_gate("ready", True, {})
    passed_quality = module.gates.direct_gate(
        "quality",
        True,
        {
            "success": True,
            "gate": "sections",
            "score": 100,
            "finding_count": 0,
            "findings": [],
            "planning_dir": "/tmp/large-and-redundant",
        },
    )
    passed_progress = module.gates.direct_gate(
        "implementation-progress",
        True,
        {
            "deferred_gate": "lint-implementation-state",
            "recorded_sections": [],
            "remaining_sections": ["section-01-example"],
        },
        required=False,
    )
    failed_direct = module.gates.direct_gate("not-ready", False, {"error": "missing"})
    legacy_flight = module.gates.flight_payload(
        phase="plan",
        stage="postflight",
        mode="strict",
        gates=[
            {
                "name": "ready",
                "required": True,
                "success": True,
                "returncode": 0,
                "command": "internal",
                "payload": {},
                "stderr_tail": "",
            }
        ],
    )

    assert failed == {
        "name": "broken",
        "required": True,
        "success": False,
        "returncode": 2,
        "command": "lint-plan --strict",
        "payload": {"success": False, "error": "broken gate"},
        "stderr_tail": "diagnostic trace\n",
    }
    assert malformed == {
        "name": "malformed",
        "required": True,
        "success": False,
        "returncode": 0,
        "command": "lint-plan",
        "payload": {"error_code": "invalid-gate-json", "stdout": "not-json\n"},
        "stderr_tail": "",
    }
    assert timed_out == {
        "name": "slow",
        "required": True,
        "success": False,
        "returncode": 124,
        "command": "lint-plan",
        "payload": {
            "error_code": "gate-timeout",
            "timeout_seconds": 9,
            "stdout": "partial progress\n",
        },
        "stderr_tail": "timeout detail\n",
    }
    assert passed_direct == {"name": "ready", "required": True, "success": True}
    assert passed_quality == {
        "name": "quality",
        "required": True,
        "success": True,
        "payload": {"gate": "sections", "score": 100, "finding_count": 0},
    }
    assert passed_progress == {
        "name": "implementation-progress",
        "required": False,
        "success": True,
        "payload": {"deferred_gate": "lint-implementation-state"},
    }
    assert failed_direct == {
        "name": "not-ready",
        "required": True,
        "success": False,
        "returncode": 1,
        "command": "internal",
        "payload": {"error": "missing"},
        "stderr_tail": "",
    }
    assert legacy_flight["gates"] == [{"name": "ready", "required": True, "success": True}]


def test_failure_summary_deduplicates_without_losing_findings_or_evidence(capsys):
    forge = load_zagrosi_module()
    findings = [{"severity": "high", "code": f"ownership-{index}", "message": "Required " + "雪" * 600,
                 "path": f"src/file{index}.py", "recommendation": "Repair the ownership contract."}
                for index in range(23)]
    gate = forge.gates.direct_gate("ownership", False, {"findings": findings, "finding_count": len(findings),
                                                       "owned_paths": ["src/auth.py"], "content": "contract" * 500})
    payload = forge.gates.flight_payload(phase="plan", stage="postflight", mode="strict", gates=[
        gate, forge.gates.direct_gate("nested", False, {"admission": gate["payload"]}),
    ])
    assert forge.output.print_json(payload, 1) == 1
    compact = json.loads(capsys.readouterr().out)
    report = Path(compact["full_report"])
    try:
        assert compact["diagnostics"] == findings
        assert compact["gates"][0]["payload"]["finding_refs"] == list(range(23))
        assert compact["gates"][1]["payload"]["finding_refs"] == list(range(23))
        assert compact["gates"][0]["payload"]["owned_paths"] == ["src/auth.py"]
        assert compact["gates"][0]["payload"]["content"] == "contract" * 500
        assert json.loads(report.read_text()) == payload
        assert json.loads(report.read_text())["gates"][0]["payload"]["findings"] == findings
        if os.name == "posix":
            assert report.stat().st_mode & 0o777 == 0o600
        pretty = forge.output.format_pretty(compact)
        assert "ownership-22" in pretty and str(report) in pretty
    finally:
        report.unlink(missing_ok=True)


def test_failed_report_save_preserves_full_inline_evidence(monkeypatch, capsys):
    forge = load_zagrosi_module()
    import tempfile

    monkeypatch.setattr(tempfile, "mkstemp", lambda **_: (_ for _ in ()).throw(OSError("no storage")))
    payload = {"success": False, "phase": "plan", "stage": "postflight", "gates": [
        {"success": False, "payload": {"findings": [{"code": "important", "message": "Keep this evidence"}]}}]}
    assert forge.output.print_json(payload, 1) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["gates"] == payload["gates"]
    assert "full_report_error" in result


def test_full_output_preserves_machine_gate_payloads(tmp_path):
    planning = write_lean_plan_fixture(tmp_path / "plan")
    (planning / "codex-plan-tdd.md").write_text("No verification supplied.\n")
    result = run_raw("postflight", "--phase", "plan", "--planning-dir", str(planning), "--strict", "--full-output")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "full_report" not in payload and "diagnostics" not in payload
    assert any(gate.get("payload", {}).get("findings") for gate in payload["gates"])


@pytest.mark.parametrize("error", [{"error": "Restore the missing authorization evidence."},
                                  {"errors": ["Restore the missing authorization evidence."]},
                                  {"success": False, "message": "Restore the missing authorization evidence."}])
def test_failure_summary_preserves_nested_plain_errors(error):
    forge = load_zagrosi_module()
    payload = {"success": False, "gates": [{"name": "admission", "success": False, "payload": {
        "findings": [{"code": "review", "message": "Review the contract", "severity": "high"}],
        "nested": {**error, "path": "src/auth.py", "next_command": "check-auth --strict"},
    }}]}
    result = forge.output.failure_summary(payload)
    try:
        diagnostic = next(item for item in result["diagnostics"] if item["message"] == error.get("error", error.get("message", error.get("errors", [None])[0])))
        assert diagnostic["path"] == "src/auth.py"
        assert diagnostic["next_command"] == "check-auth --strict"
    finally:
        Path(result["full_report"]).unlink()
