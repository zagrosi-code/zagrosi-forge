"""Forge traceability."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import re

from . import artifacts as _artifacts
from . import markdown as _markdown
from . import models as _models
from . import planning_contract as _contract
from . import quality as _quality
from . import state as _state
from . import storage as _storage

def traceability_analysis(planning_dir: Path) -> tuple[list[_models.Finding], dict[str, Any]]:
    spec_path = _artifacts.requirement_source_spec(planning_dir)
    plan_path = _artifacts.implementation_plan_path(planning_dir)
    tdd_path = _artifacts.planning_artifacts(planning_dir)["tdd"]
    sections_dir = planning_dir / "sections"

    plan_text = _markdown.visible_markdown(_storage.read_text(plan_path)) if plan_path else ""
    tdd_text = _artifacts.planning_artifact_text(planning_dir, "tdd", tdd_path)
    section_files = sorted(sections_dir.glob("section-*.md")) if sections_dir.exists() else []
    section_text_by_file = {path.name: _storage.read_text(path) for path in section_files}
    req_ids, _ = _contract.requirements(planning_dir)

    plan_ids = set(_markdown.requirement_ids(plan_text))
    tdd_ids = set(_markdown.requirement_ids(_markdown.visible_markdown(tdd_text)))
    verified_tdd = _markdown.has_verification(tdd_text)
    section_ids = {name: set(_markdown.requirement_ids(_markdown.visible_markdown(text))) for name, text in section_text_by_file.items()}
    tested_sections = {
        name for name, text in section_text_by_file.items()
        if _markdown.has_verification(text)
    }
    coverage: dict[str, Any] = {}
    for req_id in req_ids:
        sections = [name for name, ids in section_ids.items() if req_id in ids]
        section_tests = [name for name in sections if name in tested_sections]
        in_tdd = req_id in tdd_ids and verified_tdd or bool(section_tests)
        coverage[req_id] = {
            "in_plan": req_id in plan_ids,
            "in_tdd": in_tdd,
            "sections": sections,
            "section_tests": section_tests,
            "covered": bool(req_id in plan_ids and in_tdd and sections),
        }

    uncovered = [req_id for req_id, item in coverage.items() if not item["covered"]]
    findings = _artifacts.compact_plan_findings(planning_dir) + [
        _quality.finding("high", "traceability-gap", f"{req_id} is not fully covered.", spec_path or planning_dir)
        for req_id in uncovered
    ]
    if not req_ids:
        findings.append(_quality.finding("medium", "no-requirement-ids", "No visible source requirements or source-linked canonical contract found.", spec_path or planning_dir))

    section_orphans = [
        name
        for name, ids in section_ids.items()
        if not ids.intersection(req_ids)
    ]
    if section_orphans:
        findings.append(
            _quality.finding(
                "medium",
                "orphan-sections",
                f"Section files do not reference known requirements: {', '.join(section_orphans)}",
                sections_dir,
            )
        )

    test_orphans = []
    if tdd_text and _markdown.contains_any(tdd_text, ["test_", "it(", "describe(", "pytest"]) and not tdd_ids:
        test_orphans.append(tdd_path.name if tdd_path else "codex-plan-tdd.md")
        findings.append(
            _quality.finding(
                "medium",
                "orphan-tests",
                "TDD plan names tests but does not tie them to REQ-* IDs.",
                tdd_path or planning_dir,
            )
        )

    extras = {
        "planning_dir": str(planning_dir),
        "requirement_ids": req_ids,
        "coverage": coverage,
        "implementation_evidence": _state.implementation_evidence_by_section(planning_dir),
        "orphans": {
            "sections": section_orphans,
            "tests": test_orphans,
        },
    }
    return findings, extras


def existing_traceability_cells(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, str]] = {}
    header: list[str] | None = None
    for line in _storage.read_text(path).splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
            continue
        if header is None:
            header = cells
            continue
        if len(cells) != len(header) or not cells:
            continue
        row = dict(zip(header, cells, strict=False))
        requirement = row.get("Requirement")
        if requirement:
            rows[requirement] = row
    return rows


def requirement_implementation_status(item: dict[str, Any], completed: set[str]) -> str:
    if not item.get("covered"):
        return "Gap"
    section_names = {Path(section).stem for section in item.get("sections", [])}
    if section_names and section_names.issubset(completed):
        return "Implemented"
    if section_names.intersection(completed):
        return "Partially implemented"
    return "Planned"


def traceability_matrix_content(planning_dir: Path) -> str | None:
    findings, extras = traceability_analysis(planning_dir)
    coverage = extras.get("coverage", {})
    if not coverage:
        return None
    existing = existing_traceability_cells(planning_dir / "traceability.md")
    completed = _state.completed_sections(planning_dir)
    evidence = _state.implementation_evidence_by_section(planning_dir)
    artifacts = _artifacts.planning_artifacts(planning_dir)
    lines = [
        "# Traceability Matrix",
        "",
        "| Requirement | Plan Coverage | Section Coverage | Test Coverage | Implementation Evidence | Status |",
        "|-------------|---------------|------------------|---------------|-------------------------|--------|",
    ]
    for req_id, item in coverage.items():
        previous = existing.get(req_id, {})
        plan_path = artifacts["plan"]
        plan_reference = f"`{plan_path.relative_to(planning_dir).as_posix()}`" if plan_path and item.get("in_plan") else "-"
        plan_coverage = previous.get("Plan Coverage") or plan_reference
        sections = "; ".join(f"`{section}`" for section in item.get("sections", [])) or previous.get("Section Coverage") or "-"
        tdd_path = artifacts["tdd"]
        test_reference = (
            f"`{tdd_path.relative_to(planning_dir).as_posix()}`" if tdd_path and item.get("in_tdd")
            else "; ".join(f"`sections/{name}`" for name in item.get("section_tests", [])) or "-"
        )
        test_coverage = previous.get("Test Coverage") or test_reference
        evidence_items = [
            _state.compact_section_evidence(evidence[Path(section).stem])
            for section in item.get("sections", [])
            if Path(section).stem in evidence
        ]
        implementation_evidence = "; ".join(item for item in evidence_items if item and item != "-") or previous.get("Implementation Evidence") or "-"
        status = requirement_implementation_status(item, completed)
        lines.append(f"| {req_id} | {plan_coverage} | {sections} | {test_coverage} | {implementation_evidence} | {status} |")
    if findings:
        lines.extend(["", "Open traceability findings:"])
        lines.extend(f"- {item.severity}: {item.code} - {item.message}" for item in findings)
    return "\n".join(lines) + "\n"


def refresh_traceability_matrix(planning_dir: Path) -> Path | None:
    content = traceability_matrix_content(planning_dir)
    if content is None:
        return None
    path = planning_dir / "traceability.md"
    path.write_text(content, encoding="utf-8")
    return path


def traceability(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    findings, extras = traceability_analysis(planning_dir)
    payload = _quality.quality_from_args(
        "traceability",
        findings,
        args,
        extras,
    )
    return _quality.emit_payload(payload, args)
