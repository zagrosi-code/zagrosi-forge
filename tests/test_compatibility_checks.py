"""Caller checks run against original and candidate code without changing their inputs."""

import copy
import json
import os
from pathlib import Path
import sys

import pytest

from forge_test_helpers import ROOT
from runtime_support import load_runtime
from test_compact_plan import SECTION, make_plan


CHECK = "from source import value\nassert value() == 'x'\nassert value(name=' y ') == 'y'\n"
COMMAND = [sys.executable, "-B", "checks.py"]


def contract(**changes):
    return {"version": 1, "mode": "required", "source_paths": ["source.py"],
            "check_paths": ["checks.py"],
            "check_provenance": {"source": "writer", "author": "Fixture author"}, **changes}


def block(value):
    return "\n## Compatibility\n```json\n" + json.dumps(value) + "\n```\n"


@pytest.fixture
def workspace(tmp_path):
    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    planning = make_plan(tmp_path / "planning")
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + block(contract()))
    target = tmp_path / "target"
    target.mkdir()
    (target / "source.py").write_text("def value(name='x'):\n    return name.strip()\n")
    return forge, planning, target


def activate(workspace):
    forge, planning, target = workspace
    return forge.compatibility.activate(planning, target, SECTION)


def run(workspace, stage, command=COMMAND):
    forge, planning, target = workspace
    return forge.compatibility.capture(planning, target, SECTION, stage, command, 10)


def baseline(workspace):
    activate(workspace)
    (workspace[2] / "checks.py").write_text(CHECK)
    result = run(workspace, "baseline")
    assert result["success"], result
    return result


def test_original_caller_checks_reject_public_import_regression(workspace):
    forge, planning, target = workspace
    baseline(workspace)
    (target / "source.py").write_text("def renamed(name='x'):\n    return name.strip()\n")
    result = run(workspace, "candidate")
    assert not result["success"] and result["outcome"] == "failed"
    assert "ImportError" in result["stderr_tail"]
    with pytest.raises(ValueError, match="passed"):
        forge.compatibility.completion_evidence(planning, target, SECTION)


def test_unchanged_checks_accept_refactor_and_preserve_historical_evidence(workspace):
    forge, planning, target = workspace
    baseline(workspace)
    (target / "helper.py").write_text("def value(name='x'):\n    return name.strip()\n")
    (target / "source.py").write_text("from helper import value\n")
    assert run(workspace, "candidate")["success"]
    evidence = forge.compatibility.completion_evidence(planning, target, SECTION)
    assert evidence["baseline"]["command"] == evidence["candidate"]["command"] == COMMAND
    assert evidence["declaration"]["check_provenance"]["source"] == "writer"
    (target / "source.py").write_text("# Later section owns another change.\n")
    assert forge.compatibility.evidence_error(planning, target, SECTION, evidence) is None
    with pytest.raises(ValueError, match="changed"):
        forge.compatibility.completion_evidence(planning, target, SECTION)


def test_origin_is_captured_before_check_authoring_and_never_replaced(workspace):
    forge, planning, target = workspace
    original = activate(workspace)
    assert not (target / "checks.py").exists()
    (target / "source.py").write_text("def value(name='x'):\n    return name.upper()\n")
    assert activate(workspace) == original
    (target / "checks.py").write_text(CHECK)
    with pytest.raises(ValueError, match="original"):
        run(workspace, "baseline")
    assert activate(workspace) == original


@pytest.mark.parametrize("drift", ["check", "command", "mode"])
def test_candidate_rejects_changed_check_inputs(workspace, drift):
    _, _, target = workspace
    baseline(workspace)
    command = COMMAND
    if drift == "check":
        (target / "checks.py").write_text("pass\n")
    elif drift == "command":
        command = [sys.executable, "-B", "-c", "pass"]
    else:
        if os.name == "nt":
            pytest.skip("Windows does not expose executable file modes")
        (target / "checks.py").chmod(0o700)
    with pytest.raises(ValueError, match="check|command"):
        run(workspace, "candidate", command)


