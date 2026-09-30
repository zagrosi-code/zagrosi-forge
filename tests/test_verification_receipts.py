"""Completion evidence has explicit provenance, outcomes, and current inputs."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from forge_test_helpers import ROOT
from runtime_support import load_runtime
from test_compact_plan import SECTION, make_plan


@pytest.fixture
def workspace(tmp_path):
    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    planning = make_plan(tmp_path / "plan")
    target = tmp_path / "repo"
    target.mkdir()
    (target / "source.py").write_text("value = 1\n")
    return forge, planning, target


def invoke(workspace, capsys, *args):
    forge, planning, target = workspace
    code = forge.entrypoint.main(list(args))
    return code, json.loads(capsys.readouterr().out)


def verify_args(workspace, *args):
    _, planning, target = workspace
    return ["implement-verify", "--planning-dir", str(planning), "--target-dir", str(target), *args]


def record_args(workspace, *args):
    _, planning, target = workspace
    return ["implement-record-section", "--sections-dir", str(planning / "sections"), "--target-dir", str(target),
            "--section", SECTION, "--review-status", "pass", "--flight", "off", *args]


@pytest.mark.parametrize("text", ["pytest: FAILED (3 failures)", "pytest passed", "pytest -q"])
def test_free_text_never_establishes_completion(workspace, capsys, text):
    code, result = invoke(workspace, capsys, *record_args(workspace, "--verification", text))
    assert code == 1 and not result["success"]
    assert workspace[0].state.completed_sections(workspace[1]) == set()


@pytest.mark.parametrize("outcome", ["failed", "timed_out", "skipped", "pending"])
@pytest.mark.parametrize("source", ["attestation", "inspection"])
def test_explicit_unsuccessful_outcomes_cannot_close_a_section(workspace, capsys, source, outcome):
    code, result = invoke(workspace, capsys, *record_args(
        workspace, "--verification", "Everything passed", "--verification-source", source, "--verification-outcome", outcome))
    assert code == 1 and not result["success"]


@pytest.mark.parametrize("source", ["attestation", "inspection"])
def test_manual_evidence_retains_its_source(workspace, capsys, source):
    code, result = invoke(workspace, capsys, *record_args(
        workspace, "--verification", "Checked Unicode and empty-label cases", "--verification-source", source,
        "--verification-outcome", "passed"))
    assert code == 0
    assert result["record"]["verification_result"]["source"] == source
    assert "exit_code" not in result["record"]["verification_result"]


@pytest.mark.parametrize("exit_code", [0, 3])
def test_captured_exit_status_overrides_output_text(workspace, capsys, exit_code):
    code, result = invoke(workspace, capsys, *verify_args(workspace, "--section", SECTION, "--", sys.executable,
                                                        "-c", f"print('passed'); raise SystemExit({exit_code})"))
    assert (code == 0) is (exit_code == 0)
    assert result["exit_code"] == exit_code
    code, record = invoke(workspace, capsys, *record_args(workspace, "--verification-receipt", result["receipt_path"]))
    assert (code == 0) is (exit_code == 0)
    if exit_code == 0:
        assert record["record"]["verification_result"]["source"] == "captured"


def test_timeout_is_saved_and_cannot_complete(workspace, capsys):
    code, result = invoke(workspace, capsys, *verify_args(workspace, "--timeout", "0.1", "--", sys.executable,
                                                        "-c", "import time; time.sleep(10)"))
    assert code == 1 and result["outcome"] == "timed_out"
    assert json.loads(Path(result["receipt_path"]).read_text())["outcome"] == "timed_out"


@pytest.mark.parametrize("change", ["edit", "add", "remove", "contract", "linked"])
def test_changed_inputs_invalidate_final_receipt(workspace, capsys, change):
    forge, planning, target = workspace
    detail = planning / "detail.md"
    detail.write_text("REQ-001: Preserve Unicode.\n")
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + "\nSee [detail](../detail.md).\n")
    code, _ = invoke(workspace, capsys, *verify_args(workspace, "--", sys.executable, "-c", "pass"))
    assert code == 0
    assert forge.verification.integration_report(planning, target)["success"]
    if change == "edit":
        (target / "source.py").write_text("value = 2\n")
    elif change == "add":
        (target / "new.py").write_text("new = True\n")
    elif change == "remove":
        (target / "source.py").unlink()
    elif change == "contract":
        section.write_text(section.read_text().replace("value.strip()", "value.lower()"))
    else:
        detail.write_text("REQ-001: Reject Unicode.\n")
    assert not forge.verification.integration_report(planning, target)["success"]


def test_postflight_requires_and_reuses_integration_receipt(workspace, capsys, monkeypatch):
    forge, planning, target = workspace
    code, _ = invoke(workspace, capsys, *record_args(workspace, "--verification", "Targeted cases passed",
                                                  "--verification-source", "attestation", "--verification-outcome", "passed"))
    assert code == 0
    args = ["postflight", "--phase", "implement", "--planning-dir", str(planning), "--target-dir", str(target), "--strict"]
    code, result = invoke(workspace, capsys, *args)
    assert code == 1 and "integration-verification" in result["blocking_gates"]
    code, _ = invoke(workspace, capsys, *verify_args(workspace, "--", sys.executable, "-c", "pass"))
    assert code == 0
    monkeypatch.setattr(forge.verification, "_capture", lambda *a: pytest.fail("Postflight repeated verification"))
    for _ in range(2):
        code, result = invoke(workspace, capsys, *args)
        assert code == 0 and result["success"]


def test_one_full_run_can_record_section_and_final_integration(workspace, capsys, monkeypatch):
    forge, planning, target = workspace
    code, result = invoke(workspace, capsys, *verify_args(workspace, "--section", SECTION, "--integration", "--",
                                                        sys.executable, "-c", "pass"))
    assert code == 0 and result["integration_receipt_path"]
    monkeypatch.setattr(forge.verification, "_capture", lambda *a: pytest.fail("Repeated full suite"))
    code, _ = invoke(workspace, capsys, *record_args(workspace, "--verification-receipt", result["receipt_path"]))
    assert code == 0
    code, result = invoke(workspace, capsys, "postflight", "--phase", "implement", "--planning-dir", str(planning),
                          "--target-dir", str(target), "--strict")
    assert code == 0 and result["integration_verification"]["source"] == "captured"


@pytest.mark.parametrize("override", [False, True])
def test_postflight_uses_saved_target_from_another_directory(workspace, capsys, monkeypatch, tmp_path, override):
    forge, planning, saved = workspace
    config = planning / "implementation/zagrosi_implement_config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"target_dir": str(saved)}))
    target = tmp_path / "explicit target" if override else saved
    target.mkdir(exist_ok=True)
    current = tmp_path / "unrelated current directory"
    current.mkdir()
    monkeypatch.chdir(current)
    active = forge, planning, target
    code, verified = invoke(active, capsys, *verify_args(active, "--section", SECTION, "--integration", "--",
                                                        sys.executable, "-c", "pass"))
    assert code == 0
    assert invoke(active, capsys, *record_args(active, "--verification-receipt", verified["receipt_path"]))[0] == 0
    diff = tmp_path / "empty.diff"
    diff.write_text("")
    seen = []
    original = forge.gates.run_internal_gate_batch

    def collect(jobs):
        seen.extend(command[command.index("--repo") + 1] for _, command, _ in jobs if "--repo" in command)
        return original(jobs)

    monkeypatch.setattr(forge.gates, "run_internal_gate_batch", collect)
    args = ["postflight", "--phase", "implement", "--planning-dir", str(planning), "--strict", "--diff-file", str(diff),
            "--section-file", str(planning / "sections" / f"{SECTION}.md")]
    if override:
        args.extend(["--target-dir", str(target)])
    code, result = invoke(active, capsys, *args)
    assert code == 0 and result["integration_verification"]["success"]
    assert result["target_dir"] == str(target)
    assert seen == [str(target), str(target)]


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_generated_reports_preserve_receipt_but_nested_source_changes_do_not(tmp_path, capsys, depth):
    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    planning = make_plan(tmp_path / "planning with spaces", depth)
    source = planning / "implementation/source with spaces.py"
    source.parent.mkdir()
    source.write_text("value = 1\n")
    workspace = forge, planning, tmp_path
    code, result = invoke(workspace, capsys, *verify_args(workspace, "--section", SECTION, "--integration", "--",
                                                        sys.executable, "-c", "pass"))
    assert code == 0
    code, _ = invoke(workspace, capsys, *record_args(workspace, "--verification-receipt", result["receipt_path"]))
    assert code == 0
    code, result = invoke(workspace, capsys, "postflight", "--phase", "implement", "--planning-dir", str(planning),
                          "--target-dir", str(tmp_path), "--strict", "--write-report")
    assert code == 0 and result["integration_verification"]["success"]
    assert forge.verification.integration_report(planning, tmp_path)["success"]
    source.write_text("value = 2\n")
    assert not forge.verification.integration_report(planning, tmp_path)["success"]


def test_verifier_never_executes_plan_text_or_rewrites_child_arguments(workspace, capsys):
    _, planning, target = workspace
    index = planning / "sections/index.md"
    index.write_text(index.read_text().replace("uv run pytest tests/test_labels.py", "touch SHOULD_NOT_EXIST"))
    code, _ = invoke(workspace, capsys, *verify_args(workspace))
    assert code == 1 and not (target / "SHOULD_NOT_EXIST").exists()
    code, result = invoke(workspace, capsys, *verify_args(workspace, "--", sys.executable, "-c",
                                                        "import sys; print(sys.argv[1:])", "$(touch SHOULD_NOT_EXIST)", "--pretty", "--full-output"))
    assert code == 0 and "$(touch SHOULD_NOT_EXIST)" in result["stdout_tail"]
    assert "--pretty" in result["stdout_tail"] and "--full-output" in result["stdout_tail"]
    assert not (target / "SHOULD_NOT_EXIST").exists()


def test_failed_new_execution_replaces_prior_passing_receipt(workspace, capsys):
    forge, planning, target = workspace
    assert invoke(workspace, capsys, *verify_args(workspace, "--", sys.executable, "-c", "pass"))[0] == 0
    code, result = invoke(workspace, capsys, *verify_args(workspace, "--", str(target / "missing-executable")))
    assert code == 1 and result["outcome"] == "failed"
    assert not forge.verification.integration_report(planning, target)["success"]


def test_interrupted_execution_leaves_pending_receipt(workspace, capsys, monkeypatch):
    forge, planning, target = workspace
    assert invoke(workspace, capsys, *verify_args(workspace, "--", sys.executable, "-c", "pass"))[0] == 0
    def interrupt(*args):
        raise KeyboardInterrupt
    monkeypatch.setattr(forge.verification, "_capture", interrupt)
    with pytest.raises(KeyboardInterrupt):
        invoke(workspace, capsys, *verify_args(workspace, "--", sys.executable, "-c", "pass"))
    report = forge.verification.integration_report(planning, target)
    assert not report["success"] and report["outcome"] == "pending"


def test_receipt_cannot_hide_explicit_failed_outcome(workspace, capsys):
    _, result = invoke(workspace, capsys, *verify_args(workspace, "--section", SECTION, "--", sys.executable, "-c", "pass"))
    code, record = invoke(workspace, capsys, *record_args(workspace, "--verification-receipt", result["receipt_path"],
                                                       "--verification-outcome", "failed"))
    assert code == 1 and "not both" in record["error"]


def test_git_ignored_cache_does_not_invalidate_receipt(workspace, capsys):
    forge, planning, target = workspace
    subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
    (target / ".gitignore").write_text("cache/\n")
    code, _ = invoke(workspace, capsys, *verify_args(workspace, "--", sys.executable, "-c", "pass"))
    assert code == 0
    (target / "cache").mkdir()
    (target / "cache/result").write_text("derived")
    assert forge.verification.integration_report(planning, target)["success"]
    (target / "new-source.py").write_text("value = 3\n")
    assert not forge.verification.integration_report(planning, target)["success"]


@pytest.mark.parametrize("tracked", [False, True])
def test_ignored_nested_target_still_binds_untracked_source(tmp_path, capsys, tracked):
    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    repository = tmp_path / "ancestor"
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    (repository / ".gitignore").write_text("scratch/\n")
    target = repository / "scratch/workspace"
    target.mkdir(parents=True)
    source = target / "source.py"
    source.write_text("value = 1\n")
    if tracked:
        (target / "tracked.py").write_text("tracked = True\n")
        subprocess.run(["git", "add", "-f", "scratch/workspace/tracked.py"], cwd=repository, check=True, capture_output=True)
    planning = make_plan(tmp_path / "plan")
    workspace = forge, planning, target
    assert invoke(workspace, capsys, *verify_args(workspace, "--", sys.executable, "-c", "pass"))[0] == 0
    assert forge.verification.integration_report(planning, target)["success"]
    source.write_text("value = 2\n")
    assert not forge.verification.integration_report(planning, target)["success"]


def test_nested_target_keeps_its_own_ignore_rules(tmp_path, capsys):
    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    repository = tmp_path / "ancestor"
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    target = repository / "workspace"
    target.mkdir()
    (target / ".gitignore").write_text("*\n!.gitignore\n!source.py\n")
    (target / "source.py").write_text("value = 1\n")
    planning = make_plan(tmp_path / "plan")
    workspace = forge, planning, target
    assert invoke(workspace, capsys, *verify_args(workspace, "--", sys.executable, "-c", "pass"))[0] == 0
    (target / "derived.bin").write_bytes(b"cache")
    assert forge.verification.integration_report(planning, target)["success"]
    (target / "source.py").write_text("value = 2\n")
    assert not forge.verification.integration_report(planning, target)["success"]


@pytest.mark.parametrize("artifact", ["quality-gates.md", "codex-integration-notes.md", "codex-research.md",
                                      "codex-interview.md", "codex-evidence.md"])
def test_external_authoritative_artifact_changes_invalidate_final_receipt(workspace, capsys, artifact):
    forge, planning, target = workspace
    path = planning / artifact
    path.write_text("Approved checks: unit tests only.\n")
    assert invoke(workspace, capsys, *verify_args(workspace, "--", sys.executable, "-c", "pass"))[0] == 0
    assert forge.verification.integration_report(planning, target)["success"]
    path.write_text("Require security and integration checks.\n")
    assert not forge.verification.integration_report(planning, target)["success"]


def test_source_changed_during_verification_cannot_pass(workspace, capsys):
    code, result = invoke(workspace, capsys, *verify_args(workspace, "--", sys.executable, "-c",
                                                        "from pathlib import Path; Path('source.py').write_text('value = 2')"))
    assert code == 1 and result["outcome"] == "failed"


@pytest.mark.parametrize("phase", ["project", "plan"])
def test_failed_required_setup_gate_returns_failure_and_preserves_artifacts(workspace, capsys, monkeypatch, phase):
    forge, planning, target = workspace
    failure = {"success": False, "blocking_gates": ["doctor"]}
    monkeypatch.setattr(forge.flights, f"{phase}_preflight_report", lambda *a: failure)
    args = (["project-setup", "--brief", "Build a labels API", "--planning-dir", str(target)] if phase == "project" else
            ["plan-setup", "--file", str(planning / "spec.md")])
    code, result = invoke(workspace, capsys, *args)
    assert code == 1 and not result["success"]
    assert result["artifacts_created"] is True
    assert Path(result["state_dir"] if phase == "project" else result["config_path"]).exists()
