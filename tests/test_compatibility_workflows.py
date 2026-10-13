"""Public mutable commands must preserve caller checks before accepting completion."""
from __future__ import annotations

import builtins
import json
from pathlib import Path
import subprocess
import sys

import pytest

from forge_test_helpers import ROOT
from test_compact_plan import SECTION, forge, invoke, make_plan


def workspace(tmp_path, depth="lean", mode="required"):
    planning = make_plan(tmp_path / "planning", depth)
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    (target / "tests").mkdir()
    (target / "src/labels.py").write_text("def normalize(value):\n    return value\n")
    if mode:
        declared = {"version": 1, "mode": mode}
        if mode == "required":
            declared.update(source_paths=["src/labels.py"], check_paths=["tests/callers.py"],
                            check_provenance={"source": "existing", "author": "Public caller expectations"})
        else:
            declared["reason"] = "Documentation-only section has no executable behavior to preserve."
        section = planning / "sections" / f"{SECTION}.md"
        section.write_text(section.read_text() + "\n## Compatibility\n```json\n" + json.dumps(declared) + "\n```\n")
    return planning, target


def setup(forge, capsys, planning, target, *extra):
    return invoke(forge, capsys, "implement-setup", "--sections-dir", str(planning / "sections"),
                  "--target-dir", str(target), "--flight", "off", *extra)


def record(forge, capsys, planning, target, *extra):
    return invoke(forge, capsys, "implement-record-section", "--sections-dir", str(planning / "sections"),
                  "--target-dir", str(target), "--section", SECTION, "--review-status", "pass",
                  "--verification-source", "inspection", "--verification-outcome", "passed",
                  "--verification", "Inspected the feature behavior and all changed callers.",
                  "--file", "src/labels.py", "--flight", "off", *extra)


def verify(forge, capsys, planning, target, stage):
    return invoke(forge, capsys, "implement-verify", "--planning-dir", str(planning),
                  "--target-dir", str(target), "--section", SECTION, "--stage", stage,
                  "--", sys.executable, "-B", "tests/callers.py")


def write_checks(target):
    (target / "tests/callers.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
        "from labels import normalize\nassert normalize('Ada  Lovelace') == 'Ada  Lovelace'\n"
    )


@pytest.mark.parametrize("source", [[], {}])
@pytest.mark.parametrize("kind", ["compatibility", "integration"])
def test_malformed_receipt_source_is_rejected_without_execution(forge, tmp_path, capsys, source, kind):
    planning, target = workspace(tmp_path, mode="required" if kind == "compatibility" else None)
    assert setup(forge, capsys, planning, target)[0] == 0
    write_checks(target)
    marker = tmp_path / "executed"
    checks = target / "tests/callers.py"
    checks.write_text(checks.read_text() + f"Path({str(marker)!r}).write_text('ran')\n")
    args = ["--planning-dir", str(planning), "--target-dir", str(target)]
    if kind == "compatibility":
        assert verify(forge, capsys, planning, target, "baseline")[0] == 0
        path = forge.compatibility.receipt_path(planning, SECTION)
        command = ["implement-verify", *args, "--section", SECTION, "--stage", "candidate",
                   "--", sys.executable, "-B", "tests/callers.py"]
    else:
        assert record(forge, capsys, planning, target)[0] == 0
        assert invoke(forge, capsys, "implement-verify", *args,
                      "--", sys.executable, "-B", "tests/callers.py")[0] == 0
        path = forge.verification.receipt_path(planning)
        command = ["postflight", "--phase", "implement", *args, "--strict", "--flight", "strict"]
    marker.unlink()
    saved = json.loads(path.read_text())
    (saved["baseline"] if kind == "compatibility" else saved)["source"] = source
    path.write_text(json.dumps(saved))
    before = path.read_bytes()
    result = subprocess.run([sys.executable, str(ROOT / "scripts/zagrosi_skills.py"), *command],
                            capture_output=True, text=True)
    assert result.returncode == 1 and not json.loads(result.stdout)["success"], result.stderr
    assert "Traceback" not in result.stderr
    assert path.read_bytes() == before and not marker.exists()


def test_required_pair_cannot_be_bypassed_by_manual_completion(forge, tmp_path, capsys):
    planning, target = workspace(tmp_path)
    assert setup(forge, capsys, planning, target)[0] == 0
    code, payload = record(forge, capsys, planning, target)
    assert code == 1, payload
    assert not forge.state.completed_sections(planning, target_dir=target)


