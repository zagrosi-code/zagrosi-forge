"""Completion and context recovery suggest only the work still required."""

import json
from pathlib import Path
import sys

import pytest

from forge_test_helpers import ROOT
from runtime_support import load_runtime
from test_compact_plan import SECTION, make_plan


def invoke(forge, capsys, *args):
    code = forge.entrypoint.main(list(args))
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_completed_handoff_reuses_fresh_receipt(tmp_path, capsys, monkeypatch, depth):
    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    planning = make_plan(tmp_path / "plan", depth)
    target = tmp_path / "repo"
    target.mkdir()
    source = target / "source.py"
    source.write_text("value = 1\n")
    setup = ("implement-setup", "--sections-dir", str(planning / "sections"),
             "--target-dir", str(target), "--depth", depth, "--flight", "off")
    code, payload = invoke(forge, capsys, *setup)
    assert code == 0, payload
    code, receipt = invoke(forge, capsys, "implement-verify", "--planning-dir", str(planning),
                           "--target-dir", str(target), "--section", SECTION, "--integration",
                           "--", sys.executable, "-c", "pass")
    assert code == 0, receipt
    code, recorded = invoke(forge, capsys, "implement-record-section", "--sections-dir", str(planning / "sections"),
                            "--target-dir", str(target), "--section", SECTION, "--review-status", "pass",
                            "--verification-receipt", receipt["receipt_path"], "--flight", "off")
    assert code == 0, recorded
    assert forge.verification.integration_report(planning, target)["success"]
    monkeypatch.setattr(forge.verification, "_capture", lambda *_a, **_kw: pytest.fail("Handoff repeated tests"))
    results = [recorded]
    commands = [setup, ("next-section", "--planning-dir", str(planning)), ("status", "--path", str(planning))]
    for args in commands:
        code, result = invoke(forge, capsys, *args)
        assert code == 0, result
        results.append(result)
    for result in results:
        assert result["integration_verification"]["success"]
        assert "postflight" in result["next_action"]
        assert "verify_integration" not in result["commands"]
        postflight = result["commands"]["postflight"]
        assert "--run-tests" not in postflight
        assert postflight[postflight.index("--target-dir") + 1] == str(target)
    source.write_text("value = 2\n")
    for args in commands:
        code, result = invoke(forge, capsys, *args)
        assert code == 0, result
        assert not result["integration_verification"]["success"]
        assert "verify_integration" in result["commands"]
        assert "capture" in result["next_action"]


@pytest.mark.parametrize("receipt", [None, "{", '{"source":"captured","outcome":"failed","exit_code":1}'])
def test_missing_or_invalid_receipt_requests_verification(tmp_path, receipt):
    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    planning = make_plan(tmp_path / "plan")
    target = tmp_path / "repo"
    target.mkdir()
    if receipt is not None:
        path = forge.verification.receipt_path(planning)
        path.parent.mkdir(parents=True)
        path.write_text(receipt)
    result = forge.actions.implementation_commands(planning, target_dir=target)
    assert not result["integration_verification"]["success"]
    assert result["commands"]["verify_integration"][-3:] == ["--", "<command>", "<argument>"]


def test_context_retry_preserves_target_and_profile(tmp_path, capsys):
    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    planning = make_plan(tmp_path / "plan")
    target = tmp_path / "repo with spaces"
    target.mkdir()
    result = forge.resume.section_entry(planning, SECTION, target_dir=target, profile="enterprise", max_words=1)
    assert not result["success"]
    argv = result["commands"]["retry_context"]
    args = forge.cli.build_parser().parse_args(argv[2:])
    assert args.target_dir == str(target)
    assert args.profile == "enterprise"
    code, recovered = invoke(forge, capsys, *argv[2:])
    assert code == 0, recovered
    record = recovered["commands"]["record"]
    assert record[record.index("--target-dir") + 1] == str(target)
    assert record[record.index("--profile") + 1] == "enterprise"
    assert recovered["packet"]["success"]
