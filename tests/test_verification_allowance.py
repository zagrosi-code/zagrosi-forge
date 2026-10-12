"""Optional verification allocation preserves evidence; fixture seconds are not timings."""

import errno
import json
import os
from pathlib import Path

import pytest

from test_compact_plan import SECTION
from test_compatibility_checks import COMMAND
from test_verification_diagnostics import compatibility_inputs, fake_executor, saved_receipt
from test_verification_receipts import invoke, record_args, serialization_lock, verify_args, workspace


def allowance_path(workspace):
    return workspace[1] / "implementation/verification/allowance.json"


def assert_account(workspace, limit, measured=0, unmeasured=0, pending=0):
    _, planning, target = workspace
    assert json.loads(allowance_path(workspace).read_text()) == {
        "version": 1, "planning_dir": str(planning.resolve()), "target_dir": str(target.resolve()),
        "limit_seconds": limit, "measured_seconds": measured,
        "unmeasured_seconds": unmeasured, "pending_seconds": pending,
    }


def verify(workspace, capsys, *options):
    return invoke(workspace, capsys, *verify_args(workspace, *options, "--", *COMMAND))


def evidence_bytes(workspace):
    directory = allowance_path(workspace).parent
    return {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*")
            if path.is_file() and path != allowance_path(workspace)}


def test_unconfigured_calls_stay_unlimited_and_opt_in_is_prospective(workspace, capsys, monkeypatch):
    calls, process = fake_executor(workspace[0], monkeypatch)
    process["seconds"] = 3
    for _ in range(2):
        code, result = verify(workspace, capsys)
        assert code == 0 and "allowance" not in result
        assert "allowance" not in json.loads(Path(result["receipt_path"]).read_text())
        assert not allowance_path(workspace).exists()
    code, result = verify(workspace, capsys, "--allowance-seconds", "10")
    assert code == 0 and isinstance(result["allowance"], dict)
    assert_account(workspace, 10, measured=3)
    assert [call[2]["timeout"] for call in calls] == [600, 600, 10]


def test_failures_timeouts_and_extension_preserve_cumulative_spend(workspace, capsys, monkeypatch):
    calls, process = fake_executor(workspace[0], monkeypatch)
    process["seconds"] = 3
    assert verify(workspace, capsys, "--allowance-seconds", "10")[0] == 0
    process.update(seconds=4, returncode=3)
    code, result = verify(workspace, capsys, "--allowance-seconds", "10")
    assert code == 1 and result["outcome"] == "failed" and result["seconds"] == 4
    process.update(seconds=3, returncode=124, timed_out=True)
    code, result = verify(workspace, capsys)
    assert code == 1 and result["outcome"] == "timed_out" and result["seconds"] == 3
    assert_account(workspace, 10, measured=10)
    before, account = evidence_bytes(workspace), allowance_path(workspace).read_bytes()
    for options in [(), ("--allowance-seconds", "10"), ("--allowance-seconds", "9")]:
        code, result = verify(workspace, capsys, *options)
        assert code == 1 and not result["success"]
        assert evidence_bytes(workspace) == before and allowance_path(workspace).read_bytes() == account
    assert len(calls) == 3
    process.update(seconds=2, returncode=0, timed_out=False)
    assert verify(workspace, capsys, "--allowance-seconds", "15")[0] == 0
    assert_account(workspace, 15, measured=12)
    assert [call[2]["timeout"] for call in calls] == [10, 7, 3, 5]


def test_zero_persists_exhaustion_and_manual_evidence_cannot_configure_it(workspace, capsys, monkeypatch):
    calls, _ = fake_executor(workspace[0], monkeypatch)
    assert verify(workspace, capsys, "--allowance-seconds", "0")[0] == 1
    assert_account(workspace, 0)
    before = allowance_path(workspace).read_bytes()
    assert verify(workspace, capsys)[0] == 1 and not evidence_bytes(workspace)
    manual = ["--source", "inspection", "--outcome", "passed", "--evidence", "Inspected Unicode caller behavior"]
    code, result = invoke(workspace, capsys, *verify_args(workspace, "--allowance-seconds", "5", *manual))
    assert code == 1 and not result["success"] and not evidence_bytes(workspace)
    assert invoke(workspace, capsys, *verify_args(workspace, *manual))[0] == 0
    assert calls == [] and allowance_path(workspace).read_bytes() == before


