"""Forge ownership."""

from __future__ import annotations

import re
import shlex

from . import markdown as _markdown
from . import policy as _policy

def extract_file_paths(text: str) -> list[str]:
    paths = {match.group(0).strip("`").removeprefix("./") for match in _policy.FILE_PATH_RE.finditer(text)}
    return sorted(paths)


def normalize_owned_path(value: str) -> str | None:
    candidate = value.strip()
    if len(candidate) >= 2 and candidate.startswith("`") and candidate.endswith("`"):
        candidate = candidate[1:-1].strip()
    candidate = candidate.removeprefix("./")
    return candidate if _policy.OWNED_PATH_RE.fullmatch(candidate) else None


def safe_legacy_file_paths(text: str) -> list[str]:
    paths = {path for value in extract_file_paths(text) if (path := normalize_owned_path(value))}
    return sorted(paths)


def ownership_declaration_end(text: str) -> int | None:
    active_fence: tuple[str, int] | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        if active_fence:
            if _markdown.markdown_fence_closes(line, *active_fence):
                active_fence = None
        elif opening := _markdown.markdown_fence_opening(line):
            active_fence = opening[:2]
        elif match := _policy.OWNERSHIP_DECLARATION_RE.search(line):
            return offset + match.end()
        offset += len(line)
    return None


def standalone_owned_path(line: str) -> str | None:
    candidate = line.strip()
    list_item = re.fullmatch(r"(?:[-*+]|\d+[.)])\s+(.+)", candidate)
    if list_item:
        candidate = list_item.group(1).strip()
    return normalize_owned_path(candidate)


def owned_paths_from_body(body: str) -> list[str]:
    fenced_blocks, plain_lines = _markdown.split_markdown_fences_with_closure(body)
    fenced_paths: set[str] = set()
    rejected_ownership_fence = False
    for language, lines, closed in fenced_blocks:
        if language not in {"", "text", "plaintext"}:
            continue
        if not closed:
            rejected_ownership_fence = True
            continue
        fenced_paths.update(
            path for line in lines if (path := standalone_owned_path(line))
        )
    if fenced_paths:
        return sorted(fenced_paths)
    if rejected_ownership_fence:
        return []

    structured: set[str] = set()
    for line in plain_lines:
        is_indented = line.startswith(("    ", "\t"))
        is_list_item = bool(re.match(r"\s*(?:[-*+]|\d+[.)])\s+", line))
        if (is_indented or is_list_item) and (path := standalone_owned_path(line)):
            structured.add(path)
    return sorted(structured) if structured else safe_legacy_file_paths("\n".join(plain_lines))


def extract_section_owned_paths(text: str) -> list[str]:
    found_ownership_section = False
    for title, body in _markdown.markdown_h2_sections(text):
        title_declares_ownership = bool(_policy.OWNERSHIP_TITLE_RE.search(title))
        declaration_end = ownership_declaration_end(body)
        if not (title_declares_ownership or declaration_end is not None):
            continue
        found_ownership_section = True
        ownership_body = body[declaration_end:] if declaration_end is not None else body
        if paths := owned_paths_from_body(ownership_body):
            return paths
    return [] if found_ownership_section else safe_legacy_file_paths(text)


def add_shell_token_owned_path_reference(
    references: set[str],
    token: str,
    owned_paths: set[str],
) -> None:
    candidates = [token]
    if "=" in token:
        candidates.append(token.rsplit("=", 1)[1])
    for candidate in candidates:
        path = candidate.removeprefix("./").split("::", 1)[0]
        if path in owned_paths:
            references.add(path)


