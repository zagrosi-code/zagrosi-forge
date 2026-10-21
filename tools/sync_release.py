#!/usr/bin/env python3
"""Keep native host releases aligned with pyproject.toml."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tomllib

# Hosts that pin a manifest version (Claude Code) only refresh installs when it changes.
RUNTIME_PREFIXES = ("skills/", "scripts/")
# Generated hash manifests change with every test edit but ship no new behavior.
GENERATED_MANIFESTS = {"scripts/forge/sources.py": "TEST", "scripts/zagrosi_skills.py": "RUNTIME"}


def project_version(text: str) -> str:
    version = tomllib.loads(text)["project"]["version"]
    if not isinstance(version, str) or not version:
        raise ValueError("project.version must be a nonempty string")
    return version


def unreleased_changes(root: Path, base: str) -> list[str]:
    """Runtime paths changed since base while the release version stayed the same."""
    def git(*args: str) -> bytes:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
        if result.returncode:
            message = result.stderr.decode("utf-8", "replace").strip() or "git failed"
            raise ValueError(f"Cannot compare with {base}: {message}")
        return result.stdout

    def manifest_only(path: str, kind: str) -> bool:
        block = re.compile(rb"^# BEGIN " + kind.encode() + rb" MANIFEST$.*?^# END " + kind.encode() + rb" MANIFEST$",
                           re.DOTALL | re.MULTILINE)
        return block.sub(b"", git("show", f"{merge_base}:{path}")) == block.sub(b"", git("show", f"HEAD:{path}"))

    merge_base = git("merge-base", base, "HEAD").decode().strip()
    # Without rename detection, moving a runtime file out of skills/ or scripts/ still counts; -z keeps
    # non-ASCII paths unquoted. Additions, deletions and mode changes always count.
    entries = git("diff", "--raw", "--no-renames", "-z", f"{merge_base}..HEAD").decode().split("\0")
    changed = []
    for meta, path in zip(entries[0::2], entries[1::2]):
        if not path.startswith(RUNTIME_PREFIXES):
            continue
        old_mode, new_mode, *_, status = meta.lstrip(":").split()
        kind = GENERATED_MANIFESTS.get(path)
        if status != "M" or old_mode != new_mode or kind is None or not manifest_only(path, kind):
            changed.append(path)
    if changed and project_version(git("show", f"{base}:pyproject.toml").decode()) == project_version(
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
