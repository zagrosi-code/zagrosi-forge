from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zagrosi_skills.py"


def run_cmd(*args: str, cwd: Path | None = None) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(result.stdout)


def run_raw(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or ROOT,
        text=True,
        capture_output=True,
    )


def run_text(*args: str, cwd: Path | None = None) -> str:
    result = run_raw(*args, cwd=cwd)
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout


def test_project_setup_and_create_dirs(tmp_path: Path) -> None:
    req = tmp_path / "requirements.md"
    req.write_text("# Build a SaaS app\n\nAuth, billing, dashboard.\n")

    setup = run_cmd("project-setup", "--file", str(req))
    assert setup["success"] is True
    assert setup["resume_step"] == 1
    assert setup["preflight"]["phase"] == "project"
    assert setup["preflight"]["stage"] == "preflight"

    (tmp_path / "project-manifest.md").write_text(
        "<!-- SPLIT_MANIFEST\n"
        "01-auth\n"
        "02-billing\n"
        "END_MANIFEST -->\n\n"
        "# Project Manifest\n"
    )
    created = run_cmd("project-create-dirs", "--planning-dir", str(tmp_path))
    assert created["splits"] == ["01-auth", "02-billing"]
    assert created["postflight"]["phase"] == "project"
    assert (tmp_path / "01-auth").is_dir()
    assert (tmp_path / "02-billing").is_dir()
    assert str(tmp_path / "01-auth" / "spec.md") in created["missing_specs"]


def test_plan_setup_sections_and_prompts(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Auth\n\nAdd OAuth login.\n")

    setup = run_cmd("plan-setup", "--file", str(spec), "--plugin-root", str(ROOT))
    assert setup["success"] is True
    assert setup["resume_step"] == 6
    assert setup["preflight"]["phase"] == "plan"
    assert (tmp_path / "zagrosi_plan_config.json").exists()
    assert (tmp_path / "decisions.md").exists()
    assert (tmp_path / "risk-register.md").exists()

    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-foundation\n"
        "section-02-oauth\n"
        "END_MANIFEST -->\n\n"
        "# Sections\n"
    )

    checked = run_cmd("plan-check-sections", "--planning-dir", str(tmp_path))
    assert checked["state"] == "has_index"
    assert checked["missing"] == ["section-01-foundation", "section-02-oauth"]

    prompts = run_cmd(
        "plan-generate-section-prompts",
        "--planning-dir",
        str(tmp_path),
        "--batch-size",
        "1",
    )
    assert len(prompts["prompt_files"]) == 1
    assert Path(prompts["prompt_files"][0]).exists()


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
    (sections / "section-01-foundation.md").write_text("# Section\n\nTests first.\n")

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
    )
    assert record["success"] is True
    assert record["postflight"]["phase"] == "implement"
    state = json.loads((tmp_path / "implementation" / "zagrosi_implement_state.json").read_text())
    assert state["completed_sections"]["section-01-foundation"]["commit"] == "abc123"

    impl_gate = run_cmd("lint-implementation-state", "--sections-dir", str(sections))
    assert impl_gate["success"] is True
    assert "section-01-foundation" in impl_gate["completed_sections"]


