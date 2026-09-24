"""Forge markdown."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re
import shlex

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


def passing_review(text: str, *, allow_legacy: bool = False) -> bool:
    """Validate current verdicts; fenced examples cannot supply review evidence."""
    blocks, lines = split_markdown_fences_with_closure(text)
    if any(not closed for _, _, closed in blocks):
        return False
    fields: dict[str, list[str]] = {"verdict": [], "reviewed": []}
    body: list[str] = []
    legacy_verdicts: list[str] = []
    normalized: list[str] = []
    for raw in lines:
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", raw).strip().replace("**", "")
        normalized.append(line)
        match = re.match(r"(?i)^(verdict|reviewed):\s*(.*)$", line)
        if match:
            fields[match[1].lower()].append(match[2].strip())
        elif line and not line.startswith("#"):
            body.append(line)
            if re.match(r"(?i)^(?:pass|fixed|blocked|fail|failed|pending|unresolved)[ \t]*[:—-]", line):
                legacy_verdicts.append(line)
    verdicts = fields["verdict"] + legacy_verdicts
    if verdicts and not all(
        re.match(r"(?i)^(?:pass|fixed)\b", verdict)
        and not re.search(r"(?i)(?:^|[/|,;:—(\[]|\s-\s|\b(?:and|or)\b)\s*(?:blocked|fail(?:ed)?|pending|unresolved)\b", verdict)
        for verdict in verdicts
    ):
        return False
    reviewed = fields["reviewed"]
    substantive_scope = bool(reviewed) and all(
        value.strip("`*. ").lower() not in {"", "none", "n/a", "tbd", "todo", "pending"}
        for value in reviewed
    )
    if not allow_legacy:
        return bool(fields["verdict"] and substantive_scope)
    legacy_pass = re.search(
        r"(?im)^(?:pass:[ \t]*)?no (?:blocking|material) findings(?:[.!]|\s*$)", "\n".join(normalized),
    )
    if reviewed:
        return bool((verdicts or legacy_pass) and substantive_scope)
    return bool(legacy_pass or verdicts and body)


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


def visible_markdown(text: str) -> str:
    """Remove HTML comments without treating code or escaped openers as comments."""
    if "<!--" not in text:
        return text
    offset = skip = 0
    fence = None
    removed: list[tuple[int, int]] = []
    for line in text.splitlines(keepends=True):
        start, offset = offset, offset + len(line)
        if fence:
            if markdown_fence_closes(line, *fence):
                fence = None
            continue
        if skip >= offset:
            continue
        opening = markdown_fence_opening(line) if skip <= start else None
        if opening:
            fence = opening[:2]
            continue
        for token in re.finditer(r"<!--|`+", line):
            position = start + token.start()
            if position < skip:
                continue
            escape_start = position
            while escape_start and text[escape_start - 1] == "\\":
                escape_start -= 1
            if (position - escape_start) % 2:
                continue
            if token[0] == "<!--":
                close = text.find("-->", position + 4)
                skip = len(text) if close < 0 else close + 3
                removed.append((position, skip))
            else:
                close = re.compile(rf"(?<!`){token[0]}(?!`)").search(text, start + token.end())
                if close and not any(markdown_fence_opening(row) for row in text[offset:close.end()].splitlines()):
                    skip = close.end()
    parts = []
    previous = 0
    for start, end in removed:
        parts.extend((text[previous:start], re.sub(r"[^\r\n]", " ", text[start:end])))
        previous = end
    return "".join(parts) + text[previous:]


def is_test_command(command: str) -> bool:
    """Recognize runner invocations without executing shell input."""
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    while words:
        executable, *words = words
        if re.fullmatch(r"[A-Za-z_]\w*=.*", executable):
            continue
        name = executable.replace("\\", "/").rsplit("/", 1)[-1]
        name = re.sub(r"\.(?:exe|cmd|bat)$", "", name)
        if name in {"env", "npx"}:
            continue
        if name in {"uv", "poetry", "pipenv", "hatch"} and words[:1] == ["run"]:
            words = words[1:]
            while name == "uv" and words and (words[0] == "--with" or words[0].startswith("--with=")):
                option, *words = words
                if option == "--with":
                    if not words or not words[0] or words[0].startswith("-"):
                        return False
                    words = words[1:]
                elif not option.removeprefix("--with="):
                    return False
            continue
        if re.fullmatch(r"python[\d.]*", name):
            while words and words[0] != "-m":
                option, *words = words
                if option in {"-W", "-X"} and words:
                    words = words[1:]
                elif not re.fullmatch(r"-(?:[BEIOPqsSuvx]+|W.+|X.+)", option):
                    return False
            if words[:1] != ["-m"]:
                return False
            return len(words) > 1 and words[1] in {"pytest", "unittest"}
        if name in {"pytest", "unittest", "vitest", "jest", "ctest", "rspec"}:
            return True
        if name == "node":
            return words[:1] == ["--test"]
        if name in {"go", "cargo", "dotnet", "swift", "mix", "mvn", "gradle", "gradlew", "npm", "pnpm", "yarn", "bun", "make"}:
            if words[:1] == ["run"]:
                words = words[1:]
            return bool(words and re.match(r"test\b", words[0]))
        return False
    return False


def has_verification(text: str) -> bool:
    """Read visible regression/inspection contracts once for every quality gate."""
    blocks, plain_lines = split_markdown_fences_with_closure(visible_markdown(text))
    if any(not closed for _, _, closed in blocks):
        return False
    fields: dict[str, str] = {}
    cases: list[dict[str, str]] = []
    body = []
    aliases = {"test_case": "case", "behavior": "case", "test_command": "command"}
    for line in plain_lines:
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).replace("**", "")
        match = re.fullmatch(r"\s*([\w ]+):[ \t]*(.*)", line)
        if not match:
            body.append(line)
            continue
        key, value = match.groups()
        key = re.sub(r"\s+", "_", key.strip().lower())
        key = aliases.get(key, key)
        if key not in {"case", "expected", "command", "verification_mode", "inspection", "test_rationale"}:
            body.append(line)
            continue
        value = value.strip().strip("`* ")
        if key == "case" and (not cases or cases[-1].get("case") != value):
            cases.append({})
        record = cases[-1] if cases and key in {"case", "expected", "command"} else fields
        if value.lower().rstrip(".") in {"", "none", "n/a", "tbd", "todo", "pending"} or key in record and record[key] != value:
            return False
        record[key] = value
    if fields.get("verification_mode", "").lower() == "inspection":
        rationale = fields.get("test_rationale", "")
        scope = re.search(r"\b(?:docs?|documentation|cosmetic|formatting)[ -]only\b", rationale, re.I)
        return bool(scope and fields.get("inspection") and fields.get("expected"))
    if fields.get("verification_mode", "").lower() not in {"", "test", "automated"}:
        return False
    plain = "\n".join(body)
    code = "\n".join("\n".join(lines) for language, lines, _ in blocks if language not in {"", "text", "txt", "plain", "plaintext", "markdown", "md"})
    commands = re.findall(r"`([^`\n]+)`", plain) + code.splitlines() + re.findall(r"(?im)^\s*run\s+(.+)$", plain)
    command = fields.get("command") or any(is_test_command(item) for item in commands)
    if cases:
        return all(case.get("expected") and (case.get("command") or command) for case in cases)
    case = re.search(
        r"\b(?:test_[A-Za-z0-9_]+|Test[A-Z][A-Za-z0-9_]*)\b(?![.:])|"
        r"\b(?:it|test)\(\s*[\"'][^\"']+[\"']|"
        r"`[a-z][a-z0-9_]*_[a-z0-9_]+`\s+(?:expects|verifies|asserts)\b|"
        r"(?i:write\s+(?:red|failing)\b[^\n]*|(?:test\s+)?cases?\s*:)\s*`[a-z][a-z0-9_]*_[a-z0-9_]+`",
        plain + "\n" + code,
    )
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
