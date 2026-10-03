"""New mutable plans choose preservation boundaries without upgrading legacy plans."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from forge_test_helpers import ROOT
from runtime_support import load_runtime
from test_compact_plan import SECTION, make_plan
from test_resume_guidance import documented_detached_plan


NOT_REQUIRED = {"version": 1, "mode": "not_required", "reason": "Adds an isolated helper without changing existing callers."}
REQUIRED = {"version": 1, "mode": "required", "source_paths": ["src/labels.py"],
            "check_paths": ["tests/test_labels.py"],
            "check_provenance": {"source": "existing", "author": "Repository maintainers"}}


@pytest.fixture
def forge():
    return load_runtime(ROOT / "scripts/zagrosi_skills.py")


def invoke(forge, capsys, *args):
    code = forge.entrypoint.main(list(args))
    return code, json.loads(capsys.readouterr().out)


def declared_plan(path, depth="lean"):
    planning = make_plan(path, depth)
    (planning / "zagrosi_plan_config.json").write_text(json.dumps({"depth_mode": depth, "compatibility_policy": "declared"}))
    return planning


def declaration(value):
    return "\n## Compatibility\n```json\n" + json.dumps(value) + "\n```\n"


def append_decision(planning, value, section=SECTION):
    path = planning / "sections" / f"{section}.md"
    path.write_text(path.read_text() + declaration(value))


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("gate", ["lint-plan-artifacts", "lint-sections"])
def test_declared_policy_requires_a_section_decision(forge, capsys, tmp_path, depth, gate):
    planning = declared_plan(tmp_path / "plan", depth)
    code, payload = invoke(forge, capsys, gate, "--planning-dir", str(planning), "--strict")
    assert code == 1 and not payload["success"]
    assert "missing-compatibility" in {finding["code"] for finding in payload["findings"]}


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("value", [REQUIRED, NOT_REQUIRED])
def test_deliberate_decisions_admit_without_running_or_activating_checks(forge, capsys, tmp_path, depth, value):
    planning = declared_plan(tmp_path / "plan", depth)
    append_decision(planning, value)
    before = {path: path.read_bytes() for path in planning.rglob("*") if path.is_file()}
    for gate in ("lint-plan-artifacts", "lint-sections"):
        code, payload = invoke(forge, capsys, gate, "--planning-dir", str(planning), "--strict")
        assert code == 0 and payload["success"], payload
    assert {path: path.read_bytes() for path in planning.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("invalid", ["malformed", "duplicate", "unclosed", "reason", "overlap"])
def test_present_invalid_declarations_block_legacy_and_new_plans(forge, capsys, tmp_path, configured, invalid):
    planning = declared_plan(tmp_path / "plan") if configured else make_plan(tmp_path / "plan")
    content = declaration(NOT_REQUIRED)
    if invalid == "malformed":
        content = content.replace('"version": 1', '"version":')
    elif invalid == "duplicate":
        content += content
    elif invalid == "unclosed":
        content = content.rsplit("```", 1)[0]
    elif invalid == "reason":
        content = declaration({**NOT_REQUIRED, "reason": "TODO"})
    else:
        content = declaration({**REQUIRED, "check_paths": REQUIRED["source_paths"]})
    path = planning / "sections" / f"{SECTION}.md"
    path.write_text(path.read_text() + content)
    for gate in ("lint-plan-artifacts", "lint-sections"):
        code, payload = invoke(forge, capsys, gate, "--planning-dir", str(planning), "--strict")
        assert code == 1 and not payload["success"]
        assert "invalid-compatibility" in {finding["code"] for finding in payload["findings"]}


def test_declared_policy_covers_every_listed_section(forge, capsys, tmp_path):
    planning = declared_plan(tmp_path / "plan")
    append_decision(planning, NOT_REQUIRED)
    index = planning / "sections/index.md"
    index.write_text(index.read_text().split("END_FORGE_META -->\n", 1)[1].replace("END_MANIFEST", "section-02-summary\nEND_MANIFEST"))
    first = planning / "sections" / f"{SECTION}.md"
    second = planning / "sections/section-02-summary.md"
    second.write_text(first.read_text().split("\n## Compatibility\n", 1)[0].replace("labels.py", "summary.py"))
    code, payload = invoke(forge, capsys, "lint-sections", "--planning-dir", str(planning), "--strict")
    missing = [finding for finding in payload["findings"] if finding["code"] == "missing-compatibility"]
    assert code == 1 and len(missing) == 1 and missing[0]["path"] == str(second)
    append_decision(planning, NOT_REQUIRED, "section-02-summary")
    code, payload = invoke(forge, capsys, "lint-sections", "--planning-dir", str(planning), "--strict")
    assert code == 0, payload


@pytest.mark.parametrize("policy", [None, False, "legacy", [], {}])
def test_present_unknown_policy_cannot_bypass_admission(forge, capsys, tmp_path, policy):
    planning = declared_plan(tmp_path / "plan")
    (planning / "zagrosi_plan_config.json").write_text(json.dumps({"compatibility_policy": policy}))
    for gate in ("lint-plan-artifacts", "lint-sections"):
        code, payload = invoke(forge, capsys, gate, "--planning-dir", str(planning), "--strict")
        assert code == 1 and not payload["success"]
        assert "invalid-compatibility-policy" in {finding["code"] for finding in payload["findings"]}


@pytest.mark.parametrize("decision", [None, {"version": 1, "mode": "TODO"}])
def test_other_finished_fields_cannot_hide_an_undecided_draft(forge, capsys, tmp_path, decision):
    planning = declared_plan(tmp_path / "plan")
    index = planning / "sections/index.md"
    index.write_text("<!-- FORGE_SCAFFOLD -->\n" + index.read_text())
    if decision:
        append_decision(planning, decision)
    code, payload = invoke(forge, capsys, "plan-setup", "--file", str(planning / "spec.md"), "--flight", "off")
    assert code == 0 and payload["scaffold"]["unfinished"]
    assert payload["resume_label"] == "write_plan"


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_new_setup_saves_policy_and_an_undecided_draft(forge, capsys, tmp_path, depth):
    spec = tmp_path / "spec.md"
    spec.write_text("REQ-001: Preserve existing label errors while adding normalization.\n")
    code, first = invoke(forge, capsys, "plan-setup", "--file", str(spec), "--depth", depth, "--flight", "off")
    assert code == 0, first
    config = Path(first["config_path"])
    assert json.loads(config.read_text())["compatibility_policy"] == "declared"
    section = tmp_path / "sections/section-01-contract.md"
    assert "## Compatibility" in section.read_text()
    code, rejected = invoke(forge, capsys, "lint-plan-artifacts", "--planning-dir", str(tmp_path), "--strict")
    assert code == 1 and "invalid-compatibility" in {finding["code"] for finding in rejected["findings"]}
    original = {path: path.read_bytes() for path in (config, section)}
    code, resumed = invoke(forge, capsys, "plan-setup", "--file", str(spec), "--flight", "off")
    assert code == 0 and resumed["mode"] == "resume"
    assert {path: path.read_bytes() for path in original} == original


@pytest.mark.parametrize("config_name", ["zagrosi_plan_config.json", "deep_plan_config.json"])
def test_existing_config_is_not_upgraded(forge, capsys, tmp_path, config_name):
    spec = tmp_path / "spec.md"
    spec.write_text("REQ-001: Add label normalization.\n")
    config = tmp_path / config_name
    config.write_text(json.dumps({"depth_mode": "lean", "review_mode": "agent_review"}))
    before = config.read_bytes()
    code, payload = invoke(forge, capsys, "plan-setup", "--file", str(spec), "--flight", "off")
    assert code == 0 and payload["mode"] == "resume"
    assert config.read_bytes() == before


@pytest.mark.parametrize("artifact", ["codex-plan.md", "claude-plan.md", "sections/index.md"])
def test_existing_artifacts_without_config_remain_legacy(forge, capsys, tmp_path, artifact):
    planning = make_plan(tmp_path / "plan")
    if artifact != "sections/index.md":
        (planning / artifact).write_text((planning / "sections" / f"{SECTION}.md").read_text())
    before = {path: path.read_bytes() for path in planning.rglob("*") if path.is_file()}
    code, payload = invoke(forge, capsys, "plan-setup", "--file", str(planning / "spec.md"), "--flight", "off")
    assert code == 0, payload
    assert "compatibility_policy" not in json.loads(Path(payload["config_path"]).read_text())
    assert {path: path.read_bytes() for path in before} == before


def test_detached_setup_has_no_mutable_policy_or_scaffold(forge, capsys, tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text("REQ-001: Add label normalization.\n")
    code, payload = invoke(forge, capsys, "plan-setup", "--file", str(spec), "--for-detached", "--flight", "off")
    assert code == 0, payload
    assert "compatibility_policy" not in json.loads(Path(payload["config_path"]).read_text())
    assert payload["scaffold"]["created"] == [] and not (tmp_path / "sections").exists()


@pytest.mark.parametrize("boundary", ["legacy-heading", "declared-policy"])
def test_detached_preflight_retains_its_existing_section_contract(forge, tmp_path, boundary):
    planning = documented_detached_plan(tmp_path / "physical", "lean")
    args = SimpleNamespace(implementation_root=str(tmp_path / "external"), depth="lean", profile="solo", flight_mode="strict")

    def preflight():
        return forge.flights.implement_preflight_report(planning / "sections", tmp_path, args, repo={"available": False})

    assert preflight()["success"]
    if boundary == "legacy-heading":
        path = planning / "sections" / f"{SECTION}.md"
        path.write_text(path.read_text() + "\n## Compatibility\nPreserve existing exported names and errors.\n")
    else:
        (planning / "zagrosi_plan_config.json").write_text(json.dumps({"compatibility_policy": "declared"}))
    before = {path: path.read_bytes() for path in planning.rglob("*") if path.is_file()}
    result = preflight()
    assert result["success"], result
    assert {path: path.read_bytes() for path in before} == before


def test_new_policy_requires_a_decision_even_with_legacy_prose(forge, capsys, tmp_path):
    planning = declared_plan(tmp_path / "plan")
    path = planning / "sections" / f"{SECTION}.md"
    path.write_text(path.read_text() + "\n## Compatibility\nPreserve public signatures and callers.\n")
    code, payload = invoke(forge, capsys, "lint-plan-artifacts", "--planning-dir", str(planning), "--strict")
    assert code == 1 and not payload["success"]
    assert "missing-compatibility" in {finding["code"] for finding in payload["findings"]}
