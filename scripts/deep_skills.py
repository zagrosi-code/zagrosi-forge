#!/usr/bin/env python3
"""Backward-compatible wrapper for the renamed Zagrosi helper CLI."""

from zagrosi_skills import run_program


if __name__ == "__main__":
    raise SystemExit(run_program())
