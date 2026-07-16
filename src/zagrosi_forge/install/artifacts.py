"""Deterministic, bounded artifact writing, inspection, and offline builds."""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
import csv
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import compat32
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import platform
import re
import signal
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import tomllib
from typing import Never
import unicodedata
import urllib.parse
import zipfile
import zlib

from . import bundle as _bundle_contract
from .bundle import BundlePolicy, CanonicalBundle, canonical_bundle_json_bytes
from .contracts import BundleEntry, ForgeError, parse_release_version
from .policies import LIMIT_POLICY
from .toolchain import MAX_TOOL_BYTES, load_toolchain_lock, select_artifact
from .version import VERSION


_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_GENERATED_MANIFEST_PATH = ".codex-plugin/bundle-manifest.json"
_PLUGIN_MANIFEST_PATH = ".codex-plugin/plugin.json"
_CHUNK_BYTES = 64 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_UV_VERSION = "0.11.23"
_BACKEND_DISTRIBUTION = "uv-build"
_BACKEND_VERSION = "0.11.28"
_MAX_METADATA_BYTES = 1024 * 1024
_SDIST_ROOT_MEMBERS = frozenset(
    {
        "PKG-INFO",
        "LICENSE",
        "NOTICE.md",
        "README.md",
        "component-inventory.json",
        "pyproject.toml",
    }
)
_WHEEL_BYTES = (
    b"Wheel-Version: 1.0\n"
    b"Generator: uv 0.11.28\n"
    b"Root-Is-Purelib: true\n"
    b"Tag: py3-none-any\n"
)
_ENTRY_POINTS_BYTES = (
    b"[console_scripts]\nzagrosi-forge = zagrosi_forge.install:main\n\n"
)
_CONSTRAINTS_SHA256 = "11e0e75c3bb12f61ebc4b70889647dd31093d8b6ce314e01d24e35967132b1d9"
_CONSTRAINT_HASHES = (
    "fb10719142d431087f5e177d43c83f304391084a28ea52e1588542fe0f113f91",
    "384a352d6b00df4824dcb56baa070498666795c3fd1b6d7377368649b695f864",
    "3ec5e107bb7128cd15c274f9562b966b038f6342cb7c94239c02e4f1809e7690",
    "5fb0d9e5e5b808d9ca3ed15356633b7e3ae00ae43f47e50e75eb72dddf98c6bc",
    "7af5f0a4319832aefd4deeff5ef01df2809b71e0bfc897bab42c2670ec2fa21b",
    "1a46aace1911c4502acc01c437a47602839b4dff91f58a3388998450f66cb5b1",
    "67b4d61a16886c8f4d91f987ea813630b80d1fb57f1bd5d2466591a72bb2a7af",
    "fa4f2807129c852c2472e1fc2e1a2ba28018023a9349950cb19622fbb21156e4",
    "cb5265d4f692e30889d6ef81608041e970763c684919acdb41ed6af5c345296b",
    "d99cb38411412134781140cc64dc7d296dbc383fa1e81ddc13ecb6863852ecf5",
    "a3b8bd731e57f1451de4ef15ade18d328a68b6cad173794eda8fafe2e912e997",
    "79091c7ee68a90ff44dd876c6d4b21d3c133015612ddecf9ade931779bfb4d92",
    "6c89dde2c9d3bbfde41fa92bd252a87ab016c7722a85331315151bd39a78a591",
    "2e57f0a02736b0b08f5d40b96be52be235de0898b58d00b0397485dffc55381b",
    "75327189c71ad829fb1fb8d1b4be02b6b26aaf98e76c2817a68d16aa15c31a1d",
    "75c1b66084502b42adbd1c6076347e2b97866030f81ecda5aac541499e928b6f",
    "52ad1a230e2a0e7dfdf1fa6a93e40a94fcf3c390ab2aa44a7bd0d31b780d02ed",
    "b5d4a79a0da94de28e2f2890782562e391d9936b686adf1403e671c355ad1370",
    "daba66fd25a9a82caa23949686e967cbb69fd056177e991650643186057adaef",
)
_PYTHON_NORMALIZATION_PROFILE = "python-artifact-v1"
_PYTHON_CONTAINER_METADATA_EXCLUSIONS = (
    (
        "wheel-zip",
        (
            "archive-comment",
            "member-comment",
            "member-compressed-size",
            "member-compression-container",
            "member-create-system",
            "member-data-descriptor-signature",
            "member-deflate-representation",
            "member-dos-attribute-bits",
            "member-extra",
            "member-file-permission-bits",
            "member-flag-data-descriptor",
            "member-header-offset",
            "member-internal-attributes",
            "member-name-encoding-flag",
            "member-order",
            "member-timestamp",
            "member-version-fields",
        ),
    ),
    (
        "sdist-tar-gzip",
        (
            "gzip-comment",
            "gzip-deflate-representation",
            "gzip-extra-field",
            "gzip-extra-flags",
            "gzip-filename",
            "gzip-header-crc",
            "gzip-mtime",
            "gzip-os",
            "gzip-text-flag",
            "member-checksum-container",
            "member-file-permission-bits",
            "member-gid",
            "member-gname",
            "member-header-format",
            "member-mtime",
            "member-order",
            "member-pax-headers",
            "member-regular-type-encoding",
            "member-uid",
            "member-uname",
            "tar-end-padding",
        ),
    ),
)
_ARTIFACT_TOKEN = object()
_OFFLINE_EVIDENCE_TOKEN = object()
_OFFLINE_IDENTITY_TOKEN = object()
_PYTHON_POLICY_TOKEN = object()
_PYTHON_RESULT_TOKEN = object()
_AUTHORITY_SECRET = os.urandom(32)


def _artifact_error(code: str, message: str) -> ForgeError:
    return ForgeError(code, 12, message)


def _limit_error() -> ForgeError:
    return _artifact_error(
        "bundle.limit_exceeded", "Artifact exceeds the trusted archive limits."
    )


def _unsafe_member() -> ForgeError:
    return _artifact_error(
        "artifact.unsafe_member", "Artifact contains an unsafe archive member."
    )


def _canonical_json(value: object, *, final_newline: bool = True) -> bytes:
    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, UnicodeEncodeError, ValueError, TypeError) as exc:
        raise _artifact_error(
            "artifact.manifest_invalid", "Artifact manifest cannot be serialized."
        ) from exc
    return rendered + (b"\n" if final_newline else b"")


def _authority_digest(kind: str, value: object) -> str:
    domain = kind.encode("ascii") + b"\0" + _canonical_json(value, final_newline=False)
    return hmac.new(_AUTHORITY_SECRET, domain, hashlib.sha256).hexdigest()


def _decode_json(raw: bytes) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    try:
        return json.loads(
            raw,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _artifact_error(
            "artifact.manifest_invalid", "Artifact manifest is not valid JSON."
        ) from exc


def _portable_path(path: str) -> None:
    try:
        BundleEntry(
            path=path,
            file_type="regular",
            mode=0o644,
            size=0,
            sha256="0" * 64,
        )
    except (TypeError, ValueError) as exc:
        raise _unsafe_member() from exc


def _collision_key(path: str) -> str:
    return unicodedata.normalize("NFKC", path).casefold()


def _normalized_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _member_record(member: BundleEntry) -> dict[str, object]:
    return {
        "file_type": member.file_type,
        "mode": member.mode,
        "path": member.path,
        "sha256": member.sha256,
        "size": member.size,
    }


def _manifest_digest(
    artifact_kind: str,
    distribution: str,
    version: str,
    members: tuple[BundleEntry, ...],
    directories: tuple[ArtifactDirectory, ...],
    root_directory_mode: int | None,
    policy_digest: str,
) -> str:
    domain = {
        "artifact_kind": artifact_kind,
        "distribution": distribution,
        "directories": [
            {"mode": directory.mode, "path": directory.path}
            for directory in directories
        ],
        "members": [_member_record(member) for member in members],
        "normalization_profile": _PYTHON_NORMALIZATION_PROFILE,
        "policy_digest": policy_digest,
        "root_directory_mode": root_directory_mode,
        "version": version,
    }
    return hashlib.sha256(_canonical_json(domain)).hexdigest()


@dataclass(frozen=True, slots=True)
class _ControlledArchiveEntry:
    path: str
    data: bytes
    mode: int

    def __post_init__(self) -> None:
        _portable_path(self.path)
        if not isinstance(self.data, bytes):
            raise TypeError("data")
        if isinstance(self.mode, bool) or self.mode not in {0o644, 0o755}:
            raise ValueError("mode")
        if len(self.data) > LIMIT_POLICY.value("bundle_member_bytes"):
            raise _limit_error()


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_members: int
    max_compressed_bytes: int
    max_expanded_bytes: int
    max_ratio: int
    max_member_bytes: int = LIMIT_POLICY.value("bundle_member_bytes")

    def __post_init__(self) -> None:
        values = (
            (self.max_members, LIMIT_POLICY.value("bundle_files")),
            (
                self.max_compressed_bytes,
                LIMIT_POLICY.value("archive_compressed_bytes"),
            ),
            (
                self.max_expanded_bytes,
                LIMIT_POLICY.value("archive_expanded_bytes"),
            ),
            (self.max_ratio, LIMIT_POLICY.value("archive_ratio")),
            (self.max_member_bytes, LIMIT_POLICY.value("bundle_member_bytes")),
        )
        for value, installed_maximum in values:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("archive limit")
            if value > installed_maximum:
                raise ValueError("archive limit exceeds installed policy")


@dataclass(frozen=True, slots=True)
class ArtifactDirectory:
    path: str
    mode: int

    def __post_init__(self) -> None:
        _portable_path(self.path)
        if (
            isinstance(self.mode, bool)
            or not isinstance(self.mode, int)
            or not 0 <= self.mode <= 0o777
        ):
            raise ValueError("directory mode")


@dataclass(frozen=True, slots=True, init=False)
class PythonArtifactPolicy:
    policy_digest: str
    bundle_policy_digest: str
    distribution: str
    version: str
    backend_distribution: str
    backend_version: str
    build_backend: str
    module_name: str
    module_root: str
    source_include: tuple[str, ...]
    normalization_profile: str
    container_metadata_exclusions: tuple[tuple[str, tuple[str, ...]], ...]
    sdist_root_members: tuple[str, ...]
    package_source_paths: tuple[str, ...]
    wheel_metadata_paths: tuple[str, ...]
    wheel_bytes: bytes
    entry_points_bytes: bytes
    _bundle_policy: BundlePolicy = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)
    _authority_digest_value: str = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        policy_digest: str,
        bundle_policy: BundlePolicy,
        package_source_paths: tuple[str, ...],
        wheel_metadata_paths: tuple[str, ...],
        _token: object,
    ) -> None:
        if _token is not _PYTHON_POLICY_TOKEN:
            raise TypeError("PythonArtifactPolicy is derived from trusted policy")
        values: dict[str, object] = {
            "policy_digest": policy_digest,
            "bundle_policy_digest": bundle_policy.policy_digest,
            "distribution": "zagrosi-forge",
            "version": VERSION,
            "backend_distribution": _BACKEND_DISTRIBUTION,
            "backend_version": _BACKEND_VERSION,
            "build_backend": "uv_build",
            "module_name": "zagrosi_forge",
            "module_root": "src",
            "source_include": ("component-inventory.json",),
            "normalization_profile": _PYTHON_NORMALIZATION_PROFILE,
            "container_metadata_exclusions": _PYTHON_CONTAINER_METADATA_EXCLUSIONS,
            "sdist_root_members": tuple(sorted(_SDIST_ROOT_MEMBERS)),
            "package_source_paths": package_source_paths,
            "wheel_metadata_paths": wheel_metadata_paths,
            "wheel_bytes": _WHEEL_BYTES,
            "entry_points_bytes": _ENTRY_POINTS_BYTES,
            "_bundle_policy": bundle_policy,
            "_seal": _PYTHON_POLICY_TOKEN,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "_authority_digest_value",
            _authority_digest("python-artifact-policy", _python_policy_domain(self)),
        )

    def __reduce__(self) -> Never:
        raise TypeError("trusted Python artifact policies are not serializable")


@dataclass(frozen=True, slots=True, init=False)
class ArtifactEvidence:
    artifact_kind: str
    distribution: str
    version: str
    members: tuple[BundleEntry, ...]
    directories: tuple[ArtifactDirectory, ...]
    root_directory_mode: int | None
    normalized_manifest_digest: str
    policy_digest: str
    _seal: object = field(repr=False, compare=False)
    _authority_digest_value: str = field(repr=False, compare=False)
    manifest_path: str | None = None

    def __init__(
        self,
        *,
        artifact_kind: str,
        distribution: str,
        version: str,
        members: tuple[BundleEntry, ...],
        directories: tuple[ArtifactDirectory, ...],
        root_directory_mode: int | None,
        normalized_manifest_digest: str,
        policy_digest: str,
        manifest_path: str | None = None,
        _token: object,
    ) -> None:
        if _token is not _ARTIFACT_TOKEN:
            raise TypeError("ArtifactEvidence is created only by trusted inspection")
        values = {
            "artifact_kind": artifact_kind,
            "distribution": distribution,
            "version": version,
            "members": members,
            "directories": directories,
            "root_directory_mode": root_directory_mode,
            "normalized_manifest_digest": normalized_manifest_digest,
            "policy_digest": policy_digest,
            "manifest_path": manifest_path,
            "_seal": _ARTIFACT_TOKEN,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "_authority_digest_value",
            _authority_digest("artifact-evidence", _artifact_evidence_domain(self)),
        )

    def __reduce__(self) -> Never:
        raise TypeError("artifact evidence is not serializable")


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    matches: bool
    differences: tuple[str, ...]


