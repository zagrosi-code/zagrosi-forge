"""Mutable completion stays current and survives concurrent writers."""

import json
import os
import subprocess
import sys

import pytest

from forge_test_helpers import ROOT, run_cmd
from runtime_support import load_runtime
from test_compact_plan import SECTION, make_plan


@pytest.fixture
def forge():
    return load_runtime(ROOT / "scripts/zagrosi_skills.py")


def record(planning, section=SECTION, *args):
    return run_cmd("implement-record-section", "--sections-dir", str(planning / "sections"),
                   "--section", section, "--review-status", "pass", "--verification", "pytest -q",
                   "--flight", "off", *args)


@pytest.mark.parametrize("review", [
    "Verdict: pass / blocked\nReviewed: output and errors.\n",
    "Verdict: pass\nReviewed: output and errors.\nBLOCKED: lost data.\n",
    "No blocking findings.\n```text\nunclosed example\n",
])
def test_legacy_completion_uses_shared_review_contract(forge, tmp_path, review):
    planning = make_plan(tmp_path / "plan")
    path = planning / "implementation/code_review" / f"{SECTION}-review.md"
    path.parent.mkdir(parents=True)
    path.write_text(review + "Verification: `pytest -q` passed.\n")
    findings = forge.state.completion_evidence_findings(planning, SECTION, {})
    assert "missing-review-status" in {finding.code for finding in findings}


def test_changed_contract_reopens_completion(forge, tmp_path):
    planning = make_plan(tmp_path / "plan")
    record(planning)
    assert forge.state.completed_sections(planning) == {SECTION}
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace("value.strip()", "value.strip().lower()"))
    assert forge.state.completed_sections(planning) == set()
    findings, _ = forge.state.implementation_state_analysis(planning)
    assert "stale-completion-contract" in {finding.code for finding in findings}


def test_unrelated_requirements_do_not_invalidate_completion(forge, tmp_path):
    planning = make_plan(tmp_path / "plan")
    spec = planning / "spec.md"
    spec.write_text(spec.read_text() + "\nREQ-002: Unrelated export.\n")
    record(planning)
    spec.write_text(spec.read_text().replace("Unrelated export", "Unrelated pagination"))
    assert forge.state.completed_sections(planning) == {SECTION}


def test_relevant_requirement_edit_invalidates_completion(forge, tmp_path):
    planning = make_plan(tmp_path / "plan")
    record(planning)
    spec = planning / "spec.md"
    spec.write_text(spec.read_text().replace("preserve internal whitespace and case", "lowercase the result"))
    assert forge.state.completed_sections(planning) == set()


def test_linked_decision_edit_invalidates_completion(forge, tmp_path):
    planning = make_plan(tmp_path / "plan")
    decision = planning / "normalization-policy.md"
    decision.write_text("# Normalization\n\nPreserve Unicode.\n")
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + "\nFollow [policy](../normalization-policy.md#normalization).\n")
    record(planning)
    decision.write_text(decision.read_text().replace("Preserve Unicode", "Strip non-ASCII characters"))
    assert forge.state.completed_sections(planning) == set()


def test_explicit_external_spec_can_link_its_own_contract(forge, tmp_path):
    planning = make_plan(tmp_path / "plan")
    source = tmp_path / "requirements.md"
    source.write_text("# Requirements\n\nREQ-001: Preserve case. See [details](#details).\n\n## Details\n\nPreserve Unicode.\n")
    index = planning / "sections/index.md"
    index.write_text(index.read_text().replace('"source": "spec.md"', '"source": "../requirements.md"'))
    record(planning)
    assert forge.state.completed_sections(planning) == {SECTION}
    source.write_text(source.read_text().replace("Preserve Unicode", "Reject Unicode"))
    assert forge.state.completed_sections(planning) == set()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipes")
def test_code_observation_rejects_fifo_without_opening_a_stream(forge, tmp_path):
    planning = make_plan(tmp_path / "plan")
    source = tmp_path / "src/labels.py"
    source.parent.mkdir()
    os.mkfifo(source)
    with pytest.raises(ValueError, match="not a regular file"):
        forge.state.contract_snapshot(planning, SECTION, target_dir=tmp_path)


