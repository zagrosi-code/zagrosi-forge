"""Both consumers must bind the same producer report to its actual Git revision."""
import copy
import json
import sys
from pathlib import Path

import pytest

from forge_test_helpers import load_zagrosi_module
from test_compact_plan import SECTION
from test_section_groups import group_plan
from test_team_git import git
from test_team_workflows import call


@pytest.fixture(scope="module")
def produced(tmp_path_factory):
    """Capture one genuine public verification, then clone its history for each consumer test."""
    from contextlib import redirect_stdout
    from io import StringIO

    folder = tmp_path_factory.mktemp("group-binding")
    root = folder / "repo"
    git(folder, "init", "-b", "feature", str(root))
    git(root, "commit", "--allow-empty", "-m", "Before the plan")
    git(root, "branch", "base")
    plan = group_plan(root)
    runtime = load_zagrosi_module()

    def group(*args):
        out = StringIO()
        with redirect_stdout(out):
            code = runtime.entrypoint.main(["section-group", *map(str, args), "--target-dir", str(root)])
        result = json.loads(out.getvalue())
        assert code == 0, result
        return result

    handoff = group("create", "--planning-dir", plan, "--pr", "42", "--sections", SECTION)
    file = folder / "handoff.json"
    file.write_text(json.dumps(handoff))
    out = StringIO()
    with redirect_stdout(out):
        code = runtime.entrypoint.main(["section-group", "verify", "--target-dir", str(root),
                                       "--handoff", str(file), "--", sys.executable, "-c", "pass"])
    result = json.loads(out.getvalue())
    assert code == 0, result
    return root, plan.relative_to(root), result


@pytest.fixture
def workspace(produced, tmp_path, capsys):
    original, plan, report = produced
    root = tmp_path / "repo"
    git(tmp_path, "clone", "--local", str(original), str(root))
    git(root, "branch", "base", "origin/base")
    runtime = load_zagrosi_module()

    def run(*args):
        return call(runtime, capsys, *args)

    def consume(document):
        file = tmp_path / "report.json"
        file.write_text(json.dumps(document))
        delivered = run("delivery", "test", "--target-dir", root, "--pr", "42", "--group-report", file,
                        "--source", "build/42", "--actor", "ci")
        measured = run("build-report", "--planning-dir", root / plan, "--group", file)
        return delivered, measured

    code, tracked = run("delivery", "track", "--target-dir", root, "--pr", "42",
                        "--head-ref", "refs/heads/feature", "--base-ref", "refs/heads/base")
    assert code == 0, tracked
    return root, consume, copy.deepcopy(report)


def test_produced_check_is_accepted_and_replayed_once(workspace):
    root, consume, report = workspace
    first, measured = consume(report)
    assert first[0] == measured[0] == 0, (first, measured)
    assert measured[1]["groups"][0]["verification"]["observed_checks"] == 1
    ledger = root / ".git" / "forge-delivery.json"
    saved = ledger.read_bytes()
    assert consume(report)[0][0] == 0
    assert ledger.read_bytes() == saved


@pytest.mark.parametrize("mutation", [
    "historical-empty-commit", "missing-target", "file-target", "wrong-owned-paths",
    "duplicate-sections", "negative-seconds", "seconds-above-limit", "seconds-overflow",
    "timezone-overflow", "missing-finish", "nonboolean-timeout", "cancelled-success",
    "failed-snapshot-success", "conflicting-history",
])
def test_rejected_group_does_not_change_existing_records(workspace, mutation):
    root, consume, original = workspace
    report = copy.deepcopy(original)
    group = report["handoff"]
    run = report["verification"]
    check = report["history"]["group_check"]
    if mutation == "historical-empty-commit":
        group["commit_sha"] = git(root, "rev-parse", "base").stdout.strip()
        group["tree_sha"] = git(root, "rev-parse", "base^{tree}").stdout.strip()
    elif mutation in ("missing-target", "file-target"):
        group["target_path"] = "absent" if mutation == "missing-target" else group["plan_path"] + "/spec.md"
        # Keep scope internally consistent: only inspecting the recorded Git tree can detect this target.
        group["owned_paths"] = [group["target_path"] + "/" + path for path in group["owned_paths"]]
    elif mutation == "wrong-owned-paths":
        group["owned_paths"] = ["unrelated.py"]
    elif mutation == "duplicate-sections":
        group["sections"] += group["sections"]
    elif mutation in ("negative-seconds", "seconds-above-limit", "seconds-overflow"):
        seconds = {"negative-seconds": -1, "seconds-above-limit": 172801, "seconds-overflow": 10**350}[mutation]
        run["seconds"] = check["seconds"] = seconds
    elif mutation == "timezone-overflow":
        run["started_at"] = "0001-01-01T00:00:00+14:00"
    elif mutation == "missing-finish":
        del run["finished_at"]
    elif mutation == "nonboolean-timeout":
        run["timed_out"] = 0
    elif mutation == "cancelled-success":
        run["cancelled"] = True
    elif mutation == "failed-snapshot-success":
        run["snapshot_error"] = "Checkout changed during verification"
    elif mutation == "conflicting-history":
        check["command_sha256"] = "0"*64

    # A previous genuine result must survive a failed import byte for byte.
    assert consume(original)[0][0] == 0
    ledger = root / ".git" / "forge-delivery.json"
    saved = ledger.read_bytes()
    refs = git(root, "show-ref").stdout
    files = git(root, "status", "--porcelain", "--untracked-files=all").stdout
    # A distinct run ID prevents duplicate detection from masking acceptance of malformed input.
    run["run_id"] = check["run_id"] = "e"*32
    delivered, measured = consume(report)
    assert (delivered[0], measured[0]) == (1, 1), (delivered, measured)
    assert delivered[1].get("error") and measured[1].get("error")
    assert ledger.read_bytes() == saved
    assert git(root, "show-ref").stdout == refs
    assert git(root, "status", "--porcelain", "--untracked-files=all").stdout == files
