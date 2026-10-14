"""Known preparation failures release only their unused verification allocation."""

import json
from pathlib import Path

import pytest

from test_compact_plan import SECTION
from test_compatibility_checks import COMMAND
from test_verification_allowance import allowance_path, assert_account, evidence_bytes, verify
from test_verification_diagnostics import compatibility_inputs, fake_executor, saved_receipt
from test_verification_receipts import workspace


def retained_allowance(workspace, *, pending=0):
    """A prior account with measured work, uncertain work and optional interruption."""
    forge, planning, target = workspace
    forge.storage.write_json(allowance_path(workspace), {
        "version": 1, "planning_dir": str(planning.resolve()), "target_dir": str(target.resolve()),
        "limit_seconds": 10, "measured_seconds": 2, "unmeasured_seconds": 1,
        "pending_seconds": pending,
    })


@pytest.mark.parametrize("phase", ["archive", "pending", "contract"])
def test_ordinary_preparation_failure_releases_only_new_allocation(workspace, capsys, monkeypatch, phase):
    forge, planning, _ = workspace
    saved_receipt(workspace, seconds=2)
    path = forge.verification.receipt_path(planning)
    previous = path.read_bytes()
    retained_allowance(workspace, pending=3)
    calls, process = fake_executor(forge, monkeypatch)
    process["seconds"] = 1
    message = f"fixture {phase} preparation refused"

    with monkeypatch.context() as failure:
        if phase == "archive":
            write = forge.storage.write_bytes

            def refuse_archive(destination, raw):
                if Path(destination).parent == path.parent / "history" / path.stem:
                    raise OSError(message)
                return write(destination, raw)

            failure.setattr(forge.storage, "write_bytes", refuse_archive)
        elif phase == "pending":
            write = forge.storage.write_json

            def refuse_pending(destination, value):
                if Path(destination) == path and value.get("outcome") == "pending":
                    raise OSError(message)
                return write(destination, value)

            failure.setattr(forge.storage, "write_json", refuse_pending)
        else:
            prepared = forge.mutable_inputs.prepared_contract

            def refuse_recheck(*args):
                # Earlier validation sees the old pending 3; only the new reservation is 2.
                if json.loads(allowance_path(workspace).read_text())["pending_seconds"] == 2:
                    raise ValueError(message)
                return prepared(*args)

            failure.setattr(forge.mutable_inputs, "prepared_contract", refuse_recheck)
        code, result = verify(workspace, capsys, "--allowance-seconds", "12", "--timeout", "2")

    assert code == 1 and not result["success"] and result["error"] == message
    assert calls == []
    # The explicit extension retains the OLD pending 3 as uncertain work, plus the earlier 1.
    assert_account(workspace, 12, measured=2, unmeasured=4)
    assert previous in evidence_bytes(workspace).values()
    if phase == "contract":
        assert json.loads(path.read_text())["outcome"] == "pending"
    else:
        assert path.read_bytes() == previous
    options = ("--allowance-seconds", "12") if phase == "pending" else ()
    code, result = verify(workspace, capsys, *options, "--timeout", "2")
    assert code == 0 and result["success"] and len(calls) == 1
    assert_account(workspace, 12, measured=3, unmeasured=4)


@pytest.mark.parametrize("phase", ["pending", "contract"])
def test_compatibility_preparation_failure_preserves_pair_and_allows_retry(workspace, capsys, monkeypatch, phase):
    forge, planning, target = workspace
    compatibility_inputs(workspace)
    calls, process = fake_executor(forge, monkeypatch)
    process["seconds"] = 1
    for stage in ("baseline", "candidate"):
        assert forge.compatibility.capture(planning, target, SECTION, stage, COMMAND, 2)["success"]
    calls.clear()
    path = forge.compatibility.receipt_path(planning, SECTION)
    previous = path.read_bytes()
    original = json.loads(previous)
    retained_allowance(workspace)
    message = f"fixture compatibility {phase} refused"

    with monkeypatch.context() as failure:
        if phase == "pending":
            write = forge.storage.write_json

            def refuse_pending(destination, value):
                if Path(destination) == path and value.get("candidate", {}).get("outcome") == "pending":
                    raise OSError(message)
                return write(destination, value)

            failure.setattr(forge.storage, "write_json", refuse_pending)
            with pytest.raises(OSError, match=message):
                forge.compatibility.capture(planning, target, SECTION, "candidate", COMMAND, 2)
        else:
            prepared = forge.mutable_inputs.prepared_contract

            def refuse_recheck(*args):
                if json.loads(allowance_path(workspace).read_text())["pending_seconds"] == 2:
                    raise ValueError(message)
                return prepared(*args)

            failure.setattr(forge.mutable_inputs, "prepared_contract", refuse_recheck)
            result = forge.compatibility.capture(planning, target, SECTION, "candidate", COMMAND, 2)
            assert not result["success"] and result["error"] == message

    assert calls == []
    assert_account(workspace, 10, measured=2, unmeasured=1)
    pair = json.loads(path.read_text())
    assert pair["origin"] == original["origin"] and pair["baseline"] == original["baseline"]
    if phase == "pending":
        assert path.read_bytes() == previous
    else:
        assert pair["candidate"]["outcome"] == "failed" and pair["candidate"]["error"] == message
        assert {"stage": "candidate", "result": original["candidate"]} in pair["prior_attempts"]
        assert "seconds" not in pair["candidate"]
    options = ("--allowance-seconds", "10") if phase == "pending" else ()
    code, result = verify(workspace, capsys, "--section", SECTION, "--stage", "candidate", *options, "--timeout", "2")
    assert code == 0 and result["success"] and len(calls) == 1
    assert_account(workspace, 10, measured=3, unmeasured=1)
    assert forge.compatibility.completion_evidence(planning, target, SECTION)