@dataclass(frozen=True, slots=True, init=False)
class OfflineBuildIdentity:
    uv_version: str
    uv_sha256: str
    backend_name: str
    backend_version: str
    backend_sha256: str
    constraints_sha256: str
    _seal: object = field(repr=False, compare=False)
    _authority_digest_value: str = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        uv_version: str,
        uv_sha256: str,
        backend_name: str,
        backend_version: str,
        backend_sha256: str,
        constraints_sha256: str,
        _token: object,
    ) -> None:
        if _token is not _OFFLINE_IDENTITY_TOKEN:
            raise TypeError("OfflineBuildIdentity is created only by trusted inputs")
        values = {
            "uv_version": uv_version,
            "uv_sha256": uv_sha256,
            "backend_name": backend_name,
            "backend_version": backend_version,
            "backend_sha256": backend_sha256,
            "constraints_sha256": constraints_sha256,
            "_seal": _OFFLINE_IDENTITY_TOKEN,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "_authority_digest_value",
            _authority_digest("offline-build-identity", _offline_identity_domain(self)),
        )

    def __reduce__(self) -> Never:
        raise TypeError("offline build identities are not serializable")


@dataclass(frozen=True, slots=True)
class _LockedBuildInputs:
    identity: OfflineBuildIdentity
    uv_spec: Mapping[str, str]
    backend_filename: str
    constraints: bytes


@dataclass(frozen=True, slots=True, init=False)
class OfflineBuildEvidence:
    command_flags: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    resolver_mode: str
    egress: str
    returncode: int
    captured_output_bytes: int
    captured_tail_bytes: int
    _seal: object = field(repr=False, compare=False)
    _authority_digest_value: str = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        command_flags: tuple[str, ...],
        environment: tuple[tuple[str, str], ...],
        resolver_mode: str,
        egress: str,
        returncode: int,
        captured_output_bytes: int,
        captured_tail_bytes: int,
        _token: object,
    ) -> None:
        if _token is not _OFFLINE_EVIDENCE_TOKEN:
            raise TypeError("OfflineBuildEvidence is created only by trusted builds")
        values: dict[str, object] = {
            "command_flags": command_flags,
            "environment": environment,
            "resolver_mode": resolver_mode,
            "egress": egress,
            "returncode": returncode,
            "captured_output_bytes": captured_output_bytes,
            "captured_tail_bytes": captured_tail_bytes,
            "_seal": _OFFLINE_EVIDENCE_TOKEN,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "_authority_digest_value",
            _authority_digest("offline-build-evidence", _offline_evidence_domain(self)),
        )

    def __reduce__(self) -> Never:
        raise TypeError("offline build evidence is not serializable")


@dataclass(frozen=True, slots=True, init=False)
class PythonBuildResult:
    wheel_name: str
    wheel_bytes: bytes
    sdist_name: str
    sdist_bytes: bytes
    uv_version: str
    uv_sha256: str
    backend_name: str
    backend_version: str
    backend_sha256: str
    constraints_sha256: str
    base_payload_digest: str
    artifact_policy_digest: str
    command: tuple[str, ...]
    offline_evidence: OfflineBuildEvidence
    _seal: object = field(repr=False, compare=False)
    _authority_digest_value: str = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        wheel_name: str,
        wheel_bytes: bytes,
        sdist_name: str,
        sdist_bytes: bytes,
        identity: OfflineBuildIdentity,
        base_payload_digest: str,
        artifact_policy_digest: str,
        command: tuple[str, ...],
        offline_evidence: OfflineBuildEvidence,
        _token: object,
    ) -> None:
        if _token is not _PYTHON_RESULT_TOKEN:
            raise TypeError("PythonBuildResult is created only by trusted builds")
        if not _is_offline_identity(identity) or not _is_offline_evidence(
            offline_evidence
        ):
            raise TypeError("trusted Python build authority is required")
        values: dict[str, object] = {
            "wheel_name": wheel_name,
            "wheel_bytes": bytes(wheel_bytes),
            "sdist_name": sdist_name,
            "sdist_bytes": bytes(sdist_bytes),
            "uv_version": identity.uv_version,
            "uv_sha256": identity.uv_sha256,
            "backend_name": identity.backend_name,
            "backend_version": identity.backend_version,
            "backend_sha256": identity.backend_sha256,
            "constraints_sha256": identity.constraints_sha256,
            "base_payload_digest": base_payload_digest,
            "artifact_policy_digest": artifact_policy_digest,
            "command": command,
            "offline_evidence": offline_evidence,
            "_seal": _PYTHON_RESULT_TOKEN,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "_authority_digest_value",
            _authority_digest("python-build-result", _python_result_domain(self)),
        )

    def __reduce__(self) -> Never:
        raise TypeError("Python build results are not serializable")


def _python_policy_domain(value: PythonArtifactPolicy) -> dict[str, object]:
    return {
        "backend_distribution": value.backend_distribution,
        "backend_version": value.backend_version,
        "build_backend": value.build_backend,
        "bundle_policy_digest": value.bundle_policy_digest,
        "container_metadata_exclusions": value.container_metadata_exclusions,
        "distribution": value.distribution,
        "entry_points_sha256": hashlib.sha256(value.entry_points_bytes).hexdigest(),
        "module_name": value.module_name,
        "module_root": value.module_root,
        "normalization_profile": value.normalization_profile,
        "package_source_paths": value.package_source_paths,
        "policy_digest": value.policy_digest,
        "sdist_root_members": value.sdist_root_members,
        "source_include": value.source_include,
        "version": value.version,
        "wheel_metadata_paths": value.wheel_metadata_paths,
        "wheel_sha256": hashlib.sha256(value.wheel_bytes).hexdigest(),
    }


def _artifact_evidence_domain(value: ArtifactEvidence) -> dict[str, object]:
    return {
        "artifact_kind": value.artifact_kind,
        "directories": [
            {"mode": directory.mode, "path": directory.path}
            for directory in value.directories
        ],
        "distribution": value.distribution,
        "manifest_path": value.manifest_path,
        "members": [_member_record(member) for member in value.members],
        "normalized_manifest_digest": value.normalized_manifest_digest,
        "policy_digest": value.policy_digest,
        "root_directory_mode": value.root_directory_mode,
        "version": value.version,
    }


def _offline_identity_domain(value: OfflineBuildIdentity) -> dict[str, object]:
    return {
        "backend_name": value.backend_name,
        "backend_sha256": value.backend_sha256,
        "backend_version": value.backend_version,
        "constraints_sha256": value.constraints_sha256,
        "uv_sha256": value.uv_sha256,
        "uv_version": value.uv_version,
    }


def _offline_evidence_domain(value: OfflineBuildEvidence) -> dict[str, object]:
    return {
        "captured_output_bytes": value.captured_output_bytes,
        "captured_tail_bytes": value.captured_tail_bytes,
        "command_flags": value.command_flags,
        "egress": value.egress,
        "environment": value.environment,
        "resolver_mode": value.resolver_mode,
        "returncode": value.returncode,
    }


def _python_result_domain(value: PythonBuildResult) -> dict[str, object]:
    return {
        "artifact_policy_digest": value.artifact_policy_digest,
        "backend_name": value.backend_name,
        "backend_sha256": value.backend_sha256,
        "backend_version": value.backend_version,
        "base_payload_digest": value.base_payload_digest,
        "command": value.command,
        "constraints_sha256": value.constraints_sha256,
        "offline_evidence": _offline_evidence_domain(value.offline_evidence),
        "sdist_name": value.sdist_name,
        "sdist_sha256": hashlib.sha256(value.sdist_bytes).hexdigest(),
        "sdist_size": len(value.sdist_bytes),
        "uv_sha256": value.uv_sha256,
        "uv_version": value.uv_version,
        "wheel_name": value.wheel_name,
        "wheel_sha256": hashlib.sha256(value.wheel_bytes).hexdigest(),
        "wheel_size": len(value.wheel_bytes),
    }


def _is_offline_identity(value: object) -> bool:
    try:
        return (
            type(value) is OfflineBuildIdentity
            and value._seal is _OFFLINE_IDENTITY_TOKEN
            and value.uv_version == _UV_VERSION
            and isinstance(value.uv_sha256, str)
            and _DIGEST.fullmatch(value.uv_sha256) is not None
            and value.backend_name == _BACKEND_DISTRIBUTION
            and value.backend_version == _BACKEND_VERSION
            and isinstance(value.backend_sha256, str)
            and _DIGEST.fullmatch(value.backend_sha256) is not None
            and value.constraints_sha256 == _CONSTRAINTS_SHA256
            and hmac.compare_digest(
                value._authority_digest_value,
                _authority_digest(
                    "offline-build-identity", _offline_identity_domain(value)
                ),
            )
        )
    except (AttributeError, ForgeError, TypeError, ValueError):
        return False


def _is_offline_evidence(value: object) -> bool:
    required_flags = {
        "--build-constraints",
        "--find-links",
        "--no-config",
        "--no-index",
        "--no-managed-python",
        "--no-sources",
        "--offline",
        "--out-dir",
        "--python",
        "--require-hashes",
        "--wheel",
    }
    expected_environment = (
        ("PIP_NO_INDEX", "1"),
        ("UV_NO_INDEX", "1"),
        ("UV_OFFLINE", "1"),
        ("UV_PYTHON_DOWNLOADS", "never"),
    )
    try:
        return (
            type(value) is OfflineBuildEvidence
            and value._seal is _OFFLINE_EVIDENCE_TOKEN
            and isinstance(value.command_flags, tuple)
            and all(isinstance(item, str) for item in value.command_flags)
            and required_flags <= set(value.command_flags)
            and value.environment == expected_environment
            and value.resolver_mode
            == "offline-no-index-fresh-cache-verified-wheelhouse"
            and value.egress == "not_claimed"
            and value.returncode == 0
            and isinstance(value.captured_output_bytes, int)
            and not isinstance(value.captured_output_bytes, bool)
            and 0
            <= value.captured_output_bytes
            <= LIMIT_POLICY.value("subprocess_channel_bytes")
            and isinstance(value.captured_tail_bytes, int)
            and not isinstance(value.captured_tail_bytes, bool)
            and 0
            <= value.captured_tail_bytes
            <= LIMIT_POLICY.value("subprocess_tail_bytes")
            and value.captured_tail_bytes <= value.captured_output_bytes
            and hmac.compare_digest(
                value._authority_digest_value,
                _authority_digest(
                    "offline-build-evidence", _offline_evidence_domain(value)
                ),
            )
        )
    except (AttributeError, ForgeError, TypeError, ValueError):
        return False


def _is_artifact_evidence(value: object) -> bool:
    try:
        if (
            type(value) is not ArtifactEvidence
            or value._seal is not _ARTIFACT_TOKEN
            or not isinstance(value.members, tuple)
            or not isinstance(value.directories, tuple)
            or not isinstance(value.normalized_manifest_digest, str)
            or _DIGEST.fullmatch(value.normalized_manifest_digest) is None
            or not isinstance(value.policy_digest, str)
            or _DIGEST.fullmatch(value.policy_digest) is None
            or tuple(member.path for member in value.members)
            != tuple(sorted((member.path for member in value.members), key=str.encode))
            or len({_collision_key(member.path) for member in value.members})
            != len(value.members)
            or any(
                type(member) is not BundleEntry
                or member.file_type != "regular"
                or member.mode not in {0o644, 0o755}
                or isinstance(member.size, bool)
                or not isinstance(member.size, int)
                or not 0 <= member.size <= LIMIT_POLICY.value("bundle_member_bytes")
                or not isinstance(member.sha256, str)
                or _DIGEST.fullmatch(member.sha256) is None
                for member in value.members
            )
            or tuple(directory.path for directory in value.directories)
            != tuple(
                sorted(
                    (directory.path for directory in value.directories), key=str.encode
                )
            )
            or len({_collision_key(directory.path) for directory in value.directories})
            != len(value.directories)
            or any(
                type(directory) is not ArtifactDirectory
                for directory in value.directories
            )
        ):
            return False
        for member in value.members:
            _portable_path(member.path)
        for directory in value.directories:
            _portable_path(directory.path)
        if value.artifact_kind in {"wheel", "sdist"}:
            if (
                value.manifest_path is not None
                or value.normalized_manifest_digest
                != _manifest_digest(
                    value.artifact_kind,
                    value.distribution,
                    value.version,
                    value.members,
                    value.directories,
                    value.root_directory_mode,
                    value.policy_digest,
                )
            ):
                return False
        elif (
            value.artifact_kind != "plugin-zip"
            or value.distribution != "zagrosi-forge"
            or value.manifest_path != _GENERATED_MANIFEST_PATH
            or value.directories
            or value.root_directory_mode is not None
        ):
            return False
        return hmac.compare_digest(
            value._authority_digest_value,
            _authority_digest("artifact-evidence", _artifact_evidence_domain(value)),
        )
    except (AttributeError, ForgeError, TypeError, ValueError):
        return False


