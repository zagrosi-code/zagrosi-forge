from __future__ import annotations

import json
import re
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
    readme = (ROOT / "README.md").read_text()

    for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", readme):
        if "://" not in target and not target.startswith("#"):
            assert (ROOT / target.split("#")[0]).exists(), target
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

    # Native discovery, reference resolution, phase routing and completion are
    # exercised by host/workflow tests; prose wording is not an executable API.
