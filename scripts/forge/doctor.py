"""Forge doctor."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import re
import tomllib
import sys

from . import CLI_PATH
from . import models as _models
from . import quality as _quality
from . import storage as _storage


SKILL_NAMES = ("zagrosi-forge", "zagrosi-project", "zagrosi-plan", "zagrosi-implement", "zagrosi-cleanup")


def metadata_object(path: Path, code: str, findings: list) -> dict:
    if not path.exists():
        return {}
    try:
        value = _storage.load_json(path)
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value
    except (OSError, ValueError) as exc:
        findings.append(_quality.finding("critical", code, str(exc), path))
        return {}


def release_version(plugin_root: Path, findings: list) -> str | None:
    path = plugin_root / "pyproject.toml"
    try:
        project = tomllib.loads(_storage.read_text(path)).get("project", {})
        version = project.get("version") if isinstance(project, dict) else None
        if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
            raise ValueError("project.version must be a release version such as 0.3.0")
        return version
    except (OSError, ValueError) as exc:
        findings.append(_quality.finding("critical", "invalid-release-version", str(exc), path))
        return None


def claude_metadata_findings(plugin_root: Path) -> list[_models.Finding]:
    findings = []
    for name in ("plugin", "marketplace"):
        path = plugin_root / ".claude-plugin" / f"{name}.json"
        if not path.exists():
            continue  # The shared required-file check reports missing metadata.
        metadata = metadata_object(path, f"invalid-claude-{name}-json", findings)
        expected_name = "zagrosi-forge" if name == "plugin" else "zagrosi"
        if metadata.get("name") != expected_name:
            findings.append(_quality.finding("high", f"claude-{name}-name", f"Claude {name} name must be {expected_name}.", path))
        if name == "plugin":
            if metadata.get("skills", "./skills/") not in ("./skills", "./skills/"):
                findings.append(_quality.finding("high", "claude-skill-root", "Claude must use the shared root skills directory.", path))
            continue
        plugins = metadata.get("plugins")
        entries = [item for item in plugins if isinstance(item, dict) and item.get("name") == "zagrosi-forge"] if isinstance(plugins, list) else []
        if len(entries) != 1:
            findings.append(_quality.finding("high", "claude-marketplace-plugin", "Claude marketplace must contain exactly one zagrosi-forge entry.", path))
        elif entries[0].get("source") not in (".", "./"):
            findings.append(_quality.finding("high", "claude-marketplace-source", "Claude marketplace source must be './'.", path))
    return findings


def doctor(args: argparse.Namespace) -> int:
    plugin_root = _storage.resolve_path(args.plugin_root) if args.plugin_root else Path(str(CLI_PATH)).resolve().parents[1]
    findings: list[_models.Finding] = []
    expected = [
        plugin_root / ".codex-plugin" / "plugin.json",
        plugin_root / ".agents" / "plugins" / "marketplace.json",
        plugin_root / ".claude-plugin" / "plugin.json",
        plugin_root / ".claude-plugin" / "marketplace.json",
        plugin_root / "pyproject.toml",
        plugin_root / "scripts" / "zagrosi_skills.py",
        plugin_root / "scripts" / "deep_skills.py",
    ]
    for path in expected:
        if not path.exists():
            findings.append(_quality.finding("critical", "missing-package-file", f"Missing package file: {path}", path))
    findings.extend(claude_metadata_findings(plugin_root))

    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = metadata_object(manifest_path, "invalid-plugin-json", findings)
    if manifest_path.exists() and manifest.get("name") != "zagrosi-forge":
        findings.append(_quality.finding("medium", "plugin-name", "Plugin package name should be zagrosi-forge.", manifest_path))

    marketplace_path = plugin_root / ".agents" / "plugins" / "marketplace.json"
    marketplace = metadata_object(marketplace_path, "invalid-marketplace-json", findings)
    marketplace_entry: dict[str, Any] = {}
    if marketplace_path.exists():
        if marketplace.get("name") != "zagrosi":
            findings.append(_quality.finding("medium", "marketplace-name", "Marketplace name should be zagrosi.", marketplace_path))
        plugins = marketplace.get("plugins")
        if not isinstance(plugins, list):
            findings.append(_quality.finding("high", "marketplace-plugins", "marketplace.json must contain a plugins array.", marketplace_path))
        else:
            for item in plugins:
                if isinstance(item, dict) and item.get("name") == "zagrosi-forge":
                    marketplace_entry = item
                    break
            if not marketplace_entry:
                findings.append(
                    _quality.finding("high", "marketplace-plugin-entry", "Marketplace must include zagrosi-forge.", marketplace_path)
                )
    if marketplace_entry:
        source = marketplace_entry.get("source")
        if not isinstance(source, dict) or source.get("source") != "local" or source.get("path") not in (".", "./"):
            findings.append(
                _quality.finding(
                    "medium",
                    "marketplace-plugin-source",
                    "zagrosi-forge marketplace source should be local with path './'.",
                    marketplace_path,
                )
            )
        policy = marketplace_entry.get("policy")
        if not isinstance(policy, dict):
            findings.append(_quality.finding("high", "marketplace-plugin-policy", "Marketplace entry must include policy.", marketplace_path))
        else:
            if policy.get("installation") not in ("NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"):
                findings.append(
                    _quality.finding("high", "marketplace-installation-policy", "Invalid marketplace installation policy.", marketplace_path)
                )
            if policy.get("authentication") not in ("ON_INSTALL", "ON_USE"):
                findings.append(
                    _quality.finding("high", "marketplace-authentication-policy", "Invalid marketplace authentication policy.", marketplace_path)
                )
        if not marketplace_entry.get("category"):
            findings.append(_quality.finding("low", "marketplace-category", "Marketplace entry should include a category.", marketplace_path))

    version = release_version(plugin_root, findings)
    for directory in (".codex-plugin", ".claude-plugin"):
        path = plugin_root / directory / "plugin.json"
        metadata = metadata_object(path, "invalid-release-metadata", [])
        if version and metadata and metadata.get("version") != version:
            findings.append(_quality.finding("high", "release-version-drift", f"Plugin version must match pyproject.toml: {version}.", path))

    for skill_name in SKILL_NAMES:
        skill_path = plugin_root / "skills" / skill_name / "SKILL.md"
        if not skill_path.exists():
            findings.append(_quality.finding("critical", "missing-skill", f"Missing skill: {skill_name}", skill_path))
            continue
        text = _storage.read_text(skill_path)
        if f"name: {skill_name}" not in text[:300]:
            findings.append(_quality.finding("high", "skill-frontmatter", f"{skill_name} frontmatter name is missing or stale.", skill_path))
        if skill_name in {"zagrosi-project", "zagrosi-plan", "zagrosi-implement"} and "scripts/zagrosi_skills.py" not in text:
            findings.append(_quality.finding("medium", "skill-helper-reference", f"{skill_name} does not reference the Zagrosi helper script.", skill_path))

    if sys.version_info < (3, 11):
        findings.append(_quality.finding("critical", "python-version", "Python 3.11 or newer is required."))

    extras = {
        "plugin_root": str(plugin_root),
        "python": sys.version.split()[0],
        "marketplace": {
            "name": marketplace.get("name"),
            "plugin": "zagrosi-forge@zagrosi" if marketplace_entry else None,
            "path": str(marketplace_path),
        },
        "version": version,
        "skills": list(SKILL_NAMES),
        "plugin_scoped_skills": [f"zagrosi-forge:{name}" for name in SKILL_NAMES],
    }
    return _quality.emit_quality("doctor", findings, args, extras)
