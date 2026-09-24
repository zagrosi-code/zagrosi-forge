"""Final verification reuses contracts only during an unchanged read phase."""
from __future__ import annotations

from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path

import pytest

from forge_test_helpers import load_zagrosi_module
from test_compact_plan import SECTION, make_plan
from test_runtime_performance import requires_interval_timer


def completion(forge, planning, target):
    return {"review_status": "pass", "verification": ["pytest passed"], "completed_at": "observed",
            "input_snapshot": forge.state.contract_snapshot(planning, SECTION, target_dir=target)}


@requires_interval_timer
def test_final_verification_reads_and_parses_unchanged_contract_once(tmp_path, monkeypatch, capsys):
    forge = load_zagrosi_module()
    planning = make_plan(tmp_path / "plan")
    record = completion(forge, planning, tmp_path)
    forge.storage.write_json(forge.state.implementation_state_path(planning), {"completed_sections": {SECTION: record}})
    inputs = importlib.import_module(forge.state.__package__ + ".mutable_inputs")
    name = "_contract_inputs" if hasattr(inputs, "_contract_inputs") else "contract_inputs"
    analyze, read = getattr(inputs, name), Path.read_text
    calls, reads = [], Counter()

    def counted(*args):
        calls.append(True)
        return analyze(*args)

    def counted_read(path, *args, **kwargs):
        reads[path] += 1
        return read(path, *args, **kwargs)

    monkeypatch.setattr(inputs, name, counted)
    monkeypatch.setattr(Path, "read_text", counted_read)
    args = ["postflight", "--phase", "implement", "--planning-dir", str(planning), "--strict", "--full-output"]
    assert forge.entrypoint.main(args) == 0
    expected = json.loads(capsys.readouterr().out)
    assert len(calls) == 1, calls
    assert reads[planning / "spec.md"] == reads[planning / "sections" / f"{SECTION}.md"] == 1
    assert forge.session._CLI_CONTEXT.get() is None
    monkeypatch.setattr(forge.session, "read_phase", nullcontext)
    assert forge.entrypoint.main(args) == 0
    assert json.loads(capsys.readouterr().out) == expected


@pytest.mark.parametrize("change", ["same-size", "linked", "new-plan", "state", "candidate", "link-retarget", "external-create"])
def test_phase_reuse_rechecks_inputs_and_completion_state(tmp_path, change):
    forge = load_zagrosi_module()
    planning = make_plan(tmp_path / "plan")
    section = planning / "sections" / f"{SECTION}.md"
    linked = planning / "contracts" / "detail.md"
    linked.parent.mkdir()
    linked.write_text("REQ-001: Preserve case.\n")
    section.write_text(section.read_text() + "\n[Detail](../contracts/detail.md)\n")
    external = tmp_path / "external.md"
    if change == "external-create":
        # A physical plan can select a source which does not yet exist.
        (planning / "codex-plan.md").write_text("REQ-001: Preserve case.\n")
        (planning / "zagrosi_plan_config.json").write_text(json.dumps({"initial_file": str(external)}))
    elif change == "link-retarget":
        first = linked.with_name("first.md")
        linked.rename(first)
        try:
            linked.symlink_to(first.name)
        except OSError:
            pytest.skip("Symlinks are unavailable")
    record = completion(forge, planning, tmp_path)
    state = {"completed_sections": {SECTION: record}}
    state_path = forge.state.implementation_state_path(planning)
    forge.storage.write_json(state_path, state)
    outer = {"texts": None, "owned_paths": None}
    token = forge.session._CLI_CONTEXT.set(outer)
    try:
        with forge.session.read_phase():
            assert forge.state.completed_sections(planning) == {SECTION}
            if change == "same-size":
                before = section.stat()
                section.write_text(section.read_text().replace("case changes", "CASE changes"))
                os.utime(section, ns=(before.st_atime_ns, before.st_mtime_ns))
            elif change == "linked":
                linked.write_text("REQ-001: Change case.\n")
            elif change == "new-plan":
                (planning / "codex-plan.md").write_text("REQ-001: Change behavior.\n")
            elif change == "link-retarget":
                second = linked.with_name("second.md")
                second.write_text("REQ-001: Change case.\n")
                linked.unlink()
                linked.symlink_to(second.name)
            elif change == "external-create":
                external.write_text("REQ-001: Change source requirements.\n")
            else:
                state = deepcopy(state)
                state["completed_sections"][SECTION]["review_status"] = "blocked"
                if change == "state":
                    forge.storage.write_json(state_path, state)
            assert forge.state.completed_sections(planning, state if change == "candidate" else None) == set()
        assert forge.session._CLI_CONTEXT.get() is outer
    finally:
        forge.session._CLI_CONTEXT.reset(token)
