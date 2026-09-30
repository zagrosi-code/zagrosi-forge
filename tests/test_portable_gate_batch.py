"""A verified portable worker shares read analyses without losing isolation."""
import io
import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from forge_test_helpers import load_zagrosi_module, write_lean_plan_fixture


@pytest.fixture
def forge():
    return load_zagrosi_module()


@pytest.fixture
def plan(tmp_path):
    return write_lean_plan_fixture(tmp_path / "plan")


@pytest.mark.parametrize("invalid", [False, True])
def test_portable_batch_matches_individual_gates_with_one_process(forge, plan, tmp_path, monkeypatch, invalid):
    target = tmp_path / "missing" if invalid else plan
    jobs = [(name, [name, "--planning-dir", str(target), "--strict"], required)
            for name, required in [("lint-plan", True), ("lint-plan-artifacts", False), ("lint-sections", True)]]
    expected = [forge.gates.run_internal_gate(name, command, required=required) for name, command, required in jobs]
    processes = []
    popen = subprocess.Popen

    def counted(*args, **kwargs):
        processes.append(popen(*args, **kwargs))
        return processes[-1]

    monkeypatch.setattr(subprocess, "Popen", counted)
    assert forge.gates.run_gate_worker(jobs) == expected
    assert len(processes) == 1 and processes[0].poll() == 0


def test_platform_without_timers_batches_entire_plan_flight(forge, plan, monkeypatch, capsys):
    expected_args = ["postflight", "--phase", "plan", "--planning-dir", str(plan), "--full-output"]
    assert forge.entrypoint.main(expected_args) == 0
    expected = json.loads(capsys.readouterr().out)
    monkeypatch.delattr(signal, "setitimer", raising=False)
    calls = []
    execute = forge.child_process.execute

    def counted(*args, **kwargs):
        calls.append(args[0])
        return execute(*args, **kwargs)

    monkeypatch.setattr(forge.child_process, "execute", counted)
    assert forge.entrypoint.main(expected_args) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert len(calls) == 1 and calls[0][2] == "gate-batch"


@pytest.mark.parametrize("jobs", [
    {}, [], [["doctor", ["doctor"], "true"]],
    [["traceability", ["traceability", "--planning-dir", ".", "--write"], True]],
    [["status", ["status", "--path", "."], True]],
    [["doctor", ["status"], True]], [["doctor", ["doctor"], True]] * 65,
])
def test_worker_rejects_unapproved_input_without_running_commands(forge, monkeypatch, capsys, jobs):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Unapproved worker command executed")

    monkeypatch.setattr(forge.gates, "run_internal_gate", forbidden)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(jobs)))
    assert forge.entrypoint.main(["gate-batch"]) == 1
    assert not json.loads(capsys.readouterr().out)["success"]


def test_worker_cache_detects_mutations_without_timers_or_children(forge, tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.txt"
    source.write_text("old")
    reads, checks = [], []
    original_read = Path.read_text

    def read(path, *args, **kwargs):
        if path == source:
            reads.append(True)
        return original_read(path, *args, **kwargs)

    def doctor(_args):
        expected = "new" if checks else "old"
        assert forge.storage.read_text(source) == forge.storage.read_text(source) == expected
        checks.append(True)
        source.write_text("new")
        return forge.output.print_json({"success": True})

    def forbidden(*_args, **_kwargs):
        pytest.fail("An isolated worker attempted to create another child or a timer")

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(forge.doctor, "doctor", doctor)
    monkeypatch.setattr(signal, "setitimer", forbidden, raising=False)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps([["doctor", ["doctor"], True]] * 2)))
    assert forge.entrypoint.main(["gate-batch"]) == 0
    assert all(gate["success"] for gate in json.loads(capsys.readouterr().out)["gates"])
    assert len(reads) == len(checks) == 2
    assert forge.session._CLI_CONTEXT.get() is forge.session._GATE_STREAMS.get() is None


def test_worker_deadline_returns_failure_and_reaps_process(forge, monkeypatch):
    processes = []
    popen, execute = subprocess.Popen, forge.child_process.execute

    def counted(*args, **kwargs):
        processes.append(popen(*args, **kwargs))
        return processes[-1]

    def stalled(_argv, workspace, **kwargs):
        assert kwargs["timeout"] == 120
        return execute([sys.executable, "-c", "import time; time.sleep(30)"], workspace, timeout=.05)

    monkeypatch.setattr(subprocess, "Popen", counted)
    monkeypatch.setattr(forge.child_process, "execute", stalled)
    gates = forge.gates.run_gate_worker([("doctor", ["doctor"], True)])
    assert gates[0]["success"] is False and gates[0]["returncode"] == 124
    assert gates[0]["payload"] == {"error_code": "gate-timeout", "timeout_seconds": 120, "timeout_scope": "batch"}
    assert len(processes) == 1 and processes[0].poll() is not None


@pytest.mark.parametrize("result", [
    {"stdout": "{}"},
    {"stdout": '{"gates":[{"name":"other","required":true,"success":true}]}'},
    {"stdout": '{"gates":[{"name":"doctor","required":true,"success":true}]}', "stdout_truncated": True},
    {"stdout": '{"gates":[{"name":"doctor","required":true,"success":true}]}', "stderr_truncated": True},
    {"stdout": '{"gates":[{"name":"doctor","required":true,"success":true}]}', "returncode": 1},
])
def test_incomplete_or_failed_worker_cannot_report_success(forge, monkeypatch, result):
    monkeypatch.setattr(forge.child_process, "execute", lambda *_args, **_kwargs:
                        {"returncode": 0, "timed_out": False, "stderr": "", **result})
    gates = forge.gates.run_gate_worker([("doctor", ["doctor"], True)])
    assert not gates[0]["success"]
    assert gates[0]["payload"]["error_code"] == "invalid-gate-batch"