def test_baseline_failure_is_retained_after_check_repair(workspace):
    forge, planning, target = workspace
    activate(workspace)
    (target / "checks.py").write_text("assert False, 'characterization needs repair'\n")
    first = run(workspace, "baseline")
    assert not first["success"]
    (target / "checks.py").write_text(CHECK)
    assert run(workspace, "baseline")["success"]
    pair = json.loads(Path(first["receipt_path"]).read_text())
    assert pair["prior_attempts"][0]["stage"] == "baseline"
    assert pair["prior_attempts"][0]["result"]["outcome"] == "failed"
    assert "characterization needs repair" in pair["prior_attempts"][0]["result"]["stderr_tail"]
    assert run(workspace, "candidate")["success"]
    assert forge.compatibility.completion_evidence(planning, target, SECTION)


@pytest.mark.parametrize("changed", ["source.py", "checks.py"])
def test_mutation_during_command_cannot_create_passing_evidence(workspace, monkeypatch, changed):
    forge, planning, target = workspace
    baseline(workspace)
    original = forge.verification._capture

    def mutate(*args):
        result = original(*args)
        with (target / changed).open("a") as handle:
            handle.write("\n# modified during capture\n")
        return result

    monkeypatch.setattr(forge.verification, "_capture", mutate)
    result = run(workspace, "candidate")
    assert not result["success"] and result["outcome"] == "failed"
    assert "during execution" in result["error"]


def test_interrupted_retry_invalidates_old_pass_and_retains_it(workspace, monkeypatch):
    forge, planning, target = workspace
    baseline(workspace)
    assert run(workspace, "candidate")["success"]

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(forge.verification, "_capture", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run(workspace, "candidate")
    with pytest.raises(ValueError, match="pending"):
        forge.compatibility.completion_evidence(planning, target, SECTION)
    pair = json.loads(forge.compatibility.receipt_path(planning, SECTION).read_text())
    assert pair["candidate"]["outcome"] == "pending"
    assert pair["prior_attempts"][-1]["result"]["outcome"] == "passed"


@pytest.mark.parametrize("corrupt", ["origin", "checks", "command", "attestation", "contract", "target", "history", "version"])
def test_copied_evidence_rejects_unbound_or_relabelled_results(workspace, corrupt):
    forge, planning, target = workspace
    baseline(workspace)
    assert run(workspace, "candidate")["success"]
    evidence = copy.deepcopy(forge.compatibility.completion_evidence(planning, target, SECTION))
    if corrupt == "origin":
        evidence["origin"]["source"]["source.py"] = "0" * 64
    elif corrupt == "checks":
        evidence["candidate"]["checks"]["checks.py"] = "0" * 64
    elif corrupt == "command":
        evidence["candidate"]["command"] = ["true"]
    elif corrupt == "attestation":
        evidence["candidate"].update(source="attestation", evidence=["Passed"])
    elif corrupt == "contract":
        evidence["contract_digest"] = "0" * 64
    elif corrupt == "target":
        evidence["target_dir"] = str(target.parent)
    elif corrupt == "history":
        evidence["prior_attempts"] = {"discarded": True}
    else:
        evidence["version"] = True
    assert forge.compatibility.evidence_error(planning, target, SECTION, evidence)


@pytest.mark.parametrize("payload", ["{}", "[]", "{broken", '{"version": 1, "version": 1}'])
def test_malformed_origin_is_preserved_without_replacement(workspace, payload):
    forge, planning, _ = workspace
    activate(workspace)
    path = forge.compatibility.receipt_path(planning, SECTION)
    path.write_text(payload)
    with pytest.raises(ValueError):
        activate(workspace)
    assert path.read_text() == payload


def test_malformed_history_stage_fails_without_a_traceback_or_overwrite(workspace):
    forge, planning, target = workspace
    pair = activate(workspace)
    pair["prior_attempts"] = [{"stage": [], "result": {}}]
    path = forge.compatibility.receipt_path(planning, SECTION)
    path.write_text(json.dumps(pair))
    with pytest.raises(ValueError, match="prior_attempts"):
        activate(workspace)
    assert json.loads(path.read_text()) == pair
    assert forge.compatibility.status(planning, target, SECTION)["stage"] == "blocked"


def test_explicit_ignored_helpers_are_bound(workspace):
    forge, planning, target = workspace
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace('["checks.py"]', '["checks.py", ".planning/check-data"]'))
    (target / ".gitignore").write_text(".planning/\n")
    helper = target / ".planning/check-data/expected.txt"
    helper.parent.mkdir(parents=True)
    helper.write_text("x\n")
    baseline(workspace)
    helper.write_text("y\n")
    with pytest.raises(ValueError, match="checks"):
        run(workspace, "candidate")
    assert forge.compatibility.status(planning, target, SECTION)["stage"] == "baseline"


