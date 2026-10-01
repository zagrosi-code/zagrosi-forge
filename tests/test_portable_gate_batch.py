"""A verified portable worker shares read analyses without losing isolation."""
import io
import json
import os
import signal
import subprocess
import sys
from argparse import Namespace
from collections import Counter
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


@pytest.mark.parametrize("phase", ["preflight", "postflight"])
@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_platform_without_timers_batches_entire_plan_flight(forge, plan, monkeypatch, capsys, phase, depth):
    config_path = plan / "zagrosi_plan_config.json"
    config = json.loads(config_path.read_text())
    config_path.write_text(json.dumps({**config, "depth_mode": depth}))
    expected_args = [phase, "--phase", "plan", "--planning-dir", str(plan), "--file", str(plan / "spec.md"),
                     "--target-dir", str(plan.parent), "--full-output"]
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
    worker = [sys.executable, "-c", "import time; time.sleep(30)"]

    def counted(*args, **kwargs):
        processes.append(popen(*args, **kwargs))
        return processes[-1]

    def stalled(_argv, workspace, **kwargs):
        assert kwargs["timeout"] == 120
        return execute(worker, workspace, timeout=.05)

    monkeypatch.setattr(subprocess, "Popen", counted)
    monkeypatch.setattr(forge.child_process, "execute", stalled)
    gates = forge.gates.run_gate_worker([("doctor", ["doctor"], True)])
    assert gates[0]["success"] is False and gates[0]["returncode"] == 124
    assert gates[0]["payload"] == {"error_code": "gate-timeout", "timeout_seconds": 120, "timeout_scope": "batch"}
    assert sum(process.args == worker for process in processes) == 1
    assert all(process.poll() is not None for process in processes)  # Includes Windows taskkill.


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


def plan_jobs(forge, plan, depth="standard", profile="solo"):
    config_path = plan / "zagrosi_plan_config.json"
    config_path.write_text(json.dumps({**json.loads(config_path.read_text()), "depth_mode": depth}))
    return forge.flights._plan_gate_jobs(plan, Namespace(
        depth=depth, profile=profile, strict=True, flight_mode="strict", write_report=False))


def worker_result(forge, jobs, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(jobs)))
    assert forge.entrypoint.main(["gate-batch", "--full-output"]) == 0
    assert forge.session._CLI_CONTEXT.get() is None
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("depth", ["standard", "deep"])
@pytest.mark.parametrize("profile", ["solo", "enterprise"])
def test_worker_scores_completed_analyses_once(forge, plan, monkeypatch, capsys, depth, profile):
    jobs = plan_jobs(forge, plan, depth, profile)
    calls = Counter()
    for module, name in (
        (forge.validation, "plan_analysis"), (forge.validation, "section_analysis"),
        (forge.traceability, "traceability_analysis"), (forge.scoring, "implementation_readiness_analysis"),
    ):
        original = getattr(module, name)

        def counted(*args, _original=original, _name=name, **kwargs):
            calls[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, counted)
    actual = worker_result(forge, jobs, monkeypatch, capsys)
    assert len(calls) == 4 and set(calls.values()) == {1}, calls
    calls.clear()
    monkeypatch.setattr(forge.scoring.FlightScoreInputs, "reusable", lambda *_args: {})
    assert worker_result(forge, jobs, monkeypatch, capsys) == actual
    assert set(calls.values()) == {2}, calls


@pytest.mark.parametrize("change", ["rewrite", "new-artifact", "new-section", "delete-section", "configured-source"])
def test_worker_recomputes_score_after_observed_inputs_change(forge, plan, tmp_path, monkeypatch, capsys, change):
    jobs = plan_jobs(forge, plan)
    score = forge.scoring.forge_score
    payloads, recomputed = [], []
    analyze = forge.scoring.section_findings_for_score
    emit = forge.quality.emit_payload

    def counted(*args):
        recomputed.append(True)
        return analyze(*args)

    def capture(payload, args, exit_code=None):
        if payload.get("gate") == "forge-score":
            payloads.append(payload)
        return emit(payload, args, exit_code)

    def changed_score(args):
        inputs = forge.session._CLI_CONTEXT.get().get("score_inputs")
        assert inputs is not None and inputs.reusable(plan, "standard", 8)
        if change == "rewrite":
            source = plan / "spec.md"
            before = source.stat()
            source.write_text(source.read_text().replace("REQ-001", "REQ-999"))
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        elif change == "new-artifact":
            (plan / "codex-spec.md").write_text("# Replacement spec\n\nREQ-999: new source.\n")
        elif change == "new-section":
            (plan / "sections/section-02-extra.md").write_text("# Extra\n\nREQ-999: orphan section.\n")
        elif change == "delete-section":
            next((plan / "sections").glob("section-*.md")).unlink()
        else:
            external = tmp_path / "new-source.md"
            external.write_text("# External source\n\nREQ-999: new selected source.\n")
            config_path = plan / "zagrosi_plan_config.json"
            config_path.write_text(json.dumps({**json.loads(config_path.read_text()), "initial_file": str(external)}))
        assert not inputs.reusable(plan, "standard", 8)
        return score(args)

    with monkeypatch.context() as scoped:
        scoped.setattr(forge.scoring, "section_findings_for_score", counted)
        scoped.setattr(forge.scoring, "forge_score", changed_score)
        scoped.setattr(forge.quality, "emit_payload", capture)
        worker_result(forge, jobs, scoped, capsys)
    assert len(payloads) == len(recomputed) == 1
    forge.entrypoint.main(["forge-score", "--planning-dir", str(plan), "--depth", "standard", "--strict", "--full-output"])
    assert json.loads(capsys.readouterr().out) == payloads[0]


@pytest.mark.parametrize("mismatch", ["root", "depth", "profile", "order", "missing", "limit", "invalid-depth"])
def test_worker_does_not_reuse_incompatible_gate_batches(forge, plan, tmp_path, monkeypatch, capsys, mismatch):
    jobs = plan_jobs(forge, plan)
    if mismatch in {"root", "depth", "profile"}:
        name = "traceability" if mismatch == "root" else "lint-plan"
        command = next(command for job_name, command, _ in jobs if job_name == name)
        option = {"root": "--planning-dir", "depth": "--depth", "profile": "--profile"}[mismatch]
        value = {"root": str(tmp_path / "missing-plan"), "depth": "lean", "profile": "enterprise"}[mismatch]
        command[command.index(option) + 1] = value
    elif mismatch == "order":
        jobs.insert(0, jobs.pop())
    elif mismatch == "limit":
        command = next(command for name, command, _ in jobs if name == "lint-implementation-readiness")
        command.extend(["--max-files", "2"])
    elif mismatch == "invalid-depth":
        command = next(command for name, command, _ in jobs if name == "lint-plan")
        command[command.index("--depth") + 1] = "invalid"
    else:
        jobs = [job for job in jobs if job[0] != "traceability"]
    seen = []
    score = forge.scoring.forge_score

    def checked_score(args):
        inputs = forge.session._CLI_CONTEXT.get().get("score_inputs")
        assert inputs is None or not inputs.reusable(plan, "standard", 8)
        seen.append(True)
        return score(args)

    monkeypatch.setattr(forge.scoring, "forge_score", checked_score)
    actual = worker_result(forge, jobs, monkeypatch, capsys)
    monkeypatch.setattr(forge.scoring.FlightScoreInputs, "reusable", lambda *_args: {})
    assert worker_result(forge, jobs, monkeypatch, capsys) == actual
    assert len(seen) == 2
