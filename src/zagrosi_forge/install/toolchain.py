"""Checksum-bound tool selection and acquisition without execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from importlib import resources
import os
from pathlib import Path
import stat
from typing import cast
import urllib.parse
import urllib.request

from .contracts import ForgeError, decode_persistent_record


TOOLCHAIN_SCHEMA_DIGEST = (
    "606ca57ef9cd17dd6227fe9100dc6ed666a45138c1d3f1bd326ed11342bf463f"
)
MAX_TOOL_BYTES = 128 * 1024 * 1024


def load_toolchain_lock() -> Mapping[str, object]:
    """Load the immutable toolchain mirror installed with the trusted package."""

    install_root = resources.files("zagrosi_forge.install")
    schema = install_root.joinpath("schemas/toolchain-lock-v1.schema.json").read_bytes()
    if hashlib.sha256(schema).hexdigest() != TOOLCHAIN_SCHEMA_DIGEST:
        raise ForgeError(
            "tool.schema_mismatch",
            15,
            "Packaged toolchain schema digest does not match.",
        )
    lock = decode_persistent_record(
        install_root.joinpath("toolchain-lock.json").read_bytes()
    )
    if lock.get("schema_digest") != TOOLCHAIN_SCHEMA_DIGEST:
        raise ForgeError(
            "tool.schema_mismatch", 15, "Toolchain lock schema digest does not match."
        )
    return lock


def select_artifact(
    lock: Mapping[str, object], *, tool: str, platform: str
) -> Mapping[str, str]:
    """Select exactly one platform artifact, allowing only an explicit `any` fallback."""

    tools = lock.get("tools")
    if not isinstance(tools, Sequence) or isinstance(tools, (str, bytes, bytearray)):
        raise ForgeError("tool.lock_invalid", 15, "Toolchain lock has no tool list.")
    if any(
        not isinstance(item, Mapping)
        or not isinstance(item.get("name"), str)
        or not item.get("name")
        for item in tools
    ):
        raise ForgeError("tool.lock_invalid", 15, "Toolchain entry name is invalid.")
    matches = [
        item for item in tools if isinstance(item, Mapping) and item.get("name") == tool
    ]
    if len(matches) != 1:
        raise ForgeError(
            "tool.unsupported", 15, "Requested tool is not uniquely locked."
        )
    artifacts = matches[0].get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(
        artifacts, (str, bytes, bytearray)
    ):
        raise ForgeError("tool.lock_invalid", 15, "Toolchain entry has no artifacts.")
    if any(
        not isinstance(item, Mapping)
        or not isinstance(item.get("platform"), str)
        or not item.get("platform")
        for item in artifacts
    ):
        raise ForgeError("tool.lock_invalid", 15, "Tool artifact platform is invalid.")
    candidates = [
        item
        for item in artifacts
        if isinstance(item, Mapping) and item.get("platform") in {platform, "any"}
    ]
    exact = [item for item in candidates if item.get("platform") == platform]
    selected = exact or [item for item in candidates if item.get("platform") == "any"]
    if len(selected) != 1:
        raise ForgeError(
            "tool.platform_unsupported",
            15,
            "Requested tool platform is not uniquely locked.",
        )
    required = {"platform", "url", "sha256", "archive_type", "executable"}
    if any(
        not isinstance(selected[0].get(key), str) or not selected[0][key]
        for key in required
    ):
        raise ForgeError("tool.lock_invalid", 15, "Tool artifact entry is incomplete.")
    return cast(Mapping[str, str], selected[0])


def verify_artifact(path: Path, *, expected_sha256: str) -> None:
    """Verify a regular artifact by full SHA-256 before any consumer can execute it."""

    if path.is_symlink() or not path.is_file():
        raise ForgeError(
            "tool.file_invalid", 15, "Tool artifact is not a regular file."
        )
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_TOOL_BYTES:
        raise ForgeError(
            "tool.file_invalid", 15, "Tool artifact exceeds the trusted file contract."
        )
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ForgeError(
            "tool.hash_mismatch", 15, "Tool artifact SHA-256 does not match the lock."
        )


def _destination_file(destination: Path, url: str) -> Path:
    if not destination.is_absolute() or destination.is_symlink():
        raise ForgeError(
            "tool.destination_invalid",
            15,
            "Tool destination must be an absolute real directory.",
        )
    destination.mkdir(parents=False, exist_ok=True)
    if not destination.is_dir():
        raise ForgeError(
            "tool.destination_invalid", 15, "Tool destination is not a directory."
        )
    filename = Path(urllib.parse.urlparse(url).path).name
    if not filename or filename in {".", ".."}:
        raise ForgeError(
            "tool.url_invalid", 15, "Tool artifact URL has no safe filename."
        )
    return destination / filename


def acquire_artifact(
    lock: Mapping[str, object],
    *,
    tool: str,
    platform: str,
    destination: Path,
    offline: bool,
) -> Path:
    """Acquire into one declared disposable directory, verify, and never execute."""

    artifact = select_artifact(lock, tool=tool, platform=platform)
    target = _destination_file(destination, artifact["url"])
    if target.exists():
        verify_artifact(target, expected_sha256=artifact["sha256"])
        return target
    if offline:
        raise ForgeError(
            "tool.offline_missing", 15, "Verified tool artifact is unavailable offline."
        )

    temporary = target.with_name(f".{target.name}.part")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ForgeError(
            "tool.acquire_conflict", 15, "Tool acquisition is already in progress."
        ) from exc
    try:
        total = 0
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            with urllib.request.urlopen(artifact["url"], timeout=90) as response:  # noqa: S310
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_TOOL_BYTES:
                        raise ForgeError(
                            "tool.limit_exceeded",
                            15,
                            "Tool artifact exceeds the download limit.",
                        )
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        verify_artifact(temporary, expected_sha256=artifact["sha256"])
        os.link(temporary, target)
        temporary.unlink()
        return target
    finally:
        if temporary.exists():
            temporary.unlink()
