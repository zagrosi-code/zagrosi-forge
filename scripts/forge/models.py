"""Forge models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    code: str
    message: str
    path: str | None = None
    recommendation: str | None = None
    category: str = "general"

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "category": self.category,
        }
        if self.path:
            payload["path"] = self.path
        if self.recommendation:
            payload["recommendation"] = self.recommendation
        return payload


@dataclass(frozen=True, slots=True)
class ProjectInput:
    planning_dir: Path
    input_file: Path | None
    input_mode: str
    generated_file: bool
    brief_word_count: int
    warnings: tuple[str, ...] = ()


class DetachedImplementationError(ValueError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def detached_error_payload(exc: DetachedImplementationError, **extras: Any) -> dict[str, Any]:
    return {
        "success": False,
        "error_code": exc.code,
        "error": str(exc),
        **extras,
        **exc.details,
    }


def detached_io_error_payload(exc: OSError, **extras: Any) -> dict[str, Any]:
    return {
        "success": False,
        "error_code": "detached-io-failure",
        "error": f"Detached implementation I/O failed closed: {exc.__class__.__name__}",
        **extras,
    }
