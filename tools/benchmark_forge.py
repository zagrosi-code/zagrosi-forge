#!/usr/bin/env python3
"""Compare Forge checkouts on identical disposable fixtures at every depth."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEPTHS = ("lean", "standard", "deep")
COMMANDS = ("status", "lint_plan", "forge_score", "context_brief", "plan_preflight", "implement_preflight", "plan_postflight")
META = re.compile(r"(<!--\s*FORGE_META\s*)(.*?)(\s*END_FORGE_META\s*-->)", re.S)


def fixture(source: Path, destination: Path, depth: str) -> tuple[Path, Path]:
    """Copy the same legacy fixture for every runtime; never modify a checkout."""
    planning = destination / "planning"
    shutil.copytree(source, planning)
    changed = 0
    for path in planning.rglob("*.md"):
        text = path.read_text()
        def update(match):
            nonlocal changed
            metadata = json.loads(match[2])
            metadata["depth_mode"] = depth
            changed += 1
            return match[1] + json.dumps(metadata, sort_keys=True) + match[3]
        path.write_text(META.sub(update, text))
    if not changed:
        raise ValueError("Benchmark fixture needs explicit FORGE_META depth metadata")
    if any((planning / name).exists() for name in ("zagrosi_plan_config.json", "deep_plan_config.json")):
        raise ValueError("Use an unconfigured fixture so metadata controls depth")
    target = destination / "target"
    for relative in ("src/auth/oauth.py", "src/auth/session.py", "src/auth/config.py", "tests/auth/test_oauth.py"):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('"""Benchmark target: file discovery only; no application execution."""\n')
    (target / "pyproject.toml").write_text('[project]\nname = "forge-benchmark-target"\nversion = "0.0.0"\n')
    return planning, target


def commands(root: Path, planning: Path, target: Path, depth: str) -> dict[str, list[str]]:
    # Flight commands resolve plan metadata. Deliberately conflict with it so
    # a mislabeled lean-only benchmark cannot silently masquerade as all-depth.
    conflicting_depth = "standard" if depth == "lean" else "lean"
    common = ["--depth", conflicting_depth]
    return {
        "status": ["status", "--path", str(planning)],
        "lint_plan": ["lint-plan", "--planning-dir", str(planning), "--strict"],
        "forge_score": ["forge-score", "--planning-dir", str(planning), "--depth", depth, "--strict"],
        "context_brief": ["context-brief", "--planning-dir", str(planning), "--section", "section-01-oauth-foundation"],
        "plan_preflight": ["preflight", "--phase", "plan", "--file", str(planning / "spec.md"), "--target-dir", str(target), "--plugin-root", str(root), *common],
        "implement_preflight": ["preflight", "--phase", "implement", "--sections-dir", str(planning / "sections"), "--target-dir", str(target), *common],
        "plan_postflight": ["postflight", "--phase", "plan", "--planning-dir", str(planning), "--flight", "strict", *common],
    }


def verify_depth(name: str, depth: str, payload: dict) -> None:
    if name in {"lint_plan", "forge_score"} and payload.get("depth_mode") != depth:
        raise RuntimeError(f"{name}: expected {depth}, got {payload.get('depth_mode')}")
    gates = {gate["name"] for gate in payload.get("gates", [])}
    expected_gate = {"plan_preflight": "codebase-evidence", "plan_postflight": "lint-evidence"}.get(name)
    if expected_gate and ((expected_gate in gates) != (depth != "lean")):
        raise RuntimeError(f"{name}: {depth} metadata did not select the expected gates: {sorted(gates)}")


def measure(root: Path, source: Path, name: str, depth: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="forge-benchmark-") as temporary:
        directory = Path(temporary).resolve()
        planning, target = fixture(source, directory, depth)
        command = commands(root, planning, target, depth)[name]
        start = time.perf_counter()
        result = subprocess.run([sys.executable, str(root / "scripts/zagrosi_skills.py"), *command],
                                cwd=target, capture_output=True, text=True, timeout=60)
        elapsed = (time.perf_counter() - start) * 1000
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{root.name}/{depth}/{name}: invalid JSON; {result.stderr[-1000:]}") from exc
        if result.returncode or payload.get("success") is not True:
            raise RuntimeError(f"{root.name}/{depth}/{name}: failed gate: {payload}")
        verify_depth(name, depth, payload)
        normalized = result.stdout.replace(str(root), "{root}").replace(str(directory), "{fixture}")
        return {"ms": elapsed, "stdout_bytes": len(normalized.encode()), "context_words": payload.get("word_count"),
                "depth_mode": payload.get("depth_mode"),
                "requested_depth": command[command.index("--depth") + 1] if "--depth" in command else None,
                "gates": [gate["name"] for gate in payload.get("gates", [])]}


def skill_loads(root: Path) -> dict:
    """Explicit reading scenarios, not model telemetry or all stored Markdown."""
    skills = root / "skills"
    engineering = "zagrosi-implement/references/engineering.md"
    scenarios = {
        "project": ["zagrosi-project/SKILL.md", engineering, *[f"zagrosi-project/references/{name}.md" for name in ("splitting", "manifest-format", "spec-format")]],
        "plan_lean": ["zagrosi-plan/SKILL.md", engineering, *[f"zagrosi-plan/references/{name}.md" for name in ("plan-format", "section-format", "review")]],
        "implement_mutable": ["zagrosi-implement/SKILL.md", engineering],
    }
    # Current lean instructions explicitly link research; previous entrypoints
    # made that reference conditional on wider investigation.
    if (skills / engineering).exists():
        scenarios["plan_lean"].append("zagrosi-plan/references/research.md")
    scenarios["plan_standard"] = [*scenarios["plan_lean"], "zagrosi-plan/references/depth-standards.md", "zagrosi-plan/references/research.md"]
    scenarios["plan_deep"] = scenarios["plan_standard"]
    scenarios["implement_detached"] = [*scenarios["implement_mutable"], "zagrosi-implement/references/detached-frozen.md"]
    loads = {}
    for name, candidates in scenarios.items():
        paths = sorted({relative for relative in candidates if (skills / relative).exists()})
        loads[name] = {"words": sum(len((skills / path).read_text().split()) for path in paths), "paths": paths}
    return {"method": "Modeled core reference loads, deduplicated per scenario; optional packs/interviews and diagnostic-only detached-protocol.md excluded. Extra source/code/plan context is workload-dependent.",
            "entry_words": sum(len(path.read_text().split()) for path in skills.glob("*/SKILL.md")), "scenarios": loads}


def source_identity(root: Path) -> dict:
    cli = root / "scripts/zagrosi_skills.py"
    runtime = sorted((root / "scripts/forge").glob("*.py"))
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True)
    return {"base_commit": revision.stdout.strip() if revision.returncode == 0 else None,
            "script_sha256": hashlib.sha256(cli.read_bytes()).hexdigest(),
            "runtime_sha256": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in runtime},
            "skill_loads": skill_loads(root)}


def fixture_identity(source: Path) -> dict:
    return {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(source.rglob("*")) if path.is_file()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline-root", type=Path, help="Earlier optimization snapshot")
    parser.add_argument("--original-root", type=Path, help="Unmodified main checkout")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    roots = {label: path.resolve() for label, path in (("original", args.original_root), ("baseline", args.baseline_root), ("current", args.root)) if path}
    source = args.root.resolve() / "examples/saas/01-authentication"
    fixture_hashes = fixture_identity(source)
    identities = {label: source_identity(root) for label, root in roots.items()}
    results = {label: {depth: {} for depth in DEPTHS} for label in roots}
    for depth in DEPTHS:
        for name in COMMANDS:
            samples = {label: [] for label in roots}
            for label, root in roots.items():
                measure(root, source, name, depth)  # Unmeasured warmup, including gate/depth validation.
            for _ in range(args.runs):
                for label, root in roots.items():
                    samples[label].append(measure(root, source, name, depth))
            for label, rows in samples.items():
                if any({k: v for k, v in row.items() if k != "ms"} != {k: v for k, v in rows[0].items() if k != "ms"} for row in rows):
                    raise RuntimeError(f"Unstable output for {label}/{depth}/{name}")
                results[label][depth][name] = {"median_ms": round(statistics.median(row["ms"] for row in rows), 2),
                                              **{key: value for key, value in rows[0].items() if key != "ms"}}
    for label, root in roots.items():
        if source_identity(root) != identities[label]:
            raise RuntimeError(f"{label} sources changed during benchmark; rerun on stable bytes")
    if fixture_identity(source) != fixture_hashes:
        raise RuntimeError("Fixture changed during benchmark; rerun on stable bytes")
    report = {"python": platform.python_version(), "platform": platform.system(), "runs": args.runs,
              "method": "Identical legacy SaaS fixture per depth; fresh temporary copies and tiny target per invocation; fixture setup excluded; one warmup; interleaved runtimes; median wall time; no application tests executed. Flights receive conflicting CLI depth and must select metadata depth. Paths normalized in payload sizes. Skill loads are explicit scenarios, not live model telemetry.",
              "fixture_sha256": fixture_hashes,
              "results": {label: {**identities[label], "depths": results[label]} for label in roots}}
    content = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    print(content, end="")


if __name__ == "__main__":
    main()