@pytest.mark.parametrize("depth", ["lean", "fast", "standard", "deep"])
def test_original_checks_are_required_before_and_after_changes(forge, tmp_path, capsys, depth):
    planning, target = workspace(tmp_path, depth)
    code, payload = setup(forge, capsys, planning, target)
    assert code == 0, payload
    pair_path = planning / "implementation/verification" / f"{SECTION}-compatibility.json"
    assert pair_path.is_file()
    write_checks(target)
    code, payload = verify(forge, capsys, planning, target, "baseline")
    assert code == 0, payload
    assert not forge.verification.receipt_path(planning, SECTION).exists()
    assert record(forge, capsys, planning, target)[0] == 1
    (target / "src/labels.py").write_text("def normalize(value):\n    return value.strip().lower()\n")
    assert verify(forge, capsys, planning, target, "candidate")[0] == 1
    assert record(forge, capsys, planning, target)[0] == 1
    (target / "src/labels.py").write_text("def normalize(value):\n    return value.strip()\n")
    assert verify(forge, capsys, planning, target, "candidate")[0] == 0
    code, payload = record(forge, capsys, planning, target)
    assert code == 0, payload
    saved = forge.state.load_implementation_state(planning)["completed_sections"][SECTION]
    assert saved["compatibility"]["baseline"]["outcome"] == "passed"
    assert saved["compatibility"]["candidate"]["outcome"] == "passed"
    # Later sections may change source; historical evidence remains bound to its own contract.
    (target / "src/labels.py").write_text("def normalize(value):\n    return value.strip()\n\nVERSION = 2\n")
    assert SECTION in forge.state.completed_sections(planning, target_dir=target)


@pytest.mark.parametrize("mode", [None, "not_required"])
def test_legacy_and_not_required_keep_existing_completion(forge, tmp_path, capsys, mode):
    planning, target = workspace(tmp_path, mode=mode)
    assert setup(forge, capsys, planning, target)[0] == 0
    code, payload = record(forge, capsys, planning, target)
    assert code == 0, payload
    assert not forge.compatibility.receipt_path(planning, SECTION).exists()


def test_setup_rejects_unknown_selection_before_state_writes(forge, tmp_path, capsys):
    planning, target = workspace(tmp_path)
    code, payload = setup(forge, capsys, planning, target, "--section", "section-99-missing")
    assert code == 1, payload
    assert not (planning / "implementation/zagrosi_implement_config.json").exists()
    assert not (planning / "implementation/zagrosi_implement_state.json").exists()


def test_readonly_actions_require_activation_and_pin_candidate_command(forge, tmp_path, capsys):
    planning, target = workspace(tmp_path)
    payload = forge.actions.implementation_commands(planning, SECTION, target_dir=target)
    assert payload["compatibility"]["stage"] == "activate"
    assert not (planning / "implementation").exists()
    assert setup(forge, capsys, planning, target, "--section", SECTION)[0] == 0
    write_checks(target)
    assert verify(forge, capsys, planning, target, "baseline")[0] == 0
    payload = forge.actions.implementation_commands(planning, SECTION, target_dir=target)
    command = payload["commands"]["verify_candidate"]
    assert command[command.index("--") + 1:] == [sys.executable, "-B", "tests/callers.py"]


@pytest.mark.parametrize("extra", [[], ["--section", SECTION, "--integration"], ["--section", SECTION, "--source", "inspection", "--outcome", "passed", "--evidence", "Inspected all callers"]])
def test_staged_capture_rejects_incompatible_flags(forge, tmp_path, capsys, extra):
    planning, target = workspace(tmp_path)
    code, payload = invoke(forge, capsys, "implement-verify", "--planning-dir", str(planning),
                           "--target-dir", str(target), "--stage", "baseline", *extra)
    assert code == 1, payload
    assert not (planning / "implementation/verification").exists()


def test_resume_cannot_reanchor_original_source_after_edits(forge, tmp_path, capsys):
    planning, target = workspace(tmp_path)
    assert setup(forge, capsys, planning, target)[0] == 0
    path = forge.compatibility.receipt_path(planning, SECTION)
    before = path.read_bytes()
    (target / "src/labels.py").write_text("def normalize(value):\n    return value.strip()\n")
    code, payload = setup(forge, capsys, planning, target)
    assert code == 0, payload
    assert path.read_bytes() == before
    assert payload["compatibility"]["stage"] == "blocked"
    write_checks(target)
    code, payload = verify(forge, capsys, planning, target, "baseline")
    assert code == 1, payload
    assert path.read_bytes() == before


