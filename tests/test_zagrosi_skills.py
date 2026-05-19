from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zagrosi_skills.py"


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


def run_text(*args: str, cwd: Path | None = None) -> str:
    result = run_raw(*args, cwd=cwd)
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout


def load_zagrosi_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("zagrosi_skills_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def test_project_setup_from_chat_brief_materializes_requirements(tmp_path: Path) -> None:
    brief = "Improve Zagrosi Forge so project decomposition can start from a chat idea and interview."

    setup = run_cmd("project-setup", "--brief", brief, "--planning-dir", str(tmp_path))

    generated = tmp_path / "requirements.md"
    assert setup["success"] is True
    assert setup["input_mode"] == "chat"
    assert setup["initial_file"] == str(generated)
    assert setup["generated_requirements_file"] == str(generated)
    assert setup["resume_step"] == 1
    assert setup["preflight"]["input_mode"] == "chat"
    assert setup["preflight"]["gates"][0]["name"] == "chat-brief"
    assert generated.exists()
    assert brief in generated.read_text()

    resumed = run_cmd("project-setup", "--brief", brief, "--planning-dir", str(tmp_path))
    assert resumed["mode"] == "resume"
    assert resumed["initial_file"] == str(generated)
    assert resumed["generated_requirements_file"] is None
    assert not (tmp_path / "requirements-2.md").exists()

    preflight = run_cmd("preflight", "--phase", "project", "--brief", brief, "--planning-dir", str(tmp_path))
    assert preflight["success"] is True
    assert preflight["input_mode"] == "chat"


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


def test_parallel_plan_parses_documented_dependency_graph_prose(tmp_path: Path) -> None:
    sections = tmp_path / "sections"
    sections.mkdir(parents=True)
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-foundation\n"
        "section-02-api\n"
        "section-03-ui\n"
        "END_MANIFEST -->\n\n"
        "# Sections\n\n"
        "## Dependency Graph\n\n"
        "- section-02-api depends on section-01-foundation.\n"
        "- `section-03-ui` depends on `section-02-api`.\n"
    )

    parallel = run_cmd("parallel-plan", "--planning-dir", str(tmp_path))

    assert parallel["layers"] == [
        ["section-01-foundation"],
        ["section-02-api"],
        ["section-03-ui"],
    ]


def test_parallel_plan_reports_unknown_dependency_tokens(tmp_path: Path) -> None:
    sections = tmp_path / "sections"
    sections.mkdir(parents=True)
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-foundation\n"
        "section-02-api\n"
        "END_MANIFEST -->\n\n"
        "# Sections\n\n"
        "## Dependency Graph\n\n"
        "- section-02-api depends on section-99-missing.\n"
    )

    result = run_raw("parallel-plan", "--planning-dir", str(tmp_path))

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["unknown_dependencies"] == {"section-02-api": ["section-99-missing"]}
    assert payload["blocked_or_cyclic"] == ["section-02-api"]


def test_status_reports_plan_artifact_sequence(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Improve Forge\n\nMake operator workflows clearer.\n")
    run_cmd("plan-setup", "--file", str(spec), "--plugin-root", str(ROOT), "--flight", "off")

    status = run_cmd("status", "--path", str(tmp_path))
    assert "codex-research.md" in status["next_action"]

    (tmp_path / "codex-research.md").write_text("# Research\n\nCurrent state verified with `rg` and `uv run pytest`.\n")
    status = run_cmd("status", "--path", str(tmp_path))
    assert "codex-interview.md" in status["next_action"]

    (tmp_path / "codex-interview.md").write_text(
        "interview_mode: skipped_with_reason\n"
        "skip_reason: Fixture has enough detail to proceed.\n"
    )
    status = run_cmd("status", "--path", str(tmp_path))
    assert "codex-spec.md" in status["next_action"]

    (tmp_path / "codex-spec.md").write_text("# Spec\n\nREQ-001: Improve status.\n")
    status = run_cmd("status", "--path", str(tmp_path))
    assert "codex-plan.md" in status["next_action"]

    (tmp_path / "codex-plan.md").write_text("")
    status = run_cmd("status", "--path", str(tmp_path))
    assert "codex-plan.md" in status["next_action"]

    (tmp_path / "codex-plan.md").write_text("# Plan\n\nREQ-001 implementation plan.\n")
    status = run_cmd("status", "--path", str(tmp_path))
    assert "review" in status["next_action"].lower()

    (tmp_path / "codex-integration-notes.md").write_text("# Review Integration\n\nAccepted review items.\n")
    status = run_cmd("status", "--path", str(tmp_path))
    assert "codex-plan-tdd.md" in status["next_action"]

    (tmp_path / "codex-plan-tdd.md").write_text("# TDD\n\n`test_status_reports_plan_artifact_sequence` fails first.\n")
    status = run_cmd("status", "--path", str(tmp_path))
    assert "sections/index.md" in status["next_action"]

    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-status\n"
        "END_MANIFEST -->\n\n"
        "# Sections\n"
    )
    status = run_cmd("status", "--path", str(tmp_path))
    assert "section files" in status["next_action"]