def write_quality_plan_fixture(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    spec = tmp_path / "codex-spec.md"
    spec.write_text(
        "<!-- FORGE_META\n"
        "{\n"
        '  "artifact_type": "normalized_spec",\n'
        '  "workflow": "zagrosi-plan",\n'
        '  "depth_mode": "standard",\n'
        '  "requirement_ids": ["REQ-001"]\n'
        "}\n"
        "END_FORGE_META -->\n\n"
        "# Spec\n\n"
        "## Reader Note\n"
        "This normalized spec is self-contained for a fresh implementer.\n\n"
        "## Current System Context\n"
        "REQ-001: Add an authenticated OAuth callback flow. The existing system has auth modules, session helpers, "
        "provider configuration, and pytest coverage. The implementation must preserve current password login and "
        "session behavior while introducing OAuth callback support.\n\n"
        "## Requirements\n"
        "- REQ-001: Valid OAuth callbacks create authenticated local sessions after state validation.\n"
        "- REQ-001: Invalid state, provider denial, and duplicate-account ambiguity fail without creating sessions.\n\n"
        "## Contracts And Constraints\n"
        "The contract spans `src/auth/oauth.py`, `src/auth/session.py`, `src/auth/config.py`, and "
        "`tests/auth/test_oauth.py`. Session creation remains owned by `src/auth/session.py`; OAuth handling delegates "
        "to that module. Provider secrets are never logged.\n\n"
        "## Testing And Risks\n"
        "Tests cover valid callback, invalid state, provider denial, duplicate email handling, and configuration errors. "
        "The main risks are account-link ambiguity, token leakage, and route-level duplication of session policy.\n\n"
        + "Additional context for the implementer: OAuth callback behavior must be deterministic, observable, "
        "security reviewed, and compatible with existing auth routes. " * 34
    )
    (tmp_path / "codex-interview.md").write_text(
        "user_interviewed: true\n\n"
        "# Planning Interview\n\n"
        "Q: Should OAuth callback work create sessions directly in the route handler?\n"
        "A: No. Session creation should remain in `src/auth/session.py` so existing cookie policy is preserved.\n\n"
        "Q: Which failure cases must be planned before implementation?\n"
        "A: Invalid state, provider denial, duplicate accounts, missing config, and token leakage must be covered.\n"
    )
    (tmp_path / "codex-plan.md").write_text(
        "<!-- FORGE_META\n"
        "{\n"
        '  "artifact_type": "implementation_plan",\n'
        '  "workflow": "zagrosi-plan",\n'
        '  "depth_mode": "standard",\n'
        '  "requirement_ids": ["REQ-001"]\n'
        "}\n"
        "END_FORGE_META -->\n\n"
        "# Plan\n\n"
        "## Reader Note\nThis plan is self-contained for a fresh implementer with no prior context.\n\n"
        "## Current State Evidence\nVerified existing auth ownership through current state review: `src/auth/oauth.py`, "
        "`src/auth/session.py`, `src/auth/config.py`, and `tests/auth/test_oauth.py` are the files in scope. "
        "A grep for auth callback routes should happen before implementation.\n\n"
        "## Goal and Non-Goals\nREQ-001 adds OAuth callback handling and excludes billing.\n\n"
        "## Architecture\nUse `src/auth/oauth.py`, `src/auth/session.py`, and `src/auth/config.py`.\n\n"
        "## Architecture Rationale\nThe rationale is to keep callback-specific provider behavior in OAuth code while preserving session policy in "
        "the existing session module. The rejected alternative is duplicating cookie/session creation inside the callback route.\n\n"
        "## Contracts\nThe callback contract accepts provider payload, state, and configuration, then returns a typed result shape for success, "
        "provider denial, invalid state, or ambiguous account linking.\n\n"
        "## File Tree\n```\nsrc/auth/oauth.py\nsrc/auth/session.py\nsrc/auth/config.py\ntests/auth/test_oauth.py\n```\n\n"
        "## Phase Plan\nBatch 1 writes tests and config validation. Batch 2 implements callback state validation. Batch 3 wires session creation "
        "and executes verification.\n\n"
        "## File Plan\nModify `src/auth/oauth.py` and create `tests/auth/test_oauth.py`.\n\n"
        "## Test Matrix\nUnit tests cover valid callback, invalid state, provider denial, duplicate email, config missing fields, and no token leakage. "
        "Write pytest cases first and run `uv run pytest tests/auth/test_oauth.py`.\n\n"
        "## Security and Privacy\nValidate callback state, protect tokens, and enforce auth permissions.\n\n"
        "## Risks and Edge Cases\nHandle provider denial, duplicate accounts, and invalid state failure.\n\n"
        "## Rollout\nShip behind configuration with backward compatibility and rollback by disabling provider config.\n\n"
        "## Review Integration\nReview integration confirms account-link ambiguity and token logging are the stop-line risks. Accepted review edits "
        "must keep provider secrets out of logs and require explicit user confirmation for ambiguous linking.\n\n"
        "## Acceptance\nREQ-001 is complete when tests pass and valid callbacks create sessions.\n\n"
        + "Detailed implementation context with current-state evidence, contracts, phase sequencing, test matrix, rollback, "
        "review integration, and implementation rationale. " * 80
    )
    (tmp_path / "codex-plan-tdd.md").write_text(
        "# TDD\n\n"
        "REQ-001: `tests/auth/test_oauth.py::test_valid_callback_creates_session` "
        "expects failure before implementation. Run `uv run pytest`.\n\n"
        "## Test Matrix\n"
        "- `test_valid_callback_creates_session`: expected failure until OAuth callback maps a provider identity to a local session.\n"
        "- `test_invalid_state_rejects_callback`: expected failure until state validation rejects tampered values.\n"
        "- `test_provider_denial_does_not_create_session`: expected failure until provider errors short-circuit before session creation.\n"
        "- `test_duplicate_email_requires_explicit_policy`: expected failure until ambiguous account linking stops safely.\n"
        "- `test_provider_config_missing_fields_fails_startup`: expected failure until config validation is centralized.\n\n"
        + "Fixture context: provider payloads, signed state values, invalid state values, duplicate local users, and log capture "
        "must be available to every test. " * 32
    )
    (tmp_path / "decisions.md").write_text(
        "# Decision Log\n\n"
        "| ID | Date | Decision | Alternatives | Rationale | Impact |\n"
        "|----|------|----------|--------------|-----------|--------|\n"
        "| DEC-001 | Test | Keep OAuth callback orchestration in auth service. | Put all callback logic in a route. | Service ownership keeps REQ-001 testable. | `src/auth/oauth.py` owns callback policy. |\n"
    )
    (tmp_path / "risk-register.md").write_text(
        "# Risk Register\n\n"
        "| ID | Risk | Severity | Likelihood | Mitigation | Section | Verification |\n"
        "|----|------|----------|------------|------------|---------|--------------|\n"
        "| RISK-001 | Invalid state creates a session. | High | Medium | Validate state before provider work. | section-01-auth | `test_invalid_state_rejects_callback`. |\n"
    )
    (tmp_path / "traceability.md").write_text(
        "# Traceability Matrix\n\n"
        "| Requirement | Plan Coverage | Section Coverage | Test Coverage | Status |\n"
        "|-------------|---------------|------------------|---------------|--------|\n"
        "| REQ-001 | `codex-plan.md`, `codex-plan-tdd.md` | `section-01-auth.md` | `tests/auth/test_oauth.py` | Covered |\n"
    )
    (tmp_path / "quality-gates.md").write_text("# Quality Gates\n\nREQ-001 covered by pytest and traceability.\n")
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-auth\n"
        "END_MANIFEST -->\n\n"
        "# Sections\n\n"
        "## Project Notes\n"
        "Runtime is python-uv and tests run through pytest. This index is self-contained enough for an implementer to "
        "understand the build order, dependency boundary, and verification command. The auth section owns OAuth callback "
        "tests, provider configuration validation, session creation wiring, and token logging safety. No billing or dashboard "
        "work belongs in this section.\n\n"
        "## Dependency Graph\n"
        "| Section | Depends On | Blocks | Parallelizable |\n"
        "|---------|------------|--------|----------------|\n"
        "| section-01-auth | - | - | No |\n\n"
        "## Execution Order\n"
        "1. Batch 1: section-01-auth. Write tests first, implement the callback flow, run `uv run pytest`, then update "
        "implementation notes.\n\n"
        "## Section Summaries\n"
        "### section-01-auth\n"
        "Implements REQ-001 by validating OAuth callback state, handling provider denial, delegating session creation to "
        "`src/auth/session.py`, and verifying behavior in `tests/auth/test_oauth.py`. It can be implemented alone because "
        "it has no dependency on other sections.\n"
    )
    (sections / "section-01-auth.md").write_text(
        "# section-01-auth\n\n"
        "## Purpose\nImplement REQ-001 OAuth callback behavior.\n\n"
        "## Tests First\nCreate `tests/auth/test_oauth.py` with failing tests for valid callback, invalid state, and provider error.\n\n"
        "## Implementation\nModify `src/auth/oauth.py`, `src/auth/session.py`, and `src/auth/config.py` to validate state, handle provider errors, and create sessions.\n\n"
        "## Acceptance\nREQ-001 is complete when verification passes with `uv run pytest` and invalid callbacks do not create sessions.\n\n"
        "## Background Context\nThis section is self-contained and copies the OAuth ownership, security rationale, session contract, and route boundaries "
        "from the plan. It depends on no prior sections.\n\n"
        "## File Tree\n```\nsrc/auth/oauth.py\nsrc/auth/session.py\nsrc/auth/config.py\ntests/auth/test_oauth.py\n```\n\n"
        "## Risks\nInvalid state, provider denial, duplicate accounts, and token leakage are the main risks.\n\n"
        + "The section is self-contained and includes enough implementation context, expected failures, file paths, contracts, "
        "verification, risks, and acceptance details. " * 12
    )
    return tmp_path