def test_setup_does_not_activate_a_blocked_successor(forge, tmp_path, capsys):
    planning, target = workspace(tmp_path)
    index = planning / "sections/index.md"
    marker, rest = index.read_text().split("END_FORGE_META -->\n", 1)
    successor = "section-02-consumer"
    section = planning / "sections" / f"{SECTION}.md"
    (planning / "codex-plan.md").write_text(marker + "END_FORGE_META -->\n" + section.read_text())
    index.write_text(rest.replace("END_MANIFEST", successor + "\nEND_MANIFEST") + f"\n{successor} depends on {SECTION}.\n")
    (planning / "sections" / f"{successor}.md").write_text(section.read_text().replace(SECTION, successor))
    code, payload = setup(forge, capsys, planning, target, "--section", successor)
    assert code == 1, payload
    assert not forge.compatibility.receipt_path(planning, successor).exists()
    assert not (planning / "implementation/zagrosi_implement_state.json").exists()


def test_detached_setup_rejects_mutable_selector_before_writes(forge, tmp_path, capsys, monkeypatch):
    planning, target = workspace(tmp_path)
    external = tmp_path / "detached"
    original_import = builtins.__import__

    def portable_import(name, globals=None, locals=None, fromlist=(), level=0):
        if "detached_setup" in (fromlist or ()):
            raise AssertionError("Invalid arguments must not load native detached dependencies")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", portable_import)
    code, payload = setup(forge, capsys, planning, target, "--section", SECTION,
                          "--implementation-root", str(external))
    assert code == 1, payload
    assert "mutable" in payload["error"]
    assert not external.exists()


def test_candidate_is_not_a_substitute_for_feature_verification(forge, tmp_path, capsys):
    planning, target = workspace(tmp_path)
    assert setup(forge, capsys, planning, target)[0] == 0
    write_checks(target)
    assert verify(forge, capsys, planning, target, "baseline")[0] == 0
    assert verify(forge, capsys, planning, target, "candidate")[0] == 0
    code, payload = invoke(forge, capsys, "implement-record-section", "--sections-dir", str(planning / "sections"),
                           "--target-dir", str(target), "--section", SECTION, "--review-status", "pass", "--flight", "off")
    assert code == 1, payload
    assert not forge.state.completed_sections(planning, target_dir=target)


def test_removed_pair_in_completion_record_reopens_required_section(forge, tmp_path, capsys):
    planning, target = workspace(tmp_path)
    assert setup(forge, capsys, planning, target)[0] == 0
    write_checks(target)
    assert verify(forge, capsys, planning, target, "baseline")[0] == 0
    assert verify(forge, capsys, planning, target, "candidate")[0] == 0
    assert record(forge, capsys, planning, target)[0] == 0
    state = forge.state.load_implementation_state(planning)
    state["completed_sections"][SECTION].pop("compatibility")
    assert SECTION not in forge.state.completed_sections(planning, state, target_dir=target)


def test_rejected_retarget_preserves_saved_default_target(forge, tmp_path, capsys):
    planning, target = workspace(tmp_path)
    assert setup(forge, capsys, planning, target)[0] == 0
    config = planning / "implementation/zagrosi_implement_config.json"
    before = config.read_bytes()
    other = tmp_path / "other"
    other.mkdir()
    code, payload = setup(forge, capsys, planning, other)
    assert code == 1, payload
    assert config.read_bytes() == before
    assert forge.mutable_inputs.target_directory(planning) == target


def test_missing_original_source_leaves_no_setup_state(forge, tmp_path, capsys):
    planning, target = workspace(tmp_path)
    (target / "src/labels.py").unlink()
    code, payload = setup(forge, capsys, planning, target)
    assert code == 1, payload
    assert not (planning / "implementation/zagrosi_implement_state.json").exists()
    assert not (planning / "implementation/zagrosi_implement_config.json").exists()


def test_legacy_compatibility_prose_keeps_existing_completion(forge, tmp_path, capsys):
    planning, target = workspace(tmp_path, mode=None)
    path = planning / "sections" / f"{SECTION}.md"
    path.write_text(path.read_text() + "\n## Compatibility\nPreserve public signatures and callers.\n")
    assert setup(forge, capsys, planning, target)[0] == 0
    code, payload = record(forge, capsys, planning, target)
    assert code == 0, payload
    assert not forge.compatibility.receipt_path(planning, SECTION).exists()


