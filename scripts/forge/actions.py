"""Argument vectors for the next mutable-workflow action; never execute them."""

from __future__ import annotations

from pathlib import Path
import sys

from . import artifacts, mutable_inputs, sections, storage


def command(name: str, *args: str) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "zagrosi_skills.py"), name, *args]


def implementation_profile(planning_dir: Path, profile: str | None = None) -> str:
    if profile is not None:
        return profile
    config = artifacts.artifact(planning_dir / "implementation", ["zagrosi_implement_config.json", "deep_implement_config.json"])
    return storage.load_json(config).get("profile", "solo") if config else "solo"


def plan_commands(planning_dir: Path, depth: str, target_dir: Path, *, detached: bool = False) -> dict:
    commands = {
        "verify_plan": command("postflight", "--phase", "plan", "--planning-dir", str(planning_dir),
                               "--target-dir", str(target_dir), "--depth", depth, "--strict"),
        "implement_after_pass": command("implement-setup", "--sections-dir", str(planning_dir / "sections"),
                                        "--target-dir", str(target_dir), "--depth", depth),
    }
    if detached:
        commands["implement_after_pass"].extend([
            "--implementation-root", "<external-state-root>", "--admission-pinner", "<admission-pinner>",
            "--expected-admission-pinner-sha256", "<admission-sha256>",
            "--expected-implement-tool-sha256", "<tool-sha256>",
            "--expected-implement-skill-sha256", "<skill-sha256>",
            "--expected-implement-test-sha256", "<test-sha256>",
        ])
    return commands


def implementation_commands(planning_dir: Path, section: str | None = None, *, target_dir: Path | None = None,
                            pending: bool = False, profile: str | None = None) -> dict:
    target = str(mutable_inputs.target_directory(planning_dir, target_dir))
    depth = artifacts.planning_depth(planning_dir)
    profile = implementation_profile(planning_dir, profile)
    commands = {"postflight": command("postflight", "--phase", "implement", "--planning-dir", str(planning_dir),
                                       "--sections-dir", str(planning_dir / "sections"), "--target-dir", target,
                                       "--depth", depth, "--profile", profile, "--strict")}
    payload = {"commands": commands}
    if section:
        commands["record"] = command(
            "implement-record-section", "--sections-dir", str(planning_dir / "sections"), "--section", section,
            "--target-dir", target, "--depth", depth, "--profile", profile, "--review-status", "<review-status>",
            "--verification", "<verification>", "--file", "<changed-file>", "--flight", "strict" if pending else "off",
        )
        payload["record_inputs"] = "Replace placeholders with actual pass/fixed review, passed command or inspection, and changed file. Repeat --verification/--file as needed."
        payload["record_options"] = {"--test-file": "Repeat for actual changed test files.",
                                     "--commit": "Existing commit only; follow the user's Git authorization."}
    else:
        payload["test_command"] = sections.check_section_progress(planning_dir).get("project_config", {}).get("test_command")
        payload["next_action"] = "run the full test command once, then postflight without --run-tests"
    return payload
