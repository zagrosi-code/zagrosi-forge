"""Four retained preparation counterexamples using the approved study-reference API.

The meaningful old-API RED was captured before this setup-only adaptation.
No execution occurs during authoring.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from coding_trial_suite import _freeze_study, _prepare_attempt, prepare_suite
from trial_suite_prepare_cases import PYTHON, bytes_at, git, marked_suite, save_manifest, schedule


class TestSuitePreparationReview:
    def test_changed_schedule_cannot_be_adopted_between_attempts(self, tmp_path):
        path, _, _ = marked_suite(tmp_path)
        study_ref = _freeze_study(tmp_path / "study", path, schedule("alpha", "bravo"), PYTHON)
        study_path = Path(study_ref["path"])
        first = tmp_path / "first"
        initial = _prepare_attempt(first, study_ref, 0, status="prepared",
                                   qualify_loading=False, auth_file=None)
        preserved = bytes_at(first)
        study = json.loads(study_path.read_text())
        study["schedule"][1]["arm"] = "charlie"
        study_path.write_text(json.dumps(study) + "\n")
        with pytest.raises(ValueError, match="input-drift"):
            _prepare_attempt(tmp_path / "second", study_ref, 1, status="prepared",
                             qualify_loading=False, auth_file=None)
        assert not (tmp_path / "second").exists()
        assert bytes_at(first) == preserved
        assert json.loads((first / "trial.json").read_text())["study"] == initial["study"]

    def test_literal_generated_root_does_not_ignore_a_glob_neighbor(self, tmp_path):
        path, suite, _ = marked_suite(tmp_path)
        suite["tasks"]["normalize"]["scope"]["generated"] = ["generated[1]"]
        save_manifest(path, suite)
        trial = tmp_path / "trial"
        prepare_suite(trial, path, "normalize", "alpha", assessor_python=PYTHON)
        workspace = trial / "workspace"
        for name in ("generated[1]", "generated1"):
            (workspace / name).mkdir()
            (workspace / name / "new.txt").write_text("temporary candidate output")
        visible = git(workspace, "status", "--porcelain", "--untracked-files=all")
        assert "generated[1]/new.txt" not in visible
        assert "generated1/new.txt" in visible

    def test_fixture_auth_rejection_happens_before_any_study_or_attempt_creation(self, tmp_path):
        path, _, marker = marked_suite(tmp_path)
        auth = tmp_path / "not-a-real-credential"
        auth.write_bytes(b"fixture marker; never provider authentication")
        before = bytes_at(path.parent)
        with pytest.raises(ValueError, match="suite-invalid"):
            prepare_suite(tmp_path / "trial", path, "normalize", "alpha",
                          assessor_python=PYTHON, auth_file=auth)
        assert not (tmp_path / "trial").exists()
        assert not (tmp_path / "trial.study").exists()
        assert not marker.exists() and bytes_at(path.parent) == before
        assert auth.read_bytes() == b"fixture marker; never provider authentication"

    def test_uncoded_git_setup_failure_retains_a_named_error_code(self, tmp_path, monkeypatch):
        path, _, marker = marked_suite(tmp_path)
        original = subprocess.run
        def without_git(argv, *args, **kwargs):
            if Path(argv[0]).name == "git":
                raise FileNotFoundError("fictional Git is unavailable")
            return original(argv, *args, **kwargs)
        monkeypatch.setattr(subprocess, "run", without_git)
        trial = tmp_path / "trial"
        with pytest.raises(ValueError, match="suite-invalid"):
            prepare_suite(trial, path, "normalize", "alpha", assessor_python=PYTHON)
        failed = json.loads((trial / "trial.json").read_text())
        assert failed["status"] == "failed" and failed["error"]["code"] == "suite-invalid"
        assert "Git is required" in failed["error"]["message"]
        assert failed["initial_git"] is None and failed["runner"] is None
        assert not marker.exists()

    @pytest.mark.parametrize("separator", ["\n", "\r"])
    def test_line_break_exclusion_rejection_precedes_all_creation(self, tmp_path, separator):
        path, suite, marker = marked_suite(tmp_path)
        suite["tasks"]["normalize"]["scope"]["generated"] = ["generated" + separator + "output"]
        save_manifest(path, suite)
        before = bytes_at(path.parent)
        trial = tmp_path / "trial"
        with pytest.raises(ValueError, match="suite-invalid"):
            prepare_suite(trial, path, "normalize", "alpha", assessor_python=PYTHON)
        assert not trial.exists() and not (tmp_path / "trial.study").exists()
        assert bytes_at(path.parent) == before and not marker.exists()