@pytest.mark.parametrize("evidence", ["progress", "verification", "history", "pending", "completed", "legacy"])
def test_missing_origin_with_saved_work_requires_original_receipt(forge, tmp_path, capsys, evidence):
    planning, target = workspace(tmp_path)
    assert setup(forge, capsys, planning, target)[0] == 0
    write_checks(target)
    assert verify(forge, capsys, planning, target, "baseline")[0] == 0
    pair = forge.compatibility.receipt_path(planning, SECTION)
    original = pair.read_bytes()
    saved = pair.with_suffix(".saved")
    pair.rename(saved)
    state_dir = planning / "implementation"
    if evidence == "progress":
        assert invoke(forge, capsys, "implement-progress", "--planning-dir", str(planning),
                      "--section", SECTION, "--stage", "red", "--result", "Fixture feature failed")[0] == 0
    elif evidence in {"verification", "history"}:
        args = ["implement-verify", "--planning-dir", str(planning), "--target-dir", str(target),
                "--section", SECTION, "--", sys.executable, "-c", "raise SystemExit(1)"]
        assert invoke(forge, capsys, *args)[0] == 1
        if evidence == "history":
            assert invoke(forge, capsys, *args)[0] == 1
            forge.verification.receipt_path(planning, SECTION).rename(state_dir / "feature.saved")
    else:
        state = forge.state.load_implementation_state(planning)
        field = "completed_sections" if evidence == "completed" else "pending_sections"
        state[field] = {SECTION: {"compatibility": json.loads(original)}}
        path = forge.state.implementation_state_path(planning)
        path.write_text(json.dumps(state))
        if evidence == "legacy":
            path.rename(state_dir / "deep_implement_state.json")
    source = target / "src/labels.py"
    source.write_text("def normalize(value):\n    return value.strip()\n")
    before = {p: p.read_bytes() for p in planning.rglob("*") if p.is_file()}
    status = forge.compatibility.status(planning, target, SECTION)
    assert status["stage"] == "blocked"
    code, payload = setup(forge, capsys, planning, target)
    assert code == 1, payload
    assert "origin" in payload["error"].lower() and "restore" in payload["error"].lower()
    assert not pair.exists()
    assert verify(forge, capsys, planning, target, "baseline")[0] == 1
    assert {p: p.read_bytes() for p in planning.rglob("*") if p.is_file()} == before
    assert source.read_text() == "def normalize(value):\n    return value.strip()\n"
    saved.rename(pair)
    assert setup(forge, capsys, planning, target)[0] == 0
    assert pair.read_bytes() == original
    assert forge.compatibility.status(planning, target, SECTION)["stage"] == "candidate"
    assert verify(forge, capsys, planning, target, "candidate")[0] == 0
    if evidence == "progress":
        assert invoke(forge, capsys, "implement-verify", "--planning-dir", str(planning),
                      "--target-dir", str(target), "--section", SECTION, "--", sys.executable, "-c",
                      "import sys; sys.path.insert(0, 'src'); from labels import normalize; "
                      "assert normalize(' Ada  Lovelace ') == 'Ada  Lovelace'")[0] == 0
        assert invoke(forge, capsys, "implement-record-section", "--sections-dir", str(planning / "sections"),
                      "--target-dir", str(target), "--section", SECTION, "--review-status", "pass",
                      "--verification-receipt", str(forge.verification.receipt_path(planning, SECTION)),
                      "--file", "src/labels.py", "--flight", "off")[0] == 0
        accepted = forge.state.load_implementation_state(planning)["completed_sections"][SECTION]["compatibility"]
        assert accepted["origin"] == json.loads(original)["origin"]
        assert accepted["baseline"]["protected_source"] != accepted["candidate"]["protected_source"]


