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
    make_detached_record_fixture,
    pinner_state_record_for_test,
    planning_tree_snapshot,
    replace_file,
    tree_bytes_metadata_snapshot,
)
from fault_injection_support import (
    instrument_record_crashpoints,
)
from forge_test_helpers import (
    load_zagrosi_module,
    run_script_raw,
)


def test_identical_same_second_leaf_rerecord_refuses_before_transaction_publication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "same-second-noop")
    fixture = make_detached_record_fixture(tmp_path / "fixture-same-second-noop", plugin_root=plugin_root)
    module = load_zagrosi_module(fixture.script)
    monkeypatch.setattr(module.storage, "now_iso", lambda: "2026-08-22T12:00:00Z")
    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(
        module.output,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.cli.build_parser().parse_args(detached_record_arguments(fixture))
    assert parsed.func(parsed) == 0
    assert captured[-1][0]["transaction_status"] == "committed-clean"
    before = tree_bytes_metadata_snapshot(fixture.implementation_root)
    publication_called = False

    def refuse_publication(*args, **kwargs):
        nonlocal publication_called
        publication_called = True
        raise AssertionError("no-op rerecord reached staged pinner publication")

    monkeypatch.setattr(module.transaction_io, "publish_section_record_staged_pinner", refuse_publication)
    captured.clear()
    assert parsed.func(parsed) == 1
    assert captured[-1][0]["error_code"] == "section-record-state-conflict"
    assert publication_called is False
    assert tree_bytes_metadata_snapshot(fixture.implementation_root) == before
    assert not (
        fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    ).exists()


@pytest.mark.parametrize(
    ("crashpoint", "recovers_completed"),
    (
        ("pinner-tmp-partial", False),
        ("pinner-tmp-write-fsync", False),
        ("pinner-rename-before-dir-fsync", False),
        ("pinner-rename-dir-fsync", False),
        ("journal-write-temp-partial", False),
        ("journal-write-temp-fsync", False),
        ("journal-temp-rename-before-dir-fsync", False),
        ("journal-temp-rename-fsync", False),
        ("journal-rename-before-dir-fsync", True),
        ("journal-rename-fsync", True),
        ("final-link-before-dir-fsync", True),
        ("final-link-fsync", True),
        ("forward-state-temp-fsync", True),
        ("forward-state-replace-before-root-fsync", True),
        ("state-cas-fsync", True),
        ("post-state-validation", True),
        ("journal-unlink-fsync", True),
        ("journal-unlink-before-dir-fsync", True),
        ("stage-cleanup-fsync", True),
        ("transaction-rmdir-before-parent-fsync", True),
    ),
)
def test_section_record_real_sigkill_recovers_every_durable_edge(
    tmp_path: Path,
    crashpoint: str,
    recovers_completed: bool,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / crashpoint)
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-{crashpoint}",
        plugin_root=plugin_root,
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = crashpoint
    crashed = run_script_raw(
        script,
        *detached_record_arguments(fixture),
        env=environment,
    )
    assert crashed.returncode == -signal.SIGKILL

    final_pinners_before_recovery = list(
        (fixture.implementation_root / "pinners").glob("section-01-foundation-*.json")
    )
    for pinner_path in final_pinners_before_recovery:
        raw = pinner_path.read_bytes()
        assert canonical_json_bytes_for_test(json.loads(raw)) == raw

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    payload = json.loads(recovered.stdout)
    state = assert_canonical_json_file(fixture.implementation_root / "zagrosi_implement_state.json")
    assert (fixture.section in state["completed_sections"]) is recovers_completed
    assert (payload["next_section"] is None) is recovers_completed
    assert not (
        fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    ).exists()
    assert set(path.name for path in fixture.implementation_root.iterdir()) == {
        "code_review",
        "evidence",
        "pinners",
        "zagrosi_implement_config.json",
        "zagrosi_implement_state.json",
        "forge-progress.json",
    }
    marker = fixture.implementation_root / "pinners" / ".record-section.lock"
    assert marker.is_file()
    assert marker.stat().st_mode & 0o777 == 0o600
    final_pinners = list(
        (fixture.implementation_root / "pinners").glob("section-01-foundation-*.json")
    )
    assert len(final_pinners) == int(recovers_completed)
    if final_pinners:
        assert final_pinners[0].stat().st_nlink == 1


@pytest.mark.parametrize("mutation", ("noncanonical", "candidate-missing-final", "unknown-section"))
def test_no_journal_published_stage_requires_canonical_known_current_state_join(
    tmp_path: Path,
    mutation: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"no-journal-stage-{mutation}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-no-journal-stage-{mutation}",
        plugin_root=plugin_root,
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "pinner-rename-dir-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    staged_path = transaction_dir / "pinner.json"
    if mutation == "noncanonical":
        replace_file(staged_path, b'{"partial":', mode=0o600)
    else:
        staged_raw = staged_path.read_bytes()
        staged = json.loads(staged_raw)
        if mutation == "unknown-section":
            staged["section"] = "section-99-unknown"
            replace_file(staged_path, canonical_json_bytes_for_test(staged), mode=0o600)
        else:
            state_path = fixture.implementation_root / "zagrosi_implement_state.json"
            state = assert_canonical_json_file(state_path)
            state["completed_sections"][fixture.section] = pinner_state_record_for_test(
                staged,
                staged_raw,
            )
            replace_file(state_path, canonical_json_bytes_for_test(state), mode=0o600)
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
    assert json.loads(result.stdout)["error_code"] in {
        "section-record-recovery-required",
        "invalid-section-record-transaction",
        "invalid-section-pinner",
    }
    assert planning_tree_snapshot(fixture.implementation_root) == before


@pytest.mark.parametrize("committed_candidate", (False, True))
def test_no_journal_exact_distinct_final_is_preserved_and_only_candidate_is_committed(
    tmp_path: Path,
    committed_candidate: bool,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"no-journal-adopted-{committed_candidate}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-no-journal-adopted-{committed_candidate}",
        plugin_root=plugin_root,
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "pinner-rename-dir-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    staged_path = transaction_dir / "pinner.json"
    staged_raw = staged_path.read_bytes()
    staged = json.loads(staged_raw)
    state_record = pinner_state_record_for_test(staged, staged_raw)
    final_path = fixture.implementation_root / state_record["pinner_path"]
    final_path.write_bytes(staged_raw)
    final_path.chmod(0o600)
    assert os.stat(staged_path).st_ino != os.stat(final_path).st_ino
    if committed_candidate:
        state_path = fixture.implementation_root / "zagrosi_implement_state.json"
        state = assert_canonical_json_file(state_path)
        state["completed_sections"][fixture.section] = state_record
        replace_file(state_path, canonical_json_bytes_for_test(state), mode=0o600)

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    state = assert_canonical_json_file(fixture.implementation_root / "zagrosi_implement_state.json")
    assert (fixture.section in state["completed_sections"]) is committed_candidate
    assert final_path.is_file()
    assert final_path.stat().st_nlink == 1
    assert not transaction_dir.exists()


def test_no_journal_base_refuses_wrong_distinct_orphan_without_mutation(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "no-journal-wrong-orphan")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-no-journal-wrong-orphan", plugin_root=plugin_root)
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "pinner-rename-dir-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    staged_path = transaction_dir / "pinner.json"
    staged_raw = staged_path.read_bytes()
    staged = json.loads(staged_raw)
    state_record = pinner_state_record_for_test(staged, staged_raw)
    final_path = fixture.implementation_root / state_record["pinner_path"]
    final_path.write_bytes(canonical_json_bytes_for_test({**staged, "notes": "wrong orphan"}))
    final_path.chmod(0o600)
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
    assert json.loads(result.stdout)["error_code"] in {
        "section-record-recovery-required",
        "invalid-detached-json",
    }
    assert planning_tree_snapshot(fixture.implementation_root) == before


@pytest.mark.parametrize("mutation", ("noncanonical", "candidate"))
def test_published_transaction_temp_requires_exact_canonical_base_projection(
    tmp_path: Path,
    mutation: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"transaction-temp-{mutation}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-transaction-temp-{mutation}",
        plugin_root=plugin_root,
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "journal-temp-rename-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction_temp = transaction_dir / "transaction.tmp"
    if mutation == "noncanonical":
        replace_file(transaction_temp, b'{"partial":', mode=0o600)
    else:
        transaction = assert_canonical_json_file(transaction_temp)
        state_path = fixture.implementation_root / "zagrosi_implement_state.json"
        state = assert_canonical_json_file(state_path)
        state["completed_sections"][transaction["section"]] = transaction["state_record"]
        replace_file(state_path, canonical_json_bytes_for_test(state), mode=0o600)
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
    assert json.loads(result.stdout)["error_code"] in {
        "section-record-recovery-required",
        "invalid-detached-json",
    }
    assert planning_tree_snapshot(fixture.implementation_root) == before


@pytest.mark.parametrize("mutation", ("candidate", "mixed-stage"))
def test_journal_write_temp_refuses_candidate_or_mixed_exact_stage(
    tmp_path: Path,
    mutation: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"journal-write-{mutation}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-journal-write-{mutation}",
        plugin_root=plugin_root,
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "journal-write-temp-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.write.tmp")
    if mutation == "candidate":
        state_path = fixture.implementation_root / "zagrosi_implement_state.json"
        state = assert_canonical_json_file(state_path)
        state["completed_sections"][transaction["section"]] = transaction["state_record"]
        replace_file(state_path, canonical_json_bytes_for_test(state), mode=0o600)
    else:
        staged_path = transaction_dir / "pinner.json"
        staged = assert_canonical_json_file(staged_path)
        replace_file(
            staged_path,
            canonical_json_bytes_for_test({**staged, "notes": "different exact stage"}),
            mode=0o600,
        )
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


@pytest.mark.parametrize("drift_kind", ("review", "evidence"))
def test_post_commit_cleanup_ignores_mutable_review_and_evidence_drift(
    tmp_path: Path,
    drift_kind: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"post-commit-{drift_kind}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / f"fixture-post-commit-{drift_kind}", plugin_root=plugin_root)
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "journal-unlink-fsync"
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
    assert transaction_dir.is_dir()
    assert not (transaction_dir / "transaction.json").exists()

    if drift_kind == "review":
        (fixture.implementation_root / "code_review" / f"{fixture.section}-review.md").write_text(
            "# Review\n\nChanged after commit.\n"
        )
    else:
        (fixture.implementation_root / "evidence" / "record-gate.json").write_bytes(
            b'{"schema":"test-record-gate-v1","verdict":"CHANGED"}\n'
        )

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    assert json.loads(recovered.stdout)["completed_sections"] == [fixture.section]
    assert not transaction_dir.exists()
