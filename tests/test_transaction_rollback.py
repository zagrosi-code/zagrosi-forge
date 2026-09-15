from __future__ import annotations

import json
import os
import signal
from pathlib import Path

import pytest
from detached_test_support import (
    assert_canonical_json_file,
    canonical_json_bytes_for_test,
    copy_implementation_plugin,
    detached_record_arguments,
    file_sha256,
    implementation_source_args,
    make_detached_record_fixture,
    planning_tree_snapshot,
    replace_file,
    write_test_admission_pinner,
)
from fault_injection_support import (
    instrument_record_crashpoints,
)
from forge_test_helpers import (
    load_zagrosi_module,
    run_script_raw,
    write_non_topological_section_fixture,
)


@pytest.mark.parametrize("adopted_final", (False, True))
def test_candidate_recovery_failure_rolls_back_state_and_only_invocation_created_final(
    tmp_path: Path,
    adopted_final: bool,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"candidate-rollback-{adopted_final}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-candidate-rollback-{adopted_final}",
        plugin_root=plugin_root,
    )
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "state-cas-fsync"
    if adopted_final:
        environment["ZAGROSI_TEST_RECORD_ADOPT_PREEXISTING"] = "1"
    crashed = run_script_raw(
        script,
        *detached_record_arguments(
            fixture,
            "--evidence-row",
            "record_gate=evidence/record-gate.json",
        ),
        env=environment,
    )
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
    final_path = fixture.implementation_root / transaction["pinner_path"]
    assert final_path.is_file()
    (fixture.implementation_root / "evidence" / "record-gate.json").write_bytes(
        b'{"schema":"test-record-gate-v1","verdict":"DRIFT"}\n'
    )

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert recovered.returncode == 1
    assert json.loads(recovered.stdout)["error_code"] == "section-record-recovery-required"
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == base_state_raw
    assert final_path.exists() is adopted_final
    assert not transaction_dir.exists()


def test_forward_candidate_with_missing_final_durably_rolls_back_without_repromotion(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "candidate-missing-final")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-candidate-missing-final", plugin_root=plugin_root)
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "state-cas-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
    final_path = fixture.implementation_root / transaction["pinner_path"]
    final_path.unlink()
    assert (transaction_dir / "pinner.json").stat().st_nlink == 1

    rolled_back = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert rolled_back.returncode == 1
    assert json.loads(rolled_back.stdout)["error_code"] == "section-record-recovery-required"
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == base_state_raw
    assert not final_path.exists()
    assert not transaction_dir.exists()

    retried = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert retried.returncode == 0, retried.stderr + retried.stdout
    assert json.loads(retried.stdout)["next_section"] == fixture.section


def test_forward_candidate_missing_final_rollback_survives_real_sigkill_replay(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "candidate-missing-final-sigkill")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / "fixture-candidate-missing-final-sigkill",
        plugin_root=plugin_root,
    )
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    initial_environment = dict(os.environ)
    initial_environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "state-cas-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=initial_environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
    final_path = fixture.implementation_root / transaction["pinner_path"]
    final_path.unlink()

    rollback_environment = dict(os.environ)
    rollback_environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "rollback-rename-fsync"
    rollback_crashed = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
        env=rollback_environment,
    )
    assert rollback_crashed.returncode == -signal.SIGKILL
    assert (transaction_dir / "rollback.json").is_file()
    assert not (transaction_dir / "transaction.json").exists()

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == base_state_raw
    assert json.loads(recovered.stdout)["next_section"] == fixture.section
    assert not transaction_dir.exists()
    assert not final_path.exists()


def test_rollback_restores_base_before_refusing_drifted_predecessor_closure(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "rollback-predecessor-drift")
    script = instrument_record_crashpoints(plugin_root)
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    setup = run_script_raw(
        script,
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(plugin_root),
        "--flight",
        "off",
    )
    assert setup.returncode == 0, setup.stderr + setup.stdout

    def record_args(section: str, commit: str) -> list[str]:
        review_dir = implementation_root / "code_review"
        (review_dir / f"{section}-review.md").write_text("# Review\n\nNo blockers.\n")
        (review_dir / f"{section}-decisions.md").write_text("# Decisions\n\nAccepted.\n")
        return [
            "implement-record-section",
            "--sections-dir",
            str(sections),
            "--implementation-root",
            str(implementation_root),
            "--section",
            section,
            "--commit",
            commit,
            "--review-artifact",
            f"code_review/{section}-review.md",
            "--review-artifact",
            f"code_review/{section}-decisions.md",
            "--verification",
            f"uv run pytest tests/test_{section}.py",
            "--flight",
            "off",
        ]

    storage = run_script_raw(script, *record_args("section-03-storage", "storage-1"))
    assert storage.returncode == 0, storage.stderr + storage.stdout
    state_path = implementation_root / "zagrosi_implement_state.json"
    base_state_raw = state_path.read_bytes()
    base_state = json.loads(base_state_raw)
    storage_path = implementation_root / base_state["completed_sections"]["section-03-storage"]["pinner_path"]

    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "state-cas-fsync"
    crashed = run_script_raw(
        script,
        *record_args("section-01-foundation", "foundation-1"),
        env=environment,
    )
    assert crashed.returncode == -signal.SIGKILL
    drifted_storage = assert_canonical_json_file(storage_path)
    replace_file(
        storage_path,
        canonical_json_bytes_for_test({**drifted_storage, "notes": "predecessor drift"}),
        mode=0o600,
    )

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(planning),
        "--implementation-root",
        str(implementation_root),
    )
    assert recovered.returncode == 1
    assert json.loads(recovered.stdout)["error_code"] == "section-record-recovery-required"
    assert state_path.read_bytes() == base_state_raw
    transaction_dir = implementation_root / "pinners" / ".record-section-transaction-v1"
    assert (transaction_dir / "rollback.json").is_file()
    assert not (transaction_dir / "transaction.json").exists()


