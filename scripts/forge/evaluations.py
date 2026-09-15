"""Forge evaluations."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import html
import json
import re

from . import artifacts as _artifacts
from . import output as _output
from . import quality as _quality
from . import scoring as _scoring
from . import sections as _sections
from . import storage as _storage
from . import traceability as _traceability

def grade_for_score(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    return "D"


def forge_score_row(planning_dir: Path, *, name: str, depth: str, profile: str) -> dict[str, Any]:
    findings, components, score = _scoring.forge_score_analysis(planning_dir, depth, profile)
    return {
        "name": name,
        "planning_dir": str(planning_dir),
        "depth_mode": depth,
        "forge_score": score,
        "grade": grade_for_score(score),
        "components": components,
        "findings": len(findings),
    }


def snapshot_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "planning_dir_name": Path(row["planning_dir"]).name,
        "forge_score": row["forge_score"],
        "grade": row["grade"],
        "components": row["components"],
    }


def snapshot_filename(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-") or "benchmark"
    return f"{slug}-forge-score.json"


def eval_fixture(planning_dir: Path, default_depth: str) -> dict[str, Any] | None:
    plan = _artifacts.implementation_plan_path(planning_dir)
    compact = _artifacts.compact_plan_descriptor(planning_dir)
    if not plan or not plan.is_file() or not _storage.read_text(plan).strip() or (compact and compact["errors"]):
        return None
    return {
        "name": planning_dir.name,
        "planning_dir": planning_dir,
        "depth": compact["depth_mode"] if compact else default_depth,
    }


def eval_suite_benchmarks(root: Path, default_depth: str) -> tuple[str, Path | None, Path | None, list[dict[str, Any]], list[dict[str, str]]]:
    if not root.exists():
        return "missing", None, None, [], [{"name": root.name or "examples", "error": "examples_dir does not exist"}]
    if not root.is_dir():
        return "missing", None, None, [], [{"name": root.name or "examples", "error": "examples_dir is not a directory"}]

    suite_path = root / "evals" / "suite.json"
    if not suite_path.exists():
        markers = sorted(path for pattern in ("**/codex-plan.md", "**/claude-plan.md", "**/sections/index.md") for path in root.glob(pattern))
        benchmarks = []
        seen: set[Path] = set()
        for marker in markers:
            planning_dir = marker.parent.parent if marker.name == "index.md" else marker.parent
            if planning_dir in seen or "invalid" in marker.relative_to(root).parts:
                continue
            fixture = eval_fixture(planning_dir, default_depth)
            if fixture:
                benchmarks.append(fixture)
                seen.add(planning_dir)
        benchmarks.sort(key=lambda item: str(_artifacts.implementation_plan_path(item["planning_dir"])))
        if not benchmarks:
            return "glob", None, None, [], [{"name": root.name or "examples", "error": "No benchmark planning fixtures found"}]
        return "glob", None, None, benchmarks, []

    try:
        suite = _storage.load_json(suite_path)
    except json.JSONDecodeError as exc:
        return "suite", suite_path, None, [], [{"name": "suite.json", "error": f"Invalid JSON: {exc}"}]

    if not isinstance(suite, dict):
        return "suite", suite_path, None, [], [{"name": "suite.json", "error": "suite must be an object"}]
    snapshots_value = suite.get("snapshots_dir", "golden")
    if not isinstance(snapshots_value, str) or not snapshots_value.strip():
        return "suite", suite_path, None, [], [{"name": "suite.json", "error": "snapshots_dir must be a nonempty path"}]
    snapshots_dir = suite_path.parent / snapshots_value
    raw_benchmarks = suite.get("benchmarks")
    if not isinstance(raw_benchmarks, list):
        return "suite", suite_path, snapshots_dir, [], [{"name": "suite.json", "error": "benchmarks must be a list"}]

    benchmarks: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for index, raw in enumerate(raw_benchmarks, start=1):
        if not isinstance(raw, dict):
            errors.append({"name": f"benchmark-{index}", "error": "benchmark row must be an object"})
            continue
        name = str(raw.get("name") or f"benchmark-{index}")
        planning_value = raw.get("planning_dir")
        if not isinstance(planning_value, str) or not planning_value.strip():
            errors.append({"name": name, "error": "planning_dir is required"})
            continue
        planning_dir = (suite_path.parent / planning_value).resolve()
        fixture = eval_fixture(planning_dir, default_depth)
        if fixture is None:
            errors.append({"name": name, "planning_dir": str(planning_dir), "error": "planning_dir does not contain a valid, nonempty implementation plan"})
            continue
        benchmarks.append(
            {
                "name": name,
                "planning_dir": planning_dir,
                "depth": str(raw.get("depth") or fixture["depth"]),
            }
        )
    if not benchmarks and not errors:
        errors.append({"name": "suite.json", "error": "benchmarks list is empty"})
    return "suite", suite_path, snapshots_dir, benchmarks, errors


def evaluate_snapshots(rows: list[dict[str, Any]], snapshots_dir: Path | None, *, check: bool, update: bool) -> tuple[dict[str, Any], bool]:
    summary: dict[str, Any] = {
        "checked": [],
        "matched": [],
        "missing": [],
        "drifted": [],
        "updated": [],
    }
    if not check and not update:
        return summary, True
    if snapshots_dir is None:
        return summary, True
    if update:
        snapshots_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for row in rows:
        name = str(row["name"])
        snapshot = snapshots_dir / snapshot_filename(name)
        expected = snapshot_payload(row)
        if update:
            snapshot.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            summary["updated"].append(name)
            continue
        summary["checked"].append(name)
        if not snapshot.exists():
            summary["missing"].append({"name": name, "snapshot": str(snapshot)})
            ok = False
            continue
        try:
            actual = _storage.load_json(snapshot)
        except json.JSONDecodeError as exc:
            summary["drifted"].append({"name": name, "snapshot": str(snapshot), "error": f"Invalid JSON: {exc}"})
            ok = False
            continue
        if actual != expected:
            summary["drifted"].append({"name": name, "snapshot": str(snapshot), "expected": expected, "actual": actual})
            ok = False
        else:
            summary["matched"].append(name)
    return summary, ok


def eval_suite(args: argparse.Namespace) -> int:
    root = _storage.resolve_path(args.examples_dir)
    discovery_mode, suite_path, snapshots_dir, benchmarks, errors = eval_suite_benchmarks(root, args.depth)
    if errors:
        return _output.print_json(
            {
                "success": False,
                "examples_dir": str(root),
                "depth_mode": args.depth,
                "profile": args.profile,
                "discovery_mode": discovery_mode,
                "suite_path": str(suite_path) if suite_path else None,
                "suite_errors": errors,
                "rows": [],
            },
            1,
        )

    rows = [
        forge_score_row(item["planning_dir"], name=item["name"], depth=item["depth"], profile=args.profile)
        for item in benchmarks
    ]
    snapshot_summary, snapshots_ok = evaluate_snapshots(
        rows,
        snapshots_dir,
        check=getattr(args, "check_snapshots", False),
        update=getattr(args, "update_snapshots", False),
    )
    payload = {
        "success": snapshots_ok,
        "examples_dir": str(root),
        "depth_mode": args.depth,
        "profile": args.profile,
        "discovery_mode": discovery_mode,
        "suite_path": str(suite_path) if suite_path else None,
        "snapshots_dir": str(snapshots_dir) if snapshots_dir else None,
        "snapshot_summary": snapshot_summary,
        "rows": rows,
    }
    if args.output:
        output = _storage.resolve_path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        payload["output"] = str(output)
    return _output.print_json(payload, 0 if snapshots_ok else 1)


def html_report(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    plan_findings, _ = _scoring.plan_findings_for_score(planning_dir, args.depth)
    section_findings, _ = _scoring.section_findings_for_score(planning_dir, args.depth)
    trace_findings, trace = _traceability.traceability_analysis(planning_dir)
    evidence_findings = _scoring.evidence_findings_for_score(planning_dir, 3)
    readiness_findings = _scoring.readiness_findings_for_score(planning_dir, 8)
    all_findings = plan_findings + section_findings + trace_findings + evidence_findings + readiness_findings
    components = {
        "Plan": _quality.quality_score(plan_findings, args.profile),
        "Sections": _quality.quality_score(section_findings, args.profile),
        "Traceability": _quality.quality_score(trace_findings, args.profile),
        "Evidence": _quality.quality_score(evidence_findings, args.profile),
        "Readiness": _quality.quality_score(readiness_findings, args.profile),
    }
    score = round(sum(components.values()) / len(components))
    rows = "".join(f"<tr><th>{html.escape(name)}</th><td>{value}</td></tr>" for name, value in components.items())
    findings_html = "".join(
        f"<li><strong>{html.escape(item.severity)}</strong> {html.escape(item.code)}: {html.escape(item.message)}</li>"
        for item in all_findings
    ) or "<li>No findings.</li>"
    coverage_html = "".join(
        f"<tr><td>{html.escape(req)}</td><td>{html.escape(str(data['covered']))}</td><td>{html.escape(', '.join(data['sections']))}</td></tr>"
        for req, data in trace.get("coverage", {}).items()
    )
    content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Zagrosi Forge Report</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; line-height: 1.45; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid #ccc; padding: 0.5rem; text-align: left; }}
    .score {{ font-size: 2rem; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>Zagrosi Forge Report</h1>
  <p>Planning directory: <code>{html.escape(str(planning_dir))}</code></p>
  <p class="score">Forge Score: {score}</p>
  <h2>Components</h2>
  <table>{rows}</table>
  <h2>Traceability</h2>
  <table><tr><th>Requirement</th><th>Covered</th><th>Sections</th></tr>{coverage_html}</table>
  <h2>Findings</h2>
  <ul>{findings_html}</ul>
</body>
</html>
"""
    output = _storage.resolve_path(args.output) if args.output else planning_dir / ".forge" / "report.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    return _output.print_json({"success": True, "planning_dir": str(planning_dir), "output": str(output), "forge_score": score})


