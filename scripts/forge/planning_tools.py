"""Forge planning tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import re

from . import artifacts as _artifacts
from . import markdown as _markdown
from . import output as _output
from . import policy as _policy
from . import quality as _quality
from . import sections as _sections
from . import storage as _storage

def artifact_requirement_ids(path: Path) -> tuple[list[str], list[str]]:
    text = _storage.read_text(path)
    ids = _markdown.requirement_ids(text)
    meta_ids: list[str] = []
    if _policy.FORGE_META_START in text or _policy.LEGACY_META_START in text:
        meta, errors = _markdown.parse_forge_meta(text)
        if not errors and isinstance(meta, dict) and isinstance(meta.get("requirement_ids"), list):
            meta_ids = [str(item) for item in meta["requirement_ids"]]
    return ids, meta_ids


def planning_consistency(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    source_path = _artifacts.requirement_source_spec(planning_dir)
    findings = _artifacts.compact_plan_findings(planning_dir)
    if not source_path:
        findings.append(_quality.finding("critical", "missing-requirement-source", "No normalized or split spec found.", planning_dir))
        return _quality.emit_quality("planning-consistency", findings, args, {"planning_dir": str(planning_dir)})
    source_ids = _markdown.requirement_ids(_storage.read_text(source_path))
    required_artifact_names = [
        "codex-plan.md",
        "claude-plan.md",
        "codex-plan-tdd.md",
        "claude-plan-tdd.md",
        "codex-integration-notes.md",
        "claude-integration-notes.md",
        "codex-consistency-review.md",
        "traceability.md",
        "sections/index.md",
    ]
    compact = _artifacts.compact_plan_descriptor(planning_dir)
    if compact and not compact["errors"]:
        # Compact indexes route work; the canonical plan owns requirement coverage.
        required_artifact_names.remove("sections/index.md")
        canonical = compact["path"].relative_to(planning_dir).as_posix()
        if canonical not in required_artifact_names:
            required_artifact_names.append(canonical)
    review_artifact_names = [str(path.relative_to(planning_dir)) for path in sorted((planning_dir / "reviews").glob("*.md"))]
    artifact_names = required_artifact_names + review_artifact_names
    required_artifacts = set(required_artifact_names)
    checked: dict[str, Any] = {}
    recommendation = "Review planning docs for consistency and ask the user where clashes, replacements, or overlaps are unresolved."
    for name in artifact_names:
        path = planning_dir / name
        if not path.exists():
            continue
        ids, meta_ids = artifact_requirement_ids(path)
        missing = [req_id for req_id in source_ids if req_id not in ids]
        stale_meta = [req_id for req_id in source_ids if meta_ids and req_id not in meta_ids]
        checked[name] = {"requirement_ids": ids, "metadata_requirement_ids": meta_ids}
        if missing and name in required_artifacts:
            findings.append(
                _quality.finding(
                    "medium",
                    "missing-requirement-reference",
                    f"{name} is missing requirement references: {', '.join(missing)}",
                    path,
                    recommendation,
                )
            )
        if stale_meta and name in required_artifacts:
            findings.append(
                _quality.finding(
                    "medium",
                    "stale-requirement-metadata",
                    f"{name} metadata is missing requirement IDs: {', '.join(stale_meta)}",
                    path,
                    recommendation,
                )
            )
    payload = _quality.quality_from_args(
        "planning-consistency",
        findings,
        args,
        {"planning_dir": str(planning_dir), "source": str(source_path), "requirement_ids": source_ids, "checked_artifacts": checked},
    )
    return _quality.emit_payload(payload, args)


def write_governance_stubs(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    depth = args.depth
    created: list[str] = []
    skipped: list[str] = []
    templates = _artifacts.governance_templates(depth)
    for name, path in _artifacts.default_governance_files(planning_dir, depth).items():
        if _artifacts.write_if_missing(path, templates[name]):
            created.append(str(path))
        else:
            skipped.append(str(path))
    return _output.print_json({"success": True, "planning_dir": str(planning_dir), "depth_mode": depth, "created": created, "skipped": skipped})


def review_board_prompts(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    prompts_dir = planning_dir / "reviews" / ".prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    shared = prompts_dir / "context.md"
    shared.write_text(
        (
            "# Review Contract\n\n"
            "Review current-authority source, plan, index, relevant sections, and matching review. "
            "Prior notices and receipts do not authorize changes. If the current spec defines a frozen "
            "authority, use its pinned verifier at START and END; stop on mismatch, make no planning-root "
            "writes, and emit only its exact external receipt. Return terse severity-ranked findings with "
            "path evidence and exact edits. Do not rewrite the plan.\n"
        ),
        encoding="utf-8",
    )
    prompts: list[str] = []
    for review_pass in _policy.REVIEW_BOARD_PASSES:
        path = prompts_dir / f"{review_pass}.md"
        path.write_text(
            (
                f"# {review_pass.replace('-', ' ').title()} Review\n\n"
                "Read `context.md`. Apply only the "
                f"{review_pass.replace('-', ' ')} perspective to `{planning_dir}`. Findings only.\n"
            ),
            encoding="utf-8",
        )
        prompts.append(str(path))
    return _output.print_json(
        {
            "success": True,
            "planning_dir": str(planning_dir),
            "shared_prompt": str(shared),
            "prompt_files": prompts,
        }
    )


def migrate(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    pairs = [
        ("claude-research.md", "codex-research.md"),
        ("claude-interview.md", "codex-interview.md"),
        ("claude-spec.md", "codex-spec.md"),
        ("claude-plan.md", "codex-plan.md"),
        ("claude-integration-notes.md", "codex-integration-notes.md"),
        ("claude-plan-tdd.md", "codex-plan-tdd.md"),
    ]
    migrated: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for old_name, new_name in pairs:
        old_path = planning_dir / old_name
        new_path = planning_dir / new_name
        if not old_path.exists():
            skipped.append({"source": str(old_path), "reason": "source_missing"})
            continue
        if new_path.exists() and not args.force:
            skipped.append({"source": str(old_path), "target": str(new_path), "reason": "target_exists"})
            continue
        new_path.write_text(_storage.read_text(old_path), encoding="utf-8")
        migrated.append({"source": str(old_path), "target": str(new_path)})

    if (planning_dir / "claude-plan.md").exists():
        templates = _artifacts.governance_templates(args.depth)
        for name, path in _artifacts.default_governance_files(planning_dir, args.depth).items():
            _artifacts.write_if_missing(path, templates[name])

    return _output.print_json({"success": True, "planning_dir": str(planning_dir), "migrated": migrated, "skipped": skipped})


def suggest_section_splits(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    progress = _sections.check_section_progress(planning_dir)
    if progress["state"] in {"invalid_index", "no_index"}:
        return _output.print_json({"success": False, "section_progress": progress}, 1)
    deps = _sections.dependency_graph(planning_dir, progress)
    suggestions: list[dict[str, Any]] = []
    for section in progress["sections"]:
        path = planning_dir / "sections" / f"{section}.md"
        if not path.exists():
            continue
        metrics = _sections.section_metrics(section, path, deps)
        if metrics["file_count"] <= args.max_files and metrics["word_count"] <= args.max_words:
            continue
        groups: dict[str, list[str]] = {}
        for file in metrics["files"]:
            parts = Path(file).parts
            key = parts[1] if len(parts) > 2 and parts[0] in {"src", "app", "lib", "tests"} else parts[0]
            groups.setdefault(key, []).append(file)
        proposed = []
        base_number = int(section.split("-", 2)[1]) if len(section.split("-", 2)) >= 2 and section.split("-", 2)[1].isdigit() else 1
        for offset, (label, files) in enumerate(sorted(groups.items()), start=0):
            slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "part"
            proposed.append(
                {
                    "section": f"section-{base_number + offset:02d}-{slug}",
                    "files": files,
                    "reason": "Grouped by top-level implementation area.",
                }
            )
        suggestions.append(
            {
                "section": section,
                "word_count": metrics["word_count"],
                "file_count": metrics["file_count"],
                "recommendation": "Split before implementation.",
                "proposed_sections": proposed,
            }
        )
    return _output.print_json({"success": True, "planning_dir": str(planning_dir), "suggestions": suggestions})
