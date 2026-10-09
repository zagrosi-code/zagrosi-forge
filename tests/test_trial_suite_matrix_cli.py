"""Public matrix dispatch only; suite operations are explicit boundary doubles.

No provider, Git, candidate or assessment process is permitted. Saved headers
select routes; real record validation/vertical execution belongs to other tests.
Legacy reviews/error behavior characterizes existing call sites. The separate
vertical draft owns override rejection, legacy defaults and shared exports.
"""
from __future__ import annotations

from copy import deepcopy
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import coding_trial_comparison as comparison
import trial_matrix as matrix


def forbid_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A CLI routing test must not launch any process")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)


def header(directory, *, schema="coding-trial-matrix/v1"):
    directory.mkdir()
    value = {"schema": schema, "study": {"path": str(directory / "study/study.json"), "sha256": "a" * 64},
             "suite_sha256": "b" * 64}
    (directory / "matrix.json").write_text(json.dumps(value) + "\n")


def boundary(monkeypatch, *, success=True):
    forbid_execution(monkeypatch)
    # Deferred import keeps legacy/parser characterizations collectable before
    # the new suite module exists; absence is a genuine per-case RED, not a skip.
    api = importlib.import_module("coding_trial_suite_matrix")
    payload = {"schema": "coding-trial-suite-report/v1", "success": success, "suite_sha256": "b" * 64,
               "purpose": "synthetic", "study_complete": success, "study_eligible": False,
               "attempts": [], "accepted_outcomes": {"accepted": 0, "scheduled": 3},
               "synthetic_validation": "passed" if success else "pending", "errors": []}
    calls = []
    def operation(name):
        def call(*args, **kwargs):
            calls.append((name, args, kwargs))
            if name == "blind_suite" and success:
                (Path(args[0]) / "blind/000001").mkdir(parents=True)
            return deepcopy(payload)
        return call
    for name in ("compare_suite", "report_suite", "blind_suite", "apply_suite_reviews"):
        monkeypatch.setattr(api, name, operation(name))
    return api, calls, payload


def invoke(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["trial_matrix.py", *map(str, args)])
    code = matrix.main()
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


