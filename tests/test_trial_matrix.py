"""Whole-task reports retain failures and do not invent timings or success."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("trial_matrix", ROOT / "tools/trial_matrix.py")
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


def test_report_keeps_timeout_and_pending_attempts_in_denominator(tmp_path):
    entries = [{"id": name, "case": "summary", "depth": "deep"} for name in ("ok", "timeout", "pending")]
    (tmp_path / "matrix.json").write_text(json.dumps({"trials": entries, "plugin_root": str(ROOT), "runner": ["agent"]}))
    for name, seconds, passed in (("ok", 10, True), ("timeout", 90, False)):
        trial = tmp_path / name
        trial.mkdir()
        (trial / "attempt.json").write_text(json.dumps({"returncode": 0 if passed else 1, "seconds": seconds + 2}))
        (trial / "result.json").write_text(json.dumps({"success": passed,
            "runner": {"returncode": 0 if passed else 124, "seconds": seconds, "timed_out": not passed}}))
    result = matrix.report(tmp_path)
    group = result["groups"][0]
    assert not result["success"]
    assert group["scheduled"] == 3
    assert group["outcomes"] == {"passed": 1, "failed": 1, "pending": 1}
    assert group["median_runner_seconds"] == 50
    assert group["timed_attempts"] == 2
    assert group["attempts"][-1]["reported_telemetry"] is None


def test_passing_check_without_runner_is_not_a_complete_task(tmp_path):
    (tmp_path / "matrix.json").write_text(json.dumps({"trials": [{"id": "one", "case": "cleanup", "depth": "lean"}],
        "plugin_root": str(ROOT), "runner": ["agent"]}))
    trial = tmp_path / "one"
    trial.mkdir()
    (trial / "result.json").write_text('{"success": true}')
    result = matrix.report(tmp_path)
    assert not result["success"]
    assert result["groups"][0]["median_runner_seconds"] is None


def test_cancelled_schedule_is_retained_without_inventing_failures(tmp_path):
    (tmp_path / "matrix.json").write_text(json.dumps({"trials": [{"id": "one", "case": "cleanup", "depth": "lean"}],
        "plugin_root": str(ROOT), "runner": ["agent"], "cancelled": "Source superseded"}))
    result = matrix.report(tmp_path)
    assert not result["success"]
    assert result["cancelled"] == "Source superseded"
    assert result["groups"][0]["outcomes"] == {"cancelled": 1}


def test_matrix_runs_fresh_attempts_and_retains_nonzero_exits(tmp_path):
    root = tmp_path / "plugin"
    (root / "tools").mkdir(parents=True)
    (root / "examples/evals/coding").mkdir(parents=True)
    (root / "examples/evals/coding/cases.json").write_text('{"summary": {}}')
    (root / "tools/coding_trials.py").write_text('''import json, pathlib, sys
trial = pathlib.Path(sys.argv[2])
trial.mkdir()
(trial / 'result.json').write_text(json.dumps({'success': False, 'runner': {'seconds': 1, 'returncode': 7}}))
raise SystemExit(7)
''')
    destination = tmp_path / "experiment"
    command = [sys.executable, str(ROOT / "tools/trial_matrix.py"), "run", str(destination),
               "--plugin-root", str(root), "--cases", "summary", "--depths", "lean", "deep",
               "--repeats", "2", "--jobs", "2", "--runner", "unused"]
    completed = subprocess.run(command, capture_output=True, text=True)
    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert len(list(destination.glob('*/attempt.json'))) == 4
    assert all(g["outcomes"] == {"failed": 2} for g in result["groups"])
    before = (destination / "matrix.json").read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert (destination / "matrix.json").read_bytes() == before
