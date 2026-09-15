from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


SCRIPT = ROOT / "scripts" / "zagrosi_skills.py"


PLUGIN_VERSION = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())["version"]


IMPLEMENTATION_SOURCE_RELATIVE_PATHS = {
    "tool": Path("scripts/zagrosi_skills.py"),
    "skill": Path("skills/zagrosi-implement/SKILL.md"),
    "test": Path("tests/test_zagrosi_skills.py"),
}


DETACHED_CONTRACT_RELATIVE_PATH = Path("skills/zagrosi-implement/references/detached-frozen.md")


def run_cmd(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(result.stdout)


def run_raw(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


def run_script_raw(
    script: Path,
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=cwd or script.parents[1],
        env=env,
        text=True,
        capture_output=True,
    )


def run_text(*args: str, cwd: Path | None = None) -> str:
    result = run_raw(*args, cwd=cwd)
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout


def load_zagrosi_module(script: Path = SCRIPT):
    from runtime_support import load_runtime
    return load_runtime(script)


def write_required_plan_artifacts(planning_dir: Path) -> None:
    def write_missing(relative: str, content: str) -> None:
        path = planning_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(content)

    write_missing(
        "codex-research.md",
        "# Research\n\nVerified current state with `rg` and `uv run pytest`. Existing files include `scripts/zagrosi_skills.py`, "
        "`tests/test_zagrosi_skills.py`, and `skills/zagrosi-implement/SKILL.md`.\n",
    )
    write_missing(
        "codex-evidence.md",
        "# Codebase Evidence\n\nRuntime: `pyproject.toml`. Source files: `scripts/zagrosi_skills.py`. "
        "Tests: `tests/test_zagrosi_skills.py`. Commands: `uv run pytest`.\n",
    )
    write_missing(
        "codex-interview.md",
        "interview_mode: skipped_with_reason\n"
        "skip_reason: Test fixture has complete approved requirements and no product ambiguity.\n",
    )
    write_missing(
        "codex-spec.md",
        "# Spec\n\nREQ-001: Implement the planned Forge behavior with tests, traceability, and documentation.\n",
    )
    write_missing(
        "codex-plan.md",
        "# Plan\n\nREQ-001 updates `scripts/zagrosi_skills.py` and verifies with `tests/test_zagrosi_skills.py`. "
        "Architecture keeps workflow policy in Forge helpers. Rollback is reverting the helper change.\n",
    )
    write_missing(
        "codex-integration-notes.md",
        "# Review Integration\n\nAccepted review: enforce process completeness before implementation and keep traceability explicit.\n",
    )
    write_missing(
        "codex-plan-tdd.md",
        "# TDD Plan\n\nREQ-001: `test_implement_setup_and_record` verifies implementation recording with `uv run pytest`.\n",
    )
    write_missing(
        "decisions.md",
        "# Decision Log\n\n"
        "| ID | Date | Decision | Alternatives | Rationale | Impact |\n"
        "|----|------|----------|--------------|-----------|--------|\n"
        "| DEC-001 | Test | Enforce Forge process artifacts. | Rely on operator memory. | Durable records are required. | Implementation waits for planning artifacts. |\n",
    )
    write_missing(
        "risk-register.md",
        "# Risk Register\n\n"
        "| ID | Risk | Severity | Likelihood | Mitigation | Section | Verification |\n"
        "|----|------|----------|------------|------------|---------|--------------|\n"
        "| RISK-001 | Process artifacts are missing. | High | Medium | Gate implementation setup. | section-01-foundation | `uv run pytest`. |\n",
    )
    write_missing(
        "traceability.md",
        "# Traceability Matrix\n\n"
        "| Requirement | Plan Coverage | Section Coverage | Test Coverage | Status |\n"
        "|-------------|---------------|------------------|---------------|--------|\n"
        "| REQ-001 | `codex-plan.md` | `section-01-foundation.md` | `test_implement_setup_and_record` | Planned |\n",
    )
    write_missing("quality-gates.md", "# Quality Gates\n\nRun `uv run pytest`, `lint-plan-artifacts`, and `traceability`.\n")
    write_missing("reviews/process.md", "# Process Review\n\nNo blocking findings. The plan names files, tests, risks, and verification.\n")


def write_single_section_fixture(planning_dir: Path, section: str = "section-01-foundation") -> Path:
    sections = planning_dir / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        f"{section}\n"
        "END_MANIFEST -->\n"
    )
    (sections / f"{section}.md").write_text(
        "# Section\n\n"
        "REQ-001 changes `scripts/zagrosi_skills.py` and `tests/test_zagrosi_skills.py`.\n"
        "Tests first, expected failure, implementation, acceptance, rollback, and verification.\n"
    )
    write_required_plan_artifacts(planning_dir)
    review_dir = planning_dir / "implementation" / "code_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / f"{section}-diff.md").write_text("# Diff\n\nChanged helper and tests.\n")
    (review_dir / f"{section}-review.md").write_text("# Review\n\nNo blocking findings.\n")
    return sections


def write_non_topological_section_fixture(planning_dir: Path) -> Path:
    sections = planning_dir / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-foundation\n"
        "section-02-api\n"
        "section-03-storage\n"
        "END_MANIFEST -->\n\n"
        "# Sections\n\n"
        "## Dependency Graph\n\n"
        "| Section | Depends on |\n"
        "|---|---|\n"
        "| section-01-foundation | section-03-storage |\n"
        "| section-02-api | section-01-foundation |\n"
        "| section-03-storage | none |\n"
    )
    for section in ("section-01-foundation", "section-02-api", "section-03-storage"):
        (sections / f"{section}.md").write_text(
            f"# {section}\n\nTests first, expected failure, implementation, acceptance, rollback, and verification.\n"
        )
    write_required_plan_artifacts(planning_dir)
    return sections