def _is_python_build_result(value: object) -> bool:
    try:
        return (
            type(value) is PythonBuildResult
            and value._seal is _PYTHON_RESULT_TOKEN
            and isinstance(value.wheel_bytes, bytes)
            and isinstance(value.sdist_bytes, bytes)
            and value.wheel_name.startswith(f"zagrosi_forge-{VERSION}-")
            and value.wheel_name.endswith(".whl")
            and Path(value.wheel_name).name == value.wheel_name
            and value.sdist_name == f"zagrosi_forge-{VERSION}.tar.gz"
            and value.uv_version == _UV_VERSION
            and isinstance(value.uv_sha256, str)
            and _DIGEST.fullmatch(value.uv_sha256) is not None
            and value.backend_name == _BACKEND_DISTRIBUTION
            and value.backend_version == _BACKEND_VERSION
            and isinstance(value.backend_sha256, str)
            and _DIGEST.fullmatch(value.backend_sha256) is not None
            and value.constraints_sha256 == _CONSTRAINTS_SHA256
            and isinstance(value.base_payload_digest, str)
            and _DIGEST.fullmatch(value.base_payload_digest) is not None
            and isinstance(value.artifact_policy_digest, str)
            and _DIGEST.fullmatch(value.artifact_policy_digest) is not None
            and value.artifact_policy_digest
            == derive_python_artifact_policy(
                _bundle_contract.load_trusted_bundle_policy()
            ).policy_digest
            and isinstance(value.command, tuple)
            and all(isinstance(item, str) for item in value.command)
            and _is_offline_evidence(value.offline_evidence)
            and hmac.compare_digest(
                value._authority_digest_value,
                _authority_digest("python-build-result", _python_result_domain(value)),
            )
        )
    except (AttributeError, ForgeError, TypeError, ValueError):
        return False


@dataclass(frozen=True, slots=True)
class _ZipPlan:
    info: zipfile.ZipInfo
    path: str
    mode: int
    directory: bool


def _validate_zip_framing(raw: bytes) -> tuple[int, int, int]:
    if not raw.startswith(b"PK\x03\x04"):
        raise _unsafe_member()
    minimum = max(0, len(raw) - (22 + 0xFFFF))
    offset = len(raw)
    central_offset: int | None = None
    eocd_offset: int | None = None
    entry_count = 0
    while True:
        offset = raw.rfind(b"PK\x05\x06", minimum, offset)
        if offset < 0:
            break
        if offset + 22 <= len(raw):
            comment_size = struct.unpack_from("<H", raw, offset + 20)[0]
            if offset + 22 + comment_size == len(raw):
                (
                    _signature,
                    disk_number,
                    central_disk,
                    disk_entries,
                    total_entries,
                    central_size,
                    selected_offset,
                    _comment_size,
                ) = struct.unpack_from("<4s4H2LH", raw, offset)
                if (
                    disk_number != 0
                    or central_disk != 0
                    or disk_entries != total_entries
                    or total_entries == 0xFFFF
                    or central_size == 0xFFFFFFFF
                    or selected_offset == 0xFFFFFFFF
                    or selected_offset + central_size != offset
                ):
                    raise _unsafe_member()
                central_offset = selected_offset
                eocd_offset = offset
                entry_count = total_entries
                break
        if offset == 0:
            break
    if central_offset is None or eocd_offset is None:
        raise _unsafe_member()
    return central_offset, eocd_offset, entry_count


def _validate_zip_member_framing(
    raw: bytes,
    archive: zipfile.ZipFile,
    central_offset: int,
    eocd_offset: int,
    entry_count: int,
) -> None:
    central_infos = archive.infolist()
    infos = sorted(central_infos, key=lambda info: info.header_offset)
    if (
        not infos
        or len(infos) != entry_count
        or infos[0].header_offset != 0
        or archive.start_dir != central_offset
    ):
        raise _unsafe_member()
    expected_offset = 0
    for info in infos:
        if info.orig_filename != info.filename or info.header_offset != expected_offset:
            raise _unsafe_member()
        if (info.is_dir() or info.filename.endswith("/")) and info.CRC != 0:
            raise _unsafe_member()
        if info.header_offset + 30 > len(raw):
            raise _unsafe_member()
        (
            signature,
            _extract_version,
            flags,
            compression,
            _time,
            _date,
            crc,
            compressed_size,
            file_size,
            filename_size,
            extra_size,
        ) = struct.unpack_from("<4s5H3L2H", raw, info.header_offset)
        if signature != b"PK\x03\x04" or flags != info.flag_bits:
            raise _unsafe_member()
        encoding = "utf-8" if flags & 0x0800 else "cp437"
        name_start = info.header_offset + 30
        name_end = name_start + filename_size
        data_start = name_end + extra_size
        try:
            local_name = raw[name_start:name_end].decode(encoding)
        except UnicodeDecodeError as exc:
            raise _unsafe_member() from exc
        if (
            local_name != info.orig_filename
            or compression != info.compress_type
            or data_start + info.compress_size > central_offset
        ):
            raise _unsafe_member()
        if flags & 0x0008:
            if crc != 0 or compressed_size != 0 or file_size != 0:
                raise _unsafe_member()
        elif (
            crc != info.CRC
            or compressed_size != info.compress_size
            or file_size != info.file_size
        ):
            raise _unsafe_member()
        expected_offset = data_start + info.compress_size
        if flags & 0x0008:
            descriptor_offset = expected_offset
            if raw[descriptor_offset : descriptor_offset + 4] == b"PK\x07\x08":
                descriptor_offset += 4
            if descriptor_offset + 12 > central_offset:
                raise _unsafe_member()
            descriptor = struct.unpack_from("<3L", raw, descriptor_offset)
            if descriptor != (info.CRC, info.compress_size, info.file_size):
                raise _unsafe_member()
            expected_offset = descriptor_offset + 12
        if expected_offset > central_offset:
            raise _unsafe_member()
    if expected_offset != central_offset:
        raise _unsafe_member()
    expected_offset = central_offset
    for info in central_infos:
        if expected_offset + 46 > eocd_offset:
            raise _unsafe_member()
        (
            signature,
            _create_version,
            _extract_version,
            flags,
            compression,
            _time,
            _date,
            crc,
            compressed_size,
            file_size,
            filename_size,
            extra_size,
            comment_size,
            disk_number,
            internal_attr,
            external_attr,
            local_offset,
        ) = struct.unpack_from("<4s6H3L5H2L", raw, expected_offset)
        del internal_attr
        name_start = expected_offset + 46
        name_end = name_start + filename_size
        record_end = name_end + extra_size + comment_size
        if signature != b"PK\x01\x02" or record_end > eocd_offset:
            raise _unsafe_member()
        encoding = "utf-8" if flags & 0x0800 else "cp437"
        try:
            central_name = raw[name_start:name_end].decode(encoding)
        except UnicodeDecodeError as exc:
            raise _unsafe_member() from exc
        if (
            info.orig_filename != info.filename
            or central_name != info.orig_filename
            or flags != info.flag_bits
            or compression != info.compress_type
            or crc != info.CRC
            or compressed_size != info.compress_size
            or file_size != info.file_size
            or disk_number != 0
            or external_attr != info.external_attr
            or local_offset != info.header_offset
        ):
            raise _unsafe_member()
        expected_offset = record_end
    if expected_offset != eocd_offset:
        raise _unsafe_member()


def _validate_tar_framing(raw: bytes) -> int:
    maximum = LIMIT_POLICY.value("archive_expanded_bytes")
    ratio_maximum = len(raw) * LIMIT_POLICY.value("archive_ratio")
    member_maximum = LIMIT_POLICY.value("bundle_member_bytes")
    member_count_maximum = LIMIT_POLICY.value("bundle_files")
    header = bytearray()
    member_collisions: set[str] = set()
    data_remaining = 0
    padding_remaining = 0
    zero_blocks = 0
    end_seen = False
    member_count = 0

    def consume_expanded(chunk: bytes) -> None:
        nonlocal data_remaining
        nonlocal end_seen
        nonlocal member_count
        nonlocal padding_remaining
        nonlocal zero_blocks
        offset = 0
        view = memoryview(chunk)
        while offset < len(view):
            if end_seen:
                if any(view[offset:]):
                    raise _unsafe_member()
                return
            if data_remaining:
                consumed = min(data_remaining, len(view) - offset)
                data_remaining -= consumed
                offset += consumed
                continue
            if padding_remaining:
                consumed = min(padding_remaining, len(view) - offset)
                if any(view[offset : offset + consumed]):
                    raise _unsafe_member()
                padding_remaining -= consumed
                offset += consumed
                continue
            consumed = min(tarfile.BLOCKSIZE - len(header), len(view) - offset)
            header.extend(view[offset : offset + consumed])
            offset += consumed
            if len(header) != tarfile.BLOCKSIZE:
                continue
            block = bytes(header)
            header.clear()
            if block == tarfile.NUL * tarfile.BLOCKSIZE:
                zero_blocks += 1
                if zero_blocks == 2:
                    end_seen = True
                continue
            if zero_blocks:
                raise _unsafe_member()
            try:
                item = tarfile.TarInfo.frombuf(block, "utf-8", "surrogateescape")
            except (tarfile.TarError, ValueError) as exc:
                raise _unsafe_member() from exc
            member_count += 1
            if (
                member_count > member_count_maximum
                or item.size < 0
                or item.size > member_maximum
            ):
                raise _limit_error()
            if item.type not in {
                tarfile.REGTYPE,
                tarfile.AREGTYPE,
                tarfile.DIRTYPE,
                tarfile.XHDTYPE,
                tarfile.XGLTYPE,
            }:
                raise _unsafe_member()
            if item.type in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}:
                full_path = item.name[:-1] if item.name.endswith("/") else item.name
                _portable_path(full_path)
                parts = full_path.split("/")
                if len(parts) > 1:
                    relative = "/".join(parts[1:])
                    _portable_path(relative)
                    collision = _collision_key(relative)
                    if collision in member_collisions:
                        raise _unsafe_member()
                    member_collisions.add(collision)
                if (
                    (item.type == tarfile.DIRTYPE and item.size != 0)
                    or item.linkname
                    or item.devmajor
                    or item.devminor
                ):
                    raise _unsafe_member()
            data_remaining = item.size
            padding_remaining = (-item.size) % tarfile.BLOCKSIZE

    try:
        decompressor = zlib.decompressobj(wbits=31)
        compressed_offset = 0
        compressed = b""
        expanded_size = 0
        while compressed or compressed_offset < len(raw):
            if not compressed:
                compressed = raw[compressed_offset : compressed_offset + _CHUNK_BYTES]
                compressed_offset += len(compressed)
            allowance = min(maximum, ratio_maximum) - expanded_size
            output = decompressor.decompress(
                compressed, min(_CHUNK_BYTES, max(1, allowance + 1))
            )
            compressed = decompressor.unconsumed_tail
            if output:
                consume_expanded(output)
                expanded_size += len(output)
                if expanded_size > maximum or expanded_size > ratio_maximum:
                    raise _limit_error()
            if decompressor.eof:
                if (
                    decompressor.unused_data
                    or compressed
                    or compressed_offset != len(raw)
                ):
                    raise _unsafe_member()
                break
            if not output and not compressed and compressed_offset == len(raw):
                break
    except zlib.error as exc:
        raise _unsafe_member() from exc
    if (
        not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
        or expanded_size % tarfile.BLOCKSIZE
        or header
        or data_remaining
        or padding_remaining
        or not end_seen
    ):
        raise _unsafe_member()
    return expanded_size


def _default_archive_limits() -> ArchiveLimits:
    return ArchiveLimits(
        max_members=LIMIT_POLICY.value("bundle_files"),
        max_compressed_bytes=LIMIT_POLICY.value("archive_compressed_bytes"),
        max_expanded_bytes=LIMIT_POLICY.value("archive_expanded_bytes"),
        max_ratio=LIMIT_POLICY.value("archive_ratio"),
        max_member_bytes=LIMIT_POLICY.value("bundle_member_bytes"),
    )


def _ordered_controlled_entries(
    entries: Sequence[_ControlledArchiveEntry],
) -> tuple[_ControlledArchiveEntry, ...]:
    if isinstance(entries, (str, bytes, bytearray)):
        raise _unsafe_member()
    selected = tuple(entries)
    if (
        not selected
        or len(selected) > LIMIT_POLICY.value("bundle_files")
        or any(type(entry) is not _ControlledArchiveEntry for entry in selected)
    ):
        raise _unsafe_member()
    ordered = tuple(sorted(selected, key=lambda entry: entry.path.encode("utf-8")))
    keys = tuple(_collision_key(entry.path) for entry in ordered)
    if len(set(entry.path for entry in ordered)) != len(ordered) or len(
        set(keys)
    ) != len(keys):
        raise _unsafe_member()
    if sum(len(entry.data) for entry in ordered) > LIMIT_POLICY.value(
        "bundle_total_bytes"
    ):
        raise _limit_error()
    return ordered


