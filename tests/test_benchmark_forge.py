"""Keep benchmark claims tied to isolated inputs and actual depth selection."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("forge_benchmark_tests", ROOT / "tools/benchmark_forge.py")
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


@pytest.mark.parametrize("depth", benchmark.DEPTHS)
def test_fixture_sets_metadata_without_changing_original(tmp_path, depth):
    source = ROOT / "examples/saas/01-authentication"
    before = benchmark.fixture_identity(source)
    planning, target = benchmark.fixture(source, tmp_path, depth)
    metadata = json.loads(benchmark.META.search((planning / "codex-plan.md").read_text())[2])
    assert metadata["depth_mode"] == depth
    assert benchmark.fixture_identity(source) == before
    assert (target / "src/auth/oauth.py").is_file()
    commands = benchmark.commands(ROOT, planning, target, depth)
    assert "--depth" not in commands["lint_plan"]  # Exercise metadata inference.
    for name in ("plan_preflight", "implement_preflight", "plan_postflight"):
        assert commands[name][commands[name].index("--depth") + 1] != depth


@pytest.mark.parametrize("name,gate", [("plan_preflight", "codebase-evidence"), ("plan_postflight", "lint-evidence")])
@pytest.mark.parametrize("depth", benchmark.DEPTHS)
def test_depth_validation_refuses_mislabeled_gate_branches(name, gate, depth):
    right = [] if depth == "lean" else [{"name": gate}]
    wrong = [{"name": gate}] if depth == "lean" else []
    benchmark.verify_depth(name, depth, {"gates": right})
    with pytest.raises(RuntimeError, match="expected gates"):
        benchmark.verify_depth(name, depth, {"gates": wrong})


def test_measure_discards_gate_writes_and_normalizes_paths(monkeypatch):
    source = ROOT / "examples/saas/01-authentication"
    before = benchmark.fixture_identity(source)
    observed = []

    def run(argv, **kwargs):
        planning = Path(argv[argv.index("--path") + 1])
        (planning / "gate-wrote-this.txt").write_text("mutable gate output")
        observed.append(planning)
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"success": True, "planning_dir": str(planning)}))

    monkeypatch.setattr(benchmark.subprocess, "run", run)
    first = benchmark.measure(ROOT, source, "status", "deep")
    second = benchmark.measure(ROOT, source, "status", "deep")
    assert first["stdout_bytes"] == second["stdout_bytes"]
    assert observed[0] != observed[1]
    assert not any(path.exists() for path in observed)
    assert benchmark.fixture_identity(source) == before


def test_load_scenarios_exclude_archived_protocol_and_deduplicate_references():
    loads = benchmark.skill_loads(ROOT)
    for scenario in loads["scenarios"].values():
        paths = scenario["paths"]
        assert len(paths) == len(set(paths))
        assert not any("detached-protocol.md" in path for path in paths)
        assert scenario["words"] == sum(len((ROOT / "skills" / path).read_text().split()) for path in paths)
    assert "zagrosi-implement/references/detached-frozen.md" in loads["scenarios"]["implement_detached"]["paths"]
    assert "zagrosi-implement/references/detached-frozen.md" not in loads["scenarios"]["implement_mutable"]["paths"]


def test_existing_config_cannot_silently_override_benchmark_metadata(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "codex-plan.md").write_text('<!-- FORGE_META\n{"depth_mode":"lean"}\nEND_FORGE_META -->\n')
    (source / "zagrosi_plan_config.json").write_text('{"depth_mode":"lean"}')
    with pytest.raises(ValueError, match="metadata controls depth"):
        benchmark.fixture(source, tmp_path / "copy", "deep")
