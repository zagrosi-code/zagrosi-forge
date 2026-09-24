from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from detached_test_support import (
    file_sha256,
    implementation_source_args,
    planning_tree_snapshot,
    write_test_admission_pinner,
)
from forge_test_helpers import (
    load_zagrosi_module,
    run_cmd,
    run_raw,
    write_lean_plan_fixture,
    write_non_topological_section_fixture,
    write_quality_plan_fixture,
    write_required_plan_artifacts,
    write_single_section_fixture,
)


def test_implement_setup_and_record(tmp_path: Path) -> None:
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-foundation\n"
        "END_MANIFEST -->\n"
    )
    (sections / "section-01-foundation.md").write_text(
        "# Section\n\n"
        "REQ-001: Goal and dependencies: none. Tests first in `tests/test_zagrosi_skills.py`; "
        "implementation modifies `scripts/zagrosi_skills.py`. Acceptance uses verification; "
        "risks use rollback.\n"
    )
    write_required_plan_artifacts(tmp_path)

    setup = run_cmd(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(tmp_path),
    )
    assert setup["success"] is True
    assert setup["next_section"] == "section-01-foundation"
    assert setup["preflight"]["phase"] == "implement"
    assert (tmp_path / "implementation" / "zagrosi_implement_config.json").exists()

    record = run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-01-foundation",
        "--commit",
        "abc123",
        "--review-status",
        "pass",
        "--verification",
        "pytest -q",
    )
    assert record["success"] is True
    assert "postflight" not in record
    state = json.loads((tmp_path / "implementation" / "zagrosi_implement_state.json").read_text())
    assert state["completed_sections"]["section-01-foundation"]["commit"] == "abc123"

    impl_gate = run_cmd("lint-implementation-state", "--sections-dir", str(sections))
    assert impl_gate["success"] is True
    assert "section-01-foundation" in impl_gate["completed_sections"]


def test_implement_record_section_refreshes_traceability_matrix(tmp_path: Path) -> None:
    sections = tmp_path / "sections"
    sections.mkdir()
    (tmp_path / "zagrosi_plan_config.json").write_text('{"depth_mode":"standard"}\n')
    (tmp_path / "codex-spec.md").write_text("# Spec\n\nREQ-001: Implement status.\nREQ-002: Document status.\n")
    (tmp_path / "codex-plan.md").write_text("# Plan\n\nREQ-001 in `scripts/tool.py`.\nREQ-002 in `README.md`.\n")
    (tmp_path / "codex-plan-tdd.md").write_text(
        "# TDD\n\nREQ-001: `test_status_flow`.\nREQ-002: `test_readme_status_docs`.\nRun `pytest -q`.\n"
    )
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-status\n"
        "section-02-docs\n"
        "END_MANIFEST -->\n"
    )
    (sections / "section-01-status.md").write_text("# Section\n\nREQ-001 with `test_status_flow`.\n")
    (sections / "section-02-docs.md").write_text("# Section\n\nREQ-002 with `test_readme_status_docs`.\n")
    write_required_plan_artifacts(tmp_path)
    (tmp_path / "traceability.md").write_text(
        "# Traceability Matrix\n\n"
        "| Requirement | Plan Coverage | Section Coverage | Test Coverage | Status |\n"
        "|-------------|---------------|------------------|---------------|--------|\n"
        "| REQ-001 | `codex-plan.md` | `section-01-status.md` | `test_status_flow` | Planned |\n"
        "| REQ-002 | `codex-plan.md` | `section-02-docs.md` | `test_readme_status_docs` | Planned |\n"
    )

    recorded = run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-01-status",
        "--review-status",
        "pass",
        "--verification",
        "pytest -q",
        "--commit",
        "abc123",
        "--flight",
        "off",
    )

    matrix = (tmp_path / "traceability.md").read_text()
    assert recorded["traceability_matrix"] == str(tmp_path / "traceability.md")
    assert "| Requirement | Plan Coverage | Section Coverage | Test Coverage | Implementation Evidence | Status |" in matrix
    assert (
        "| REQ-001 | `codex-plan.md` | `section-01-status.md` | `test_status_flow` | commit `abc123`; verification: `pytest -q` | Implemented |"
        in matrix
    )
    assert (
        "| REQ-002 | `codex-plan.md` | `section-02-docs.md` | `test_readme_status_docs` | - | Planned |"
        in matrix
    )


