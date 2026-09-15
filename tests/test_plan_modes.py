from __future__ import annotations

import json
from pathlib import Path

import pytest
from forge_test_helpers import (
    ROOT,
    load_zagrosi_module,
    run_cmd,
    run_raw,
    run_text,
    write_lean_plan_fixture,
    write_quality_plan_fixture,
)


def test_plan_setup_defaults_to_lean_without_governance_stubs(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec\n\nREQ-001: Compact planning.\n")

    payload = run_cmd("plan-setup", "--file", str(spec), "--plugin-root", str(ROOT), "--flight", "off")

    assert payload["depth_mode"] == "lean"
    assert not (tmp_path / "decisions.md").exists()
    assert not (tmp_path / "risk-register.md").exists()
    assert not (tmp_path / "traceability.md").exists()
    assert not (tmp_path / "quality-gates.md").exists()


def test_lean_plan_accepts_minimal_dense_artifacts(tmp_path: Path) -> None:
    planning = write_lean_plan_fixture(tmp_path)

    artifacts = run_cmd("lint-plan-artifacts", "--planning-dir", str(planning), "--strict")
    plan = run_cmd("lint-plan", "--planning-dir", str(planning), "--depth", "lean", "--strict")
    sections = run_cmd("lint-sections", "--planning-dir", str(planning), "--depth", "lean", "--strict")
    trace = run_cmd("traceability", "--planning-dir", str(planning), "--strict")

    assert artifacts["success"] is True
    assert artifacts["required_artifacts"] == ["plan", "source_spec"]
    assert plan["success"] is True
    assert sections["success"] is True
    assert trace["coverage"]["REQ-001"]["covered"] is True


@pytest.mark.parametrize("depth", ["lean", "fast"])
def test_lean_and_fast_reject_bloat_not_brevity(tmp_path: Path, depth: str) -> None:
    planning = write_lean_plan_fixture(tmp_path)
    plan_path = planning / "codex-plan.md"
    plan_path.write_text(plan_path.read_text() + (" filler" * 1000))

    result = run_raw("lint-plan", "--planning-dir", str(planning), "--depth", depth, "--strict")

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert "plan-too-large" in {item["code"] for item in payload["findings"]}
    assert not any(item["code"].endswith("too-thin") for item in payload["findings"])


@pytest.mark.parametrize("source_name", ["spec.md", "codex-spec.md"])
def test_lean_plan_does_not_budget_the_unchanged_source_spec(tmp_path: Path, source_name: str) -> None:
    planning = write_lean_plan_fixture(tmp_path)
    source = planning / "spec.md"
    if source_name != source.name:
        renamed = planning / source_name
        source.rename(renamed)
        source = renamed
        config_path = planning / "zagrosi_plan_config.json"
        config = json.loads(config_path.read_text())
        config["initial_file"] = str(source)
        config_path.write_text(json.dumps(config))
    source.write_text(source.read_text() + (" detailed requirement" * 1000))

    result = run_raw("lint-plan", "--planning-dir", str(planning), "--depth", "lean", "--strict")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "spec-too-large" not in {item["code"] for item in json.loads(result.stdout)["findings"]}


def test_lean_section_prompts_are_bounded_and_reference_context(tmp_path: Path) -> None:
    planning = write_lean_plan_fixture(tmp_path)

    payload = run_cmd(
        "plan-generate-section-prompts",
        "--planning-dir",
        str(planning),
        "--depth",
        "lean",
        "--all",
    )
    prompt = Path(payload["prompt_files"][0]).read_text()

    assert "300 words max" in prompt
    assert "Do not copy" in prompt
    assert "1,000" not in prompt
    assert "1,500" not in prompt


def test_migrated_section_prompt_resolves_actual_artifacts(tmp_path: Path) -> None:
    planning = write_lean_plan_fixture(tmp_path)
    (planning / "codex-plan.md").rename(planning / "claude-plan.md")

    payload = run_cmd(
        "plan-generate-section-prompts",
        "--planning-dir",
        str(planning),
        "--depth",
        "lean",
        "--all",
    )
    prompt = Path(payload["prompt_files"][0]).read_text()

    assert str(planning / "claude-plan.md") in prompt
    assert str(planning / "spec.md") in prompt
    assert "codex-plan.md" not in prompt
    assert "zagrosi_plan_config.json" not in prompt


def test_default_json_output_is_compact() -> None:
    output = run_text("workflow-options", "--brief", "small change")

    assert "\n  \"" not in output
    assert len(output) < 5000


@pytest.mark.parametrize(
    ("depth", "expected_plan_budget"),
    [("lean", 800), ("fast", 800), ("standard", 2500), ("deep", 4000)],
)
def test_all_depths_have_upper_budgets_without_word_floors(
    tmp_path: Path,
    depth: str,
    expected_plan_budget: int,
) -> None:
    planning = write_lean_plan_fixture(tmp_path)

    result = run_raw("lint-plan", "--planning-dir", str(planning), "--depth", depth, "--strict")
    payload = json.loads(result.stdout)

    assert not any(item["code"].endswith("too-thin") for item in payload["findings"])
    assert payload["word_budgets"]["plan"] == expected_plan_budget


def test_lint_plan_never_requires_padding_short_optional_artifacts(tmp_path: Path) -> None:
    planning = write_quality_plan_fixture(tmp_path)
    review_file = planning / "reviews" / "architecture.md"
    review_file.parent.mkdir(exist_ok=True)
    review_file.write_text("# Architecture\n\nREQ-001: too short.\n")

    result = run_raw("lint-plan", "--planning-dir", str(planning), "--depth", "deep", "--strict")

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["word_counts"]["reviews"]["architecture.md"] == 5
    assert not any(item["code"].endswith("too-thin") for item in payload["findings"])


def test_planning_depth_defaults_lean_and_honors_configless_plan_metadata(tmp_path: Path) -> None:
    module = load_zagrosi_module()
    assert module.artifacts.planning_depth(tmp_path) == "lean"

    (tmp_path / "codex-plan.md").write_text(
        '<!-- FORGE_META\n{"artifact_type":"implementation_plan","depth_mode":"deep"}\nEND_FORGE_META -->\n'
    )
    assert module.artifacts.planning_depth(tmp_path) == "deep"

    (tmp_path / "zagrosi_plan_config.json").write_text('{"depth_mode":"standard"}\n')
    assert module.artifacts.planning_depth(tmp_path) == "standard"
