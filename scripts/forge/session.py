"""Forge session."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any
import io

PRETTY_OUTPUT = False


_CLI_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("forge_cli_context", default=None)


_GATE_STREAMS: ContextVar[tuple[io.StringIO, io.StringIO] | None] = ContextVar("forge_gate_streams", default=None)


_QUALITY_CAPTURE: ContextVar[dict[str, Any] | None] = ContextVar("forge_quality_capture", default=None)
