"""Reuse compact plans without hiding changed inputs or sharing mutable results."""
from __future__ import annotations

from collections import Counter
import json
import os

import pytest

from forge_test_helpers import load_zagrosi_module
from test_compact_plan import make_plan, SECTION
from test_runtime_performance import requires_interval_timer


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@requires_interval_timer
def test_postflight_parses_compact_inputs_once_with_identical_results(tmp_path, monkeypatch, capsys, depth):
    forge = load_zagrosi_module()
    planning = make_plan(tmp_path / "plan", depth)
    calls = Counter()
    for module, name in ((forge.artifacts, "_compact_plan_descriptor"),
                         (forge.sections, "_check_section_progress"),
                         (forge.sections, "_section_dependency_analysis"),
                         (forge.validation, "plan_analysis"),
                         (forge.validation, "section_analysis")):
        original = getattr(module, name)

        def counted(*args, _name=name, _original=original, **kwargs):
            calls[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, counted)
    args = ["postflight", "--phase", "plan", "--planning-dir", str(planning), "--flight", "strict"]
    assert forge.entrypoint.main(args) == 0
    expected = json.loads(capsys.readouterr().out)
    assert set(calls.values()) == {1}, calls
    assert forge.entrypoint.main(args) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert set(calls.values()) == {2}, calls
    monkeypatch.setattr(forge.session, "cached_analysis", lambda _name, _key, analyze: analyze(lambda path: path))
    monkeypatch.setattr(forge.scoring.FlightScoreInputs, "reusable", lambda *_args: {})
    assert forge.entrypoint.main(args) == 0
    assert json.loads(capsys.readouterr().out) == expected


@pytest.mark.parametrize("change", ["source-delete", "source-invalid", "source-create", "heading-rewrite",
                                  "section-empty", "section-replace", "dependency", "legacy-plan"])
def test_compact_cache_invalidates_all_selected_inputs(tmp_path, monkeypatch, capsys, change):
    forge = load_zagrosi_module()
    planning = make_plan(tmp_path / "plan")
    section = planning / "sections" / f"{SECTION}.md"
    source = planning / "spec.md"
    if change == "source-create":
        source.unlink()

    def status(_args):
        before = forge.artifacts.compact_plan_descriptor(planning)
        before_progress = forge.sections.check_section_progress(planning)
        if change == "source-delete":
            source.unlink()
        elif change == "source-invalid":
            source.write_text('<!-- FORGE_META\n{"artifact_type":"compact_plan"}\nEND_FORGE_META -->\n')
        elif change == "source-create":
            source.write_text("REQ-001: Restore the missing source.\n")
        elif change == "heading-rewrite":
            previous = section.stat()
            section.write_text(section.read_text().replace("Verdict: pass", "Verdict: fail"))
            os.utime(section, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        elif change == "section-empty":
            section.write_text("")
        elif change == "section-replace":
            replacement = section.with_suffix(".new")
            replacement.write_text(section.read_text().replace("Verdict: pass", "Verdict: fail"))
            replacement.replace(section)
        elif change == "dependency":
            index = planning / "sections/index.md"
            index.write_text(index.read_text() + f"\n{SECTION} depends on section-bad\n")
        else:
            (planning / "codex-plan.md").write_text("# A legacy physical plan takes precedence.\n")
        after = forge.artifacts.compact_plan_descriptor(planning)
        after_progress = forge.sections.check_section_progress(planning)
        assert after != before or after_progress != before_progress
        with monkeypatch.context() as direct:
            direct.setattr(forge.session, "cached_analysis", lambda _name, _key, analyze: analyze(lambda path: path))
            assert forge.artifacts.compact_plan_descriptor(planning) == after
            assert forge.sections.check_section_progress(planning) == after_progress
        return forge.output.print_json({"success": True})

    monkeypatch.setattr(forge.status, "status", status)
    assert forge.entrypoint.main(["status", "--path", str(planning)]) == 0
    assert json.loads(capsys.readouterr().out)["success"]


@pytest.mark.skipif(os.name != "posix", reason="Symlink creation needs POSIX permissions")
def test_source_link_retarget_and_section_link_invalidate_descriptor(tmp_path, monkeypatch, capsys):
    forge = load_zagrosi_module()
    planning = make_plan(tmp_path / "plan")
    source = planning / "spec.md"
    old = source.with_name("old.md")
    source.rename(old)
    source.symlink_to(old.name)
    new = source.with_name("new.md")
    new.write_text(old.read_text())
    section = planning / "sections" / f"{SECTION}.md"

    def status(_args):
        before = forge.artifacts.compact_plan_descriptor(planning)
        assert before["source"] == old
        source.unlink()
        source.symlink_to(new.name)
        assert forge.artifacts.compact_plan_descriptor(planning)["source"] == new
        target = section.with_suffix(".target")
        section.rename(target)
        section.symlink_to(target.name)
        assert "Compact plan files must be regular files, not symbolic links." in forge.artifacts.compact_plan_descriptor(planning)["errors"]
        return 0

    monkeypatch.setattr(forge.status, "status", status)
    assert forge.entrypoint.main(["status", "--path", str(planning)]) == 0


def test_cached_results_are_copy_safe_bounded_and_disabled_without_read_cache(tmp_path):
    forge = load_zagrosi_module()
    planning = make_plan(tmp_path / "plan")
    context = {"texts": {}, "owned_paths": {}}
    token = forge.session._CLI_CONTEXT.set(context)
    try:
        descriptor = forge.artifacts.compact_plan_descriptor(planning)
        descriptor["headings"]["review"] = "Verdict: blocked"
        assert "Verdict: pass" in forge.artifacts.compact_plan_descriptor(planning)["headings"]["review"]
        progress = forge.sections.check_section_progress(planning)
        progress["sections"].clear()
        assert forge.sections.check_section_progress(planning)["sections"] == [SECTION]
        for index in range(100):
            forge.sections.section_dependency_analysis(f"Dependencies: none. Example {index}", [SECTION])
        assert len(context["analyses"]["dependencies"]) == 64
    finally:
        forge.session._CLI_CONTEXT.reset(token)
    for context in (None, {"texts": None}):
        token = forge.session._CLI_CONTEXT.set(context)
        try:
            forge.artifacts.compact_plan_descriptor(planning)
            assert context is None or "analyses" not in context
        finally:
            forge.session._CLI_CONTEXT.reset(token)


@requires_interval_timer
def test_local_quality_gate_preserves_payload_without_json_roundtrip(tmp_path, monkeypatch, capsys):
    forge = load_zagrosi_module()
    planning = make_plan(tmp_path / "plan")
    original = forge.gates.run_local_gate
    outputs = []

    def capture(*args):
        result = original(*args)
        outputs.append(result.stdout)
        return result

    monkeypatch.setattr(forge.gates, "run_local_gate", capture)
    assert forge.entrypoint.main(["postflight", "--phase", "plan", "--planning-dir", str(planning), "--flight", "strict"]) == 0
    assert json.loads(capsys.readouterr().out)["success"]
    assert sum(isinstance(item, dict) for item in outputs) >= 5
    assert forge.session._QUALITY_CAPTURE.get() is None
