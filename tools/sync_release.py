#!/usr/bin/env python3
"""Keep native host releases aligned with pyproject.toml."""
import argparse
import json
from pathlib import Path
import tomllib


def synchronize(root: Path, check: bool = False) -> list[str]:
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if not isinstance(version, str) or not version:
        raise ValueError("project.version must be a nonempty string")
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
    args = parser.parse_args()
    try:
        changed = synchronize(args.plugin_root, args.check)
        success = not args.check or not changed
        print(json.dumps({"success": success, "changed": changed}))
        return 0 if success else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
