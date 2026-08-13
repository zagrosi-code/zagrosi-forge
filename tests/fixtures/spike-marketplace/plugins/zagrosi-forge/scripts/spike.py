"""Fail visibly if native verification ever executes candidate fixture code."""

raise RuntimeError("cachebuster verification executed candidate code")
