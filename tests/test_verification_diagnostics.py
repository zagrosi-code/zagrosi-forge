"""Verification metadata preserves measured costs and strict receipt freshness.

Executor results below are deliberate fixtures, not actual timing measurements.
"""

import copy
import json
from pathlib import Path

import pytest

from test_compact_plan import SECTION
from test_compatibility_checks import CHECK, COMMAND, activate, block, contract, run
from test_verification_receipts import invoke, verify_args, workspace


STALE = "Verification inputs changed; rerun verification against the current source and contract."
OUTCOMES = [(0, False, "passed"), (3, False, "failed"), (124, True, "timed_out")]


def fake_executor(forge, monkeypatch):
    calls = []
    result = {"returncode": 0, "timed_out": False, "seconds": 12.375,
              "stdout": "captured stdout", "stderr": "captured stderr",
              "stdout_bytes": 15, "stderr_bytes": 15,
              "stdout_truncated": False, "stderr_truncated": False}

    def execute(argv, target, **options):
        calls.append((list(argv), target, options))
        return result.copy()

    monkeypatch.setattr(forge.child_process, "execute", execute)
    return calls, result


def compatibility_inputs(workspace):
    _, planning, target = workspace
    (target / "source.py").write_text("def value(name='x'):\n    return name.strip()\n")
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + block(contract()))
    activate(workspace)
    (target / "checks.py").write_text(CHECK)


def saved_receipt(workspace, **changes):
    forge, planning, target = workspace
    receipt = {"version": 1, "source": "captured", "outcome": "passed",
               "command": ["fixture-command"], "exit_code": 0,
               "snapshot": forge.mutable_inputs.verification_snapshot(planning, target), **changes}
    forge.storage.write_json(forge.verification.receipt_path(planning), receipt)
    return receipt


@pytest.mark.parametrize("exit_code,timed_out,outcome", OUTCOMES)
@pytest.mark.parametrize("stage", ["ordinary", "baseline", "candidate"])
def test_exact_duration_survives_capture_and_returned_summary(workspace, capsys, monkeypatch,
                                                             stage, exit_code, timed_out, outcome):
    forge, planning, target = workspace
    calls, measured = fake_executor(forge, monkeypatch)
    if stage != "ordinary":
        compatibility_inputs(workspace)
        if stage == "candidate":
            assert run(workspace, "baseline")["success"]
    measured.update(returncode=exit_code, timed_out=timed_out)
    extra = [] if stage == "ordinary" else ["--section", SECTION, "--stage", stage]
    code, result = invoke(workspace, capsys, *verify_args(workspace, *extra, "--", *COMMAND))
    receipt = json.loads(Path(result["receipt_path"]).read_text())
    if stage != "ordinary":
        receipt = receipt[stage]
    assert code == (0 if outcome == "passed" else 1)
    for observed in (result, receipt):
        assert observed["seconds"] == measured["seconds"]
        assert observed["outcome"] == outcome
        assert observed["exit_code"] == exit_code
        assert observed["stdout_tail"] == measured["stdout"]
        assert observed["stderr_tail"] == measured["stderr"]
    assert receipt["command"] == COMMAND
    assert calls[-1][0:2] == (COMMAND, target)
    if stage == "ordinary":
        report = forge.verification.integration_report(planning, target)
        assert report["seconds"] == measured["seconds"]
        assert report["success"] is (outcome == "passed")
        assert result["changed_components"] == ([] if outcome == "passed" else None)
        assert report["changed_components"] == ([] if outcome == "passed" else None)
    assert len(calls) == (2 if stage == "candidate" else 1)


def test_old_fresh_receipt_without_duration_remains_reusable(workspace, monkeypatch):
    forge, planning, target = workspace
    old = saved_receipt(workspace)
    path = forge.verification.receipt_path(planning)
    before = path.read_bytes()
    observations = []

    def observe(*args):
        observations.append(args)
        return copy.deepcopy(old["snapshot"])

    monkeypatch.setattr(forge.mutable_inputs, "verification_snapshot", observe)
    monkeypatch.setattr(forge.child_process, "execute", lambda *_a, **_k: pytest.fail("Readback executed tests"))
    report = forge.verification.integration_report(planning, target)
    assert report["success"] and report["changed_components"] == []
    assert report.get("seconds") is None
    assert len(observations) == 1
    assert forge.verification.receipt_error(old, planning, target) is None
    assert len(observations) == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize("source", ["attestation", "inspection"])
def test_manual_evidence_has_no_invented_duration(workspace, capsys, monkeypatch, source):
    forge, planning, target = workspace
    monkeypatch.setattr(forge.child_process, "execute", lambda *_a, **_k: pytest.fail("Manual evidence executed tests"))
    code, result = invoke(workspace, capsys, *verify_args(
        workspace, "--source", source, "--outcome", "passed", "--evidence", "Inspected Unicode preservation"))
    receipt = json.loads(Path(result["receipt_path"]).read_text())
    report = forge.verification.integration_report(planning, target)
    assert code == 0
    for observed in (result, receipt, report):
        assert observed["source"] == source and observed.get("seconds") is None
    assert result["changed_components"] == report["changed_components"] == []


