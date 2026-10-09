"""Native-shaped evidence stays bound after finalization and material review.

Offline setup, native loading/writer, and candidate isolation are explicit test
doubles. Initial assessment runs only the tiny trusted oracle and temporary Git.
These controls prove evidence bookkeeping, never real native qualification.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from coding_trial_assessment import assess_suite
from coding_trial_delivery import read_delivery
import coding_trial_isolation as isolation
from coding_trial_suite import _read_study, run_suite
from native_admission_fixtures import attach_static_admission
from test_trial_suite_native_preparation import PreparedNativeBoundary, SetupBoundary, inputs
from trial_suite_assessment_cases import (
    ORACLE, RoleBoundary, codes, last_assessment, load_json, review_for, save_review,
)
from trial_suite_prepare_cases import PYTHON, bytes_at, read_reference


pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd,
    reason="Native reservation requires the admitted no-follow controller")


def completed_native(tmp_path, monkeypatch):
    seed = inputs(tmp_path, admitted=False)
    root = seed["suite_root"]
    (root / "checks/oracle.py").write_text(ORACLE)
    (root / "checks/scenario.json").write_text(json.dumps({"oracle": "passed"}) + "\n")
    oracle = seed["suite"]["tasks"]["normalize"]["checks"]["oracle"]
    oracle.update(support=["checks/scenario.json"],
                  argv=["{python}", "{entry}", "{assessment}", "{receipt}"], timeout_seconds=5)
    attach_static_admission(seed)  # Bind the actual fixture oracle bytes before preparation.
    setup = SetupBoundary(seed, monkeypatch)
    native = PreparedNativeBoundary(seed, monkeypatch)
    checks = RoleBoundary()
    monkeypatch.setattr(isolation, "execute_isolated", checks)
    trial = tmp_path / "trial"
    folder = trial / "assessor/assessments/000001"
    oracle_argv = [str(PYTHON), "-I", "-B", str(tmp_path / "trial.study/suite/checks/oracle.py"),
                   str(folder / "assessment.json"), str(folder / "oracle-receipt.json")]
    original = subprocess.Popen

    def checked(argv, *args, **kwargs):
        assert isinstance(argv, (list, tuple)) and argv and not kwargs.get("shell")
        allowed = (Path(argv[0]).name == "git" or list(argv) == [str(PYTHON), "-I", "-B", "--version"]
                   or list(argv) == oracle_argv)
        assert allowed, f"No real native/provider process is allowed: {argv!r}"
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", checked)
    attempt = run_suite(trial, root / "suite.json", "normalize", seed["arm"],
                        assessor_python=PYTHON, qualify_loading=True)
    assert attempt["status"] == "completed" and attempt["native"] is not None
    assert setup.calls and native.calls[-1]["stage"] == "writer"
    assert [call["role"] for call in checks.calls] == ["native"]
    first = last_assessment(trial)
    assert first["common_quality"] == "pending" and codes(first) == {"review-pending"}
    review = save_review(trial, review_for(trial, first))
    reviewed = assess_suite(trial, review=review)
    assert reviewed["common_quality"] == reviewed["acceptance"] == "passed"
    assert reviewed["behavior"] == first["behavior"]
    return trial, first, review, (setup, native, checks)


def forbid_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Terminal evidence readback/review cannot launch any process")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)


class TestNativeTerminalLineage:
    def test_unchanged_native_receipts_allow_repeated_process_free_reviews(self, tmp_path, monkeypatch):
        trial, first, review, boundaries = completed_native(tmp_path, monkeypatch)
        before = load_json(trial / "trial.json")
        calls = [deepcopy(boundary.calls) for boundary in boundaries]
        retained = bytes_at(trial / "assessor/assessments/000001")
        forbid_execution(monkeypatch)
        for number in (3, 4):
            result = assess_suite(trial, review=review)
            assert result["number"] == number and result["behavior"] == first["behavior"]
            assert result["common_quality"] == result["acceptance"] == "passed"
            current = load_json(trial / "trial.json")
            assert {key: value for key, value in current.items() if key != "assessments"} == {
                key: value for key, value in before.items() if key != "assessments"}
        assert bytes_at(trial / "assessor/assessments/000001") == retained
        assert [boundary.calls for boundary in boundaries] == calls

    @pytest.mark.parametrize("target", ["reservation", "loading_source", "loading_derived",
                                        "execution_manifest", "restoration",
                                        "source-evidence", "derived-evidence"])
    def test_review_rejects_changed_terminal_native_receipt_bytes(self, tmp_path, monkeypatch, target):
        trial, _, review, boundaries = completed_native(tmp_path, monkeypatch)
        attempt = load_json(trial / "trial.json")
        _, _, suite_root = _read_study(attempt["study"])
        if target in {"source-evidence", "derived-evidence"}:
            name = "loading_source" if target == "source-evidence" else "loading_derived"
            root = trial if name == "loading_source" else suite_root
            receipt_path, raw = read_reference(root, attempt["native"][name])
            evidence = json.loads(raw)["evidence"][0]
            evidence_root = receipt_path.parent if name == "loading_source" else suite_root
            selected, original = read_reference(evidence_root, evidence)
        else:
            root = suite_root if target in {"loading_derived", "execution_manifest"} else trial
            reference = attempt["native"][target]
            selected, original = read_reference(root, {key: reference[key] for key in ("path", "sha256")})
        assert selected.is_file() and not selected.is_symlink()
        retained = bytes_at(trial / "assessor/assessments")
        writer_path, writer_raw = read_reference(trial, attempt["runner"])
        calls = [deepcopy(boundary.calls) for boundary in boundaries]
        selected.write_bytes(original + b" ")  # Preserve parseability; invalidate only byte identity.
        forbid_execution(monkeypatch)
        result = assess_suite(trial, review=review)
        assert result["kind"] == "review" and result["common_quality"] == result["acceptance"] == "failed"
        assert codes(result) & {"receipt-invalid", "candidate-drift", "input-drift"}
        assert result["study_eligible"] is False
        assert writer_path.read_bytes() == writer_raw and selected.read_bytes() == original + b" "
        assert all((trial / "assessor/assessments" / name).read_bytes() == raw for name, raw in retained.items())
        assert [boundary.calls for boundary in boundaries] == calls

    @pytest.mark.parametrize("change", ["clear-native", "same-byte-reservation-ref"])
    def test_frozen_delivery_rejects_terminal_native_map_rebinding(self, tmp_path, monkeypatch, change):
        trial, _, _, _ = completed_native(tmp_path, monkeypatch)
        original = load_json(trial / "trial.json")
        attempt = deepcopy(original)
        _, suite, suite_root = _read_study(attempt["study"])
        if change == "clear-native":
            attempt["native"] = None
        else:
            path, raw = read_reference(trial, attempt["native"]["reservation"])
            replacement = path.with_name("same-byte-reservation.json")
            with replacement.open("xb") as stream:
                stream.write(raw)
            attempt["native"]["reservation"] = {
                "path": replacement.relative_to(trial).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()}
            assert replacement.read_bytes() == path.read_bytes()
        forbid_execution(monkeypatch)
        with pytest.raises(ValueError, match="candidate-drift"):
            read_delivery(trial, attempt, suite, suite_root)
        assert load_json(trial / "trial.json") == original