def _bundle_archive_entries(
    bundle: CanonicalBundle, policy: BundlePolicy
) -> tuple[_ControlledArchiveEntry, ...]:
    if not _bundle_contract._is_canonical(bundle) or not _bundle_contract._is_policy(
        policy
    ):
        raise _artifact_error(
            "bundle.policy_invalid", "Plugin archive authority is not trusted."
        )
    manifest = bundle.manifest
    paths = tuple(entry.path for entry in manifest.entries)
    if (
        paths != policy.required_files
        or set(bundle.entry_bytes) != set(policy.required_files)
        or manifest.schema_version != "1.0"
        or manifest.policy_digest != policy.policy_digest
        or manifest.normalization_profile != "bundle-v1"
        or manifest.builder_version != VERSION
        or manifest.aggregate_size != sum(entry.size for entry in manifest.entries)
    ):
        raise _artifact_error(
            "bundle.digest_mismatch", "Canonical bundle authority is inconsistent."
        )
    domain = {
        "aggregate_size": manifest.aggregate_size,
        "base_version": manifest.base_version,
        "entries": [_member_record(entry) for entry in manifest.entries],
        "normalization_profile": manifest.normalization_profile,
        "policy_digest": manifest.policy_digest,
        "schema_version": manifest.schema_version,
    }
    if (
        hashlib.sha256(canonical_bundle_json_bytes(domain)).hexdigest()
        != manifest.payload_digest
        or bundle.manifest_bytes
        != canonical_bundle_json_bytes(manifest, final_newline=True)
    ):
        raise _artifact_error(
            "bundle.digest_mismatch", "Canonical bundle digest is inconsistent."
        )
    entries: list[_ControlledArchiveEntry] = []
    for entry in manifest.entries:
        raw = bundle.entry_bytes[entry.path]
        expected_mode = 0o755 if entry.path in policy.executable_files else 0o644
        if (
            entry.file_type != "regular"
            or entry.mode != expected_mode
            or entry.size != len(raw)
            or entry.sha256 != hashlib.sha256(raw).hexdigest()
        ):
            raise _artifact_error(
                "bundle.digest_mismatch", "Canonical bundle member is inconsistent."
            )
        entries.append(
            _ControlledArchiveEntry(path=entry.path, data=raw, mode=entry.mode)
        )
    entries.append(
        _ControlledArchiveEntry(
            path=_GENERATED_MANIFEST_PATH,
            data=bundle.manifest_bytes,
            mode=0o644,
        )
    )
    return _ordered_controlled_entries(entries)


def write_controlled_plugin_zip(bundle: CanonicalBundle, policy: BundlePolicy) -> bytes:
    """Write one byte-stable plugin ZIP with no ambient metadata."""

    ordered = _bundle_archive_entries(bundle, policy)
    output = io.BytesIO()
    try:
        with zipfile.ZipFile(
            output,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=False,
        ) as archive:
            archive.comment = b""
            for entry in ordered:
                info = zipfile.ZipInfo(entry.path, date_time=_FIXED_ZIP_TIME)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | entry.mode) << 16
                info.internal_attr = 0
                info.extra = b""
                info.comment = b""
                archive.writestr(
                    info,
                    entry.data,
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise _artifact_error(
            "artifact.write_failed", "Controlled plugin archive could not be written."
        ) from exc
    raw = output.getvalue()
    if len(raw) > LIMIT_POLICY.value("archive_compressed_bytes"):
        raise _limit_error()
    return raw


def _zip_plan(
    archive: zipfile.ZipFile,
    *,
    raw_size: int,
    limits: ArchiveLimits,
    allow_directories: bool,
    controlled_profile: bool,
) -> tuple[_ZipPlan, ...]:
    infos = archive.infolist()
    if raw_size > limits.max_compressed_bytes or len(infos) > limits.max_members:
        raise _limit_error()
    if controlled_profile and archive.comment:
        raise _unsafe_member()

    plans: list[_ZipPlan] = []
    names: set[str] = set()
    collision_keys: set[str] = set()
    expanded = 0
    compressed = 0
    for info in infos:
        directory = info.is_dir() or info.filename.endswith("/")
        path = info.filename[:-1] if directory else info.filename
        if not path:
            raise _unsafe_member()
        _portable_path(path)
        collision = _collision_key(path)
        if path in names or collision in collision_keys:
            raise _unsafe_member()
        names.add(path)
        collision_keys.add(collision)

        raw_mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(raw_mode)
        if raw_mode & 0o7000:
            raise _unsafe_member()
        if directory:
            if (
                not allow_directories
                or file_type not in {0, stat.S_IFDIR}
                or info.file_size != 0
                or info.compress_size != 0
            ):
                raise _unsafe_member()
            permissions = raw_mode & 0o777
            mode = permissions
        else:
            if file_type not in {0, stat.S_IFREG}:
                raise _unsafe_member()
            permissions = raw_mode & 0o777
            mode = 0o755 if permissions & 0o111 else 0o644
            if controlled_profile and permissions not in {0o644, 0o755}:
                raise _unsafe_member()

        if info.flag_bits & ~(0x0008 | 0x0800) or info.compress_type not in {
            zipfile.ZIP_STORED,
            zipfile.ZIP_DEFLATED,
        }:
            raise _unsafe_member()
        if controlled_profile and info.compress_type != zipfile.ZIP_DEFLATED:
            raise _unsafe_member()
        if controlled_profile and (
            info.date_time != _FIXED_ZIP_TIME
            or info.create_system != 3
            or info.extra
            or info.comment
        ):
            raise _unsafe_member()
        if info.file_size < 0 or info.compress_size < 0:
            raise _unsafe_member()
        if info.file_size > min(
            limits.max_member_bytes, LIMIT_POLICY.value("bundle_member_bytes")
        ):
            raise _limit_error()
        expanded += info.file_size
        compressed += info.compress_size
        if (
            expanded > limits.max_expanded_bytes
            or compressed > limits.max_compressed_bytes
        ):
            raise _limit_error()
        plans.append(_ZipPlan(info=info, path=path, mode=mode, directory=directory))

    if expanded and (compressed == 0 or expanded > compressed * limits.max_ratio):
        raise _limit_error()
    if controlled_profile and [plan.path for plan in plans] != sorted(
        (plan.path for plan in plans), key=lambda path: path.encode("utf-8")
    ):
        raise _unsafe_member()
    return tuple(plans)


def _stream_zip_members(
    archive: zipfile.ZipFile,
    plans: tuple[_ZipPlan, ...],
    *,
    limits: ArchiveLimits,
    captures: frozenset[str],
) -> tuple[tuple[BundleEntry, ...], dict[str, bytes]]:
    members: list[BundleEntry] = []
    captured: dict[str, bytes] = {}
    expanded = 0
    for plan in plans:
        if plan.directory:
            continue
        digest = hashlib.sha256()
        size = 0
        saved = bytearray() if plan.path in captures else None
        try:
            with archive.open(plan.info, mode="r") as stream:
                while True:
                    chunk = stream.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    expanded += len(chunk)
                    if (
                        expanded > limits.max_expanded_bytes
                        or size > plan.info.file_size
                        or size
                        > min(
                            limits.max_member_bytes,
                            LIMIT_POLICY.value("bundle_member_bytes"),
                        )
                    ):
                        raise _limit_error()
                    digest.update(chunk)
                    if saved is not None:
                        saved.extend(chunk)
        except ForgeError:
            raise
        except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile) as exc:
            raise _unsafe_member() from exc
        if size != plan.info.file_size:
            raise _unsafe_member()
        member = BundleEntry(
            path=plan.path,
            file_type="regular",
            mode=plan.mode,
            size=size,
            sha256=digest.hexdigest(),
        )
        members.append(member)
        if saved is not None:
            captured[plan.path] = bytes(saved)
    return (
        tuple(sorted(members, key=lambda member: member.path.encode("utf-8"))),
        captured,
    )


def _verify_expected_plugin_members(
    plans: tuple[_ZipPlan, ...], expected: tuple[_ControlledArchiveEntry, ...]
) -> tuple[_ControlledArchiveEntry, ...]:
    selected = _ordered_controlled_entries(expected)
    regular = tuple(plan for plan in plans if not plan.directory)
    if tuple(plan.path for plan in regular) != tuple(entry.path for entry in selected):
        raise _artifact_error(
            "artifact.manifest_mismatch",
            "Plugin archive does not match the expected projection.",
        )
    for plan, entry in zip(regular, selected, strict=True):
        if plan.mode != entry.mode or plan.info.file_size != len(entry.data):
            if plan.path == _GENERATED_MANIFEST_PATH:
                raise _artifact_error(
                    "bundle.digest_mismatch",
                    "Plugin archive manifest does not match its authority.",
                )
            raise _artifact_error(
                "artifact.manifest_mismatch",
                "Plugin archive metadata does not match the expected projection.",
            )
    return selected


def _verify_plugin_manifest(
    members: tuple[BundleEntry, ...],
    captured: Mapping[str, bytes],
    expected: CanonicalBundle,
    policy: BundlePolicy,
) -> str:
    try:
        manifest_raw = captured[_GENERATED_MANIFEST_PATH]
        plugin_raw = captured[_PLUGIN_MANIFEST_PATH]
    except KeyError as exc:
        raise _artifact_error(
            "bundle.digest_mismatch", "Plugin archive manifest is missing."
        ) from exc
    try:
        manifest = _decode_json(manifest_raw)
        plugin = _decode_json(plugin_raw)
    except ForgeError as exc:
        raise _artifact_error(
            "bundle.digest_mismatch", "Plugin archive manifest is invalid."
        ) from exc
    if not isinstance(manifest, dict) or not isinstance(plugin, dict):
        raise _artifact_error(
            "bundle.digest_mismatch", "Plugin archive manifest is invalid."
        )
    required_keys = {
        "aggregate_size",
        "base_version",
        "builder_version",
        "entries",
        "normalization_profile",
        "payload_digest",
        "policy_digest",
        "schema_version",
    }
    if (
        set(manifest) != required_keys
        or manifest_raw != canonical_bundle_json_bytes(manifest, final_newline=True)
        or manifest_raw != expected.manifest_bytes
    ):
        raise _artifact_error(
            "bundle.digest_mismatch", "Plugin archive manifest is noncanonical."
        )
    payload_members = tuple(
        member for member in members if member.path != _GENERATED_MANIFEST_PATH
    )
    expected_entries = [_member_record(member) for member in expected.manifest.entries]
    if (
        tuple(payload_members) != expected.manifest.entries
        or manifest.get("entries") != expected_entries
        or manifest.get("aggregate_size") != expected.manifest.aggregate_size
        or manifest.get("schema_version") != expected.manifest.schema_version
        or manifest.get("policy_digest") != policy.policy_digest
        or manifest.get("normalization_profile")
        != expected.manifest.normalization_profile
        or manifest.get("builder_version") != expected.manifest.builder_version
        or manifest.get("base_version") != expected.manifest.base_version
    ):
        raise _artifact_error(
            "bundle.digest_mismatch", "Plugin archive payload manifest does not match."
        )
    domain = {
        key: manifest[key]
        for key in (
            "aggregate_size",
            "base_version",
            "entries",
            "normalization_profile",
            "policy_digest",
            "schema_version",
        )
    }
    if (
        not isinstance(manifest.get("payload_digest"), str)
        or not _DIGEST.fullmatch(manifest["payload_digest"])
        or hashlib.sha256(canonical_bundle_json_bytes(domain)).hexdigest()
        != manifest["payload_digest"]
        or manifest["payload_digest"] != expected.manifest.payload_digest
    ):
        raise _artifact_error(
            "bundle.digest_mismatch", "Plugin archive payload digest does not match."
        )
    version = manifest.get("base_version")
    if not isinstance(version, str):
        raise _artifact_error(
            "bundle.digest_mismatch", "Plugin archive version is invalid."
        )
    try:
        parse_release_version(version)
    except ValueError as exc:
        raise _artifact_error(
            "bundle.digest_mismatch", "Plugin archive version is invalid."
        ) from exc
    if (
        plugin_raw != expected.entry_bytes[_PLUGIN_MANIFEST_PATH]
        or plugin.get("version") != version
    ):
        raise _artifact_error(
            "bundle.digest_mismatch", "Plugin and bundle versions do not match."
        )
    return version


