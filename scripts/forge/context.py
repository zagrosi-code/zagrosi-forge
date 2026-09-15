"""Forge context."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Any
import argparse
import re

from . import artifacts as _artifacts
from . import markdown as _markdown
from . import models as _models
from . import output as _output
from . import ownership as _ownership
from . import policy as _policy
from . import quality as _quality
from . import storage as _storage

def context_budget(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    candidates = [
        path
        for path in [
            _artifacts.artifact(planning_dir, ["codex-spec.md", "claude-spec.md"]),
            _artifacts.implementation_plan_path(planning_dir),
            _artifacts.artifact(planning_dir, ["codex-plan-tdd.md", "claude-plan-tdd.md"]),
            planning_dir / "decisions.md",
            planning_dir / "risk-register.md",
            planning_dir / "traceability.md",
            planning_dir / "quality-gates.md",
        ]
        if path and path.exists()
    ]
    sections_dir = planning_dir / "sections"
    if sections_dir.exists():
        candidates.extend(sorted(sections_dir.glob("*.md")))

    files = [{"path": str(path), "word_count": _markdown.word_count(_storage.read_text(path))} for path in dict.fromkeys(candidates)]
    total_words = sum(item["word_count"] for item in files)
    findings: list[_models.Finding] = []
    if total_words > args.max_words:
        findings.append(
            _quality.finding(
                "medium",
                "context-budget-exceeded",
                f"Planning artifacts total {total_words} words, above budget {args.max_words}.",
                planning_dir,
                "Prefer section-specific context, summaries, and trace exports before implementation.",
            )
        )
    largest = sorted(files, key=lambda item: item["word_count"], reverse=True)[:5]
    return _quality.emit_quality(
        "context-budget",
        findings,
        args,
        {
            "planning_dir": str(planning_dir),
            "max_words": args.max_words,
            "total_words": total_words,
            "largest_files": largest,
        },
    )


def assumption_ledger_line_token(line_no: int) -> str:
    if isinstance(line_no, bool) or not isinstance(line_no, int) or line_no < 1:
        raise ValueError("assumption-ledger-line-number-invalid")
    letters: list[str] = []
    remaining = line_no
    while remaining:
        remaining, offset = divmod(remaining - 1, 26)
        letters.append(chr(ord("a") + offset))
    return "L" + "".join(reversed(letters))


def assumption_ledger(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    texts = _artifacts.existing_artifact_texts(planning_dir)
    rows: list[dict[str, str]] = []
    labels = {
        "assumption": ["assumption", "assume", "assumes"],
        "open_question": ["open question", "unknown", "unclear"],
        "stop_line": ["stop-line", "stop line", "stop and"],
    }
    for name, text in texts.items():
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip("- ").strip()
            if not stripped:
                continue
            for label, terms in labels.items():
                if _markdown.contains_any(stripped, terms):
                    line_token = assumption_ledger_line_token(line_no)
                    rows.append({"type": label, "artifact": name, "line": line_token, "text": stripped})
                    break
    content = "# Assumption Ledger\n\n| Type | Artifact | Line | Text |\n|------|----------|------|------|\n"
    for row in rows:
        safe_text = row["text"].replace("|", "\\|")
        content += f"| {row['type']} | {row['artifact']} | {row['line']} | {safe_text} |\n"
    output = None
    if args.write:
        output = planning_dir / "assumption-ledger.md"
        output.write_text(content, encoding="utf-8")
    return _output.print_json({"success": True, "planning_dir": str(planning_dir), "count": len(rows), "rows": rows, "output": str(output) if output else None, "content": content if not output else None})


def context_blocks(text: str) -> list[tuple[int, str, set[str]]]:
    """Keep complete Markdown blocks and inherit requirement scope from headings."""
    blocks: list[tuple[int, str, set[str]]] = []
    headings: list[tuple[int, set[str]]] = []
    lines: list[str] = []
    start = 1
    fence: tuple[str, int] | None = None
    metadata = False

    def flush() -> None:
        if lines:
            body = "\n".join(lines)
            inherited = next((ids for _, ids in reversed(headings) if ids), set())
            blocks.append((start, body, set(_markdown.requirement_ids(body)) | inherited))
            lines.clear()

    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not fence and re.match(r"<!--\s*(?:FORGE|DEEP)_META\b", stripped):
            flush()
            metadata = True
        if metadata:
            if "-->" in line:
                metadata = False
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)", line) if not fence else None
        if not fence and re.match(r"^(?:[-+*]|\d+[.)])\s", line):
            flush()
        if heading:
            flush()
            level = len(heading[1])
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, set(_markdown.requirement_ids(heading[2]))))
        if not stripped and not fence:
            flush()
            continue
        if fence:
            if _markdown.markdown_fence_closes(line, *fence):
                fence = None
        elif opening := _markdown.markdown_fence_opening(line):
            fence = opening[:2]
        if not lines:
            start = number
        lines.append(line)
    flush()
    return blocks


def context_requirement_ids(text: str) -> set[str]:
    return set(_markdown.requirement_ids("\n".join(block for _, block, _ in context_blocks(text))))


def selected_context_blocks(text: str, reqs: set[str]) -> list[tuple[int, str]]:
    selected = []
    for line, block, ids in context_blocks(text):
        rows = block.splitlines()
        if not reqs:
            selected.append((line, block))
        elif not _markdown.markdown_fence_opening(rows[0]) and any(
            re.fullmatch(r"\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*", row) for row in rows[:2]
        ):
            row_scoped = any(
                re.fullmatch(r"requirements?(?: ids?)?|req(?:[- ]?id)?|id", cell.strip(), re.IGNORECASE)
                for cell in rows[0].strip("|").split("|")
            )
            if not row_scoped:
                if ids.intersection(reqs):
                    selected.append((line, block))
                continue
            relevant = [row for row in rows if reqs.intersection(_markdown.requirement_ids(row))]
            if relevant:
                header = [row for row in rows[:2] if not _markdown.requirement_ids(row)]
                selected.append((line, "\n".join(header + relevant)))
            elif ids.intersection(reqs) and not _markdown.requirement_ids(block):
                selected.append((line, block))
        elif ids.intersection(reqs):
            selected.append((line, block))
    return selected


def build_context(planning_dir: Path, section: str | None, max_words: int, line_limit: int = 20) -> dict[str, Any]:
    if max_words <= 0 or line_limit <= 0:
        return {"success": False, "error": "Context budgets must be positive."}
    if not planning_dir.is_dir():
        return {"success": False, "error": f"Planning directory not found: {planning_dir}"}
    compact_errors = _artifacts.compact_plan_findings(planning_dir)
    if compact_errors:
        return {"success": False, "error": "Compact plan is incomplete.", "findings": [item.to_dict() for item in compact_errors]}
    if section and not _policy.SECTION_RE.fullmatch(section):
        return {"success": False, "error": "Invalid section name."}
    section_path = planning_dir / "sections" / f"{section}.md" if section else None
    if section_path and not section_path.is_file():
        return {"success": False, "error": f"Section file not found: {section_path}"}
    section_text = _storage.read_text(section_path) if section_path else ""
    if section_path and not section_text.strip():
        return {"success": False, "error": f"Section file is empty: {section_path}"}
    reqs = context_requirement_ids(section_text)
    artifacts = _artifacts.planning_artifacts(planning_dir)
    artifacts["spec"] = _artifacts.requirement_source_spec(planning_dir)
    names = ("spec", "plan") if _markdown.is_lean_depth(_artifacts.planning_depth(planning_dir)) else ("spec", "plan", "tdd", "decisions", "risks", "traceability")
    sources = []
    seen_paths: set[Path] = set()
    for name in names:
        path = artifacts.get(name)
        if path and path.is_file() and path != section_path and path not in seen_paths:
            seen_paths.add(path)
            text = _artifacts.planning_artifact_text(planning_dir, name, path)
            blocks = selected_context_blocks(text, reqs) if not section or reqs else []
            shared = bool(section and any(
                not ids and any(line.strip() and not re.match(r"^#{1,6}\s", line) for line in block.splitlines())
                for _, block, ids in context_blocks(text)
            ))
            if blocks or shared:
                reference = f"Omitted {name} context: `{path}`."
                sources.append((name, path, blocks, reference, _markdown.word_count(reference), shared))
    if not section and not sources:
        return {"success": False, "error": f"No planning sources found: {planning_dir}"}
    parts = [section_text.rstrip()] if section_path else [f"# Context: {planning_dir.name}"]
    used = _markdown.word_count("\n\n".join(parts))
    reserved = sum(cost for _, _, _, _, cost, _ in sources)
    if used + reserved > max_words:
        return {"success": False, "error": "The complete section and necessary source references exceed --max-words. Split the section or raise the budget; no contract was truncated.",
                "required_words": used + reserved, "max_words": max_words}
    omitted: list[str] = []
    for name, path, blocks, reference, cost, shared in sources:
        included_lines = 0
        skipped = shared
        if shared:
            omitted.append(str(path))
        for line, block in blocks:
            excerpt = f"## {name}: `{path}:{line}`\n\n{block}"
            size = _markdown.word_count(excerpt)
            if used + size + reserved > max_words or included_lines + len(block.splitlines()) > line_limit:
                omitted.append(f"{path}:{line}")
                skipped = True
                continue
            parts.append(excerpt)
            used += size
            included_lines += len(block.splitlines())
        reserved -= cost
        if skipped:
            parts.append(reference)
            used += cost
    content = "\n\n".join(parts) + "\n"
    result = {"success": True, "content": content, "word_count": _markdown.word_count(content), "max_words": max_words,
              "requirements": sorted(reqs), "files": _ownership.extract_section_owned_paths(section_text), "tests": _markdown.section_owned_test_names(section_text)}
    if omitted:
        result.update({"omitted_sources": omitted[:20], "omitted_source_count": len(omitted)})
    return result


def implementation_packet(args: argparse.Namespace) -> int:
    detached = bool(getattr(args, "implementation_root", None))
    if detached:
        from . import detached_authority as _detached_authority
        from . import detached_context as _detached_context
        from . import detached_contract as _detached_contract
        from . import detached_state as _detached_state
        from . import secure_io as _secure_io

    planning_dir = _storage.absolute_path_no_follow(args.planning_dir) if detached else _storage.resolve_path(args.planning_dir)
    implementation_root: Path | None = None
    try:
        with ExitStack() as contexts:
            if detached:
                if not args.output_dir:
                    raise _models.DetachedImplementationError(
                        "missing-detached-output-dir",
                        "Detached implementation packets require an explicit external --output-dir.",
                    )
                implementation_root, root_fd, config, guard, require_lock_authority = contexts.enter_context(_detached_context.open_detached_context(
                    planning_dir,
                    args.implementation_root,
                ))
            section = args.section
            packet = build_context(planning_dir, section, args.max_words)
            if not packet["success"]:
                return _output.print_json(packet, 1)
            section_path = planning_dir / "sections" / f"{section}.md"
            section_text = _storage.read_text(section_path)
            artifacts = _artifacts.planning_artifacts(planning_dir)
            artifacts["spec"] = _artifacts.requirement_source_spec(planning_dir) or artifacts["plan"]
            ids = {name: context_requirement_ids(_artifacts.planning_artifact_text(planning_dir, name, path)) if (path := artifacts.get(name)) else set()
                   for name in ("spec", "plan", "tdd")}
            section_tests = _markdown.contains_any(section_text, ["tests first", "expected failure", "test_", "pytest", "vitest", "cargo test", "go test"])
            gaps = {req: [name for name in ("spec", "plan", "tdd") if req not in ids[name] and not (name == "tdd" and section_tests)]
                    for req in packet["requirements"]}
            gaps = {req: missing for req, missing in gaps.items() if missing}
            if not packet["requirements"] or gaps:
                return _output.print_json({"success": False, "error": "Selected section has incomplete requirement coverage.", "coverage_gaps": gaps}, 1)
            content = packet.pop("content")
            filename = f"{section}-packet.md"
            if detached:
                assert implementation_root is not None and root_fd is not None and guard is not None
                output = _storage.absolute_path_no_follow(args.output_dir) / filename
                relative = _detached_state.detached_artifact_relative(implementation_root, str(output))
                if Path(relative).parts[0] != "code_review":
                    raise _models.DetachedImplementationError(
                        "invalid-detached-output-dir",
                        "Detached generated packets must stay beneath the fixed code_review directory.",
                        path=str(output),
                    )
                _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
                _secure_io.write_regular_bytes_at(root_fd, relative, content.encode("utf-8"), cap=_detached_contract.DETACHED_REVIEW_CAP)
                _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
                require_lock_authority()
                output = implementation_root / relative
            else:
                output_dir = _storage.resolve_path(args.output_dir) if args.output_dir else planning_dir / ".forge" / "packets"
                output_dir.mkdir(parents=True, exist_ok=True)
                output = output_dir / filename
                output.write_text(content, encoding="utf-8")
            return _output.print_json({**packet, "planning_dir": str(planning_dir), "section": section, "output": str(output)})
    except _models.DetachedImplementationError as exc:
        if not detached:
            raise
        return _output.print_json(
            _models.detached_error_payload(
                exc,
                mode="detached-frozen",
                planning_dir=str(planning_dir),
                implementation_root=str(implementation_root or _storage.absolute_path_no_follow(args.implementation_root)),
            ),
            1,
        )
    except OSError as exc:
        if not detached:
            raise
        return _output.print_json(
            _models.detached_io_error_payload(
                exc,
                mode="detached-frozen",
                planning_dir=str(planning_dir),
                implementation_root=str(implementation_root or _storage.absolute_path_no_follow(args.implementation_root)),
            ),
            1,
        )


def context_brief(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    brief = build_context(planning_dir, args.section, args.max_words, args.lines_per_artifact)
    if not brief["success"]:
        return _output.print_json(brief, 1)
    for key in ("requirements", "files", "tests"):
        brief.pop(key)
    brief["output"] = None
    if args.output:
        output = _storage.resolve_path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(brief["content"], encoding="utf-8")
        brief["content"] = None
        brief["output"] = str(output)
    return _output.print_json({**brief, "planning_dir": str(planning_dir), "section": args.section})


def tdd_skeletons(args: argparse.Namespace) -> int:
    detached = bool(getattr(args, "implementation_root", None))
    if detached:
        from . import detached_authority as _detached_authority
        from . import detached_context as _detached_context
        from . import detached_contract as _detached_contract
        from . import detached_state as _detached_state
        from . import secure_io as _secure_io

    planning_dir = _storage.absolute_path_no_follow(args.planning_dir) if detached else _storage.resolve_path(args.planning_dir)
    implementation_root: Path | None = None
    try:
        with ExitStack() as contexts:
            if detached:
                if not args.output_dir:
                    raise _models.DetachedImplementationError(
                        "missing-detached-output-dir",
                        "Detached TDD skeletons require an explicit external --output-dir.",
                    )
                implementation_root, root_fd, config, guard, require_lock_authority = contexts.enter_context(_detached_context.open_detached_context(
                    planning_dir,
                    args.implementation_root,
                ))
            artifacts = _artifacts.planning_artifacts(planning_dir)
            if not artifacts["tdd"] or not artifacts["tdd"].exists():
                return _output.print_json({"success": False, "error": "codex-plan-tdd.md is missing"}, 1)
            text = _storage.read_text(artifacts["tdd"])
            tests = _markdown.test_names(text)
            ext = {"pytest": "py", "vitest": "ts", "go": "go", "rust": "rs"}[args.framework]
            filename = f"test_skeleton.{ext}"
            if args.framework == "pytest":
                body = "\n\n".join(f"def {name}():\n    \"\"\"Generated from Forge TDD plan. Replace with real red test.\"\"\"\n    raise AssertionError(\"red test not implemented\")" for name in tests if name.startswith("test_"))
            elif args.framework == "vitest":
                body = "import { describe, it, expect } from 'vitest';\n\n" + "\n\n".join(f"it('{name}', () => {{\n  expect.fail('red test not implemented');\n}});" for name in tests)
            elif args.framework == "go":
                body = "package tests\n\nimport \"testing\"\n\n" + "\n\n".join(f"func Test{re.sub(r'[^A-Za-z0-9]', '', name.title())}(t *testing.T) {{\n\tt.Fatal(\"red test not implemented\")\n}}" for name in tests)
            else:
                body = "\n\n".join(f"#[test]\nfn {re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())}() {{\n    panic!(\"red test not implemented\");\n}}" for name in tests)
            raw = (body + "\n").encode("utf-8")
            if detached:
                assert implementation_root is not None and root_fd is not None and guard is not None
                output = _storage.absolute_path_no_follow(args.output_dir) / filename
                relative = _detached_state.detached_artifact_relative(implementation_root, str(output))
                if Path(relative).parts[0] != "code_review":
                    raise _models.DetachedImplementationError(
                        "invalid-detached-output-dir",
                        "Detached generated TDD skeletons must stay beneath the fixed code_review directory.",
                        path=str(output),
                    )
                _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
                _secure_io.write_regular_bytes_at(root_fd, relative, raw, cap=_detached_contract.DETACHED_REVIEW_CAP)
                _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
                require_lock_authority()
                output = implementation_root / relative
            else:
                output_dir = _storage.resolve_path(args.output_dir) if args.output_dir else planning_dir / ".forge" / "tdd-skeletons"
                output_dir.mkdir(parents=True, exist_ok=True)
                output = output_dir / filename
                output.write_bytes(raw)
            return _output.print_json({"success": True, "planning_dir": str(planning_dir), "framework": args.framework, "tests": tests, "output": str(output)})
    except _models.DetachedImplementationError as exc:
        if not detached:
            raise
        return _output.print_json(
            _models.detached_error_payload(
                exc,
                mode="detached-frozen",
                planning_dir=str(planning_dir),
                implementation_root=str(implementation_root or _storage.absolute_path_no_follow(args.implementation_root)),
            ),
            1,
        )
    except OSError as exc:
        if not detached:
            raise
        return _output.print_json(
            _models.detached_io_error_payload(
                exc,
                mode="detached-frozen",
                planning_dir=str(planning_dir),
                implementation_root=str(implementation_root or _storage.absolute_path_no_follow(args.implementation_root)),
            ),
            1,
        )


def plan_diff(args: argparse.Namespace) -> int:
    before = _storage.resolve_path(args.before)
    after = _storage.resolve_path(args.after)
    if not before.exists() or not after.exists():
        return _output.print_json({"success": False, "error": "Both --before and --after must exist."}, 1)
    before_text = _storage.read_text(before)
    after_text = _storage.read_text(after)
    result = {
        "success": True,
        "before": str(before),
        "after": str(after),
        "word_delta": _markdown.word_count(after_text) - _markdown.word_count(before_text),
        "requirements_added": sorted(set(_markdown.requirement_ids(after_text)) - set(_markdown.requirement_ids(before_text))),
        "requirements_removed": sorted(set(_markdown.requirement_ids(before_text)) - set(_markdown.requirement_ids(after_text))),
        "headings_added": sorted(set(_markdown.markdown_headings(after_text)) - set(_markdown.markdown_headings(before_text))),
        "headings_removed": sorted(set(_markdown.markdown_headings(before_text)) - set(_markdown.markdown_headings(after_text))),
        "files_added": sorted(set(_ownership.extract_file_paths(after_text)) - set(_ownership.extract_file_paths(before_text))),
        "files_removed": sorted(set(_ownership.extract_file_paths(before_text)) - set(_ownership.extract_file_paths(after_text))),
    }
    return _output.print_json(result)
