from __future__ import annotations

import json
import re
from pathlib import Path

from forge_test_helpers import (
    run_cmd,
    run_raw,
    write_quality_plan_fixture,
)


def test_quality_gates_traceability_and_status(tmp_path: Path) -> None:
    planning = write_quality_plan_fixture(tmp_path)

    export_path = tmp_path / "findings.jsonl"
    plan = run_cmd(
        "lint-plan",
        "--planning-dir",
        str(planning),
        "--depth",
        "standard",
        "--profile",
        "enterprise",
        "--strict",
        "--export",
        str(export_path),
    )
    assert plan["success"] is True
    assert plan["score"] == 100
    assert export_path.exists()

    sections = run_cmd("lint-sections", "--planning-dir", str(planning), "--depth", "standard")
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

    reviews = planning / "reviews"
    reviews.mkdir()
    (reviews / "plan.md").write_text(
        "Verdict: pass\nReviewed: auth requirements, ownership, regression cases and failure behavior.\n",
        encoding="utf-8",
    )
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

    score = run_cmd("forge-score", "--planning-dir", str(planning), "--depth", "standard")
    assert score["forge_score"] >= 90
    assert score["components"]["traceability"] == 100

    first_history = run_cmd("forge-score", "--planning-dir", str(planning), "--depth", "standard", "--write-history")
    second_history = run_cmd("forge-score", "--planning-dir", str(planning), "--depth", "standard", "--write-history")
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


def test_assumption_ledger_uses_canonical_typed_line_tokens_and_replays_deterministically(
    tmp_path: Path,
) -> None:
    planning = tmp_path / "planning"
    planning.mkdir()
    source_rows = {
        1: "Assumption at the first boundary.",
        26: "Assumption at the final single-letter boundary.",
        27: "Assumption at the first double-letter boundary.",
        52: "Assumption at the final a-prefix boundary.",
        53: "Assumption at the first b-prefix boundary.",
        211: "Assumption exactly at the collision source line.",
    }
    source_lines = [""] * 211
    for line_no, text in source_rows.items():
        source_lines[line_no - 1] = text
    (planning / "codex-spec.md").write_text("\n".join(source_lines) + "\n", encoding="utf-8")

    first = run_cmd("assumption-ledger", "--planning-dir", str(planning), "--write")
    ledger_path = planning / "assumption-ledger.md"
    first_bytes = ledger_path.read_bytes()
    second = run_cmd("assumption-ledger", "--planning-dir", str(planning), "--write")
    second_bytes = ledger_path.read_bytes()

    assert first["rows"] == [
        {
            "type": "assumption",
            "artifact": "spec",
            "line": "La",
            "text": "Assumption at the first boundary.",
        },
        {
            "type": "assumption",
            "artifact": "spec",
            "line": "Lz",
            "text": "Assumption at the final single-letter boundary.",
        },
        {
            "type": "assumption",
            "artifact": "spec",
            "line": "Laa",
            "text": "Assumption at the first double-letter boundary.",
        },
        {
            "type": "assumption",
            "artifact": "spec",
            "line": "Laz",
            "text": "Assumption at the final a-prefix boundary.",
        },
        {
            "type": "assumption",
            "artifact": "spec",
            "line": "Lba",
            "text": "Assumption at the first b-prefix boundary.",
        },
        {
            "type": "assumption",
            "artifact": "spec",
            "line": "Lhc",
            "text": "Assumption exactly at the collision source line.",
        },
    ]
    assert all(re.fullmatch(r"L[a-z]+", row["line"]) for row in first["rows"])
    assert second == first
    assert second_bytes == first_bytes
    ledger = first_bytes.decode("utf-8")
    rows_json = json.dumps(first["rows"], sort_keys=True)
    assert "| assumption | spec | Lhc | Assumption exactly at the collision source line. |" in ledger
    assert "| 211 |" not in ledger
    assert "L211" not in ledger
    assert "Lxd3" not in ledger
    assert "L211" not in rows_json
    assert "Lxd3" not in rows_json
    assert re.search(r"(?<![0-9A-Za-z])211(?![0-9A-Za-z])", ledger) is None
    assert re.search(r"(?<![0-9A-Za-z])211(?![0-9A-Za-z])", rows_json) is None


def test_workflow_options_defaults_to_lean_without_routine_interview() -> None:
    payload = run_cmd(
        "workflow-options",
        "--brief",
        "maybe use external review or auto PR, whatever you recommend",
    )

    assert payload["success"] is True
    assert payload["depth"]["recommended"] == "lean"
    assert payload["depth"]["requires_confirmation"] is False
    assert payload["interview"]["required"] is False
    assert payload["interview"]["option_sets"] == []
    assert payload["interview"]["use_structured_input_when_available"] is True
    assert payload["interview"]["fallback"] == "chat"
    assert payload["autonomy"]["auto_commit"] is False
    assert payload["autonomy"]["auto_pr"] is False
    assert payload["autonomy"]["ci_watch"] is False
    assert payload["autonomy"]["fix_watch_loop"] is False


def test_workflow_options_respects_explicit_depth() -> None:
    payload = run_cmd("workflow-options", "--brief", "small docs fix", "--depth", "deep")

    assert payload["success"] is True
    assert payload["depth"]["selected"] == "deep"
    assert payload["depth"]["recommended"] == "deep"
    assert payload["depth"]["requires_confirmation"] is False


def test_workflow_options_includes_recommended_interview_choices_with_rationale() -> None:
    payload = run_cmd(
        "workflow-options",
        "--brief",
        "TBD architecture tradeoff: external review, web research, or automatic PR",
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
    assert Path(prompts["shared_prompt"]).exists()
    generated_words = sum(len(Path(path).read_text().split()) for path in [prompts["shared_prompt"], *prompts["prompt_files"]])
    assert generated_words < 250

    stubs_dir = tmp_path / "new"
    stubs = run_cmd("write-governance-stubs", "--planning-dir", str(stubs_dir), "--depth", "deep")
    assert stubs["success"] is True
    assert len(stubs["created"]) == 4