def write_implementation_drift_fixture(
    planning: Path,
    manifest: tuple[str, ...],
    section_bodies: dict[str, str],
) -> None:
    sections = planning / "sections"
    sections.mkdir(parents=True)
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        + "".join(f"{section}\n" for section in manifest)
        + "END_MANIFEST -->\n"
    )
    for section, body in section_bodies.items():
        (sections / f"{section}.md").write_text(body)


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


def write_lean_plan_fixture(planning_dir: Path) -> Path:
    planning_dir.mkdir(parents=True, exist_ok=True)
    source_spec = planning_dir / "spec.md"
    source_spec.write_text(
        "# Spec\n\n"
        "REQ-001: Add compact Forge planning with semantic checks and bounded output.\n"
    )
    (planning_dir / "zagrosi_plan_config.json").write_text(
        json.dumps(
            {
                "initial_file": str(source_spec),
                "planning_dir": str(planning_dir),
                "review_mode": "codex_review",
                "depth_mode": "lean",
                "workflow": "zagrosi-plan",
            }
        )
    )
    (planning_dir / "codex-plan.md").write_text(
        '<!-- FORGE_META\n{"artifact_type":"implementation_plan","workflow":"zagrosi-plan",'
        '"depth_mode":"lean","source":"spec.md","requirement_ids":["REQ-001"]}\nEND_FORGE_META -->\n\n'
        "# Plan\n\n"
        "Goal: REQ-001 makes Forge concise. Non-goal: weaken safety gates.\n\n"
        "Current-state evidence: `scripts/zagrosi_skills.py` duplicates checks; "
        "`tests/test_zagrosi_skills.py` covers CLI behavior.\n\n"
        "Design and contract: add lean defaults in `scripts/zagrosi_skills.py`; retain explicit standard/deep modes. "
        "Tests first: `test_workflow_options_defaults_to_lean_without_routine_interview`.\n\n"
        "Risk/security: compact output could omit a required invariant. Mitigate with semantic lint, exact path ownership, "
        "traceability, adversarial review, and final verification. Rollback: revert lean mode.\n\n"
        "Acceptance: default setup selects lean; required tests pass.\n"
    )
    reviews = planning_dir / "reviews"
    reviews.mkdir()
    (reviews / "codex.md").write_text(
        "# Review\n\nPASS — semantic checks, TDD, ownership, traceability, and rollback remain required.\n",
        encoding="utf-8",
    )
    sections = planning_dir / "sections"
    sections.mkdir()
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest tests/test_zagrosi_skills.py -q\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-lean-default\n"
        "END_MANIFEST -->\n\n"
        "Dependencies: none. Execution order: section-01-lean-default. Parallel: no.\n"
    )
    (sections / "section-01-lean-default.md").write_text(
        "# section-01-lean-default\n\n"
        "Goal: implement REQ-001. Dependencies: none.\n\n"
        "## Exact Path Ownership\n\n"
        "- `scripts/zagrosi_skills.py`\n"
        "- `tests/test_zagrosi_skills.py`\n\n"
        "## Tests First\n\n"
        "Add `test_workflow_options_defaults_to_lean_without_routine_interview`; expected failure: default is standard/deep.\n\n"
        "## Implementation\n\nModify default selection and compact artifact validation. Contract: explicit deep remains explicit.\n\n"
        "## Risks And Rollback\n\nRisk: missing safety coverage. Keep semantic gates. Rollback: revert lean default.\n\n"
        "## Acceptance And Verification\n\nREQ-001 done when `uv run pytest tests/test_zagrosi_skills.py -q` passes.\n"
    )
    return planning_dir


