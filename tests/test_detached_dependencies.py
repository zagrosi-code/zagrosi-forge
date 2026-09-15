from __future__ import annotations

import hashlib
import json
from pathlib import Path

from detached_test_support import (
    canonical_json_bytes_for_test,
    file_sha256,
    implementation_source_args,
    replace_file,
    write_test_admission_pinner,
)
from forge_test_helpers import (
    load_zagrosi_module,
    run_cmd,
    run_raw,
    write_non_topological_section_fixture,
)


def test_detached_rerecord_rejects_completed_transitive_dependants_and_current_pointer_drift(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json", planning_dir=planning)
    run_cmd(
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

    def record(section: str, commit: str) -> dict:
        review_dir = implementation_root / "code_review"
        (review_dir / f"{section}-review.md").write_text("# Review\n\nNo blockers.\n")
        (review_dir / f"{section}-decisions.md").write_text("# Decisions\n\nAccepted.\n")
        return run_cmd(
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
        )

    record("section-03-storage", "storage-1")
    record("section-01-foundation", "foundation-1")
    record("section-02-api", "api-1")
    state_path = implementation_root / "zagrosi_implement_state.json"
    state_before = state_path.read_bytes()
    pinners_before = {
        path.name: path.read_bytes() for path in sorted((implementation_root / "pinners").glob("*.json"))
    }

    rerecord = run_raw(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-03-storage",
        "--commit",
        "storage-2",
        "--notes",
        "must not replace a closed predecessor",
        "--review-artifact",
        "code_review/section-03-storage-review.md",
        "--review-artifact",
        "code_review/section-03-storage-decisions.md",
        "--verification",
        "uv run pytest tests/test_storage.py",
        "--flight",
        "off",
    )
    assert rerecord.returncode != 0
    rerecord_payload = json.loads(rerecord.stdout)
    assert rerecord_payload["error_code"] == "completed-dependent-pinner-conflict"
    assert rerecord_payload["completed_dependants"] == ["section-01-foundation", "section-02-api"]
    assert state_path.read_bytes() == state_before
    assert {
        path.name: path.read_bytes() for path in sorted((implementation_root / "pinners").glob("*.json"))
    } == pinners_before

    state = json.loads(state_before)
    storage_record = state["completed_sections"]["section-03-storage"]
    old_pinner_path = implementation_root / storage_record["pinner_path"]
    replacement_pinner = json.loads(old_pinner_path.read_bytes())
    replacement_pinner["notes"] = "authenticated replacement"
    replacement_raw = (json.dumps(replacement_pinner, sort_keys=True, separators=(",", ":")) + "\n").encode()
    replacement_sha256 = "sha256:" + hashlib.sha256(replacement_raw).hexdigest()
    replacement_relative = f"pinners/section-03-storage-{replacement_sha256.removeprefix('sha256:')}.json"
    replacement_path = implementation_root / replacement_relative
    replacement_path.write_bytes(replacement_raw)
    replacement_path.chmod(0o600)
    storage_record["notes"] = replacement_pinner["notes"]
    storage_record["pinner_path"] = replacement_relative
    storage_record["pinner_file_sha256"] = replacement_sha256
    replace_file(
        state_path,
        (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        mode=0o600,
    )

    stale_dependent = run_raw(
        "next-section",
        "--planning-dir",
        str(planning),
        "--implementation-root",
        str(implementation_root),
    )
    assert stale_dependent.returncode != 0
    stale_payload = json.loads(stale_dependent.stdout)
    assert stale_payload["error_code"] == "predecessor-pinner-current-state-mismatch"
    assert stale_payload["section"] == "section-01-foundation"
    assert stale_payload["predecessor_section"] == "section-03-storage"


def test_detached_record_rolls_back_new_pinner_on_post_pinner_predecessor_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    run_cmd(
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
    review_dir = implementation_root / "code_review"
    for section in ("section-03-storage", "section-01-foundation"):
        (review_dir / f"{section}-review.md").write_text("# Review\n\nNo blockers.\n")
        (review_dir / f"{section}-decisions.md").write_text("# Decisions\n\nAccepted.\n")
    run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-03-storage",
        "--commit",
        "storage-parent",
        "--review-artifact",
        "code_review/section-03-storage-review.md",
        "--review-artifact",
        "code_review/section-03-storage-decisions.md",
        "--verification",
        "uv run pytest tests/test_storage.py",
        "--flight",
        "off",
    )
    state_path = implementation_root / "zagrosi_implement_state.json"
    initial_state = state_path.read_bytes()
    state = json.loads(initial_state)
    parent_path = implementation_root / state["completed_sections"]["section-03-storage"]["pinner_path"]
    original_parent = parent_path.read_bytes()
    parent_mutant = json.loads(original_parent)
    parent_mutant["notes"] = "post-child predecessor replacement"
    parent_mutant_raw = canonical_json_bytes_for_test(parent_mutant)
    initial_pinner_names = {path.name for path in (implementation_root / "pinners").glob("*.json")}

    module = load_zagrosi_module()
    original_install = module.transaction_io.install_staged_section_pinner
    mutated = False

    def replace_parent_after_child_pinner(root_fd, transaction_fd, pinner_path, pinner_raw):
        nonlocal mutated
        result = original_install(root_fd, transaction_fd, pinner_path, pinner_raw)
        if not mutated and pinner_path.startswith("pinners/section-01-foundation-"):
            mutated = True
            replace_file(parent_path, parent_mutant_raw, mode=0o600)
        return result

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module.transaction_io, "install_staged_section_pinner", replace_parent_after_child_pinner)
    monkeypatch.setattr(
        module.output,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.cli.build_parser().parse_args(
        [
            "implement-record-section",
            "--sections-dir",
            str(sections),
            "--implementation-root",
            str(implementation_root),
            "--section",
            "section-01-foundation",
            "--commit",
            "foundation-child",
            "--review-artifact",
            "code_review/section-01-foundation-review.md",
            "--review-artifact",
            "code_review/section-01-foundation-decisions.md",
            "--verification",
            "uv run pytest tests/test_foundation.py",
            "--flight",
            "off",
        ]
    )

    assert parsed.func(parsed) == 1
    assert mutated is True
    assert captured[-1][0]["error_code"] == "section-record-recovery-required"
    assert state_path.read_bytes() == initial_state
    assert {path.name for path in (implementation_root / "pinners").glob("*.json")} == initial_pinner_names


def test_detached_record_rejects_unknown_section_and_uncompleted_predecessors(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    run_cmd(
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

    blocked = run_raw(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-01-foundation",
        "--commit",
        "abc123",
        "--flight",
        "off",
    )
    assert blocked.returncode != 0
    blocked_payload = json.loads(blocked.stdout)
    assert blocked_payload["error_code"] == "incomplete-predecessors"
    assert blocked_payload["incomplete_predecessors"] == ["section-03-storage"]

    unknown = run_raw(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-99-missing",
        "--commit",
        "abc123",
        "--flight",
        "off",
    )
    assert unknown.returncode != 0
    unknown_payload = json.loads(unknown.stdout)
    assert unknown_payload["error_code"] == "unknown-section"
    assert unknown_payload["section"] == "section-99-missing"
