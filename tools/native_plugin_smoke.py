#!/usr/bin/env python3
"""Exercise native plugin loading in disposable settings; never invoke a model."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time


def run(command, cwd, env, *, timeout=60):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError(f"{' '.join(map(str, command))}: {result.stderr or result.stdout}")
    return result.stdout


def codex_skills(executable, cwd, env):
    process = subprocess.Popen([executable, "app-server"], cwd=cwd, env=env, text=True,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    events = queue.Queue()

    def receive():
        for line in process.stdout:
            try:
                events.put(json.loads(line))
            except ValueError:
                continue

    threading.Thread(target=receive, daemon=True).start()

    def call(identity, method, params):
        process.stdin.write(json.dumps({"id": identity, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 30
        while True:
            event = events.get(timeout=max(0, deadline - time.monotonic()))
            if event.get("id") == identity:
                if "error" in event:
                    raise ValueError(f"{method}: {event['error']}")
                return event["result"]

    try:
        call(1, "initialize", {"clientInfo": {"name": "forge-smoke", "version": "1"},
                               "capabilities": {"experimentalApi": True}})
        rows = call(2, "skills/list", {"cwds": [str(cwd)], "forceReload": True})["data"]
        return [skill for row in rows for skill in row["skills"]
                if skill.get("pluginId") == "zagrosi-forge@zagrosi"]
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def mismatches(source: Path, installed: Path, members: list[str]) -> list[str]:
    return [name for name in members if not (installed / name).is_file()
            or (installed / name).read_bytes() != (source / name).read_bytes()]


def check_references(root: Path) -> list[str]:
    missing = []
    for skill in (root / "skills").glob("*/SKILL.md"):
        for target in re.findall(r"\]\(([^)]+)\)", skill.read_text()):
            if not re.match(r"[a-z]+:|#", target) and not (skill.parent / target.split("#", 1)[0]).is_file():
                missing.append(f"{skill.relative_to(root).as_posix()}: {target}")
    return missing


def smoke(root: Path, host: str, executable: str, *, upgrade: bool = False) -> dict:
    members = json.loads((root / ".codex-plugin/package-files.json").read_text())
    expected = sorted(path.parent.name for path in (root / "skills").glob("*/SKILL.md"))
    with tempfile.TemporaryDirectory(prefix=f"forge-{host}-native-") as temporary:
        base = Path(temporary)
        source, settings = base / "source with spaces", base / "settings"
        source.mkdir(); settings.mkdir()
        for name in members:
            destination = source / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / name, destination)
        env = {**os.environ, "CODEX_HOME": str(settings), "CLAUDE_CONFIG_DIR": str(settings)}
        version = run([executable, "--version"], base, env).strip()
        manifest = ".claude-plugin/plugin.json" if host == "claude" else ".codex-plugin/plugin.json"
        original = (source / manifest).read_text()
        if upgrade:
            metadata = json.loads(original)
            metadata["version"] = "0.0.0-smoke"
            (source / manifest).write_text(json.dumps(metadata))
        if host == "claude":
            run([executable, "plugin", "validate", str(source), "--strict"], base, env)
        run([executable, "plugin", "marketplace", "add", str(source)], base, env)

        def install(update=False):
            verb = "update" if host == "claude" and update else "install" if host == "claude" else "add"
            output = run([executable, "plugin", verb, "zagrosi-forge@zagrosi"], base, env)
            listing = json.loads(run([executable, "plugin", "list", "--json"], base, env))
            rows = listing if isinstance(listing, list) else listing["installed"]
            item = next(row for row in rows if row.get("id", row.get("pluginId")) == "zagrosi-forge@zagrosi")
            if not item.get("enabled"):
                raise ValueError("Native plugin is disabled")
            if host == "claude":
                return Path(item["installPath"]), item
            # Codex add reports the cache path; list confirms it is enabled.
            installed = json.loads(output)["installedPath"] if output.lstrip().startswith("{") else None
            if installed is None:
                installed = settings / "plugins/cache/zagrosi/zagrosi-forge" / item["version"]
            return Path(installed), item

        installed, item = install()
        initial_mismatches = mismatches(source, installed, members)
        initial_version = item["version"]
        upgrade_report = None
        if upgrade:
            (source / "README.md").write_text((source / "README.md").read_text() + "\nNative update canary.\n")
            installed, _ = install(update=True)
            stale = mismatches(source, installed, members)
            upgrade_report = {"initial_version": initial_version, "initial_mismatches": initial_mismatches, "same_version": "stale_content_detected" if stale else "content_refreshed",
                              "same_version_mismatches": stale}
            (source / manifest).write_text(original)
            shutil.copy2(root / "README.md", source / "README.md")
            installed, item = install(update=True)
            upgrade_report["version_changed"] = item["version"] == json.loads(original)["version"]
        missing = mismatches(source, installed, members)
        references = check_references(installed)
        if host == "claude":
            details = run([executable, "plugin", "details", "zagrosi-forge"], base, env)
            match = re.search(r"Skills \(\d+\)\s+([^\n]+)", details)
            discovered = sorted(name.strip() for name in match[1].split(",")) if match else []
        else:
            skills = codex_skills(executable, base, env)
            discovered = sorted(item["name"].removeprefix("zagrosi-forge:") for item in skills if item.get("enabled"))
            if any(not Path(item["path"]).resolve().is_relative_to(installed.resolve()) for item in skills):
                raise ValueError("Native skill resolved outside the installed plugin")
        doctor = json.loads(run([sys.executable, str(installed / "scripts/zagrosi_skills.py"), "doctor",
                                 "--plugin-root", str(installed), "--strict"], base, env))
        return {"success": not initial_mismatches and not missing and not references and discovered == expected and doctor["success"]
                           and (not upgrade_report or upgrade_report["version_changed"]),
                "host": host, "cli_version": version, "plugin_version": item["version"],
                "expected_skills": expected, "discovered_skills": discovered, "file_count": len(members),
                "mismatched_files": missing, "missing_references": references, "upgrade": upgrade_report,
                "model_calls": 0, "settings": "disposable", "limits": "Native loading and local-source upgrades only; no model-led skill use or hosted marketplace propagation."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--host", choices=("codex", "claude", "all"), default="all")
    parser.add_argument("--require", action="store_true", help="Missing native CLIs fail instead of being reported unavailable")
    parser.add_argument("--upgrade", action="store_true")
    args = parser.parse_args()
    results = []
    for host in (("codex", "claude") if args.host == "all" else (args.host,)):
        executable = shutil.which(host)
        try:
            results.append(smoke(args.plugin_root.resolve(), host, executable, upgrade=args.upgrade) if executable else
                           {"host": host, "status": "unavailable", "success": False if args.require else None})
        except (OSError, ValueError, KeyError, StopIteration, queue.Empty, subprocess.TimeoutExpired) as exc:
            results.append({"host": host, "success": False, "error": str(exc) or type(exc).__name__})
    success = all(row.get("success") is not False for row in results)
    print(json.dumps({"success": success, "results": results}, indent=2))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
