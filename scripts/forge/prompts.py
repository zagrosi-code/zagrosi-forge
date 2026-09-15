"""Forge prompts."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re

from . import artifacts as _artifacts
from . import markdown as _markdown
from . import output as _output
from . import policy as _policy
from . import quality as _quality
from . import sections as _sections
from . import storage as _storage
from . import traceability as _traceability

def deep_plan_check_sections(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    result = _sections.check_section_progress(planning_dir)
    result["success"] = result["state"] != "invalid_index"
    return _output.print_json(result, 0 if result["success"] else 1)


def deep_plan_generate_section_prompts(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    progress = _sections.check_section_progress(planning_dir)
    if progress["state"] in {"invalid_index", "no_index"}:
        return _output.print_json({"success": False, "section_progress": progress}, 1)

    pending = progress["missing"] + progress["empty"]
    if args.all:
        pending = progress["sections"]
    batch = pending[: args.batch_size]
    prompts_dir = planning_dir / "sections" / ".prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    prompts: list[str] = []
    depth = _artifacts.planning_depth(planning_dir, args.depth)
    budgets = _markdown.word_budgets(depth)
    canonical = _artifacts.compact_plan_descriptor(planning_dir)
    canonical_path = canonical["path"] if canonical else None
    plan_path = _artifacts.implementation_plan_path(planning_dir) or canonical_path or planning_dir / "codex-plan.md"
    source_path = (canonical["source"] if canonical else None) or _artifacts.requirement_source_spec(planning_dir) or planning_dir / "spec.md"
    index_path = planning_dir / "sections" / "index.md"
    plugin_root = _storage.current_plugin_root()
    for section in batch:
        prompt_path = prompts_dir / f"{section}-prompt.md"
        section_path = planning_dir / "sections" / f"{section}.md"
        prompt_path.write_text(
            _policy.SECTION_PROMPT.format(
                section=section,
                plan_path=plan_path,
                index_path=index_path,
                source_path=source_path,
                max_words=budgets["plan"] if section_path == canonical_path else budgets["section"],
                depth=depth,
                depth_path=plugin_root / "skills/zagrosi-plan/references/depth-standards.md",
                engineering_path=plugin_root / "skills/zagrosi-implement/references/engineering.md",
                format_path=plugin_root / "skills/zagrosi-plan/references/section-format.md",
            ),
            encoding="utf-8",
        )
        prompts.append(str(prompt_path))

    return _output.print_json(
        {
            "success": True,
            "planning_dir": str(planning_dir),
            "prompts_dir": str(prompts_dir),
            "batch_size": args.batch_size,
            "remaining": pending[args.batch_size :],
            "prompt_files": prompts,
        }
    )


def requirement_candidate(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith(("-", "*")):
        return False
    if "REQ-" in stripped:
        return False
    return _markdown.contains_any(
        stripped,
        ["must", "should", "shall", "allow", "support", "enable", "provide", "user can", "system can", "needs to"],
    )


def extract_requirements(args: argparse.Namespace) -> int:
    path = _storage.resolve_path(args.file)
    if not path.exists():
        return _output.print_json({"success": False, "error": f"File not found: {path}"}, 1)
    text = _storage.read_text(path)
    existing = _markdown.requirement_ids(text)
    next_number = 1
    if existing:
        numeric = [int(match.group(1)) for req in existing if (match := re.match(r"REQ-(\d+)$", req))]
        next_number = max(numeric, default=0) + 1

    requirements: list[dict[str, str]] = []
    rewritten: list[str] = []
    for line in text.splitlines():
        if requirement_candidate(line):
            req_id = f"REQ-{next_number:03d}"
            next_number += 1
            prefix, body = line.split(maxsplit=1)
            rewritten_line = f"{prefix} {req_id}: {body}"
            requirements.append({"id": req_id, "text": body.strip(), "line": rewritten_line})
            rewritten.append(rewritten_line)
        else:
            rewritten.append(line)

    if args.write and requirements:
        path.write_text("\n".join(rewritten) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")

    return _output.print_json(
        {
            "success": True,
            "file": str(path),
            "requirements": requirements,
            "updated": bool(args.write and requirements),
            "content": "\n".join(rewritten),
        }
    )


def trace_export(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    findings, extras = _traceability.traceability_analysis(planning_dir)
    payload = _quality.quality_from_args("traceability", findings, args, extras)
    rows = [
        {
            "requirement": req_id,
            "in_plan": str(item["in_plan"]).lower(),
            "in_tdd": str(item["in_tdd"]).lower(),
            "sections": ";".join(item["sections"]),
            "covered": str(item["covered"]).lower(),
        }
        for req_id, item in payload["coverage"].items()
    ]

    if args.format == "json":
        content = json.dumps({"rows": rows, "orphans": payload["orphans"]}, indent=2, sort_keys=True) + "\n"
    elif args.format == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=["requirement", "in_plan", "in_tdd", "sections", "covered"])
        writer.writeheader()
        writer.writerows(rows)
        content = buffer.getvalue()
    else:
        lines = ["| Requirement | In Plan | In TDD | Sections | Covered |", "|-------------|---------|--------|----------|---------|"]
        for row in rows:
            lines.append(
                f"| {row['requirement']} | {row['in_plan']} | {row['in_tdd']} | {row['sections'] or '-'} | {row['covered']} |"
            )
        content = "\n".join(lines) + "\n"

    if args.output:
        output = _storage.resolve_path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
        payload["output"] = str(output)
    else:
        payload["content"] = content
    return _quality.emit_payload(payload, args)


def agent_prompts(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    prompt_names = sorted(_policy.PROMPT_TYPES) if args.type == "all" else [args.type]
    prompts_dir = planning_dir / ".prompts" / "agents"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    engineering_path = _storage.current_plugin_root() / "skills/zagrosi-implement/references/engineering.md"
    for name in prompt_names:
        path = prompts_dir / f"{name}.md"
        path.write_text(
            (
                f"# {name.replace('-', ' ').title()}\n\n"
                f"{_policy.PROMPT_TYPES[name]}\n\n"
                f"Planning directory: `{planning_dir}`\n\n"
                f"Apply the engineering standard: `{engineering_path}`.\n\n"
                "Use only evidence from the repository and named planning artifacts. "
                "Return concise findings with paths, risks, and recommended next actions.\n"
            ),
            encoding="utf-8",
        )
        written.append(str(path))
    return _output.print_json({"success": True, "planning_dir": str(planning_dir), "prompt_files": written})
