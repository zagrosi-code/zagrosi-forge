"""Bound repeated work without carrying planning results past changed inputs."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from forge_test_helpers import load_zagrosi_module, write_lean_plan_fixture
from test_runtime_performance import requires_interval_timer


def configure_depth(plan, depth):
    path = plan / "zagrosi_plan_config.json"
    config = json.loads(path.read_text())
    config["depth_mode"] = depth
    path.write_text(json.dumps(config))


def postflight(forge, capsys, plan, *, depth="standard", profile="solo"):
    rc = forge.entrypoint.main([
        "postflight", "--phase", "plan", "--planning-dir", str(plan),
        "--depth", depth, "--profile", profile, "--flight", "strict",
        "--full-output",
    ])
    return rc, json.loads(capsys.readouterr().out)


def test_ownership_cache_is_bounded_copy_safe_and_command_local(tmp_path, monkeypatch, capsys):
    forge = load_zagrosi_module()
    calls = Counter()
    parse = forge.ownership._parse_section_owned_paths

    def counted(text):
        calls[text] += 1
        return parse(text)

    text = "## Owned files\n\n- `src/old.py`\n"
    changed = text.replace("old.py", "new.py")

    def status(_args):
        paths = forge.ownership.extract_section_owned_paths(text)
        paths.append("src/injected.py")
        assert forge.ownership.extract_section_owned_paths(text) == ["src/old.py"]
        assert forge.ownership.extract_section_owned_paths(changed) == ["src/new.py"]
        for index in range(forge.ownership._OWNERSHIP_CACHE_LIMIT + 1):
            forge.ownership.extract_section_owned_paths(f"## Owned files\n\n- `src/item{index}.py`\n")
        context = forge.session._CLI_CONTEXT.get()
        assert len(context["owned_paths"]) == forge.ownership._OWNERSHIP_CACHE_LIMIT
        return forge.output.print_json({"success": True})

    monkeypatch.setattr(forge.ownership, "_parse_section_owned_paths", counted)
    monkeypatch.setattr(forge.status, "status", status)
    for _ in range(2):
        assert forge.entrypoint.main(["status", "--path", str(tmp_path)]) == 0
        assert json.loads(capsys.readouterr().out)["success"]
        assert forge.session._CLI_CONTEXT.get() is None
    assert calls[text] == calls[changed] == 2


def test_mutable_preflight_reads_each_unchanged_file_once(tmp_path, monkeypatch, capsys):
    forge = load_zagrosi_module()
    plan = write_lean_plan_fixture(tmp_path / "plan")
    reads = Counter()
    read = Path.read_text

    def counted(path, *args, **kwargs):
        reads[path.absolute()] += 1
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    command = ["preflight", "--phase", "implement", "--sections-dir", str(plan / "sections"), "--target-dir", str(tmp_path)]
    expected_rc = forge.entrypoint.main(command)
    expected = json.loads(capsys.readouterr().out)
    assert reads and set(reads.values()) == {1}
    monkeypatch.setattr(forge.storage, "read_text", lambda path: read(path, encoding="utf-8"))
    assert forge.entrypoint.main(command) == expected_rc
    assert json.loads(capsys.readouterr().out) == expected


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@requires_interval_timer
def test_postflight_parses_each_section_ownership_once(tmp_path, monkeypatch, capsys, depth):
    forge = load_zagrosi_module()
    plan = write_lean_plan_fixture(tmp_path / "plan")
    configure_depth(plan, depth)
    calls = Counter()
    parse = forge.ownership._parse_section_owned_paths

    def counted(text):
        calls[text] += 1
        return parse(text)

    monkeypatch.setattr(forge.ownership, "_parse_section_owned_paths", counted)
    postflight(forge, capsys, plan, depth=depth)
    section = next((plan / "sections").glob("section-*.md"))
    assert calls[section.read_text()] == 1


@pytest.mark.parametrize("depth", ["standard", "deep"])
@pytest.mark.parametrize("profile", ["solo", "enterprise", "incident-response"])
@pytest.mark.parametrize("broken", [False, True])
@requires_interval_timer
def test_postflight_reuses_full_findings_with_exact_score_equivalence(tmp_path, monkeypatch, capsys, depth, profile, broken):
    forge = load_zagrosi_module()
    plan = write_lean_plan_fixture(tmp_path / "plan")
    configure_depth(plan, depth)
    if broken:
        section = next((plan / "sections").glob("section-*.md"))
        section.write_text("# Section\n\n" + "\n".join(f"REQ-{index:03d}: missing implementation." for index in range(1,30)))
    calls = Counter()
    for module, name in (
        (forge.validation, "plan_analysis"), (forge.validation, "section_analysis"),
        (forge.traceability, "traceability_analysis"), (forge.scoring, "implementation_readiness_analysis"),
    ):
        handler = getattr(module, name)

        def counted(*args, _handler=handler, _name=name, **kwargs):
            calls[_name] += 1
            return _handler(*args, **kwargs)

        monkeypatch.setattr(module, name, counted)
    score_payloads = []
    emit = forge.quality.emit_payload

    def capture(payload, args, exit_code=None):
        if payload.get("gate") == "forge-score":
            score_payloads.append(payload)
        return emit(payload, args, exit_code)

    monkeypatch.setattr(forge.quality, "emit_payload", capture)
    expected = postflight(forge, capsys, plan, depth=depth, profile=profile)
    assert set(calls.values()) == {1}
    calls.clear()
    monkeypatch.setattr(forge.scoring.FlightScoreInputs, "reusable", lambda *_args: {})
    assert postflight(forge, capsys, plan, depth=depth, profile=profile) == expected
    assert set(calls.values()) == {2}
    assert score_payloads[0] == score_payloads[1]
    assert forge.session._CLI_CONTEXT.get() is None


@pytest.mark.parametrize("change", ["rewrite", "new-artifact", "new-section", "configured-source"])
@requires_interval_timer
def test_postflight_recomputes_score_after_input_changes(tmp_path, monkeypatch, capsys, change):
    forge = load_zagrosi_module()
    plan = write_lean_plan_fixture(tmp_path / "plan")
    configure_depth(plan, "standard")
    external = tmp_path / "external" / "source.md"
    external.parent.mkdir()
    if change == "configured-source":
        config_path = plan / "zagrosi_plan_config.json"
        config = json.loads(config_path.read_text())
        config["initial_file"] = str(external)
        config_path.write_text(json.dumps(config))
    calls = []
    analysis = forge.scoring.section_findings_for_score

    def counted(*args):
        calls.append(True)
        return analysis(*args)

    score = forge.scoring.forge_score
    seen = []

    def changed_score(args):
        inputs = forge.session._CLI_CONTEXT.get()["score_inputs"]
        assert inputs.reusable(plan, "standard", 8)
        if change == "rewrite":
            source = plan / "spec.md"
            before = source.stat()
            source.write_text(source.read_text().replace("REQ-001", "REQ-999"))
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        elif change == "new-artifact":
            (plan / "codex-spec.md").write_text("# Replacement spec\n\nREQ-999: newly selected source.\n")
        elif change == "new-section":
            (plan / "sections" / "section-02-extra.md").write_text("# Extra section\n\nREQ-999: orphan section.\n")
        else:
            external.write_text("# External source\n\nREQ-999: newly available source.\n")
        assert not inputs.reusable(plan, "standard", 8)
        result = score(args)
        seen.append(True)
        return result

    monkeypatch.setattr(forge.scoring, "section_findings_for_score", counted)
    monkeypatch.setattr(forge.scoring, "forge_score", changed_score)
    postflight(forge, capsys, plan)
    assert seen and len(calls) == 1
    assert forge.session._CLI_CONTEXT.get() is None


@pytest.mark.skipif(os.name != "posix", reason="Detached handoff requires POSIX file locks")
def test_detached_ownership_parsing_stays_uncached(tmp_path, monkeypatch, capsys):
    forge = load_zagrosi_module()

    def handoff(_args):
        context = forge.session._CLI_CONTEXT.get()
        assert context["texts"] is None and context["owned_paths"] is None
        assert context.get("score_inputs") is None
        return forge.output.print_json({"success": True})

    monkeypatch.setattr(forge.handoff, "detached_implement_evidence_handoff", handoff)
    assert forge.entrypoint.main(["implement-evidence-handoff", "--implementation-root", str(tmp_path), "--section", "S26"]) == 0
    assert json.loads(capsys.readouterr().out)["success"]