def test_interrupted_capture_does_not_reuse_an_old_duration(workspace, capsys, monkeypatch):
    forge, planning, target = workspace
    saved_receipt(workspace, seconds=73.25)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(forge.child_process, "execute", interrupt)
    with pytest.raises(KeyboardInterrupt):
        invoke(workspace, capsys, *verify_args(workspace, "--", *COMMAND))
    receipt = json.loads(forge.verification.receipt_path(planning).read_text())
    report = forge.verification.integration_report(planning, target)
    assert receipt["outcome"] == report["outcome"] == "pending"
    assert receipt.get("seconds") is report.get("seconds") is None
    assert report["changed_components"] is None and not report["success"]


@pytest.mark.parametrize("change,expected", [
    ("source_digest", ["source"]), ("source_file_count", ["source"]),
    ("contract_digest", ["contract"]), ("planning_dir", ["planning_dir"]),
    ("target_dir", ["target_dir"]), ("section", ["section"]),
    ("version", ["snapshot"]), ("unknown", ["snapshot"]),
    ("missing_section", ["snapshot"]), ("null", ["snapshot"]),
    ("list", ["snapshot"]), ("string", ["snapshot"]),
])
def test_stale_snapshot_explains_components_without_projecting_away_differences(
        workspace, monkeypatch, change, expected):
    forge, planning, target = workspace
    receipt = saved_receipt(workspace)
    current = copy.deepcopy(receipt["snapshot"])
    previous = receipt["snapshot"]
    if change == "unknown":
        previous["unrecognized"] = "must not be ignored"
    elif change == "missing_section":
        previous.pop("section")  # Missing is not equal to an explicit null selector.
    elif change in {"null", "list", "string"}:
        receipt["snapshot"] = {"null": None, "list": [], "string": "invalid"}[change]
    elif change in {"version", "source_file_count"}:
        previous[change] += 1
    elif change in {"source_digest", "contract_digest"}:
        previous[change] = ("0" if previous[change] != "0" * 64 else "1") * 64
    else:
        previous[change] = "different"
    forge.storage.write_json(forge.verification.receipt_path(planning), receipt)
    observations = []

    def observe(*args):
        observations.append(args)
        return copy.deepcopy(current)

    monkeypatch.setattr(forge.mutable_inputs, "verification_snapshot", observe)
    monkeypatch.setattr(forge.child_process, "execute", lambda *_a, **_k: pytest.fail("Readback executed tests"))
    report = forge.verification.integration_report(planning, target)
    assert report["success"] is False and report["error"] == STALE
    assert report["changed_components"] == expected
    assert len(observations) == 1
    assert forge.verification.receipt_error(receipt, planning, target) == STALE
    assert len(observations) == 2  # Each public validation observes once.


@pytest.mark.parametrize("field,value", [
    ("source_digest", None), ("contract_digest", []),
    ("source_digest", "g" * 64), ("contract_digest", "a" * 63),
    ("source_file_count", []), ("source_file_count", -1), ("source_file_count", False),
])
def test_unequal_malformed_known_fields_report_snapshot(workspace, monkeypatch, field, value):
    forge, planning, target = workspace
    receipt = saved_receipt(workspace)
    current = copy.deepcopy(receipt["snapshot"])
    assert value != current[field]  # Diagnostic shape checks cannot redefine equality.
    receipt["snapshot"][field] = value
    forge.storage.write_json(forge.verification.receipt_path(planning), receipt)
    observations = []

    def observe(*args):
        observations.append(args)
        return current

    monkeypatch.setattr(forge.mutable_inputs, "verification_snapshot", observe)
    report = forge.verification.integration_report(planning, target)
    assert report["success"] is False and report["error"] == STALE
    assert report["changed_components"] == ["snapshot"]
    assert len(observations) == 1


@pytest.mark.parametrize("field", ["version", "source_file_count"])
def test_existing_full_snapshot_equality_precedes_diagnostic_shape_checks(workspace, monkeypatch, field):
    forge, planning, target = workspace
    receipt = saved_receipt(workspace)
    current = copy.deepcopy(receipt["snapshot"])
    receipt["snapshot"][field] = True if field == "version" else float(current[field])
    assert receipt["snapshot"] == current
    forge.storage.write_json(forge.verification.receipt_path(planning), receipt)
    monkeypatch.setattr(forge.mutable_inputs, "verification_snapshot", lambda *_a: current)
    report = forge.verification.integration_report(planning, target)
    assert report["success"] is True and report["error"] is None
    assert report["changed_components"] == []


def test_combined_changes_are_deterministic_and_source_is_not_duplicated(workspace, monkeypatch):
    forge, planning, target = workspace
    receipt = saved_receipt(workspace)
    current = copy.deepcopy(receipt["snapshot"])
    current.update(source_digest="a" * 64, contract_digest="b" * 64,
                   source_file_count=current["source_file_count"] + 1)
    observations = []

    def observe(*args):
        observations.append(args)
        return current

    monkeypatch.setattr(forge.mutable_inputs, "verification_snapshot", observe)
    first = forge.verification.integration_report(planning, target)
    second = forge.verification.integration_report(planning, target)
    assert first["changed_components"] == second["changed_components"]
    assert sorted(first["changed_components"]) == ["contract", "source"]
    assert not first["success"] and len(observations) == 2