def test_implement_record_section_stores_evidence_and_refreshes_traceability(tmp_path: Path) -> None:
    sections = write_single_section_fixture(tmp_path)
    (tmp_path / "zagrosi_plan_config.json").write_text('{"depth_mode":"standard"}\n')
    (tmp_path / "implementation" / "code_review" / "section-01-foundation-decisions.md").write_text(
        "# Decisions\n\nAccepted implementation evidence and traceability updates.\n"
    )

    recorded = run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-01-foundation",
        "--commit",
        "abc123",
        "--file",
        "scripts/zagrosi_skills.py",
        "--file",
        "scripts/zagrosi_skills.py",
        "--test-file",
        "tests/test_zagrosi_skills.py",
        "--review-artifact",
        "implementation/code_review/section-01-foundation-review.md",
        "--review-artifact",
        "implementation/code_review/section-01-foundation-decisions.md",
        "--verification",
        "uv run pytest",
        "--flight",
        "off",
    )

    assert recorded["success"] is True
    record = recorded["record"]
    assert record["files_changed"] == ["scripts/zagrosi_skills.py"]
    assert record["test_files"] == ["tests/test_zagrosi_skills.py"]
    assert record["review_artifacts"] == [
        "implementation/code_review/section-01-foundation-review.md",
        "implementation/code_review/section-01-foundation-decisions.md",
    ]
    assert record["verification"] == ["uv run pytest"]
    assert record["commit_status"] == "recorded"

    state = json.loads((tmp_path / "implementation" / "zagrosi_implement_state.json").read_text())
    persisted = state["completed_sections"]["section-01-foundation"]
    assert persisted["files_changed"] == ["scripts/zagrosi_skills.py"]
    assert persisted["test_files"] == ["tests/test_zagrosi_skills.py"]

    matrix = (tmp_path / "traceability.md").read_text()
    assert "Implementation Evidence" in matrix
    assert "abc123" in matrix
    assert "scripts/zagrosi_skills.py" in matrix
    assert "tests/test_zagrosi_skills.py" in matrix


def test_traceability_handles_legacy_implementation_records(tmp_path: Path) -> None:
    write_single_section_fixture(tmp_path)
    state_path = tmp_path / "implementation" / "zagrosi_implement_state.json"
    state_path.write_text(
        json.dumps(
            {
                "completed_sections": {
                    "section-01-foundation": {
                        "completed_at": "2026-05-18T00:00:00+00:00",
                        "commit": "abc123",
                        "notes": "legacy record",
                    }
                }
            }
        )
    )

    trace = run_cmd("traceability", "--planning-dir", str(tmp_path), "--strict")
    assert trace["success"] is True
    assert trace["implementation_evidence"]["section-01-foundation"]["commit"] == "abc123"


def test_implementation_state_reuses_substantive_legacy_review(tmp_path: Path) -> None:
    sections = write_single_section_fixture(tmp_path)
    (tmp_path / "zagrosi_plan_config.json").write_text('{"depth_mode":"standard"}\n')
    run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-01-foundation",
        "--commit",
        "abc123",
        "--file",
        "scripts/zagrosi_skills.py",
        "--test-file",
        "tests/test_zagrosi_skills.py",
        "--review-artifact",
        "implementation/code_review/section-01-foundation-review.md",
        "--verification",
        "uv run pytest",
        "--flight",
        "off",
    )
    passed = run_cmd("lint-implementation-state", "--sections-dir", str(sections), "--strict")
    assert passed["success"] is True
    (tmp_path / "implementation" / "code_review" / "section-01-foundation-review.md").write_text("")
    missing = run_raw("lint-implementation-state", "--sections-dir", str(sections), "--strict")
    assert missing.returncode != 0
    assert "missing-review-status" in {item["code"] for item in json.loads(missing.stdout)["findings"]}