def inspect_plugin_zip(
    raw: bytes,
    *,
    expected: CanonicalBundle,
    policy: BundlePolicy,
    limits: ArchiveLimits | None = None,
) -> ArtifactEvidence:
    """Inspect a plugin ZIP without extracting or executing any member."""

    if not isinstance(raw, bytes):
        raise TypeError("raw")
    selected_limits = _default_archive_limits() if limits is None else limits
    if type(selected_limits) is not ArchiveLimits:
        raise TypeError("limits")
    expected_entries = _bundle_archive_entries(expected, policy)
    if len(raw) > selected_limits.max_compressed_bytes:
        raise _limit_error()
    central_offset, eocd_offset, entry_count = _validate_zip_framing(raw)
    if entry_count > selected_limits.max_members:
        raise _limit_error()
    try:
        with zipfile.ZipFile(io.BytesIO(raw), mode="r") as archive:
            _validate_zip_member_framing(
                raw, archive, central_offset, eocd_offset, entry_count
            )
            plans = _zip_plan(
                archive,
                raw_size=len(raw),
                limits=selected_limits,
                allow_directories=False,
                controlled_profile=True,
            )
            selected_expected = _verify_expected_plugin_members(plans, expected_entries)
            members, captured = _stream_zip_members(
                archive,
                plans,
                limits=selected_limits,
                captures=frozenset({_GENERATED_MANIFEST_PATH, _PLUGIN_MANIFEST_PATH}),
            )
    except ForgeError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise _unsafe_member() from exc
    expected_by_path = {entry.path: entry for entry in selected_expected}
    for member in members:
        entry = expected_by_path[member.path]
        if member.sha256 != hashlib.sha256(entry.data).hexdigest():
            if member.path == _GENERATED_MANIFEST_PATH:
                raise _artifact_error(
                    "bundle.digest_mismatch",
                    "Plugin archive manifest does not match its authority.",
                )
            raise _artifact_error(
                "artifact.manifest_mismatch",
                "Plugin archive bytes do not match the expected projection.",
            )
    version = _verify_plugin_manifest(members, captured, expected, policy)
    if raw != write_controlled_plugin_zip(expected, policy):
        raise _artifact_error(
            "artifact.manifest_mismatch",
            "Plugin archive container is not the controlled projection.",
        )
    return ArtifactEvidence(
        artifact_kind="plugin-zip",
        distribution="zagrosi-forge",
        version=version,
        members=members,
        directories=(),
        root_directory_mode=None,
        normalized_manifest_digest=expected.manifest.payload_digest,
        policy_digest=policy.policy_digest,
        manifest_path=_GENERATED_MANIFEST_PATH,
        _token=_ARTIFACT_TOKEN,
    )


def _metadata_identity(raw: bytes) -> tuple[str, str]:
    if len(raw) > _MAX_METADATA_BYTES:
        raise _unsafe_member()
    message = BytesParser(policy=compat32).parsebytes(raw)
    names = message.get_all("Name", [])
    versions = message.get_all("Version", [])
    if len(names) != 1 or len(versions) != 1:
        raise _artifact_error(
            "artifact.metadata_invalid", "Python artifact metadata is incomplete."
        )
    name = names[0]
    version = versions[0]
    if not isinstance(name, str) or not isinstance(version, str):
        raise _artifact_error(
            "artifact.metadata_invalid", "Python artifact metadata is invalid."
        )
    return _normalized_distribution(name), version


def _inspect_wheel(
    raw: bytes,
) -> tuple[
    tuple[BundleEntry, ...],
    Mapping[str, bytes],
    tuple[ArtifactDirectory, ...],
]:
    limits = _default_archive_limits()
    if len(raw) > limits.max_compressed_bytes:
        raise _limit_error()
    central_offset, eocd_offset, entry_count = _validate_zip_framing(raw)
    if entry_count > limits.max_members:
        raise _limit_error()
    try:
        with zipfile.ZipFile(io.BytesIO(raw), mode="r") as archive:
            _validate_zip_member_framing(
                raw, archive, central_offset, eocd_offset, entry_count
            )
            plans = _zip_plan(
                archive,
                raw_size=len(raw),
                limits=limits,
                allow_directories=True,
                controlled_profile=False,
            )
            capture_suffixes = (
                ".dist-info/METADATA",
                ".dist-info/WHEEL",
                ".dist-info/entry_points.txt",
                ".dist-info/RECORD",
            )
            capture_paths = frozenset(
                plan.path
                for plan in plans
                if not plan.directory
                and any(plan.path.endswith(suffix) for suffix in capture_suffixes)
            )
            if any(
                sum(path.endswith(suffix) for path in capture_paths) != 1
                for suffix in capture_suffixes
            ):
                raise _artifact_error(
                    "artifact.metadata_invalid",
                    "Wheel metadata records are incomplete.",
                )
            members, captured = _stream_zip_members(
                archive,
                plans,
                limits=limits,
                captures=capture_paths,
            )
            directories = tuple(
                sorted(
                    (
                        ArtifactDirectory(path=plan.path, mode=plan.mode)
                        for plan in plans
                        if plan.directory
                    ),
                    key=lambda directory: directory.path.encode("utf-8"),
                )
            )
    except ForgeError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise _unsafe_member() from exc
    return members, captured, directories


def _inspect_sdist(
    raw: bytes,
) -> tuple[
    tuple[BundleEntry, ...],
    Mapping[str, bytes],
    str,
    int,
    tuple[ArtifactDirectory, ...],
]:
    limits = _default_archive_limits()
    if len(raw) > limits.max_compressed_bytes:
        raise _limit_error()
    expanded_size = _validate_tar_framing(raw)
    members: list[BundleEntry] = []
    collision_keys: set[str] = set()
    roots: set[str] = set()
    declared = 0
    streamed = 0
    member_count = 0
    captured: dict[str, bytes] = {}
    directories: list[ArtifactDirectory] = []
    root_directories: list[tuple[str, int]] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r|gz") as archive:
            for item in archive:
                member_count += 1
                if member_count > limits.max_members:
                    raise _limit_error()
                full_path = item.name[:-1] if item.name.endswith("/") else item.name
                _portable_path(full_path)
                parts = full_path.split("/")
                roots.add(parts[0])
                if item.type not in {
                    tarfile.REGTYPE,
                    tarfile.AREGTYPE,
                    tarfile.DIRTYPE,
                }:
                    raise _unsafe_member()
                if (
                    (item.isdir() and item.size != 0)
                    or item.linkname
                    or item.devmajor
                    or item.devminor
                ):
                    raise _unsafe_member()
                if len(parts) == 1:
                    if not item.isdir():
                        raise _unsafe_member()
                    root_directories.append((full_path, item.mode & 0o777))
                    continue
                path = "/".join(parts[1:])
                _portable_path(path)
                collision = _collision_key(path)
                if collision in collision_keys:
                    raise _unsafe_member()
                collision_keys.add(collision)
                if item.size < 0 or item.size > LIMIT_POLICY.value(
                    "bundle_member_bytes"
                ):
                    raise _limit_error()
                declared += item.size
                if declared > limits.max_expanded_bytes:
                    raise _limit_error()
                if item.isdir():
                    directories.append(
                        ArtifactDirectory(path=path, mode=item.mode & 0o777)
                    )
                    continue
                source = archive.extractfile(item)
                if source is None:
                    raise _unsafe_member()
                digest = hashlib.sha256()
                saved = bytearray() if path in {"PKG-INFO", "pyproject.toml"} else None
                size = 0
                while True:
                    chunk = source.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    streamed += len(chunk)
                    if size > item.size or streamed > limits.max_expanded_bytes:
                        raise _limit_error()
                    digest.update(chunk)
                    if saved is not None:
                        saved.extend(chunk)
                if size != item.size:
                    raise _unsafe_member()
                mode = 0o755 if item.mode & 0o111 else 0o644
                members.append(
                    BundleEntry(
                        path=path,
                        file_type="regular",
                        mode=mode,
                        size=size,
                        sha256=digest.hexdigest(),
                    )
                )
                if saved is not None:
                    captured[path] = bytes(saved)
    except ForgeError:
        raise
    except (EOFError, OSError, tarfile.TarError, ValueError) as exc:
        raise _unsafe_member() from exc
    if len(roots) != 1:
        raise _unsafe_member()
    if expanded_size and expanded_size > len(raw) * limits.max_ratio:
        raise _limit_error()
    if declared and (not raw or declared > len(raw) * limits.max_ratio):
        raise _limit_error()
    if (
        set(captured) != {"PKG-INFO", "pyproject.toml"}
        or streamed != declared
        or len(root_directories) != 1
        or root_directories[0][0] != next(iter(roots))
    ):
        raise _artifact_error(
            "artifact.metadata_invalid", "Source distribution metadata is missing."
        )
    return (
        tuple(sorted(members, key=lambda member: member.path.encode("utf-8"))),
        captured,
        next(iter(roots)),
        root_directories[0][1],
        tuple(
            sorted(directories, key=lambda directory: directory.path.encode("utf-8"))
        ),
    )


def _python_package_paths(policy: BundlePolicy, module_root: str) -> tuple[str, ...]:
    prefix = f"src/{module_root}/"
    selected = tuple(path for path in policy.required_files if path.startswith(prefix))
    if not selected:
        raise _artifact_error(
            "bundle.policy_invalid", "Bundle policy has no Python package members."
        )
    return selected


def _wheel_metadata_paths(module_root: str, version: str) -> frozenset[str]:
    dist_info = f"{module_root}-{version}.dist-info"
    return frozenset(
        {
            f"{dist_info}/METADATA",
            f"{dist_info}/WHEEL",
            f"{dist_info}/entry_points.txt",
            f"{dist_info}/RECORD",
            f"{dist_info}/licenses/LICENSE",
            f"{dist_info}/licenses/NOTICE.md",
        }
    )


def _directory_ancestors(paths: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                "/".join(parts[:index])
                for path in paths
                for parts in (path.split("/"),)
                for index in range(1, len(parts))
            },
            key=str.encode,
        )
    )