def shell_line_literal_heredocs(line: str) -> tuple[list[tuple[str, bool]], bool]:
    heredocs: list[tuple[str, bool]] = []
    arithmetic_depth = 0
    index = 0
    while index < len(line):
        if line.startswith("\\\n", index):
            index += 2
            continue
        char = line[index]
        if char == "#" and (index == 0 or line[index - 1].isspace() or line[index - 1] in ";|&()"):
            break
        if char in {"'", '"'}:
            quote = char
            index += 1
            while index < len(line) and line[index] != quote:
                if quote == '"' and line[index] == "\\" and index + 1 < len(line):
                    index += 2
                else:
                    index += 1
            if index >= len(line):
                return [], True
            index += 1
            continue
        if char == "\\":
            if index + 1 >= len(line):
                return [], True
            index += 2
            continue
        if line.startswith("((", index):
            arithmetic_depth += 1
            index += 2
            continue
        if line.startswith("))", index) and arithmetic_depth:
            arithmetic_depth -= 1
            index += 2
            continue
        if arithmetic_depth or not line.startswith("<<", index):
            index += 1
            continue
        if line.startswith("<<<", index):
            index += 3
            continue

        index += 2
        strip_tabs = index < len(line) and line[index] == "-"
        if strip_tabs:
            index += 1
        while index < len(line):
            if line.startswith("\\\n", index):
                index += 2
            elif line[index].isspace():
                index += 1
            else:
                break

        delimiter_parts: list[str] = []
        while index < len(line) and not line[index].isspace() and line[index] not in ";|&()<>#":
            char = line[index]
            if char in {"'", '"'}:
                quote = char
                end = line.find(quote, index + 1)
                if end == -1:
                    return [], True
                part = line[index + 1 : end]
                if part and not re.fullmatch(r"[A-Za-z0-9_.-]+", part):
                    return [], True
                delimiter_parts.append(part)
                index = end + 1
                continue
            if not re.fullmatch(r"[A-Za-z0-9_.-]", char):
                return [], True
            delimiter_parts.append(char)
            index += 1
        delimiter = "".join(delimiter_parts)
        if not _policy.SHELL_HEREDOC_DELIMITER_RE.fullmatch(delimiter):
            return [], True
        heredocs.append((delimiter, strip_tabs))
    return heredocs, False


def shell_lines_without_literal_heredoc_bodies(lines: list[str]) -> tuple[list[str], bool]:
    lexical_lines: list[str] = []
    line_index = 0
    while line_index < len(lines):
        command_lines = [lines[line_index]]
        line_index += 1
        while (
            (len(command_lines[-1]) - len(command_lines[-1].rstrip("\\"))) % 2 == 1
            and line_index < len(lines)
        ):
            command_lines.append(lines[line_index])
            line_index += 1
        heredocs, malformed = shell_line_literal_heredocs("\n".join(command_lines))
        lexical_lines.extend(command_lines)
        if malformed:
            return lexical_lines, True
        for delimiter, strip_tabs in heredocs:
            closed = False
            while line_index < len(lines):
                candidate = lines[line_index].lstrip("\t") if strip_tabs else lines[line_index]
                line_index += 1
                if candidate == delimiter:
                    closed = True
                    break
            if not closed:
                return lexical_lines, True
    return lexical_lines, False


def shell_gate_owned_path_references(text: str, owned_paths: set[str]) -> tuple[set[str], bool]:
    references: set[str] = set()
    malformed = False
    for language, lines, closed in _markdown.split_markdown_fences_with_closure(text)[0]:
        if language not in _policy.SHELL_FENCE_LANGUAGES:
            continue
        malformed = malformed or not closed
        lexical_lines, heredoc_malformed = shell_lines_without_literal_heredoc_bodies(lines)
        malformed = malformed or heredoc_malformed
        lexer = shlex.shlex("\n".join(lexical_lines), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        while True:
            try:
                token = lexer.get_token()
            except ValueError:
                malformed = True
                if lexer.token:
                    add_shell_token_owned_path_reference(references, lexer.token, owned_paths)
                break
            if token == lexer.eof:
                break
            add_shell_token_owned_path_reference(references, token, owned_paths)
    return references, malformed
