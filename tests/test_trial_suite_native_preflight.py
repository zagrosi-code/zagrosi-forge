"""Public native preflight controls; every live boundary stops deliberately."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_manifest import validate_suite
from coding_trial_runner import qualify_native_loading, run_suite_writer
from native_reservation_fixtures import make_reserved_native
from test_trial_suite_isolation import tree_state
from test_trial_suite_native_runtime import prepare_auth, forbid_credential_reads


requires_controller = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd,
    reason="Reservation restoration requires the admitted no-follow controller platform")


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Public native preflight controls must never start a process")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def reserved(tmp_path):
    # Opaque synthetic record bytes, not an invented section03 state schema.
    return make_reserved_native(tmp_path, initial_attempt_bytes=b'{"fixture":"initial opaque bytes"}\n',
                                with_admission=True)


@pytest.fixture
def stopped_boundary(monkeypatch):
    calls = []
    def stop(suite, suite_root, layout, evidence_dir, *, roots, auth_file=None):
        calls.append({"layout": layout, "evidence_dir": Path(evidence_dir), "auth_file": auth_file})
        raise ValueError("isolation-unqualified: deliberate synthetic boundary stop; no qualification executed")
    def no_owned(*args, **kwargs):
        pytest.fail("No native command may run after the deliberate isolation stop")
    monkeypatch.setattr("coding_trial_loading.qualify_profile", stop)
    monkeypatch.setattr("coding_trial_loading.run_owned", no_owned)
    return calls


def invoke(fixture, *, writer=False, auth_file=None):
    function = run_suite_writer if writer else qualify_native_loading
    evidence = fixture["writer_evidence"] if writer else fixture["loading_evidence"]
    return function(fixture["suite"], fixture["suite_root"], "normalize", fixture["arm"], fixture["roots"],
                    evidence, auth_file=auth_file)


def state(fixture):
    return tree_state(fixture["suite_root"]), tree_state(fixture["native"].parent)


@requires_controller
def test_valid_static_proofs_reach_fresh_isolation_without_historical_loading_or_isolation(reserved, stopped_boundary):
    suite = reserved["suite"]
    assert validate_suite(suite, reserved["suite_root"]) == suite
    assert suite["host"]["isolation"]["probe_receipt"] is None
    assert suite["arms"][reserved["arm"]]["loading"]["receipt"] is None
    with pytest.raises(ValueError, match="^isolation-unqualified:"):
        invoke(reserved)
    assert len(stopped_boundary) == 1
    assert stopped_boundary[0]["evidence_dir"] == reserved["loading_evidence"] / "isolation-before"
    assert not reserved["writer_evidence"].exists()


@pytest.mark.parametrize("change", ["record-bytes", "same-bytes-new-workspace"])
@requires_controller
def test_public_bootstrap_rejects_stale_creation_identity_before_isolation(reserved, stopped_boundary, tmp_path, change):
    if change == "record-bytes":
        reserved["trial_path"].write_bytes(b'{"fixture":"changed opaque bytes"}\n')
    else:
        workspace = Path(reserved["roots"]["workspace"])
        original = tmp_path / "original-workspace"
        workspace.rename(original)
        shutil.copytree(original, workspace, symlinks=True)
    before = state(reserved)
    with pytest.raises(ValueError):
        invoke(reserved)
    assert stopped_boundary == []
    assert state(reserved) == before


@pytest.mark.parametrize("collision", ["loading", "writer", "execution.lock"])
@requires_controller
def test_existing_attempt_evidence_or_lock_is_not_replayed_or_removed(reserved, stopped_boundary, collision):
    path = reserved["native"].parent / "private" / collision
    if collision == "execution.lock":
        path.write_text("Synthetic prior owner", encoding="utf-8")
    else:
        path.mkdir()
        (path / "retained.txt").write_text("Prior evidence must survive", encoding="utf-8")
    before = state(reserved)
    with pytest.raises(ValueError, match="^input-exists:"):
        invoke(reserved)
    assert stopped_boundary == []
    assert state(reserved) == before


@pytest.mark.parametrize("proof", ["environment", "task"])
def test_explicit_bootstrap_does_not_waive_required_static_admission(reserved, stopped_boundary, proof):
    task = reserved["suite"]["tasks"]["normalize"]
    if proof == "environment":
        task["dependencies"]["environment"] = None
    else:
        task["admission"] = None
    before = state(reserved)
    with pytest.raises(ValueError):
        invoke(reserved)
    assert stopped_boundary == []
    assert state(reserved) == before


def test_ordinary_writer_cannot_implicitly_bootstrap_missing_loading(reserved, stopped_boundary):
    before = state(reserved)
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        invoke(reserved, writer=True)
    assert stopped_boundary == []
    assert state(reserved) == before


@pytest.mark.parametrize("writer", [False, True])
def test_public_native_auth_alias_is_rejected_before_any_private_resource_read(reserved, stopped_boundary,
                                                                             tmp_path, monkeypatch, writer):
    prepare_auth(reserved, tmp_path)
    reserved["roots"]["private"] = [str(reserved["native"].parent / "private")]
    oracle = reserved["suite_root"] / "checks/oracle.py"
    forbid_credential_reads(monkeypatch, oracle)
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        invoke(reserved, writer=writer, auth_file=oracle)
    assert stopped_boundary == []


@pytest.mark.parametrize("gate", ["environment", "task"])
def test_missing_admission_reports_its_named_gate_without_an_unrelated_stale_task_proof(reserved, stopped_boundary, gate):
    task = reserved["suite"]["tasks"]["normalize"]
    task["admission"] = None
    if gate == "environment":
        task["dependencies"]["environment"] = None
    before = state(reserved)
    with pytest.raises(ValueError, match="^" + gate + "-unqualified:"):
        invoke(reserved)
    assert stopped_boundary == []
    assert state(reserved) == before


@pytest.mark.parametrize("writer", [False, True])
@pytest.mark.parametrize("malformed", ["isolation-key", "dependencies-object", "environment-key"])
def test_malformed_admission_is_a_named_error_before_execution(reserved, stopped_boundary, writer, malformed):
    task = reserved["suite"]["tasks"]["normalize"]
    if malformed == "isolation-key":
        del reserved["suite"]["host"]["isolation"]
    elif malformed == "dependencies-object":
        task["dependencies"] = None
    else:
        del task["dependencies"]["environment"]
    before = state(reserved)
    with pytest.raises(ValueError, match="^[a-z-]+:"):
        invoke(reserved, writer=writer)
    assert stopped_boundary == []
    assert state(reserved) == before
