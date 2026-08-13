"""Installer-safety entry point and shared contracts."""

from __future__ import annotations

import json
from collections.abc import Sequence
import sys
from typing import Any

__all__ = ["VERSION", "main"]


def _installed_version() -> str:
    from .version import VERSION

    return VERSION


def __getattr__(name: str) -> Any:
    if name == "VERSION":
        return _installed_version()
    raise AttributeError(name)


def main(argv: Sequence[str] | None = None) -> int:
    """Expose version now and fail closed until the Section 06 adapter exists."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    installed_version = _installed_version()
    if arguments == ["--version"]:
        print(installed_version)
        return 0
    print(
        json.dumps(
            {
                "success": False,
                "code": "package.feature_unavailable",
                "exit_category": 15,
                "message": "Installer commands are unavailable in this package build.",
                "version": installed_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 15
