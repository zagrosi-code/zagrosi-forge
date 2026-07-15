#!/usr/bin/env python3
"""Verify or acquire one exact artifact from the Zagrosi toolchain lock."""

from __future__ import annotations

import argparse
from pathlib import Path

from zagrosi_forge.install.toolchain import (
    acquire_artifact,
    load_toolchain_lock,
    verify_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    lock = load_toolchain_lock()
    if args.artifact is not None:
        from zagrosi_forge.install.toolchain import select_artifact

        selected = select_artifact(lock, tool=args.tool, platform=args.platform)
        verify_artifact(args.artifact, expected_sha256=selected["sha256"])
        print(args.artifact)
        return 0
    result = acquire_artifact(
        lock,
        tool=args.tool,
        platform=args.platform,
        destination=args.destination,
        offline=args.offline,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
