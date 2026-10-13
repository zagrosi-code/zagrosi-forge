"""CLI test boundaries fail usefully and clean up stalled commands."""

import json
import os
import sys
import time

import pytest

import forge_test_helpers as helpers
from test_claude_plugin import run as run_claude


@pytest.mark.parametrize("helper", ["run_cmd", "run_raw", "run_script_raw", "run_text", "claude"])
def test_cli_helpers_bound_stalls_and_keep_diagnostics(tmp_path, monkeypatch, helper):
    # A literal backslash on POSIX also exercises Windows diagnostic escaping.
    script = tmp_path / r"nested\stalled.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "import sys,time\nprint('stdout progress', flush=True)\n"
        "print('stderr detail', file=sys.stderr, flush=True)\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(helpers, "SCRIPT", script)
    started = time.monotonic()
    with pytest.raises(AssertionError) as failure:
        if helper == "claude":
            run_claude([sys.executable, str(script)], tmp_path, success=False, timeout=1)
        else:
            getattr(helpers, helper)(*([script] if helper == "run_script_raw" else []), cwd=tmp_path, timeout=1)
    message = str(failure.value)
    assert "timed_out=True" in message and repr(str(script)) in message
    assert "stdout progress" in message and "stderr detail" in message
    assert time.monotonic() - started < 10


