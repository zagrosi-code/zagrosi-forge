"""Literal argument vectors for the verified Forge launcher; never execute them."""

from __future__ import annotations

from pathlib import Path
import sys


def command(name: str, *args: str) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "zagrosi_skills.py"), name, *args]