def test_status_exposes_plan_artifact_state(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Improve Forge\n\nExpose plan artifact state.\n")
    run_cmd("plan-setup", "--file", str(spec), "--plugin-root", str(ROOT), "--flight", "off")
    (tmp_path / "codex-research.md").write_text("# Research\n\nVerified current state.\n")
    (tmp_path / "codex-plan.md").write_text("   ")

    status = run_cmd("status", "--path", str(tmp_path))

    assert status["files"]["zagrosi_plan_config"] == str(tmp_path / "zagrosi_plan_config.json")
    assert status["plan_artifacts"]["research"] == str(tmp_path / "codex-research.md")
    assert status["plan_artifacts"]["interview"] is None
    assert status["plan_artifacts"]["plan"] is None
    assert status["plan_artifacts"]["section_index"] is None
    assert status["section_progress"]["state"] == "no_index"


def test_commands_catalog_outputs_grouped_json_and_pretty_text() -> None:
    catalog = run_cmd("commands")

    required = {"project-setup", "plan-setup", "implement-setup", "status", "codebase-evidence", "eval-suite", "release-check"}
    by_name = {entry["name"]: entry for entry in catalog["commands"]}
    assert required <= set(by_name)
    for name in required:
        entry = by_name[name]
        assert entry["phase"]
        assert entry["summary"]
        assert isinstance(entry["aliases"], list)
        assert entry["examples"]

    plan_catalog = run_cmd("commands", "--phase", "plan")
    assert plan_catalog["commands"]
    assert {entry["phase"] for entry in plan_catalog["commands"]} <= {"plan", "all", "quality", "utility"}

    pretty = run_text("commands", "--pretty")
    assert "PLAN" in pretty.upper()
    assert "status" in pretty
    assert "codebase-evidence" in pretty


def test_command_catalog_matches_parser_aliases() -> None:
    catalog = run_cmd("commands")
    entries = catalog["commands"]
    names = {entry["name"] for entry in entries}
    aliases = {alias for entry in entries for alias in entry["aliases"]}

    assert {"project-setup", "plan-setup", "implement-setup", "status", "doctor", "eval-suite", "release-check"} <= names
    assert {
        "project",
        "plan",
        "implement",
        "install",
        "deep-project-setup",
        "deep-plan-setup",
        "deep-implement-setup",
    } <= aliases

    help_text = run_text("--help")
    assert "Inspect workflow state" in help_text
    assert "Show grouped command catalog" in help_text


def test_codebase_evidence_includes_forge_surface_without_cache_noise(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    planning.mkdir()

    evidence = run_cmd("codebase-evidence", "--target-dir", str(ROOT), "--planning-dir", str(planning), "--write")

    assert "scripts/zagrosi_skills.py" in evidence["source_files"]
    assert "skills/zagrosi-plan/SKILL.md" in evidence["skill_files"]
    assert ".codex-plugin/plugin.json" in evidence["plugin_metadata"]
    assert ".github/workflows/validate.yml" in evidence["ci_files"]
    assert "examples/evals/suite.json" in evidence["example_files"]

    grouped_paths = [
        path
        for key in ("runtime_files", "test_files", "source_files", "skill_files", "plugin_metadata", "ci_files", "example_files")
        for path in evidence[key]
    ]
    assert not any(".git/" in path or ".venv/" in path or "__pycache__/" in path for path in grouped_paths)
    assert not any(".codex/plugins/cache" in path for path in grouped_paths)

    written = Path(evidence["output"]).read_text()
    assert "Forge Source Files" in written
    assert "Skills" in written
    assert "Assumptions / Open Questions" in written


def test_lint_evidence_accepts_expanded_codebase_evidence(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    (target / "scripts").mkdir()
    (target / "scripts" / "tool.py").write_text("print('fixture')\n")
    (target / "tests").mkdir()
    (target / "tests" / "test_tool.py").write_text("def test_tool():\n    assert True\n")

    planning = tmp_path / "planning"
    planning.mkdir()
    run_cmd("codebase-evidence", "--target-dir", str(target), "--planning-dir", str(planning), "--write")
    (planning / "codex-plan.md").write_text(
        "# Plan\n\n"
        "REQ-006: Current state verified with `uv run pytest`; see `codex-evidence.md` for existing files.\n"
        "Assumption: no open question blocks evidence linting.\n"
    )

    evidence = run_cmd("lint-evidence", "--planning-dir", str(planning), "--strict")

    assert evidence["success"] is True
    assert evidence["file_count"] >= 3
    assert "codex-evidence.md" in evidence["artifacts"]


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
    )
    assert record["success"] is True
    assert record["postflight"]["phase"] == "implement"
    state = json.loads((tmp_path / "implementation" / "zagrosi_implement_state.json").read_text())
    assert state["completed_sections"]["section-01-foundation"]["commit"] == "abc123"

    impl_gate = run_cmd("lint-implementation-state", "--sections-dir", str(sections))
    assert impl_gate["success"] is True
    assert "section-01-foundation" in impl_gate["completed_sections"]


def test_implement_record_section_refreshes_traceability_matrix(tmp_path: Path) -> None:
    sections = tmp_path / "sections"
    sections.mkdir()
    (tmp_path / "codex-spec.md").write_text("# Spec\n\nREQ-001: Implement status.\nREQ-002: Document status.\n")
    (tmp_path / "codex-plan.md").write_text("# Plan\n\nREQ-001 in `scripts/tool.py`.\nREQ-002 in `README.md`.\n")
    (tmp_path / "codex-plan-tdd.md").write_text(
        "# TDD\n\nREQ-001: `test_status_flow`.\nREQ-002: `test_readme_status_docs`.\n"
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
        "--commit",
        "abc123",
        "--flight",
        "off",
    )

    matrix = (tmp_path / "traceability.md").read_text()
    assert recorded["traceability_matrix"] == str(tmp_path / "traceability.md")
    assert "| Requirement | Plan Coverage | Section Coverage | Test Coverage | Implementation Evidence | Status |" in matrix
    assert (
        "| REQ-001 | `codex-plan.md` | `section-01-status.md` | `test_status_flow` | commit `abc123` | Implemented |"
        in matrix
    )
    assert (
        "| REQ-002 | `codex-plan.md` | `section-02-docs.md` | `test_readme_status_docs` | - | Planned |"
        in matrix
    )


def test_implement_record_section_stores_evidence_and_refreshes_traceability(tmp_path: Path) -> None:
    sections = write_single_section_fixture(tmp_path)
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


def test_implementation_state_requires_review_decisions(tmp_path: Path) -> None:
    sections = write_single_section_fixture(tmp_path)
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
    (tmp_path / "implementation" / "usage.md").write_text("# Usage\n\nRun `uv run pytest`.\n")

    missing = run_raw("lint-implementation-state", "--sections-dir", str(sections), "--strict")
    assert missing.returncode != 0
    payload = json.loads(missing.stdout)
    assert "missing-review-decisions" in {item["code"] for item in payload["findings"]}

    (tmp_path / "implementation" / "code_review" / "section-01-foundation-decisions.md").write_text(
        "# Decisions\n\nAccepted all review items.\n"
    )
    passed = run_cmd("lint-implementation-state", "--sections-dir", str(sections), "--strict")
    assert passed["success"] is True


def test_implement_setup_blocks_incomplete_forge_process_even_with_flight_off(tmp_path: Path) -> None:
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
    assert "missing-research" in codes
    assert "missing-plan" in codes
    assert "placeholder-decisions" in codes


def test_patch_scope_preserves_long_file_extensions(tmp_path: Path) -> None:
    section_file = tmp_path / "section-01-snapshots.md"
    section_file.write_text(
        "# Section\n\n"
        "Update `examples/evals/suite.json`, `src/ui/Widget.jsx`, `src/ui/App.tsx`, and `config/settings.yaml`.\n"
    )
    diff_file = tmp_path / "scope.diff"
    diff_file.write_text(
        "diff --git a/examples/evals/suite.json b/examples/evals/suite.json\n"
        "+++ b/examples/evals/suite.json\n"
        "diff --git a/src/ui/Widget.jsx b/src/ui/Widget.jsx\n"
        "+++ b/src/ui/Widget.jsx\n"
        "diff --git a/src/ui/App.tsx b/src/ui/App.tsx\n"
        "+++ b/src/ui/App.tsx\n"
        "diff --git a/config/settings.yaml b/config/settings.yaml\n"
        "+++ b/config/settings.yaml\n"
    )

    scope = run_cmd("patch-scope", "--section-file", str(section_file), "--diff-file", str(diff_file), "--strict")

    assert scope["success"] is True
    assert scope["declared_files"] == [
        "config/settings.yaml",
        "examples/evals/suite.json",
        "src/ui/App.tsx",
        "src/ui/Widget.jsx",
    ]
    assert scope["out_of_scope"] == []


def test_patch_scope_accepts_declared_frontend_assets(tmp_path: Path) -> None:
    section_file = tmp_path / "section-01-ui.md"
    section_file.write_text("# Section\n\nUpdate `index.html`, `src/App.css`, and `public/logo.svg`.\n")
    diff_file = tmp_path / "scope.diff"
    diff_file.write_text(
        "diff --git a/index.html b/index.html\n"
        "+++ b/index.html\n"
        "diff --git a/src/App.css b/src/App.css\n"
        "+++ b/src/App.css\n"
        "diff --git a/public/logo.svg b/public/logo.svg\n"
        "+++ b/public/logo.svg\n"
    )

    scope = run_cmd("patch-scope", "--section-file", str(section_file), "--diff-file", str(diff_file), "--strict")

    assert scope["success"] is True
    assert scope["declared_files"] == ["index.html", "public/logo.svg", "src/App.css"]
    assert scope["out_of_scope"] == []


def test_patch_scope_reports_untracked_files_by_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "src/auth").mkdir(parents=True)
    (repo / "src/auth/extra.py").write_text("SECRET = 'new file'\n")

    section_file = tmp_path / "section-01-auth.md"
    section_file.write_text("# Section\n\nModify `src/auth/oauth.py`.\n")

    scope = run_raw("patch-scope", "--section-file", str(section_file), "--repo", str(repo))

    assert scope.returncode != 0
    payload = json.loads(scope.stdout)
    assert "src/auth/extra.py" in payload["changed_files"]
    assert payload["out_of_scope"] == ["src/auth/extra.py"]
    assert any(item["code"] == "out-of-scope-file" for item in payload["findings"])


def test_patch_scope_staged_includes_tracked_worktree_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "src/auth").mkdir(parents=True)
    extra = repo / "src/auth/extra.py"
    extra.write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "src/auth/extra.py"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    extra.write_text("VALUE = 2\n")

    section_file = tmp_path / "section-01-auth.md"
    section_file.write_text("# Section\n\nModify `src/auth/oauth.py`.\n")

    scope = run_raw("patch-scope", "--section-file", str(section_file), "--repo", str(repo), "--staged")

    assert scope.returncode != 0
    payload = json.loads(scope.stdout)
    assert "src/auth/extra.py" in payload["changed_files"]
    assert payload["out_of_scope"] == ["src/auth/extra.py"]


