"""Fresh copied host runtimes resume a checkpoint after its owning process dies.

These tests cover Forge persistence and host-independent packages, not model behavior.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from test_claude_plugin import helper, package, run
from test_compact_plan import SECTION, make_plan


@pytest.mark.parametrize("hosts", [("codex", "claude"), ("claude", "codex"), ("codex", "codex"), ("claude", "claude")])
@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_interrupted_checkpoint_resumes_across_host_packages(package, tmp_path, hosts, depth):
    # Separate plugin paths reproduce a host switch without needing credentials or model calls.
    first = package.rename(tmp_path / f"{hosts[0]} initial plugin")
    second = tmp_path / f"{hosts[1]} resumed plugin"
    shutil.copytree(first, second)
    target = tmp_path / "target repo"
    planning = make_plan(target / ".planning", depth)
    (target / "src").mkdir()
    (target / "tests").mkdir()
    source = target / "src/labels.py"
    source.write_text("def normalize(value):\n    return value\n")
    (target / "tests/test_labels.py").write_text(
        "import unittest\nfrom src.labels import normalize\n"
        "class Tests(unittest.TestCase):\n"
        "    def test_trim_edges(self):\n        self.assertEqual(normalize(' Ada '), 'Ada')\n")
    notes = target / "user-notes.txt"
    notes.write_text("Retain this unrelated user edit.\n")
    original_notes = notes.read_bytes()
    run(helper(first, "implement-setup", "--sections-dir", planning / "sections", "--target-dir", target,
               "--depth", depth, "--flight", "off"), target)
    test = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
    red = subprocess.run(test, cwd=target, capture_output=True, text=True, timeout=10)
    assert red.returncode != 0 and "FAIL" in red.stderr
    progress = helper(first, "implement-progress", "--planning-dir", planning, "--section", SECTION,
                      "--stage", "red", "--command", "python -m unittest discover -s tests", "--result", red.stderr)
    marker = tmp_path / "checkpoint-written"
    owner_script = (
        "import json, pathlib, subprocess, sys, time; "
        "subprocess.run(json.loads(sys.argv[1]), check=True, timeout=10); "
        "pathlib.Path(sys.argv[2]).write_text('saved'); time.sleep(60)"
    )
    owner = subprocess.Popen([sys.executable, "-c", owner_script, json.dumps(progress), str(marker)],
                             cwd=target, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 12
        while not marker.exists() and owner.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists(), "The owner failed to persist its checkpoint"
        assert owner.poll() is None
    finally:
        if owner.poll() is None:
            owner.terminate()
        owner.communicate(timeout=5)
    assert owner.returncode != 0
    log = planning / "implementation/forge-progress.json"
    original_event = json.loads(log.read_text())["events"][0]
    for _ in range(2):
        entry = run(helper(second, "next-section", "--planning-dir", planning), target)
        assert entry["resume"]["evidence_current"]
        assert entry["resume"]["stage"] == "red" and entry["resume"]["result"] == red.stderr
        command = entry["commands"]["record"]
        assert command[:2] == helper(second)
        assert command[command.index("--depth") + 1] == depth
    assert json.loads(log.read_text())["events"] == [original_event]
    source.write_text("def normalize(value):\n    return value.strip()\n")
    stale = run(helper(second, "next-section", "--planning-dir", planning), target)
    assert not stale["resume"]["evidence_current"] and "code:src/labels.py" in stale["resume"]["changed_inputs"]
    green = subprocess.run(test, cwd=target, capture_output=True, text=True, timeout=10)
    assert green.returncode == 0, green.stderr
    run(helper(second, "implement-progress", "--planning-dir", planning, "--section", SECTION,
               "--stage", "green", "--command", "python -m unittest discover -s tests", "--result", green.stderr), target)
    resumed = run(helper(second, "next-section", "--planning-dir", planning), target)
    assert resumed["resume"]["evidence_current"] and resumed["next_action"] == f"review {SECTION}"
    events = json.loads(log.read_text())["events"]
    assert len(events) == 2 and events[0] == original_event
    assert notes.read_bytes() == original_notes
