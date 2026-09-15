"""Coding trial verdicts use independent behavior checks and real process results."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coding_trials", ROOT / "tools/coding_trials.py")
trials = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trials)


@pytest.mark.parametrize("case", ["summary", "discount", "resume", "godfile"])
def test_unfinished_feature_fails_independent_oracle(tmp_path, case):
    trials.prepare(tmp_path / "trial", case)
    result = trials.check(tmp_path / "trial")
    assert not result["success"]
    assert result["oracle"]["returncode"] != 0
    assert result["tests"]["returncode"] == 0
    assert result["reported_telemetry"] is None


def test_behavior_preservation_checks_exact_exports_and_public_wrapper(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    baseline = trials.check(trial)
    assert baseline["success"]
    assert json.loads(baseline["oracle"]["stdout"])["assertions"] >= 500
    path = trial / "workspace/src/ledger.py"
    path.write_text(path.read_text().replace('"Total: "', '"Total:"'))
    result = trials.check(trial)
    assert not result["success"]
    assert result["after"]["source_lines"] == result["before"]["source_lines"]


def test_independent_oracle_survives_replaced_candidate_tests(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "summary")
    (trial / "workspace/tests/test_ledger.py").write_text("# No candidate tests\n")
    result = trials.check(trial)
    assert not result["success"]
    assert result["oracle"]["returncode"] != 0


def test_scope_expansion_is_reported_and_rejected(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    (trial / "workspace/requirements.txt").write_text("unrequested-package\n")
    result = trials.check(trial)
    assert not result["success"]
    assert result["outside_scope"] == ["requirements.txt"]


def test_prepare_refuses_to_overwrite_existing_trial(tmp_path):
    trials.prepare(tmp_path / "trial", "cleanup")
    with pytest.raises(FileExistsError):
        trials.prepare(tmp_path / "trial", "cleanup")


def test_runner_failures_and_timeouts_are_real_results(tmp_path):
    failed = trials.execute([sys.executable, "-c", "raise SystemExit(3)"], tmp_path)
    assert failed["returncode"] == 3
    timed_out = trials.execute([sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, timeout=0.05)
    assert timed_out["returncode"] == 124


@pytest.mark.parametrize("returncode", [3, 124])
def test_recheck_preserves_failed_runner_verdict(tmp_path, returncode):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    path = trial / "trial.json"
    record = json.loads(path.read_text())
    record["runner"] = {"returncode": returncode, "seconds": 1.0}
    path.write_text(json.dumps(record))
    result = trials.check(trial)
    assert result["oracle"]["returncode"] == 0
    assert not result["success"]
    assert not json.loads((trial / "result.json").read_text())["success"]


@pytest.mark.parametrize("injected", [
    'raise SystemExit(0)',
    'print("not an oracle result"); raise SystemExit(0)',
    'print(\'{"case": "summary", "assertions": 1}\'); raise SystemExit(0)',
])
def test_premature_oracle_exit_cannot_pass(tmp_path, injected):
    trial = tmp_path / "trial"
    trials.prepare(trial, "summary")
    path = trial / "workspace/src/ledger.py"
    with path.open("a") as handle:
        handle.write('\nif __name__ == "candidate_ledger":\n    ' + injected + '\n')
    result = trials.check(trial)
    assert result["oracle"]["returncode"] == 0
    assert not result["oracle_complete"]
    assert not result["success"]


def test_optimized_parent_environment_cannot_disable_oracle(tmp_path, monkeypatch):
    trial = tmp_path / "trial"
    trials.prepare(trial, "summary")
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    result = trials.check(trial)
    assert not result["success"]
    assert result["oracle"]["returncode"] != 0
