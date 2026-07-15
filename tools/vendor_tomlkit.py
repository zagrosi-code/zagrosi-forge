#!/usr/bin/env python3
"""Regenerate the audited TOML Kit vendor tree from a verified local sdist."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
import unicodedata


UPSTREAM_NAME = "tomlkit"
UPSTREAM_VERSION = "0.15.0"
EXPECTED_PREFIX = f"{UPSTREAM_NAME}-{UPSTREAM_VERSION}"
RUNTIME_PREFIX = f"{EXPECTED_PREFIX}/tomlkit/"
LICENSE_MEMBER = f"{EXPECTED_PREFIX}/LICENSE"
VENDOR_SCHEMA_DIGEST = (
    "c5e02631a7f8c804180863a9dc88c7aca054c07a62b3ee9dfeb1b08eac13e8c9"
)
TRANSFORMATION_PROFILE = "absolute-import-prefix-v1"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 4_096
# Sorted ``path\0kind\0size\n`` rows from the verified 0.15.0 sdist.
AUDITED_MEMBER_COUNT = 1_113
AUDITED_MEMBER_MANIFEST_SHA256 = (
    "bfafdcbd4059699dae1bd9917d0eda889482ce82a403820af7407def389ca89a"
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class VendorError(ValueError):
    """Raised before publishing any invalid vendor output."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _safe_member_name(name: str) -> PurePosixPath:
    if "\\" in name or any(ord(character) < 32 for character in name):
        raise VendorError(f"unsafe archive member: {name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise VendorError(f"unsafe archive member: {name!r}")
    if path.parts[0] != EXPECTED_PREFIX:
        raise VendorError(f"unexpected archive root: {name!r}")
    return path


def _read_archive(
    artifact: Path, expected_sha256: str
) -> tuple[dict[str, bytes], bytes]:
    if not _DIGEST.fullmatch(expected_sha256):
        raise VendorError("expected SHA-256 must be 64 lowercase hex characters")
    with artifact.open("rb") as stream:
        archive_bytes = stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise VendorError("source artifact exceeds the compressed limit")
    if _sha256(archive_bytes) != expected_sha256:
        raise VendorError("source artifact SHA-256 mismatch")

    selected: dict[str, bytes] = {}
    license_bytes: bytes | None = None
    names: set[str] = set()
    normalized_names: set[str] = set()
    manifest_rows: list[str] = []
    expanded = 0
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > MAX_MEMBERS:
            raise VendorError("source artifact has too many members")
        for member in members:
            path = _safe_member_name(member.name)
            normalized = unicodedata.normalize("NFC", path.as_posix()).casefold()
            if member.name in names or normalized in normalized_names:
                raise VendorError(
                    f"duplicate or colliding archive member: {member.name!r}"
                )
            names.add(member.name)
            normalized_names.add(normalized)
            if not (member.isdir() or member.isreg()):
                raise VendorError(
                    f"links and special members are forbidden: {member.name!r}"
                )
            kind = "directory" if member.isdir() else "file"
            manifest_rows.append(f"{path.as_posix()}\0{kind}\0{member.size}\n")
            if member.isdir():
                continue
            expanded += member.size
            if expanded > MAX_EXPANDED_BYTES:
                raise VendorError("source artifact exceeds the expanded limit")
            if member.name == LICENSE_MEMBER or member.name.startswith(RUNTIME_PREFIX):
                stream = archive.extractfile(member)
                if stream is None:
                    raise VendorError(f"cannot read selected member: {member.name!r}")
                data = stream.read()
                if len(data) != member.size:
                    raise VendorError(
                        f"short read for selected member: {member.name!r}"
                    )
                if member.name == LICENSE_MEMBER:
                    license_bytes = data
                else:
                    relative = PurePosixPath(member.name).relative_to(EXPECTED_PREFIX)
                    selected[relative.as_posix()] = data
    member_manifest = _sha256("".join(sorted(manifest_rows)).encode("utf-8"))
    if (
        len(manifest_rows) != AUDITED_MEMBER_COUNT
        or member_manifest != AUDITED_MEMBER_MANIFEST_SHA256
    ):
        raise VendorError("source artifact member manifest mismatch")
    if license_bytes is None:
        raise VendorError("source artifact is missing LICENSE")
    expected_runtime = {
        "tomlkit/__init__.py",
        "tomlkit/_compat.py",
        "tomlkit/_types.py",
        "tomlkit/_utils.py",
        "tomlkit/api.py",
        "tomlkit/container.py",
        "tomlkit/exceptions.py",
        "tomlkit/items.py",
        "tomlkit/parser.py",
        "tomlkit/py.typed",
        "tomlkit/source.py",
        "tomlkit/toml_char.py",
        "tomlkit/toml_document.py",
        "tomlkit/toml_file.py",
    }
    if set(selected) != expected_runtime:
        missing = sorted(expected_runtime - set(selected))
        extra = sorted(set(selected) - expected_runtime)
        raise VendorError(
            f"unexpected runtime tree; missing={missing!r}, extra={extra!r}"
        )
    return selected, license_bytes


def _rewrite_imports(path: str, data: bytes) -> tuple[bytes, bool]:
    if not path.endswith(".py"):
        return data, False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VendorError(f"runtime source is not UTF-8: {path}") from exc
    rewritten = text.replace(
        "from tomlkit",
        "from zagrosi_forge._vendor.tomlkit",
    ).replace(
        "import tomlkit",
        "from zagrosi_forge._vendor import tomlkit",
    )
    if re.search(r"(^|\s)(from|import) tomlkit", rewritten, flags=re.MULTILINE):
        raise VendorError(f"unrewritten absolute import remains: {path}")
    return rewritten.encode("utf-8"), rewritten != text


def _receipt(
    *,
    selected: dict[str, bytes],
    license_bytes: bytes,
    artifact_sha256: str,
    source_url: str,
    source_commit: str,
    audit_date: str,
) -> dict[str, object]:
    selected_rows = [
        {"path": path, "sha256": _sha256(data), "size": len(data)}
        for path, data in sorted(selected.items())
    ]
    tree_lines = "".join(
        f"{row['path']}\0{row['sha256']}\0{row['size']}\n" for row in selected_rows
    ).encode("utf-8")
    value: dict[str, object] = {
        "schema_version": "1.0",
        "schema_digest": VENDOR_SCHEMA_DIGEST,
        "writer_version": "0.2.0",
        "minimum_reader_version": "0.2.0",
        "limit_policy_version": "1.0",
        "generator_version": "vendor-tomlkit-v1",
        "audit_date": audit_date,
        "transformation_profile": TRANSFORMATION_PROFILE,
        "transformations": [
            "rewrite absolute tomlkit imports to zagrosi_forge._vendor.tomlkit"
        ],
        "upstream": {
            "name": UPSTREAM_NAME,
            "version": UPSTREAM_VERSION,
            "artifact_url": source_url,
            "artifact_sha256": artifact_sha256,
            "verified_tag_commit": source_commit,
        },
        "selected_files": selected_rows,
        "selected_tree_digest": _sha256(tree_lines),
        "license": {
            "expression": "MIT",
            "path": "tomlkit-LICENSE",
            "sha256": _sha256(license_bytes),
        },
    }
    value["record_digest"] = _sha256(_canonical(value))
    return value


def regenerate(
    *,
    artifact: Path,
    expected_sha256: str,
    destination: Path,
    source_url: str,
    source_commit: str,
    audit_date: str,
) -> None:
    """Build and atomically publish one absent vendor directory."""

    if destination.exists():
        raise VendorError(f"destination already exists: {destination}")
    raw_selected, license_bytes = _read_archive(artifact, expected_sha256)
    selected: dict[str, bytes] = {}
    rewritten_paths: list[str] = []
    for path, data in raw_selected.items():
        rewritten, changed = _rewrite_imports(path, data)
        selected[path] = rewritten
        if changed:
            rewritten_paths.append(path)
    if not rewritten_paths:
        raise VendorError("expected import transformation did not occur")
    receipt = _receipt(
        selected=selected,
        license_bytes=license_bytes,
        artifact_sha256=expected_sha256,
        source_url=source_url,
        source_commit=source_commit,
        audit_date=audit_date,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
    )
    try:
        (temporary / "__init__.py").write_text(
            '"""Audited third-party runtime sources; do not edit manually."""\n',
            encoding="utf-8",
        )
        for path, data in selected.items():
            target = temporary / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        (temporary / "tomlkit-LICENSE").write_bytes(license_bytes)
        (temporary / "vendor-receipt.json").write_bytes(_canonical(receipt) + b"\n")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--audit-date", required=True)
    args = parser.parse_args()
    regenerate(
        artifact=args.artifact,
        expected_sha256=args.expected_sha256,
        destination=args.destination,
        source_url=args.source_url,
        source_commit=args.source_commit,
        audit_date=args.audit_date,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
