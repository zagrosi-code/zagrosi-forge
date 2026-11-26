"""Review handoffs through the public CLI in a real Git checkout."""
import json
import os
import subprocess
import sys

import pytest

from forge_test_helpers import SCRIPT
from test_group_binding import produced, workspace


def forge(*args, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env=env)


@pytest.mark.parametrize(("outcome", "returncode"), [("success", 0), ("failure", 7), ("timeout", 124)])
def test_receipt_without_output_keeps_the_execution_for_both_consumers(workspace, tmp_path, outcome, returncode):
    repo, consume, previous = workspace
    handoff = tmp_path / "handoff.json"
    handoff.write_text(json.dumps(previous), encoding="utf-8")
    sentinel = "private verification output 27af6"
    message = tmp_path / "message.txt"
    message.write_text(sentinel, encoding="utf-8")
    child = """\
import sys
import time
from pathlib import Path

message = Path(sys.argv[1]).read_text(encoding="utf-8")
print(message, flush=True)
print(message, file=sys.stderr, flush=True)
if sys.argv[2] == "timeout":
    time.sleep(60)
sys.exit(7 if sys.argv[2] == "failure" else 0)
"""
    argv = [sys.executable, "-c", child, str(message), outcome]
    assert all(sentinel not in arg for arg in argv)
    verify = ["section-group", "verify", "--target-dir", repo, "--handoff", handoff,
              "--timeout", "5" if outcome == "timeout" else "30"]
    full = forge(*verify, "--", *argv)
    assert full.returncode == int(returncode != 0), full.stderr
    logged = json.loads(full.stdout)["verification"]
    assert sentinel in logged["stdout"] and sentinel in logged["stderr"]

    compact = forge(*verify, "--omit-output", "--", *argv)
    assert compact.returncode == int(returncode != 0), compact.stderr
    assert sentinel not in compact.stdout and sentinel not in compact.stderr
    receipt = json.loads(compact.stdout)
    observed = receipt["verification"]
    assert "stdout" not in observed and "stderr" not in observed
    assert observed["command"] == argv and observed["returncode"] == returncode
    assert observed["timed_out"] == (outcome == "timeout")
    assert receipt["history"]["group_check"]["passed"] == (outcome == "success")

    delivered, measured = consume(receipt)
    assert delivered[0] == measured[0] == 0, (delivered, measured)
    ledger = repo / ".git" / "forge-delivery.json"
    saved = ledger.read_bytes()
    assert consume(receipt)[0][0] == 0
    assert ledger.read_bytes() == saved
    path = tmp_path / "compact.json"
    path.write_text(compact.stdout, encoding="utf-8")
    replay = forge("build-report", "--planning-dir", repo / receipt["handoff"]["plan_path"],
                   "--group", path, "--group", path)
    assert replay.returncode == 0, replay.stderr
    measurements = json.loads(replay.stdout)["groups"][0]["verification"]
    assert measurements == measured[1]["groups"][0]["verification"]
    assert measurements["observed_checks"] == 1


def test_markdown_shows_tested_revision_and_omits_private_command(workspace):
    repo, consume, report = workspace
    assert consume(report)[0][0] == 0
    result = forge("delivery", "status", "--pr", "42", "--format", "markdown", "--target-dir", repo)
    assert result.returncode == 0, result.stderr
    assert report["handoff"]["commit_sha"] in result.stdout
    assert report["handoff"]["sections"][0] in result.stdout
    assert "Next action" in result.stdout and sys.executable not in result.stdout


def test_required_name_is_literal_in_markdown_status_and_next_action(workspace):
    repo, _, _ = workspace
    name = "東京: run `fast` or ``slow`` [docs](https://example.invalid) <b>build</b>"
    required = forge("delivery", "require", "--pr", "42", "--stage", "ci", "--name", name, "--target-dir", repo)
    assert required.returncode == 0, required.stderr
    status = forge("delivery", "status", "--pr", "42", "--format", "markdown", "--target-dir", repo,
                   env={**os.environ, "PYTHONIOENCODING": "ascii"})
    assert status.returncode == 0, status.stderr
    stages, actions = status.stdout.split("### Next action")
    literal = f"``` {name} ```"
    assert f"  - {literal} — missing" in stages.splitlines()
    assert f"- Check {literal} (missing)." in actions.splitlines()