def write_compact_project_fixture(planning_dir: Path) -> Path:
    planning_dir.mkdir(parents=True, exist_ok=True)
    (planning_dir / "requirements.md").write_text(
        "# Requirements\n\nREQ-001: Add auth.\nREQ-002: Add billing.\n"
    )
    (planning_dir / "project-manifest.md").write_text(
        '<!-- FORGE_META\n'
        '{"artifact_type":"project_manifest","depth_mode":"lean","source":"requirements.md"}\n'
        'END_FORGE_META -->\n\n'
        '<!-- SPLIT_MANIFEST\n01-auth\n02-billing\nEND_MANIFEST -->\n\n'
        "# Project Manifest\n\n"
        "| Split | REQ | Depends on | Owns/boundary | Next command |\n"
        "|---|---|---|---|---|\n"
        "| 01-auth | REQ-001 | none | `src/auth.py` | `$zagrosi-plan 01-auth/spec.md` |\n"
        "| 02-billing | REQ-002 | 01-auth | `src/billing.py` | `$zagrosi-plan 02-billing/spec.md` |\n\n"
        "Execution order: 01-auth then 02-billing. Dependencies are listed above. "
        "Parallel work is blocked by the dependency. Shared concerns: tests only.\n"
    )
    specs = {
        "01-auth": ("REQ-001", "src/auth.py", "none"),
        "02-billing": ("REQ-002", "src/billing.py", "01-auth"),
    }
    for split, (req_id, path, dependency) in specs.items():
        split_dir = planning_dir / split
        split_dir.mkdir()
        (split_dir / "spec.md").write_text(
            f"# {split}\n\nDependencies: {dependency}\nBoundary: `{path}`\n\n"
            f"In scope: {req_id}. Out of scope: other splits. "
            "Tests verify behavior. Risk: boundary drift. Acceptance criteria: done when tests pass.\n"
        )
    return planning_dir


def project_manifest_codes(planning_dir: Path) -> set[str]:
    result = run_raw("lint-project-manifest", "--planning-dir", str(planning_dir), "--strict")
    assert result.returncode != 0, result.stderr + result.stdout
    return {item["code"] for item in json.loads(result.stdout)["findings"]}