@pytest.mark.parametrize("alias", ["symlink", "parent-symlink", "hardlink"])
def test_source_and_check_aliases_are_rejected(workspace, alias):
    _, planning, target = workspace
    checks = target / "checks.py"
    try:
        if alias == "hardlink":
            os.link(target / "source.py", checks)
        elif alias == "symlink":
            checks.symlink_to(target / "source.py")
        else:
            (target / "linked").symlink_to(target, target_is_directory=True)
            section = planning / "sections" / f"{SECTION}.md"
            section.write_text(section.read_text().replace('["checks.py"]', '["linked/source.py"]'))
    except OSError as exc:
        pytest.skip(f"Host cannot create this link: {exc}")
    with pytest.raises(ValueError, match="overlap|symlink"):
        activate(workspace)


def test_missing_protected_path_is_not_an_origin(workspace):
    _, _, target = workspace
    (target / "source.py").unlink()
    with pytest.raises(ValueError, match="missing"):
        activate(workspace)


def test_status_is_read_only_and_tracks_actionable_recovery(workspace):
    forge, planning, target = workspace
    before = sorted(planning.rglob("*"))
    assert forge.compatibility.status(planning, target, SECTION)["stage"] == "activate"
    assert sorted(planning.rglob("*")) == before
    activate(workspace)
    assert forge.compatibility.status(planning, target, SECTION)["stage"] == "baseline"
    (target / "checks.py").write_text(CHECK)
    assert run(workspace, "baseline")["success"]
    assert forge.compatibility.status(planning, target, SECTION)["stage"] == "candidate"
    assert forge.compatibility.status(planning, target, SECTION)["command"] == COMMAND
    assert run(workspace, "candidate")["success"]
    assert forge.compatibility.status(planning, target, SECTION)["stage"] == "complete"
    (target / "source.py").write_text("# candidate modified\n")
    assert forge.compatibility.status(planning, target, SECTION)["stage"] == "candidate"
    (target / "checks.py").write_text("pass\n")
    state = forge.compatibility.status(planning, target, SECTION)
    assert state["stage"] == "blocked" and "Original source" in state["error"]


@pytest.mark.parametrize("value", [
    [], {"version": True, "mode": "not_required", "reason": "No edits"},
    {"version": 1, "mode": "not_required", "reason": "TODO"},
    contract(source_paths=["../source.py"]), contract(check_paths=["source.py"]),
    contract(source_paths=["src"], check_paths=["src/checks.py"]),
    contract(check_paths=["checks.py", "./checks.py"]),
    contract(check_provenance={"source": [], "author": "Someone"}),
])
def test_invalid_declarations_fail_closed(workspace, value):
    with pytest.raises(ValueError):
        workspace[0].compatibility.parse_declaration(block(value))


def test_legacy_and_explicit_non_applicability_do_not_claim_a_baseline(workspace):
    forge, planning, target = workspace
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().split("\n## Compatibility\n")[0])
    assert activate(workspace) is None
    assert forge.compatibility.completion_evidence(planning, target, SECTION) is None
    assert forge.compatibility.status(planning, target, SECTION)["mode"] == "legacy"
    decision = {"version": 1, "mode": "not_required", "reason": "Only documentation changes."}
    section.write_text(section.read_text() + block(decision))
    assert activate(workspace) == decision
    assert forge.compatibility.completion_evidence(planning, target, SECTION) == decision
    assert not forge.compatibility.receipt_path(planning, SECTION).exists()
    with pytest.raises(ValueError, match="no required"):
        run(workspace, "baseline")


@pytest.mark.parametrize("suffix", ["duplicate", "unclosed", "malformed", "repeated-key"])
def test_bad_compatibility_blocks_cannot_be_ignored(workspace, suffix):
    text = block(contract())
    if suffix == "duplicate":
        text += text
    elif suffix == "unclosed":
        text = text.rsplit("```", 1)[0]
    elif suffix == "malformed":
        text = text.replace('"version": 1', '"version":')
    else:
        text = text.replace('"version": 1', '"version": 1, "version": 1')
    with pytest.raises(ValueError):
        workspace[0].compatibility.parse_declaration(text)


