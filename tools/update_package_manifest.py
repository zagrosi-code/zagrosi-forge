#!/usr/bin/env python3
"""Declare packaged files from the Git index; untracked files are never admitted."""
import argparse
import json
from pathlib import Path
import subprocess

ROOTS = {".agents", ".codex-plugin", "assets", "docs", "examples", "scripts", "skills", "tests", "tools"}
FILES = {"README.md", "NOTICE.md", "LICENSE", "pyproject.toml"}
MANIFEST = ".codex-plugin/package-files.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    paths = subprocess.check_output(["git", "ls-files", "-z", "--cached"], cwd=args.root).decode().split("\0")
    members = sorted({MANIFEST, *(name for name in paths if name and (name in FILES or Path(name).parts[0] in ROOTS)
        and not any(part.startswith(".") and part not in {".agents", ".codex-plugin"} for part in Path(name).parts)
        and not name.startswith("docs/development/"))})
    rendered = json.dumps(members, indent=2) + "\n"
    path = args.root / MANIFEST
    if args.check:
        if not path.exists() or path.read_text() != rendered:
            parser.exit(1, "Package manifest is stale; stage intended files and run tools/update_package_manifest.py\n")
    else:
        path.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