@pytest.mark.parametrize("total", ["-1", "nan", "inf"])
def test_invalid_cli_total_refuses_before_execution_or_evidence(workspace, capsys, monkeypatch, total):
    calls, _ = fake_executor(workspace[0], monkeypatch)
    code, result = verify(workspace, capsys, "--allowance-seconds", total)
    assert code == 1 and not result["success"] and result["error"]
    assert calls == [] and not allowance_path(workspace).exists() and not evidence_bytes(workspace)


def test_direct_compatibility_rejects_boolean_total_without_mutation(workspace, monkeypatch):
    forge, planning, target = workspace
    compatibility_inputs(workspace)
    before = evidence_bytes(workspace)
    calls, _ = fake_executor(forge, monkeypatch)
    with pytest.raises(ValueError):
        forge.compatibility.capture(planning, target, SECTION, "baseline", COMMAND, 10, allowance_seconds=True)
    assert calls == [] and evidence_bytes(workspace) == before and not allowance_path(workspace).exists()


def test_direct_compatibility_and_ordinary_capture_share_one_account(workspace, capsys, monkeypatch):
    forge, planning, target = workspace
    compatibility_inputs(workspace)
    calls, process = fake_executor(forge, monkeypatch)
    process["seconds"] = 2
    first = forge.compatibility.capture(planning, target, SECTION, "baseline", COMMAND, 10, allowance_seconds=6)
    assert first["success"] and isinstance(first["allowance"], dict)
    assert verify(workspace, capsys)[0] == 0
    assert forge.compatibility.capture(planning, target, SECTION, "candidate", COMMAND, 10)["success"]
    assert_account(workspace, 6, measured=6)
    before, account = evidence_bytes(workspace), allowance_path(workspace).read_bytes()
    with pytest.raises(ValueError):
        forge.compatibility.capture(planning, target, SECTION, "candidate", COMMAND, 10)
    assert evidence_bytes(workspace) == before and allowance_path(workspace).read_bytes() == account
    assert [call[2]["timeout"] for call in calls] == [6, 4, 2]
    assert forge.compatibility.completion_evidence(planning, target, SECTION)


def test_interruption_blocks_remainder_until_higher_total_retains_uncertainty(workspace, capsys, monkeypatch):
    forge, planning, target = workspace
    calls, process = fake_executor(forge, monkeypatch)
    execute = forge.child_process.execute

    def interrupt(*args, **kwargs):
        assert_account(workspace, 10, pending=3)
        raise KeyboardInterrupt

    monkeypatch.setattr(forge.child_process, "execute", interrupt)
    with pytest.raises(KeyboardInterrupt):
        verify(workspace, capsys, "--allowance-seconds", "10", "--timeout", "3")
    assert_account(workspace, 10, pending=3)
    assert forge.verification.integration_report(planning, target)["outcome"] == "pending"
    before = evidence_bytes(workspace)
    for options in [(), ("--allowance-seconds", "10")]:
        assert verify(workspace, capsys, *options)[0] == 1
        assert evidence_bytes(workspace) == before
    monkeypatch.setattr(forge.child_process, "execute", execute)
    process["seconds"] = 2
    assert verify(workspace, capsys, "--allowance-seconds", "12")[0] == 0
    assert_account(workspace, 12, measured=2, unmeasured=3)
    assert len(calls) == 1 and calls[0][2]["timeout"] == 9
    assert any(json.loads(raw).get("outcome") == "pending" for raw in evidence_bytes(workspace).values())


@pytest.mark.parametrize("duration", [None, -1, float("nan"), float("inf"), True],
                         ids=["missing", "negative", "nan", "infinity", "boolean"])
def test_invalid_duration_retains_pending_allocation_and_cannot_pass(workspace, capsys, monkeypatch, duration):
    forge, planning, target = workspace
    calls, process = fake_executor(forge, monkeypatch)
    if duration is None:
        process.pop("seconds")
    else:
        process["seconds"] = duration
    code, result = verify(workspace, capsys, "--allowance-seconds", "10", "--timeout", "2")
    assert code == 1 and not result["success"] and result["error"]
    assert_account(workspace, 10, pending=2)
    assert len(calls) == 1 and not forge.verification.integration_report(planning, target)["success"]
    before = evidence_bytes(workspace)
    assert verify(workspace, capsys)[0] == 1
    assert len(calls) == 1 and evidence_bytes(workspace) == before