def test_implement_setup_blocks_missing_core_plan_artifacts_even_with_flight_off(tmp_path: Path) -> None:
    sections = tmp_path / "sections"
    sections.mkdir()
    (tmp_path / "spec.md").write_text("# Fix Forge\n\nREQ-001: Fix workflow shortcuts.\n")
    (tmp_path / "decisions.md").write_text(
        "# Decision Log\n\n"
        "| ID | Date | Decision | Alternatives | Rationale | Impact |\n"
        "|----|------|----------|--------------|-----------|--------|\n"
        "| DEC-001 | TBD | TBD | TBD | TBD | TBD |\n"
    )
    (tmp_path / "risk-register.md").write_text(
        "# Risk Register\n\n"
        "| ID | Risk | Severity | Likelihood | Mitigation | Section | Verification |\n"
        "|----|------|----------|------------|------------|---------|--------------|\n"
        "| RISK-001 | TBD | TBD | TBD | TBD | TBD | TBD |\n"
    )
    (tmp_path / "traceability.md").write_text(
        "# Traceability Matrix\n\n"
        "| Requirement | Plan Coverage | Section Coverage | Test Coverage | Status |\n"
        "|-------------|---------------|------------------|---------------|--------|\n"
        "| REQ-001 | TBD | TBD | TBD | TBD |\n"
    )
    (tmp_path / "quality-gates.md").write_text("# Quality Gates\n\n- `lint-plan`\n")
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-shortcut\n"
        "END_MANIFEST -->\n"
    )
    (sections / "section-01-shortcut.md").write_text("# Section\n\nTests first in `tests/test_zagrosi_skills.py`.\n")

    result = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(tmp_path),
        "--flight",
        "off",
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert payload["gate"] == "plan-artifacts"
    codes = {item["code"] for item in payload["findings"]}
    assert {"missing-plan", "missing-review"} <= codes
    assert "missing-research" not in codes
    assert "placeholder-decisions" not in codes


def test_legacy_implementation_uses_dependency_ready_order_and_rejects_early_record(tmp_path: Path) -> None:
    sections = write_non_topological_section_fixture(tmp_path)
    setup = run_cmd(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(tmp_path),
        "--flight",
        "off",
    )
    assert setup["next_section"] == "section-03-storage"
    assert setup["ready_sections"] == ["section-03-storage"]

    blocked = run_raw(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-01-foundation",
        "--commit",
        "abc123",
        "--flight",
        "off",
    )
    assert blocked.returncode != 0
    assert json.loads(blocked.stdout)["incomplete_predecessors"] == ["section-03-storage"]

    unknown = run_raw(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-99-missing",
        "--commit",
        "abc123",
        "--flight",
        "off",
    )
    assert unknown.returncode != 0
    assert json.loads(unknown.stdout)["error_code"] == "unknown-section"