def test_record_adopts_exact_preexisting_pinner_and_preserves_single_link_final(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "adopted-success")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-adopted-success", plugin_root=plugin_root)
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_ADOPT_PREEXISTING"] = "1"
    result = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    final_path = Path(payload["pinner_path"])
    assert final_path.is_file()
    assert final_path.stat().st_nlink == 1
    assert not (
        fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    ).exists()


def test_record_conflicting_preexisting_pinner_is_retained_and_never_adopted(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "adopted-conflict")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-adopted-conflict", plugin_root=plugin_root)
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_ADOPT_PREEXISTING"] = "wrong"
    result = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == "section-record-recovery-required"
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == base_state_raw
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
    final_path = fixture.implementation_root / transaction["pinner_path"]
    assert final_path.is_file()
    assert final_path.stat().st_nlink == 1
    assert final_path.read_bytes() != (transaction_dir / "pinner.json").read_bytes()


@pytest.mark.parametrize("failure", ("state-rollback", "created-final-unlink"))
def test_candidate_recovery_rollback_failure_retains_transaction_for_retry(
    tmp_path: Path,
    monkeypatch,
    failure: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"candidate-rollback-failure-{failure}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-candidate-rollback-failure-{failure}",
        plugin_root=plugin_root,
    )
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "state-cas-fsync"
    crashed = run_script_raw(
        script,
        *detached_record_arguments(
            fixture,
            "--evidence-row",
            "record_gate=evidence/record-gate.json",
        ),
        env=environment,
    )
    assert crashed.returncode == -signal.SIGKILL
    candidate_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
    final_path = fixture.implementation_root / transaction["pinner_path"]
    (fixture.implementation_root / "evidence" / "record-gate.json").write_bytes(
        b'{"schema":"test-record-gate-v1","verdict":"DRIFT"}\n'
    )

    module = load_zagrosi_module(script)
    if failure == "state-rollback":
        monkeypatch.setattr(
            module.transaction_io,
            "replace_state_from_rollback",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected rollback write failure")),
        )
    else:
        monkeypatch.setattr(
            module.transaction_io,
            "unlink_invocation_created_section_pinner",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected pinner unlink failure")),
        )
    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(
        module.output,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.cli.build_parser().parse_args(
        [
            "next-section",
            "--planning-dir",
            str(fixture.planning),
            "--implementation-root",
            str(fixture.implementation_root),
        ]
    )
    assert parsed.func(parsed) == 1
    assert captured[-1][0]["error_code"] == "section-record-recovery-required"
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == candidate_state_raw
    assert (transaction_dir / "rollback.json").is_file()
    assert final_path.exists() is (failure == "created-final-unlink")


@pytest.mark.parametrize(
    "crashpoint",
    (
        "rollback-rename-before-dir-fsync",
        "rollback-rename-fsync",
        "rollback-final-delete-fsync",
        "rollback-state-temp-fsync",
        "rollback-state-replace-before-root-fsync",
        "rollback-state-replace-fsync",
        "rollback-unlink-before-dir-fsync",
        "rollback-unlink-fsync",
        "stage-cleanup-fsync",
    ),
)
def test_section_record_real_sigkill_resumes_durable_rollback_edges(
    tmp_path: Path,
    crashpoint: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / crashpoint)
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / f"fixture-{crashpoint}", plugin_root=plugin_root)
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    environment = dict(os.environ)
    environment.update(
        {
            "ZAGROSI_TEST_RECORD_CRASHPOINT": crashpoint,
            "ZAGROSI_TEST_FORCE_ROLLBACK": "1",
            "ZAGROSI_TEST_FORCE_ROLLBACK_PATH": str(fixture.evidence_path),
        }
    )
    crashed = run_script_raw(
        script,
        *detached_record_arguments(
            fixture,
            "--evidence-row",
            "record_gate=evidence/record-gate.json",
        ),
        env=environment,
    )
    assert crashed.returncode == -signal.SIGKILL

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    assert json.loads(recovered.stdout)["next_section"] == fixture.section
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == base_state_raw
    assert not (
        fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    ).exists()
    assert list((fixture.implementation_root / "pinners").glob(f"{fixture.section}-*.json")) == []


@pytest.mark.parametrize("root_state", ("base", "candidate"))
def test_no_journal_state_temp_is_retained_as_unreachable_for_base_and_candidate(
    tmp_path: Path,
    root_state: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"no-journal-state-{root_state}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-no-journal-state-{root_state}",
        plugin_root=plugin_root,
    )
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    if root_state == "candidate":
        environment = dict(os.environ)
        environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "state-cas-fsync"
        crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
        assert crashed.returncode == -signal.SIGKILL
        transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
        final_path = fixture.implementation_root / transaction["pinner_path"]
        (transaction_dir / "transaction.json").unlink()
        (transaction_dir / "pinner.json").unlink()
        final_path.unlink()
    else:
        transaction_dir.mkdir(mode=0o700)
    state_temp = transaction_dir / "state.json"
    state_temp.write_bytes(base_state_raw)
    state_temp.chmod(0o600)
    before = planning_tree_snapshot(fixture.implementation_root)

    result = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == "section-record-recovery-required"
    assert planning_tree_snapshot(fixture.implementation_root) == before
