"""Bounded, complete Markdown contracts reached through explicit local links."""

from __future__ import annotations

from collections import deque
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from . import markdown as _markdown
from . import storage as _storage


LINK = re.compile(r'(?<!!)\[[^\]\n]+\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+["\'][^\n]*?["\'])?\s*\)')
INLINE_TOKEN = re.compile(r"`+|(?<!!)\[")
MAX_CONTRACTS = 64
MAX_SOURCE_BYTES = 1_048_576


def local_links(text: str) -> list[str]:
    """Scan in source order so comments and code cannot activate each other."""
    text = _markdown.visible_markdown(text)
    links = []
    offset = skip = 0
    fence = None
    for line in text.splitlines(keepends=True):
        start, offset = offset, offset + len(line)
        if fence:
            if _markdown.markdown_fence_closes(line, *fence):
                fence = None
            continue
        if skip >= offset:
            continue
        opening = _markdown.markdown_fence_opening(line) if skip <= start else None
        if opening:
            fence = opening[:2]
            continue
        for token in INLINE_TOKEN.finditer(line, max(0, skip - start)):
            position = start + token.start()
            if position < skip:
                continue
            if token[0].startswith("`"):
                close = re.compile(rf"(?<!`){token[0]}(?!`)").search(text, start + token.end())
                if close and not any(
                    _markdown.markdown_fence_opening(following)
                    for following in text[offset:close.end()].splitlines()
                ):
                    skip = close.end()
            else:
                link = LINK.match(text, position)
                if link:
                    links.append(link[1] or link[2])
                    skip = link.end()
    return links


def heading_contract(text: str, anchor: str) -> tuple[int, int]:
    """Return the complete heading body, including nested headings and fences."""
    headings = []
    counts: dict[str, int] = {}
    fence = None
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if fence:
            if _markdown.markdown_fence_closes(line, *fence):
                fence = None
            continue
        opening = _markdown.markdown_fence_opening(line)
        if opening:
            fence = opening[:2]
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            slug = re.sub(r"[^\w\s-]", "", match[2].lower()).replace(" ", "-")
            count = counts.get(slug, 0)
            counts[slug] = count + 1
            headings.append((index, len(match[1]), f"{slug}-{count}" if count else slug))
    for offset, (start, level, slug) in enumerate(headings):
        if slug == anchor:
            end = next((line for line, depth, _ in headings[offset + 1:] if depth <= level), len(lines))
            return start, end
    raise ValueError(f"Missing Markdown anchor: #{anchor}")


def linked_contracts(
    planning_dir: Path, seeds: list[tuple[Path, str]], *, known_paths: set[Path] | None = None,
) -> dict[Path, list[tuple[int, int, str]]]:
    """Follow explicit local links; return merged source spans in discovery order."""
    root = planning_dir.resolve()
    allowed = {path.resolve() for path in known_paths or ()}
    queue = deque(seeds)
    seen: set[tuple[Path, str]] = set()
    texts: dict[Path, str] = {}
    spans: dict[Path, list[tuple[int, int]]] = {}
    while queue:
        origin, body = queue.popleft()
        for link in local_links(body):
            target = urlsplit(link)
            if target.scheme or target.netloc or target.query:
                continue
            name, anchor = unquote(target.path), unquote(target.fragment)
            if name and Path(name).suffix.lower() != ".md":
                continue
            if not name and not anchor:
                continue
            path = (origin.parent / name).resolve() if name else origin.resolve()
            if not path.is_relative_to(root) and path not in allowed:
                raise ValueError(f"Contract link leaves the planning directory: {origin}: {link}")
            key = path, anchor
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > MAX_CONTRACTS:
                raise ValueError(f"Contract links exceed the {MAX_CONTRACTS}-reference limit.")
            if path not in texts:
                if not path.is_file():
                    raise ValueError(f"Missing linked contract: {origin}: {link}")
                if path.stat().st_size > MAX_SOURCE_BYTES:
                    raise ValueError(f"Linked contract exceeds the {MAX_SOURCE_BYTES}-byte limit: {path}")
                texts[path] = _storage.read_text(path)
            text = texts[path]
            try:
                start, end = heading_contract(text, anchor) if anchor else (0, len(text.splitlines()))
            except ValueError as exc:
                raise ValueError(f"{exc} in {path} (linked from {origin})") from exc
            excerpt = "\n".join(text.splitlines()[start:end]).rstrip()
            spans.setdefault(path, []).append((start, end))
            queue.append((path, excerpt))
    result = {}
    for path, ranges in spans.items():
        merged: list[tuple[int, int]] = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = merged[-1][0], max(merged[-1][1], end)
            else:
                merged.append((start, end))
        lines = texts[path].splitlines()
        result[path] = [(start + 1, end, "\n".join(lines[start:end]).rstrip()) for start, end in merged]
    return result