def test_implement_progress_preserves_overlapping_writes(tmp_path: Path, monkeypatch) -> None:
    module = load_zagrosi_module()
    planning = tmp_path / "planning"
    planning.mkdir()
    start = threading.Barrier(2)
    original_write_json = module.write_json

    def slow_progress_write(path: Path, payload: dict) -> None:
        if path.name == "forge-progress.json":
            time.sleep(0.05)
        original_write_json(path, payload)

    monkeypatch.setattr(module, "write_json", slow_progress_write)

    def record(stage: str) -> int:
        start.wait(timeout=2)
        return module.implement_progress(
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


def test_workflow_options_recommends_deep_and_interview_for_ambiguous_prompt() -> None:
    payload = run_cmd(
        "workflow-options",
        "--brief",
        "maybe use external review or auto PR, whatever you recommend",
    )

    assert payload["success"] is True
    assert payload["depth"]["recommended"] == "deep"
    assert payload["depth"]["requires_confirmation"] is True
    assert payload["interview"]["required"] is True
    assert payload["interview"]["use_structured_input_when_available"] is True
    assert payload["interview"]["fallback"] == "chat"
    assert payload["autonomy"]["auto_commit"] is False
    assert payload["autonomy"]["auto_pr"] is False
    assert payload["autonomy"]["ci_watch"] is False
    assert payload["autonomy"]["fix_watch_loop"] is False


def test_workflow_options_respects_explicit_depth() -> None:
    payload = run_cmd("workflow-options", "--brief", "small docs fix", "--depth", "fast")

    assert payload["success"] is True
    assert payload["depth"]["selected"] == "fast"
    assert payload["depth"]["recommended"] == "fast"
    assert payload["depth"]["requires_confirmation"] is False


def test_workflow_options_includes_recommended_interview_choices_with_rationale() -> None:
    payload = run_cmd(
        "workflow-options",
        "--brief",
        "maybe use external review, web research, auto PR, whatever you recommend",
    )

    option_sets = payload["interview"]["option_sets"]
    assert option_sets
    assert any(
        len([option for option in option_set["options"] if option["recommended"]]) == 1
        for option_set in option_sets
    )
    for option_set in option_sets:
        recommended = [option for option in option_set["options"] if option["recommended"]]
        assert len(recommended) <= 1
        if recommended:
            option = recommended[0]
            assert option["recommended_label"].endswith("(Recommended)")
            assert option["rationale"]


def test_capability_inventory_redacts_secrets_and_reports_tools(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[plugins."github@openai-curated"]\n'
        "enabled = true\n\n"
        '[plugins."zagrosi-forge@zagrosi"]\n'
        "enabled = true\n\n"
        "[mcp_servers.context7]\n"
        'url = "https://mcp.context7.com/mcp"\n\n'
        "[mcp_servers.context7.http_headers]\n"
        'CONTEXT7_API_KEY = "SECRET-DO-NOT-LEAK"\n'
    )

    payload = run_cmd("capability-inventory", "--plugin-root", str(ROOT), "--config", str(config))
    serialized = json.dumps(payload)

    assert payload["success"] is True
    assert "SECRET-DO-NOT-LEAK" not in serialized
    assert {"gh", "codex", "claude", "gemini"} <= set(payload["local_tools"])
    assert "github@openai-curated" in {item["id"] for item in payload["plugins"]["configured"]}
    assert "zagrosi-forge@zagrosi" in {item["id"] for item in payload["plugins"]["configured"]}
    assert "context7" in {item["name"] for item in payload["mcp_servers"]["configured"]}
    assert payload["recommendations"]


def test_capability_inventory_handles_missing_config(tmp_path: Path) -> None:
    payload = run_cmd("capability-inventory", "--plugin-root", str(ROOT), "--config", str(tmp_path / "missing.toml"))

    assert payload["success"] is True
    assert {"gh", "codex", "claude", "gemini"} <= set(payload["local_tools"])
    assert payload["warnings"]


def test_review_capabilities_reports_mandatory_codex_fallback(tmp_path: Path) -> None:
    (tmp_path / "zagrosi_plan_config.json").write_text(json.dumps({"review_mode": "external_llm"}))

    payload = run_cmd("review-capabilities", "--planning-dir", str(tmp_path))

    assert payload["success"] is True
    assert payload["configured_mode"] == "external_llm"
    assert payload["baseline"]["codex_review"]["available"] is True
    assert payload["baseline"]["codex_review"]["mandatory"] is True
    assert payload["external"]
    assert {item["execution"] for item in payload["external"].values()} <= {"opt_in", "not_configured"}


def test_review_capabilities_warns_on_skip_mode(tmp_path: Path) -> None:
    (tmp_path / "zagrosi_plan_config.json").write_text(json.dumps({"review_mode": "skip"}))

    payload = run_cmd("review-capabilities", "--planning-dir", str(tmp_path))

    assert payload["success"] is True
    assert payload["configured_mode"] == "skip"
    assert any("skip" in item.lower() and "review" in item.lower() for item in payload["recommendations"])


def test_lint_plan_thin_artifacts_recommend_questions_before_padding(tmp_path: Path) -> None:
    planning = write_quality_plan_fixture(tmp_path)
    review_file = planning / "reviews" / "architecture.md"
    review_file.parent.mkdir(exist_ok=True)
    review_file.write_text("# Architecture\n\nREQ-001: too short.\n")

    result = run_raw("lint-plan", "--planning-dir", str(planning), "--depth", "deep", "--strict")

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    thin_findings = [item for item in payload["findings"] if item["code"].endswith("too-thin")]
    assert thin_findings
    recommendation = " ".join(item.get("recommendation", "") for item in thin_findings).lower()
    assert "ask relevant questions" in recommendation or "targeted research" in recommendation or "missing decisions" in recommendation
    assert "add more words" not in recommendation


def test_planning_consistency_reports_missing_late_requirement(tmp_path: Path) -> None:
    (tmp_path / "codex-spec.md").write_text("# Spec\n\nREQ-001: Existing behavior.\nREQ-011: Late interview consistency.\n")
    (tmp_path / "codex-plan.md").write_text("# Plan\n\nREQ-001 only.\n")
    (tmp_path / "codex-plan-tdd.md").write_text("# TDD\n\nREQ-001 only.\n")

    result = run_raw("planning-consistency", "--planning-dir", str(tmp_path), "--strict")

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert any("REQ-011" in item["message"] for item in payload["findings"])
    recommendation = " ".join(item.get("recommendation", "") for item in payload["findings"]).lower()
    assert "review planning docs" in recommendation
    assert "ask the user" in recommendation


def test_new_commands_are_discoverable() -> None:
    commands = run_cmd("commands")
    names = {item["name"] for item in commands["commands"]}
    assert {
        "workflow-options",
        "capability-inventory",
        "review-capabilities",
        "planning-consistency",
        "update-check",
        "self-update",
    } <= names


def test_doctor_and_requirement_extraction(tmp_path: Path) -> None:
    doctor = run_cmd("doctor", "--plugin-root", str(ROOT))
    assert doctor["success"] is True
    assert doctor["marketplace"]["name"] == "zagrosi"
    assert doctor["marketplace"]["plugin"] == "zagrosi-forge@zagrosi"

    marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text())
    assert marketplace["plugins"][0]["name"] == "zagrosi-forge"
    assert marketplace["plugins"][0]["source"] == {"source": "local", "path": "./"}
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

    (project / "zagrosi_project_interview.md").write_text(
        "user_interviewed: true\n\n"
        "# Project Interview\n\n"
        "Q: What guardrail is this validating?\n"
        "A: It validates that skipped or fake interviews are blocked without treating this answer as fake.\n"
    )
    real_project = run_cmd("lint-interview", "--phase", "project", "--planning-dir", str(project), "--strict")
    assert real_project["success"] is True

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
        "--no-verify-codex",
    )
    assert dry_run["success"] is True
    assert dry_run["changed"] is True
    assert "[marketplaces.zagrosi]" in dry_run["config_preview"]
    assert "[marketplaces.zagrosi]" not in config.read_text()
    assert dry_run["cache"]["changed"] is True
    assert not Path(dry_run["cache"]["path"]).exists()

    installed = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
        "--no-verify-codex",
    )
    assert installed["success"] is True
    assert installed["changed"] is True
    assert installed["config_changed"] is True
    assert installed["backup_path"]
    updated = config.read_text()
    assert "[marketplaces.zagrosi]" in updated
    assert f'source = "{ROOT}"' in updated
    assert '[plugins."zagrosi-forge@zagrosi"]' in updated
    assert "enabled = true" in updated
    assert Path(installed["backup_path"]).exists()
    cache_path = Path(installed["cache"]["path"])
    assert cache_path == tmp_path / "plugins" / "cache" / "zagrosi" / "zagrosi-forge" / "0.2.0"
    assert (cache_path / ".codex-plugin" / "plugin.json").exists()
    assert (cache_path / "skills" / "zagrosi-project" / "SKILL.md").exists()

    repeated = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
        "--no-verify-codex",
    )
    assert repeated["success"] is True
    assert repeated["changed"] is False
    assert repeated["cache"]["changed"] is False
    assert repeated["backup_path"] is None