def test_implement_progress_preserves_overlapping_writes(tmp_path: Path, monkeypatch) -> None:
    module = load_zagrosi_module()
    planning = tmp_path / "planning"
    planning.mkdir()
    start = threading.Barrier(2)
    original_write_json = module.storage.write_json

    def slow_progress_write(path: Path, payload: dict) -> None:
        if path.name == "forge-progress.json":
            time.sleep(0.05)
        original_write_json(path, payload)

    monkeypatch.setattr(module.storage, "write_json", slow_progress_write)

    def record(stage: str) -> int:
        start.wait(timeout=2)
        return module.scheduling.implement_progress(
            SimpleNamespace(
                planning_dir=str(planning),
                section="section-01-progress",
                stage=stage,
                command=None,
                result=f"{stage} recorded",
                notes=None,
            )
        )

    threads = [threading.Thread(target=record, args=(stage,)) for stage in ("red", "green")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    assert not any(thread.is_alive() for thread in threads)

    state = json.loads((planning / "implementation" / "forge-progress.json").read_text())
    assert sorted(event["stage"] for event in state["events"]) == ["green", "red"]


def test_implement_postflight_defers_state_lint_until_sections_recorded(tmp_path: Path) -> None:
    planning = write_quality_plan_fixture(tmp_path / "planning")

    postflight = run_cmd(
        "postflight",
        "--phase",
        "implement",
        "--planning-dir",
        str(planning),
        "--sections-dir",
        str(planning / "sections"),
        "--target-dir",
        str(tmp_path),
        "--depth",
        "fast",
        "--flight",
        "strict",
    )

    assert postflight["success"] is True
    assert postflight["sections_recorded_complete"] is False
    assert postflight["remaining_sections"] == ["section-01-auth"]
    assert "lint-implementation-state" not in postflight["blocking_gates"]
    progress_gate = next(gate for gate in postflight["gates"] if gate["name"] == "implementation-progress")
    assert progress_gate["success"] is True
    assert progress_gate["payload"]["deferred_gate"] == "lint-implementation-state"


def test_lean_implementation_uses_machine_record_and_one_final_gate(tmp_path: Path) -> None:
    planning = write_lean_plan_fixture(tmp_path)
    sections = planning / "sections"

    record = run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-01-lean-default",
        "--review-status",
        "pass",
        "--verification",
        "pytest tests/test_zagrosi_skills.py -q",
        "--flight",
        "off",
    )

    assert record["record"]["review_status"] == "pass"
    assert record["traceability_matrix"] is None
    assert record["next_section"] is None
    assert record["ready_sections"] == []
    assert record["remaining_sections"] == []
    assert record["blocked_sections"] == {}
    assert "postflight" not in record
    assert not (planning / "implementation" / "code_review").exists()

    state = run_cmd("lint-implementation-state", "--sections-dir", str(sections), "--strict")
    assert state["success"] is True

    postflight = run_cmd(
        "postflight",
        "--phase",
        "implement",
        "--planning-dir",
        str(planning),
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(tmp_path),
        "--depth",
        "lean",
        "--flight",
        "strict",
    )
    gate_names = [gate["name"] for gate in postflight["gates"]]
    assert postflight["success"] is True
    assert "lint-implementation-state" in gate_names
    assert "forge-score" not in gate_names


def test_lean_implement_preflight_uses_one_in_process_analysis(tmp_path: Path) -> None:
    planning = write_lean_plan_fixture(tmp_path / "planning")

    result = run_raw(
        "preflight",
        "--phase",
        "implement",
        "--sections-dir",
        str(planning / "sections"),
        "--target-dir",
        str(tmp_path),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert len(result.stdout.encode()) <= 1_500
    preflight = json.loads(result.stdout)
    assert preflight["success"] is True
    names = {gate["name"] for gate in preflight["gates"]}
    assert names == {
        "sections-directory",
        "target-directory",
        "lint-plan-artifacts",
        "lint-sections",
        "traceability",
        "lint-implementation-readiness",
    }
    assert {"doctor", "next-section", "suggest-section-splits", "status"}.isdisjoint(names)


def test_implement_preflight_preserves_readiness_findings_and_metrics(tmp_path: Path) -> None:
    planning = write_lean_plan_fixture(tmp_path / "planning")
    section = planning / "sections" / "section-01-lean-default.md"
    section.write_text(
        "# section-01-lean-default\n\n"
        "Goal: implement REQ-001. Dependencies: none.\n\n"
        "## Exact Path Ownership\n\n- Runtime module and focused tests.\n\n"
        "## Tests First\n\nAdd `test_missing_ownership`; expected failure comes first.\n\n"
        "## Implementation\n\nModify the runtime. Contract: explicit deep remains explicit.\n\n"
        "## Risks And Rollback\n\nRisk: missing coverage. Rollback: revert the change.\n\n"
        "## Acceptance And Verification\n\nREQ-001 is done when `pytest -q` passes.\n"
    )

    readiness_result = run_raw(
        "lint-implementation-readiness",
        "--planning-dir",
        str(planning),
        "--strict",
    )
    preflight_result = run_raw(
        "preflight",
        "--phase",
        "implement",
        "--sections-dir",
        str(planning / "sections"),
        "--target-dir",
        str(tmp_path),
        "--flight",
        "strict",
    )

    assert readiness_result.returncode != 0
    assert preflight_result.returncode != 0
    readiness = json.loads(readiness_result.stdout)
    preflight = json.loads(preflight_result.stdout)
    gate = next(gate for gate in preflight["gates"] if gate["name"] == "lint-implementation-readiness")
    assert gate["success"] is False
    assert [preflight["diagnostics"][index] for index in gate["payload"]["finding_refs"]] == readiness["findings"]
    full_report = json.loads(Path(preflight["full_report"]).read_text())
    assert next(gate for gate in full_report["gates"] if gate["name"] == "lint-implementation-readiness")["payload"] == readiness
    assert {item["code"] for item in readiness["findings"]} == {"no-file-ownership"}
    assert readiness["sections"][0]["section"] == "section-01-lean-default"
    assert readiness["sections"][0]["file_count"] == 0
    assert readiness["sections"][0]["files"] == []


def test_mutable_implement_setup_propagates_preflight_failure(tmp_path: Path) -> None:
    planning = write_lean_plan_fixture(tmp_path / "planning")
    sections = planning / "sections"
    (sections / "section-01-lean-default.md").write_text(
        "# section-01-lean-default\n\nREQ-001 changes `scripts/zagrosi_skills.py`.\n"
    )

    result = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(tmp_path),
        "--flight",
        "strict",
    )

    assert result.returncode == 1, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert payload["preflight"]["success"] is False
    assert "lint-sections" in payload["preflight"]["blocking_gates"]
    assert "lint-implementation-readiness" in payload["preflight"]["blocking_gates"]


