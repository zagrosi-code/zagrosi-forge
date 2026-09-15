"""Forge evidence."""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import os

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
        if _markdown.contains_any(path.name, ["test", "spec"])
        and path.suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb"}
    ][: args.max_tests]
    source_files, source_truncated = cap_paths(
        [
            path.as_posix()
            for path in all_files
            if path.suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".sh"}
            and (path.parts[0] in {"scripts", "src", "lib", "app", "bin"} or path.name == "zagrosi_skills.py")
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
    commands: list[str] = []
    package_json = target_dir / "package.json"
    if package_json.exists():
        try:
            package = _storage.load_json(package_json)
            scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
            commands.extend(f"npm run {name}" for name in sorted(scripts) if _markdown.contains_any(name, ["test", "lint", "typecheck", "check"]))
        except json.JSONDecodeError:
            pass
    if (target_dir / "pyproject.toml").exists():
        commands.extend(["uv run pytest", "python -m pytest"])
    if (target_dir / "go.mod").exists():
        commands.append("go test ./...")
    if (target_dir / "Cargo.toml").exists():
        commands.append("cargo test")
    candidate_commands = sorted(set(commands))
    content = (
        "# Codebase Evidence\n\n"
        f"Target: `{target_dir}`\n\n"
        "## Current State\n\n"
        "Existing file tree evidence was verified from relative paths only; source contents were not copied.\n\n"
        "## Runtime And Package Files\n\n"
        + markdown_path_list(sorted(found_files))
        + "\n\n## Tests Discovered\n\n"
        + markdown_path_list(sorted(test_files))
        + "\n\n## Forge Source Files\n\n"
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