def test_other_section_evidence_and_empty_history_allow_first_origin(forge, tmp_path, capsys):
    planning, target = workspace(tmp_path)
    other = "section-02-unrelated"
    state_dir = planning / "implementation"
    history = state_dir / "verification/history"
    (history / SECTION).mkdir(parents=True)
    (history / other).mkdir()
    (history / other / "old.json").write_text("retained unrelated result")
    (state_dir / "verification" / f"{other}.json").write_text("unrelated result")
    (state_dir / "verification/integration.json").write_text("integration result")
    (state_dir / "zagrosi_implement_state.json").write_text(json.dumps({"pending_sections": {other: {}}}))
    (state_dir / "forge-progress.json").write_text(json.dumps({"events": [{"section": other, "stage": "red"}]}))
    forge.storage.write_json(state_dir / "zagrosi_implement_config.json", {
        "sections_dir": str((planning / "sections").resolve()), "target_dir": str(target.resolve()),
        "planning_dir": str(planning.resolve()), "test_command": "uv run pytest tests/test_labels.py",
        "runtime": "python-uv", "profile": "solo", "depth_mode": "lean",
    })
    before = {p: p.read_bytes() for p in planning.rglob("*") if p.is_file()}
    assert forge.compatibility.status(planning, target, SECTION)["stage"] == "activate"
    assert {p: p.read_bytes() for p in planning.rglob("*") if p.is_file()} == before
    assert setup(forge, capsys, planning, target)[0] == 0
    assert forge.compatibility.receipt_path(planning, SECTION).is_file()
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize("progress", ["not JSON", '{"events": {}}', '{"events": [null]}'])
def test_missing_origin_does_not_hide_invalid_progress(forge, tmp_path, capsys, progress):
    planning, target = workspace(tmp_path)
    assert setup(forge, capsys, planning, target)[0] == 0
    pair = forge.compatibility.receipt_path(planning, SECTION)
    pair.rename(pair.with_suffix(".saved"))
    path = planning / "implementation/forge-progress.json"
    path.write_text(progress)
    before = {p: p.read_bytes() for p in planning.rglob("*") if p.is_file()}
    assert forge.compatibility.status(planning, target, SECTION)["stage"] == "blocked"
    assert setup(forge, capsys, planning, target)[0] == 1
    assert not pair.exists()
    assert {p: p.read_bytes() for p in planning.rglob("*") if p.is_file()} == before


def test_completed_pair_survives_missing_live_receipt(forge, tmp_path, capsys):
    planning, target = workspace(tmp_path)
    assert setup(forge, capsys, planning, target)[0] == 0
    write_checks(target)
    assert verify(forge, capsys, planning, target, "baseline")[0] == 0
    assert verify(forge, capsys, planning, target, "candidate")[0] == 0
    assert record(forge, capsys, planning, target)[0] == 0
    path = forge.compatibility.receipt_path(planning, SECTION)
    path.rename(path.with_suffix(".saved"))
    state = forge.state.implementation_state_path(planning).read_bytes()
    code, payload = setup(forge, capsys, planning, target)
    assert code == 0, payload
    assert payload["completed_sections"] == [SECTION]
    assert not path.exists()
    assert forge.state.implementation_state_path(planning).read_bytes() == state


@pytest.mark.parametrize("review_section", [SECTION, "section-02-unrelated"])
def test_review_artifact_alone_marks_only_its_section_started(forge, tmp_path, capsys, review_section):
    planning, target = workspace(tmp_path)
    pair = forge.compatibility.receipt_path(planning, SECTION)
    if review_section == SECTION:
        assert setup(forge, capsys, planning, target)[0] == 0
        pair.rename(pair.with_suffix(".saved"))
    source = target / "src/labels.py"
    source.write_text("def normalize(value):\n    return value.strip()\n")
    review = planning / "implementation/code_review" / f"{review_section}-review.md"
    review.parent.mkdir(parents=True)
    review.write_text("Verdict: blocked\nReviewed: fixture caller behavior still needs verification.\n")
    before = {p: p.read_bytes() for p in planning.rglob("*") if p.is_file()}
    expected = "blocked" if review_section == SECTION else "activate"
    assert forge.compatibility.status(planning, target, SECTION)["stage"] == expected
    assert {p: p.read_bytes() for p in planning.rglob("*") if p.is_file()} == before
    code, payload = setup(forge, capsys, planning, target)
    if review_section == SECTION:
        assert code == 1, payload
        assert "restore" in payload["error"].lower()
        assert not pair.exists()
        assert {p: p.read_bytes() for p in planning.rglob("*") if p.is_file()} == before
    else:
        assert code == 0, payload
        assert pair.is_file()
        assert all(p.read_bytes() == content for p, content in before.items())
    assert source.read_text() == "def normalize(value):\n    return value.strip()\n"
