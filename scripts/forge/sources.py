"""Forge sources."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import os
import re

from . import CLI_PATH, verify_sources
from . import detached_contract as _detached_contract
from . import handoff_wire as _handoff_wire
from . import models as _models
from . import secure_io as _secure_io
from . import storage as _storage

def implementation_source_paths() -> dict[str, Path]:
    running_tool = _storage.absolute_path_no_follow(str(CLI_PATH))
    plugin_root = running_tool.parent.parent
    paths = {
        "tool": plugin_root / "scripts" / "zagrosi_skills.py",
        "skill": plugin_root / "skills" / "zagrosi-implement" / "SKILL.md",
        "test": plugin_root / "tests" / "test_zagrosi_skills.py",
    }
    if running_tool != paths["tool"]:
        raise _models.DetachedImplementationError(
            "unsafe-implement-source",
            "Detached mode must run from the fixed scripts/zagrosi_skills.py plugin path.",
            implement_source="tool",
            expected_implement_source_path=str(paths["tool"]),
            actual_implement_source_path=str(running_tool),
        )
    return paths


def reopen_implementation_source(source: str, path: Path) -> dict[str, Any]:
    parent_fd: int | None = None
    reopened_parent_fd: int | None = None
    try:
        parent_fd = _secure_io.open_directory_chain_no_follow(path.parent)
        parent_stat = os.fstat(parent_fd)
        raw = _secure_io.read_single_link_regular_at(parent_fd, path.name, cap=_detached_contract.IMPLEMENTATION_SOURCE_CAP)
        reopened_parent_fd = _secure_io.open_directory_chain_no_follow(path.parent)
        reopened_parent_stat = os.fstat(reopened_parent_fd)
        reopened_raw = _secure_io.read_single_link_regular_at(reopened_parent_fd, path.name, cap=_detached_contract.IMPLEMENTATION_SOURCE_CAP)
        if (
            (parent_stat.st_dev, parent_stat.st_ino) != (reopened_parent_stat.st_dev, reopened_parent_stat.st_ino)
            or raw != reopened_raw
        ):
            raise _models.DetachedImplementationError(
                "implement-source-changed",
                f"Implementation {source} source changed while its complete bytes were reopened.",
                implement_source=source,
                implement_source_path=str(path),
            )
        return {"path": str(path), "sha256": _handoff_wire.sha256_digest(raw), "size": len(raw)}
    except _models.DetachedImplementationError as exc:
        if exc.code == "implement-source-changed":
            raise
        raise _models.DetachedImplementationError(
            "unsafe-implement-source",
            f"Implementation {source} source must be a component-wise no-follow regular single-link file.",
            implement_source=source,
            implement_source_path=str(path),
            source_error_code=exc.code,
        ) from exc
    except OSError as exc:
        raise _models.DetachedImplementationError(
            "unsafe-implement-source",
            f"Implementation {source} source could not be reopened safely.",
            implement_source=source,
            implement_source_path=str(path),
        ) from exc
    finally:
        if reopened_parent_fd is not None:
            os.close(reopened_parent_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def verify_detached_contract_reference(skill_path: Path) -> dict[str, Any]:
    contract_path = skill_path.parent / _detached_contract.DETACHED_CONTRACT_RELATIVE_PATH
    record = reopen_implementation_source("contract", contract_path)
    if record["sha256"] != _detached_contract.DETACHED_CONTRACT_SHA256:
        raise _models.DetachedImplementationError(
            "implement-contract-drift",
            "Detached implementation contract bytes do not match the tool-pinned complete-file sha256.",
            implement_source="contract",
            implement_source_path=record["path"],
            expected_implement_source_sha256=_detached_contract.DETACHED_CONTRACT_SHA256,
            actual_implement_source_sha256=record["sha256"],
        )
    return record


def expected_implementation_source_hashes(args: argparse.Namespace) -> dict[str, str]:
    expected: dict[str, str] = {}
    for source in _detached_contract.IMPLEMENTATION_SOURCE_NAMES:
        argument = f"--expected-implement-{source}-sha256"
        value = getattr(args, f"expected_implement_{source}_sha256", None)
        if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise _models.DetachedImplementationError(
                "missing-implement-source-hash",
                f"Detached frozen-planning mode requires {argument} with an exact sha256 digest.",
                implement_source=source,
                required_argument=argument,
            )
        expected[source] = value
    return expected


def reopen_implementation_sources(*, expected_hashes: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    try:
        verify_sources()
    except ImportError as exc:
        raise _models.DetachedImplementationError(
            "implement-source-drift", "Implementation runtime modules no longer match the tool-pinned manifest.",
            implement_source="tool",
        ) from exc
    records: dict[str, dict[str, Any]] = {}
    paths = implementation_source_paths()
    verify_detached_contract_reference(paths["skill"])
    for source, path in paths.items():
        record = reopen_implementation_source(source, path)
        expected_sha256 = expected_hashes.get(source) if expected_hashes is not None else None
        if expected_sha256 is not None and record["sha256"] != expected_sha256:
            raise _models.DetachedImplementationError(
                "implement-source-drift",
                f"Implementation {source} source bytes do not match the required complete-file sha256.",
                implement_source=source,
                implement_source_path=record["path"],
                expected_implement_source_sha256=expected_sha256,
                actual_implement_source_sha256=record["sha256"],
            )
        records[source] = record
    return records


def implementation_source_config_fields(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for source in _detached_contract.IMPLEMENTATION_SOURCE_NAMES:
        record = records[source]
        fields[f"implement_{source}_path"] = record["path"]
        fields[f"implement_{source}_sha256"] = record["sha256"]
        fields[f"implement_{source}_size"] = record["size"]
    return fields


def verify_implementation_sources(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    paths = implementation_source_paths()
    verify_detached_contract_reference(paths["skill"])
    expected_hashes: dict[str, str] = {}
    for source in _detached_contract.IMPLEMENTATION_SOURCE_NAMES:
        path_field = f"implement_{source}_path"
        hash_field = f"implement_{source}_sha256"
        size_field = f"implement_{source}_size"
        expected_path = str(paths[source])
        expected_sha256 = config.get(hash_field)
        expected_size = config.get(size_field)
        if config.get(path_field) != expected_path:
            raise _models.DetachedImplementationError(
                "invalid-detached-config",
                f"Detached config does not bind the exact current implementation {source} source path.",
                implement_source=source,
                expected_implement_source_path=expected_path,
                actual_implement_source_path=config.get(path_field),
            )
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_sha256):
            raise _models.DetachedImplementationError(
                "invalid-detached-config",
                f"Detached config implementation {source} source sha256 is invalid.",
                implement_source=source,
            )
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
            raise _models.DetachedImplementationError(
                "invalid-detached-config",
                f"Detached config implementation {source} source size is invalid.",
                implement_source=source,
            )
        expected_hashes[source] = expected_sha256
    records = reopen_implementation_sources(expected_hashes=expected_hashes)
    for source, record in records.items():
        expected_size = config[f"implement_{source}_size"]
        if record["size"] != expected_size:
            raise _models.DetachedImplementationError(
                "implement-source-drift",
                f"Implementation {source} source size no longer matches implement-setup.",
                implement_source=source,
                implement_source_path=record["path"],
                expected_implement_source_size=expected_size,
                actual_implement_source_size=record["size"],
            )
    return records