def test_update_check_reports_cache_and_config_status(tmp_path: Path) -> None:
    config = tmp_path / "codex" / "config.toml"

    status = run_cmd("update-check", "--plugin-root", str(ROOT), "--config", str(config))

    assert status["success"] is True
    assert status["operation"] == "update-check"
    assert status["network_policy"] == "local-only"
    assert status["remote_checked"] is False
    assert status["cache"]["current"] is False
    assert status["cache"]["exists"] is False
    assert status["cache"]["changed"] is True
    assert Path(status["cache"]["path"]) == tmp_path / "codex" / "plugins" / "cache" / "zagrosi" / "zagrosi-forge" / "0.2.0"
    assert status["config"]["current"] is False
    assert status["restart_required"] is True
    assert any("self-update" in item for item in status["next_steps"])
    assert not config.exists()


def test_self_update_materializes_cache_and_update_check_passes(tmp_path: Path) -> None:
    config = tmp_path / "codex" / "config.toml"

    updated = run_cmd("self-update", "--plugin-root", str(ROOT), "--config", str(config), "--no-verify-codex")

    assert updated["success"] is True
    assert updated["operation"] == "self-update"
    assert updated["changed"] is True
    cache_path = Path(updated["cache"]["path"])
    assert cache_path == tmp_path / "codex" / "plugins" / "cache" / "zagrosi" / "zagrosi-forge" / "0.2.0"
    assert not (cache_path / "planning").exists()
    assert config.exists()

    status = run_cmd("update-check", "--plugin-root", str(ROOT), "--config", str(config))

    assert status["success"] is True
    assert status["cache"]["current"] is True
    assert status["cache"]["changed"] is False
    assert status["config"]["current"] is True
    assert status["restart_required"] is False
    assert any("already current" in item.lower() for item in status["next_steps"])


