"""Stakeholder journeys bind individual observable actions to captured tests."""
import json
import sys

import pytest

from test_verification_receipts import SECTION, invoke, record_args, verify_args, workspace


def case(name, **updates):
    return {
        "id": name, "requirement": "REQ-001", "actor": "finance approver",
        "starting_state": "An editable invoice on a fresh database with the independence rule off",
        "action": name, "expected": "The eligible approver succeeds",
        "check": "test_" + name,
        "argv": [sys.executable, "-c", "import sys; print(sys.argv[1])", "test_" + name],
        **updates,
    }


def declare(workspace, cases):
    path = workspace[1] / "sections" / f"{SECTION}.md"
    with path.open("a", encoding="utf-8") as out:
        out.write("\n## Acceptance journeys\n\n```json\n" + json.dumps(cases) + "\n```\n")


def capture(workspace, capsys, row, *argv):
    return invoke(workspace, capsys, *verify_args(workspace, "--section", SECTION,
                  "--journey", row["id"], "--", *(argv or row["argv"])))


def test_return_and_reopen_require_independent_current_cases(workspace, capsys):
    rows = [case("return"), case("reopen")]
    declare(workspace, rows)
    code, trace = invoke(workspace, capsys, "traceability", "--planning-dir", str(workspace[1]), "--strict")
    assert code == 0
    assert [r["id"] for r in trace["coverage"]["REQ-001"]["journeys"]] == ["return", "reopen"]
    code, wrong = capture(workspace, capsys, rows[0], *rows[1]["argv"])
    assert code == 1 and "command" in wrong["error"].lower()
    code, first = capture(workspace, capsys, rows[0])
    assert code == 0, first
    args = record_args(workspace, "--verification-receipt", first["receipt_path"])
    code, gap = invoke(workspace, capsys, *args)
    assert code == 1 and "reopen" in json.dumps(gap)
    code, second = capture(workspace, capsys, rows[1])
    assert code == 0, second
    code, recorded = invoke(workspace, capsys, *args)
    assert code == 0, recorded
    assert set(recorded["record"]["journeys"]) == {"return", "reopen"}
    section_record = recorded["record"].copy()
    section_record["journeys"] = {"return": section_record["journeys"]["return"]}
    assert any(f.code == "invalid-journey-evidence" for f in workspace[0].state.completion_evidence_findings(
        workspace[1], SECTION, section_record, target_dir=workspace[2]))
    (workspace[2] / "source.py").write_text("value = 2\n")
    code, status = invoke(workspace, capsys, "journey-status", "--planning-dir", str(workspace[1]),
                          "--target-dir", str(workspace[2]), "--section", SECTION)
    assert code == 0 and {r["status"] for r in status["journeys"]} == {"stale"}


@pytest.mark.parametrize("rows", [
    [case("return", actor="")], [case("return", action="TODO")],
    [case("../return")], [case("return", requirement="REQ-OTHER")],
    [case("return"), case("return")], [case("return"), case("reopen", argv=case("return")["argv"])],
    [case("return", check="irrelevant")],
])
def test_invalid_journeys_block_plan_admission(workspace, capsys, rows):
    declare(workspace, rows)
    code, payload = invoke(workspace, capsys, "postflight", "--phase", "plan",
                           "--planning-dir", str(workspace[1]), "--strict")
    assert code == 1 and "traceability" in payload["blocking_gates"], payload


def test_failed_retry_replaces_a_previously_passing_journey(workspace, capsys):
    row = case("return", argv=[sys.executable, "-c",
               "from pathlib import Path; raise SystemExit(Path('../external-failure').exists())", "test_return"])
    declare(workspace, [row])
    assert capture(workspace, capsys, row)[0] == 0
    (workspace[2].parent / "external-failure").touch()
    code, failed = capture(workspace, capsys, row)
    assert code == 1 and failed["outcome"] == "failed"
    code, status = invoke(workspace, capsys, "journey-status", "--planning-dir", str(workspace[1]),
                          "--target-dir", str(workspace[2]), "--section", SECTION)
    assert code == 0 and status["journeys"][0]["status"] == "failed"
