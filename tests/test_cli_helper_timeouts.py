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


@pytest.mark.parametrize("operation,reader_number", [
    ("construct", 1), ("construct", 2), ("start", 1), ("start", 2), ("after_start", 2),
])
def test_reader_initialization_failure_reaps_child_and_preserves_exception(
        tmp_path, monkeypatch, operation, reader_number):
    executor = helpers.load_zagrosi_module().child_process
    real_popen, real_thread = executor.subprocess.Popen, executor.threading.Thread
    command = [sys.executable, "-B", "-c", "import time; time.sleep(10)"]
    error_type = KeyboardInterrupt if operation == "after_start" else RuntimeError
    failure = error_type(f"injected {operation} failure for reader {reader_number}")
    children, readers = [], []
    constructed = 0

    def spawn(argv, *args, **kwargs):
        child = real_popen(argv, *args, **kwargs)
        if argv == command:  # Windows taskkill is a separate executor-owned helper.
            children.append(child)
        return child

    def reader(*args, **kwargs):
        nonlocal constructed
        constructed += 1
        number = constructed
        if operation == "construct" and number == reader_number:
            raise failure
        thread = real_thread(*args, **kwargs)
        readers.append(thread)
        real_start = thread.start

        def start():
            if operation == "start" and number == reader_number:
                raise failure
            real_start()
            if operation == "after_start" and number == reader_number:
                raise failure

        monkeypatch.setattr(thread, "start", start)
        return thread

    monkeypatch.setattr(executor.subprocess, "Popen", spawn)
    monkeypatch.setattr(executor.threading, "Thread", reader)
    try:
        with pytest.raises(error_type) as caught:
            executor.execute(command, tmp_path, timeout=1)
        assert caught.value is failure
        assert len(children) == 1 and children[0].returncode is not None
        assert all(not thread.is_alive() for thread in readers)
        assert all(stream.closed for stream in (children[0].stdout, children[0].stderr))
    finally:
        # RED must not leave the real fixture child or its pipe readers behind.
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        for thread in readers:
            if thread.ident is not None:
                thread.join(timeout=5)
        for child in children:
            child.stdout.close()
            child.stderr.close()
        assert all(not thread.is_alive() for thread in readers)
