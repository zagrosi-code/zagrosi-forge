"""Check removed operations at every depth without weakening aggregate gates."""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
from forge_test_helpers import SCRIPT, load_zagrosi_module, write_lean_plan_fixture
from runtime_support import load_entrypoint
from test_runtime_performance import requires_interval_timer


def test_normal_workflow_imports_leave_detached_engine_unloaded():
    package = load_entrypoint(SCRIPT).load_runtime()
    for name in ("workflows", "scheduling", "context"):
        importlib.import_module(f".{name}", package.__name__)
    prefix = package.__name__ + "."
    loaded = {name.removeprefix(prefix) for name in sys.modules if name.startswith(prefix)}
    assert not loaded.intersection({
        "detached_setup", "detached_record", "detached_context", "detached_authority",
        "detached_state", "detached_progress", "handoff", "handoff_host", "recovery",
        "transaction_io", "transaction_state", "secure_io", "pinners",
    })


def test_every_cli_command_resolves_its_lazy_handler():
    forge = load_zagrosi_module()
    cli = forge.cli
    parser = cli.build_parser()
    commands = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    for command, parser in commands.choices.items():
        handler = parser.get_default("handler")
        assert isinstance(handler, tuple) and len(handler) == 2, command
        module = importlib.import_module(f".{handler[0]}", cli.__package__)
        assert callable(getattr(module, handler[1])), command


@pytest.mark.parametrize("depth", ["lean", "fast", "standard", "deep"])
@pytest.mark.parametrize("write_evidence", [False, True])
@requires_interval_timer
def test_preflight_only_launches_requested_writer(tmp_path, monkeypatch, capsys, depth, write_evidence):
    forge = load_zagrosi_module()
    plan = write_lean_plan_fixture(tmp_path / "plan")
    config_path = plan / "zagrosi_plan_config.json"
    config = json.loads(config_path.read_text())
    config["depth_mode"] = depth
    config_path.write_text(json.dumps(config))
    calls, inventories = [], []
    process = subprocess.run

    def tracked(argv, **kwargs):
        if argv[0] == sys.executable:
            calls.append(argv[2:])
        else:
            assert argv[:2] == ["git", "ls-files"]
            assert kwargs["timeout"] == 10
            inventories.append(argv)
        return process(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", tracked)
    args = ["preflight", "--phase", "plan", "--file", str(plan / "spec.md"), "--target-dir", str(tmp_path), "--depth", depth]
    if write_evidence:
        args.append("--write-evidence")
    assert forge.entrypoint.main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"]
    evidence_expected = write_evidence or depth in {"standard", "deep"}
    assert [gate["name"] for gate in payload["gates"]] == ["spec-file", "doctor", *(["codebase-evidence"] if evidence_expected else []), "status"]
    assert len(calls) == int(write_evidence)
    assert len(inventories) == int(evidence_expected and not write_evidence)
    if calls:
        assert calls[0][0] == "codebase-evidence" and "--write" in calls[0]
    assert (plan / "codex-evidence.md").exists() is write_evidence


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@requires_interval_timer
def test_report_writer_does_not_externalize_remaining_postflight_gates(tmp_path, monkeypatch, capsys, depth):
    forge = load_zagrosi_module()
    plan = write_lean_plan_fixture(tmp_path / "plan")
    config_path = plan / "zagrosi_plan_config.json"
    config = json.loads(config_path.read_text())
    config["depth_mode"] = depth
    config_path.write_text(json.dumps(config))
    args = ["postflight", "--phase", "plan", "--planning-dir", str(plan), "--depth", depth]
    expected_rc = forge.entrypoint.main(args)
    expected = json.loads(capsys.readouterr().out)
    calls = []

    def process(argv, **kwargs):
        calls.append(argv[2:])
        assert kwargs["timeout"] == 120
        return subprocess.CompletedProcess(argv, 0, '{"success":true}', "")

    monkeypatch.setattr(subprocess, "run", process)
    assert forge.entrypoint.main([*args, "--write-report"]) == expected_rc
    actual = json.loads(capsys.readouterr().out)
    assert actual["success"] == expected["success"]
    assert [gate for gate in actual["gates"] if gate["name"] != "report"] == expected["gates"]
    assert len(calls) == 1 and calls[0][0] == "report"
    if depth != "lean":
        assert {"lint-evidence", "lint-artifact-schema", "forge-score"} <= {gate["name"] for gate in actual["gates"]}


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("profile", ["solo", "enterprise", "incident-response"])
def test_score_command_and_row_share_exact_analysis(tmp_path, capsys, depth, profile):
    forge = load_zagrosi_module()
    plan = write_lean_plan_fixture(tmp_path / "plan")
    forge.entrypoint.main(["forge-score", "--planning-dir", str(plan), "--depth", depth, "--profile", profile])
    payload = json.loads(capsys.readouterr().out)
    row = forge.evaluations.forge_score_row(plan, name="example", depth=depth, profile=profile)
    assert row["forge_score"] == payload["forge_score"]
    assert row["components"] == payload["components"]
    assert row["findings"] == payload["finding_count"]


def test_score_uses_direct_analysis_without_emitting_output(tmp_path, monkeypatch, capsys):
    forge = load_zagrosi_module()
    barrier = threading.Barrier(2)

    def analysis(_path, depth):
        barrier.wait(timeout=2)
        return [forge.quality.finding("low", depth, "analyzed")], {"depth_mode": depth}

    def forbidden(*_args, **_kwargs):
        pytest.fail("Scoring invoked a CLI output handler")

    monkeypatch.setattr(forge.validation, "plan_analysis", analysis)
    monkeypatch.setattr(forge.quality, "emit_payload", forbidden)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda depth: forge.scoring.plan_findings_for_score(tmp_path, depth), ["lean", "deep"]))
    assert [findings[0].code for findings, _ in results] == ["lean", "deep"]
    assert capsys.readouterr().out == ""
    assert forge.session._QUALITY_CAPTURE.get() is None


def test_html_report_evidence_and_readiness_are_analyzed_once(tmp_path, monkeypatch, capsys):
    forge = load_zagrosi_module()
    plan = write_lean_plan_fixture(tmp_path / "plan")
    calls = Counter()

    def track(name):
        original = getattr(forge.scoring, name)

        def analysis(*args):
            calls[name] += 1
            return original(*args)

        monkeypatch.setattr(forge.scoring, name, analysis)

    for name in ("evidence_findings_for_score", "readiness_findings_for_score"):
        track(name)
    assert forge.evaluations.html_report(argparse.Namespace(planning_dir=str(plan), depth="lean", profile="solo", output=None)) == 0
    assert json.loads(capsys.readouterr().out)["success"]
    assert set(calls.values()) == {1}
