#!/usr/bin/env python3
"""Bind runtime and detached test sources; --check verifies without writing."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def update_manifest(path: Path, kind: str, manifest: dict[str, str], *, check: bool) -> bool:
    start, end = f"# BEGIN {kind} MANIFEST", f"# END {kind} MANIFEST"
    text = path.read_text()
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f"Expected one {kind.lower()} manifest")
    before, rest = text.split(start)
    _, after = rest.split(end)
    rendered = f"{before}{start}\n{kind}_MANIFEST = {json.dumps(manifest, indent=4, sort_keys=True)}\n{end}{after}"
    if not check and rendered != text:
        path.write_text(rendered)
    return rendered == text


def update(script: Path, *, check: bool = False) -> bool:
    tests_current = True
    tests_dir = script.parent.parent / "tests"
    if tests_dir.is_dir():
        tests = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(tests_dir.glob("*.py"))}
        if "test_zagrosi_skills.py" not in tests:
            raise ValueError("Implementation test anchor is missing")
        tests_current = update_manifest(script.parent / "forge/sources.py", "TEST", tests, check=check)
    manifest = {
        path.relative_to(script.parent).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((script.parent / "forge").rglob("*.py"))
    }
    if "forge/__init__.py" not in manifest:
        raise ValueError("Runtime package is missing")
    return update_manifest(script, "RUNTIME", manifest, check=check) and tests_current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--script", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    current = update(args.script or args.plugin_root / "scripts/zagrosi_skills.py", check=args.check)
    if args.check and not current:
        parser.exit(1, "Runtime or test manifest is stale; run tools/update_runtime_manifest.py\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
