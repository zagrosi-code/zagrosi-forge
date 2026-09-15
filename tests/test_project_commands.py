from __future__ import annotations

import json
from pathlib import Path

from forge_test_helpers import (
    ROOT,
    run_cmd,
    run_raw,
    run_text,
)


def test_project_setup_and_create_dirs(tmp_path: Path) -> None:
    req = tmp_path / "requirements.md"
    req.write_text("# Build a SaaS app\n\nAuth, billing, dashboard.\n")

    setup = run_cmd("project-setup", "--file", str(req))
    assert setup["success"] is True
    assert setup["depth_mode"] == "lean"
    assert setup["resume_step"] == 2
    assert setup["resume_label"] == "split_analysis"
    assert not (tmp_path / "zagrosi_project_interview.md").exists()
    assert json.loads((tmp_path / ".zagrosi-project" / "session.json").read_text())["contract_version"] == "compact-v1"
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
    assert setup["depth_mode"] == "lean"
    assert setup["resume_step"] == 2
    assert setup["resume_label"] == "split_analysis"
    assert not (tmp_path / "zagrosi_project_interview.md").exists()
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
    assert setup["depth_mode"] == "lean"
    assert setup["resume_step"] == 11
    assert setup["resume_label"] == "write_plan"
    assert setup["preflight"]["phase"] == "plan"
    assert (tmp_path / "zagrosi_plan_config.json").exists()
    assert not (tmp_path / "codex-research.md").exists()
    assert not (tmp_path / "codex-interview.md").exists()
    assert not (tmp_path / "decisions.md").exists()
    assert not (tmp_path / "risk-register.md").exists()

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
    assert status["next_action"] == "write the canonical implementation plan"
    assert status["plan_artifacts"]["research"] is None
    assert status["plan_artifacts"]["interview"] is None

    (tmp_path / "codex-plan.md").write_text("")
    status = run_cmd("status", "--path", str(tmp_path))
    assert status["next_action"] == "write the canonical implementation plan"

    (tmp_path / "codex-plan.md").write_text("# Plan\n\nREQ-001 implementation plan.\n")
    status = run_cmd("status", "--path", str(tmp_path))
    assert status["next_action"] == "review plan and record the verdict"

    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / "codex.md").write_text("# Review\n\nPASS: no material findings.\n")
    status = run_cmd("status", "--path", str(tmp_path))
    assert status["next_action"] == "create concise sections/index.md"

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
    assert status["next_action"] == "write missing concise section files"

    (sections / "section-01-status.md").write_text(
        "# section-01-status\n\nREQ-001 tests first; implement, verify, accept, and rollback.\n"
    )
    status = run_cmd("status", "--path", str(tmp_path))
    assert status["next_action"] == "run zagrosi-implement"


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
        assert "aliases" not in entry
        assert "examples" not in entry
    assert len(json.dumps(catalog)) < 4000

    verbose = run_cmd("commands", "--verbose")
    verbose_by_name = {entry["name"]: entry for entry in verbose["commands"]}
    for name in required:
        assert isinstance(verbose_by_name[name]["aliases"], list)
        assert verbose_by_name[name]["examples"]

    plan_catalog = run_cmd("commands", "--phase", "plan")
    assert plan_catalog["commands"]
    assert {entry["phase"] for entry in plan_catalog["commands"]} <= {"plan", "all", "quality", "utility"}

    pretty = run_text("commands", "--pretty")
    assert "PLAN" in pretty.upper()
    assert "status" in pretty
    assert "codebase-evidence" in pretty


def test_command_catalog_matches_parser_aliases() -> None:
    catalog = run_cmd("commands", "--verbose")
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
    assert "Source Files" in written
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