def test_helper_exact_environment_and_executor_default_inheritance(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_TEST_PARENT_ONLY", "parent")
    env = {key: value for key, value in os.environ.items() if key != "FORGE_TEST_PARENT_ONLY"}
    env["FORGE_TEST_CHILD"] = "child"
    command = [sys.executable, "-c", "import json,os; print(json.dumps([os.getenv('FORGE_TEST_PARENT_ONLY'), os.getenv('FORGE_TEST_CHILD')]))"]
    exact = helpers.run_process(command, cwd=tmp_path, env=env)
    assert exact.returncode == 0 and json.loads(exact.stdout) == [None, "child"]
    inherited = helpers._command_executor()(command, tmp_path, env={"FORGE_TEST_CHILD": "child"})
    assert inherited["returncode"] == 0 and json.loads(inherited["stdout"]) == ["parent", "child"]


def test_helper_retains_nonzero_status_and_complete_output(tmp_path):
    command = [sys.executable, "-c", "import os,sys; os.write(1,b'failed output\\r\\n'); os.write(2,b'failure detail\\r\\n'); sys.exit(3)"]
    result = helpers.run_process(command, cwd=tmp_path)
    assert result.args == command and result.returncode == 3
    assert result.stdout == "failed output\n" and result.stderr == "failure detail\n"


def test_helper_refuses_truncated_output_even_on_success(tmp_path):
    command = [sys.executable, "-c", "print('x' * (2 * 1024 * 1024)); print('retained tail')"]
    with pytest.raises(AssertionError, match="truncated=True") as failure:
        helpers.run_process(command, cwd=tmp_path)
    assert "retained tail" in str(failure.value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group cleanup")
def test_helper_timeout_stops_descendant_writes(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    release = tmp_path / "release-child"
    child = (f"import pathlib,time,sys; p=pathlib.Path(sys.argv[1]); stop=pathlib.Path({str(release)!r});\n"
             "while not stop.exists():\n p.write_text(str(time.time_ns())); time.sleep(.01)")
    leader = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); time.sleep(60)"
    command = [sys.executable, "-c", leader, child, str(heartbeat)]
    try:
        with pytest.raises(AssertionError, match="timed_out=True"):
            helpers.run_process(command, cwd=tmp_path, timeout=1)
        before = heartbeat.read_bytes()
        time.sleep(.1)
        assert heartbeat.read_bytes() == before
    finally:
        release.touch()


@pytest.fixture
def reader_setup_resources(tmp_path, monkeypatch):
    from types import SimpleNamespace

    executor = helpers.load_zagrosi_module().child_process
    real_popen, real_threading = executor.subprocess.Popen, executor.threading
    resources = SimpleNamespace(
        executor=executor,
        command=[sys.executable, "-B", "-c", "import time; time.sleep(10)"],
        children=[], readers=[], launches=[], gates=[], real_event=real_threading.Event,
    )

    class ReaderThreads:
        def __getattr__(self, name):
            return getattr(real_threading, name)

        def Event(self, *args, **kwargs):
            gate = real_threading.Event(*args, **kwargs)
            resources.gates.append(gate)
            return gate

        def Thread(self, *args, **kwargs):
            thread = real_threading.Thread(*args, **kwargs)
            resources.readers.append(thread)
            return thread

    def spawn(argv, *args, **kwargs):
        owned = argv == resources.command
        if owned:
            resources.launches.append(argv)
        child = real_popen(argv, *args, **kwargs)
        if owned:  # Windows taskkill is a separate executor-owned helper.
            resources.children.append(child)
        return child

    # Keep real Thread internals on their original threading module. Only Events
    # allocated directly by this executor are available for fixture cleanup.
    monkeypatch.setattr(executor, "threading", ReaderThreads())
    monkeypatch.setattr(executor.subprocess, "Popen", spawn)
    try:
        yield resources
    finally:
        # Release only fixture-owned gates after the lifecycle assertions. This
        # cannot make a leaked reader pass its pre-cleanup stopped-thread check.
        for gate in resources.gates:
            gate.set()
        for child in resources.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        for thread in resources.readers:
            if thread.ident is not None:
                thread.join(timeout=5)
        for child in resources.children:
            child.stdout.close()
            child.stderr.close()
        assert all(not thread.is_alive() for thread in resources.readers)


@pytest.mark.parametrize("operation,reader_number", [
    ("construct", 1), ("construct", 2), ("start", 1), ("start", 2), ("after_start", 2),
])
def test_reader_initialization_failure_reaps_child_and_preserves_exception(
        tmp_path, monkeypatch, operation, reader_number, reader_setup_resources):
    resources = reader_setup_resources
    executor = resources.executor
    real_thread = executor.threading.Thread
    error_type = KeyboardInterrupt if operation == "after_start" else RuntimeError
    failure = error_type(f"injected {operation} failure for reader {reader_number}")
    constructed = 0

    def reader(*args, **kwargs):
        nonlocal constructed
        constructed += 1
        number = constructed
        if operation == "construct" and number == reader_number:
            raise failure
        thread = real_thread(*args, **kwargs)
        real_start = thread.start

        def start():
            if operation == "start" and number == reader_number:
                raise failure
            real_start()
            if operation == "after_start" and number == reader_number:
                raise failure

        monkeypatch.setattr(thread, "start", start)
        return thread

    monkeypatch.setattr(executor.threading, "Thread", reader)
    with pytest.raises(error_type) as caught:
        executor.execute(resources.command, tmp_path, timeout=1)
    assert caught.value is failure
    assert resources.launches == [] and resources.children == []
    assert all(not thread.is_alive() for thread in resources.readers)


def test_missing_executable_stops_created_readers(tmp_path, reader_setup_resources):
    resources = reader_setup_resources
    resources.command = [str(tmp_path / "missing-executable")]
    result = resources.executor.execute(resources.command, tmp_path, timeout=1)
    assert result["returncode"] == 127 and result["timed_out"] is False
    assert result["stdout"] == "" and result["stderr"]
    assert result["seconds"] >= 0
    assert set(result) == {"returncode", "seconds", "stdout", "stderr", "timed_out"}
    assert resources.children == []
    assert all(not thread.is_alive() for thread in resources.readers)


def test_cancellation_during_reader_setup_prevents_launch(
        tmp_path, monkeypatch, reader_setup_resources):
    resources = reader_setup_resources
    executor = resources.executor
    real_thread = executor.threading.Thread
    event = resources.real_event()

    def reader(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        number = len(resources.readers)
        real_start = thread.start

        def start():
            real_start()
            if number == 2:
                event.set()

        monkeypatch.setattr(thread, "start", start)
        return thread

    monkeypatch.setattr(executor.threading, "Thread", reader)
    result = executor.execute(resources.command, tmp_path, timeout=1, cancel_event=event)
    assert event.is_set()
    assert result["returncode"] == 130 and result["cancelled"] is True
    assert result["timed_out"] is False and result["seconds"] >= 0
    for name in ("stdout", "stderr"):
        assert result[name] == "" and result[name + "_bytes"] == 0
        assert result[name + "_truncated"] is False
    assert "termination_error" not in result
    assert resources.launches == [] and resources.children == []
    assert all(not thread.is_alive() for thread in resources.readers)
