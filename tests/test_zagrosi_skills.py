from __future__ import annotations

import json
import os
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


def test_skills_describe_state_first() -> None:
    reference = (ROOT / "skills/zagrosi-implement/references/state-first.md").read_text()
    for command in ("plan-setup", "--state-first", "--stage done", "--stage baseline", "implement-verify", "status --path"):
        assert command in reference
    for skill, link in (("zagrosi-plan", "../zagrosi-implement/references/state-first.md"),
                        ("zagrosi-implement", "references/state-first.md"),
                        ("zagrosi-forge", "../zagrosi-implement/references/state-first.md")):
        content = (ROOT / "skills" / skill / "SKILL.md").read_text()
        assert f"]({link})" in content and "state-first" in content, skill
        assert len(content.split()) < 1000, skill


# Pre-approved helper commands only read Forge state; commands that write caller-chosen
# paths, run checks, contact models or push keep the host's prompt.
SAFE_HELPER_COMMANDS = ["team status", "status", "next-section"]
HELPER_LAUNCHERS = ["python3", "python", "py -3"]


def skill_parts(path: Path) -> tuple[str, str]:
    _, frontmatter, body = path.read_text().split("---", 2)
    return frontmatter, body


def test_every_skill_resolves_plugin_root() -> None:
    for skill in sorted((ROOT / "skills").glob("*/SKILL.md")):
        _, body = skill_parts(skill)
        assert "scripts/zagrosi_skills.py" in body and "${CLAUDE_PLUGIN_ROOT}" in body, skill
    _, router = skill_parts(ROOT / "skills/zagrosi-forge/SKILL.md")
    assert "zagrosi-forge:zagrosi-plan" in router


REVIEWER = ROOT / "agents" / "forge-reviewer.md"


def test_reviewer_agent_is_read_only_packaged_and_requested() -> None:
    frontmatter, body = skill_parts(REVIEWER)
    fields = dict(re.findall(r"^(\w+): (.+)$", frontmatter, re.MULTILINE))
    # Plugin agents ignore permissionMode, hooks and mcpServers, and memory adds write tools, so
    # read-only access rests on exactly these keys, each a single-line plain YAML scalar.
    assert sorted(fields) == ["description", "name", "tools"] and len(frontmatter.strip().splitlines()) == 3
    assert not any(": " in value or " #" in value or value[0] in "-?:,[]{}#&*!|>'\"%@`" for value in fields.values())
    assert fields["name"] == REVIEWER.stem
    assert {tool.strip() for tool in fields["tools"].split(",")} == {"Read", "Grep", "Glob"}
    assert "path:line" in body and "Distinguishing check" in body
    assert "agents/forge-reviewer.md" in json.loads((ROOT / ".codex-plugin/package-files.json").read_text())
    for skill in ("zagrosi-implement", "zagrosi-cleanup"):
        assert "providers.md#same-host-review" in (ROOT / "skills" / skill / "SKILL.md").read_text(), skill
    providers = (ROOT / "skills/zagrosi-forge/references/providers.md").read_text()
    assert "## Same-host review" in providers and "{plugin_root}/agents/forge-reviewer.md" in providers


PROVIDERS = ROOT / "skills/zagrosi-forge/references/providers.md"


def delegation_text(path: str) -> tuple[str, str]:
    """Whitespace-normalized skill text and its expected link to the delegated-agents rule."""
    source = ROOT / "skills" / path
    target = Path(os.path.relpath(PROVIDERS, source.parent)).as_posix()
    return " ".join(source.read_text().split()), f"[delegated agents]({target}#delegated-agents)"


def test_claude_code_runs_delegated_agents_in_the_foreground() -> None:
    # Headless `claude -p` stops background agents still running 10 minutes after the last turn.
    _, heading, rest = PROVIDERS.read_text().partition("\n## Delegated agents\n")
    assert heading
    rule = " ".join(rest.split("\n## ", 1)[0].split())
    for phrase in ("In Claude Code", "in the foreground when `Agent` offers `run_in_background`",
                   "pass `run_in_background: false`", "use the agent's result in the same turn",
                   "Headless `claude -p` offers it", "10 minutes after the last turn"):
        assert phrase in rule, phrase
    for path in ("zagrosi-implement/SKILL.md", "zagrosi-cleanup/SKILL.md", "zagrosi-plan/references/research.md",
                 "zagrosi-implement/references/state-first.md"):
        text, link = delegation_text(path)
        assert f"In Claude Code, run {link} in the foreground when offered." in text, path
    text, link = delegation_text("zagrosi-implement/references/compatibility.md")
    assert (f"in Claude Code, pass `run_in_background: false` when `Agent` offers it ({link}) "
            "and use its result in the same turn.") in text
    # Codex trials never delegated; the writer-derived path stays as it was.
    assert "If delegation is unavailable, derive the checks yourself and say `writer`." in text


def test_skills_preapprove_only_safe_helper_commands() -> None:
    expected = [f'Bash({launcher} "${{CLAUDE_PLUGIN_ROOT}}/scripts/zagrosi_skills.py" {command} *)'
                for launcher in HELPER_LAUNCHERS for command in SAFE_HELPER_COMMANDS]
    for skill in sorted((ROOT / "skills").glob("*/SKILL.md")):
        frontmatter, body = skill_parts(skill)
        assert "allowed-tools:" in frontmatter, skill
        rules = re.findall(r"^  - (.+)$", frontmatter.split("allowed-tools:", 1)[1], re.MULTILINE)
        assert rules == expected, skill
        assert 'python3 "{plugin_root}/scripts/zagrosi_skills.py"' in body, skill
