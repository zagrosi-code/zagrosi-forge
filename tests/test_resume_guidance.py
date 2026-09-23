"""Resume and delegated instructions preserve executable workflow constraints."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from detached_test_support import make_detached_record_fixture, tree_bytes_metadata_snapshot
from forge_test_helpers import ROOT, run_cmd, run_raw, write_non_topological_section_fixture
from test_compact_plan import SECTION, make_plan


def documented_detached_plan(path: Path, depth: str) -> Path:
    reference = ROOT / "skills/zagrosi-plan/references/detached-plan-format.md"
    artifacts = re.findall(r"^### `([^`]+)`\n\n```markdown\n(.*?)^```", reference.read_text(), re.M | re.S)
    assert {name for name, _ in artifacts} == {
        "spec.md", "codex-plan.md", "reviews/codex.md", "sections/index.md", "sections/section-01-normalize.md",
    }
    for name, content in artifacts:
        output = path / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content.replace('"depth_mode":"lean"', f'"depth_mode":"{depth}"'))
    return path


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_documented_detached_plan_passes_real_artifact_gates(tmp_path, depth):
    planning = documented_detached_plan(tmp_path / "plan", depth)
    before = {p.relative_to(planning): p.read_bytes() for p in planning.rglob("*") if p.is_file()}
    postflight = run_cmd("postflight", "--phase", "plan", "--planning-dir", str(planning), "--depth", depth, "--strict")
    assert postflight["success"]
    payload = run_cmd("lint-plan-artifacts", "--planning-dir", str(planning), "--strict", "--for-detached")
    assert payload["success"]
    assert payload["depth_mode"] == depth
    assert before == {p.relative_to(planning): p.read_bytes() for p in planning.rglob("*") if p.is_file()}


def test_pre_freeze_gate_rejects_compact_and_missing_physical_review(tmp_path):
    compact = make_plan(tmp_path / "compact")
    result = run_raw("lint-plan-artifacts", "--planning-dir", str(compact), "--for-detached", "--strict")
    assert result.returncode == 1
    assert "compact-plan-not-supported" in {f["code"] for f in json.loads(result.stdout)["findings"]}
    physical = documented_detached_plan(tmp_path / "physical", "lean")
    (physical / "reviews/codex.md").unlink()
    result = run_raw("lint-plan-artifacts", "--planning-dir", str(physical), "--for-detached", "--strict")
    assert result.returncode == 1
    assert "missing-review" in {f["code"] for f in json.loads(result.stdout)["findings"]}


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_status_blocks_unadmitted_compact_plan_without_setup_config(tmp_path, depth):
    planning = make_plan(tmp_path / "plan", depth)
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace("Verdict: pass", "Verdict: blocked"))
    payload = run_cmd("status", "--path", str(section))
    assert payload["planning_dir"] == str(planning)
    assert payload["admission"]["success"] is False
    assert payload["next_action"] == "repair planning admission findings before implementation"


def test_status_uses_dependency_readiness_and_exposes_blocked_work(tmp_path):
    planning = tmp_path / "plan"
    sections = write_non_topological_section_fixture(planning)
    state = planning / "implementation/zagrosi_implement_state.json"
    state.parent.mkdir()
    state.write_text('{"completed_sections":{}}')
    payload = run_cmd("status", "--path", str(sections))
    assert payload["next_action"] == "implement section-03-storage"
    assert payload["ready_sections"] == ["section-03-storage"]
    index = sections / "index.md"
    index.write_text(index.read_text().replace("| section-03-storage | none |", "| section-03-storage | section-02-api |"))
    payload = run_cmd("status", "--path", str(planning))
    assert payload["next_action"] == "resolve blocked section dependencies"
    assert payload["remaining_sections"]
    assert payload["ready_sections"] == []


def test_status_prioritizes_pending_verification_retry(tmp_path):
    planning = make_plan(tmp_path / "plan")
    state = planning / "implementation/zagrosi_implement_state.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"completed_sections": {}, "pending_sections": {SECTION: {"failed_postflight": {"success": False}}}}))
    payload = run_cmd("status", "--path", str(planning))
    assert payload["pending_sections"] == [SECTION]
    assert payload["next_action"] == f"resolve pending verification and retry recording {SECTION} with --flight strict"


def test_status_accepts_mutable_implementation_state_path(tmp_path):
    planning = make_plan(tmp_path / "plan")
    state = planning / "implementation/zagrosi_implement_state.json"
    state.parent.mkdir()
    state.write_text('{"completed_sections":{}}')
    (state.parent / "zagrosi_implement_config.json").write_text(json.dumps({"planning_dir": str(planning)}))
    payload = run_cmd("status", "--path", str(state))
    assert payload["planning_dir"] == str(planning)
    assert payload["next_action"] == f"implement {SECTION}"


def test_detached_status_returns_authority_check_without_recovery(tmp_path):
    fixture = make_detached_record_fixture(tmp_path)
    before = tree_bytes_metadata_snapshot(fixture.implementation_root)
    for path in (fixture.implementation_root, fixture.implementation_root / "zagrosi_implement_state.json"):
        payload = run_cmd("status", "--path", str(path))
        assert payload["mode"] == "detached-frozen"
        assert payload["readiness_verified"] is False
        argv = shlex.split(payload["next_command"])
        assert argv[2:] == ["next-section", "--planning-dir", str(fixture.planning), "--implementation-root", str(fixture.implementation_root)]
    assert before == tree_bytes_metadata_snapshot(fixture.implementation_root)
    result = subprocess.run(argv, capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["next_section"] == fixture.section


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_section_prompts_keep_investigation_depth_and_short_guidance(tmp_path, depth):
    planning = make_plan(tmp_path / "plan", depth)
    payload = run_cmd("plan-generate-section-prompts", "--planning-dir", str(planning), "--all")
    text = Path(payload["prompt_files"][0]).read_text()
    assert f"Apply `{depth}` investigation/review depth:" in text
    assert str(ROOT / "skills/zagrosi-plan/references/depth-standards.md") in text
    assert len(text.split()) <= 300


def test_update_command_preserves_custom_config_from_another_directory(tmp_path):
    config = tmp_path / "custom config's" / "config.toml"
    payload = run_cmd("update-check", "--plugin-root", str(ROOT), "--config", str(config), cwd=tmp_path)
    instruction = payload["next_steps"][0]
    command = instruction.removeprefix("Run ").removesuffix(" to refresh Codex config and plugin cache.")
    argv = shlex.split(command)
    assert Path(argv[1]).is_absolute()
    assert argv[argv.index("--plugin-root") + 1] == str(ROOT)
    assert argv[argv.index("--config") + 1] == str(config)
    result = subprocess.run([*argv, "--dry-run"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    preview = json.loads(result.stdout)
    assert preview["config_path"] == str(config)
    assert preview["dry_run"] is True
    assert not config.exists()