def test_quality_gates_traceability_and_status(tmp_path: Path) -> None:
    planning = write_quality_plan_fixture(tmp_path)

    export_path = tmp_path / "findings.jsonl"
    plan = run_cmd(
        "lint-plan",
        "--planning-dir",
        str(planning),
        "--depth",
        "fast",
        "--profile",
        "enterprise",
        "--strict",
        "--export",
        str(export_path),
    )
    assert plan["success"] is True
    assert plan["score"] == 100
    assert export_path.exists()

    sections = run_cmd("lint-sections", "--planning-dir", str(planning), "--depth", "fast")
    assert sections["success"] is True
    assert sections["section_estimates"][0]["effort"] in {"small", "medium", "large"}

    trace = run_cmd("traceability", "--planning-dir", str(planning))
    assert trace["success"] is True
    assert trace["coverage"]["REQ-001"]["covered"] is True
    assert trace["orphans"] == {"sections": [], "tests": []}

    trace_csv = tmp_path / "trace.csv"
    exported = run_cmd("trace-export", "--planning-dir", str(planning), "--format", "csv", "--output", str(trace_csv))
    assert exported["success"] is True
    assert trace_csv.read_text().startswith("requirement,in_plan,in_tdd,sections,covered")

    next_ready = run_cmd("next-section", "--planning-dir", str(planning))
    assert next_ready["next_section"] == "section-01-auth"

    parallel = run_cmd("parallel-plan", "--planning-dir", str(planning))
    assert parallel["layers"] == [["section-01-auth"]]

    estimates = run_cmd("section-estimates", "--planning-dir", str(planning))
    assert estimates["estimates"][0]["section"] == "section-01-auth"

    prompts = run_cmd("agent-prompts", "--planning-dir", str(planning), "--type", "security-reviewer")
    assert Path(prompts["prompt_files"][0]).exists()

    budget = run_cmd("context-budget", "--planning-dir", str(planning), "--max-words", "12000")
    assert budget["success"] is True
    assert budget["total_words"] > 0

    evidence = run_cmd("lint-evidence", "--planning-dir", str(planning), "--min-files", "3")
    assert evidence["success"] is True
    assert evidence["file_count"] >= 3

    readiness = run_cmd("lint-implementation-readiness", "--planning-dir", str(planning))
    assert readiness["success"] is True
    assert readiness["sections"][0]["section"] == "section-01-auth"

    score = run_cmd("forge-score", "--planning-dir", str(planning), "--depth", "fast")
    assert score["forge_score"] >= 90
    assert score["components"]["traceability"] == 100

    first_history = run_cmd("forge-score", "--planning-dir", str(planning), "--depth", "fast", "--write-history")
    second_history = run_cmd("forge-score", "--planning-dir", str(planning), "--depth", "fast", "--write-history")
    assert Path(first_history["history_path"]).exists()
    assert second_history["trend_delta"] == 0

    ledger = run_cmd("assumption-ledger", "--planning-dir", str(planning), "--write")
    assert ledger["success"] is True
    assert (planning / "assumption-ledger.md").exists()

    packet = run_cmd("implementation-packet", "--planning-dir", str(planning), "--section", "section-01-auth")
    assert Path(packet["output"]).exists()
    assert "REQ-001" in packet["requirements"]

    brief = run_cmd("context-brief", "--planning-dir", str(planning), "--section", "section-01-auth")
    assert brief["success"] is True
    assert brief["word_count"] > 0

    skeletons = run_cmd("tdd-skeletons", "--planning-dir", str(planning), "--framework", "pytest")
    assert Path(skeletons["output"]).exists()
    assert "test_valid_callback_creates_session" in skeletons["tests"]

    progress = run_cmd(
        "implement-progress",
        "--planning-dir",
        str(planning),
        "--section",
        "section-01-auth",
        "--stage",
        "verified",
        "--result",
        "tests passed",
    )
    assert progress["event_count"] == 1

    section_file = planning / "sections" / "section-01-auth.md"
    commit = run_cmd("commit-message", "--section-file", str(section_file))
    assert commit["subject"] == "feat: implement 01 auth"

    diff_file = tmp_path / "scope.diff"
    diff_file.write_text("diff --git a/src/auth/oauth.py b/src/auth/oauth.py\n+++ b/src/auth/oauth.py\n")
    scope = run_cmd("patch-scope", "--section-file", str(section_file), "--diff-file", str(diff_file))
    assert scope["success"] is True
    assert scope["out_of_scope"] == []

    status = run_cmd("status", "--path", str(planning))
    assert status["success"] is True
    assert status["section_progress"]["state"] == "complete"

    diffed = run_cmd("plan-diff", "--before", str(planning / "codex-spec.md"), "--after", str(planning / "codex-plan.md"))
    assert diffed["success"] is True
    assert diffed["word_delta"] > 0


