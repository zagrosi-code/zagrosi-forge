"""Forge doctor."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json
import sys

from . import CLI_PATH
from . import models as _models
from . import quality as _quality
from . import storage as _storage

def doctor(args: argparse.Namespace) -> int:
    plugin_root = _storage.resolve_path(args.plugin_root) if args.plugin_root else Path(str(CLI_PATH)).resolve().parents[1]
    findings: list[_models.Finding] = []
    expected = [
        plugin_root / ".codex-plugin" / "plugin.json",
        plugin_root / ".agents" / "plugins" / "marketplace.json",
        plugin_root / "pyproject.toml",
        plugin_root / "scripts" / "zagrosi_skills.py",
        plugin_root / "scripts" / "deep_skills.py",
    ]
    for path in expected:
        if not path.exists():
            findings.append(_quality.finding("critical", "missing-package-file", f"Missing package file: {path}", path))

    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = _storage.load_json(manifest_path)
        except json.JSONDecodeError as exc:
            findings.append(_quality.finding("critical", "invalid-plugin-json", f"plugin.json is invalid JSON: {exc}", manifest_path))
    if manifest and manifest.get("name") != "zagrosi-forge":
        findings.append(_quality.finding("medium", "plugin-name", "Plugin package name should be zagrosi-forge.", manifest_path))

    marketplace_path = plugin_root / ".agents" / "plugins" / "marketplace.json"
    marketplace: dict[str, Any] = {}
    marketplace_entry: dict[str, Any] = {}
    if marketplace_path.exists():
        try:
            marketplace = _storage.load_json(marketplace_path)
        except json.JSONDecodeError as exc:
            findings.append(
                _quality.finding("critical", "invalid-marketplace-json", f"marketplace.json is invalid JSON: {exc}", marketplace_path)
            )
    if marketplace:
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
        if not isinstance(source, dict) or source.get("source") != "local" or source.get("path") not in {".", "./"}:
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
            if policy.get("installation") not in {"NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"}:
                findings.append(
                    _quality.finding("high", "marketplace-installation-policy", "Invalid marketplace installation policy.", marketplace_path)
                )
            if policy.get("authentication") not in {"ON_INSTALL", "ON_USE"}:
                findings.append(
                    _quality.finding("high", "marketplace-authentication-policy", "Invalid marketplace authentication policy.", marketplace_path)
                )
        if not marketplace_entry.get("category"):
            findings.append(_quality.finding("low", "marketplace-category", "Marketplace entry should include a category.", marketplace_path))

    for skill_name in ("zagrosi-project", "zagrosi-plan", "zagrosi-implement"):
        skill_path = plugin_root / "skills" / skill_name / "SKILL.md"
        if not skill_path.exists():
            findings.append(_quality.finding("critical", "missing-skill", f"Missing skill: {skill_name}", skill_path))
            continue
        text = _storage.read_text(skill_path)
        if f"name: {skill_name}" not in text[:300]:
            findings.append(_quality.finding("high", "skill-frontmatter", f"{skill_name} frontmatter name is missing or stale.", skill_path))
        if "scripts/zagrosi_skills.py" not in text:
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
        "skills": ["zagrosi-project", "zagrosi-plan", "zagrosi-implement"],
        "plugin_scoped_skills": [
            "zagrosi-forge:zagrosi-project",
            "zagrosi-forge:zagrosi-plan",
            "zagrosi-forge:zagrosi-implement",
        ],
    }
    return _quality.emit_quality("doctor", findings, args, extras)