def _derived_python_policy_values(
    policy: BundlePolicy,
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    package_source_paths = _python_package_paths(policy, "zagrosi_forge")
    wheel_metadata_paths = tuple(
        sorted(_wheel_metadata_paths("zagrosi_forge", VERSION), key=str.encode)
    )
    domain = {
        "backend_distribution": _BACKEND_DISTRIBUTION,
        "backend_version": _BACKEND_VERSION,
        "build_backend": "uv_build",
        "bundle_policy_digest": policy.policy_digest,
        "container_metadata_exclusions": _PYTHON_CONTAINER_METADATA_EXCLUSIONS,
        "distribution": "zagrosi-forge",
        "entry_points_sha256": hashlib.sha256(_ENTRY_POINTS_BYTES).hexdigest(),
        "module_name": "zagrosi_forge",
        "module_root": "src",
        "normalization_profile": _PYTHON_NORMALIZATION_PROFILE,
        "package_source_paths": package_source_paths,
        "sdist_root_members": tuple(sorted(_SDIST_ROOT_MEMBERS)),
        "source_include": ("component-inventory.json",),
        "version": VERSION,
        "wheel_metadata_paths": wheel_metadata_paths,
        "wheel_sha256": hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    }
    policy_digest = hashlib.sha256(canonical_bundle_json_bytes(domain)).hexdigest()
    return package_source_paths, wheel_metadata_paths, policy_digest


def derive_python_artifact_policy(policy: BundlePolicy) -> PythonArtifactPolicy:
    """Derive the fixed Python artifact contract from installed bundle policy."""

    if not _bundle_contract._is_policy(policy):
        raise _artifact_error(
            "bundle.policy_invalid", "Python artifact policy authority is not trusted."
        )
    package_source_paths, wheel_metadata_paths, policy_digest = (
        _derived_python_policy_values(policy)
    )
    return PythonArtifactPolicy(
        policy_digest=policy_digest,
        bundle_policy=policy,
        package_source_paths=package_source_paths,
        wheel_metadata_paths=wheel_metadata_paths,
        _token=_PYTHON_POLICY_TOKEN,
    )


def _is_python_policy(value: object) -> bool:
    try:
        if (
            type(value) is not PythonArtifactPolicy
            or value._seal is not _PYTHON_POLICY_TOKEN
            or not _bundle_contract._is_policy(value._bundle_policy)
        ):
            return False
        package_paths, wheel_paths, policy_digest = _derived_python_policy_values(
            value._bundle_policy
        )
        expected = {
            "backend_distribution": _BACKEND_DISTRIBUTION,
            "backend_version": _BACKEND_VERSION,
            "build_backend": "uv_build",
            "bundle_policy_digest": value._bundle_policy.policy_digest,
            "container_metadata_exclusions": _PYTHON_CONTAINER_METADATA_EXCLUSIONS,
            "distribution": "zagrosi-forge",
            "entry_points_sha256": hashlib.sha256(_ENTRY_POINTS_BYTES).hexdigest(),
            "module_name": "zagrosi_forge",
            "module_root": "src",
            "normalization_profile": _PYTHON_NORMALIZATION_PROFILE,
            "package_source_paths": package_paths,
            "policy_digest": policy_digest,
            "sdist_root_members": tuple(sorted(_SDIST_ROOT_MEMBERS)),
            "source_include": ("component-inventory.json",),
            "version": VERSION,
            "wheel_metadata_paths": wheel_paths,
            "wheel_sha256": hashlib.sha256(_WHEEL_BYTES).hexdigest(),
        }
        return _python_policy_domain(value) == expected and hmac.compare_digest(
            value._authority_digest_value,
            _authority_digest("python-artifact-policy", expected),
        )
    except (AttributeError, ForgeError, TypeError, ValueError):
        return False


def _validate_pyproject_profile(raw: bytes, expected: PythonArtifactPolicy) -> None:
    if not _is_python_policy(expected):
        raise _artifact_error(
            "bundle.policy_invalid", "Python artifact policy is not trusted."
        )
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise _artifact_error(
            "artifact.metadata_invalid", "Python build metadata is invalid."
        ) from exc
    build_system = document.get("build-system")
    tool = document.get("tool")
    project = document.get("project")
    uv = tool.get("uv") if isinstance(tool, dict) else None
    build_profile = uv.get("build-backend") if isinstance(uv, dict) else None
    if (
        build_system
        != {
            "requires": [
                f"{expected.backend_distribution.replace('-', '_')}"
                f"=={expected.backend_version}"
            ],
            "build-backend": expected.build_backend,
        }
        or build_profile
        != {
            "module-name": expected.module_name,
            "module-root": expected.module_root,
            "source-include": list(expected.source_include),
        }
        or not isinstance(project, dict)
        or project.get("name") != expected.distribution
        or project.get("version") != expected.version
        or project.get("scripts") != {"zagrosi-forge": "zagrosi_forge.install:main"}
    ):
        raise _artifact_error(
            "artifact.metadata_invalid",
            "Python build metadata does not match the trusted profile.",
        )


def _python_source_entries(
    bundle: CanonicalBundle, expected: PythonArtifactPolicy
) -> tuple[_ControlledArchiveEntry, ...]:
    if not _is_python_policy(expected):
        raise _artifact_error(
            "bundle.policy_invalid", "Python artifact policy is not trusted."
        )
    _bundle_archive_entries(bundle, expected._bundle_policy)
    source_paths = frozenset(
        (
            *(path for path in expected.sdist_root_members if path != "PKG-INFO"),
            *expected.package_source_paths,
        )
    )
    manifest_by_path = {entry.path: entry for entry in bundle.manifest.entries}
    if not source_paths <= set(bundle.entry_bytes):
        raise _artifact_error(
            "artifact.manifest_mismatch", "Canonical Python source is incomplete."
        )
    selected: list[_ControlledArchiveEntry] = []
    for path in sorted(source_paths, key=str.encode):
        raw = bundle.entry_bytes[path]
        member = manifest_by_path[path]
        if (
            member.file_type != "regular"
            or member.mode != 0o644
            or member.size != len(raw)
            or member.sha256 != hashlib.sha256(raw).hexdigest()
        ):
            raise _artifact_error(
                "artifact.manifest_mismatch",
                "Canonical Python source member is invalid.",
            )
        selected.append(_ControlledArchiveEntry(path=path, data=raw, mode=0o644))
    _validate_pyproject_profile(bundle.entry_bytes["pyproject.toml"], expected)
    return tuple(selected)


def _write_private_python_source(
    root: Path, entries: tuple[_ControlledArchiveEntry, ...]
) -> None:
    root.mkdir(mode=0o700)
    for entry in entries:
        target = root.joinpath(*entry.path.split("/"))
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(entry.data)
        target.chmod(entry.mode)


def _verify_sdist_against_bundle(
    evidence: ArtifactEvidence,
    bundle: CanonicalBundle,
    expected: PythonArtifactPolicy,
) -> None:
    if not _is_artifact_evidence(evidence) or evidence.artifact_kind != "sdist":
        raise TypeError("sdist evidence")
    if not _is_python_policy(expected):
        raise TypeError("Python artifact policy")
    _bundle_archive_entries(bundle, expected._bundle_policy)
    source_paths = frozenset(
        (
            *(path for path in expected.sdist_root_members if path != "PKG-INFO"),
            *expected.package_source_paths,
        )
    )
    observed = {member.path: member for member in evidence.members}
    authority = {member.path: member for member in bundle.manifest.entries}
    for path in source_paths:
        if observed.get(path) != authority.get(path):
            raise _artifact_error(
                "artifact.reproducibility_mismatch",
                "Source distribution content does not match the canonical bundle.",
            )


def _captured_suffix(captured: Mapping[str, bytes], suffix: str) -> bytes:
    selected = tuple(raw for path, raw in captured.items() if path.endswith(suffix))
    if len(selected) != 1:
        raise _artifact_error(
            "artifact.metadata_invalid", "Wheel metadata records are incomplete."
        )
    return selected[0]


def _record_digest(member: BundleEntry) -> str:
    encoded = base64.urlsafe_b64encode(bytes.fromhex(member.sha256)).rstrip(b"=")
    return f"sha256={encoded.decode('ascii')}"


def _validate_wheel_record(
    raw: bytes, members: tuple[BundleEntry, ...], record_path: str
) -> None:
    try:
        rendered = raw.decode("utf-8")
        rows = tuple(csv.reader(io.StringIO(rendered, newline="")))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise _artifact_error(
            "artifact.metadata_invalid", "Wheel RECORD is invalid."
        ) from exc
    if any(len(row) != 3 for row in rows):
        raise _artifact_error("artifact.metadata_invalid", "Wheel RECORD is invalid.")
    by_path = {row[0]: (row[1], row[2]) for row in rows}
    member_by_path = {member.path: member for member in members}
    if len(by_path) != len(rows) or set(by_path) != set(member_by_path):
        raise _artifact_error("artifact.metadata_invalid", "Wheel RECORD is invalid.")
    for path, member in member_by_path.items():
        digest, size = by_path[path]
        if path == record_path:
            valid = digest == "" and size == ""
        else:
            valid = digest == _record_digest(member) and size == str(member.size)
        if not valid:
            raise _artifact_error(
                "artifact.metadata_invalid", "Wheel RECORD is invalid."
            )


def _validate_python_contract(
    *,
    artifact_kind: str,
    members: tuple[BundleEntry, ...],
    directories: tuple[ArtifactDirectory, ...],
    root_directory_mode: int | None,
    captured: Mapping[str, bytes],
    expected: PythonArtifactPolicy,
) -> None:
    if artifact_kind == "sdist":
        expected_paths = frozenset(
            (*expected.sdist_root_members, *expected.package_source_paths)
        )
    else:
        expected_paths = frozenset(
            (
                *(
                    path.removeprefix(f"{expected.module_root}/")
                    for path in expected.package_source_paths
                ),
                *expected.wheel_metadata_paths,
            )
        )
    if frozenset(member.path for member in members) != expected_paths or any(
        member.mode != 0o644 for member in members
    ):
        raise _artifact_error(
            "artifact.manifest_mismatch",
            "Python artifact members do not match the trusted policy.",
        )
    expected_directories = tuple(
        ArtifactDirectory(path=path, mode=0o755)
        for path in _directory_ancestors(tuple(expected_paths))
    )
    expected_root_mode = 0o755 if artifact_kind == "sdist" else None
    if directories != expected_directories or root_directory_mode != expected_root_mode:
        raise _artifact_error(
            "artifact.manifest_mismatch",
            "Python artifact directories do not match the trusted policy.",
        )
    if artifact_kind == "wheel":
        if (
            _captured_suffix(captured, ".dist-info/WHEEL") != expected.wheel_bytes
            or _captured_suffix(captured, ".dist-info/entry_points.txt")
            != expected.entry_points_bytes
        ):
            raise _artifact_error(
                "artifact.metadata_invalid", "Wheel metadata is not canonical."
            )
        record_path = next(
            path for path in expected_paths if path.endswith(".dist-info/RECORD")
        )
        _validate_wheel_record(captured[record_path], members, record_path)
    else:
        _validate_pyproject_profile(captured["pyproject.toml"], expected)


def inspect_python_artifact(
    raw: bytes,
    *,
    artifact_kind: str,
    expected: PythonArtifactPolicy,
) -> ArtifactEvidence:
    """Normalize a wheel or sdist without importing any packaged source."""

    if not isinstance(raw, bytes):
        raise TypeError("raw")
    if not _is_python_policy(expected):
        raise _artifact_error(
            "bundle.policy_invalid", "Python artifact policy is not trusted."
        )
    if artifact_kind == "wheel":
        members, captured, directories = _inspect_wheel(raw)
        metadata = _captured_suffix(captured, ".dist-info/METADATA")
        source_root = None
        root_directory_mode = None
    elif artifact_kind == "sdist":
        (
            members,
            captured,
            source_root,
            root_directory_mode,
            directories,
        ) = _inspect_sdist(raw)
        metadata = captured["PKG-INFO"]
    else:
        raise ValueError("artifact_kind")
    distribution, version = _metadata_identity(metadata)
    if (
        distribution != _normalized_distribution(expected.distribution)
        or version != expected.version
        or (
            source_root is not None
            and source_root != f"{distribution.replace('-', '_')}-{version}"
        )
    ):
        raise _artifact_error(
            "artifact.metadata_mismatch",
            "Python artifact identity does not match project metadata.",
        )
    _validate_python_contract(
        artifact_kind=artifact_kind,
        members=members,
        directories=directories,
        root_directory_mode=root_directory_mode,
        captured=captured,
        expected=expected,
    )
    return ArtifactEvidence(
        artifact_kind=artifact_kind,
        distribution=distribution,
        version=version,
        members=members,
        directories=directories,
        root_directory_mode=root_directory_mode,
        normalized_manifest_digest=_manifest_digest(
            artifact_kind,
            distribution,
            version,
            members,
            directories,
            root_directory_mode,
            expected.policy_digest,
        ),
        policy_digest=expected.policy_digest,
        _token=_ARTIFACT_TOKEN,
    )


def _comparison_differences(
    left: ArtifactEvidence, right: ArtifactEvidence
) -> tuple[str, ...]:
    differences: list[str] = []
    if left.artifact_kind != right.artifact_kind:
        differences.append("artifact_kind")
    if left.distribution != right.distribution:
        differences.append("distribution")
    if left.version != right.version:
        differences.append("version")
    if left.members != right.members:
        differences.append("members")
    if left.directories != right.directories:
        differences.append("directories")
    if left.root_directory_mode != right.root_directory_mode:
        differences.append("root_directory_mode")
    if left.policy_digest != right.policy_digest:
        differences.append("policy_digest")
    return tuple(differences)


def _wheel_from_sdist_differences(
    source: ArtifactEvidence, wheel: ArtifactEvidence
) -> tuple[str, ...]:
    differences: list[str] = []
    if source.artifact_kind != "sdist" or wheel.artifact_kind != "wheel":
        differences.append("artifact_kind")
    if source.distribution != wheel.distribution:
        differences.append("distribution")
    if source.version != wheel.version:
        differences.append("version")
    if source.policy_digest != wheel.policy_digest:
        differences.append("policy_digest")
    module_root = source.distribution.replace("-", "_")
    source_prefix = f"src/{module_root}/"
    source_members = tuple(
        BundleEntry(
            path=member.path.removeprefix("src/"),
            file_type=member.file_type,
            mode=member.mode,
            size=member.size,
            sha256=member.sha256,
        )
        for member in source.members
        if member.path.startswith(source_prefix)
    )
    wheel_members = tuple(
        member
        for member in wheel.members
        if member.path == module_root or member.path.startswith(f"{module_root}/")
    )
    if not source_members or source_members != wheel_members:
        differences.append("package_members")
    expected_source_paths = frozenset(
        (*_SDIST_ROOT_MEMBERS, *(f"src/{member.path}" for member in wheel_members))
    )
    if frozenset(member.path for member in source.members) != expected_source_paths:
        differences.append("source_members")
    expected_wheel_paths = frozenset(
        (
            *(member.path for member in source_members),
            *_wheel_metadata_paths(module_root, source.version),
        )
    )
    if frozenset(member.path for member in wheel.members) != expected_wheel_paths:
        differences.append("wheel_members")
    source_by_path = {member.path: member for member in source.members}
    wheel_by_path = {member.path: member for member in wheel.members}
    dist_info = f"{module_root}-{source.version}.dist-info"
    content_pairs = (
        ("PKG-INFO", f"{dist_info}/METADATA", "metadata_content"),
        ("LICENSE", f"{dist_info}/licenses/LICENSE", "license_content"),
        ("NOTICE.md", f"{dist_info}/licenses/NOTICE.md", "notice_content"),
    )
    for source_path, wheel_path, difference in content_pairs:
        source_member = source_by_path.get(source_path)
        wheel_member = wheel_by_path.get(wheel_path)
        if (
            source_member is None
            or wheel_member is None
            or (
                source_member.size,
                source_member.sha256,
            )
            != (wheel_member.size, wheel_member.sha256)
        ):
            differences.append(difference)
    return tuple(differences)


def compare_normalized_artifacts(
    left: ArtifactEvidence,
    right: ArtifactEvidence,
    *,
    contract: str,
) -> ComparisonResult:
    """Compare evidence under one explicit, closed normalization contract."""

    if not _is_artifact_evidence(left) or not _is_artifact_evidence(right):
        raise TypeError("evidence")
    if contract == "same-kind-v1":
        differences = _comparison_differences(left, right)
    elif contract == "wheel-from-sdist-v1":
        differences = _wheel_from_sdist_differences(left, right)
    else:
        raise ValueError("contract")
    return ComparisonResult(matches=not differences, differences=differences)


def _safe_bytes(path: Path, *, maximum: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _artifact_error(
            "artifact.build_input_invalid", "Build input cannot be opened safely."
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise _artifact_error(
                "artifact.build_input_invalid", "Build input exceeds its trusted limit."
            )
        chunks: list[bytes] = []
        total = 0
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while True:
                chunk = stream.read(min(_CHUNK_BYTES, maximum + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum:
                    raise _artifact_error(
                        "artifact.build_input_invalid",
                        "Build input exceeds its trusted limit.",
                    )
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    raw = b"".join(chunks)
    if identity_before != identity_after or len(raw) != before.st_size:
        raise _artifact_error(
            "artifact.build_input_invalid", "Build input changed while being verified."
        )
    return raw


def _backend_identity(raw: bytes) -> tuple[str, str]:
    if len(raw) > LIMIT_POLICY.value("archive_compressed_bytes"):
        raise _limit_error()
    try:
        with zipfile.ZipFile(io.BytesIO(raw), mode="r") as archive:
            metadata_infos = [
                info
                for info in archive.infolist()
                if info.filename.endswith(".dist-info/METADATA")
            ]
            if (
                len(metadata_infos) != 1
                or metadata_infos[0].file_size > _MAX_METADATA_BYTES
            ):
                raise _artifact_error(
                    "artifact.backend_identity_mismatch",
                    "Build backend metadata is invalid.",
                )
            info = metadata_infos[0]
            _portable_path(info.filename)
            raw_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_IFMT(raw_mode) not in {0, stat.S_IFREG}:
                raise _unsafe_member()
            with archive.open(info, mode="r") as stream:
                metadata = stream.read(_MAX_METADATA_BYTES + 1)
    except ForgeError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise _artifact_error(
            "artifact.backend_identity_mismatch", "Build backend wheel is invalid."
        ) from exc
    if len(metadata) > _MAX_METADATA_BYTES:
        raise _artifact_error(
            "artifact.backend_identity_mismatch", "Build backend metadata is invalid."
        )
    return _metadata_identity(metadata)


def _constraints_bind_backend(
    constraints: bytes,
    *,
    backend_name: str,
    backend_version: str,
    backend_sha256: str,
) -> bool:
    try:
        rendered = constraints.decode("utf-8")
    except UnicodeDecodeError:
        return False
    escaped_backend = re.escape(backend_name).replace(r"\-", "[-_]")
    requirement = re.compile(
        rf"(?im)^\s*{escaped_backend}"
        rf"=={re.escape(backend_version)}(?:\s|\\|$)"
    )
    return requirement.search(rendered) is not None and (
        f"sha256:{backend_sha256}" in rendered
    )


def _trusted_constraints_bytes() -> bytes:
    lines = [f"uv_build=={_BACKEND_VERSION} \\"]
    for index, digest in enumerate(_CONSTRAINT_HASHES):
        suffix = " \\" if index < len(_CONSTRAINT_HASHES) - 1 else ""
        lines.append(f"    --hash=sha256:{digest}{suffix}")
    rendered = ("\n".join(lines) + "\n").encode("ascii")
    if hashlib.sha256(rendered).hexdigest() != _CONSTRAINTS_SHA256:
        raise _artifact_error(
            "artifact.backend_identity_mismatch",
            "Installed build constraints are internally inconsistent.",
        )
    return rendered


def _toolchain_platform() -> str:
    machine = platform.machine().lower()
    if sys.platform.startswith("linux") and machine in {"amd64", "x86_64"}:
        return "linux-x86_64"
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if sys.platform == "darwin" and machine in {"amd64", "x86_64"}:
        return "macos-x86_64"
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return "windows-x86_64"
    raise _artifact_error(
        "artifact.builder_unavailable", "Python build platform is not supported."
    )


def _locked_tool_version(lock: Mapping[str, object], name: str) -> str:
    tools = lock.get("tools")
    if not isinstance(tools, Sequence) or isinstance(tools, (str, bytes, bytearray)):
        raise _artifact_error(
            "artifact.backend_identity_mismatch",
            "Installed toolchain authority is invalid.",
        )
    matches = [
        item for item in tools if isinstance(item, Mapping) and item.get("name") == name
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("version"), str):
        raise _artifact_error(
            "artifact.backend_identity_mismatch",
            "Installed toolchain authority is invalid.",
        )
    return str(matches[0]["version"])


def _locked_inputs(
    *, uv_artifact: bytes, backend_artifact: bytes
) -> _LockedBuildInputs:
    if not isinstance(uv_artifact, bytes) or not isinstance(backend_artifact, bytes):
        raise TypeError("build inputs")
    if len(uv_artifact) > MAX_TOOL_BYTES or len(backend_artifact) > MAX_TOOL_BYTES:
        raise _limit_error()
    try:
        lock = load_toolchain_lock()
        platform_name = _toolchain_platform()
        uv_spec = select_artifact(lock, tool="uv", platform=platform_name)
        backend_spec = select_artifact(lock, tool="uv-build", platform=platform_name)
        uv_lock_version = _locked_tool_version(lock, "uv")
        backend_lock_version = _locked_tool_version(lock, "uv-build")
    except ForgeError as exc:
        raise _artifact_error(
            "artifact.backend_identity_mismatch",
            "Installed toolchain authority is unavailable.",
        ) from exc
    uv_sha256 = hashlib.sha256(uv_artifact).hexdigest()
    backend_sha256 = hashlib.sha256(backend_artifact).hexdigest()
    constraints = _trusted_constraints_bytes()
    if (
        uv_sha256 != uv_spec["sha256"]
        or backend_sha256 != backend_spec["sha256"]
        or uv_lock_version != _UV_VERSION
        or backend_lock_version != _BACKEND_VERSION
        or uv_spec["archive_type"] not in {"tar.gz", "zip"}
        or backend_spec["archive_type"] != "wheel"
    ):
        raise _artifact_error(
            "artifact.backend_identity_mismatch",
            "Offline build input does not match the installed toolchain lock.",
        )
    backend_name, backend_version = _backend_identity(backend_artifact)
    if (
        backend_name != _BACKEND_DISTRIBUTION
        or backend_version != _BACKEND_VERSION
        or not _constraints_bind_backend(
            constraints,
            backend_name=backend_name,
            backend_version=backend_version,
            backend_sha256=backend_sha256,
        )
    ):
        raise _artifact_error(
            "artifact.backend_identity_mismatch",
            "Build backend does not match the installed build profile.",
        )
    backend_filename = Path(urllib.parse.urlparse(backend_spec["url"]).path).name
    if not backend_filename:
        raise _artifact_error(
            "artifact.backend_identity_mismatch", "Locked backend filename is invalid."
        )
    return _LockedBuildInputs(
        identity=OfflineBuildIdentity(
            uv_version=uv_lock_version,
            uv_sha256=uv_sha256,
            backend_name=backend_name,
            backend_version=backend_version,
            backend_sha256=backend_sha256,
            constraints_sha256=_CONSTRAINTS_SHA256,
            _token=_OFFLINE_IDENTITY_TOKEN,
        ),
        uv_spec=uv_spec,
        backend_filename=backend_filename,
        constraints=constraints,
    )


def validate_offline_build_inputs(
    *,
    uv_artifact: bytes,
    backend_artifact: bytes,
) -> OfflineBuildIdentity:
    """Bind raw uv/backend bytes to the installed platform toolchain lock."""

    return _locked_inputs(
        uv_artifact=uv_artifact, backend_artifact=backend_artifact
    ).identity


def validate_python_build_result(result: PythonBuildResult) -> PythonBuildResult:
    """Reject any post-mint mutation of a Python build result."""

    if not _is_python_build_result(result):
        raise TypeError("Python build result")
    return result


def _build_environment(private_root: Path, executable: Path) -> dict[str, str]:
    private_root.mkdir(mode=0o700)
    home = private_root / "home"
    temporary = private_root / "tmp"
    cache = private_root / "uv-cache"
    config = private_root / "config"
    roaming = private_root / "profile-roaming"
    local = private_root / "profile-local"
    for directory in (home, temporary, cache, config, roaming, local):
        directory.mkdir(mode=0o700)
    selected = {
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.pathsep.join(
            (str(executable.parent), str(Path(sys.executable).parent))
        ),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "SOURCE_DATE_EPOCH": "315532800",
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "TMPDIR": str(temporary),
        "TZ": "UTC",
        "USERPROFILE": str(home),
        "UV_CACHE_DIR": str(cache),
        "UV_NO_INDEX": "1",
        "UV_NO_PROGRESS": "1",
        "UV_OFFLINE": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "XDG_CACHE_HOME": str(cache),
        "XDG_CONFIG_HOME": str(config),
        "APPDATA": str(roaming),
        "LOCALAPPDATA": str(local),
    }
    for name in ("COMSPEC", "PATHEXT", "SYSTEMROOT", "WINDIR"):
        value = os.environ.get(name)
        if value is not None:
            selected[name] = value
    return selected


def _extract_locked_uv(raw: bytes, spec: Mapping[str, str], destination: Path) -> Path:
    expected_path = spec["executable"]
    _portable_path(expected_path)
    selected: bytes | None = None
    names: set[str] = set()
    try:
        if spec["archive_type"] == "zip":
            with zipfile.ZipFile(io.BytesIO(raw), mode="r") as archive:
                for info in archive.infolist():
                    directory = info.is_dir() or info.filename.endswith("/")
                    path = info.filename[:-1] if directory else info.filename
                    _portable_path(path)
                    collision = _collision_key(path)
                    if collision in names:
                        raise _unsafe_member()
                    names.add(collision)
                    raw_mode = (info.external_attr >> 16) & 0xFFFF
                    file_type = stat.S_IFMT(raw_mode)
                    if directory:
                        if file_type not in {0, stat.S_IFDIR}:
                            raise _unsafe_member()
                        continue
                    if (
                        file_type not in {0, stat.S_IFREG}
                        or info.file_size > MAX_TOOL_BYTES
                    ):
                        raise _unsafe_member()
                    if path == expected_path:
                        if selected is not None:
                            raise _unsafe_member()
                        with archive.open(info, mode="r") as stream:
                            selected = stream.read(MAX_TOOL_BYTES + 1)
        elif spec["archive_type"] == "tar.gz":
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r|gz") as archive:
                for item in archive:
                    path = item.name[:-1] if item.name.endswith("/") else item.name
                    _portable_path(path)
                    collision = _collision_key(path)
                    if collision in names:
                        raise _unsafe_member()
                    names.add(collision)
                    if item.isdir():
                        continue
                    if not item.isfile() or item.size > MAX_TOOL_BYTES:
                        raise _unsafe_member()
                    if path == expected_path:
                        if selected is not None:
                            raise _unsafe_member()
                        source = archive.extractfile(item)
                        if source is None:
                            raise _unsafe_member()
                        selected = source.read(MAX_TOOL_BYTES + 1)
        else:
            raise _unsafe_member()
    except ForgeError:
        raise
    except (
        EOFError,
        OSError,
        RuntimeError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        raise _artifact_error(
            "artifact.builder_unavailable", "Locked uv archive is invalid."
        ) from exc
    if selected is None or len(selected) > MAX_TOOL_BYTES:
        raise _artifact_error(
            "artifact.builder_unavailable", "Locked uv executable is missing."
        )
    executable = destination / ("uv.exe" if os.name == "nt" else "uv")
    executable.write_bytes(selected)
    executable.chmod(0o700)
    return executable


def _safe_backend_filename(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.endswith(".whl")
        or Path(value).name != value
        or value in {".", ".."}
    ):
        raise _artifact_error(
            "artifact.build_input_invalid", "Build backend filename is invalid."
        )
    _portable_path(value)
    return value


def _windows_job_for_process(process: subprocess.Popen[bytes]) -> int:
    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError("CreateJobObjectW failed")
    try:
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        if not kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise OSError("SetInformationJobObject failed")
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        process_handle = wintypes.HANDLE(int(getattr(process, "_handle")))
        if not kernel32.AssignProcessToJobObject(job, process_handle):
            raise OSError("AssignProcessToJobObject failed")
        return int(job)
    except BaseException:
        kernel32.CloseHandle(job)
        raise


def _resume_windows_process(process: subprocess.Popen[bytes]) -> None:
    """Resume the sole primary thread after race-free Job assignment."""

    import ctypes
    from ctypes import wintypes

    class ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError("CreateToolhelp32Snapshot failed")
    thread_ids: list[int] = []
    try:
        entry = ThreadEntry32()
        entry.dwSize = ctypes.sizeof(ThreadEntry32)
        kernel32.Thread32First.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ThreadEntry32),
        ]
        kernel32.Thread32First.restype = wintypes.BOOL
        kernel32.Thread32Next.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ThreadEntry32),
        ]
        kernel32.Thread32Next.restype = wintypes.BOOL
        available = bool(kernel32.Thread32First(snapshot, ctypes.byref(entry)))
        while available:
            if entry.th32OwnerProcessID == process.pid:
                thread_ids.append(int(entry.th32ThreadID))
            entry.dwSize = ctypes.sizeof(ThreadEntry32)
            available = bool(kernel32.Thread32Next(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)
    if len(thread_ids) != 1:
        raise OSError("suspended process primary thread is unavailable")

    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenThread.restype = wintypes.HANDLE
    thread = kernel32.OpenThread(0x0002, False, thread_ids[0])
    if not thread:
        raise OSError("OpenThread failed")
    try:
        kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel32.ResumeThread.restype = wintypes.DWORD
        if kernel32.ResumeThread(thread) != 1:
            raise OSError("ResumeThread failed")
    finally:
        kernel32.CloseHandle(thread)


def _terminate_process_tree(process: subprocess.Popen[bytes], job: int | None) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return
    if job is not None:
        import ctypes
        from ctypes import wintypes

        kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject(wintypes.HANDLE(job), 1)
        return
    try:
        process.kill()
    except OSError:
        pass


def _close_windows_job(job: int | None) -> None:
    if job is None or os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(wintypes.HANDLE(job))


def _bounded_process(
    command: tuple[str, ...],
    *,
    cwd: Path | None,
    environment: Mapping[str, str],
    umask: int | None,
    timeout: int,
    channel_limit: int,
    tail_limit: int,
    failure_code: str,
    failure_message: str,
) -> tuple[int, bytes]:
    exceeded = threading.Event()
    reader_failed = threading.Event()
    total = [0]
    tail = bytearray()
    job: int | None = None
    termination_lock = threading.Lock()
    try:
        if os.name == "posix" and umask is not None:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=dict(environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                umask=umask,
            )
        elif os.name == "posix":
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=dict(environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        else:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=dict(environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x00000004
                ),
            )
            job = _windows_job_for_process(process)
            _resume_windows_process(process)
    except OSError as exc:
        if "process" in locals():
            try:
                process.kill()
            except OSError:
                pass
        _close_windows_job(job)
        raise _artifact_error(failure_code, failure_message) from exc
    stream = process.stdout
    if stream is None:
        _terminate_process_tree(process, job)
        _close_windows_job(job)
        raise _artifact_error(failure_code, failure_message)

    def terminate_tree() -> None:
        with termination_lock:
            _terminate_process_tree(process, job)

    def drain() -> None:
        try:
            while True:
                chunk = stream.read(_CHUNK_BYTES)
                if not chunk:
                    break
                total[0] += len(chunk)
                tail.extend(chunk)
                if len(tail) > tail_limit:
                    del tail[:-tail_limit]
                if total[0] > channel_limit:
                    exceeded.set()
                    terminate_tree()
        except (OSError, ValueError):
            reader_failed.set()

    reader = threading.Thread(target=drain, name="zagrosi-uv-output", daemon=True)
    reader.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_tree()
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            returncode = -1
    terminate_tree()
    reader.join(timeout=5)
    if reader.is_alive():
        try:
            stream.close()
        except OSError:
            pass
        reader.join(timeout=1)
    _close_windows_job(job)
    if (
        timed_out
        or exceeded.is_set()
        or reader_failed.is_set()
        or reader.is_alive()
        or returncode != 0
    ):
        raise _artifact_error(failure_code, failure_message)
    return total[0], bytes(tail)


def _run_uv_build(
    source: str,
    *,
    executable: Path,
    cwd: Path,
    destination: Path,
    environment_root: Path,
    backend_artifact: Path,
    constraints: Path,
    identity: OfflineBuildIdentity,
    build_sdist: bool,
) -> tuple[tuple[str, ...], tuple[Path, ...], OfflineBuildEvidence]:
    if not _is_offline_identity(identity):
        raise TypeError("identity")
    selected_environment = _build_environment(environment_root, executable)
    command = (
        str(executable),
        "build",
        "--no-create-gitignore",
        "--no-config",
        "--no-managed-python",
        "--no-sources",
        "--offline",
        "--no-index",
        "--find-links",
        str(backend_artifact.parent),
        "--build-constraints",
        str(constraints),
        "--require-hashes",
        "--python",
        sys.executable,
        *(("--sdist",) if build_sdist else ()),
        "--wheel",
        "--out-dir",
        str(destination),
        source,
    )
    command_profile = (
        "uv",
        "build",
        "--no-create-gitignore",
        "--no-config",
        "--no-managed-python",
        "--no-sources",
        "--offline",
        "--no-index",
        "--find-links",
        "<verified-wheelhouse>",
        "--build-constraints",
        "<verified-constraints>",
        "--require-hashes",
        "--python",
        "<verified-python>",
        *(("--sdist",) if build_sdist else ()),
        "--wheel",
        "--out-dir",
        "<private-output>",
        "<source-tree>" if source == "." else "<source-sdist>",
    )
    output_bytes, tail = _bounded_process(
        command,
        cwd=cwd,
        environment=selected_environment,
        umask=0o077,
        timeout=LIMIT_POLICY.value("subprocess_total_seconds"),
        channel_limit=LIMIT_POLICY.value("subprocess_channel_bytes"),
        tail_limit=LIMIT_POLICY.value("subprocess_tail_bytes"),
        failure_code="artifact.build_failed",
        failure_message="Offline Python artifact build failed safely.",
    )
    outputs = tuple(
        sorted(
            (
                path
                for path in destination.iterdir()
                if path.is_file() and not path.is_symlink()
            ),
            key=lambda path: path.name,
        )
    )
    expected_count = 2 if build_sdist else 1
    if len(outputs) != expected_count:
        raise _artifact_error(
            "artifact.build_failed", "Offline build produced unexpected outputs."
        )
    if identity.backend_name != _BACKEND_DISTRIBUTION:
        raise _artifact_error(
            "artifact.backend_identity_mismatch", "Build backend identity changed."
        )
    proof_environment = tuple(
        (name, selected_environment[name])
        for name in (
            "PIP_NO_INDEX",
            "UV_NO_INDEX",
            "UV_OFFLINE",
            "UV_PYTHON_DOWNLOADS",
        )
    )
    evidence = OfflineBuildEvidence(
        command_flags=tuple(item for item in command_profile if item.startswith("--")),
        environment=proof_environment,
        resolver_mode="offline-no-index-fresh-cache-verified-wheelhouse",
        egress="not_claimed",
        returncode=0,
        captured_output_bytes=output_bytes,
        captured_tail_bytes=len(tail),
        _token=_OFFLINE_EVIDENCE_TOKEN,
    )
    return command_profile, outputs, evidence


@contextmanager
def _staged_build_inputs(
    *,
    parent: Path,
    backend_filename: str,
    backend_raw: bytes,
    constraints_raw: bytes,
    identity: OfflineBuildIdentity,
) -> Iterator[tuple[Path, Path]]:
    if not _is_offline_identity(identity):
        raise TypeError("identity")
    _safe_backend_filename(backend_filename)
    with tempfile.TemporaryDirectory(
        prefix=".zagrosi-build-inputs-", dir=parent
    ) as temporary:
        root = Path(temporary)
        backend = root / backend_filename
        constraints = root / "build-constraints.txt"
        backend.write_bytes(backend_raw)
        constraints.write_bytes(constraints_raw)
        backend.chmod(0o600)
        constraints.chmod(0o600)
        if (
            hashlib.sha256(
                _safe_bytes(
                    backend,
                    maximum=LIMIT_POLICY.value("archive_compressed_bytes"),
                )
            ).hexdigest()
            != identity.backend_sha256
            or hashlib.sha256(
                _safe_bytes(
                    constraints,
                    maximum=LIMIT_POLICY.value("json_record_bytes"),
                )
            ).hexdigest()
            != identity.constraints_sha256
        ):
            raise _artifact_error(
                "artifact.backend_identity_mismatch",
                "Staged build inputs do not match verified bytes.",
            )
        yield backend, constraints


def _python_build_result(
    outputs: tuple[Path, ...],
    *,
    identity: OfflineBuildIdentity,
    command: tuple[str, ...],
    offline_evidence: OfflineBuildEvidence,
    expected: PythonArtifactPolicy,
    bundle: CanonicalBundle,
    input_sdist: tuple[str, bytes] | None = None,
) -> PythonBuildResult:
    if not _is_offline_identity(identity) or not _is_offline_evidence(offline_evidence):
        raise TypeError("build authority")
    if not _is_python_policy(expected):
        raise TypeError("Python artifact policy")
    wheels = tuple(path for path in outputs if path.name.endswith(".whl"))
    sdists = tuple(path for path in outputs if path.name.endswith(".tar.gz"))
    if len(wheels) != 1 or (input_sdist is None and len(sdists) != 1):
        raise _artifact_error(
            "artifact.build_failed", "Offline build output names are invalid."
        )
    wheel_bytes = _safe_bytes(
        wheels[0], maximum=LIMIT_POLICY.value("archive_compressed_bytes")
    )
    if input_sdist is None:
        sdist_name = sdists[0].name
        sdist_bytes = _safe_bytes(
            sdists[0], maximum=LIMIT_POLICY.value("archive_compressed_bytes")
        )
    else:
        sdist_name, sdist_bytes = input_sdist
    wheel_evidence = inspect_python_artifact(
        wheel_bytes,
        artifact_kind="wheel",
        expected=expected,
    )
    sdist_evidence = inspect_python_artifact(
        sdist_bytes,
        artifact_kind="sdist",
        expected=expected,
    )
    _verify_sdist_against_bundle(sdist_evidence, bundle, expected)
    comparison = compare_normalized_artifacts(
        sdist_evidence, wheel_evidence, contract="wheel-from-sdist-v1"
    )
    if not comparison.matches:
        raise _artifact_error(
            "artifact.reproducibility_mismatch",
            "Wheel and source distribution do not match the trusted contract.",
        )
    return PythonBuildResult(
        wheel_name=wheels[0].name,
        wheel_bytes=wheel_bytes,
        sdist_name=sdist_name,
        sdist_bytes=sdist_bytes,
        identity=identity,
        base_payload_digest=bundle.manifest.payload_digest,
        artifact_policy_digest=expected.policy_digest,
        command=command,
        offline_evidence=offline_evidence,
        _token=_PYTHON_RESULT_TOKEN,
    )


def build_python_artifacts(
    bundle: CanonicalBundle,
    *,
    expected: PythonArtifactPolicy,
    uv_artifact: bytes,
    backend_artifact: bytes,
) -> PythonBuildResult:
    """Build from sealed canonical bytes with the installed offline toolchain."""

    source_entries = _python_source_entries(bundle, expected)
    locked = _locked_inputs(uv_artifact=uv_artifact, backend_artifact=backend_artifact)
    with tempfile.TemporaryDirectory(prefix="zagrosi-python-build-") as temporary:
        root = Path(temporary)
        tool_root = root / "tool"
        tool_root.mkdir(mode=0o700)
        executable = _extract_locked_uv(uv_artifact, locked.uv_spec, tool_root)
        source = root / "source"
        _write_private_python_source(source, source_entries)
        output_root = root / "output"
        output_root.mkdir(mode=0o700)
        with _staged_build_inputs(
            parent=root,
            backend_filename=locked.backend_filename,
            backend_raw=backend_artifact,
            constraints_raw=locked.constraints,
            identity=locked.identity,
        ) as (staged_backend, staged_constraints):
            command, outputs, offline_evidence = _run_uv_build(
                ".",
                executable=executable,
                cwd=source,
                destination=output_root,
                environment_root=root / "environment",
                backend_artifact=staged_backend,
                constraints=staged_constraints,
                identity=locked.identity,
                build_sdist=True,
            )
            return _python_build_result(
                outputs,
                identity=locked.identity,
                command=command,
                offline_evidence=offline_evidence,
                expected=expected,
                bundle=bundle,
            )


def build_wheel_from_sdist(
    sdist: bytes,
    *,
    bundle: CanonicalBundle,
    expected: PythonArtifactPolicy,
    uv_artifact: bytes,
    backend_artifact: bytes,
) -> PythonBuildResult:
    """Build exactly one wheel from an already inspected source distribution."""

    if not isinstance(sdist, bytes) or len(sdist) > LIMIT_POLICY.value(
        "archive_compressed_bytes"
    ):
        raise _limit_error()
    inspected_sdist = inspect_python_artifact(
        sdist,
        artifact_kind="sdist",
        expected=expected,
    )
    _verify_sdist_against_bundle(inspected_sdist, bundle, expected)
    sdist_name = (
        f"{inspected_sdist.distribution.replace('-', '_')}-{expected.version}.tar.gz"
    )
    locked = _locked_inputs(uv_artifact=uv_artifact, backend_artifact=backend_artifact)
    with tempfile.TemporaryDirectory(prefix="zagrosi-sdist-build-") as temporary:
        root = Path(temporary)
        tool_root = root / "tool"
        tool_root.mkdir(mode=0o700)
        executable = _extract_locked_uv(uv_artifact, locked.uv_spec, tool_root)
        source_archive = root / sdist_name
        source_archive.write_bytes(sdist)
        output_root = root / "output"
        output_root.mkdir(mode=0o700)
        with _staged_build_inputs(
            parent=root,
            backend_filename=locked.backend_filename,
            backend_raw=backend_artifact,
            constraints_raw=locked.constraints,
            identity=locked.identity,
        ) as (staged_backend, staged_constraints):
            command, outputs, offline_evidence = _run_uv_build(
                str(source_archive),
                executable=executable,
                cwd=root,
                destination=output_root,
                environment_root=root / "environment",
                backend_artifact=staged_backend,
                constraints=staged_constraints,
                identity=locked.identity,
                build_sdist=False,
            )
            return _python_build_result(
                outputs,
                identity=locked.identity,
                command=command,
                offline_evidence=offline_evidence,
                expected=expected,
                bundle=bundle,
                input_sdist=(sdist_name, sdist),
            )


__all__ = [
    "ArchiveLimits",
    "ArtifactDirectory",
    "ArtifactEvidence",
    "ComparisonResult",
    "OfflineBuildEvidence",
    "OfflineBuildIdentity",
    "PythonArtifactPolicy",
    "PythonBuildResult",
    "build_python_artifacts",
    "build_wheel_from_sdist",
    "compare_normalized_artifacts",
    "derive_python_artifact_policy",
    "inspect_plugin_zip",
    "inspect_python_artifact",
    "validate_offline_build_inputs",
    "validate_python_build_result",
    "write_controlled_plugin_zip",
]