def test_doctor_and_requirement_extraction(tmp_path: Path) -> None:
    doctor = run_cmd("doctor", "--plugin-root", str(ROOT))
    assert doctor["success"] is True
    assert doctor["marketplace"]["name"] == "zagrosi"
    assert doctor["marketplace"]["plugin"] == "zagrosi-forge@zagrosi"

    marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text())
    assert marketplace["plugins"][0]["name"] == "zagrosi-forge"
    assert marketplace["plugins"][0]["source"] == {"source": "local", "path": "."}
    assert marketplace["plugins"][0]["policy"]["authentication"] == "ON_INSTALL"

    req_file = tmp_path / "brief.md"
    req_file.write_text("# Brief\n\n- must support OAuth login\n- should allow logout\n")
    extracted = run_cmd("extract-requirements", "--file", str(req_file), "--write")
    assert extracted["updated"] is True
    assert "REQ-001" in req_file.read_text()
    assert extracted["requirements"][1]["id"] == "REQ-002"


def test_interview_gate_blocks_missing_and_fake_interviews(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project-manifest.md").write_text(
        "<!-- SPLIT_MANIFEST\n"
        "01-auth\n"
        "END_MANIFEST -->\n\n"
        "# Project Manifest\n\n"
        "## Execution Order\nRun `01-auth` first.\n\n"
        "## Dependencies\n`01-auth` depends on no earlier split and blocks later work.\n\n"
        "## Parallelization\nNo parallel work is needed for this fixture.\n\n"
        "## Shared Concerns\nTesting and docs are shared concerns.\n\n"
        "## Commands\nUse `$zagrosi-plan` on `01-auth/spec.md`.\n"
    )
    split = project / "01-auth"
    split.mkdir()
    (split / "spec.md").write_text(
        "# Auth Spec\n\n"
        "## In Scope\nREQ-001: Implement auth.\n\n"
        "## Out Of Scope\nBilling is out of scope.\n\n"
        "## Acceptance Criteria\nDone when auth tests pass.\n\n"
        "## Testing\nRun pytest.\n\n"
        "## Open Questions\nUnknown provider details remain.\n"
    )

    missing = run_raw("lint-project-manifest", "--planning-dir", str(project), "--strict")
    assert missing.returncode != 0
    missing_codes = {item["code"] for item in json.loads(missing.stdout)["findings"]}
    assert "missing-interview" in missing_codes

    postflight = run_raw("postflight", "--phase", "project", "--planning-dir", str(project), "--flight", "strict")
    assert postflight.returncode != 0
    assert "lint-interview" in json.loads(postflight.stdout)["blocking_gates"]

    (project / "zagrosi_project_interview.md").write_text(
        "interview_mode: skipped_with_reason\n"
        "skip_reason: User explicitly asked to proceed from a complete written brief.\n"
    )
    skipped = run_cmd("lint-interview", "--phase", "project", "--planning-dir", str(project), "--strict")
    assert skipped["success"] is True

    planning = write_quality_plan_fixture(tmp_path / "planning")
    (planning / "codex-interview.md").write_text(
        "user_interviewed: true\n\n"
        "# Planning Interview\n\n"
        "Q: TBD\n"
        "A: TBD\n"
    )
    fake = run_raw("lint-interview", "--phase", "plan", "--planning-dir", str(planning), "--strict")
    assert fake.returncode != 0
    fake_codes = {item["code"] for item in json.loads(fake.stdout)["findings"]}
    assert "placeholder-interview" in fake_codes

    (planning / "codex-interview.md").write_text(
        "interview_mode: skipped_with_reason\n"
        "skip_reason: User supplied a complete approved spec and asked to skip questions for this fixture.\n"
    )
    skipped_plan = run_cmd("lint-interview", "--phase", "plan", "--planning-dir", str(planning), "--strict")
    assert skipped_plan["success"] is True


