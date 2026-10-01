"""Argument vectors for the next mutable-workflow action; never execute them."""

from __future__ import annotations

from pathlib import Path
import sys

from . import artifacts, mutable_inputs, sections, state, storage


def command(name: str, *args: str) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "zagrosi_skills.py"), name, *args]


def implementation_profile(planning_dir: Path, profile: str | None = None) -> str:
    if profile is not None:
        return profile
    return state.load_implementation_config(planning_dir).get("profile", "solo")


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
        from .verification import receipt_path

        commands["record"] = command(
            "implement-record-section", "--sections-dir", str(planning_dir / "sections"), "--section", section,
            "--target-dir", target, "--depth", depth, "--profile", profile, "--review-status", "<review-status>",
            "--verification-receipt", str(receipt_path(planning_dir, section)),
            "--file", "<changed-file>", "--flight", "strict" if pending else "off",
        )
        payload["record_inputs"] = "Capture the authorized targeted checks with verify_section, then replace record placeholders with actual pass/fixed review and changed files. The receipt must still match current inputs."
        commands["verify_section"] = command("implement-verify", "--planning-dir", str(planning_dir),
                                             "--target-dir", target, "--section", section, "--", "<command>", "<argument>")
        payload["record_options"] = {"--test-file": "Repeat for actual changed test files.",
                                     "--commit": "Existing commit only; follow the user's Git authorization.",
                                     "manual_evidence": "Replace --verification-receipt with --verification-source attestation|inspection --verification-outcome passed --verification TEXT. This labels manual evidence, not captured execution."}
    else:
        from .verification import integration_report

        integration = integration_report(planning_dir, Path(target))
        payload["integration_verification"] = integration
        payload["test_command"] = sections.check_section_progress(planning_dir).get("project_config", {}).get("test_command")
        if integration["success"]:
            payload["next_action"] = "run postflight with the current passing integration receipt, then summarize"
        else:
            commands["verify_integration"] = command("implement-verify", "--planning-dir", str(planning_dir),
                                                     "--target-dir", target, "--", "<command>", "<argument>")
            payload["next_action"] = "capture the authorized full test command once with implement-verify, then postflight; an unchanged receipt is reused"
    return payload