@pytest.mark.parametrize("mode", ["ordinary", "baseline"])
def test_preparation_cleanup_write_failure_retains_pending_and_original_error(workspace, capsys, monkeypatch, mode):
    forge, _, _ = workspace
    if mode == "baseline":
        compatibility_inputs(workspace)
    else:
        saved_receipt(workspace)
    retained_allowance(workspace)
    calls, _ = fake_executor(forge, monkeypatch)
    options = () if mode == "ordinary" else ("--section", SECTION, "--stage", "baseline")
    preparation_error, cleanup_error = "fixture refreshed contract refused", "fixture allocation release refused"
    refused = []
    prepared, write = forge.mutable_inputs.prepared_contract, forge.storage.write_json

    def refuse_recheck(*args):
        if json.loads(allowance_path(workspace).read_text())["pending_seconds"] == 2:
            raise ValueError(preparation_error)
        return prepared(*args)

    def refuse_cleanup(destination, value):
        if Path(destination) == allowance_path(workspace) and value["pending_seconds"] == 0:
            refused.append(True)
            raise OSError(cleanup_error)
        return write(destination, value)

    with monkeypatch.context() as failure:
        failure.setattr(forge.mutable_inputs, "prepared_contract", refuse_recheck)
        failure.setattr(forge.storage, "write_json", refuse_cleanup)
        code, result = verify(workspace, capsys, *options, "--timeout", "2")
    assert code == 1 and not result["success"]
    assert preparation_error in result["error"] and cleanup_error in result["error"]
    assert refused and calls == []
    assert_account(workspace, 10, measured=2, unmeasured=1, pending=2)
    before, account = evidence_bytes(workspace), allowance_path(workspace).read_bytes()
    code, result = verify(workspace, capsys, *options, "--timeout", "2")
    assert code == 1 and "unresolved execution accounting" in result["error"] and calls == []
    assert evidence_bytes(workspace) == before and allowance_path(workspace).read_bytes() == account


@pytest.mark.parametrize("mode", ["ordinary", "baseline"])
@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_capture_entry_exception_never_releases_allocation(workspace, capsys, monkeypatch, mode, error):
    forge, _, _ = workspace
    if mode == "baseline":
        compatibility_inputs(workspace)
    else:
        saved_receipt(workspace)
    retained_allowance(workspace)
    options = () if mode == "ordinary" else ("--section", SECTION, "--stage", "baseline")
    entered = []

    def interrupt(*args):
        entered.append(True)
        raise error("fixture capture entered")

    monkeypatch.setattr(forge.verification, "_capture", interrupt)
    if error is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt, match="fixture capture entered"):
            verify(workspace, capsys, *options, "--timeout", "2")
        capsys.readouterr()
    else:
        code, result = verify(workspace, capsys, *options, "--timeout", "2")
        assert code == 1 and not result["success"] and result["error"] == "fixture capture entered"
    assert entered == [True]
    assert_account(workspace, 10, measured=2, unmeasured=1, pending=2)
    before, account = evidence_bytes(workspace), allowance_path(workspace).read_bytes()
    code, result = verify(workspace, capsys, *options, "--timeout", "2")
    assert code == 1 and "unresolved execution accounting" in result["error"]
    assert entered == [True] and evidence_bytes(workspace) == before
    assert allowance_path(workspace).read_bytes() == account
