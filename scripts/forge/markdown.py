"""Forge markdown."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re

from . import policy as _policy

def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def word_budgets(depth: str) -> dict[str, int]:
    return _policy.WORD_BUDGETS.get(depth, _policy.WORD_BUDGETS["standard"])


def is_lean_depth(depth: str | None) -> bool:
    return depth in {"lean", "fast"}


def contains_any(text: str, terms: list[str]) -> bool:
    haystack = text.lower()
    return any(term.lower() in haystack for term in terms)


def is_test_path(text: str) -> bool:
    filename = text.rsplit("/", 1)[-1]
    if filename.lower().endswith(
        (".stories.js", ".stories.jsx", ".stories.mjs", ".stories.ts", ".stories.tsx")
    ):
        return True
    test_tokens = {"test", "tests", "spec", "specs"}
    if any(
        test_tokens.intersection(re.split(r"[._-]+", component.lower()))
        for component in text.split("/")
    ):
        return True
    stem = filename.rsplit(".", 1)[0]
    return stem.lower() == "conftest" or bool(
        re.search(r"^(?:Test|Spec)[A-Z0-9]|(?:Test|Tests|Spec|Specs)$", stem)
    )


def requirement_ids(text: str) -> list[str]:
    return sorted(set(_policy.REQ_ID_RE.findall(text)))


def parse_forge_meta(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    raw = extract_block(text, _policy.FORGE_META_START, "END_FORGE_META")
    if raw is None:
        raw = extract_block(text, _policy.LEGACY_META_START, "END_DEEP_META")
    if raw is None:
        return None, ["Missing FORGE_META block"]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"Invalid FORGE_META JSON: {exc}"]
    if not isinstance(payload, dict):
        return None, ["FORGE_META must contain a JSON object"]
    return payload, []


def parse_deep_meta(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    return parse_forge_meta(text)


def normalize_repeated(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def extract_block(text: str, start: str, end: str) -> str | None:
    pattern = re.compile(rf"<!--\s*{re.escape(start)}\s*\n(.*?)\n{re.escape(end)}\s*-->", re.S)
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip()


def parse_numbered_manifest(text: str, block: str, item_re: re.Pattern[str], prefix: str = "") -> tuple[list[str], list[str]]:
    raw = extract_block(text, block, "END_MANIFEST")
    if raw is None:
        return [], [f"Missing {block} block"]

    items: list[str] = []
    errors: list[str] = []
    for line_no, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not item_re.match(line):
            errors.append(f"Line {line_no}: invalid item {line!r}")
            continue
        items.append(line)

    expected = 1
    for item in items:
        number_part = item.removeprefix(prefix).split("-", 1)[0]
        actual = int(number_part)
        if actual != expected:
            errors.append(f"Expected {expected:02d}, got {actual:02d} in {item}")
        expected += 1

    if not items and not errors:
        errors.append(f"{block} block is empty")
    return items, errors


def parse_project_config(text: str) -> tuple[dict[str, str], list[str]]:
    raw = extract_block(text, "PROJECT_CONFIG", "END_PROJECT_CONFIG")
    if raw is None:
        return {}, ["Missing PROJECT_CONFIG block"]

    config: dict[str, str] = {}
    errors: list[str] = []
    for line_no, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not _policy.CONFIG_RE.match(line):
            errors.append(f"Line {line_no}: invalid config entry {line!r}")
            continue
        key, value = line.split(":", 1)
        config[key.strip()] = value.strip()

    for key in ("runtime", "test_command"):
        if key not in config:
            errors.append(f"PROJECT_CONFIG missing required field: {key}")
    return config, errors


def markdown_fence_opening(line: str) -> tuple[str, int, str] | None:
    match = re.fullmatch(r" {0,3}(`{3,}|~{3,})([^\r\n]*)", line.rstrip("\r\n"))
    if not match:
        return None
    marker = match.group(1)
    info = match.group(2).strip()
    language = info.split(maxsplit=1)[0].casefold() if info else ""
    return marker[0], len(marker), language


def markdown_fence_closes(line: str, marker: str, minimum: int) -> bool:
    return bool(re.fullmatch(rf" {{0,3}}{re.escape(marker)}{{{minimum},}}[ \t]*", line.rstrip("\r\n")))


def markdown_h2_sections(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines(keepends=True)
    headings: list[tuple[int, str]] = []
    active_fence: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        if active_fence:
            if markdown_fence_closes(line, *active_fence):
                active_fence = None
            continue
        opening = markdown_fence_opening(line)
        if opening:
            active_fence = opening[:2]
            continue
        heading = re.fullmatch(r"##[ \t]+(.+?)[ \t]*", line.rstrip("\r\n"))
        if heading:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(1)).strip()
            headings.append((index, title))

    sections: list[tuple[str, str]] = []
    for index, (line_number, title) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
        sections.append((title, "".join(lines[line_number + 1 : end])))
    return sections


def split_markdown_fences_with_closure(
    text: str,
) -> tuple[list[tuple[str, list[str], bool]], list[str]]:
    blocks: list[tuple[str, list[str], bool]] = []
    plain_lines: list[str] = []
    active_fence: tuple[str, int, str] | None = None
    block_lines: list[str] = []
    for line in text.splitlines():
        if active_fence:
            marker, minimum, language = active_fence
            if markdown_fence_closes(line, marker, minimum):
                blocks.append((language, block_lines, True))
                active_fence = None
                block_lines = []
            else:
                block_lines.append(line)
            continue
        opening = markdown_fence_opening(line)
        if opening:
            active_fence = opening
        else:
            plain_lines.append(line)
    if active_fence:
        blocks.append((active_fence[2], block_lines, False))
    return blocks, plain_lines


def split_markdown_fences(text: str) -> tuple[list[tuple[str, list[str]]], list[str]]:
    blocks, plain_lines = split_markdown_fences_with_closure(text)
    return [(language, lines) for language, lines, _ in blocks], plain_lines


def markdown_headings(text: str) -> list[str]:
    return [line.strip("# ").strip() for line in text.splitlines() if line.startswith("#")]


def test_names(text: str) -> list[str]:
    found: set[str] = set()
    for pattern in (
        r"\btest_[A-Za-z0-9_]+\b(?!\.)",
        r"\bTest[A-Z][A-Za-z0-9_]*\b",
        r"`([a-z][a-z0-9_]*_[a-z0-9_]+)`",
        r"\bit\([\"']([^\"']+)[\"']\)",
        r"\bdescribe\([\"']([^\"']+)[\"']\)",
    ):
        for match in re.finditer(pattern, text):
            found.add(match.group(1) if match.groups() else match.group(0))
    return sorted(found)


def has_verification(text: str) -> bool:
    """Accept executable regression cases or explicitly justified inspection."""
    _, plain_lines = split_markdown_fences_with_closure(text)
    fields = {}
    for line in plain_lines:
        match = re.fullmatch(r"\s*(?:[-*]\s+)?([\w ]+):[ \t]*(\S.*)", line)
        if match:
            key, value = match.groups()
            value = value.strip().strip("`* ")
            if value.lower().rstrip(".") not in {"none", "n/a", "tbd", "todo", "pending"}:
                fields[key.lower().replace(" ", "_")] = value
    if fields.get("verification_mode", "").lower() == "inspection":
        rationale = fields.get("test_rationale", "")
        scope = re.search(r"\b(?:docs?|documentation|cosmetic|formatting)[ -]only\b", rationale, re.I)
        return bool(scope and fields.get("inspection") and fields.get("expected"))

    command = fields.get("command") or fields.get("test_command") or re.search(
        r"\b(?:pytest|vitest|jest|ctest|rspec)\b|"
        r"\b(?:go|cargo|dotnet|swift|mix|mvn|gradle|gradlew|npm|pnpm|yarn|bun|make)\s+(?:run\s+)?test\b",
        text,
    )
    case = test_names(text) or ((fields.get("case") or fields.get("behavior")) and fields.get("expected"))
    return bool(command and case)


def section_owned_test_names(text: str) -> list[str]:
    """Return concrete test cases, excluding identifiers inferred from paths."""
    found: set[str] = set(re.findall(r"\btest_[A-Za-z0-9_]+\b(?!\.)", text))
    for pattern in (r"\bit\([\"']([^\"']+)[\"']\)", r"\bdescribe\([\"']([^\"']+)[\"']\)"):
        found.update(re.findall(pattern, text))
    referenced_test_modules = {
        Path(path).stem
        for path in re.findall(r"\b(?:tests|terraform)/[A-Za-z0-9_./-]+\.py\b", text)
    }
    return sorted(found - referenced_test_modules)


def markdown_tables(text: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
                continue
            current.append(cells)
        else:
            if current:
                tables.append(current)
                current = []
    if current:
        tables.append(current)
    return tables


def table_has_columns(tables: list[list[list[str]]], required: list[str]) -> bool:
    required_lower = [item.lower() for item in required]
    for table in tables:
        if not table:
            continue
        header = [cell.lower() for cell in table[0]]
        if all(item in header for item in required_lower):
            return True
    return False
