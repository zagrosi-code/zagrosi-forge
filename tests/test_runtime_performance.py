"""Verify removed work and gate isolation without timing performance assertions."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from forge_test_helpers import load_zagrosi_module, write_lean_plan_fixture

requires_interval_timer = pytest.mark.skipif(
    not all(hasattr(signal, name) for name in ("setitimer", "getitimer", "ITIMER_REAL", "SIGALRM")),
    reason="The in-process gate path requires Unix interval timers.",
)


@pytest.fixture
def forge():
    return load_zagrosi_module()


@pytest.fixture
def plan(tmp_path):
    return write_lean_plan_fixture(tmp_path / "plan")


def call_gate(forge, monkeypatch, capsys, tmp_path, command, **kwargs):
    def status(_args):
        return forge.output.print_json(forge.gates.run_internal_gate(command[0], command, **kwargs))

    monkeypatch.setattr(forge.status, "status", status)
    assert forge.entrypoint.main(["status", "--path", str(tmp_path)]) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("phase", ["preflight", "postflight"])
@requires_interval_timer
def test_local_plan_flights_do_not_start_python_children(forge, monkeypatch, capsys, plan, phase):
    def forbidden(*_args, **_kwargs):
        pytest.fail("A local plan gate started a child process")

    monkeypatch.setattr(subprocess, "run", forbidden)
    args = [phase, "--phase", "plan"]
    args += ["--file", str(plan / "spec.md")] if phase == "preflight" else ["--planning-dir", str(plan)]
    assert forge.entrypoint.main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"]
    assert all(gate["success"] for gate in payload["gates"])


@pytest.mark.parametrize("strict_failure", [False, True])
def test_local_gate_matches_process_payload(forge, monkeypatch, capsys, tmp_path, plan, strict_failure):
    command = ["lint-plan", "--planning-dir", str(tmp_path if strict_failure else plan), "--strict"]
    expected = forge.gates.run_internal_gate("lint-plan", command)
    actual = call_gate(forge, monkeypatch, capsys, tmp_path, command)
    assert actual == expected
    assert actual["success"] is not strict_failure


def test_pretty_flight_and_following_json_are_isolated(forge, capsys, plan):
    assert forge.entrypoint.main(["postflight", "--phase", "plan", "--planning-dir", str(plan), "--pretty"]) == 0
    assert not capsys.readouterr().out.startswith("{")
    assert forge.entrypoint.main(["status", "--path", str(plan)]) == 0
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert json.loads(output)["success"]
    assert forge.session._CLI_CONTEXT.get() is None
    assert forge.session._GATE_STREAMS.get() is None


@pytest.mark.parametrize("failure", ["invalid-args", "exception", "exit-after-output", "no-output"])
@requires_interval_timer
def test_local_gate_failures_restore_streams_cwd_and_timer(forge, monkeypatch, capsys, tmp_path, failure):
    original_cwd, original_stdout, original_stderr = Path.cwd(), sys.stdout, sys.stderr
    original_handler = signal.getsignal(signal.SIGALRM)

    def doctor(_args):
        if failure == "no-output":
            return 0
        forge.output.print_json({"success": True})
        if failure == "exit-after-output":
            raise SystemExit(3)
        raise RuntimeError("test command failed")

    monkeypatch.setattr(forge.doctor, "doctor", doctor)
    command = ["doctor", "--depth", "invalid"] if failure == "invalid-args" else ["doctor"]
    gate = call_gate(forge, monkeypatch, capsys, tmp_path, command)
    assert not gate["success"]
    assert Path.cwd() == original_cwd
    assert sys.stdout is original_stdout and sys.stderr is original_stderr
    assert signal.getsignal(signal.SIGALRM) is original_handler
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert forge.session._CLI_CONTEXT.get() is None
    assert forge.session._GATE_STREAMS.get() is None


@requires_interval_timer
def test_local_gate_timeout_retains_failure_contract(forge, monkeypatch, capsys, tmp_path):
    set_timer = signal.setitimer
    original_handler = signal.getsignal(signal.SIGALRM)

    def short_timer(kind, seconds, interval=0):
        return set_timer(kind, min(seconds, 0.01), interval)

    def doctor(_args):
        time.sleep(1)
        return forge.output.print_json({"success": True})

    monkeypatch.setattr(signal, "setitimer", short_timer)
    monkeypatch.setattr(forge.doctor, "doctor", doctor)
    gate = call_gate(forge, monkeypatch, capsys, tmp_path, ["doctor"])
    assert not gate["success"]
    assert gate["returncode"] == 124
    assert gate["payload"]["error_code"] == "gate-timeout"
    assert gate["payload"]["timeout_seconds"] == 120
    assert signal.getsignal(signal.SIGALRM) is original_handler
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


@pytest.mark.parametrize("kind", [
    "custom-cwd", "timeout", "writer", "relative-path",
    pytest.param("alarm", marks=requires_interval_timer),
])
def test_unsafe_or_explicit_gate_calls_keep_process_semantics(forge, monkeypatch, capsys, tmp_path, kind):
    calls = []

    def process(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"success":true}\n', "")

    monkeypatch.setattr(subprocess, "run", process)
    command, kwargs = ["doctor"], {}
    if kind == "custom-cwd":
        kwargs["cwd"] = tmp_path
    elif kind == "timeout":
        kwargs["timeout_seconds"] = 9
    elif kind == "writer":
        command = ["traceability", "--planning-dir", str(tmp_path), "--write"]
    elif kind == "relative-path":
        command = ["status", "--path", "."]
    else:
        signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        gate = call_gate(forge, monkeypatch, capsys, tmp_path, command, **kwargs)
        assert gate["success"]
        assert len(calls) == 1
        assert calls[0][0][2:] == command
        assert calls[0][1]["timeout"] == (9 if kind == "timeout" else 120)
        if kind == "custom-cwd":
            assert calls[0][1]["cwd"] == tmp_path
        if kind == "alarm":
            assert signal.getitimer(signal.ITIMER_REAL)[0] > 0
    finally:
        if kind == "alarm":
            signal.setitimer(signal.ITIMER_REAL, 0)


@requires_interval_timer
def test_mixed_batch_retains_concurrent_processes_and_input_order(forge, monkeypatch, capsys, tmp_path):
    barrier = threading.Barrier(2)
    threads = set()

    def process(argv, **_kwargs):
        assert argv[2] != "doctor"
        threads.add(threading.get_ident())
        assert forge.session._CLI_CONTEXT.get() is None
        barrier.wait(timeout=2)
        return subprocess.CompletedProcess(argv, 0, '{"success":true}\n', "")

    def doctor(_args):
        assert threading.current_thread() is threading.main_thread()
        return forge.output.print_json({"success": True})

    def status(_args):
        gates = forge.gates.run_internal_gate_batch([
            ("doctor", ["doctor"], True),
            ("report", ["report", "--planning-dir", str(tmp_path)], False),
            ("forge-score", ["forge-score", "--planning-dir", str(tmp_path), "--write-history"], True),
        ])
        return forge.output.print_json({"gates": gates})

    monkeypatch.setattr(subprocess, "run", process)
    monkeypatch.setattr(forge.status, "status", status)
    monkeypatch.setattr(forge.doctor, "doctor", doctor)
    assert forge.entrypoint.main(["status", "--path", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [gate["name"] for gate in payload["gates"]] == ["doctor", "report", "forge-score"]
    assert len(threads) == 2


def test_nonmain_invocation_uses_processes_without_changing_signal_handlers(forge, monkeypatch, capsys, plan):
    calls = []

    def process(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{"success":true}\n', "")

    def forbidden(*_args, **_kwargs):
        pytest.fail("A worker thread attempted to manage process signal handlers")

    monkeypatch.setattr(subprocess, "run", process)
    monkeypatch.setattr(signal, "setitimer", forbidden, raising=False)
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(forge.entrypoint.main, ["postflight", "--phase", "plan", "--planning-dir", str(plan)]).result()
    assert result == 0
    assert calls
    assert json.loads(capsys.readouterr().out)["success"]
    assert forge.session._CLI_CONTEXT.get() is None


def test_platform_without_interval_timer_uses_process_fallback(forge, monkeypatch, capsys, tmp_path):
    calls = []

    def process(argv, **kwargs):
        calls.append(kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, '{"success":true}\n', "")

    monkeypatch.delattr(signal, "setitimer", raising=False)
    monkeypatch.setattr(subprocess, "run", process)
    assert call_gate(forge, monkeypatch, capsys, tmp_path, ["doctor"])["success"]
    assert calls == [120]


@requires_interval_timer
def test_local_postflight_builds_one_parser_and_reads_each_file_once(forge, monkeypatch, capsys, plan):
    reads, parsers = Counter(), []
    original_read, original_parser = Path.read_text, forge.cli.build_parser

    def read(path, *args, **kwargs):
        reads[path.absolute()] += 1
        return original_read(path, *args, **kwargs)

    def parser():
        parsers.append(True)
        return original_parser()

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(forge.cli, "build_parser", parser)
    assert forge.entrypoint.main(["postflight", "--phase", "plan", "--planning-dir", str(plan)]) == 0
    assert json.loads(capsys.readouterr().out)["success"]
    assert len(parsers) == 1
    assert reads[plan / "codex-plan.md"] == 1
    assert reads[plan / "sections" / "section-01-lean-default.md"] == 1


def test_read_cache_detects_same_length_rewrite_and_does_not_cross_invocations(forge, monkeypatch, capsys, tmp_path):
    path = tmp_path / "input.txt"
    path.write_text("old")
    reads = []
    original_read = Path.read_text

    def read(target, *args, **kwargs):
        if target == path:
            reads.append(True)
        return original_read(target, *args, **kwargs)

    def status(_args):
        assert forge.storage.read_text(path) == forge.storage.read_text(path) == "old"
        old_stat = path.stat()
        path.write_text("new")
        os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        assert forge.storage.read_text(path) == "new"
        return forge.output.print_json({"success": True})

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(forge.status, "status", status)
    assert forge.entrypoint.main(["status", "--path", str(tmp_path)]) == 0
    assert len(reads) == 2
    path.write_text("old")
    assert forge.entrypoint.main(["status", "--path", str(tmp_path)]) == 0
    assert len(reads) == 4
    assert forge.session._CLI_CONTEXT.get() is None
    capsys.readouterr()


def test_detached_handoff_retains_exact_shape_and_bypasses_runtime_cache(forge, monkeypatch, capsys, tmp_path):
    section = next(iter(forge.detached_contract.HANDOFF_SECTION_CONTRACTS))

    def handoff(_args):
        assert forge.session._CLI_CONTEXT.get()["texts"] is None
        assert not forge.session._CLI_CONTEXT.get()["local_gates"]
        return forge.output.print_json({"success": True})

    monkeypatch.setattr(forge.handoff, "detached_implement_evidence_handoff", handoff)
    command = ["implement-evidence-handoff", "--implementation-root", str(tmp_path), "--section", section]
    assert forge.entrypoint.main([*command, "--pretty"]) == 2
    assert capsys.readouterr().out == ""
    assert forge.entrypoint.main(command) == 0
    assert json.loads(capsys.readouterr().out)["success"]
    assert forge.session._CLI_CONTEXT.get() is None
