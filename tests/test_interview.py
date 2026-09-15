from __future__ import annotations

import json
from pathlib import Path

from forge_test_helpers import (
    run_cmd,
    run_raw,
    write_compact_project_fixture,
    write_quality_plan_fixture,
)


def test_interview_is_optional_but_existing_artifacts_must_be_valid(tmp_path: Path) -> None:
    project = write_compact_project_fixture(tmp_path / "project")

    missing = run_cmd("lint-project-manifest", "--planning-dir", str(project), "--strict")
    assert missing["success"] is True
    assert missing["interview"] == {"mode": "optional"}
    assert not any(item["code"] == "missing-interview" for item in missing["findings"])

    postflight = run_cmd("postflight", "--phase", "project", "--planning-dir", str(project), "--flight", "strict")
    assert "lint-interview" not in {gate["name"] for gate in postflight["gates"]}

    (project / "zagrosi_project_interview.md").write_text(
        "user_interviewed: true\n\nQ: TBD\nA: TBD\n"
    )
    malformed = run_raw("lint-project-manifest", "--planning-dir", str(project), "--strict")
    assert malformed.returncode != 0
    assert "placeholder-interview" in {item["code"] for item in json.loads(malformed.stdout)["findings"]}

    malformed_postflight = run_raw(
        "postflight",
        "--phase",
        "project",
        "--planning-dir",
        str(project),
        "--flight",
        "strict",
    )
    assert malformed_postflight.returncode != 0
    assert "lint-interview" in json.loads(malformed_postflight.stdout)["blocking_gates"]

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
