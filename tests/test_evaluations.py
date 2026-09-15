from __future__ import annotations

import json
from pathlib import Path

from forge_test_helpers import (
    ROOT,
    run_cmd,
    run_raw,
    run_text,
    write_quality_plan_fixture,
)


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


def test_eval_suite_fails_when_fixtures_are_absent_or_empty(tmp_path: Path) -> None:
    missing = run_raw("eval-suite", "--examples-dir", str(tmp_path / "missing"), "--check-snapshots")
    assert missing.returncode != 0
    missing_payload = json.loads(missing.stdout)
    assert missing_payload["success"] is False
    assert missing_payload["discovery_mode"] == "missing"
    assert missing_payload["suite_errors"][0]["error"] == "examples_dir does not exist"

    empty = tmp_path / "empty-examples"
    empty.mkdir()
    empty_result = run_raw("eval-suite", "--examples-dir", str(empty), "--check-snapshots")
    assert empty_result.returncode != 0
    empty_payload = json.loads(empty_result.stdout)
    assert empty_payload["success"] is False
    assert empty_payload["discovery_mode"] == "glob"
    assert empty_payload["suite_errors"][0]["error"] == "No benchmark planning fixtures found"

    evals = empty / "evals"
    evals.mkdir()
    (evals / "suite.json").write_text(json.dumps({"benchmarks": [], "snapshots_dir": "golden"}))
    suite_empty = run_raw("eval-suite", "--examples-dir", str(empty), "--check-snapshots")
    assert suite_empty.returncode != 0
    suite_empty_payload = json.loads(suite_empty.stdout)
    assert suite_empty_payload["discovery_mode"] == "suite"
    assert suite_empty_payload["suite_errors"][0]["error"] == "benchmarks list is empty"


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
    preflight_gates = {gate["name"] for gate in pre["gates"]}
    assert preflight_gates >= {"spec-file", "doctor", "status"}
    assert "codebase-evidence" in preflight_gates

    evidence_pre = run_cmd(
        "preflight",
        "--phase",
        "plan",
        "--file",
        str(planning / "codex-spec.md"),
        "--target-dir",
        str(ROOT),
        "--write-evidence",
        "--flight",
        "advisory",
    )
    evidence_gate = next(gate for gate in evidence_pre["gates"] if gate["name"] == "codebase-evidence")
    assert Path(evidence_gate["payload"]["output"]).exists()

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
    impl_gate_names = {gate["name"] for gate in impl_pre["gates"]}
    assert {"lint-plan-artifacts", "lint-sections", "traceability", "lint-implementation-readiness"} <= impl_gate_names
    assert {"doctor", "next-section", "suggest-section-splits", "status"}.isdisjoint(impl_gate_names)

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

    release = run_cmd("release-check", "--plugin-root", str(ROOT), "--verbose")
    assert release["success"] is True
    assert "doctor" not in release["checks"]
    assert "install-dry-run" in release["checks"]
    assert any(".agents/plugins/marketplace.json" in row["command"] for row in release["results"])
    assert any("eval-suite" in row["command"] and "--check-snapshots" in row["command"] for row in release["results"])