def test_install_codex_verifies_prompt_input_with_cached_plugin(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"debug\" ] && [ \"$2\" = \"prompt-input\" ]; then\n"
        "  printf '%s\\n' 'zagrosi-forge:zagrosi-project' 'zagrosi-forge:zagrosi-plan' 'zagrosi-forge:zagrosi-implement'\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    installed = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
        "--verify-codex",
        env=env,
    )

    assert installed["success"] is True
    assert installed["verification"]["status"] == "passed"
    assert installed["verification"]["missing"] == []


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


def test_eval_suite_uses_suite_json_rows_and_snapshot_check(tmp_path: Path) -> None:
    examples = tmp_path / "examples"
    evals = examples / "evals"
    evals.mkdir(parents=True)
    planning = write_quality_plan_fixture(examples / "benchmarks" / "alpha")
    suite = {
        "benchmarks": [
            {"name": "missing-bench", "planning_dir": "../missing"},
            {"name": "alpha-bench", "planning_dir": "../benchmarks/alpha", "depth": "standard"},
        ],
        "snapshots_dir": "golden",
    }
    suite_path = evals / "suite.json"
    suite_path.write_text(json.dumps(suite))

    missing = run_raw("eval-suite", "--examples-dir", str(examples))
    assert missing.returncode != 0
    missing_payload = json.loads(missing.stdout)
    assert missing_payload["success"] is False
    assert "missing-bench" in {item["name"] for item in missing_payload["suite_errors"]}

    suite["benchmarks"] = [{"name": "alpha-bench", "planning_dir": "../benchmarks/alpha", "depth": "standard"}]
    suite_path.write_text(json.dumps(suite))
    report = run_cmd("eval-suite", "--examples-dir", str(examples))
    assert [row["name"] for row in report["rows"]] == ["alpha-bench"]
    assert Path(report["rows"][0]["planning_dir"]) == planning

    updated = run_cmd("eval-suite", "--examples-dir", str(examples), "--update-snapshots")
    assert updated["snapshot_summary"]["updated"] == ["alpha-bench"]

    checked = run_cmd("eval-suite", "--examples-dir", str(examples), "--check-snapshots")
    assert checked["snapshot_summary"]["matched"] == ["alpha-bench"]

    snapshot = evals / "golden" / "alpha-bench-forge-score.json"
    snapshot.write_text(json.dumps({"planning_dir_name": "wrong", "forge_score": 1, "grade": "D", "components": {}}))
    drifted = run_raw("eval-suite", "--examples-dir", str(examples), "--check-snapshots")
    assert drifted.returncode != 0
    drift_payload = json.loads(drifted.stdout)
    assert "alpha-bench" in {item["name"] for item in drift_payload["snapshot_summary"]["drifted"]}


