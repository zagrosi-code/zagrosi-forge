"""Cancellation controls: real bounded children and synthetic Docker ownership.

The fake Docker CLI never runs containers or establishes isolation qualification.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import coding_trial_docker as docker
import coding_trial_isolation as isolation
import coding_trial_process as process
from coding_trial_inventory import inventory
from test_trial_suite_docker import (
    assert_every_owned_container_removed, cli_calls, docker_lifecycle, docker_preflight,
)
from test_trial_suite_isolation import attempt, clean_environment


def test_wrapper_forwards_only_explicit_cancellation(tmp_path, monkeypatch):
    calls, event, sentinel = [], threading.Event(), {"result": "unchanged"}
    def capture(*args, **kwargs):
        calls.append(kwargs)
        return sentinel
    monkeypatch.setattr(process, "_execute", capture)
    assert process.execute([sys.executable], tmp_path, cancel_event=event) is sentinel
    assert calls[-1]["cancel_event"] is event
    assert process.execute([sys.executable], tmp_path, cancel_event=None) is sentinel
    assert "cancel_event" not in calls[-1]


def test_pre_cancelled_child_never_launches(tmp_path, monkeypatch):
    event = threading.Event()
    event.set()
    def forbidden(*args, **kwargs):
        pytest.fail("A pre-cancelled command must not create a process")
    monkeypatch.setattr(process._execute.__globals__["subprocess"], "Popen", forbidden)
    result = process.execute([sys.executable, "-c", "pass"], tmp_path, cancel_event=event)
    assert result["returncode"] == 130 and result["cancelled"] is True
    assert result["timed_out"] is False and result["seconds"] >= 0
    for name in ("stdout", "stderr"):
        assert result[name] == "" and result[name + "_bytes"] == 0
        assert result[name + "_truncated"] is False
    assert "termination_error" not in result


def test_cancellation_during_stdin_preparation_prevents_launch(tmp_path, monkeypatch):
    event = threading.Event()
    globals_ = process._execute.__globals__
    temporary_file = globals_["tempfile"].TemporaryFile
    class CancellingInput:
        def __init__(self):
            self.stream = temporary_file()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def write(self, data):
            result = self.stream.write(data)
            event.set()
            return result
    def forbidden(*args, **kwargs):
        pytest.fail("Cancellation during stdin preparation must prevent Popen")
    monkeypatch.setattr(globals_["tempfile"], "TemporaryFile", CancellingInput)
    monkeypatch.setattr(globals_["subprocess"], "Popen", forbidden)
    result = process.execute([sys.executable, "-c", "pass"], tmp_path,
                             prompt="bounded private request", cancel_event=event)
    assert result["returncode"] == 130 and result["cancelled"] is True and not result["timed_out"]
    assert result["stdout"] == result["stderr"] == ""
    assert result["stdout_bytes"] == result["stderr_bytes"] == 0


@pytest.mark.parametrize("kind,code", [("success", 0), ("nonzero", 23), ("timeout", 124), ("missing", 127)])
@pytest.mark.parametrize("opt_in", [False, True])
def test_absent_and_unset_cancellation_preserve_process_outcomes(tmp_path, kind, code, opt_in):
    scripts = {"success": "print('done')", "nonzero": "raise SystemExit(23)",
               "timeout": "import time; time.sleep(2)"}
    argv = [str(tmp_path / "missing-executable")] if kind == "missing" else [sys.executable, "-c", scripts[kind]]
    options = {"cancel_event": threading.Event()} if opt_in else {}
    result = process.execute(argv, tmp_path, timeout=.1 if kind == "timeout" else 3,
                             env=clean_environment(), inherit_env=False, **options)
    assert result["returncode"] == code
    assert result["timed_out"] is (kind == "timeout")
    assert result.get("cancelled") is (False if opt_in else None)


def test_running_cancellation_retains_bounded_output_and_stops_group(tmp_path):
    event, ready, survivor = threading.Event(), tmp_path / "ready", tmp_path / "survivor"
    child = "import time; from pathlib import Path; time.sleep(1); Path('survivor').write_text('escaped')"
    script = ("import subprocess,sys,time; from pathlib import Path; "
              + (f"subprocess.Popen([sys.executable,'-c',{child!r}]); " if os.name == "posix" else "")
              + "sys.stdout.buffer.write(b'x'*100+b'READY\\n'); sys.stdout.buffer.flush(); "
              "sys.stderr.buffer.write(b'ERR\\n'); sys.stderr.buffer.flush(); "
              "Path('ready').write_text('ready'); time.sleep(5)")
    def cancel_after_ready():
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        event.set()
    watcher = threading.Thread(target=cancel_after_ready)
    watcher.start()
    try:
        result = process.execute([sys.executable, "-c", script], tmp_path, timeout=4,
                                 output_limit=32, cancel_event=event)
    finally:
        event.set()
        watcher.join(timeout=4)
    assert not watcher.is_alive() and ready.exists()
    assert result["returncode"] == 130 and result["cancelled"] is True and not result["timed_out"]
    assert result["stdout"].endswith("READY\n") and result["stdout_bytes"] == 106
    assert result["stdout_truncated"] and result["stderr"] == "ERR\n"
    assert not result.get("termination_error")
    if os.name == "posix":
        # Retained marker distinguishes a surviving owned descendant from a stopped leader.
        time.sleep(1.05)
        assert not survivor.exists()


def test_elapsed_deadline_takes_precedence_over_simultaneous_cancellation(tmp_path, monkeypatch):
    event = threading.Event()
    subprocess = process._execute.__globals__["subprocess"]
    original = subprocess.Popen
    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        event.set()
        return child
    monkeypatch.setattr(subprocess, "Popen", launch)
    result = process.execute([sys.executable, "-c", "import time; time.sleep(2)"], tmp_path,
                             timeout=1e-9, cancel_event=event)
    assert result["returncode"] == 124 and result["timed_out"] is True
    assert result["cancelled"] is False


@pytest.mark.skipif(os.name != "posix", reason="Normal-return group cleanup is POSIX-specific")
def test_cancellation_arriving_during_completed_cleanup_does_not_reclassify_success(tmp_path, monkeypatch):
    event = threading.Event()
    globals_ = process._execute.__globals__
    original = globals_["_terminate"]
    def finish(child):
        result = original(child)
        event.set()
        return result
    monkeypatch.setitem(globals_, "_terminate", finish)
    result = process.execute([sys.executable, "-c", "print('done')"], tmp_path, cancel_event=event)
    assert event.is_set() and result["returncode"] == 0 and result["cancelled"] is False


def owned(shape, event):
    shape["config_path"].write_text(json.dumps(shape["config"]))
    shape["evidence"].mkdir()
    profile = json.loads((shape["suite_root"] / "profile.json").read_text())
    return docker.run_owned(profile, shape["layout"], ["/usr/local/bin/python3", "-c", "pass"],
                            cwd="/workspace", env={}, prompt=None, timeout=2, output_limit=1024,
                            evidence_dir=shape["evidence"], cancel_event=event)


def test_cancelled_preflight_starts_no_docker_command(docker_lifecycle):
    event = threading.Event()
    event.set()
    with pytest.raises(ValueError, match="^execution-cancelled:"):
        owned(docker_lifecycle, event)
    assert cli_calls(docker_lifecycle) == []


@pytest.mark.parametrize("stage", ["context", "create", "start"])
def test_cancellation_stops_future_work_and_preserves_exact_owned_cleanup(docker_lifecycle, monkeypatch, stage):
    shape, event, observed = docker_lifecycle, threading.Event(), []
    original = docker.execute
    def execute(argv, *args, **kwargs):
        result = original(argv, *args, **kwargs)
        observed.append((argv, kwargs.get("cancel_event")))
        if stage in argv:
            event.set()
            if stage == "start":
                result.update(returncode=130, cancelled=True, timed_out=False)
        return result
    monkeypatch.setattr(docker, "execute", execute)
    if stage == "start":
        result = owned(shape, event)
        assert result["process"]["cancelled"] and result["process"]["returncode"] == 130
        assert result["lifecycle"]["status"] == "verified"
    else:
        with pytest.raises(ValueError, match="^execution-cancelled:"):
            owned(shape, event)
    calls = cli_calls(shape)
    if stage == "context":
        assert [row["operation"] for row in calls] == ["context.inspect"]
        assert observed[0][1] is event
    else:
        lifecycles = assert_every_owned_container_removed(shape, calls)
        assert lifecycles and all(row["status"] == "verified" for row in lifecycles)
        for argv, forwarded in observed:
            if "create" in argv or "container" in argv or "rm" in argv or "exec" in argv:
                assert forwarded is None, "Creation recovery and cleanup must finish despite cancellation"
        assert any("start" in argv and forwarded is event for argv, forwarded in observed) is (stage == "start")
        assert any(row["operation"] == "start" for row in calls) is (stage == "start")


@pytest.mark.parametrize("stage", ["read", "detached"])
def test_qualification_cancellation_restores_scratch_and_finishes_started_control(docker_preflight, monkeypatch, stage):
    shape, event = docker_preflight, threading.Event()
    original, controls = process.execute, []
    before = inventory(Path(shape["roots"]["workspace"]))
    def execute(argv, *args, **kwargs):
        result = original(argv, *args, **kwargs)
        detached = isolation.DETACHED_PROBE in argv
        controls.append((detached, kwargs.get("cancel_event")))
        if detached == (stage == "detached"):
            event.set()
        return result
    monkeypatch.setattr(process, "execute", execute)
    def forbidden(*args, **kwargs):
        pytest.fail("No candidate container may start after control cancellation")
    monkeypatch.setattr(docker, "run_owned", forbidden)
    with pytest.raises(ValueError, match="^execution-cancelled:"):
        isolation.qualify_profile(shape["suite"], shape["suite_root"], shape["layout"],
                                  shape["evidence"], roots=shape["roots"], cancel_event=event)
    assert inventory(Path(shape["roots"]["workspace"])) == before
    assert controls[0] == (False, event)
    assert any(detached for detached, _ in controls) is (stage == "detached")
    if stage == "detached":
        assert controls[-1] == (True, None)
        control = shape["evidence"] / "unrestricted-child"
        assert (control / "survived").read_text() == (control / "release").read_text()
    assert not any(row["operation"] == "create" for row in cli_calls(shape))


def test_cancellation_after_fresh_qualification_prevents_worker_launch(docker_preflight, monkeypatch):
    shape, event, calls = docker_preflight, threading.Event(), []
    def qualify(*args, **kwargs):
        calls.append(kwargs["cancel_event"])
        event.set()
        return {"checks": [{"id": "synthetic-control-only", "status": "passed"}]}
    def forbidden(*args, **kwargs):
        pytest.fail("Cancelled execution must not launch after qualification")
    monkeypatch.setattr(isolation, "qualify_profile", qualify)
    monkeypatch.setattr(docker, "run_owned", forbidden)
    with pytest.raises(ValueError, match="^execution-cancelled:"):
        isolation.execute_isolated(shape["suite"], shape["suite_root"], shape["layout"],
                                   ["/usr/local/bin/python3", "-c", "pass"], roots=shape["roots"],
                                   cwd="/workspace", env={}, prompt=None, timeout=2, output_limit=1024,
                                   evidence_dir=shape["evidence"], cancel_event=event)
    assert calls == [event]
