"""Source-linked compact contracts and unfinished first-plan scaffolds."""

from pathlib import Path
import json
import re

from . import markdown as _markdown
from . import storage as _storage

SCAFFOLD_MARKER = "<!-- FORGE_SCAFFOLD -->"


def visible_source(text: str) -> tuple[list[str], dict[str, set[int]]]:
    """Keep source line positions; fenced examples cannot declare requirements."""
    lines = _markdown.visible_markdown(text).splitlines()
    requirements: dict[str, set[int]] = {}
    fence = None
    for number, line in enumerate(lines, 1):
        if fence:
            if _markdown.markdown_fence_closes(line, *fence):
                fence = None
            continue
        opening = _markdown.markdown_fence_opening(line)
        if opening:
            fence = opening[:2]
        else:
            for req_id in _markdown.requirement_ids(line):
                requirements.setdefault(req_id, set()).add(number)
    return lines, requirements


def analyze_contract(source: Path, text: str, planning_dir: Path) -> dict | None:
    headings = _markdown.markdown_h2_sections(_markdown.visible_markdown(text))
    contracts = [body for title, body in headings if title.casefold() == "contract"]
    if not contracts:
        return None
    body = contracts[0]
    blocks, prose = _markdown.split_markdown_fences_with_closure(body)
    errors = ["Contract must have one heading and closed fences."] if len(contracts) != 1 or any(not closed for _, _, closed in blocks) else []
    rows = re.split(r"(?m)^###\s+(REQ-[A-Z0-9][A-Z0-9-]*)\s*$", "\n".join(prose))
    source_lines, source_ids = visible_source(_storage.read_text(source))
    meaningful = {number for number, line in enumerate(source_lines, 1) if line.strip() and not line.lstrip().startswith(("```", "~~~"))}
    covered: set[int] = set()
    ids: list[str] = []
    for req_id, contract in zip(rows[1::2], rows[2::2]):
        if req_id in ids:
            errors.append(f"Contract repeats {req_id}.")
        ids.append(req_id)
        fields = re.findall(r"(?im)^Source:\s*`?([^`\n]+?)`?\s*$", contract)
        link = re.fullmatch(r"(.+)#L([1-9]\d*)(?:-L([1-9]\d*))?", fields[0]) if len(fields) == 1 else None
        if link:
            path, start, end = link.groups()
            first, last = int(start), int(end or start)
            valid = not Path(path).is_absolute() and (planning_dir / path).resolve() == source.resolve()
            span = set(range(first, last + 1)) if first <= last <= len(source_lines) else set()
            if not valid or not span.intersection(meaningful):
                link = None
            elif source_ids and not span.intersection(source_ids.get(req_id, set())):
                errors.append(f"{req_id} Source must include its visible, unfenced requirement ID.")
            else:
                covered.update(span)
        if not link:
            errors.append(f"{req_id} needs a valid Source: path.md#L1-L2 in the selected brief.")
        if not re.search(r"(?im)^Behavior:\s*\S", contract) or not _markdown.has_verification(contract):
            errors.append(f"{req_id} needs Behavior, Expected, and runnable verification.")
        if re.search(r"(?im)^\w[\w ]*:\s*(?:TBD|TODO|pending|<[^>]*>)\s*$", contract):
            errors.append(f"{req_id} still has unfinished contract fields.")
    if not ids:
        errors.append("Contract needs visible ### REQ-* mappings.")
    if source_ids and set(ids) != set(source_ids):
        errors.append("Contract IDs must match every explicit requirement in the source brief.")
    if not source_ids and meaningful - covered:
        errors.append("Contract source links must cover the visible brief, including constraints.")
    return {"ids": sorted(source_ids) or ids, "text": body, "errors": errors}


def requirements(planning_dir: Path) -> tuple[list[str], list[str]]:
    # Local import avoids an artifacts/contract dependency cycle.
    from . import artifacts

    source = artifacts.requirement_source_spec(planning_dir)
    plan = artifacts.implementation_plan_path(planning_dir)
    if not source:
        return (sorted(visible_source(_storage.read_text(plan))[1]) if plan else []), []
    _, ids = visible_source(_storage.read_text(source))
    compact = artifacts.compact_plan_descriptor(planning_dir)
    contract = analyze_contract(source, _storage.read_text(plan), planning_dir) if compact and plan else None
    return (contract["ids"], contract["errors"]) if contract else (sorted(ids), [])


def create_plan_scaffold(spec_file: Path, depth: str, *, detached: bool = False) -> dict:
    """Seed a draft once; existing files and detached authoring remain untouched."""
    planning_dir = spec_file.parent
    index = planning_dir / "sections/index.md"
    section = planning_dir / "sections/section-01-contract.md"
    created = []
    existing = any((planning_dir / name).exists() or (planning_dir / name).is_symlink() for name in ("codex-plan.md", "claude-plan.md", "sections"))
    if not detached and not existing:
        lines, ids = visible_source(_storage.read_text(spec_file))
        ids = ids or ["REQ-001"]
        source = spec_file.relative_to(planning_dir).as_posix()
        meta = {"artifact_type": "compact_plan", "depth_mode": depth, "source": source}
        bodies = [f"### {req_id}\nSource: {source}#L1-L{max(1, len(lines))}\nBehavior: TODO\nExpected: TODO\nCommand: TODO\n" for req_id in ids]
        drafts = {
            index: (SCAFFOLD_MARKER + "\n<!-- FORGE_META\n" + json.dumps(meta) + "\nEND_FORGE_META -->\n"
                    "<!-- PROJECT_CONFIG\nruntime: TODO\ntest_command: TODO\nEND_PROJECT_CONFIG -->\n"
                    "<!-- SECTION_MANIFEST\nsection-01-contract\nEND_MANIFEST -->\n"
                    "Dependencies: none. Execution order: section-01-contract. Parallel: no siblings.\n"),
            section: (SCAFFOLD_MARKER + "\n# Draft: choose section boundaries before implementation\n\n"
                      "## Contract\n" + "\n".join(bodies) + "\n## Owned files\nTODO\n\n"
                      "## Evidence\nTODO\n\n## Review\nVerdict: blocked\nReviewed: TODO\n"),
        }
        index.parent.mkdir()
        for path, content in drafts.items():
            with path.open("x", encoding="utf-8") as handle:
                handle.write(content)
            created.append(str(path))
    return {"created": created, "unfinished": scaffold_unfinished(planning_dir)}


def scaffold_unfinished(planning_dir: Path) -> bool:
    """Read draft state without creating or changing planning artifacts."""
    from . import artifacts

    index = planning_dir / "sections/index.md"
    descriptor = artifacts.compact_plan_descriptor(planning_dir)
    plan = descriptor["path"] if descriptor else planning_dir / "sections/section-01-contract.md"
    contents = "\n".join(_storage.read_text(path) for path in (index, plan) if path and path.is_file())
    if SCAFFOLD_MARKER in contents:
        return bool(artifacts.compact_plan_findings(planning_dir)) or bool(re.search(
            r"(?im)(?:^|:\s*)(?:TODO|TBD)\s*$", contents,
        ))
    return False