def test_install_codex_updates_config(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[plugins."other@example"]\nenabled = true\n')

    dry_run = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
        "--dry-run",
    )
    assert dry_run["success"] is True
    assert dry_run["changed"] is True
    assert "[marketplaces.zagrosi]" in dry_run["config_preview"]
    assert "[marketplaces.zagrosi]" not in config.read_text()

    installed = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
    )
    assert installed["success"] is True
    assert installed["changed"] is True
    assert installed["backup_path"]
    updated = config.read_text()
    assert "[marketplaces.zagrosi]" in updated
    assert f'source = "{ROOT}"' in updated
    assert '[plugins."zagrosi-forge@zagrosi"]' in updated
    assert "enabled = true" in updated
    assert Path(installed["backup_path"]).exists()

    repeated = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
    )
    assert repeated["success"] is True
    assert repeated["changed"] is False
    assert repeated["backup_path"] is None


def test_strict_profile_blocks_medium_findings(tmp_path: Path) -> None:
    (tmp_path / "codex-plan.md").write_text(
        "<!-- FORGE_META\n"
        '{"artifact_type": "implementation_plan"}\n'
        "END_FORGE_META -->\n\n"
        "# Thin\n\nGoal, architecture, file `src/app.py`, tests, security, risk, rollout, rollback, acceptance.\n"
    )
    result = run_raw("lint-plan", "--planning-dir", str(tmp_path), "--strict")
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["strict"] is True
    assert payload["success"] is False


