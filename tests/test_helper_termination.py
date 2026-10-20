"""Host command limits and stops reach the check processes Forge owns."""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from forge_test_helpers import ROOT, load_zagrosi_module
from test_compact_plan import make_plan


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
@pytest.mark.parametrize("launcher", ["zagrosi_skills.py", "deep_skills.py"])
def test_sigterm_stops_owned_child_group(tmp_path, launcher):
    started, finished = tmp_path / "started", tmp_path / "finished"
    child = ("import pathlib, sys, time; pathlib.Path(sys.argv[1]).touch(); "
             "time.sleep(3); pathlib.Path(sys.argv[2]).touch()")
    adapter = tmp_path / "adapter.json"
    adapter.write_text(json.dumps({"argv": [sys.executable, "-c", child, str(started), str(finished)]}))
    packet = tmp_path / "packet.md"
    packet.write_text("Review this packet.")
    helper = subprocess.Popen([sys.executable, str(ROOT / "scripts" / launcher), "provider-review", "--provider", "probe",
                               "--adapter", str(adapter), "--input", str(packet),
                               "--output", str(tmp_path / "review.json"), "--timeout", "30"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while not started.exists():
            assert helper.poll() is None and time.monotonic() < deadline
            time.sleep(.05)
        helper.send_signal(signal.SIGTERM)
        assert helper.wait(timeout=10) == 143
    finally:
        if helper.poll() is None:
            helper.kill()
    time.sleep(4)
    assert not finished.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_sigterm_during_in_process_gate_stops_the_helper(tmp_path):
    # Plan gates run in-process and record SystemExit codes; a host stop must not become a gate result.
    driver = "\n".join([
        "import importlib, os, signal, sys",
        "sys.path.insert(0, sys.argv[1])",
        "import zagrosi_skills",
        "validation = importlib.import_module(zagrosi_skills.load_runtime().MODULE_NAMES['forge/validation.py'])",
        "def stop(args):",
        "    os.kill(os.getpid(), signal.SIGTERM)",
        "    raise AssertionError('SIGTERM did not interrupt the gate')",
        "validation.lint_plan = stop",
        "sys.argv = ['zagrosi_skills.py', 'postflight', '--phase', 'plan', '--planning-dir', sys.argv[2], '--depth', 'lean']",
        "raise SystemExit(zagrosi_skills.run_program())",
    ])
    planning = make_plan(tmp_path / "planning")
    result = subprocess.run([sys.executable, "-c", driver, str(ROOT / "scripts"), str(planning)],
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 143, result.stdout + result.stderr


def test_verify_default_fits_host_limit():
    forge = load_zagrosi_module()
    args = forge.cli.build_parser().parse_args(["implement-verify", "--planning-dir", "plan"])
    assert args.timeout < 600
