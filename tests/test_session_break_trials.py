"""Two-session break trials through a fake Codex writer; no model calls."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import sys

import pytest

from test_trial_realism import complete_import_preview

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("session_break_trials", ROOT / "tools/session_break_trials.py")
breaks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(breaks)
checks = sys.modules["session_break_checks"]
CLARIFICATION = "Tax rounds down per line; never round again over the combined subtotal."

FAKE_CODEX = r'''
import json, os, sys, time
from pathlib import Path

CHARACTERIZE = """import unittest

from orders import import_orders


class CharacterizeTests(unittest.TestCase):
    def test_total(self):
        rows = import_orders("order_id,sku,quantity\\nO-1,PEN,2\\n", {"PEN": 100})
        self.assertEqual(rows[0]["total"], 240)
        self.assertEqual(rows[0]["tax"], 40)
"""
PREVIEW = """

def preview_import(text, catalog):
    rows = import_orders(text, catalog)
    quantities = {}
    for row in rows:
        quantities[row["sku"]] = quantities.get(row["sku"], 0) + row["quantity"]
    return {"order_count": len({row["order_id"] for row in rows}),
            "item_count": sum(row["quantity"] for row in rows),
            **{key: sum(row[key] for row in rows) for key in ("subtotal", "tax", "total")},
            "sku_quantities": dict(sorted(quantities.items()))}