def test_elapsed_overrun_is_charged_even_when_source_change_invalidates_result(workspace, capsys, monkeypatch):
    forge, _, target = workspace
    calls, process = fake_executor(forge, monkeypatch)
    execute = forge.child_process.execute
    process["seconds"] = 4.5

    def mutate(*args, **kwargs):
        (target / "source.py").write_text("value = 2\n")
        return execute(*args, **kwargs)

    monkeypatch.setattr(forge.child_process, "execute", mutate)
    code, result = verify(workspace, capsys, "--allowance-seconds", "3", "--timeout", "2")
    assert code == 1 and result["outcome"] == "failed" and result["seconds"] == 4.5
    assert result["changed_components"] == ["source"]
    assert_account(workspace, 3, measured=4.5)
    before = evidence_bytes(workspace)
    assert verify(workspace, capsys)[0] == 1 and len(calls) == 1
    assert evidence_bytes(workspace) == before


@pytest.mark.parametrize("phase", ["reservation", "settlement"])
def test_account_publication_failure_cannot_launch_or_supply_success(workspace, capsys, monkeypatch, phase):
    forge, planning, target = workspace
    saved_receipt(workspace)
    before = evidence_bytes(workspace)
    calls, process = fake_executor(forge, monkeypatch)
    process["seconds"] = 1
    write = forge.storage.write_json

    def refuse(path, value):
        if Path(path) == allowance_path(workspace) and (
                phase == "reservation" or value["measured_seconds"] > 0):
            raise OSError("fixture allowance publication failed")
        return write(path, value)

    monkeypatch.setattr(forge.storage, "write_json", refuse)
    code, result = verify(workspace, capsys, "--allowance-seconds", "10", "--timeout", "2")
    assert code == 1 and not result["success"] and "publication failed" in result["error"]
    if phase == "reservation":
        assert calls == [] and evidence_bytes(workspace) == before and not allowance_path(workspace).exists()
    else:
        assert len(calls) == 1
        assert_account(workspace, 10, pending=2)
        assert not forge.verification.integration_report(planning, target)["success"]


@pytest.mark.parametrize("field,value", [("target_dir", "/different-target"), ("pending_seconds", True),
                                         ("measured_seconds", float("nan")), ("unmeasured_seconds", -1)])
def test_malformed_saved_account_is_not_reset_or_charged(workspace, capsys, monkeypatch, field, value):
    calls, process = fake_executor(workspace[0], monkeypatch)
    process["seconds"] = 1
    assert verify(workspace, capsys, "--allowance-seconds", "10")[0] == 0
    path = allowance_path(workspace)
    account = json.loads(path.read_text())
    account[field] = value
    path.write_text(json.dumps(account))
    before, raw = evidence_bytes(workspace), path.read_bytes()
    code, result = verify(workspace, capsys, "--allowance-seconds", "20")
    assert code == 1 and not result["success"] and result["error"] and len(calls) == 1
    assert evidence_bytes(workspace) == before and path.read_bytes() == raw


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_aliased_allowance_refuses_without_mutating_user_file(workspace, capsys, monkeypatch, tmp_path, kind):
    _, planning, target = workspace
    calls, _ = fake_executor(workspace[0], monkeypatch)
    path, external = allowance_path(workspace), tmp_path / "user-account.json"
    path.parent.mkdir(parents=True)
    external.write_text(json.dumps({
        "version": 1, "planning_dir": str(planning.resolve()), "target_dir": str(target.resolve()),
        "limit_seconds": 10, "measured_seconds": 0, "unmeasured_seconds": 0, "pending_seconds": 0,
    }))
    try:
        if kind == "symlink":
            path.symlink_to(external)
        else:
            os.link(external, path)
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM} or getattr(exc, "winerror", None) == 1314:
            pytest.skip(f"{kind} unavailable")
        raise
    raw = external.read_bytes()
    code, result = verify(workspace, capsys)
    assert code == 1 and not result["success"] and calls == []
    assert external.read_bytes() == path.read_bytes() == raw and not evidence_bytes(workspace)
    if kind == "symlink":
        assert path.is_symlink()
    else:
        assert path.stat().st_ino == external.stat().st_ino


def test_exhausted_paired_receipts_remain_reusable_until_inputs_change(workspace, capsys, monkeypatch):
    forge, planning, target = workspace
    execute = forge.child_process.execute
    calls, process = fake_executor(forge, monkeypatch)
    process["seconds"] = 1
    code, verified = verify(workspace, capsys, "--section", SECTION, "--integration", "--allowance-seconds", "1")
    assert code == 0
    assert_account(workspace, 1, measured=1)
    before = evidence_bytes(workspace)
    assert verify(workspace, capsys, "--section", SECTION, "--integration")[0] == 1
    assert evidence_bytes(workspace) == before
    monkeypatch.setattr(forge.child_process, "execute", execute)
    monkeypatch.setattr(forge.gates, "local_gate_available", lambda *_: False)
    code, record = invoke(workspace, capsys, *record_args(workspace, "--verification-receipt", verified["receipt_path"]))
    assert code == 0 and "allowance" not in record["record"]["verification_result"]
    args = ["postflight", "--phase", "implement", "--planning-dir", str(planning), "--target-dir", str(target), "--strict"]
    code, result = invoke(workspace, capsys, *args)
    assert code == 0 and result["success"]
    (target / "source.py").write_text("value = 2\n")
    code, result = invoke(workspace, capsys, *args)
    assert code == 1 and "integration-verification" in result["blocking_gates"]
    assert len(calls) == 1 and evidence_bytes(workspace) == before


