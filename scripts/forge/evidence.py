"""Forge evidence."""

from __future__ import annotations

from pathlib import Path
import argparse
import configparser
import json
import os
import re
import shlex
import subprocess
import tomllib

from . import markdown as _markdown
from . import output as _output
from . import policy as _policy
from . import storage as _storage

def evidence_path_ignored(relative_path: Path) -> bool:
    parts = set(relative_path.parts)
    if parts.intersection(_policy.EVIDENCE_IGNORE_PARTS):
        return True
    if ".codex" in parts and "cache" in parts:
        return True
    if ".agents" in parts and "plugins" in parts and "cache" in parts:
        return True
    return False


def evidence_files(target_dir: Path) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "."],
            cwd=target_dir, capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            paths = {Path(os.fsdecode(name)) for name in result.stdout.split(b"\0") if name}
            files = set()
            for path in paths:
                if not path.parts or path.is_absolute() or ".." in path.parts or evidence_path_ignored(path):
                    continue
                child = target_dir / path
                if child.is_file():
                    files.add(path)
                elif child.is_dir() and not any((target_dir / parent).is_symlink()
                                               for parent in (path, *path.parents) if parent.parts):
                    # Git lists nested repositories/submodules as directory boundaries.
                    files.update(path / nested for nested in evidence_files(child))
            return sorted(files, key=lambda path: path.as_posix())
    except (OSError, subprocess.TimeoutExpired):
        pass
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(target_dir):
        directory = Path(dirpath)
        relative_dir = directory.relative_to(target_dir)
        dirnames[:] = [
            name
            for name in dirnames
            if not evidence_path_ignored((relative_dir / name) if relative_dir.parts else Path(name))
        ]
        for filename in filenames:
            relative = (relative_dir / filename) if relative_dir.parts else Path(filename)
            if not evidence_path_ignored(relative):
                files.append(relative)
    return sorted(files, key=lambda item: item.as_posix())


def cap_paths(paths: list[str], limit: int = 80) -> tuple[list[str], int]:
    ordered = sorted(dict.fromkeys(paths))
    return ordered[:limit], max(0, len(ordered) - limit)


def markdown_path_list(paths: list[str]) -> str:
    return "\n".join(f"- `{path}`" for path in paths) if paths else "- None found"


def optional_text(path: Path) -> str:
    try:
        return _storage.read_text(path) if path.stat().st_size <= 1024 * 1024 else ""
    except (OSError, UnicodeError):
        return ""


def python_test_command(target_dir: Path, directory: Path, available: set[Path], tests: list[Path]) -> str | None:
    try:
        project = tomllib.loads(optional_text(target_dir / directory / "pyproject.toml")) if directory / "pyproject.toml" in available else {}
    except tomllib.TOMLDecodeError:
        return None
    metadata, tool = project.get("project", {}), project.get("tool", {})
    if not isinstance(metadata, dict) or not isinstance(tool, dict):
        return None
    groups = [metadata.get("dependencies", [])]
    for mapping in (metadata.get("optional-dependencies", {}), project.get("dependency-groups", {})):
        if isinstance(mapping, dict):
            groups.extend(mapping.values())
    pytest_configured = "pytest" in tool or any(
        isinstance(dep, str) and re.match(r"(?i)pytest(?:\W|$)", dep.strip())
        for group in groups if isinstance(group, list) for dep in group
    ) or any(path.name == "conftest.py" for path in tests) or any(
        directory / name in available for name in ("pytest.ini", ".pytest.ini")
    )
    for name, section in (("pytest.ini", "pytest"), (".pytest.ini", "pytest"), ("setup.cfg", "tool:pytest"), ("tox.ini", "pytest")):
        if directory / name not in available:
            continue
        config = configparser.ConfigParser()
        try:
            config.read_string(optional_text(target_dir / directory / name))
        except configparser.Error:
            continue
        pytest_configured |= config.has_section(section)
    if pytest_configured:
        uv = "uv" in tool or any(parent / "uv.lock" in available for parent in (directory, *directory.parents))
        return "uv run pytest" if uv else "python -m pytest"
    if any(re.search(r"(?m)^(?:from unittest\b|import unittest\b)", optional_text(target_dir / path)) for path in tests[:80]):
        suffix = " -s tests" if any(path.is_relative_to(directory / "tests") for path in tests) else ""
        return "python -m unittest discover" + suffix
    return None


