#!/usr/bin/env python3
"""Keep native host releases aligned with pyproject.toml."""
import argparse
import json
from pathlib import Path
import subprocess
import tomllib

# Hosts that pin a manifest version (Claude Code) only refresh installs when it changes.
RUNTIME_PREFIXES = ("skills/", "scripts/")


def project_version(text: str) -> str:
    version = tomllib.loads(text)["project"]["version"]
    if not isinstance(version, str) or not version:
        raise ValueError("project.version must be a nonempty string")
    return version


def unreleased_changes(root: Path, base: str) -> list[str]:
    """Runtime paths changed since base while the release version stayed the same."""
    def git(*args: str) -> str:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
        if result.returncode:
            raise ValueError(f"Cannot compare with {base}: {result.stderr.strip() or 'git failed'}")
        return result.stdout

    # Without rename detection, moving a runtime file out of skills/ or scripts/ still counts;
    # -z keeps non-ASCII paths unquoted.
    changed = [path for path in git("diff", "--name-only", "--no-renames", "-z", f"{base}...HEAD").split("\0")
               if path.startswith(RUNTIME_PREFIXES)]
    if changed and project_version(git("show", f"{base}:pyproject.toml")) == project_version(
            (root / "pyproject.toml").read_text()):
        return changed
    return []


def synchronize(root: Path, check: bool = False) -> list[str]:
    version = project_version((root / "pyproject.toml").read_text())
    changed = []
    for name in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        path = root / name
        metadata = json.loads(path.read_text())
        if not isinstance(metadata, dict):
            raise ValueError(f"{name} must contain a JSON object")
        if metadata.get("version") != version:
            changed.append(name)
            if not check:
                metadata["version"] = version
                path.write_text(json.dumps(metadata, indent=2) + "\n")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--base", help="Git ref to release against; skills/ or scripts/ changes since it need a new version.")
    args = parser.parse_args()
    try:
        changed = synchronize(args.plugin_root, args.check)
        report = {"success": not args.check or not changed, "changed": changed}
        if args.base:
            report["unreleased"] = unreleased_changes(args.plugin_root, args.base)
            if report["unreleased"]:
                version = project_version((args.plugin_root / "pyproject.toml").read_text())
                report.update(success=False, error=f"Runtime files changed since {args.base} under release {version}; "
                                                   "raise project.version and run tools/sync_release.py.")
        print(json.dumps(report))
        return 0 if report["success"] else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