def test_held_mutable_lock_preserves_account_and_evidence(workspace, capsys, monkeypatch, serialization_lock):
    _, planning, _ = workspace
    calls, process = fake_executor(workspace[0], monkeypatch)
    process["seconds"] = 1
    assert verify(workspace, capsys, "--allowance-seconds", "10")[0] == 0
    before, account = evidence_bytes(workspace), allowance_path(workspace).read_bytes()
    with serialization_lock(planning / "implementation/.mutable-state"):
        code, result = verify(workspace, capsys, "--allowance-seconds", "20")
    assert code == 1 and "Timed out waiting for state lock" in result["error"] and len(calls) == 1
    assert evidence_bytes(workspace) == before and allowance_path(workspace).read_bytes() == account


def test_settlement_failure_retains_completed_process_evidence_and_blocks_retry(workspace, capsys, monkeypatch):
    forge, planning, target = workspace
    calls, process = fake_executor(forge, monkeypatch)
    process.update(returncode=7, seconds=1.25, stdout="distinct completed output", stderr="distinct completed failure")
    write = forge.storage.write_json

    def refuse_settlement(path, value):
        if Path(path) == allowance_path(workspace) and value["measured_seconds"] > 0:
            raise OSError("fixture settlement publication failed")
        return write(path, value)

    monkeypatch.setattr(forge.storage, "write_json", refuse_settlement)
    code, result = verify(workspace, capsys, "--allowance-seconds", "10", "--timeout", "2")
    assert code == 1 and not result["success"] and "settlement publication failed" in result["error"]
    path = forge.verification.receipt_path(planning)
    receipt = json.loads(path.read_text())
    assert receipt["source"] == "captured" and receipt["command"] == COMMAND and receipt["outcome"] == "failed"
    assert (receipt["exit_code"], receipt["seconds"], receipt["stdout_tail"], receipt["stderr_tail"]) == (
        7, 1.25, process["stdout"], process["stderr"])
    assert "settlement publication failed" in receipt["error"]
    assert_account(workspace, 10, pending=2)
    assert not forge.verification.integration_report(planning, target)["success"]
    before, account = evidence_bytes(workspace), allowance_path(workspace).read_bytes()
    assert verify(workspace, capsys)[0] == 1 and len(calls) == 1
    assert evidence_bytes(workspace) == before and allowance_path(workspace).read_bytes() == account


def test_stored_null_allowance_is_not_treated_as_unlimited(workspace, capsys, monkeypatch):
    saved_receipt(workspace)
    path = allowance_path(workspace)
    path.write_bytes(b"null\n")
    before = evidence_bytes(workspace)
    calls, _ = fake_executor(workspace[0], monkeypatch)
    code, result = verify(workspace, capsys)
    assert code == 1 and not result["success"] and result["error"]
    assert calls == [] and path.read_bytes() == b"null\n" and evidence_bytes(workspace) == before


def test_accepted_boolean_timeout_reserves_numeric_seconds_and_can_resume(workspace, monkeypatch):
    forge, planning, target = workspace
    compatibility_inputs(workspace)
    calls, process = fake_executor(forge, monkeypatch)
    execute = forge.child_process.execute

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(forge.child_process, "execute", interrupt)
    with pytest.raises(KeyboardInterrupt):
        forge.compatibility.capture(planning, target, SECTION, "baseline", COMMAND, True, allowance_seconds=10)
    pending = json.loads(allowance_path(workspace).read_text())["pending_seconds"]
    assert type(pending) in (int, float) and pending == 1
    assert_account(workspace, 10, pending=1)
    monkeypatch.setattr(forge.child_process, "execute", execute)
    process["seconds"] = 0.5
    result = forge.compatibility.capture(planning, target, SECTION, "baseline", COMMAND, True, allowance_seconds=12)
    assert result["success"] and len(calls) == 1 and calls[0][2]["timeout"] == 1
    assert_account(workspace, 12, measured=0.5, unmeasured=1)
