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