def test_review_board_governance_and_migration(tmp_path: Path) -> None:
    (tmp_path / "claude-plan.md").write_text("# Old Plan\n\nREQ-001 with tests and `src/app.py`.\n")

    migrated = run_cmd("migrate", "--planning-dir", str(tmp_path))
    assert migrated["success"] is True
    assert (tmp_path / "codex-plan.md").exists()
    assert (tmp_path / "decisions.md").exists()

    prompts = run_cmd("review-board-prompts", "--planning-dir", str(tmp_path))
    assert prompts["success"] is True
    assert len(prompts["prompt_files"]) == 6
    assert all(Path(path).exists() for path in prompts["prompt_files"])

    stubs_dir = tmp_path / "new"
    stubs = run_cmd("write-governance-stubs", "--planning-dir", str(stubs_dir), "--depth", "deep")
    assert stubs["success"] is True
    assert len(stubs["created"]) == 4


def test_eval_suite_and_new_invalid_fixtures() -> None:
    report = run_cmd("eval-suite", "--examples-dir", str(ROOT / "examples"))
    assert report["success"] is True
    assert {Path(row["planning_dir"]).name for row in report["rows"]} == {"01-authentication", "01-auth"}
    assert all(row["forge_score"] == 100 for row in report["rows"])

    fake = run_raw("lint-evidence", "--planning-dir", str(ROOT / "examples" / "invalid" / "fake-evidence"), "--strict")
    assert fake.returncode != 0
    fake_codes = {item["code"] for item in json.loads(fake.stdout)["findings"]}
    assert "missing-command-evidence" in fake_codes

    large = run_raw(
        "lint-implementation-readiness",
        "--planning-dir",
        str(ROOT / "examples" / "invalid" / "overlarge-section"),
        "--max-files",
        "4",
        "--strict",
    )
    assert large.returncode != 0
    large_codes = {item["code"] for item in json.loads(large.stdout)["findings"]}
    assert "too-many-owned-files" in large_codes

    governance = run_raw(
        "lint-artifact-schema",
        "--planning-dir",
        str(ROOT / "examples" / "invalid" / "bad-governance"),
        "--strict",
    )
    assert governance.returncode != 0
    governance_codes = {item["code"] for item in json.loads(governance.stdout)["findings"]}
    assert {"invalid-decisions-table", "invalid-risks-table", "invalid-traceability-table"} <= governance_codes