class TestSuiteMatrixCLI:
    @pytest.mark.parametrize("authorized", [False, True])
    def test_suite_compare_delegates_only_explicit_native_choices(self, tmp_path, monkeypatch, capsys, authorized):
        _, calls, payload = boundary(monkeypatch)
        directory, suite, auth = tmp_path / "study", tmp_path / "suite.json", tmp_path / "auth.json"
        suite.write_text("{}\n")  # The public comparison boundary owns schema validation.
        auth.write_text("fixture credentials never read by this dispatch test\n")
        extras = ["--qualify-loading", "--auth-file", auth] if authorized else []
        code, result = invoke(monkeypatch, capsys, "compare", directory, "--suite", suite, *extras)
        assert code == 0 and result == payload
        assert len(calls) == 1 and calls[0][0] == "compare_suite"
        _, args, options = calls[0]
        assert tuple(map(Path, args)) == (directory, suite)
        assert Path(options["assessor_python"]) == Path(sys.executable).resolve()
        assert options.get("qualify_loading", False) is authorized
        assert options.get("auth_file") == (auth if authorized else None)
        assert set(options) <= {"assessor_python", "qualify_loading", "auth_file"}
        assert not directory.exists(), "The dispatcher must leave preparation to the public suite operation"

    @pytest.mark.parametrize("operation,target", [("report", "report_suite"), ("blind", "blind_suite"),
                                                  ("apply-reviews", "apply_suite_reviews")])
    @pytest.mark.parametrize("success", [False, True])
    def test_saved_suite_routes_and_purpose_specific_success(self, tmp_path, monkeypatch, capsys, operation, target, success):
        _, calls, payload = boundary(monkeypatch, success=success)
        header(tmp_path / "study")
        code, result = invoke(monkeypatch, capsys, operation, tmp_path / "study")
        assert code == (0 if success else 1) and result == payload
        assert calls == [(target, (tmp_path / "study",), {})]
        assert result["accepted_outcomes"]["accepted"] == 0, "Synthetic success is not prospective acceptance"
        if operation == "blind":
            assert (tmp_path / "study/blind/000001").exists() is success


    @pytest.mark.parametrize("operation,extras", [
        ("blind", ["--qualify-loading"]), ("apply-reviews", ["--auth-file", "auth.json"])])
    def test_suite_selectors_and_native_flags_cannot_leak_into_other_operations(self, tmp_path, monkeypatch, operation, extras):
        forbid_execution(monkeypatch)
        directory = tmp_path / "fresh"
        monkeypatch.setattr(sys, "argv", ["trial_matrix.py", operation, str(directory), *extras])
        with pytest.raises(SystemExit) as raised:
            matrix.main()
        assert raised.value.code == 2 and not directory.exists()

    @pytest.mark.parametrize("operation", ["report", "blind", "apply-reviews"])
    def test_unknown_explicit_saved_schema_has_named_input_error(self, tmp_path, monkeypatch, capsys, operation):
        forbid_execution(monkeypatch)
        header(tmp_path / "study", schema="unrecognized-matrix/v99")
        before = (tmp_path / "study/matrix.json").read_bytes()
        code, result = invoke(monkeypatch, capsys, operation, tmp_path / "study")
        assert code == 2 and result["success"] is False and result["error"]["code"] == "suite-invalid"
        assert (tmp_path / "study/matrix.json").read_bytes() == before

    @pytest.mark.parametrize("error,status", [("unsupported-profile: unavailable fixture", 2),
                                             ("candidate-drift: changed fixture", 1)])
    def test_named_suite_failures_keep_preflight_and_outcome_exit_codes(self, tmp_path, monkeypatch, capsys, error, status):
        api, _, _ = boundary(monkeypatch)
        def failed(directory):
            raise ValueError(error)
        monkeypatch.setattr(api, "report_suite", failed)
        header(tmp_path / "study")
        code, result = invoke(monkeypatch, capsys, "report", tmp_path / "study")
        assert code == status and result["error"]["code"] == error.split(":", 1)[0]

    @pytest.mark.parametrize("public,apply,target", [("packets", None, "blind_suite"),
        ("reviews", False, "report_suite"), ("reviews", True, "apply_suite_reviews")])
    def test_existing_comparison_entrypoints_dispatch_the_explicit_suite_schema(self, tmp_path, monkeypatch, public, apply, target):
        _, calls, payload = boundary(monkeypatch)
        header(tmp_path / "study")
        result = (comparison.packets(tmp_path / "study") if public == "packets"
                  else comparison.reviews(tmp_path / "study", apply=apply))
        assert result == payload and calls == [(target, (tmp_path / "study",), {})]


    def test_legacy_report_still_uses_its_own_reviews_lookup(self, tmp_path, monkeypatch):
        forbid_execution(monkeypatch)
        item = {"id": "one", "case": "cleanup", "depth": "lean", "arm": "current", "block": "block"}
        (tmp_path / "matrix.json").write_text(json.dumps({"comparison": True, "trials": [item],
            "plugin_root": str(ROOT), "runner": ["never-run"]}))
        trial = tmp_path / "one"
        trial.mkdir()
        (trial / "attempt.json").write_text(json.dumps({"seconds": 1, "returncode": 0}))
        (trial / "result.json").write_text(json.dumps({"success": True, "runner": {"returncode": 0},
                                                       "common_quality": {"success": True}}))
        calls = []
        def reviews(directory):
            calls.append(directory)
            return [{"block": "block", "valid": True}]
        monkeypatch.setattr(matrix, "reviews", reviews)
        assert matrix.report(tmp_path)["success"] is True and calls == [tmp_path]

    @pytest.mark.parametrize("raw", ["{", '{"unknown_legacy_field":true}'])
    def test_unreadable_or_schema_absent_legacy_records_keep_original_error(self, tmp_path, monkeypatch, raw):
        forbid_execution(monkeypatch)
        (tmp_path / "matrix.json").write_text(raw)
        monkeypatch.setattr(sys, "argv", ["trial_matrix.py", "report", str(tmp_path)])
        with pytest.raises(ValueError, match="A nonempty matrix.json is required"):
            matrix.main()
        assert (tmp_path / "matrix.json").read_text() == raw
