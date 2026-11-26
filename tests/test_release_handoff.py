"""Review handoffs through the public CLI in a real Git checkout."""
import json
import subprocess
import sys

import pytest

from forge_test_helpers import SCRIPT
from test_group_binding import produced, workspace


@pytest.mark.parametrize("rc", [0, 7])
def test_receipt_without_output_keeps_the_execution_for_both_consumers(workspace, tmp_path, rc):
    repo, consume, previous = workspace
    handoff = tmp_path / "handoff.json"
    handoff.write_text(json.dumps(previous), encoding="utf-8")
    argv = [sys.executable, "-c", "import sys; print('private transcript'); sys.exit(int(sys.argv[1]))", str(rc)]
    result = subprocess.run([sys.executable, str(SCRIPT), "section-group", "verify",
                             "--target-dir", str(repo), "--handoff", str(handoff), "--omit-output", "--", *argv],
                            capture_output=True, text=True)
    assert result.returncode == int(rc != 0), result.stderr
    receipt = json.loads(result.stdout)
    observed = receipt["verification"]
    assert "stdout" not in observed and "stderr" not in observed
    assert observed["command"] == argv and observed["returncode"] == rc
    assert receipt["history"]["group_check"]["passed"] == (rc == 0)
    delivered, measured = consume(receipt)
    assert delivered[0] == measured[0] == 0
    assert measured[1]["groups"][0]["verification"]["observed_checks"] == 1
    replayed, _ = consume(receipt)
    assert len(replayed[1]["prs"]["42"]["verification"]["checks"]) == 1


def test_markdown_shows_tested_revision_and_omits_private_command(workspace):
    repo, consume, report = workspace
    assert consume(report)[0][0] == 0
    result = subprocess.run([sys.executable, str(SCRIPT), "delivery", "status", "--pr", "42",
                             "--format", "markdown", "--target-dir", str(repo)],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert report["handoff"]["commit_sha"] in result.stdout
    assert report["handoff"]["sections"][0] in result.stdout
    assert "Next action" in result.stdout and sys.executable not in result.stdout