def test_advanced_operational_commands_and_snapshots(tmp_path: Path) -> None:
    planning = write_quality_plan_fixture(tmp_path / "planning")

    pre = run_cmd(
        "preflight",
        "--phase",
        "plan",
        "--file",
        str(planning / "codex-spec.md"),
        "--target-dir",
        str(ROOT),
        "--flight",
        "advisory",
    )
    assert pre["success"] is True
    assert {gate["name"] for gate in pre["gates"]} >= {"spec-file", "doctor", "codebase-evidence"}

    pretty = run_text(
        "preflight",
        "--phase",
        "plan",
        "--file",
        str(planning / "codex-spec.md"),
        "--target-dir",
        str(ROOT),
        "--pretty",
    )
    assert "ZAGROSI FORGE PREFLIGHT: PLAN" in pretty
    assert "[PASS] doctor" in pretty

    post = run_cmd(
        "postflight",
        "--phase",
        "plan",
        "--planning-dir",
        str(planning),
        "--depth",
        "fast",
        "--flight",
        "advisory",
    )
    assert post["success"] is True
    assert "forge-score" in {gate["name"] for gate in post["gates"]}

    pretty_score = run_text("forge-score", "--planning-dir", str(planning), "--depth", "fast", "--pretty")
    assert "ZAGROSI FORGE SCORE" in pretty_score
    assert "Components:" in pretty_score

    impl_pre = run_cmd(
        "preflight",
        "--phase",
        "implement",
        "--sections-dir",
        str(planning / "sections"),
        "--target-dir",
        str(tmp_path),
        "--flight",
        "advisory",
    )
    assert impl_pre["success"] is True
    assert "suggest-section-splits" in {gate["name"] for gate in impl_pre["gates"]}

    schema = run_cmd("lint-artifact-schema", "--planning-dir", str(planning), "--strict")
    assert schema["success"] is True
    assert schema["score"] == 100

    split = run_cmd(
        "suggest-section-splits",
        "--planning-dir",
        str(ROOT / "examples" / "invalid" / "overlarge-section"),
        "--max-files",
        "4",
    )
    assert split["suggestions"]
    assert split["suggestions"][0]["recommendation"] == "Split before implementation."

    ok_diff = tmp_path / "ok.diff"
    ok_diff.write_text(
        "diff --git a/src/auth/oauth.py b/src/auth/oauth.py\n"
        "+++ b/src/auth/oauth.py\n"
        "diff --git a/tests/auth/test_oauth.py b/tests/auth/test_oauth.py\n"
        "+++ b/tests/auth/test_oauth.py\n"
    )
    drift_ok = run_cmd("implementation-drift", "--planning-dir", str(planning), "--diff-file", str(ok_diff), "--strict")
    assert drift_ok["success"] is True
    assert drift_ok["out_of_scope"] == []

    bad_diff = tmp_path / "bad.diff"
    bad_diff.write_text(
        "diff --git a/src/auth/oauth.py b/src/auth/oauth.py\n"
        "+++ b/src/auth/oauth.py\n"
        "diff --git a/src/billing/plans.py b/src/billing/plans.py\n"
        "+++ b/src/billing/plans.py\n"
    )
    drift_bad = run_raw("implementation-drift", "--planning-dir", str(planning), "--diff-file", str(bad_diff), "--strict")
    assert drift_bad.returncode != 0
    assert "implementation-drift-file" in {item["code"] for item in json.loads(drift_bad.stdout)["findings"]}

    target = tmp_path / "target"
    target.mkdir()
    (target / "package.json").write_text('{"scripts":{"test":"vitest run","lint":"eslint .","build":"vite build"}}\n')
    tests_dir = target / "tests"
    tests_dir.mkdir()
    (tests_dir / "auth.test.ts").write_text("import { expect, test } from 'vitest';\n")
    evidence = run_cmd("codebase-evidence", "--target-dir", str(target), "--planning-dir", str(planning), "--write")
    assert "npm run test" in evidence["candidate_commands"]
    assert Path(evidence["output"]).exists()

    report_path = tmp_path / "report.html"
    report = run_cmd("report", "--planning-dir", str(planning), "--depth", "fast", "--output", str(report_path))
    assert report["success"] is True
    assert "Zagrosi Forge Report" in report_path.read_text()

    trial = run_cmd(
        "e2e-trial-record",
        "--planning-dir",
        str(planning),
        "--name",
        "fixture trial",
        "--output-dir",
        str(tmp_path / "trials"),
        "--implementation-success",
        "yes",
        "--time-to-plan-minutes",
        "42",
    )
    assert Path(trial["output"]).exists()
    assert trial["record"]["metrics"]["implementation_success"] == "yes"

    for planning_dir, snapshot in [
        (ROOT / "examples" / "saas" / "01-authentication", ROOT / "examples" / "evals" / "golden" / "saas-authentication-forge-score.json"),
        (ROOT / "examples" / "typescript-app" / "01-auth", ROOT / "examples" / "evals" / "golden" / "typescript-auth-preferences-forge-score.json"),
    ]:
        actual = run_cmd("forge-score", "--planning-dir", str(planning_dir), "--depth", "standard", "--strict")
        expected = json.loads(snapshot.read_text())
        assert {
            "planning_dir_name": Path(actual["planning_dir"]).name,
            "forge_score": actual["forge_score"],
            "grade": actual["grade"],
            "components": actual["components"],
        } == expected

    release = run_cmd("release-check", "--plugin-root", str(ROOT))
    assert release["success"] is True
    assert any(".agents/plugins/marketplace.json" in row["command"] for row in release["results"])


