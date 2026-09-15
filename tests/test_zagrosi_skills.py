from __future__ import annotations

import json
from pathlib import Path

from forge_test_helpers import (
    ROOT,
    run_cmd,
)


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


def test_readme_documents_the_concise_lean_contract() -> None:
    readme = (ROOT / "README.md").read_text().lower()

    for phrase in (
        "lean mode is default",
        "no minimum prose quotas",
        "one setup",
        "one final gate",
        "minimum sufficient artifacts",
        "project-manifest.md",
        "single-section plan",
        "one canonical section",
        "compact-plan format",
        "scripts/forge/",
        "compiles those exact bytes",
        "update_runtime_manifest.py --check",
        "targeted regression",
        "machine-readable section records",
        "`fast` remains a compatibility alias for `lean`",
        "commands --pretty",
        "release-check --plugin-root .",
        "update-check",
        "self-update",
    ):
        assert phrase in readme
    assert len(readme.split()) < 900


def test_validate_workflow_mentions_snapshot_eval() -> None:
    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text()
    assert "eval-suite --examples-dir examples --check-snapshots" in workflow


def test_skill_files_are_codex_native() -> None:
    banned = ["TaskList", "TaskUpdate", "AskUserQuestion", "CLAUDE_CODE_TASK_LIST_ID"]
    for skill in (ROOT / "skills").glob("*/SKILL.md"):
        content = skill.read_text()
        assert "[TODO:" not in content
        assert len(content.split()) < 1000
        for token in banned:
            assert token not in content, f"{token} leaked into {skill}"

    plan_skill = " ".join((ROOT / "skills" / "zagrosi-plan" / "SKILL.md").read_text().lower().split())
    for phrase in (
        "smallest implementation-ready plan",
        "depth is `lean`",
        "source spec, unchanged",
        "the canonical plan",
        "tests before implementation",
        "at most 300 words",
        "one bundled postflight",
        "adversarially review",
        "do not implement unless asked",
    ):
        assert phrase in plan_skill

    implement_skill = " ".join((ROOT / "skills" / "zagrosi-implement" / "SKILL.md").read_text().lower().split())
    for phrase in (
        "least process",
        "detached-frozen.md",
        "do not load that large reference",
        "do not run the full suite per section",
        "--review-status pass",
        "--verification",
        "one final postflight",
        "never bypass hooks",
        "do not push",
    ):
        assert phrase in implement_skill

    project_skill = " ".join((ROOT / "skills" / "zagrosi-project" / "SKILL.md").read_text().lower().split())
    for phrase in (
        "fewest independently plannable units",
        "depth is `lean`",
        "compact `project-manifest.md`",
        "material boundary",
        "ask no routine approval question",
        "one bundled postflight",
        "do not start zagrosi plan unless asked",
    ):
        assert phrase in project_skill

    assert "--depth lean --strict" in project_skill
    review_guidance = (ROOT / "skills" / "zagrosi-plan" / "references" / "review.md").read_text()
    assert "`Verdict: pass.`" in review_guidance
    assert "`No blockers` is" not in review_guidance
