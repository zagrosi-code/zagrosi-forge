"""Installer-safety entry point and shared contracts."""

from __future__ import annotations

import json
from collections.abc import Sequence
import sys

from .version import VERSION

__all__ = ["VERSION", "main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Expose version now and fail closed until the Section 06 adapter exists."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--version"]:
        print(VERSION)
        return 0
    print(
        json.dumps(
            {
                "success": False,
                "code": "package.feature_unavailable",
                "exit_category": 15,
                "message": "Installer commands are unavailable in this package build.",
                "version": VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 15