@pytest.mark.parametrize("changes,error", [
    ({"outcome": "failed"}, "Verification outcome is failed, not passed."),
    ({"exit_code": 3}, "Captured verification requires a command and exit code zero."),
    ({"source": "inspection", "evidence": []}, "Attestation and inspection require substantive evidence."),
])
def test_early_errors_keep_precedence_without_snapshot_observation(workspace, monkeypatch, changes, error):
    forge, planning, target = workspace
    receipt = saved_receipt(workspace, **changes)
    monkeypatch.setattr(forge.mutable_inputs, "verification_snapshot", lambda *_a: pytest.fail("Invalid result scanned source"))
    report = forge.verification.integration_report(planning, target)
    assert not report["success"] and report["error"] == error
    assert report["changed_components"] is None
    assert forge.verification.receipt_error(receipt, planning, target) == error


def test_unavailable_current_inputs_are_unknown_not_unchanged(workspace, monkeypatch):
    forge, planning, target = workspace
    saved_receipt(workspace, seconds=12.375)
    observations = []

    def unavailable(*args):
        observations.append(args)
        raise OSError("fixture source unreadable")

    monkeypatch.setattr(forge.mutable_inputs, "verification_snapshot", unavailable)
    report = forge.verification.integration_report(planning, target)
    assert not report["success"] and report["changed_components"] is None
    assert report["error"] == "Cannot verify current inputs: fixture source unreadable"
    assert report["seconds"] == 12.375 and len(observations) == 1


def test_explicit_reverification_executes_again_and_preserves_snapshot_shape(workspace, capsys, monkeypatch):
    forge, planning, target = workspace
    before = forge.mutable_inputs.verification_snapshot(planning, target)
    calls, _ = fake_executor(forge, monkeypatch)
    for _ in range(2):
        code, result = invoke(workspace, capsys, *verify_args(workspace, "--", *COMMAND))
        assert code == 0 and result["changed_components"] == []
        receipt = json.loads(Path(result["receipt_path"]).read_text())
        assert receipt["snapshot"] == before
    assert len(calls) == 2  # Saved freshness is not an arbitrary-command cache.


def test_source_changed_by_command_fails_with_measured_duration_and_one_after_observation(
        workspace, capsys, monkeypatch):
    forge, planning, target = workspace
    snapshots = []
    original = forge.mutable_inputs.verification_snapshot

    def observe(*args):
        snapshots.append(args)
        return original(*args)

    def execute(*args, **kwargs):
        (target / "source.py").write_text("value = 2\n")
        return {"returncode": 0, "timed_out": False, "seconds": 4.125, "stdout": "done", "stderr": ""}

    monkeypatch.setattr(forge.mutable_inputs, "verification_snapshot", observe)
    monkeypatch.setattr(forge.child_process, "execute", execute)
    code, result = invoke(workspace, capsys, *verify_args(workspace, "--", *COMMAND))
    assert code == 1 and result["outcome"] == "failed" and result["error"] == STALE
    assert result["changed_components"] == ["source"] and result["seconds"] == 4.125
    assert len(snapshots) == 2  # One before execution and one current observation.


@pytest.mark.parametrize("readback", ["snapshot", "declared_inputs"])
@pytest.mark.parametrize("exit_code,timed_out,outcome", OUTCOMES)
def test_compatibility_readback_error_retains_the_completed_process(
        workspace, monkeypatch, readback, exit_code, timed_out, outcome):
    forge, planning, target = workspace
    compatibility_inputs(workspace)
    owner, name = ((forge.mutable_inputs, "verification_snapshot") if readback == "snapshot"
                   else (forge.compatibility, "_observations"))
    original = getattr(owner, name)
    completed = False

    def observe(*args, **kwargs):
        if completed:
            raise OSError("fixture readback failed after execution")
        return original(*args, **kwargs)

    def execute(*args, **kwargs):
        nonlocal completed
        completed = True
        return {"returncode": exit_code, "timed_out": timed_out, "seconds": 9.625,
                "stdout": "retained stdout", "stderr": "retained stderr"}

    monkeypatch.setattr(owner, name, observe)
    monkeypatch.setattr(forge.child_process, "execute", execute)
    result = run(workspace, "baseline")
    receipt = json.loads(Path(result["receipt_path"]).read_text())["baseline"]
    assert not result["success"] and result["error"] == "fixture readback failed after execution"
    for observed in (result, receipt):
        assert observed["outcome"] == "failed"
        assert observed["exit_code"] == exit_code
        assert observed["seconds"] == 9.625
        assert observed["stdout_tail"] == "retained stdout"
        assert observed["stderr_tail"] == "retained stderr"
    assert receipt["command"] == COMMAND and receipt["completed_at"]