def test_lint_project_manifest_fixture() -> None:
    for example in ("saas", "typescript-app"):
        result = run_cmd("lint-project-manifest", "--planning-dir", str(ROOT / "examples" / example), "--strict")
        assert result["success"] is True
        assert result["score"] == 100


def test_typescript_fixture_and_invalid_fixture_snapshots() -> None:
    planning = ROOT / "examples" / "typescript-app" / "01-auth"
    assert run_cmd("lint-plan", "--planning-dir", str(planning), "--depth", "standard", "--strict")["score"] == 100
    assert run_cmd("lint-sections", "--planning-dir", str(planning), "--depth", "standard", "--strict")["score"] == 100
    assert run_cmd("traceability", "--planning-dir", str(planning), "--strict")["score"] == 100
    assert run_cmd("forge-score", "--planning-dir", str(planning), "--depth", "standard", "--strict")["forge_score"] == 100

    saas_planning = ROOT / "examples" / "saas" / "01-authentication"
    assert run_cmd("lint-plan", "--planning-dir", str(saas_planning), "--depth", "standard", "--strict")["score"] == 100
    assert run_cmd("lint-sections", "--planning-dir", str(saas_planning), "--depth", "standard", "--strict")["score"] == 100
    assert run_cmd("traceability", "--planning-dir", str(saas_planning), "--strict")["score"] == 100
    assert run_cmd("forge-score", "--planning-dir", str(saas_planning), "--depth", "standard", "--strict")["forge_score"] == 100

    missing_index = run_raw("lint-sections", "--planning-dir", str(ROOT / "examples" / "invalid" / "missing-section-index"))
    assert missing_index.returncode != 0
    assert json.loads(missing_index.stdout)["findings"][0]["code"] == "missing-section-index"

    vague = run_raw("lint-sections", "--planning-dir", str(ROOT / "examples" / "invalid" / "vague-section"))
    assert vague.returncode != 0
    codes = {item["code"] for item in json.loads(vague.stdout)["findings"]}
    assert "vague-section-name" in codes


def test_skill_files_are_codex_native() -> None:
    banned = ["TaskList", "TaskUpdate", "AskUserQuestion", "CLAUDE_CODE_TASK_LIST_ID"]
    for skill in (ROOT / "skills").glob("*/SKILL.md"):
        content = skill.read_text()
        assert "[TODO:" not in content
        for token in banned:
            assert token not in content, f"{token} leaked into {skill}"