def test_eval_suite_keeps_glob_fallback_without_suite_json(tmp_path: Path) -> None:
    examples = tmp_path / "examples"
    planning = write_quality_plan_fixture(examples / "gallery" / "alpha")

    report = run_cmd("eval-suite", "--examples-dir", str(examples))

    assert report["success"] is True
    assert [Path(row["planning_dir"]) for row in report["rows"]] == [planning]
    assert report["discovery_mode"] == "glob"


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
    assert any("eval-suite" in row["command"] and "--check-snapshots" in row["command"] for row in release["results"])


def test_release_check_skips_example_gates_when_examples_are_absent(tmp_path: Path) -> None:
    package = tmp_path / "bundle"
    for relative in [
        ".agents",
        ".codex-plugin",
        "assets",
        "scripts",
        "skills",
    ]:
        shutil.copytree(ROOT / relative, package / relative)
    for filename in [".codexignore", "LICENSE", "NOTICE.md", "README.md", "pyproject.toml"]:
        shutil.copy2(ROOT / filename, package / filename)

    release = run_cmd("release-check", "--plugin-root", str(package))

    assert release["success"] is True
    command_text = "\n".join(row["command"] for row in release["results"])
    assert "examples/evals/suite.json" not in command_text
    assert "lint-project-manifest" not in command_text
    assert "eval-suite" not in command_text
    assert ".agents/plugins/marketplace.json" in command_text


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


