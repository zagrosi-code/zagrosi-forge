"""Source-review regressions for setup readback before native admission.

Authored after the first implementation, without importing or running it.
Only existing explicit setup/loading doubles execute when the parent runs these
tests. This does not establish real installation, loading or Docker isolation.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from test_trial_suite_native_preparation import (
    PreparedNativeBoundary, SetupBoundary, assert_setup_evidence, finalization_observer,
    forbid_native_processes, inputs, load, pytestmark,
)
import coding_trial_loading as loading
import coding_trial_preparation as preparation
from test_trial_suite_native_runtime import save
from trial_suite_prepare_cases import PYTHON
from coding_trial_suite import prepare_suite, run_suite


class ContradictingSetup(SetupBoundary):
    """Retain a real-shaped contradiction before the controller binds raw bytes."""
    def __init__(self, seed, monkeypatch, fault):
        super().__init__(seed, monkeypatch)
        self.readback_fault = fault

    def __call__(self, *args, **kwargs):
        result = super().__call__(*args, **kwargs)
        if len(self.calls) == 1:
            folder = Path(kwargs["evidence_dir"])
            if self.readback_fault == "raw-start-contradiction":
                value = load(folder / "docker-000.json")
                value["process"]["returncode"] = 19
                save(folder / "docker-000.json", value)
            elif self.readback_fault == "raw-lifecycle-contradiction":
                value = load(folder / "lifecycle.json")
                value["status"] = "unverified"
                save(folder / "lifecycle.json", value)
        return result


@pytest.mark.parametrize("fault", [
    "raw-start-contradiction", "raw-lifecycle-contradiction",
    "result-after-index", "raw-start-after-index", "raw-lifecycle-after-index",
])
def test_bad_setup_raw_evidence_blocks_native_qualification_and_writer(tmp_path, monkeypatch, fault):
    seed = inputs(tmp_path)
    setup = ContradictingSetup(seed, monkeypatch, fault)
    native = PreparedNativeBoundary(seed, monkeypatch)
    assessments = finalization_observer(monkeypatch)
    original_reserve = preparation.reserve_native
    retained = {}

    def reserve_then_change(suite, study, attempt, *, auth_file=None):
        reference = original_reserve(suite, study, attempt, auth_file=auth_file)
        trial = Path(attempt["roots"]["workspace"]).parent
        index = load(trial / attempt["native"]["setup"]["path"])
        retained["index"] = deepcopy(index)
        retained["attempt"] = (trial / "private/initial-attempt.json").read_bytes()
        names = {"result-after-index": "result.json", "raw-start-after-index": "docker-000.json",
                 "raw-lifecycle-after-index": "lifecycle.json"}
        if fault in names:
            target = trial / "private/native-setup/steps/000" / names[fault]
            target.write_bytes(target.read_bytes() + b"\n ")
        return reference

    monkeypatch.setattr(preparation, "reserve_native", reserve_then_change)
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid setup evidence reached native qualification or execution")
    monkeypatch.setattr(loading, "qualify_profile", forbidden)
    monkeypatch.setattr(loading, "run_owned", forbidden)
    trial = tmp_path / "fresh"
    with pytest.raises(ValueError):
        run_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"],
                  assessor_python=PYTHON, qualify_loading=True)
    attempt = load(trial / "trial.json")
    assert setup.calls and attempt["status"] == "failed" and attempt["error"]
    assert attempt["runner"] is None and attempt["candidate"] is None
    assert native.calls == [] and native.isolations == [] and assessments == []
    assert not (trial / "private/loading").exists() and not (trial / "private/writer").exists()
    if fault.endswith("after-index"):
        assert (trial / "private/initial-attempt.json").read_bytes() == retained["attempt"]
        assert load(trial / attempt["native"]["setup"]["path"]) == retained["index"]


def test_unchanged_setup_readback_keeps_explicit_native_run_reachable(tmp_path, monkeypatch):
    seed = inputs(tmp_path)
    setup = ContradictingSetup(seed, monkeypatch, None)
    native = PreparedNativeBoundary(seed, monkeypatch)
    assessments = finalization_observer(monkeypatch)
    trial = tmp_path / "fresh"
    result = run_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"],
                       assessor_python=PYTHON, qualify_loading=True)
    assert result["status"] == "completed" and result["runner"] and result["candidate"]
    assert setup.calls and len(native.isolations) == 3 and len(assessments) == 1
    assert native.calls[-1]["stage"] == "writer"


@pytest.mark.parametrize("plain", [False, True])
def test_zero_exit_wrong_version_stops_before_the_next_setup_command(tmp_path, monkeypatch, plain):
    seed = inputs(tmp_path, plain=plain)
    seed["preparation"]["executions"][0]["stdout"] = "codex-cli 0.153.0\n"
    setup = SetupBoundary(seed, monkeypatch)
    native = PreparedNativeBoundary(seed, monkeypatch)
    trial = tmp_path / "fresh"
    with pytest.raises(ValueError):
        prepare_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"],
                      assessor_python=PYTHON)
    attempt = load(trial / "trial.json")
    assert attempt["status"] == "failed" and attempt["error"]
    assert len(setup.calls) == 1, "A failed actual version readback must stop offline setup immediately"
    assert setup.calls[0]["process"]["returncode"] == 0
    assert native.calls == [] and native.isolations == []
    assert attempt["native"]["runtime"] is None and attempt["native"]["preparation"] is None
    assert not (trial / "private/reservation.json").exists()
    index = assert_setup_evidence(trial, attempt, setup, seed, status="failed")
    assert index["steps"][0]["id"] == "version"
