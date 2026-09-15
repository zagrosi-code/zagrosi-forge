"""Forge installation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

from . import gates as _gates
from . import output as _output
from . import policy as _policy
from . import storage as _storage

def release_check(args: argparse.Namespace) -> int:
    plugin_root = _storage.resolve_path(args.plugin_root)
    checks: list[tuple[str, list[str]]] = [
        ("compile-cli", [sys.executable, "-m", "py_compile", *map(str, sorted((plugin_root / "scripts").rglob("*.py")))]),
        ("runtime-manifest", [sys.executable, str(plugin_root / "tools" / "update_runtime_manifest.py"), "--plugin-root", str(plugin_root), "--check"]),
        ("validate-plugin-manifest", [sys.executable, "-m", "json.tool", str(plugin_root / ".codex-plugin" / "plugin.json")]),
        ("validate-marketplace", [sys.executable, "-m", "json.tool", str(plugin_root / ".agents" / "plugins" / "marketplace.json")]),
        (
            "install-dry-run",
            [
                sys.executable,
                str(plugin_root / "scripts" / "zagrosi_skills.py"),
                "install",
                "--plugin-root",
                str(plugin_root),
                "--config",
                str(plugin_root / ".release-check" / "config.toml"),
                "--dry-run",
            ],
        ),
    ]
    examples_dir = plugin_root / "examples"
    if examples_dir.exists():
        checks.extend(
            [
                ("validate-eval-suite", [sys.executable, "-m", "json.tool", str(examples_dir / "evals" / "suite.json")]),
                ("lint-saas-manifest", [sys.executable, str(plugin_root / "scripts" / "zagrosi_skills.py"), "lint-project-manifest", "--planning-dir", str(examples_dir / "saas"), "--strict"]),
                ("lint-typescript-manifest", [sys.executable, str(plugin_root / "scripts" / "zagrosi_skills.py"), "lint-project-manifest", "--planning-dir", str(examples_dir / "typescript-app"), "--strict"]),
                ("eval-suite", [sys.executable, str(plugin_root / "scripts" / "zagrosi_skills.py"), "eval-suite", "--examples-dir", str(examples_dir), "--check-snapshots"]),
            ]
        )
    if args.run_tests:
        checks.append(("tests", ["uv", "run", "--with", "pytest", "python", "-m", "pytest"]))

    def run(check: tuple[str, list[str]]) -> dict[str, Any]:
        name, command = check
        check_started = time.monotonic()
        try:
            result = subprocess.run(command, cwd=plugin_root, capture_output=True, text=True, timeout=300)
            return {
                "name": name,
                "command": " ".join(command),
                "returncode": result.returncode,
                "duration_seconds": round(time.monotonic() - check_started, 3),
                "stdout_tail": result.stdout[-1000:],
                "stderr_tail": result.stderr[-1000:],
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "name": name,
                "command": " ".join(command),
                "returncode": 124,
                "duration_seconds": round(time.monotonic() - check_started, 3),
                "stdout_tail": _gates.bounded_output_tail(exc.stdout),
                "stderr_tail": _gates.bounded_output_tail(exc.stderr),
            }

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(4, len(checks)), thread_name_prefix="forge-release") as executor:
        results = list(executor.map(run, checks))
    duration_seconds = round(time.monotonic() - started, 3)
    success = all(result["returncode"] == 0 for result in results)
    payload: dict[str, Any] = {
        "success": success,
        "plugin_root": str(plugin_root),
        "check_count": len(checks),
        "checks": [name for name, _ in checks],
        "duration_seconds": duration_seconds,
    }
    if not success:
        payload["failed_checks"] = [result["name"] for result in results if result["returncode"] != 0]
    if not success or getattr(args, "verbose", False):
        payload["results"] = results
    return _output.print_json(payload, 0 if success else 1)


def default_codex_config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def toml_string(value: str) -> str:
    return json.dumps(value)


def toml_header_token(line: str) -> str | None:
    match = re.match(r"^\s*(\[\[?.*?\]\]?)\s*(?:#.*)?$", line)
    return match.group(1) if match else None


def find_toml_section(lines: list[str], header: str) -> tuple[int, int] | None:
    start: int | None = None
    for index, line in enumerate(lines):
        token = toml_header_token(line)
        if token is None:
            continue
        if token == header:
            start = index
            continue
        if start is not None:
            return start, index
    if start is None:
        return None
    return start, len(lines)


def upsert_toml_section(text: str, header: str, entries: dict[str, str]) -> tuple[str, list[str]]:
    lines = text.splitlines()
    changes: list[str] = []
    section = find_toml_section(lines, header)
    if section is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(header)
        for key, value in entries.items():
            lines.append(f"{key} = {value}")
        changes.append(f"added {header}")
        return "\n".join(lines).rstrip() + "\n", changes

    start, end = section
    for key, value in entries.items():
        replacement = f"{key} = {value}"
        key_re = re.compile(rf"^\s*{re.escape(key)}\s*=.*$")
        found = False
        for index in range(start + 1, end):
            if key_re.match(lines[index]):
                found = True
                if lines[index].strip() != replacement:
                    lines[index] = replacement
                    changes.append(f"updated {header}.{key}")
                break
        if not found:
            lines.insert(end, replacement)
            end += 1
            changes.append(f"added {header}.{key}")
    return "\n".join(lines).rstrip() + "\n", changes


def codex_home_for_config(config_path: Path, explicit_config: bool) -> Path:
    if explicit_config:
        return config_path.expanduser().resolve().parent
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser()
    return config_path.expanduser().resolve().parent


def package_manifest(plugin_root: Path) -> dict[str, Any]:
    manifest = _storage.load_json(plugin_root / ".codex-plugin" / "plugin.json")
    if not isinstance(manifest, dict):
        raise ValueError("plugin.json must contain a JSON object")
    return manifest


def plugin_cache_path(codex_home: Path, marketplace: str, plugin_name: str, version: str) -> Path:
    return codex_home / "plugins" / "cache" / marketplace / plugin_name / version


def should_skip_cache_path(path: Path) -> bool:
    return any(part in _policy.PLUGIN_CACHE_IGNORE_DIRS for part in path.parts) or path.name in _policy.PLUGIN_CACHE_IGNORE_FILES


def plugin_tree_fingerprint(root: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    if not root.exists():
        return ""
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if should_skip_cache_path(relative):
            continue
        digest.update(str(relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def copy_ignore(_: str, names: list[str]) -> set[str]:
    return {name for name in names if name in _policy.PLUGIN_CACHE_IGNORE_DIRS or name in _policy.PLUGIN_CACHE_IGNORE_FILES}


def materialize_plugin_cache(plugin_root: Path, cache_path: Path, dry_run: bool) -> dict[str, Any]:
    source_fingerprint = plugin_tree_fingerprint(plugin_root)
    cached_fingerprint = plugin_tree_fingerprint(cache_path)
    cache_changed = source_fingerprint != cached_fingerprint
    payload: dict[str, Any] = {
        "path": str(cache_path),
        "changed": cache_changed,
        "source_fingerprint": source_fingerprint,
        "cached_fingerprint": cached_fingerprint or None,
    }
    if dry_run:
        payload["dry_run"] = True
        return payload
    if not cache_changed:
        payload["dry_run"] = False
        return payload

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    temporary = cache_path.parent / f".{cache_path.name}.tmp-{stamp}"
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(plugin_root, temporary, ignore=copy_ignore)
    if cache_path.exists():
        shutil.rmtree(cache_path)
    temporary.rename(cache_path)
    payload["dry_run"] = False
    return payload


def expected_codex_config(existing: str, plugin_root: Path) -> tuple[str, list[str]]:
    updated, marketplace_changes = upsert_toml_section(
        existing,
        "[marketplaces.zagrosi]",
        {
            "source_type": toml_string("local"),
            "source": toml_string(str(plugin_root)),
        },
    )
    updated, plugin_changes = upsert_toml_section(
        updated,
        '[plugins."zagrosi-forge@zagrosi"]',
        {"enabled": "true"},
    )
    return updated, marketplace_changes + plugin_changes


def update_check(args: argparse.Namespace) -> int:
    plugin_root = _storage.resolve_path(args.plugin_root)
    config_path = _storage.resolve_path(args.config) if args.config else default_codex_config_path()
    codex_home = codex_home_for_config(config_path, bool(args.config))
    try:
        manifest = package_manifest(plugin_root)
    except (json.JSONDecodeError, ValueError) as exc:
        return _output.print_json(
            {
                "success": False,
                "operation": "update-check",
                "plugin_root": str(plugin_root),
                "config_path": str(config_path),
                "error": f"Could not read plugin manifest: {exc}",
            },
            1,
        )

    plugin_name = str(manifest.get("name") or "zagrosi-forge")
    plugin_version = str(manifest.get("version") or "0.0.0")
    cache_path = plugin_cache_path(codex_home, "zagrosi", plugin_name, plugin_version)
    source_fingerprint = plugin_tree_fingerprint(plugin_root)
    cached_fingerprint = plugin_tree_fingerprint(cache_path)
    cache_exists = cache_path.exists()
    cache_current = cache_exists and source_fingerprint == cached_fingerprint

    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    expected_config, config_changes = expected_codex_config(existing, plugin_root)
    config_current = expected_config == existing
    restart_required = not cache_current or not config_current
    next_steps: list[str] = []
    if restart_required:
        command = shlex.join([
            sys.executable, str(plugin_root / "scripts/zagrosi_skills.py"), "self-update",
            "--plugin-root", str(plugin_root), "--config", str(config_path),
        ])
        next_steps.append(f"Run {command} to refresh Codex config and plugin cache.")
        next_steps.append("Restart Codex after self-update reports changed cache or config.")
    else:
        next_steps.append("Codex config and Zagrosi Forge plugin cache are already current.")
    next_steps.append("This check is local-only; update the git checkout separately when you want newer remote source.")

    payload = {
        "success": True,
        "operation": "update-check",
        "plugin_root": str(plugin_root),
        "config_path": str(config_path),
        "codex_home": str(codex_home),
        "plugin": "zagrosi-forge@zagrosi",
        "plugin_name": plugin_name,
        "plugin_version": plugin_version,
        "network_policy": "local-only",
        "remote_checked": False,
        "cache": {
            "path": str(cache_path),
            "exists": cache_exists,
            "current": cache_current,
            "changed": not cache_current,
            "source_fingerprint": source_fingerprint,
            "cached_fingerprint": cached_fingerprint or None,
        },
        "config": {
            "current": config_current,
            "changed": not config_current,
            "changes": config_changes,
        },
        "restart_required": restart_required,
        "next_steps": next_steps,
    }
    return _output.print_json(payload)


def verify_codex_install(codex_home: Path, require_codex: bool) -> dict[str, Any]:
    codex = shutil.which("codex")
    if not codex:
        payload = {
            "status": "failed" if require_codex else "skipped",
            "success": not require_codex,
            "reason": "codex executable was not found on PATH",
            "required_skills": [
                "zagrosi-forge:zagrosi-project",
                "zagrosi-forge:zagrosi-plan",
                "zagrosi-forge:zagrosi-implement",
            ],
        }
        return payload

    command = [codex, "debug", "prompt-input", "Use $zagrosi-forge:zagrosi-project"]
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    required = [
        "zagrosi-forge:zagrosi-project",
        "zagrosi-forge:zagrosi-plan",
        "zagrosi-forge:zagrosi-implement",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=45)
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "failed",
            "success": False,
            "command": " ".join(command),
            "codex_home": str(codex_home),
            "reason": f"codex debug prompt-input timed out after {exc.timeout} seconds",
            "required_skills": required,
        }

    output = f"{result.stdout}\n{result.stderr}"
    missing = [skill for skill in required if skill not in output]
    success = result.returncode == 0 and not missing
    return {
        "status": "passed" if success else "failed",
        "success": success,
        "command": " ".join(command),
        "codex_home": str(codex_home),
        "returncode": result.returncode,
        "missing": missing,
        "required_skills": required,
        "stdout_tail": result.stdout[-1000:],
        "stderr_tail": result.stderr[-1000:],
    }


def install_codex(args: argparse.Namespace) -> int:
    plugin_root = _storage.resolve_path(args.plugin_root)
    config_path = _storage.resolve_path(args.config) if args.config else default_codex_config_path()
    codex_home = codex_home_for_config(config_path, bool(args.config))
    plugin_id = "zagrosi-forge@zagrosi"
    operation = "self-update" if getattr(args, "command", "") == "self-update" else "install-codex"
    if args.verify_codex and args.no_verify_codex:
        return _output.print_json(
            {
                "success": False,
                "operation": operation,
                "plugin_root": str(plugin_root),
                "config_path": str(config_path),
                "plugin": plugin_id,
                "error": "Use either --verify-codex or --no-verify-codex, not both.",
            },
            2,
        )
    required = [
        plugin_root / ".codex-plugin" / "plugin.json",
        plugin_root / ".agents" / "plugins" / "marketplace.json",
        plugin_root / "skills" / "zagrosi-project" / "SKILL.md",
        plugin_root / "skills" / "zagrosi-plan" / "SKILL.md",
        plugin_root / "skills" / "zagrosi-implement" / "SKILL.md",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        return _output.print_json(
            {
                "success": False,
                "operation": operation,
                "plugin_root": str(plugin_root),
                "config_path": str(config_path),
                "plugin": plugin_id,
                "missing": missing,
                "error": "Plugin root is missing required package files.",
            },
            1,
        )

    doctor_result = subprocess.run(
        [sys.executable, str(plugin_root / "scripts" / "zagrosi_skills.py"), "doctor", "--plugin-root", str(plugin_root), "--strict"],
        cwd=plugin_root,
        capture_output=True,
        text=True,
    )
    try:
        doctor_payload: Any = json.loads(doctor_result.stdout) if doctor_result.stdout.strip() else {}
    except json.JSONDecodeError:
        doctor_payload = {"stdout": doctor_result.stdout[-1000:], "stderr": doctor_result.stderr[-1000:]}
    if doctor_result.returncode != 0:
        return _output.print_json(
            {
                "success": False,
                "operation": operation,
                "plugin_root": str(plugin_root),
                "config_path": str(config_path),
                "plugin": plugin_id,
                "doctor": doctor_payload,
                "error": "Package doctor failed; fix the plugin before installing.",
            },
            1,
        )

    try:
        manifest = package_manifest(plugin_root)
    except (json.JSONDecodeError, ValueError) as exc:
        return _output.print_json(
            {
                "success": False,
                "operation": operation,
                "plugin_root": str(plugin_root),
                "config_path": str(config_path),
                "plugin": plugin_id,
                "error": f"Could not read plugin manifest: {exc}",
            },
            1,
        )
    plugin_name = str(manifest.get("name") or "zagrosi-forge")
    plugin_version = str(manifest.get("version") or "0.0.0")
    cache_path = plugin_cache_path(codex_home, "zagrosi", plugin_name, plugin_version)
    cache = materialize_plugin_cache(plugin_root, cache_path, args.dry_run)

    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    updated, changes = expected_codex_config(existing, plugin_root)
    changed = updated != existing or bool(cache.get("changed"))
    backup_path: Path | None = None

    config_changed = updated != existing
    if config_changed and not args.dry_run:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if config_path.exists() and not args.no_backup:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            backup_path = config_path.with_name(f"{config_path.name}.bak-{stamp}")
            backup_path.write_text(existing, encoding="utf-8")
        config_path.write_text(updated, encoding="utf-8")

    verification: dict[str, Any]
    if args.dry_run or args.no_verify_codex:
        verification = {
            "status": "skipped",
            "success": True,
            "reason": "dry run" if args.dry_run else "disabled by --no-verify-codex",
            "required_skills": [
                "zagrosi-forge:zagrosi-project",
                "zagrosi-forge:zagrosi-plan",
                "zagrosi-forge:zagrosi-implement",
            ],
        }
    elif not changed and not args.verify_codex:
        verification = {
            "status": "skipped",
            "success": True,
            "reason": "installation unchanged",
            "required_skills": [
                "zagrosi-forge:zagrosi-project",
                "zagrosi-forge:zagrosi-plan",
                "zagrosi-forge:zagrosi-implement",
            ],
        }
    else:
        verification = verify_codex_install(codex_home, args.verify_codex)
        if not verification.get("success"):
            return _output.print_json(
                {
                    "success": False,
                    "operation": operation,
                    "changed": changed,
                    "dry_run": args.dry_run,
                    "config_path": str(config_path),
                    "codex_home": str(codex_home),
                    "plugin_root": str(plugin_root),
                    "plugin": plugin_id,
                    "marketplace": "zagrosi",
                    "cache": cache,
                    "backup_path": str(backup_path) if backup_path else None,
                    "changes": changes,
                    "verification": verification,
                    "error": "Codex did not report the Zagrosi Forge skills after install.",
                },
                1,
            )

    next_steps = []
    if args.dry_run:
        next_steps.append("Run the same command without --dry-run to update Codex config.")
    elif changed:
        next_steps.append("Restart Codex so the plugin cache and marketplace are reloaded.")
    else:
        next_steps.append("Codex config and Zagrosi Forge plugin cache are already current.")
    next_steps.append(
        "Use $zagrosi-forge:zagrosi-project, $zagrosi-forge:zagrosi-plan, or $zagrosi-forge:zagrosi-implement in Codex."
    )

    payload = {
        "success": True,
        "operation": operation,
        "changed": changed,
        "config_changed": config_changed,
        "dry_run": args.dry_run,
        "config_path": str(config_path),
        "codex_home": str(codex_home),
        "plugin_root": str(plugin_root),
        "plugin": plugin_id,
        "plugin_version": plugin_version,
        "marketplace": "zagrosi",
        "cache": cache,
        "backup_path": str(backup_path) if backup_path else None,
        "changes": changes,
        "verification": verification,
        "restart_required": bool(changed and not args.dry_run),
        "next_steps": next_steps,
    }
    if args.dry_run:
        payload["config_preview"] = updated
    return _output.print_json(payload)