@pytest.mark.parametrize("body", [
    "Keep existing imports, errors, and caller behavior unchanged.\n",
    "Use the existing [caller contract](../spec.md) and preserve all public exports.\n",
    "[Caller docs](docs/api.md)\n",
    "[Caller docs][api]\n[api]: docs/api.md\n",
    "- Preserve existing imports.\n- Retain error ordering.\n",
])
def test_legacy_compatibility_prose_does_not_declare_captured_checks(workspace, body):
    assert workspace[0].compatibility.parse_declaration("## Compatibility\n" + body) is None


@pytest.mark.parametrize("body", [
    "Keep existing imports.\n## Compatibility\nKeep existing errors.\n",
    json.dumps(contract()),
    '{"version": 1, "mode":',
    "[{}]",
    "[",
    "Prose before the missing fence.\n" + json.dumps(contract()),
    "```python\npass\n```\n",
    "```json\n" + json.dumps(contract()),
])
def test_structured_or_duplicate_compatibility_cannot_fall_back_to_legacy(workspace, body):
    with pytest.raises(ValueError):
        workspace[0].compatibility.parse_declaration("## Compatibility\n" + body)


def test_baseline_cannot_complete_or_supply_missing_candidate(workspace):
    forge, planning, target = workspace
    baseline(workspace)
    with pytest.raises(ValueError, match="candidate"):
        forge.compatibility.completion_evidence(planning, target, SECTION)


def test_capture_keeps_mutable_lock_while_command_runs(workspace, monkeypatch):
    forge, planning, _ = workspace
    baseline(workspace)
    original = forge.verification._capture

    def check_lock(*args):
        with pytest.raises(TimeoutError):
            with forge.storage.file_lock(planning / "implementation/.mutable-state", timeout_seconds=0.01):
                pytest.fail("A concurrent setup could change the active origin")
        return original(*args)

    monkeypatch.setattr(forge.verification, "_capture", check_lock)
    assert run(workspace, "candidate")["success"]


def test_timeout_is_retained_and_cannot_supply_baseline(workspace):
    forge, planning, target = workspace
    activate(workspace)
    (target / "checks.py").write_text(CHECK)
    result = forge.compatibility.capture(planning, target, SECTION, "baseline",
                                          [sys.executable, "-c", "import time; time.sleep(10)"], 0.05)
    assert not result["success"] and result["outcome"] == "timed_out"
    with pytest.raises(ValueError, match="timed_out"):
        run(workspace, "candidate")


def test_directory_sources_bind_nested_modes_and_ignore_generated_bytecode(workspace):
    forge, planning, target = workspace
    source = target / "src/source.py"
    source.parent.mkdir()
    (target / "source.py").rename(source)
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace('["source.py"]', '["src"]'))
    origin = activate(workspace)
    cache = source.parent / "__pycache__/source.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"generated bytecode")
    (target / "checks.py").write_text("from src.source import value\nassert value() == 'x'\n")
    assert run(workspace, "baseline")["success"]
    assert activate(workspace)["origin"] == origin["origin"]
    if os.name != "nt":
        source.chmod(0o700)
        with pytest.raises(ValueError, match="original"):
            run(workspace, "baseline")


def test_regular_files_named_like_cache_directories_are_observed(workspace):
    forge, _, target = workspace
    source = target / "src/node_modules"
    source.parent.mkdir()
    source.write_text("original regular file\n")
    before, _ = forge.mutable_inputs.regular_tree_observations(target, ["src"])
    source.write_text("changed regular file\n")
    after, _ = forge.mutable_inputs.regular_tree_observations(target, ["src"])
    assert before != after


def test_cache_named_symlinks_are_rejected_before_directory_exclusions(workspace):
    forge, _, target = workspace
    source = target / "src"
    source.mkdir()
    try:
        (source / "venv").symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Host cannot create this link: {exc}")
    with pytest.raises(ValueError, match="symlink"):
        forge.mutable_inputs.regular_tree_observations(target, ["src"])