def e2e_trial_record(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    progress = _sections.check_section_progress(planning_dir)
    score_findings = (
        _scoring.plan_findings_for_score(planning_dir, args.depth)[0]
        + _scoring.section_findings_for_score(planning_dir, args.depth)[0]
        + _traceability.traceability_analysis(planning_dir)[0]
        + _scoring.evidence_findings_for_score(planning_dir, 3)
        + _scoring.readiness_findings_for_score(planning_dir, 8)
    )
    score = _quality.quality_score(score_findings, args.profile)
    record = {
        "timestamp": _storage.now_iso(),
        "trial_name": args.name,
        "planning_dir": str(planning_dir),
        "target_repo": args.target_repo,
        "depth_mode": args.depth,
        "profile": args.profile,
        "forge_score": score,
        "section_progress": progress.get("progress"),
        "notes": args.notes,
        "metrics": {
            "time_to_plan_minutes": args.time_to_plan_minutes,
            "implementation_success": args.implementation_success,
            "rework_notes": args.rework_notes,
        },
    }
    output_dir = _storage.resolve_path(args.output_dir) if args.output_dir else planning_dir / ".forge" / "trials"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{re.sub(r'[^a-zA-Z0-9_.-]+', '-', args.name).strip('-') or 'trial'}.json"
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _output.print_json({"success": True, "output": str(output), "record": record})
