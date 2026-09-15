"""Forge validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse

from . import artifacts as _artifacts
from . import markdown as _markdown
from . import models as _models
from . import ownership as _ownership
from . import policy as _policy
from . import projects as _projects
from . import quality as _quality
from . import sections as _sections
from . import storage as _storage

def lint_plan(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    depth = args.depth or _artifacts.planning_depth(planning_dir)
    compact = _markdown.is_lean_depth(depth)
    budgets = _markdown.word_budgets(depth)
    findings = _artifacts.compact_plan_findings(planning_dir, depth=depth)

    spec_path = _artifacts.requirement_source_spec(planning_dir)
    plan_path = _artifacts.implementation_plan_path(planning_dir)
    tdd_path = _artifacts.planning_artifacts(planning_dir)["tdd"]

    if not plan_path:
        findings.append(_quality.finding("critical", "missing-plan", "Implementation plan is missing.", planning_dir / "codex-plan.md"))
        return _quality.emit_quality("plan", findings, args)

    interview_extras: dict[str, Any] = {"mode": "optional"}
    if _artifacts.interview_artifact(planning_dir, "plan"):
        interview_gate_findings, interview_extras = _projects.interview_findings(planning_dir, "plan")
        findings.extend(interview_gate_findings)

    plan_text = _storage.read_text(plan_path)
    descriptor = _artifacts.compact_plan_descriptor(planning_dir)
    meta, meta_errors = _markdown.parse_forge_meta(_storage.read_text(descriptor["marker_path"]) if descriptor else plan_text)
    for error in meta_errors:
        findings.append(_quality.finding("low", "metadata", error, plan_path))
    if meta and meta.get("artifact_type") not in {"implementation_plan", "compact_plan"}:
        findings.append(_quality.finding("medium", "metadata-type", "FORGE_META artifact_type should be implementation_plan.", plan_path))

    plan_words = _markdown.word_count(plan_text)
    _quality.add_budget_finding(findings, plan_words, budgets["plan"], "Implementation plan", "plan-too-large", plan_path)
    _quality.require_terms(findings, plan_text, _policy.LEAN_PLAN_TERMS, plan_path, "medium")
    if not compact:
        _quality.require_terms(
            findings,
            plan_text,
            {
                "rationale": ["why", "rationale", "tradeoff", "alternative"],
                "security-privacy": ["security", "privacy", "permission", "auth"],
                "migration": ["migration", "schema", "data migration", "backward", "compatibility"],
            },
            plan_path,
        )
    if not _policy.FILE_PATH_RE.search(plan_text):
        findings.append(_quality.finding("high", "no-file-paths", "Plan does not name concrete files or paths.", plan_path))

    if not spec_path:
        findings.append(
            _quality.finding(
                "high",
                "missing-source-spec",
                "Source spec is missing.",
                planning_dir / "spec.md",
            )
        )
        spec_ids: list[str] = []
        spec_words = 0
    else:
        spec_text = _storage.read_text(spec_path)
        spec_words = _markdown.word_count(spec_text)
        normalized_spec = _artifacts.artifact(planning_dir, ["codex-spec.md", "claude-spec.md"])
        configured_source = _artifacts.configured_source_spec(planning_dir)
        if (
            normalized_spec
            and spec_path == normalized_spec
            and (configured_source is None or normalized_spec.resolve() != configured_source.resolve())
        ):
            _quality.add_budget_finding(findings, spec_words, budgets["spec"], "Normalized spec", "spec-too-large", spec_path)
        spec_ids = _markdown.requirement_ids(spec_text)
        if not spec_ids:
            spec_ids = _markdown.requirement_ids(plan_text)
            if not compact:
                findings.append(_quality.finding("medium", "no-requirement-ids", "Spec has no REQ-* identifiers.", spec_path))
        plan_ids = set(_markdown.requirement_ids(plan_text))
        missing_in_plan = [req_id for req_id in spec_ids if req_id not in plan_ids]
        if missing_in_plan:
            findings.append(
                _quality.finding(
                    "high",
                    "traceability-gap",
                    f"Requirement IDs missing from plan: {', '.join(missing_in_plan)}",
                    plan_path,
                )
            )

    if not tdd_path:
        tdd_words = 0
    else:
        tdd_text = _artifacts.planning_artifact_text(planning_dir, "tdd", tdd_path)
        tdd_words = _markdown.word_count(tdd_text)
        _quality.add_budget_finding(findings, tdd_words, budgets["tdd"], "TDD plan", "tdd-plan-too-large", tdd_path)
        if not _markdown.has_verification(tdd_text):
            findings.append(_quality.finding("medium", "thin-tdd-plan", "TDD plan needs a regression case and command, or justified inspection with an expected result.", tdd_path))
        tdd_ids = set(_markdown.requirement_ids(tdd_text))
        missing_in_tdd = [req_id for req_id in spec_ids if req_id not in tdd_ids]
        if missing_in_tdd:
            severity = "low" if compact else "medium"
            findings.append(_quality.finding(severity, "tdd-traceability-gap", f"Requirement IDs missing from TDD plan: {', '.join(missing_in_tdd)}", tdd_path))

    research_path = _artifacts.artifact(planning_dir, ["codex-research.md", "claude-research.md"])
    research_words = None
    if research_path:
        research_words = _markdown.word_count(_storage.read_text(research_path))
        _quality.add_budget_finding(findings, research_words, budgets["research"], "Research artifact", "research-too-large", research_path)
    interview_path = _artifacts.artifact(planning_dir, ["codex-interview.md", "claude-interview.md"])
    integration_path = _artifacts.artifact(planning_dir, ["codex-integration-notes.md", "claude-integration-notes.md"])
    integration_words = None
    if integration_path:
        integration_words = _markdown.word_count(_storage.read_text(integration_path))
        _quality.add_budget_finding(
            findings,
            integration_words,
            budgets["integration_notes"],
            "Integration notes",
            "integration-notes-too-large",
            integration_path,
        )

    review_files = sorted((planning_dir / "reviews").glob("*.md")) if (planning_dir / "reviews").exists() else []
    review_word_counts = {path.name: _markdown.word_count(_storage.read_text(path)) for path in review_files}
    for review_path in review_files:
        _quality.add_budget_finding(
            findings,
            review_word_counts[review_path.name],
            budgets["review"],
            f"Review file {review_path.name}",
            "review-too-large",
            review_path,
        )

    payload = _quality.quality_from_args(
        "plan",
        findings,
        args,
        {
            "planning_dir": str(planning_dir),
            "plan": str(plan_path),
            "requirement_ids": spec_ids,
            "depth_mode": depth,
            "interview": interview_extras,
            "word_budgets": budgets,
            "word_counts": {
                "spec": spec_words,
                "research": research_words,
                "interview": _markdown.word_count(_storage.read_text(interview_path)) if interview_path else None,
                "plan": plan_words,
                "tdd": tdd_words,
                "integration_notes": integration_words,
                "reviews": review_word_counts,
            },
        },
    )
    return _quality.emit_payload(payload, args)


def lint_sections(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    depth = args.depth or _artifacts.planning_depth(planning_dir)
    budgets = _markdown.word_budgets(depth)
    findings = _artifacts.compact_plan_findings(planning_dir, depth=depth)
    canonical = _artifacts.compact_plan_descriptor(planning_dir)
    canonical_path = canonical["path"] if canonical and not canonical["errors"] else None
    progress = _sections.check_section_progress(planning_dir)
    if progress["state"] == "invalid_index":
        for error in progress.get("errors", []):
            findings.append(_quality.finding("critical", "invalid-section-index", error, planning_dir / "sections" / "index.md"))
        return _quality.emit_quality("sections", findings, args, {"section_progress": progress})
    if progress["state"] == "no_index":
        findings.append(_quality.finding("critical", "missing-section-index", "sections/index.md is missing.", planning_dir / "sections" / "index.md"))
        return _quality.emit_quality("sections", findings, args, {"section_progress": progress})

    index_path = planning_dir / "sections" / "index.md"
    index_text = _storage.read_text(index_path)
    index_words = _markdown.word_count(index_text)
    _quality.add_budget_finding(
        findings,
        index_words,
        budgets["section_index"],
        "Section index",
        "section-index-too-large",
        index_path,
    )
    dependencies = _sections.parse_section_dependencies(index_text, progress["sections"])
    _quality.require_terms(
        findings,
        index_text,
        {
            "dependencies": ["dependency", "dependencies", "depends on", "blocks"],
            "execution-order": ["execution order", "sequence", "run order"],
            "parallelization": ["parallel", "concurrent"],
        },
        index_path,
    )

    spec_path = _artifacts.requirement_source_spec(planning_dir)
    spec_ids = _markdown.requirement_ids(_storage.read_text(spec_path)) if spec_path else []
    all_section_text = ""
    estimates: list[dict[str, Any]] = []
    section_texts: dict[str, str] = {}
    owned_path_owners: dict[str, set[str]] = {}

    for section in progress["sections"]:
        section_path = planning_dir / "sections" / f"{section}.md"
        if not section_path.exists():
            continue
        section_texts[section] = _storage.read_text(section_path)
        for owned_path in _ownership.extract_section_owned_paths(section_texts[section]):
            owned_path_owners.setdefault(owned_path, set()).add(section)

    predecessor_closure = {
        section: _sections.transitive_section_predecessors(section, dependencies)
        for section in progress["sections"]
    }

    for section, deps in dependencies.items():
        unknown = [dep for dep in deps if dep not in progress["sections"]]
        if unknown:
            findings.append(
                _quality.finding(
                    "high",
                    "unknown-section-dependency",
                    f"{section} depends on unknown section(s): {', '.join(unknown)}",
                    index_path,
                )
            )

    for section in progress["sections"]:
        section_path = planning_dir / "sections" / f"{section}.md"
        slug = section.split("-", 2)[2] if len(section.split("-", 2)) == 3 else section
        slug_tokens = set(slug.split("-"))
        if slug in _policy.VAGUE_SECTION_NAMES or slug_tokens.intersection(_policy.VAGUE_SECTION_NAMES):
            findings.append(
                _quality.finding(
                    "high",
                    "vague-section-name",
                    f"{section} is too vague to be a strong implementation boundary.",
                    section_path,
                    "Rename the section around a capability, data model, integration, or risk boundary.",
                )
            )
        if not section_path.exists():
            findings.append(_quality.finding("critical", "missing-section-file", f"Section file missing: {section}.md", section_path))
            continue
        text = section_texts[section]
        metrics = _sections.section_metrics(section, section_path, dependencies)
        estimates.append(metrics)
        all_section_text += "\n" + text
        _quality.add_budget_finding(
            findings,
            metrics["word_count"],
            budgets["plan"] if section_path == canonical_path else budgets["section"],
            section,
            "section-too-large",
            section_path,
        )
        if metrics["word_count"] > 5000:
            findings.append(_quality.finding("low", "section-too-large", f"{section} may be too large for focused implementation.", section_path))
        if metrics["file_count"] > 12:
            findings.append(
                _quality.finding(
                    "high",
                    "section-too-many-files",
                    f"{section} names {metrics['file_count']} files; split or narrow the section.",
                    section_path,
                )
            )
        elif metrics["file_count"] > 7:
            findings.append(
                _quality.finding(
                    "medium",
                    "section-many-files",
                    f"{section} names {metrics['file_count']} files; verify this stays implementable in one pass.",
                    section_path,
                )
            )
        if metrics["dependency_count"] > 4:
            findings.append(
                _quality.finding(
                    "medium",
                    "section-many-dependencies",
                    f"{section} has {metrics['dependency_count']} dependencies.",
                    section_path,
                )
            )
        _quality.require_terms(findings, text, _policy.LEAN_SECTION_TERMS, section_path, "medium")
        if not _policy.FILE_PATH_RE.search(text):
            findings.append(_quality.finding("medium", "section-no-file-paths", f"{section} does not name concrete files.", section_path))
        allowed_owners = predecessor_closure[section] | {section}
        referenced_paths, malformed_shell_gate = _ownership.shell_gate_owned_path_references(text, set(owned_path_owners))
        if malformed_shell_gate:
            findings.append(
                _quality.finding(
                    "high",
                    "malformed-shell-gate",
                    f"{section} contains shell gate syntax that cannot be lexically closed.",
                    section_path,
                    "Close every shell quote and escape before relying on the gate.",
                )
            )
        for referenced_path in sorted(referenced_paths):
            owners = owned_path_owners[referenced_path]
            if not owners.isdisjoint(allowed_owners):
                continue
            owner_text = ", ".join(sorted(owners))
            findings.append(
                _quality.finding(
                    "high",
                    "section-gate-non-predecessor-owned-path",
                    f"{section} shell gate names {referenced_path}, owned by non-predecessor section(s): {owner_text}.",
                    section_path,
                    "Defer the gate to an owning section, or add a dependency only when the implementation boundary genuinely requires it.",
                )
            )

    section_ids = set(_markdown.requirement_ids(all_section_text))
    missing_requirements = [req_id for req_id in spec_ids if req_id not in section_ids]
    if missing_requirements:
        findings.append(
            _quality.finding(
                "high",
                "section-traceability-gap",
                f"Requirement IDs missing from all sections: {', '.join(missing_requirements)}",
                planning_dir / "sections",
            )
        )

    payload = _quality.quality_from_args(
        "sections",
        findings,
        args,
        {
            "planning_dir": str(planning_dir),
            "depth_mode": depth,
            "word_budgets": budgets,
            "section_progress": progress,
            "requirement_ids": spec_ids,
            "section_index_word_count": index_words,
            "section_estimates": estimates,
        },
    )
    return _quality.emit_payload(payload, args)


def plan_artifact_findings(planning_dir: Path, *, allow_compact: bool = True) -> tuple[list[_models.Finding], dict[str, Any]]:
    artifacts = _artifacts.planning_artifacts(planning_dir)
    config = _artifacts.planning_config(planning_dir)
    depth = _artifacts.planning_depth(planning_dir)
    required = {
        "source_spec": "source spec",
        "plan": "implementation plan",
    }
    expected_names = {
        "research": "codex-research.md",
        "evidence": "codex-evidence.md",
        "interview": "codex-interview.md",
        "spec": "codex-spec.md",
        "plan": "codex-plan.md",
        "integration_notes": "codex-integration-notes.md",
        "tdd": "codex-plan-tdd.md",
        "decisions": "decisions.md",
        "risks": "risk-register.md",
        "traceability": "traceability.md",
        "quality": "quality-gates.md",
        "source_spec": "spec.md",
    }
    findings = _artifacts.compact_plan_findings(planning_dir, allow_compact=allow_compact)
    present: dict[str, str] = {}
    def has_placeholder_cell(text: str) -> bool:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.lower() in {"tbd", "todo"} or stripped.lower().startswith("[todo:"):
                return True
            if "|" not in stripped:
                continue
            cells = [cell.strip().lower() for cell in stripped.strip("|").split("|")]
            if any(cell in {"tbd", "todo"} or cell.startswith("[todo:") for cell in cells):
                return True
        return False

    for key, label in required.items():
        path = _artifacts.requirement_source_spec(planning_dir) if key == "source_spec" else artifacts.get(key)
        expected = path or planning_dir / expected_names[key]
        if not path or not path.exists():
            findings.append(
                _quality.finding(
                    "critical",
                    f"missing-{key}",
                    f"Missing Forge {label}. Complete zagrosi-plan before implementation.",
                    expected,
                )
            )
            continue
        text = _storage.read_text(path)
        if not text.strip():
            findings.append(
                _quality.finding(
                    "critical",
                    f"empty-{key}",
                    f"Forge {label} is empty. Complete zagrosi-plan before implementation.",
                    path,
                )
            )
            continue
        present[key] = str(path)
        if has_placeholder_cell(text):
            findings.append(
                _quality.finding(
                    "critical",
                    f"placeholder-{key}",
                    f"Forge {label} still contains placeholder text.",
                    path,
                    "Replace setup stubs with the completed planning artifact before implementation.",
                )
            )

    reviews_dir = planning_dir / "reviews"
    review_files = sorted(path for path in reviews_dir.glob("*.md") if path.is_file()) if reviews_dir.exists() else []
    nonempty_review_files = [path for path in review_files if _storage.read_text(path).strip()]
    review_required = config.get("review_mode", "codex_review") != "skip"
    compact = _artifacts.compact_plan_descriptor(planning_dir)
    embedded_review = bool(compact and not compact["errors"] and compact["headings"].get("review"))
    if review_required and not nonempty_review_files and not embedded_review:
        findings.append(
            _quality.finding(
                "critical",
                "missing-review",
                "Missing Forge plan review file under reviews/.",
                reviews_dir,
                "Run the review step and write at least one concrete review artifact before implementation.",
            )
        )
    elif embedded_review:
        present["reviews"] = [str(compact["path"])]
    elif nonempty_review_files:
        present["reviews"] = [str(path) for path in nonempty_review_files]
        for review_path in nonempty_review_files:
            review_text = _storage.read_text(review_path)
            if has_placeholder_cell(review_text):
                findings.append(
                    _quality.finding(
                        "critical",
                        "placeholder-review",
                        f"Forge review contains placeholder text: {review_path.name}.",
                        review_path,
                    )
                )
            if not _markdown.contains_any(review_text, ["verdict", "finding", "pass", "fixed", "blocked"]):
                findings.append(
                    _quality.finding(
                        "medium",
                        "review-missing-verdict",
                        f"Forge review has no concise verdict: {review_path.name}.",
                        review_path,
                        "Record pass, fixed, or blocked plus material findings only.",
                    )
                )

    progress = _sections.check_section_progress(planning_dir)
    if progress.get("state") == "no_index":
        findings.append(
            _quality.finding(
                "critical",
                "missing-section-index",
                "Missing sections/index.md. Complete sectioning before implementation.",
                planning_dir / "sections" / "index.md",
            )
        )
    elif progress.get("state") != "complete":
        findings.append(
            _quality.finding(
                "critical",
                "incomplete-sections",
                f"Section files are not complete: {progress.get('progress', 'unknown progress')}.",
                planning_dir / "sections",
                "Write every section in SECTION_MANIFEST before implementation.",
            )
        )

    return findings, {
        "planning_dir": str(planning_dir),
        "depth_mode": depth,
        "required_artifacts": sorted(required),
        "present_artifacts": present,
        "section_progress": progress,
    }


def plan_artifacts_payload(planning_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    findings, extras = plan_artifact_findings(planning_dir, allow_compact=getattr(args, "allow_compact", True))
    return _quality.quality_from_args("plan-artifacts", findings, args, extras)


def lint_evidence(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    texts = _artifacts.existing_artifact_texts(planning_dir)
    plan_path = _artifacts.planning_artifacts(planning_dir)["plan"] or planning_dir / "codex-plan.md"
    combined = "\n\n".join(texts.values())
    findings: list[_models.Finding] = []
    if not texts.get("plan"):
        findings.append(_quality.finding("critical", "missing-plan", "Implementation plan is missing.", plan_path))
    _quality.add_term_findings(findings, combined, _policy.EVIDENCE_TERMS, plan_path, "medium")
    paths = _ownership.extract_file_paths(combined)
    if len(paths) < args.min_files:
        findings.append(
            _quality.finding(
                "medium",
                "thin-file-evidence",
                f"Only {len(paths)} concrete file paths found; expected at least {args.min_files}.",
                plan_path,
                "Name inspected files, tests, config files, and implementation targets.",
            )
        )
    req_ids = _markdown.requirement_ids(combined)
    if not req_ids:
        findings.append(_quality.finding("medium", "no-requirement-ids", "No REQ-* IDs found in evidence surface.", plan_path))
    assumptions = [
        line.strip()
        for line in combined.splitlines()
        if _markdown.contains_any(line, ["assumption", "unknown", "open question", "stop-line", "stop line"])
    ]
    payload = _quality.quality_from_args(
        "evidence",
        findings,
        args,
        {
            "planning_dir": str(planning_dir),
            "artifacts": sorted(path.name for path in _artifacts.planning_artifacts(planning_dir).values() if path and path.exists()),
            "files": paths,
            "file_count": len(paths),
            "requirement_ids": req_ids,
            "assumption_lines": assumptions[:25],
            "artifact_word_counts": {name: _markdown.word_count(text) for name, text in texts.items()},
        },
    )
    return _quality.emit_payload(payload, args)


def lint_review_integration(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    compact = _artifacts.compact_plan_descriptor(planning_dir)
    if compact:
        review = compact["headings"].get("review", "")
        return _quality.emit_quality("review-integration", _artifacts.compact_plan_findings(planning_dir), args, {
            "planning_dir": str(planning_dir),
            "review_files": [str(compact["path"])] if review else [],
            "integration_notes": str(compact["path"]) if review else None,
            "integration_word_count": _markdown.word_count(review),
        })
    findings: list[_models.Finding] = []
    reviews_dir = planning_dir / "reviews"
    integration = _artifacts.planning_artifacts(planning_dir)["integration_notes"]
    plan = _artifacts.planning_artifacts(planning_dir)["plan"]
    review_files = sorted(reviews_dir.glob("*.md")) if reviews_dir.exists() else []
    if not review_files:
        findings.append(_quality.finding("medium", "missing-reviews", "No review files found.", reviews_dir))
    if not integration or not integration.exists():
        findings.append(_quality.finding("medium", "missing-integration-notes", "Integration notes are missing.", planning_dir / "codex-integration-notes.md"))
        integration_text = ""
    else:
        integration_text = _storage.read_text(integration)
        _quality.add_term_findings(
            findings,
            integration_text,
            {
                "accepted-review-items": ["accepted", "integrated", "changed", "updated"],
                "rejected-review-items": ["rejected", "deferred", "not accepted", "rationale"],
                "plan-edits": ["plan", "codex-plan.md", "section", "tdd"],
            },
            integration,
            "medium",
        )
    if plan and plan.exists() and review_files and not _markdown.contains_any(_storage.read_text(plan), ["review integration", "review-integrated", "accepted review"]):
        findings.append(_quality.finding("medium", "plan-missing-review-integration", "Plan does not mention review integration.", plan))
    return _quality.emit_quality(
        "review-integration",
        findings,
        args,
        {
            "planning_dir": str(planning_dir),
            "review_files": [str(path) for path in review_files],
            "integration_notes": str(integration) if integration else None,
            "integration_word_count": _markdown.word_count(integration_text),
        },
    )


def lint_artifact_schema(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    findings = _artifacts.compact_plan_findings(planning_dir)
    compact = _artifacts.compact_plan_descriptor(planning_dir)
    artifacts = _artifacts.planning_artifacts(planning_dir)
    checks = [
        ("decisions", ["ID", "Date", "Decision", "Alternatives", "Rationale", "Impact"]),
        ("risks", ["ID", "Risk", "Severity", "Likelihood", "Mitigation", "Section", "Verification"]),
        ("traceability", ["Requirement", "Plan Coverage", "Section Coverage", "Test Coverage", "Status"]),
    ]
    for name, required in checks:
        path = artifacts[name]
        if not path or not path.exists() or (compact and path == compact["path"]):
            continue
        text = _storage.read_text(path)
        tables = _markdown.markdown_tables(text)
        if not _markdown.table_has_columns(tables, required):
            findings.append(
                _quality.finding(
                    "medium",
                    f"invalid-{name}-table",
                    f"{path.name} does not contain the required columns: {', '.join(required)}.",
                    path,
                    "Use the Forge governance table schema so automated checks can reason over the artifact.",
                )
            )
    sections_state = _sections.check_section_progress(planning_dir)
    if sections_state["state"] == "invalid_index":
        findings.append(_quality.finding("critical", "invalid-section-index", "sections/index.md does not parse.", planning_dir / "sections" / "index.md"))
    return _quality.emit_quality(
        "artifact-schema",
        findings,
        args,
        {"planning_dir": str(planning_dir), "section_progress": sections_state},
    )


def lint_plan_artifacts(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    payload = plan_artifacts_payload(planning_dir, args)
    return _quality.emit_payload(payload, args)