def test_changed_predecessor_contract_blocks_completed_successor(forge, tmp_path):
    planning = make_plan(tmp_path / "plan")
    second = "section-02-export"
    index = planning / "sections/index.md"
    metadata, body = index.read_text().split("END_FORGE_META -->\n", 1)
    section = planning / "sections" / f"{SECTION}.md"
    (planning / "codex-plan.md").write_text(metadata + "END_FORGE_META -->\n" + section.read_text())
    index.write_text(body.replace("END_MANIFEST", second + "\nEND_MANIFEST") + f"\n{second} depends on {SECTION}\n")
    (planning / "sections" / f"{second}.md").write_text(section.read_text().replace("labels.py", "exports.py"))
    record(planning)
    record(planning, second)
    assert forge.state.completed_sections(planning) == {SECTION, second}
    section.write_text(section.read_text().replace("value.strip()", "value.strip().lower()"))
    assert forge.state.completed_sections(planning) == set()
    record(planning)
    assert forge.state.completed_sections(planning) == {SECTION}


def test_code_observations_change_without_revoking_completed_contract(forge, tmp_path):
    planning = make_plan(tmp_path / "plan")
    source = tmp_path / "src/labels.py"
    source.parent.mkdir()
    source.write_text("def normalize(value): return value.strip()\n")
    record(planning, SECTION, "--target-dir", str(tmp_path))
    saved = forge.state.load_implementation_state(planning)["completed_sections"][SECTION]["input_snapshot"]
    source.write_text("def normalize(value): return value.strip().lower()\n")
    current = forge.state.contract_snapshot(planning, SECTION, target_dir=tmp_path)
    assert current["contract"] == saved["contract"]
    assert current["code"] != saved["code"]
    assert forge.state.completed_sections(planning) == {SECTION}
    assert forge.state.implementation_recording_status(planning)["changed_code_sections"] == [SECTION]


def test_legacy_records_explicitly_remain_unbound(forge, tmp_path):
    planning = make_plan(tmp_path / "plan")
    state = {"completed_sections": {SECTION: {"review_status": "pass", "verification": ["pytest -q"]}}}
    assert forge.state.completed_sections(planning, state) == {SECTION}
    assert forge.state.implementation_recording_status(planning, state)["legacy_unbound_sections"] == [SECTION]


def test_failed_atomic_write_preserves_existing_state(forge, tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text('{"value":"before"}\n')
    previous = path.read_bytes()
    def fail(*args):
        raise OSError("injected replacement failure")
    monkeypatch.setattr(forge.storage.os, "replace", fail)
    with pytest.raises(OSError, match="replacement failure"):
        forge.storage.write_json(path, {"value": "after"})
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


def test_lock_is_released_when_owner_process_exits(forge, tmp_path):
    path = tmp_path / "state.json"
    child = subprocess.run([sys.executable, "-c", """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from runtime_support import load_runtime
forge = load_runtime(Path(sys.argv[2]))
with forge.storage.file_lock(Path(sys.argv[3])):
    os._exit(0)
""", str(ROOT / "tests"), str(ROOT / "scripts/zagrosi_skills.py"), str(path)], capture_output=True, text=True)
    assert child.returncode == 0, child.stderr
    with forge.storage.file_lock(path, timeout_seconds=.1):
        forge.storage.write_json(path, {"released": True})
    assert json.loads(path.read_text()) == {"released": True}


def test_concurrent_independent_records_preserve_both(tmp_path):
    planning = make_plan(tmp_path / "plan")
    second = "section-02-export"
    index = planning / "sections/index.md"
    metadata, body = index.read_text().split("END_FORGE_META -->\n", 1)
    section = planning / "sections" / f"{SECTION}.md"
    (planning / "codex-plan.md").write_text(metadata + "END_FORGE_META -->\n" + section.read_text())
    index.write_text(body.replace("END_MANIFEST", second + "\nEND_MANIFEST"))
    (planning / "sections" / f"{second}.md").write_text(section.read_text().replace("labels.py", "exports.py"))
    worker = tmp_path / "record.py"
    worker.write_text('''
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from runtime_support import load_runtime
forge = load_runtime(Path(sys.argv[2]))
load = forge.state.load_implementation_state
def slow_read(path):
    value = load(path)
    time.sleep(.15)
    return value
forge.state.load_implementation_state = slow_read
raise SystemExit(forge.entrypoint.main([
    "implement-record-section", "--sections-dir", sys.argv[3], "--section", sys.argv[4],
    "--review-status", "pass", "--verification", "pytest -q", "--flight", "off",
]))
''')
    processes = [subprocess.Popen([sys.executable, str(worker), str(ROOT / "tests"),
                                  str(ROOT / "scripts/zagrosi_skills.py"), str(planning / "sections"), name],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for name in (SECTION, second)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stdout + stderr
    state = json.loads((planning / "implementation/zagrosi_implement_state.json").read_text())
    assert set(state["completed_sections"]) == {SECTION, second}
