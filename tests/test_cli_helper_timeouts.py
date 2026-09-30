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
    script = tmp_path / "stalled.py"
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
    assert "timed_out=True" in message and str(script) in message
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
