"""Forge installation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from typing import Any
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from . import codex_config as _codex_config
from .codex_config import expected_codex_config
from . import gates as _gates
from . import output as _output
from .plugin_cache import materialize_plugin_cache, plugin_cache_status
from . import storage as _storage

def release_check(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="forge-release-check-") as directory:
        return _release_check(args, Path(directory) / "config.toml")


def _release_check(args: argparse.Namespace, config_path: Path) -> int:
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
                str(config_path),
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


def update_check(args: argparse.Namespace) -> int:
    plugin_root = _storage.resolve_path(args.plugin_root)
    config_path = _storage.absolute_path_no_follow(args.config) if args.config else default_codex_config_path()
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
    try:
        cache = plugin_cache_status(plugin_root, cache_path)
    except (OSError, ValueError) as exc:
        return _output.print_json({"success": False, "operation": "update-check",
                                  "error": str(exc), "cache_path": str(cache_path)}, 1)
    cache_exists = cache_path.exists()
    cache_current = cache_exists and not cache["changed"]

    try:
        existing = _codex_config.config_text(_codex_config.read_config(config_path)[0])
        expected_config, config_changes = expected_codex_config(existing, plugin_root)
    except (OSError, ValueError):
        return _output.print_json({"success": False, "operation": "update-check", "error": _codex_config.CONFIG_ERROR}, 1)
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
            **cache,
            "exists": cache_exists,
            "current": cache_current,
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
    }


def install_codex(args: argparse.Namespace) -> int:
    plugin_root = _storage.resolve_path(args.plugin_root)
    config_path = _storage.absolute_path_no_follow(args.config) if args.config else default_codex_config_path()
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
    try:
        with nullcontext() if args.dry_run else _storage.file_lock(config_path):
            original = _codex_config.read_config(config_path)
            existing = _codex_config.config_text(original[0])
            updated, changes = expected_codex_config(existing, plugin_root)
            try:
                cache = materialize_plugin_cache(plugin_root, cache_path, args.dry_run)
            except (OSError, ValueError) as exc:
                return _output.print_json({"success": False, "operation": operation,
                                          "error": str(exc), "cache_path": str(cache_path)}, 1)
            config_changed = updated != existing
            backup_path = None
            if config_changed and not args.dry_run:
                backup_path = _codex_config.publish_config(config_path, original, updated, no_backup=args.no_backup)
    except (OSError, ValueError):
        return _output.print_json({"success": False, "operation": operation,
                                  "error": _codex_config.CONFIG_ERROR, "config_path": str(config_path)}, 1)
    changed = config_changed or bool(cache.get("changed"))

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
        payload["config_preview"] = expected_codex_config("", plugin_root)[0]
    return _output.print_json(payload)