def test_detached_implement_setup_propagates_preflight_failure_without_planning_writes(
    tmp_path: Path,
) -> None:
    planning = write_lean_plan_fixture(tmp_path / "planning")
    sections = planning / "sections"
    (sections / "section-01-lean-default.md").write_text(
        "# section-01-lean-default\n\nREQ-001 changes `scripts/zagrosi_skills.py`.\n"
    )
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    expected_planning = planning_tree_snapshot(planning)

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
        "strict",
    )

    assert result.returncode == 1, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert payload["mode"] == "detached-frozen"
    assert payload["preflight"]["success"] is False
    assert "lint-sections" in payload["preflight"]["blocking_gates"]
    assert "lint-implementation-readiness" in payload["preflight"]["blocking_gates"]
    assert planning_tree_snapshot(planning) == expected_planning


def test_lean_record_rejects_incomplete_review_or_verification(tmp_path: Path) -> None:
    planning = write_lean_plan_fixture(tmp_path)
    sections = planning / "sections"

    blocked = run_raw(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-01-lean-default",
        "--review-status",
        "blocked",
        "--verification",
        "pytest -q",
        "--flight",
        "off",
    )
    unverified = run_raw(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-01-lean-default",
        "--review-status",
        "pass",
        "--flight",
        "off",
    )

    assert blocked.returncode != 0
    assert unverified.returncode != 0
    assert json.loads(blocked.stdout)["error_code"] == "incomplete-lean-record"
    assert not (planning / "implementation" / "zagrosi_implement_state.json").exists()

    state_dir = planning / "implementation"
    state_dir.mkdir(exist_ok=True)
    (state_dir / "zagrosi_implement_state.json").write_text(
        json.dumps(
            {
                "completed_sections": {
                    "section-01-lean-default": {
                        "completed_at": "2026-09-01T00:00:00Z",
                        "review_status": "blocked",
                        "verification": [],
                    }
                }
            }
        )
    )
    postflight = run_raw(
        "postflight",
        "--phase",
        "implement",
        "--planning-dir",
        str(planning),
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(tmp_path),
        "--depth",
        "lean",
    )

    assert postflight.returncode != 0
    payload = json.loads(postflight.stdout)
    assert "lint-implementation-state" in payload["blocking_gates"]