def test_readme_documents_operator_quality_commands() -> None:
    readme = (ROOT / "README.md").read_text().lower()

    for phrase in (
        "commands --pretty",
        "commands --phase plan",
        "plan-aware status",
        "plan_artifacts",
        "expanded codebase evidence",
        "source files",
        "eval-suite --examples-dir examples --check-snapshots",
        "update-snapshots",
        "release-check --plugin-root .",
        "update-check",
        "self-update",
        "does not poll git remotes automatically",
    ):
        assert phrase in readme


def test_validate_workflow_mentions_snapshot_eval() -> None:
    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text()
    assert "eval-suite --examples-dir examples --check-snapshots" in workflow


def test_skill_files_are_codex_native() -> None:
    banned = ["TaskList", "TaskUpdate", "AskUserQuestion", "CLAUDE_CODE_TASK_LIST_ID"]
    for skill in (ROOT / "skills").glob("*/SKILL.md"):
        content = skill.read_text()
        assert "[TODO:" not in content
        for token in banned:
            assert token not in content, f"{token} leaked into {skill}"

    plan_skill = (ROOT / "skills" / "zagrosi-plan" / "SKILL.md").read_text().lower()
    assert "source files" in plan_skill
    assert "plan artifact" in plan_skill
    assert "lint-plan-artifacts" in plan_skill
    assert "capability-inventory" in plan_skill
    assert "review-capabilities" in plan_skill
    assert "planning-consistency" in plan_skill
    assert "ask relevant questions" in plan_skill
    assert "targeted research" in plan_skill
    assert "(recommended)" in plan_skill

    implement_skill = (ROOT / "skills" / "zagrosi-implement" / "SKILL.md").read_text().lower()
    assert "consolidated commit" in implement_skill
    assert "section commits" in implement_skill
    assert "lint-plan-artifacts" in implement_skill
    assert "--review-artifact" in implement_skill
    assert "--verification" in implement_skill
    assert "review decisions" in implement_skill
    assert "refresh" in implement_skill
    assert "ci watch" in implement_skill

    project_skill = (ROOT / "skills" / "zagrosi-project" / "SKILL.md").read_text().lower()
    assert "workflow-options" in project_skill
    assert "capability-inventory" in project_skill
    assert "structured" in project_skill
    assert "chat" in project_skill
    assert "(recommended)" in project_skill
    assert "consistency review" in project_skill