def repository_commands(target_dir: Path, files: list[Path]) -> list[str]:
    """Infer commands from package configuration; never install or execute them."""
    available = set(files)
    python_roots = {Path(".")} | {
        path.parent for path in files if path.name in {"pyproject.toml", "pytest.ini", ".pytest.ini", "setup.cfg", "tox.ini", "setup.py"}
    }
    python_tests = {directory: [] for directory in python_roots}
    for path in files:
        if path.suffix == ".py" and _markdown.is_test_path(path.as_posix()):
            for parent in path.parents:
                if parent in python_roots:
                    python_tests[parent].append(path)
                    break
    packages = {}
    for path in files:
        if path.name == "package.json":
            try:
                package = json.loads(optional_text(target_dir / path))
                if isinstance(package, dict):
                    packages[path.parent] = package
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue

    def manager(directory: Path) -> str:
        for parent in (directory, *directory.parents):
            declared = packages.get(parent, {}).get("packageManager", "")
            name = declared.split("@", 1)[0] if isinstance(declared, str) else ""
            if name in {"npm", "pnpm", "yarn", "bun"}:
                return name
            for lock, name in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"),
                               ("bun.lock", "bun"), ("bun.lockb", "bun"), ("package-lock.json", "npm")):
                if parent / lock in available:
                    return name
        return "npm"

    commands = []

    def add(directory: Path, command: str) -> None:
        commands.append(command if directory == Path(".") else f"cd {shlex.quote(directory.as_posix())} && {command}")

    for directory, package in packages.items():
        scripts = package.get("scripts", {})
        if isinstance(scripts, dict):
            for name, command in scripts.items():
                if isinstance(command, str) and re.search(r"(?:^|[:_-])(test|lint|typecheck|check)(?:$|[:_-])", name):
                    add(directory, f"{manager(directory)} run {shlex.quote(name)}")
    for path in files:
        directory = path.parent
        if path.name == "go.mod":
            add(directory, "go test ./...")
        elif path.name == "Cargo.toml":
            add(directory, "cargo test")
    for directory, tests in python_tests.items():
        command = python_test_command(target_dir, directory, available, tests)
        if command:
            add(directory, command)
    return sorted(set(commands))


def codebase_evidence(args: argparse.Namespace) -> int:
    target_dir = _storage.resolve_path(args.target_dir)
    planning_dir = _storage.resolve_path(args.planning_dir) if args.planning_dir else target_dir
    all_files = evidence_files(target_dir)
    interesting = [
        "package.json",
        "pyproject.toml",
        "go.mod",
        "Cargo.toml",
        "Gemfile",
        "requirements.txt",
        "pnpm-lock.yaml",
        "yarn.lock",
        "uv.lock",
    ]
    found_files = [path.as_posix() for path in all_files if path.name in interesting]
    test_files = [
        path.as_posix()
        for path in all_files
        if _markdown.is_test_path(path.as_posix())
        and path.suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb"}
    ][: args.max_tests]
    source_files, source_truncated = cap_paths(
        [
            path.as_posix()
            for path in all_files
            if path.suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".sh"}
            and not _markdown.is_test_path(path.as_posix())
        ]
    )
    skill_files, skill_truncated = cap_paths(
        [path.as_posix() for path in all_files if len(path.parts) >= 3 and path.parts[0] == "skills" and path.name == "SKILL.md"]
    )
    plugin_metadata, plugin_truncated = cap_paths(
        [
            path.as_posix()
            for path in all_files
            if path.as_posix() in {".codex-plugin/plugin.json", ".agents/plugins/marketplace.json", "pyproject.toml"}
            or path.parts[:2] == (".codex-plugin", "skills")
        ]
    )
    ci_files, ci_truncated = cap_paths([path.as_posix() for path in all_files if len(path.parts) >= 3 and path.parts[:2] == (".github", "workflows")])
    example_files, example_truncated = cap_paths(
        [
            path.as_posix()
            for path in all_files
            if path.parts and path.parts[0] == "examples" and path.suffix in {".md", ".json", ".toml", ".yml", ".yaml"}
        ]
    )
    candidate_commands = repository_commands(target_dir, all_files)
    content = (
        "# Codebase Evidence\n\n"
        f"Target: `{target_dir}`\n\n"
        "## Current State\n\n"
        "Existing file tree evidence was verified from relative paths only; source contents were not copied.\n\n"
        "## Runtime And Package Files\n\n"
        + markdown_path_list(sorted(found_files))
        + "\n\n## Tests Discovered\n\n"
        + markdown_path_list(sorted(test_files))
        + "\n\n## Source Files\n\n"
        + markdown_path_list(source_files)
        + "\n\n## Skills\n\n"
        + markdown_path_list(skill_files)
        + "\n\n## Plugin Metadata\n\n"
        + markdown_path_list(plugin_metadata)
        + "\n\n## CI Files\n\n"
        + markdown_path_list(ci_files)
        + "\n\n## Example And Eval Files\n\n"
        + markdown_path_list(example_files)
        + "\n\n## Candidate Commands\n\n"
        + ("\n".join(f"- `{command}`" for command in candidate_commands) or "- None inferred")
        + "\n\n## Assumptions / Open Questions\n\n"
        "- Assumption: generated evidence is bounded planning input, not a complete repository index.\n"
        "- Open question: confirm any omitted generated files before using evidence for release decisions.\n"
    )
    output = None
    if args.write:
        output = planning_dir / "codex-evidence.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
    return _output.print_json(
        {
            "success": True,
            "target_dir": str(target_dir),
            "planning_dir": str(planning_dir),
            "runtime_files": sorted(found_files),
            "test_files": sorted(test_files),
            "source_files": source_files,
            "skill_files": skill_files,
            "plugin_metadata": plugin_metadata,
            "ci_files": ci_files,
            "example_files": example_files,
            "truncated": {
                "source_files": source_truncated,
                "skill_files": skill_truncated,
                "plugin_metadata": plugin_truncated,
                "ci_files": ci_truncated,
                "example_files": example_truncated,
            },
            "candidate_commands": candidate_commands,
            "output": str(output) if output else None,
            "content": None if output else content,
        }
    )
