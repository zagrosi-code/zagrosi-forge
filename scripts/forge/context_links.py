"""Bounded, complete Markdown contracts reached through explicit local links."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import posixpath
import stat
import re
from urllib.parse import unquote, urlsplit

from . import markdown as _markdown
from . import storage as _storage
from . import session as _session


LINK = re.compile(r'(?<!!)\[[^\]\n]+\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+["\'][^\n]*?["\'])?\s*\)')
INLINE_TOKEN = re.compile(r"`+|(?<!!)\[")
MAX_CONTRACTS = 64
MAX_SOURCE_BYTES = 1_048_576


# Namespaced copies keep ordinary relative Markdown links readable in Git while
# prepared workspaces remain self-contained and bound to their source blobs.
SHARED_DIRECTORY = ".forge-shared-documents"


def shared_documents(meta: dict) -> list[str]:
    from . import team_state

    if "shared_documents" not in meta:
        return []
    names = meta["shared_documents"]
    if not isinstance(names, list) or len(names) > MAX_CONTRACTS:
        raise ValueError("shared_documents requires at most 64 repository-relative Markdown files.")
    try:
        for name in names:
            if (team_state.normalize_paths([name]) != [name] or not name.endswith(".md")
                    or any(part.casefold() in {".git", "implementation", "node_modules", SHARED_DIRECTORY}
                           for part in name.split("/"))):
                raise ValueError("shared_documents requires explicit portable Markdown paths outside private directories.")
    except team_state.TeamError as exc:
        raise ValueError("Invalid shared_documents path: " + str(exc)) from exc
    if len({team_state._portable(name) for name in names}) != len(names):
        raise ValueError("shared_documents repeats a portable path.")
    return names


def bundle_path(origin: str, link_path: str, canonical: str, shared) -> str:
    """Map a normal relative link to its logical location inside the prepared bundle."""
    origin_path = (origin.removeprefix(SHARED_DIRECTORY + "/") if origin.startswith(SHARED_DIRECTORY + "/")
                   else posixpath.join(canonical, origin))
    destination = posixpath.normpath(posixpath.join(posixpath.dirname(origin_path), link_path))
    local = posixpath.relpath(destination, canonical)
    if local == ".." or local.startswith("../"):
        if destination not in shared:
            raise ValueError("Contract link leaves the planning directory without a shared_documents declaration.")
        return SHARED_DIRECTORY + "/" + destination
    if local.split("/")[0].casefold() == SHARED_DIRECTORY:
        raise ValueError("Contract link uses the reserved shared document directory.")
    return local


@dataclass(frozen=True)
class SharedDocuments:
    names: list[str]
    canonical: str
    source_root: Path

    def contains(self, path: Path) -> bool:
        return path.is_relative_to(self.source_root) and path.relative_to(self.source_root).as_posix() in self.names


def _shared_paths(planning: Path, plan_metadata: dict | None) -> SharedDocuments | None:
    from . import artifacts, team_git, team_plans, team_state

    if plan_metadata is None:
        descriptor = artifacts.compact_plan_descriptor(planning)
        plan_metadata = descriptor["metadata"] if descriptor else {}
    names = shared_documents(plan_metadata)
    if not names:
        return None
    marker = planning / team_plans.MARKER
    if marker.exists():
        try:
            prepared = team_plans.validate(planning)
        except team_state.TeamError as exc:
            raise ValueError(str(exc)) from exc
        return SharedDocuments(names, prepared["path"], planning / SHARED_DIRECTORY)
    if any(part == ".git" for part in planning.parts):
        raise ValueError("A private prepared plan requires its bound marker for shared documents.")
    try:
        git_root = Path(team_git._path_output(planning, "rev-parse", "--show-toplevel")).resolve()
    except team_state.TeamError as exc:
        raise ValueError("Shared documents require a plan inside a Git worktree.") from exc
    if any((git_root / name).is_relative_to(planning) for name in names):
        raise ValueError("shared_documents names documents outside the planning directory.")
    return SharedDocuments(names, planning.relative_to(git_root).as_posix(), git_root)


def _shared_link(root: Path, origin: Path, name: str, sharing: SharedDocuments) -> Path:
    # A snapshot copy and a working-copy document have the same logical name.
    original = (origin.relative_to(root).as_posix() if origin.is_relative_to(root)
                else SHARED_DIRECTORY + "/" + origin.relative_to(sharing.source_root).as_posix())
    if not name:
        return origin
    if Path(name).is_absolute() or "\\" in name:
        raise ValueError("Shared contract links must be repository-relative Markdown links.")
    dest = bundle_path(original, name, sharing.canonical, sharing.names)
    if not dest.startswith(SHARED_DIRECTORY + "/"):
        return root / dest
    path = sharing.source_root / dest.removeprefix(SHARED_DIRECTORY + "/")
    for current in (path, *path.parents):
        if _session.observe_path(current).is_symlink():
            raise ValueError("Shared document paths cannot contain symlinks.")
        if current == sharing.source_root:
            break
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Shared documents must be regular single-link files.")
    return path


def markdown_target(link: str) -> tuple[str, str] | None:
    """Return the decoded document and heading for a local Markdown reference."""
    target = urlsplit(link)
    if target.scheme or target.netloc or target.query:
        return None
    name, anchor = unquote(target.path), unquote(target.fragment)
    if name and Path(name).suffix.lower() != ".md":
        return None
    return (name, anchor) if name or anchor else None


def local_links(text: str, *, line_spans: list[tuple[int, int]] | None = None) -> list[str]:
    """Lex the full source; optional line selection cannot activate comments/code."""
    text = _markdown.visible_markdown(text)
    links = []
    offset = skip = 0
    fence = None
    for number, line in enumerate(text.splitlines(keepends=True), 1):
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
                    if line_spans is None or any(first <= number <= last for first, last in line_spans):
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
    seed_ranges: dict[Path, list[tuple[int, int]]] | None = None,
    _causes: list[tuple[Path, int, int, dict]] | None = None,
    _plan_metadata: dict | None = None,
) -> dict[Path, list[tuple[int, int, str]]]:
    """Follow explicit local links; return merged source spans in discovery order."""
    root = planning_dir.resolve()
    sharing = _shared_paths(root, _plan_metadata)
    allowed = {path.resolve() for path in known_paths or ()}
    queue = deque((path, body, (seed_ranges or {}).get(path.resolve())) for path, body in seeds)
    seen: set[tuple[Path, str]] = set()
    texts: dict[Path, str] = {}
    spans: dict[Path, list[tuple[int, int]]] = {}
    while queue:
        origin, body, line_spans = queue.popleft()
        for link in local_links(body, line_spans=line_spans):
            reference = markdown_target(link)
            if reference is None:
                continue
            name, anchor = reference
            link_path = origin.parent / name if name else origin
            if sharing and (origin.is_relative_to(root) or sharing.contains(origin)) and link_path.resolve() not in allowed:
                expected = _shared_link(root, origin, name, sharing)
                path = _session.observe_path(expected).resolve()
                if path != expected and not path.is_relative_to(root):
                    raise ValueError(f"Shared contract link crosses a symlink: {link}")
            else:
                path = _session.observe_path(link_path).resolve()
            shared_file = sharing and sharing.contains(path)
            if not path.is_relative_to(root) and path not in allowed and not shared_file:
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
                if shared_file:
                    from . import team_plans, team_state

                    try:
                        texts[path] = team_plans._read(path, MAX_SOURCE_BYTES).decode("utf-8")
                    except team_state.TeamError as exc:
                        raise ValueError(str(exc)) from exc
                else:
                    texts[path] = _storage.read_text(path)
            text = texts[path]
            try:
                start, end = heading_contract(text, anchor) if anchor else (0, len(text.splitlines()))
            except ValueError as exc:
                raise ValueError(f"{exc} in {path} (linked from {origin})") from exc
            excerpt = "\n".join(text.splitlines()[start:end]).rstrip()
            spans.setdefault(path, []).append((start, end))
            if _causes is not None:
                _causes.append((path, start + 1, end, {"origin": str(origin), "link": link, "anchor": anchor or None}))
            queue.append((path, excerpt, None))
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
