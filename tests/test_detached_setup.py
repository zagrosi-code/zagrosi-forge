from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from detached_test_support import (
    admission_pinner_payload,
    assert_canonical_json_file,
    copy_implementation_plugin,
    file_sha256,
    implementation_source_args,
    make_detached_record_fixture,
    planning_tree_snapshot,
    write_test_admission_pinner,
)
from forge_test_helpers import (
    IMPLEMENTATION_SOURCE_RELATIVE_PATHS,
    load_zagrosi_module,
    run_raw,
    run_script_raw,
    write_single_section_fixture,
)


def test_detached_setup_requires_exact_final_pinner_semantics_and_trusted_hash(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    valid = admission_pinner_payload(planning)

    def copied() -> dict:
        return json.loads(json.dumps(valid))

    fake_schema = copied()
    fake_schema["schema"] = "attacker-pinner-v1"
    fake_fields = copied()
    fake_fields["authority"] = "PASS"
    fake_pass = copied()
    fake_pass["verdict"] = "FAIL"
    mismatched_end = copied()
    mismatched_end["end"]["r_sha256"] = "sha256:" + "44" * 32
    wrong_a = copied()
    wrong_a["start"]["a_sha256"] = wrong_a["end"]["a_sha256"] = "sha256:" + "55" * 32
    wrong_d = copied()
    wrong_d_value = "sha256:" + "66" * 32
    for endpoint in (wrong_d["start"], wrong_d["end"]):
        endpoint["d_sha256"] = wrong_d_value
        digest = hashlib.sha256(b"dec075-a-v1\0")
        for field in ("r_sha256", "p_sha256", "d_sha256"):
            digest.update(bytes.fromhex(endpoint[field].removeprefix("sha256:")))
        endpoint["a_sha256"] = "sha256:" + digest.hexdigest()
    wrong_o = copied()
    wrong_o["o_sha256"] = "not-a-digest"

    cases = {
        "fake-schema": (fake_schema, None, "invalid-admission-pinner"),
        "fake-fields": (fake_fields, None, "invalid-admission-pinner"),
        "fake-pass": (fake_pass, None, "invalid-admission-pinner"),
        "mismatched-end": (mismatched_end, None, "invalid-admission-pinner"),
        "wrong-a": (wrong_a, None, "invalid-admission-pinner"),
        "wrong-d": (wrong_d, None, "invalid-admission-pinner"),
        "wrong-o": (wrong_o, None, "invalid-admission-pinner"),
        "wrong-trust-anchor": (copied(), "sha256:" + "0" * 64, "admission-pinner-drift"),
    }
    expected_planning = planning_tree_snapshot(planning)
    for label, (payload, expected_hash, error_code) in cases.items():
        pinner = write_test_admission_pinner(
            tmp_path / f"{label}-pinner.json",
            planning_dir=planning,
            payload=payload,
        )
        implementation_root = tmp_path / f"{label}-implementation"
        result = run_raw(
            "implement-setup",
            "--sections-dir",
            str(sections),
            "--target-dir",
            str(target),
            "--implementation-root",
            str(implementation_root),
            "--admission-pinner",
            str(pinner),
            "--expected-admission-pinner-sha256",
            expected_hash or file_sha256(pinner),
            *implementation_source_args(),
            "--flight",
            "off",
        )
        assert result.returncode != 0, label
        assert json.loads(result.stdout)["error_code"] == error_code
        assert not implementation_root.exists(), label
        assert planning_tree_snapshot(planning) == expected_planning


@pytest.mark.parametrize(
    "crash_point",
    (
        "slot_write:zagrosi_implement_config.json",
        "slot_write:zagrosi_implement_state.json",
        "slot_write:forge-progress.json",
        "slot_fsync:zagrosi_implement_config.json",
        "slot_fsync:zagrosi_implement_state.json",
        "slot_fsync:forge-progress.json",
        "final:zagrosi_implement_config.json",
        "final:zagrosi_implement_state.json",
        "final:forge-progress.json",
    ),
)
def test_detached_setup_recovers_only_its_exact_authenticated_pending_prefix(
    tmp_path: Path,
    monkeypatch,
    crash_point: str,
) -> None:
    phase, crash_after_relative = crash_point.split(":", 1)
    plugin_root = copy_implementation_plugin(tmp_path / f"{phase}-{Path(crash_after_relative).stem}")
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    module = load_zagrosi_module(plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"])
    parsed = module.cli.build_parser().parse_args(
        [
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
        ]
    )
    original_write = module.secure_io.write_canonical_json_at
    original_slot = module.detached_state.ensure_detached_root_file_slot
    original_write_all = module.secure_io._write_all
    crashed = False

    def crash_after_exact_write(root_fd, relative, payload, **kwargs):
        nonlocal crashed
        result = original_write(root_fd, relative, payload, **kwargs)
        if (
            phase == "final"
            and relative == crash_after_relative
            and payload.get("schema") != module.detached_contract.DETACHED_SETUP_PREFIX_SCHEMA
        ):
            crashed = True
            raise OSError("injected closed setup crash")
        return result

    def crash_after_exact_slot(root_fd, relative, payload):
        nonlocal crashed
        result = original_slot(root_fd, relative, payload)
        if phase == "slot_fsync" and relative == crash_after_relative:
            crashed = True
            raise OSError("injected closed setup slot crash")
        return result

    def crash_after_slot_write(file_fd, raw):
        nonlocal crashed
        original_write_all(file_fd, raw)
        if phase != "slot_write" or crashed:
            return
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return
        expected_slot = {
            "zagrosi_implement_config.json": "config",
            "zagrosi_implement_state.json": "state",
            "forge-progress.json": "progress",
        }[crash_after_relative]
        if payload.get("schema") == module.detached_contract.DETACHED_SETUP_PREFIX_SCHEMA and payload.get("slot") == expected_slot:
            crashed = True
            raise OSError("injected closed setup write-before-fsync crash")

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module.secure_io, "write_canonical_json_at", crash_after_exact_write)
    monkeypatch.setattr(module.detached_state, "ensure_detached_root_file_slot", crash_after_exact_slot)
    monkeypatch.setattr(module.secure_io, "_write_all", crash_after_slot_write)
    monkeypatch.setattr(
        module.output,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    assert parsed.func(parsed) == 1
    assert crashed is True
    assert captured[-1][0]["error_code"] == "detached-io-failure"

    monkeypatch.setattr(module.secure_io, "write_canonical_json_at", original_write)
    monkeypatch.setattr(module.detached_state, "ensure_detached_root_file_slot", original_slot)
    monkeypatch.setattr(module.secure_io, "_write_all", original_write_all)
    assert parsed.func(parsed) == 0
    assert captured[-1][0]["success"] is True
    assert_canonical_json_file(implementation_root / "zagrosi_implement_config.json")
    assert_canonical_json_file(implementation_root / "zagrosi_implement_state.json")
    assert_canonical_json_file(implementation_root / "forge-progress.json")
    assert {path.name for path in implementation_root.iterdir()} == {
        "zagrosi_implement_config.json",
        "zagrosi_implement_state.json",
        "forge-progress.json",
        "code_review",
        "evidence",
        "pinners",
    }


def test_detached_setup_replay_is_exact_and_rejects_changed_authorities_without_mutation(tmp_path: Path) -> None:
    fixture = make_detached_record_fixture(tmp_path)
    before = planning_tree_snapshot(fixture.implementation_root)

    def setup_again(target: Path, source_args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return run_script_raw(
            fixture.script,
            "implement-setup",
            "--sections-dir",
            str(fixture.sections),
            "--target-dir",
            str(target),
            "--implementation-root",
            str(fixture.implementation_root),
            "--admission-pinner",
            str(fixture.admission_pinner),
            "--expected-admission-pinner-sha256",
            file_sha256(fixture.admission_pinner),
            *source_args,
            "--flight",
            "off",
        )

    replay = setup_again(fixture.target, implementation_source_args())
    assert replay.returncode == 0, replay.stderr + replay.stdout
    assert planning_tree_snapshot(fixture.implementation_root) == before

    different_target = tmp_path / "different-target"
    different_target.mkdir()
    changed_target = setup_again(different_target, implementation_source_args())
    assert changed_target.returncode != 0
    assert json.loads(changed_target.stdout)["error_code"] == "detached-config-conflict"
    assert planning_tree_snapshot(fixture.implementation_root) == before

    changed_source = setup_again(
        fixture.target,
        implementation_source_args(tool="sha256:" + "00" * 32),
    )
    assert changed_source.returncode != 0
    assert json.loads(changed_source.stdout)["error_code"] == "implement-source-drift"
    assert planning_tree_snapshot(fixture.implementation_root) == before


def test_detached_setup_rejects_arbitrary_prefix_and_unknown_top_level_before_writes(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )

    def setup(root: Path) -> subprocess.CompletedProcess[str]:
        return run_raw(
            "implement-setup",
            "--sections-dir",
            str(sections),
            "--target-dir",
            str(target),
            "--implementation-root",
            str(root),
            "--admission-pinner",
            str(admission_pinner),
            "--expected-admission-pinner-sha256",
            file_sha256(admission_pinner),
            *implementation_source_args(),
            "--flight",
            "off",
        )

    planted = tmp_path / "planted-prefix"
    planted.mkdir(mode=0o700)
    (planted / "zagrosi_implement_config.json").write_bytes(b"{}\n")
    (planted / "zagrosi_implement_config.json").chmod(0o600)
    (planted / ".forge-progress.json.tmp").write_bytes(b'{"attacker":true}\n')
    (planted / ".forge-progress.json.tmp").chmod(0o600)
    planted_before = planning_tree_snapshot(planted)
    refused_prefix = setup(planted)
    assert refused_prefix.returncode != 0
    assert json.loads(refused_prefix.stdout)["error_code"] == "detached-setup-prefix-conflict"
    assert planning_tree_snapshot(planted) == planted_before

    unknown = tmp_path / "unknown-sibling"
    unknown.mkdir(mode=0o700)
    (unknown / "caller-data.txt").write_text("must remain untouched\n")
    unknown_before = planning_tree_snapshot(unknown)
    refused_unknown = setup(unknown)
    assert refused_unknown.returncode != 0
    assert json.loads(refused_unknown.stdout)["error_code"] == "unsafe-detached-root-inventory"
    assert planning_tree_snapshot(unknown) == unknown_before
    assert not (unknown / "zagrosi_implement_state.json").exists()
    assert not (unknown / "evidence").exists()
    assert not (unknown / "pinners").exists()


@pytest.mark.parametrize("directory", ("code_review", "evidence"))
def test_detached_setup_rejects_preplanted_nested_artifacts_without_mutation(
    tmp_path: Path,
    directory: str,
) -> None:
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    implementation_root = tmp_path / "detached-implementation"
    planted_dir = implementation_root / directory
    planted_dir.mkdir(parents=True, mode=0o700)
    implementation_root.chmod(0o700)
    (planted_dir / "caller-artifact.txt").write_text("retain exactly\n")
    before = planning_tree_snapshot(implementation_root)

    result = run_raw(
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
        *implementation_source_args(),
        "--flight",
        "off",
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == "detached-setup-prefix-conflict"
    assert planning_tree_snapshot(implementation_root) == before
    assert not (implementation_root / "pinners").exists()


@pytest.mark.parametrize("invalid_shape", ("missing-marker", "config-plus-progress"))
def test_detached_setup_rejects_impossible_authenticated_prefix_shapes_without_mutation(
    tmp_path: Path,
    monkeypatch,
    invalid_shape: str,
) -> None:
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    module = load_zagrosi_module()
    parsed = module.cli.build_parser().parse_args(
        [
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
            *implementation_source_args(),
            "--flight",
            "off",
        ]
    )
    original_slot = module.detached_state.ensure_detached_root_file_slot

    def leave_invalid_prefix(root_fd, relative, payload):
        if invalid_shape == "config-plus-progress" and relative == "zagrosi_implement_state.json":
            return False, payload, module.handoff_wire.canonical_json_bytes(payload)
        result = original_slot(root_fd, relative, payload)
        if (
            invalid_shape == "missing-marker"
            and relative == "zagrosi_implement_config.json"
        ) or (
            invalid_shape == "config-plus-progress"
            and relative == "forge-progress.json"
        ):
            raise OSError("injected setup prefix stop")
        return result

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module.detached_state, "ensure_detached_root_file_slot", leave_invalid_prefix)
    monkeypatch.setattr(
        module.output,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    assert parsed.func(parsed) == 1
    monkeypatch.setattr(module.detached_state, "ensure_detached_root_file_slot", original_slot)
    if invalid_shape == "missing-marker":
        (implementation_root / "pinners" / ".record-section.lock").unlink()
    before = planning_tree_snapshot(implementation_root)

    assert parsed.func(parsed) == 1
    assert captured[-1][0]["error_code"] in {
        "unsafe-section-record-lock",
        "detached-setup-prefix-conflict",
    }
    assert planning_tree_snapshot(implementation_root) == before


@pytest.mark.parametrize("relationship", ("root_inside_target", "target_inside_root"))
def test_detached_setup_rejects_target_root_ancestry_before_any_create(
    tmp_path: Path,
    relationship: str,
) -> None:
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    if relationship == "root_inside_target":
        target = tmp_path / "target"
        target.mkdir()
        implementation_root = target / "detached-state"
    else:
        implementation_root = tmp_path / "detached-state"
        implementation_root.mkdir(mode=0o700)
        target = implementation_root / "worktree"
        target.mkdir()
    before = planning_tree_snapshot(target if relationship == "root_inside_target" else implementation_root)

    result = run_raw(
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
        *implementation_source_args(),
        "--flight",
        "off",
    )

    assert result.returncode != 0
    assert json.loads(result.stdout)["error_code"] == "detached-root-target-overlap"
    assert planning_tree_snapshot(target if relationship == "root_inside_target" else implementation_root) == before
    if relationship == "root_inside_target":
        assert not implementation_root.exists()


@pytest.mark.parametrize(
    "relationship",
    ("same_root", "target_inside_planning", "planning_inside_target"),
)
def test_detached_setup_rejects_planning_target_ancestry_before_u_creation(
    tmp_path: Path,
    relationship: str,
) -> None:
    if relationship in {"same_root", "target_inside_planning"}:
        planning = tmp_path / "planning"
        sections = write_single_section_fixture(planning)
        if relationship == "same_root":
            target = planning
        else:
            target = planning / "target"
            target.mkdir()
        observed_root = planning
    else:
        target = tmp_path / "target"
        target.mkdir()
        planning = target / "planning"
        sections = write_single_section_fixture(planning)
        observed_root = target
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    before = planning_tree_snapshot(observed_root)

    result = run_raw(
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
        *implementation_source_args(),
        "--flight",
        "off",
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == "planning-target-overlap"
    assert not implementation_root.exists()
    assert planning_tree_snapshot(observed_root) == before