"""

args = sys.argv[1:]
if args == ["--version"]:
    sys.exit(print("codex-cli 0.154.0"))
prompt = sys.stdin.read()
session = 1 if "complete sections 1 and 2 only" in prompt else 2
mode = os.environ.get("FAKE_WRITER_MODE", "")
try:
    Path("../session-1.md").read_text()
    leaked = True
except OSError:
    leaked = False
with open(os.environ["FAKE_WRITER_LOG"], "a") as log:
    log.write(json.dumps({"session": session, "prompt": prompt, "args": args, "leaked": leaked}) + "\n")


def emit(**event):
    print(json.dumps(event), flush=True)


def command(text):
    for kind in ("item.started", "item.completed"):
        emit(type=kind, item={"id": text, "type": "command_execution", "command": text, "exit_code": 0})


def change(path, text):
    item = {"id": path, "type": "file_change", "changes": [{"path": str(Path.cwd() / path), "kind": "update"}]}
    emit(type="item.started", item=item)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)
    emit(type="item.completed", item=item)


emit(type="thread.started", thread_id=f"fake-{session}")
emit(type="turn.started")
if mode == "fail":
    emit(type="turn.failed", error={"message": "service unavailable"})
    sys.exit(1)
if session == 1:
    command("python3 -m unittest discover -s tests")
    change(".planning/NOTES.md", "Sections 1 and 2 done.\n")
    change("tests/test_characterize.py", CHARACTERIZE)
else:
    command("cat .planning/NOTES.md")
    time.sleep(0.4)
    if mode == "regress":
        change("src/pricing.py", Path("src/pricing.py").read_text().replace("* 20 // 100", "* 25 // 100"))
    elif mode == "delete":
        command("rm tests/test_characterize.py")
        Path("tests/test_characterize.py").unlink()
    elif mode == "weaken":
        change("tests/test_characterize.py", CHARACTERIZE.replace('        self.assertEqual(rows[0]["tax"], 40)\n', ""))
    change("src/orders.py", Path("src/orders.py").read_text() + PREVIEW)
    time.sleep(0.4)
emit(type="turn.completed", usage={"input_tokens": 1000 * session, "cached_input_tokens": 400,
                                   "output_tokens": 50, "reasoning_output_tokens": 5})
'''


@pytest.fixture
def writer(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("The fake writer uses a POSIX executable script")
    script, executable, log = tmp_path / "fake_codex.py", tmp_path / "codex", tmp_path / "writer.jsonl"
    script.write_text(FAKE_CODEX)
    # A shell wrapper keeps interpreter paths with spaces working.
    executable.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    executable.chmod(0o755)
    log.touch()
    monkeypatch.setenv("FAKE_WRITER_LOG", str(log))
    monkeypatch.setattr(breaks, "POLL_SECONDS", 0.05)
    return {"codex": str(executable), "calls": lambda: [json.loads(line) for line in log.read_text().splitlines()]}


def plugin(tmp_path):
    root = tmp_path / "plugin"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/zagrosi_skills.py").write_text("# launcher\n")
    (root / "skills/zagrosi-forge").mkdir(parents=True)
    (root / "skills/zagrosi-forge/SKILL.md").write_text("# Forge\n")
    return root


def prepare(tmp_path, writer, *, arms=("notes",), probe=False, repeats=1):
    directory = tmp_path / "matrix"
    breaks.prepare(directory, cases=["import-preview"], arms=arms, repeats=repeats, host="codex", model="fake-model",
                   effort="medium", plugin_root=plugin(tmp_path), timeout=60, probe=probe, codex=writer["codex"])
    return directory


def read(path):
    return json.loads(path.read_text())


def test_sessions_are_isolated_and_retain_their_own_evidence(tmp_path, writer):
    directory = prepare(tmp_path, writer, probe=True)
    trial = directory / "import-preview-lean-1-notes"
    result = breaks.run(directory)
    assert result["halted"] is None
    first, second = writer["calls"]()
    assert CLARIFICATION in first["prompt"] and breaks.FIRST_SESSION in first["prompt"]
    assert CLARIFICATION not in second["prompt"] and breaks.SECOND_SESSION in second["prompt"]
    assert second["prompt"].startswith(breaks.NOTES) and "rounds down per line" not in second["prompt"]
    assert "rounds down per line" not in (trial / "workspace/prompt.md").read_text()
    assert first["args"] == second["args"] and "fake-model" in first["args"]
    if os.geteuid() != 0:  # Permission bits do not restrict root.
        assert not second["leaked"]
    assert CLARIFICATION in (trial / "session-1.md").read_text()  # Readable again after session 2.
    assert not (trial / "telemetry.json").exists() and not (trial / "agent-events.jsonl").exists()
    usage = [read(trial / f"session-{number}-telemetry.json")["totals"]["input_tokens"] for number in (1, 2)]
    assert usage == [1000, 2000]
    assert all((trial / f"session-{number}-events.jsonl").stat().st_size for number in (1, 2))
    analysis = read(trial / "session-break.json")
    assert analysis["checkpoint_unchanged"]
    assert "src/orders.py" not in read(trial / "checkpoint.json")["changed_files"]
    assert (trial / "checkpoint/workspace/tests/test_characterize.py").is_file()
    assert "preview_import" not in (trial / "checkpoint/workspace/src/orders.py").read_text()
    assert read(trial / "checkpoint.json")["probes"] == {"tax_per_line": "missing"}
    assert analysis["final"]["probes"] == {"tax_per_line": "pass"} and analysis["final"]["oracle"]["passed"]
    assert not analysis["regressions"]["any"]
    row = result["attempts"][0]
    assert row["reported_telemetry"]["totals"]["total_tokens"] == 3100 and row["outside_references"] == 0
    # Cleanup still awaits an independent blind review, so nothing is accepted yet.
    assert row["common_quality"] == "failed" and row["rejection"] == ["cleanup review"]


def test_reorientation_ends_at_the_first_code_edit(tmp_path, writer):
    directory = prepare(tmp_path, writer)
    breaks.run(directory)
    reorientation = read(directory / "import-preview-lean-1-notes/session-break.json")["sessions"][1]["reorientation"]
    assert reorientation["boundary"] == "poll" and 0.3 <= reorientation["seconds"] < 30
    assert reorientation["commands"] == 1 and reorientation["planning_read"] is True
    assert reorientation["tokens"] is None  # Codex reports usage per session only.


@pytest.mark.parametrize(("mode", "kind"), [("regress", "failing"), ("delete", "deleted"), ("weaken", "weakened")])
def test_checkpoint_tests_detect_regressions(tmp_path, writer, monkeypatch, mode, kind):
    directory = prepare(tmp_path, writer)
    monkeypatch.setenv("FAKE_WRITER_MODE", mode)
    breaks.run(directory)
    found = read(directory / "import-preview-lean-1-notes/session-break.json")["regressions"]
    expected = ["test_characterize.CharacterizeTests.test_total"]
    if mode == "regress":  # The tax change also breaks the fixture's own tests.
        expected += ["test_orders.OrderTests.test_one_order", "test_orders.OrderTests.test_text_receipt"]
    assert found["tests"][kind] == expected
    assert found["compatibility"] is (mode == "regress") and found["any"]


def test_attempts_run_once_and_a_failed_session_halts_the_run(tmp_path, writer, monkeypatch):
    directory = prepare(tmp_path, writer, arms=("notes", "plain"))
    monkeypatch.setenv("FAKE_WRITER_MODE", "fail")
    result = breaks.run(directory)
    assert result["halted"]["attempt"] == "import-preview-lean-1-notes"
    assert [row["status"] for row in result["attempts"]] == ["completed", "pending"]
    assert len(writer["calls"]()) == 2
    with pytest.raises(ValueError, match="already attempted"):
        breaks.run(directory, "import-preview-lean-1-notes")
    with pytest.raises(FileExistsError):
        prepare(tmp_path, writer)


def test_changed_evaluator_inputs_block_a_run_before_any_session(tmp_path, writer):
    directory = prepare(tmp_path, writer)
    (tmp_path / "plugin/skills/zagrosi-forge/SKILL.md").write_text("# Changed\n")
    with pytest.raises(ValueError, match="plugin:skills/zagrosi-forge/SKILL.md"):
        breaks.run(directory)
    assert not (directory / "import-preview-lean-1-notes/attempt.json").exists() and writer["calls"]() == []


def test_arm_order_rotates_and_only_forge_loads_the_plugin(tmp_path, writer):
    directory = prepare(tmp_path, writer, arms=breaks.ARMS, repeats=2)
    order = [item["arm"] for item in read(directory / "matrix.json")["trials"]]
    assert order == ["forge-lean", "notes", "plain", "notes", "plain", "forge-lean"]
    prompts = {arm: (directory / f"import-preview-lean-1-{arm}/session-1.md").read_text() for arm in breaks.ARMS}
    assert "Use Forge at lean depth" in prompts["forge-lean"] and "Do not read or invoke Forge" in prompts["plain"]
    assert prompts["notes"].startswith(breaks.NOTES) and breaks.NOTES not in prompts["plain"]
    tail = lambda text: text.split("Section plan:", 1)[1]
    assert tail(prompts["forge-lean"]) == tail(prompts["notes"]) == tail(prompts["plain"])


def godfile_control(workspace, violation=None):
    """Known-good summary over three implementation modules, for checking the evaluator only."""
    source = workspace / "src"
    original = (source / "dispatch.py").read_text()
    for name in ("pricing", "records"):
        (source / f"{name}.py").write_text(original)
    summary = '''
from pricing import order_totals, validate_order
from records import order_receipt, orders_from_csv


def summarize_orders(orders):
    checked, seen = [], set()
    for order in orders:
        order = validate_order(order)
        if order["id"] in seen:
            raise ValueError("Duplicate order: " + order["id"])
        seen.add(order["id"])
        checked.append(order)
    counts = dict.fromkeys(STATUSES, 0)
    skus = {}
    for order in checked:
        counts[order["status"]] = counts.get(order["status"], 0) + 1
        if order["status"] == "confirmed":
            for item in order["items"]:
                skus[item["sku"]] = skus.get(item["sku"], 0) + item["quantity"]
    return {"order_count": len(checked), "status_counts": counts,
            "confirmed_total": sum(order_totals(order)["total"] for order in checked if order["status"] == "confirmed"),
            "outstanding_skus": dict(sorted(skus.items())),
            "latest_created_on": max((order["created_on"] for order in checked), default=None)}


OrderBook.summary = lambda self: summarize_orders(self._orders.values())
'''
    if violation == "latest_created_on_all_orders":
        summary = summary.replace('for order in checked), default=None)',
                                  'for order in checked if order["status"] != "cancelled"), default=None)')
    elif violation == "status_counts_zero":
        summary = summary.replace("counts = dict.fromkeys(STATUSES, 0)", "counts = {}")
    (source / "dispatch.py").write_text(original + summary)


@pytest.mark.parametrize("case", sorted(checks.CLAUSES))
def test_violating_each_withheld_clause_fails_the_unchanged_oracle(tmp_path, case):
    clauses = [None] + [clause["id"] for clause in checks.CLAUSES[case]]
    for violation in clauses:
        trial = tmp_path / str(violation)
        breaks.coding_trials.prepare(trial, case, plain_agent=True)
        workspace = trial / "workspace"
        if case == "godfile":
            godfile_control(workspace, violation)
        else:
            complete_import_preview(workspace)
            if violation:
                path = workspace / "src/orders.py"
                path.write_text(path.read_text().replace(
                    '"sku_quantities": dict(sorted(quantities.items()))',
                    '"tax": sum(row["subtotal"] for row in rows) * 20 // 100, '
                    '"sku_quantities": dict(sorted(quantities.items()))'))
        oracle = breaks.run_oracle(workspace, case, compatibility=False,
                                   expected=breaks.coding_trials.CASES[case]["assertions"])
        probes = breaks.run_checks(workspace, "probe", case)
        assert oracle["passed"] is (violation is None), oracle
        assert probes == {name: "fail" if name == violation else "pass" for name in probes}


@pytest.mark.parametrize("case", sorted(checks.CLAUSES))
def test_derived_briefs_drop_each_clause_and_reject_drifted_fixtures(case):
    fixture = breaks.coding_trials.PACK / breaks.coding_trials.CASES[case]["fixture"] / "prompt.md"
    brief = checks.derived_brief(fixture.read_text(), case)
    for clause in checks.CLAUSES[case]:
        for old, new in clause["edits"]:
            assert old not in brief and new in brief
    assert not re.search(r"zero counts|across every order|per line", brief)
    with pytest.raises(ValueError, match="exactly once"):
        checks.derived_brief(brief, case)


def test_protocol_texts_match_the_orchestrator():
    text = " ".join((ROOT / "examples/evals/session-break-protocol.md").read_text().split())
    plans = [f"{number}. {step}" for steps in breaks.PLANS.values() for number, step in enumerate(steps, 1)]
    clauses = [clause["clarification"].rstrip(".") for rows in checks.CLAUSES.values() for clause in rows]
    for expected in [breaks.NOTES, breaks.FIRST_SESSION, breaks.SECOND_SESSION, *plans, *clauses]:
        assert expected in text, expected


def summary(accepted, regressed, tokens):
    return {"accepted": accepted, "regressions": {"attempts": regressed}, "total_tokens": {"total": tokens}}


def test_decision_rule_needs_dominance_and_bounded_tokens():
    state = {"forge-lean": summary(4, 0, 140), "notes": summary(3, 1, 100), "plain": summary(2, 2, 50)}
    assert breaks.decide(state) | {"pairs": None} == {"H1": True, "H2": True, "H0": False, "pairs": None,
                                                      "forge_to_notes_tokens": 1.4}
    state["forge-lean"] = summary(4, 0, 151)
    assert not breaks.decide(state)["H2"] and breaks.decide(state)["H1"]
    alike = {arm: summary(3, 1, 100) for arm in breaks.ARMS}
    assert breaks.decide(alike)["H0"] and not breaks.decide(alike)["H1"]
    mixed = {**alike, "notes": summary(4, 2, 100)}
    assert breaks.decide(mixed)["H0"]  # More accepted but more regressed attempts beats no arm.
    assert breaks.decide({"plain": alike["plain"]}) is None


def test_missing_session_usage_stays_unknown(tmp_path, writer):
    directory = prepare(tmp_path, writer)
    breaks.run(directory)
    trial = directory / "import-preview-lean-1-notes"
    (trial / "session-2-telemetry.json").unlink()
    row = breaks.report(directory)["attempts"][0]
    assert row["reported_telemetry"]["totals"]["input_tokens"] is None
    assert breaks.report(directory)["arms"]["notes"]["total_tokens"]["total"] is None
