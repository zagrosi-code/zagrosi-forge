"""Trusted positive bundle policy, canonical projections, and owned staging."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
import hashlib
import hmac
from importlib import resources
import json
import math
import os
import re
import stat
from types import MappingProxyType
from typing import Any, Never, cast
import unicodedata

from . import paths as _paths
from .contracts import (
    BundleEntry,
    BundleManifest,
    DiagnosticReport,
    Finding,
    ForgeError,
    canonical_json_bytes,
    decode_persistent_record,
)
from .metadata import ValidatedPackage
from .paths import (
    PathProof,
    SafeRelativePath,
    SourceRoot,
    SourceSnapshot,
    validate_reference,
    validate_reference_set,
)
from .policies import LIMIT_POLICY
from .version import VERSION, derive_install_version


_POLICY_RESOURCE = "bundle-policy.json"
_POLICY_SCHEMA_RESOURCE = "schemas/bundle-policy-v1.schema.json"
_POLICY_SCHEMA_DIGEST = (
    "f768ef3c46d7c1c38259f0c10c00ac5b5a955a8b595d7b8bea69908437de8d22"
)
_POLICY_PATH = "src/zagrosi_forge/install/bundle-policy.json"
_PLUGIN_MANIFEST_PATH = ".codex-plugin/plugin.json"
_BUNDLE_MANIFEST_PATH = ".codex-plugin/bundle-manifest.json"
_MARKETPLACE_PATH = ".agents/plugins/marketplace.json"
_PLUGIN_ROOT = "plugins/zagrosi-forge"
_NORMALIZATION_PROFILE = "bundle-v1"
_TRANSFORMATION_PROFILE = "plugin-v1"
_POLICY_TOKEN = object()
_BUNDLE_TOKEN = object()
_AUTHORITY_SECRET = os.urandom(32)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_ROOT = re.compile(r"[A-Za-z0-9._-]+\Z")
_POLICY_KEYS = frozenset(
    {
        "allowed_roots",
        "archive_profile",
        "codexignore",
        "executable_files",
        "generated_overlays",
        "limit_policy_version",
        "limits",
        "minimum_reader_version",
        "policy_version",
        "record_digest",
        "required_conditions",
        "required_files",
        "schema_digest",
        "schema_version",
        "validation_only_files",
        "writer_version",
    }
)
_GENERATED_OVERLAYS = {
    "marketplace_manifest_path": _MARKETPLACE_PATH,
    "marketplace_source": f"./{_PLUGIN_ROOT}",
    "plugin_manifest_path": _PLUGIN_MANIFEST_PATH,
    "plugin_version_pointer": "/version",
}
_LIMIT_REFERENCES = {
    "archive_compressed_bytes": "archive_compressed_bytes",
    "archive_expanded_bytes": "archive_expanded_bytes",
    "archive_ratio": "archive_ratio",
    "files": "bundle_files",
    "member_bytes": "bundle_member_bytes",
    "total_bytes": "bundle_total_bytes",
}
_ARCHIVE_PROFILE: Mapping[str, str | int] = {
    "compression": "deflate",
    "compression_level": 9,
    "create_system": 3,
    "format": "zip",
    "name": "plugin-zip-v1",
    "timestamp": "1980-01-01T00:00:00Z",
}
_CONDITION_KEYS = frozenset(
    {"licenses", "schemas", "skill_entrypoints", "vendor_records"}
)
_LOCAL_SKILL_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9._/\\-])(references[/\\][^\s`'\"()<>{}\[\]]+)"
)
_SKILL_FRONTMATTER_NAME = re.compile(r"(?m)^name:[ \t]*([a-z0-9-]+)[ \t]*$")
_SKILL_PROMPT_REFERENCE = re.compile(r"\$zagrosi-forge:([a-z0-9-]+)")
_BUNDLE_JSON_MEMBER_LIMIT = LIMIT_POLICY.value("bundle_files") * 8 + 256
_BUNDLE_JSON_BYTE_LIMIT = (
    LIMIT_POLICY.value("bundle_total_bytes") + LIMIT_POLICY.value("bundle_files") * 1024
)


def _error(code: str, message: str) -> ForgeError:
    return ForgeError(code, 12, message)


def _policy_error(message: str = "The trusted bundle policy is invalid.") -> ForgeError:
    return _error("bundle.policy_invalid", message)


def _finding(code: str, subject: str) -> Finding:
    return Finding(
        code=code,
        severity="error",
        message="Bundle policy validation failed.",
        subject=subject,
        authority="bundle-policy",
        authority_version="1.0",
        remediation="Use the supported installed bundle policy.",
        details={},
    )


def _bundle_json_value(
    value: object, *, depth: int = 0, members: list[int] | None = None
) -> object:
    if members is None:
        members = [0]
    if depth > 32:
        raise _error("bundle.limit_exceeded", "Bundle JSON nesting is too deep.")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _error("bundle.policy_invalid", "Bundle JSON contains a number.")
        return value
    if isinstance(value, Enum):
        return _bundle_json_value(value.value, depth=depth, members=members)
    if is_dataclass(value) and not isinstance(value, type):
        value = {field.name: getattr(value, field.name) for field in fields(value)}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _policy_error("Bundle JSON object keys are invalid.")
        members[0] += len(value)
        if members[0] > _BUNDLE_JSON_MEMBER_LIMIT:
            raise _error("bundle.limit_exceeded", "Bundle JSON has too many members.")
        return {
            key: _bundle_json_value(item, depth=depth + 1, members=members)
            for key, item in sorted(value.items())
        }
    if isinstance(value, (list, tuple)):
        members[0] += len(value)
        if members[0] > _BUNDLE_JSON_MEMBER_LIMIT:
            raise _error("bundle.limit_exceeded", "Bundle JSON has too many members.")
        return [
            _bundle_json_value(item, depth=depth + 1, members=members) for item in value
        ]
    raise _policy_error("Bundle JSON contains an unsupported value.")


def canonical_bundle_json_bytes(value: object, *, final_newline: bool = False) -> bytes:
    """Serialize bundle-sized JSON without weakening the generic record bound."""

    try:
        rendered = json.dumps(
            _bundle_json_value(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, UnicodeEncodeError, ValueError) as exc:
        raise _policy_error("Bundle JSON cannot be rendered safely.") from exc
    result = rendered + (b"\n" if final_newline else b"")
    if len(result) > _BUNDLE_JSON_BYTE_LIMIT:
        raise _error("bundle.limit_exceeded", "Bundle JSON exceeds its byte limit.")
    return result


def _authority_digest(kind: str, value: object) -> str:
    domain = kind.encode("ascii") + b"\0" + canonical_bundle_json_bytes(value)
    return hmac.new(_AUTHORITY_SECRET, domain, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class _PolicyFields:
    policy_version: str
    limit_policy_version: str
    policy_digest: str
    required_files: tuple[str, ...]
    validation_only_files: tuple[str, ...]
    allowed_roots: tuple[str, ...]
    generated_overlays: Mapping[str, str]
    executable_files: tuple[str, ...]
    limits: Mapping[str, str]
    archive_profile: Mapping[str, str | int]
    required_conditions: Mapping[str, tuple[str, ...]]
    codexignore_lines: tuple[str, ...]
    codexignore_digest: str


@dataclass(frozen=True, slots=True, init=False)
class BundlePolicy:
    policy_version: str
    limit_policy_version: str
    policy_digest: str
    required_files: tuple[str, ...]
    validation_only_files: tuple[str, ...]
    allowed_roots: tuple[str, ...]
    generated_overlays: Mapping[str, str]
    executable_files: tuple[str, ...]
    limits: Mapping[str, str]
    archive_profile: Mapping[str, str | int]
    required_conditions: Mapping[str, tuple[str, ...]]
    codexignore_lines: tuple[str, ...]
    codexignore_digest: str
    _resource_bytes: bytes
    _seal: object
    _authority_digest_value: str

    def __init__(
        self, fields_value: _PolicyFields, resource_bytes: bytes, *, _token: object
    ) -> None:
        if _token is not _POLICY_TOKEN:
            raise TypeError("BundlePolicy is loaded only from installed resources")
        for policy_field in fields(_PolicyFields):
            object.__setattr__(
                self, policy_field.name, getattr(fields_value, policy_field.name)
            )
        object.__setattr__(self, "_resource_bytes", bytes(resource_bytes))
        object.__setattr__(self, "_seal", _POLICY_TOKEN)
        object.__setattr__(
            self,
            "_authority_digest_value",
            _authority_digest("bundle-policy", _policy_authority_domain(self)),
        )

    def __reduce__(self) -> Never:
        raise TypeError("trusted bundle policies are not serializable")


@dataclass(frozen=True, slots=True)
class _MarketplaceProjection:
    marketplace_name: str
    display_name: str
    plugin_name: str
    category: str
    installation_policy: str
    authentication_policy: str


@dataclass(frozen=True, slots=True, init=False)
class CanonicalBundle:
    manifest: BundleManifest
    manifest_bytes: bytes
    entry_bytes: Mapping[str, bytes]
    source_snapshot_identity: str
    marketplace: _MarketplaceProjection
    _seal: object
    _authority_digest_value: str

    def __init__(
        self,
        *,
        manifest: BundleManifest,
        manifest_bytes: bytes,
        entry_bytes: Mapping[str, bytes],
        source_snapshot_identity: str,
        marketplace: _MarketplaceProjection,
        _token: object,
    ) -> None:
        if _token is not _BUNDLE_TOKEN:
            raise TypeError("CanonicalBundle is created only by bundle enumeration")
        object.__setattr__(self, "manifest", manifest)
        object.__setattr__(self, "manifest_bytes", bytes(manifest_bytes))
        object.__setattr__(
            self,
            "entry_bytes",
            MappingProxyType(
                {path: bytes(raw) for path, raw in sorted(entry_bytes.items())}
            ),
        )
        object.__setattr__(self, "source_snapshot_identity", source_snapshot_identity)
        object.__setattr__(self, "marketplace", marketplace)
        object.__setattr__(self, "_seal", _BUNDLE_TOKEN)
        object.__setattr__(
            self,
            "_authority_digest_value",
            _authority_digest("canonical-bundle", _canonical_authority_domain(self)),
        )

    @property
    def entries(self) -> tuple[BundleEntry, ...]:
        return self.manifest.entries


@dataclass(frozen=True, slots=True, init=False)
class RenderedBundle:
    base: CanonicalBundle
    install_version: str
    transformation_profile: str
    entries: tuple[BundleEntry, ...]
    entry_bytes: Mapping[str, bytes]
    rendered_payload_digest: str
    _seal: object
    _authority_digest_value: str

    def __init__(
        self,
        *,
        base: CanonicalBundle,
        install_version: str,
        entries: tuple[BundleEntry, ...],
        entry_bytes: Mapping[str, bytes],
        rendered_payload_digest: str,
        _token: object,
    ) -> None:
        if _token is not _BUNDLE_TOKEN:
            raise TypeError("RenderedBundle is created only by the trusted renderer")
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "install_version", install_version)
        object.__setattr__(self, "transformation_profile", _TRANSFORMATION_PROFILE)
        object.__setattr__(self, "entries", entries)
        object.__setattr__(
            self,
            "entry_bytes",
            MappingProxyType(
                {path: bytes(raw) for path, raw in sorted(entry_bytes.items())}
            ),
        )
        object.__setattr__(self, "rendered_payload_digest", rendered_payload_digest)
        object.__setattr__(self, "_seal", _BUNDLE_TOKEN)
        object.__setattr__(
            self,
            "_authority_digest_value",
            _authority_digest("rendered-bundle", _rendered_authority_domain(self)),
        )


@dataclass(frozen=True, slots=True)
class ArtifactEvidence:
    payload_digest: str
    manifest_digest: str
    member_count: int
    aggregate_size: int


@dataclass(frozen=True, slots=True)
class _ObservedStageFile:
    raw: bytes
    mode: int | None
    size: int
    sha256: str


@dataclass(frozen=True, slots=True, init=False)
class StagedMarketplace:
    marketplace_relative: SafeRelativePath
    plugin_root_relative: SafeRelativePath
    plugin_entries: tuple[BundleEntry, ...]
    bundle_manifest_bytes: bytes
    rendered_payload_digest: str
    install_version: str
    evidence: ArtifactEvidence
    _stage_path: PathProof = field(repr=False, compare=False)
    _stage_identity: tuple[int, int] = field(repr=False, compare=False)
    _seal: object
    _authority_digest_value: str = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        marketplace_relative: SafeRelativePath,
        plugin_root_relative: SafeRelativePath,
        plugin_entries: tuple[BundleEntry, ...],
        bundle_manifest_bytes: bytes,
        rendered_payload_digest: str,
        install_version: str,
        evidence: ArtifactEvidence,
        stage_path: PathProof,
        stage_identity: tuple[int, int],
        _token: object,
    ) -> None:
        if _token is not _BUNDLE_TOKEN:
            raise TypeError("StagedMarketplace is created only by owned staging")
        object.__setattr__(self, "marketplace_relative", marketplace_relative)
        object.__setattr__(self, "plugin_root_relative", plugin_root_relative)
        object.__setattr__(self, "plugin_entries", plugin_entries)
        object.__setattr__(self, "bundle_manifest_bytes", bundle_manifest_bytes)
        object.__setattr__(self, "rendered_payload_digest", rendered_payload_digest)
        object.__setattr__(self, "install_version", install_version)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "_stage_path", stage_path)
        object.__setattr__(self, "_stage_identity", stage_identity)
        object.__setattr__(self, "_seal", _BUNDLE_TOKEN)
        object.__setattr__(
            self,
            "_authority_digest_value",
            _authority_digest("staged-marketplace", _staged_authority_domain(self)),
        )


def _policy_authority_domain(value: BundlePolicy) -> Mapping[str, object]:
    return {
        "allowed_roots": value.allowed_roots,
        "archive_profile": dict(value.archive_profile),
        "codexignore_digest": value.codexignore_digest,
        "codexignore_lines": value.codexignore_lines,
        "executable_files": value.executable_files,
        "generated_overlays": dict(value.generated_overlays),
        "limit_policy_version": value.limit_policy_version,
        "limits": dict(value.limits),
        "policy_digest": value.policy_digest,
        "policy_version": value.policy_version,
        "required_conditions": {
            key: tuple(selected)
            for key, selected in sorted(value.required_conditions.items())
        },
        "required_files": value.required_files,
        "resource_sha256": hashlib.sha256(value._resource_bytes).hexdigest(),
        "validation_only_files": value.validation_only_files,
    }


def _marketplace_authority_domain(value: _MarketplaceProjection) -> Mapping[str, str]:
    return {
        "authentication_policy": value.authentication_policy,
        "category": value.category,
        "display_name": value.display_name,
        "installation_policy": value.installation_policy,
        "marketplace_name": value.marketplace_name,
        "plugin_name": value.plugin_name,
    }


def _canonical_authority_domain(value: CanonicalBundle) -> Mapping[str, object]:
    return {
        "entry_bytes": {
            path: {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
            for path, raw in sorted(value.entry_bytes.items())
        },
        "manifest": asdict(value.manifest),
        "manifest_sha256": hashlib.sha256(value.manifest_bytes).hexdigest(),
        "marketplace": _marketplace_authority_domain(value.marketplace),
        "source_snapshot_identity": value.source_snapshot_identity,
    }


def _rendered_authority_domain(value: RenderedBundle) -> Mapping[str, object]:
    return {
        "base_payload_digest": value.base.manifest.payload_digest,
        "entries": [asdict(entry) for entry in value.entries],
        "entry_bytes": {
            path: {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
            for path, raw in sorted(value.entry_bytes.items())
        },
        "install_version": value.install_version,
        "rendered_payload_digest": value.rendered_payload_digest,
        "transformation_profile": value.transformation_profile,
    }


def _staged_authority_domain(value: StagedMarketplace) -> Mapping[str, object]:
    return {
        "bundle_manifest_sha256": hashlib.sha256(
            value.bundle_manifest_bytes
        ).hexdigest(),
        "bundle_manifest_size": len(value.bundle_manifest_bytes),
        "evidence": asdict(value.evidence),
        "install_version": value.install_version,
        "marketplace_relative": value.marketplace_relative.value,
        "plugin_entries": [asdict(entry) for entry in value.plugin_entries],
        "plugin_root_relative": value.plugin_root_relative.value,
        "rendered_payload_digest": value.rendered_payload_digest,
        "stage_identity": value._stage_identity,
        "stage_path_object": id(value._stage_path),
    }


def _policy_fields_from_value(value: BundlePolicy) -> _PolicyFields:
    return _PolicyFields(
        policy_version=value.policy_version,
        limit_policy_version=value.limit_policy_version,
        policy_digest=value.policy_digest,
        required_files=value.required_files,
        validation_only_files=value.validation_only_files,
        allowed_roots=value.allowed_roots,
        generated_overlays=MappingProxyType(dict(value.generated_overlays)),
        executable_files=value.executable_files,
        limits=MappingProxyType(dict(value.limits)),
        archive_profile=MappingProxyType(dict(value.archive_profile)),
        required_conditions=MappingProxyType(
            {
                key: tuple(selected)
                for key, selected in value.required_conditions.items()
            }
        ),
        codexignore_lines=value.codexignore_lines,
        codexignore_digest=value.codexignore_digest,
    )


def _is_policy(value: object) -> bool:
    try:
        raw, installed_fields = _installed_policy_authority()
        return (
            type(value) is BundlePolicy
            and value._seal is _POLICY_TOKEN
            and value._resource_bytes == raw
            and _policy_fields_from_value(value) == installed_fields
            and hmac.compare_digest(
                value._authority_digest_value,
                _authority_digest("bundle-policy", _policy_authority_domain(value)),
            )
        )
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        return False


def _is_canonical(value: object) -> bool:
    try:
        if type(value) is not CanonicalBundle or value._seal is not _BUNDLE_TOKEN:
            return False
        raw_policy, policy_fields = _installed_policy_authority()
        del raw_policy
        manifest = value.manifest
        if (
            type(manifest) is not BundleManifest
            or manifest.schema_version != "1.0"
            or manifest.base_version != VERSION
            or manifest.policy_digest != policy_fields.policy_digest
            or manifest.builder_version != VERSION
            or manifest.normalization_profile != _NORMALIZATION_PROFILE
            or tuple(value.entry_bytes) != policy_fields.required_files
            or manifest.entries != tuple(manifest.entries)
            or tuple(entry.path for entry in manifest.entries)
            != policy_fields.required_files
            or manifest.aggregate_size != sum(entry.size for entry in manifest.entries)
            or set(value.entry_bytes) != set(policy_fields.required_files)
            or not isinstance(value.source_snapshot_identity, str)
            or not value.source_snapshot_identity
            or type(value.marketplace) is not _MarketplaceProjection
            or value.marketplace
            != _MarketplaceProjection(
                marketplace_name="zagrosi",
                display_name="Zagrosi",
                plugin_name="zagrosi-forge",
                category="Coding",
                installation_policy="AVAILABLE",
                authentication_policy="ON_INSTALL",
            )
        ):
            return False
        for entry in manifest.entries:
            raw = value.entry_bytes.get(entry.path)
            expected_mode = (
                0o755 if entry.path in policy_fields.executable_files else 0o644
            )
            if (
                type(entry) is not BundleEntry
                or entry.file_type != "regular"
                or entry.mode != expected_mode
                or not isinstance(raw, bytes)
                or entry.size != len(raw)
                or entry.sha256 != hashlib.sha256(raw).hexdigest()
            ):
                return False
        domain = _manifest_domain(
            base_version=manifest.base_version,
            policy_digest=manifest.policy_digest,
            entries=manifest.entries,
        )
        if manifest.payload_digest != hashlib.sha256(
            canonical_bundle_json_bytes(domain)
        ).hexdigest() or value.manifest_bytes != canonical_bundle_json_bytes(
            manifest, final_newline=True
        ):
            return False
        return hmac.compare_digest(
            value._authority_digest_value,
            _authority_digest("canonical-bundle", _canonical_authority_domain(value)),
        )
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        return False


def _is_rendered(value: object) -> bool:
    try:
        if (
            type(value) is not RenderedBundle
            or value._seal is not _BUNDLE_TOKEN
            or not _is_canonical(value.base)
        ):
            return False
        expected_version = derive_install_version(
            value.base.manifest.base_version, value.base.manifest.payload_digest
        )
        expected_bytes = dict(value.base.entry_bytes)
        expected_bytes[_PLUGIN_MANIFEST_PATH] = _render_plugin_manifest(
            expected_bytes[_PLUGIN_MANIFEST_PATH],
            value.base.manifest.base_version,
            expected_version,
        )
        expected_entries = tuple(
            BundleEntry(
                path=entry.path,
                file_type=entry.file_type,
                mode=entry.mode,
                size=len(expected_bytes[entry.path]),
                sha256=hashlib.sha256(expected_bytes[entry.path]).hexdigest(),
            )
            for entry in value.base.manifest.entries
        )
        expected_domain = {
            "base_payload_digest": value.base.manifest.payload_digest,
            "entries": [asdict(entry) for entry in expected_entries],
            "install_version": expected_version,
            "policy_digest": value.base.manifest.policy_digest,
            "transformation_profile": _TRANSFORMATION_PROFILE,
        }
        if (
            value.install_version != expected_version
            or value.transformation_profile != _TRANSFORMATION_PROFILE
            or value.entries != expected_entries
            or dict(value.entry_bytes) != expected_bytes
            or value.rendered_payload_digest
            != hashlib.sha256(canonical_bundle_json_bytes(expected_domain)).hexdigest()
        ):
            return False
        return hmac.compare_digest(
            value._authority_digest_value,
            _authority_digest("rendered-bundle", _rendered_authority_domain(value)),
        )
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        return False


def _is_staged(value: object) -> bool:
    try:
        return (
            type(value) is StagedMarketplace
            and value._seal is _BUNDLE_TOKEN
            and isinstance(value.marketplace_relative, SafeRelativePath)
            and isinstance(value.plugin_root_relative, SafeRelativePath)
            and isinstance(value.plugin_entries, tuple)
            and all(type(entry) is BundleEntry for entry in value.plugin_entries)
            and isinstance(value.bundle_manifest_bytes, bytes)
            and isinstance(value.rendered_payload_digest, str)
            and _DIGEST.fullmatch(value.rendered_payload_digest) is not None
            and isinstance(value.install_version, str)
            and type(value.evidence) is ArtifactEvidence
            and isinstance(value._stage_path, PathProof)
            and isinstance(value._stage_identity, tuple)
            and len(value._stage_identity) == 2
            and all(isinstance(item, int) for item in value._stage_identity)
            and hmac.compare_digest(
                value._authority_digest_value,
                _authority_digest(
                    "staged-marketplace", _staged_authority_domain(value)
                ),
            )
        )
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        return False


def _string_tuple(value: object, *, name: str) -> tuple[str, ...]:
    if (
        not isinstance(value, (list, tuple))
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise _policy_error(f"Bundle policy {name} is invalid.")
    result = cast(tuple[str, ...], tuple(value))
    if len(set(result)) != len(result):
        raise _policy_error(f"Bundle policy {name} contains duplicates.")
    return result


def _path_tuple(value: object, *, name: str) -> tuple[str, ...]:
    result = _string_tuple(value, name=name)
    validated = validate_reference_set(
        result, role=f"bundle-policy-{name}", limits=LIMIT_POLICY
    )
    if not validated.is_ok:
        raise _policy_error(f"Bundle policy {name} contains an unsafe path.")
    if result != tuple(sorted(result, key=lambda item: item.encode("utf-8"))):
        raise _policy_error(f"Bundle policy {name} is not canonically ordered.")
    return result


def _exact_mapping(
    value: object, keys: frozenset[str], *, name: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _policy_error(f"Bundle policy {name} fields are invalid.")
    if any(not isinstance(key, str) for key in value):
        raise _policy_error(f"Bundle policy {name} fields are invalid.")
    return cast(Mapping[str, object], value)


def _parse_policy_bytes(raw: bytes) -> _PolicyFields:
    try:
        record = decode_persistent_record(raw)
    except (ForgeError, OSError, TypeError, ValueError) as exc:
        raise _policy_error() from exc
    if set(record) != _POLICY_KEYS:
        raise _policy_error()
    if (
        record.get("schema_version") != "1.0"
        or record.get("schema_digest") != _POLICY_SCHEMA_DIGEST
        or record.get("policy_version") != "1.0"
        or record.get("limit_policy_version") != LIMIT_POLICY.version
        or record.get("writer_version") != VERSION
    ):
        raise _policy_error()
    policy_digest = record.get("record_digest")
    if not isinstance(policy_digest, str) or _DIGEST.fullmatch(policy_digest) is None:
        raise _policy_error()

    required_files = _path_tuple(record.get("required_files"), name="required_files")
    validation_only = _path_tuple(
        record.get("validation_only_files"), name="validation_only_files"
    )
    if set(required_files) & set(validation_only):
        raise _policy_error("Bundle policy file domains overlap.")
    allowed_roots = _string_tuple(record.get("allowed_roots"), name="allowed_roots")
    if any(_ALLOWED_ROOT.fullmatch(root) is None for root in allowed_roots):
        raise _policy_error("Bundle policy allowed roots are invalid.")
    for path in required_files:
        root = path.split("/", 1)[0] if "/" in path else "."
        if root not in allowed_roots:
            raise _policy_error("Bundle policy required path is outside allowed roots.")

    overlays_raw = _exact_mapping(
        record.get("generated_overlays"),
        frozenset(_GENERATED_OVERLAYS),
        name="generated_overlays",
    )
    if dict(overlays_raw) != _GENERATED_OVERLAYS:
        raise _policy_error("Bundle policy generated overlays are incompatible.")
    overlays = MappingProxyType(
        {key: cast(str, overlays_raw[key]) for key in sorted(overlays_raw)}
    )

    executable_files = _path_tuple(
        record.get("executable_files"), name="executable_files"
    )
    if not set(executable_files) <= set(required_files):
        raise _policy_error("Bundle policy executable paths are not required files.")

    limits_raw = _exact_mapping(
        record.get("limits"), frozenset(_LIMIT_REFERENCES), name="limits"
    )
    if dict(limits_raw) != _LIMIT_REFERENCES:
        raise _policy_error("Bundle policy limit references are incompatible.")
    limits = MappingProxyType(
        {key: cast(str, limits_raw[key]) for key in sorted(limits_raw)}
    )

    archive_raw = _exact_mapping(
        record.get("archive_profile"),
        frozenset(_ARCHIVE_PROFILE),
        name="archive_profile",
    )
    if dict(archive_raw) != dict(_ARCHIVE_PROFILE):
        raise _policy_error("Bundle policy archive profile is incompatible.")
    archive_profile = MappingProxyType(
        {key: cast(str | int, archive_raw[key]) for key in sorted(archive_raw)}
    )

    conditions_raw = _exact_mapping(
        record.get("required_conditions"),
        _CONDITION_KEYS,
        name="required_conditions",
    )
    conditions: dict[str, tuple[str, ...]] = {}
    for key in sorted(conditions_raw):
        selected = _path_tuple(conditions_raw[key], name=f"required_conditions.{key}")
        if not set(selected) <= set(required_files):
            raise _policy_error("Bundle policy condition path is not required.")
        conditions[key] = selected

    codexignore = _exact_mapping(
        record.get("codexignore"),
        frozenset({"lines", "path", "sha256"}),
        name="codexignore",
    )
    if codexignore.get("path") != ".codexignore":
        raise _policy_error("Bundle policy .codexignore path is invalid.")
    codexignore_lines = _string_tuple(
        codexignore.get("lines"), name="codexignore.lines"
    )
    if any("\n" in line or "\r" in line for line in codexignore_lines):
        raise _policy_error("Bundle policy .codexignore lines are invalid.")
    codexignore_digest = codexignore.get("sha256")
    expected_codexignore = ("\n".join(codexignore_lines) + "\n").encode("utf-8")
    if (
        not isinstance(codexignore_digest, str)
        or _DIGEST.fullmatch(codexignore_digest) is None
        or hashlib.sha256(expected_codexignore).hexdigest() != codexignore_digest
        or ".codexignore" not in validation_only
    ):
        raise _policy_error("Bundle policy .codexignore digest is invalid.")

    return _PolicyFields(
        policy_version="1.0",
        limit_policy_version=LIMIT_POLICY.version,
        policy_digest=policy_digest,
        required_files=required_files,
        validation_only_files=validation_only,
        allowed_roots=allowed_roots,
        generated_overlays=overlays,
        executable_files=executable_files,
        limits=limits,
        archive_profile=archive_profile,
        required_conditions=MappingProxyType(conditions),
        codexignore_lines=codexignore_lines,
        codexignore_digest=codexignore_digest,
    )


def _installed_policy_authority() -> tuple[bytes, _PolicyFields]:
    package = resources.files("zagrosi_forge.install")
    schema = package.joinpath(_POLICY_SCHEMA_RESOURCE).read_bytes()
    if hashlib.sha256(schema).hexdigest() != _POLICY_SCHEMA_DIGEST:
        raise _policy_error("The installed bundle policy schema has drifted.")
    raw = package.joinpath(_POLICY_RESOURCE).read_bytes()
    return raw, _parse_policy_bytes(raw)


def validate_bundle_policy_document(document: object) -> DiagnosticReport:
    """Validate an untrusted document without minting bundle authority."""

    try:
        if not isinstance(document, Mapping):
            raise _policy_error()
        raw = canonical_json_bytes(document, final_newline=True)
        _parse_policy_bytes(raw)
    except (ForgeError, OSError, TypeError, ValueError):
        return DiagnosticReport((_finding("bundle.policy_invalid", "bundle:policy"),))
    return DiagnosticReport(())


def load_trusted_bundle_policy() -> BundlePolicy:
    """Load and seal the installed positive bundle policy."""

    try:
        raw, policy_fields = _installed_policy_authority()
    except ForgeError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise _policy_error() from exc
    return BundlePolicy(policy_fields, raw, _token=_POLICY_TOKEN)


def validate_bundle_member_paths(
    paths: Iterable[str], policy: BundlePolicy
) -> tuple[str, ...]:
    """Reject portable collisions before comparing the positive inventory."""

    if not _is_policy(policy):
        raise _policy_error()
    selected = tuple(paths)
    if any(not isinstance(path, str) for path in selected):
        raise _error("bundle.unexpected_member", "Bundle member path is invalid.")
    keys = tuple(unicodedata.normalize("NFKC", path).casefold() for path in selected)
    if len(set(keys)) != len(keys):
        raise _error(
            "bundle.normalization_collision",
            "Bundle members collide under portable normalization.",
        )
    validated = validate_reference_set(
        selected, role="bundle-member", limits=LIMIT_POLICY
    )
    if not validated.is_ok or set(selected) != set(policy.required_files):
        raise _error(
            "bundle.unexpected_member", "Bundle inventory contains an unknown member."
        )
    return tuple(sorted(selected, key=lambda path: path.encode("utf-8")))


def normalize_bundle_mode(path: str, source_mode: int, policy: BundlePolicy) -> int:
    """Project host modes into the fixed portable regular-file classes."""

    if (
        not _is_policy(policy)
        or isinstance(source_mode, bool)
        or not isinstance(source_mode, int)
        or path not in policy.required_files
    ):
        raise _error("bundle.unexpected_member", "Bundle member is not declared.")
    return 0o755 if path in policy.executable_files else 0o644


def enforce_bundle_limits(entries: Iterable[BundleEntry], policy: BundlePolicy) -> None:
    """Apply the installed individual and aggregate bundle limits."""

    if not _is_policy(policy):
        raise _policy_error()
    selected = tuple(entries)
    if any(type(entry) is not BundleEntry for entry in selected):
        raise _error("bundle.unexpected_member", "Bundle entry type is invalid.")
    if (
        len(selected) > LIMIT_POLICY.value(policy.limits["files"])
        or any(
            entry.size > LIMIT_POLICY.value(policy.limits["member_bytes"])
            for entry in selected
        )
        or sum(entry.size for entry in selected)
        > LIMIT_POLICY.value(policy.limits["total_bytes"])
    ):
        raise _error("bundle.limit_exceeded", "Bundle content exceeds a trusted limit.")


def _snapshot_references(policy: BundlePolicy) -> tuple[SafeRelativePath, ...]:
    raw = tuple(
        sorted(
            (*policy.required_files, *policy.validation_only_files),
            key=lambda path: path.encode("utf-8"),
        )
    )
    result = validate_reference_set(raw, role="bundle-snapshot", limits=LIMIT_POLICY)
    if not result.is_ok:
        raise _policy_error("Bundle policy snapshot paths are invalid.")
    return result.unwrap()


def open_bundle_snapshot(source: SourceRoot, policy: BundlePolicy) -> SourceSnapshot:
    """Open exactly the trusted inventory and validate defense-only inputs."""

    if not isinstance(source, SourceRoot) or not _is_policy(policy):
        raise _policy_error()
    try:
        snapshot = source.open_snapshot(_snapshot_references(policy))
    except (ForgeError, OSError, TypeError, ValueError) as exc:
        raise _error(
            "bundle.unsafe_file_type",
            "A required bundle member is not a safe regular file.",
        ) from exc
    try:
        references = {reference.value: reference for reference in snapshot.references}
        codexignore = snapshot.read_bytes(
            references[".codexignore"],
            limit=LIMIT_POLICY.value(policy.limits["member_bytes"]),
        )
        expected = ("\n".join(policy.codexignore_lines) + "\n").encode("utf-8")
        if (
            codexignore != expected
            or hashlib.sha256(codexignore).hexdigest() != policy.codexignore_digest
        ):
            raise _policy_error("The candidate .codexignore has drifted.")
        return snapshot
    except ForgeError:
        snapshot.close()
        raise
    except (KeyError, OSError, TypeError, ValueError) as exc:
        snapshot.close()
        raise _policy_error("The candidate .codexignore cannot be validated.") from exc


def _read_snapshot(snapshot: SourceSnapshot, path: str, policy: BundlePolicy) -> bytes:
    references = {reference.value: reference for reference in snapshot.references}
    try:
        return snapshot.read_bytes(
            references[path],
            limit=LIMIT_POLICY.value(policy.limits["member_bytes"]),
        )
    except (ForgeError, KeyError, OSError, TypeError, ValueError) as exc:
        raise _error(
            "bundle.source_changed", "A bundle source member changed during read."
        ) from exc


def _assert_metadata_snapshot(
    package: ValidatedPackage, snapshot: SourceSnapshot, policy: BundlePolicy
) -> None:
    if snapshot.root_identity != package.source_snapshot.root_identity:
        raise _error("bundle.source_changed", "The bundle source root has changed.")
    full_references = {reference.value for reference in snapshot.references}
    for reference in package.references:
        if reference.value not in full_references:
            raise _error(
                "bundle.source_changed", "Validated source authority is absent."
            )
        try:
            validated_raw = package.source_snapshot.read_bytes(
                reference,
                limit=LIMIT_POLICY.value(policy.limits["member_bytes"]),
            )
        except (ForgeError, OSError, TypeError, ValueError) as exc:
            raise _error(
                "bundle.source_changed", "Validated package content has changed."
            ) from exc
        if validated_raw != _read_snapshot(snapshot, reference.value, policy):
            raise _error(
                "bundle.source_changed", "Validated package content has changed."
            )


def _skill_graph_error() -> ForgeError:
    return _error(
        "bundle.unexpected_member", "The declared skill reference graph is not closed."
    )


def _skill_local_references(
    raw: bytes, *, skill_root: str, declared: frozenset[str]
) -> tuple[str, ...]:
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise _skill_graph_error() from exc
    references: set[str] = set()
    for match in _LOCAL_SKILL_REFERENCE.finditer(text):
        token = match.group(1)
        if (
            "\\" in token
            or not token.startswith("references/")
            or not token.endswith(".md")
        ):
            raise _skill_graph_error()
        candidate = f"{skill_root}/{token}"
        validated = validate_reference(
            candidate, role="bundle-skill-reference", limits=LIMIT_POLICY
        )
        if not validated.is_ok or candidate not in declared:
            raise _skill_graph_error()
        references.add(candidate)
    reference_root = f"{skill_root}/references/"
    for candidate in declared:
        if not candidate.startswith(reference_root):
            continue
        name = candidate.removeprefix(reference_root)
        if "/" in name:
            raise _skill_graph_error()
        if re.search(
            rf"(?<![A-Za-z0-9._/-]){re.escape(name)}(?![A-Za-z0-9._/-])", text
        ):
            references.add(candidate)
    return tuple(sorted(references, key=str.encode))


def _validate_required_reference_graph(
    entry_bytes: Mapping[str, bytes], policy: BundlePolicy
) -> None:
    declared = frozenset(
        path for path in policy.required_files if path.startswith("skills/")
    )
    entrypoints = policy.required_conditions["skill_entrypoints"]
    reachable: set[str] = set()
    pending: list[tuple[str, str]] = []
    roots: set[str] = set()
    for entrypoint in entrypoints:
        parts = entrypoint.split("/")
        if len(parts) != 3 or parts[0] != "skills" or parts[2] != "SKILL.md":
            raise _skill_graph_error()
        skill_name = parts[1]
        skill_root = f"skills/{skill_name}"
        if skill_root in roots or entrypoint not in declared:
            raise _skill_graph_error()
        roots.add(skill_root)
        try:
            skill_text = entry_bytes[entrypoint].decode("utf-8", "strict")
        except (KeyError, UnicodeDecodeError) as exc:
            raise _skill_graph_error() from exc
        if tuple(_SKILL_FRONTMATTER_NAME.findall(skill_text)) != (skill_name,):
            raise _skill_graph_error()
        agent = f"{skill_root}/agents/openai.yaml"
        if agent not in declared:
            raise _skill_graph_error()
        try:
            agent_text = entry_bytes[agent].decode("utf-8", "strict")
        except (KeyError, UnicodeDecodeError) as exc:
            raise _skill_graph_error() from exc
        if tuple(_SKILL_PROMPT_REFERENCE.findall(agent_text)) != (skill_name,):
            raise _skill_graph_error()
        reachable.update((entrypoint, agent))
        pending.extend(((entrypoint, skill_root), (agent, skill_root)))

    while pending:
        path, skill_root = pending.pop()
        try:
            raw = entry_bytes[path]
        except KeyError as exc:
            raise _skill_graph_error() from exc
        for reference in _skill_local_references(
            raw, skill_root=skill_root, declared=declared
        ):
            if reference not in reachable:
                reachable.add(reference)
                pending.append((reference, skill_root))
    if reachable != set(declared):
        raise _skill_graph_error()


def _manifest_domain(
    *,
    base_version: str,
    policy_digest: str,
    entries: tuple[BundleEntry, ...],
) -> Mapping[str, object]:
    return {
        "aggregate_size": sum(entry.size for entry in entries),
        "base_version": base_version,
        "entries": [asdict(entry) for entry in entries],
        "normalization_profile": _NORMALIZATION_PROFILE,
        "policy_digest": policy_digest,
        "schema_version": "1.0",
    }


def enumerate_base_bundle(
    package: ValidatedPackage,
    snapshot: SourceSnapshot,
    policy: BundlePolicy,
) -> CanonicalBundle:
    """Read the exact opened inventory and construct its canonical base identity."""

    if (
        not isinstance(package, ValidatedPackage)
        or not isinstance(snapshot, SourceSnapshot)
        or not _is_policy(policy)
    ):
        raise _policy_error("Bundle enumeration inputs are not authoritative.")
    expected_snapshot = set((*policy.required_files, *policy.validation_only_files))
    if {reference.value for reference in snapshot.references} != expected_snapshot:
        raise _error(
            "bundle.unexpected_member", "Bundle snapshot inventory is not exact."
        )
    candidate_policy = _read_snapshot(snapshot, _POLICY_PATH, policy)
    if candidate_policy != policy._resource_bytes:
        raise _policy_error("The candidate bundle policy differs from installed trust.")

    _assert_metadata_snapshot(package, snapshot, policy)

    entry_bytes: dict[str, bytes] = {}
    entries: list[BundleEntry] = []
    for path in policy.required_files:
        raw = _read_snapshot(snapshot, path, policy)
        entry_bytes[path] = raw
        entries.append(
            BundleEntry(
                path=path,
                file_type="regular",
                mode=normalize_bundle_mode(path, 0, policy),
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        )
    ordered_entries = tuple(entries)
    _validate_required_reference_graph(entry_bytes, policy)
    enforce_bundle_limits(ordered_entries, policy)

    domain = _manifest_domain(
        base_version=package.base_release_version,
        policy_digest=policy.policy_digest,
        entries=ordered_entries,
    )
    payload_digest = hashlib.sha256(canonical_bundle_json_bytes(domain)).hexdigest()
    manifest = BundleManifest(
        schema_version="1.0",
        base_version=package.base_release_version,
        policy_digest=policy.policy_digest,
        entries=ordered_entries,
        aggregate_size=sum(entry.size for entry in ordered_entries),
        payload_digest=payload_digest,
        builder_version=VERSION,
        normalization_profile=_NORMALIZATION_PROFILE,
    )
    selected = package.marketplace.plugins[0]
    marketplace = _MarketplaceProjection(
        marketplace_name=package.marketplace.name,
        display_name=package.marketplace.display_name,
        plugin_name=selected.name,
        category=selected.category,
        installation_policy=selected.installation_policy,
        authentication_policy=selected.authentication_policy,
    )
    return CanonicalBundle(
        manifest=manifest,
        manifest_bytes=canonical_bundle_json_bytes(manifest, final_newline=True),
        entry_bytes=entry_bytes,
        source_snapshot_identity=package.source_snapshot_identity,
        marketplace=marketplace,
        _token=_BUNDLE_TOKEN,
    )


def _decode_json_object(raw: bytes) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, object]:
        rendered: dict[str, object] = {}
        for key, value in pairs:
            if key in rendered:
                raise ValueError("duplicate key")
            rendered[key] = value
        return rendered

    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _error(
            "bundle.render_transform_invalid", "Plugin manifest is not valid JSON."
        ) from exc
    if not isinstance(value, dict):
        raise _error(
            "bundle.render_transform_invalid", "Plugin manifest root is invalid."
        )
    return value


def _render_plugin_manifest(
    raw: bytes, base_version: str, install_version: str
) -> bytes:
    before = _decode_json_object(raw)
    if before.get("version") != base_version:
        raise _error(
            "bundle.render_transform_invalid", "Plugin manifest version is invalid."
        )
    literal = json.dumps(base_version, ensure_ascii=False).encode("utf-8")
    pattern = re.compile(rb'("version"\s*:\s*)' + re.escape(literal))
    matches = tuple(pattern.finditer(raw))
    if len(matches) != 1:
        raise _error(
            "bundle.render_transform_invalid",
            "Plugin manifest version transformation is ambiguous.",
        )
    match = matches[0]
    replacement = json.dumps(install_version, ensure_ascii=False).encode("utf-8")
    rendered = (
        raw[: match.start(0)] + match.group(1) + replacement + raw[match.end(0) :]
    )
    after = _decode_json_object(rendered)
    expected = dict(before)
    expected["version"] = install_version
    if after != expected:
        raise _error(
            "bundle.render_transform_invalid",
            "Plugin manifest transformation changed undeclared content.",
        )
    return rendered


def _rendered_domain(rendered: RenderedBundle) -> Mapping[str, object]:
    return {
        "base_payload_digest": rendered.base.manifest.payload_digest,
        "entries": [asdict(entry) for entry in rendered.entries],
        "install_version": rendered.install_version,
        "policy_digest": rendered.base.manifest.policy_digest,
        "transformation_profile": rendered.transformation_profile,
    }


def derive_install_projection(base: CanonicalBundle) -> RenderedBundle:
    """Apply the one declared local manifest-version transformation."""

    if not _is_canonical(base):
        raise _error("bundle.render_transform_invalid", "Base bundle is not trusted.")
    install_version = derive_install_version(
        base.manifest.base_version, base.manifest.payload_digest
    )
    entry_bytes = dict(base.entry_bytes)
    entry_bytes[_PLUGIN_MANIFEST_PATH] = _render_plugin_manifest(
        entry_bytes[_PLUGIN_MANIFEST_PATH],
        base.manifest.base_version,
        install_version,
    )
    entries = tuple(
        BundleEntry(
            path=entry.path,
            file_type=entry.file_type,
            mode=entry.mode,
            size=len(entry_bytes[entry.path]),
            sha256=hashlib.sha256(entry_bytes[entry.path]).hexdigest(),
        )
        for entry in base.manifest.entries
    )
    provisional = RenderedBundle(
        base=base,
        install_version=install_version,
        entries=entries,
        entry_bytes=entry_bytes,
        rendered_payload_digest="0" * 64,
        _token=_BUNDLE_TOKEN,
    )
    digest = hashlib.sha256(
        canonical_bundle_json_bytes(_rendered_domain(provisional))
    ).hexdigest()
    return RenderedBundle(
        base=base,
        install_version=install_version,
        entries=entries,
        entry_bytes=entry_bytes,
        rendered_payload_digest=digest,
        _token=_BUNDLE_TOKEN,
    )


def _rendered_manifest(rendered: RenderedBundle) -> BundleManifest:
    return BundleManifest(
        schema_version="1.0",
        base_version=rendered.base.manifest.base_version,
        policy_digest=rendered.base.manifest.policy_digest,
        entries=rendered.entries,
        aggregate_size=sum(entry.size for entry in rendered.entries),
        payload_digest=rendered.rendered_payload_digest,
        builder_version=rendered.base.manifest.builder_version,
        normalization_profile=_NORMALIZATION_PROFILE,
    )


def _marketplace_bytes(rendered: RenderedBundle) -> bytes:
    marketplace = rendered.base.marketplace
    return canonical_bundle_json_bytes(
        {
            "interface": {"displayName": marketplace.display_name},
            "name": marketplace.marketplace_name,
            "plugins": [
                {
                    "category": marketplace.category,
                    "name": marketplace.plugin_name,
                    "policy": {
                        "authentication": marketplace.authentication_policy,
                        "installation": marketplace.installation_policy,
                    },
                    "source": {
                        "path": f"./{_PLUGIN_ROOT}",
                        "source": "local",
                    },
                }
            ],
        },
        final_newline=True,
    )


def _stage_reference(raw: str) -> SafeRelativePath:
    result = validate_reference(raw, role="bundle-stage", limits=LIMIT_POLICY)
    if not result.is_ok:
        raise _error("bundle.stage_invalid", "Generated stage path is invalid.")
    return result.unwrap()


def _stage_files(rendered: RenderedBundle) -> dict[str, tuple[bytes, int]]:
    bundle_manifest_bytes = canonical_bundle_json_bytes(
        _rendered_manifest(rendered), final_newline=True
    )
    files: dict[str, tuple[bytes, int]] = {
        _MARKETPLACE_PATH: (_marketplace_bytes(rendered), 0o644),
        f"{_PLUGIN_ROOT}/{_BUNDLE_MANIFEST_PATH}": (bundle_manifest_bytes, 0o644),
    }
    for entry in rendered.entries:
        files[f"{_PLUGIN_ROOT}/{entry.path}"] = (
            rendered.entry_bytes[entry.path],
            entry.mode,
        )
    return files


def _stage_directories(files: Mapping[str, object]) -> frozenset[str]:
    directories: set[str] = set()
    for relative in files:
        components = relative.split("/")
        directories.update(
            "/".join(components[:index]) for index in range(1, len(components))
        )
    return frozenset(directories)


def _read_posix_bytes(descriptor: int, *, limit: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while offset <= limit:
        chunk = os.pread(descriptor, min(64 * 1024, limit + 1 - offset), offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    rendered = b"".join(chunks)
    if len(rendered) > limit:
        raise _error("bundle.limit_exceeded", "The staged bundle exceeds its limit.")
    return rendered


def _read_posix_stage(
    path: PathProof,
    *,
    expected_identity: tuple[int, int],
    maximum_entries: int,
) -> tuple[Mapping[str, _ObservedStageFile], frozenset[str]]:
    root = path._duplicate_descriptor()
    member_limit = LIMIT_POLICY.value("bundle_member_bytes")
    files: dict[str, _ObservedStageFile] = {}
    directories: set[str] = set()
    consumed = 0

    def walk(descriptor: int, prefix: str, depth: int) -> None:
        nonlocal consumed
        if depth > LIMIT_POLICY.value("path_components"):
            raise _error("bundle.limit_exceeded", "The staged bundle is too deep.")
        before_directory = os.fstat(descriptor)
        if (
            not _paths._private_directory(descriptor, before_directory, exact=True)
            or before_directory.st_dev != expected_identity[0]
            or not path._filesystem_guard(descriptor)
        ):
            raise _error("bundle.digest_mismatch", "The staged directory is unsafe.")
        with os.scandir(descriptor) as iterator:
            names = [entry.name for entry in iterator]
        for name in sorted(names, key=lambda value: value.encode("utf-8")):
            consumed += 1
            if consumed > maximum_entries:
                raise _error(
                    "bundle.digest_mismatch", "The staged inventory is not exact."
                )
            relative = f"{prefix}/{name}" if prefix else name
            reference = _stage_reference(relative)
            status = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            identity = (status.st_dev, status.st_ino)
            if stat.S_ISDIR(status.st_mode) and not stat.S_ISLNK(status.st_mode):
                child = _paths._open_directory_component(
                    descriptor,
                    name,
                    linked_code="path.linked_ancestor",
                    missing_code="path.missing",
                )
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != identity:
                        raise _error(
                            "bundle.digest_mismatch",
                            "A staged directory identity changed.",
                        )
                    directories.add(reference.value)
                    walk(child, reference.value, depth + 1)
                finally:
                    os.close(child)
                current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != identity:
                    raise _error(
                        "bundle.digest_mismatch", "A staged directory identity changed."
                    )
                continue
            if not stat.S_ISREG(status.st_mode):
                raise _error("bundle.digest_mismatch", "A staged member is unsafe.")
            opened_file = os.open(name, _paths._posix_file_flags(), dir_fd=descriptor)
            try:
                before = os.fstat(opened_file)
                if (
                    (before.st_dev, before.st_ino) != identity
                    or before.st_dev != expected_identity[0]
                    or before.st_nlink != 1
                    or before.st_uid != os.geteuid()
                    or not path._filesystem_guard(opened_file)
                    or not _paths._posix_security_metadata_supported(
                        opened_file, before
                    )
                    or before.st_size > member_limit
                ):
                    raise _error("bundle.digest_mismatch", "A staged member is unsafe.")
                raw = _read_posix_bytes(opened_file, limit=member_limit)
                after = os.fstat(opened_file)
                if (
                    (after.st_dev, after.st_ino) != identity
                    or _paths._posix_status_fingerprint(after)
                    != _paths._posix_status_fingerprint(before)
                    or len(raw) != before.st_size
                ):
                    raise _error(
                        "bundle.digest_mismatch", "A staged member changed during read."
                    )
            finally:
                os.close(opened_file)
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != identity or not stat.S_ISREG(
                current.st_mode
            ):
                raise _error(
                    "bundle.digest_mismatch", "A staged member identity changed."
                )
            files[reference.value] = _ObservedStageFile(
                raw=raw,
                mode=stat.S_IMODE(before.st_mode),
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        after_directory = os.fstat(descriptor)
        if (after_directory.st_dev, after_directory.st_ino) != (
            before_directory.st_dev,
            before_directory.st_ino,
        ) or _paths._posix_status_fingerprint(
            after_directory
        ) != _paths._posix_status_fingerprint(before_directory):
            raise _error(
                "bundle.digest_mismatch", "A staged directory changed during read."
            )

    try:
        root_status = os.fstat(root)
        if (root_status.st_dev, root_status.st_ino) != expected_identity:
            raise _error("bundle.digest_mismatch", "The staged root identity changed.")
        walk(root, "", 0)
        rebound = path._duplicate_descriptor()
        try:
            status = os.fstat(rebound)
            if (status.st_dev, status.st_ino) != expected_identity:
                raise _error(
                    "bundle.digest_mismatch", "The staged root identity changed."
                )
        finally:
            os.close(rebound)
    finally:
        os.close(root)
    return MappingProxyType(files), frozenset(directories)


def _read_windows_stage(
    path: PathProof,
    *,
    expected_identity: tuple[int, int],
    maximum_entries: int,
) -> tuple[Mapping[str, _ObservedStageFile], frozenset[str]]:
    from . import ownership as _ownership

    root = path._duplicate_descriptor()
    member_limit = LIMIT_POLICY.value("bundle_member_bytes")
    files: dict[str, _ObservedStageFile] = {}
    directories: set[str] = set()
    consumed = 0

    def walk(handle: int, prefix: str, depth: int) -> None:
        nonlocal consumed
        if depth > LIMIT_POLICY.value("path_components"):
            raise _error("bundle.limit_exceeded", "The staged bundle is too deep.")
        before_directory = _paths._windows_handle_status(handle)
        if (
            not before_directory.is_directory
            or before_directory.is_reparse
            or before_directory.identity[0] != expected_identity[0]
            or not path._filesystem_guard(handle)
            or not _paths._windows_private_directory(handle, exact=True)
        ):
            raise _error("bundle.digest_mismatch", "The staged directory is unsafe.")
        remaining = maximum_entries - consumed
        names = _ownership._windows_list_names(handle, limit=max(remaining, 0))
        for name in names:
            consumed += 1
            if consumed > maximum_entries:
                raise _error(
                    "bundle.digest_mismatch", "The staged inventory is not exact."
                )
            relative = f"{prefix}/{name}" if prefix else name
            reference = _stage_reference(relative)
            child = _paths._windows_open_child(
                handle, name, directory=None, read_data=True
            )
            try:
                before = _paths._windows_handle_status(child)
                if (
                    before.is_reparse
                    or before.identity[0] != expected_identity[0]
                    or not path._filesystem_guard(child)
                ):
                    raise _error("bundle.digest_mismatch", "A staged member is unsafe.")
                if before.is_directory:
                    if not _paths._windows_private_directory(child, exact=True):
                        raise _error(
                            "bundle.digest_mismatch", "A staged directory is unsafe."
                        )
                    directories.add(reference.value)
                    walk(child, reference.value, depth + 1)
                else:
                    if (
                        before.link_count != 1
                        or before.size > member_limit
                        or not _paths._windows_private_authorization(child, exact=True)
                    ):
                        raise _error(
                            "bundle.digest_mismatch", "A staged member is unsafe."
                        )
                    raw = _paths._windows_read(child, limit=member_limit)
                    after = _paths._windows_handle_status(child)
                    if (
                        after.fingerprint != before.fingerprint
                        or len(raw) != before.size
                    ):
                        raise _error(
                            "bundle.digest_mismatch",
                            "A staged member changed during read.",
                        )
                    files[reference.value] = _ObservedStageFile(
                        raw=raw,
                        mode=None,
                        size=len(raw),
                        sha256=hashlib.sha256(raw).hexdigest(),
                    )
            finally:
                _paths._windows_close(child)
            current = _paths._windows_open_child(
                handle, name, directory=before.is_directory
            )
            try:
                if _paths._windows_handle_status(current).identity != before.identity:
                    raise _error(
                        "bundle.digest_mismatch", "A staged member identity changed."
                    )
            finally:
                _paths._windows_close(current)
        after_directory = _paths._windows_handle_status(handle)
        if after_directory.fingerprint != before_directory.fingerprint:
            raise _error(
                "bundle.digest_mismatch", "A staged directory changed during read."
            )

    try:
        if _paths._windows_handle_status(root).identity != expected_identity:
            raise _error("bundle.digest_mismatch", "The staged root identity changed.")
        walk(root, "", 0)
        rebound = path._duplicate_descriptor()
        try:
            if _paths._windows_handle_status(rebound).identity != expected_identity:
                raise _error(
                    "bundle.digest_mismatch", "The staged root identity changed."
                )
        finally:
            _paths._windows_close(rebound)
    finally:
        _paths._windows_close(root)
    return MappingProxyType(files), frozenset(directories)


def _read_staged_inventory(
    path: PathProof,
    *,
    expected_identity: tuple[int, int],
    maximum_entries: int,
) -> tuple[Mapping[str, _ObservedStageFile], frozenset[str]]:
    if os.name == "nt":
        return _read_windows_stage(
            path,
            expected_identity=expected_identity,
            maximum_entries=maximum_entries,
        )
    return _read_posix_stage(
        path,
        expected_identity=expected_identity,
        maximum_entries=maximum_entries,
    )


def stage_marketplace(
    rendered: RenderedBundle, destination: object
) -> StagedMarketplace:
    """Write the exact rendered marketplace through transaction-owned authority."""

    from .ownership import OwnershipProof

    if not _is_rendered(rendered) or not isinstance(destination, OwnershipProof):
        raise _error(
            "bundle.stage_invalid", "Stage destination is not transaction-owned."
        )
    observed = destination.observed
    path = getattr(observed, "path", None)
    if (
        not isinstance(path, PathProof)
        or not hasattr(observed, "transaction_id")
        or path.leaf_identity is None
    ):
        raise _error(
            "bundle.stage_invalid", "Stage destination is not transaction-owned."
        )

    bundle_manifest_bytes = canonical_bundle_json_bytes(
        _rendered_manifest(rendered), final_newline=True
    )
    files = _stage_files(rendered)

    try:
        writer_result = path._open_owned_directory_writer()
        if not writer_result.is_ok:
            raise cast(ForgeError, writer_result.error)
        with writer_result.unwrap() as writer:
            for relative, (raw, mode) in sorted(
                files.items(), key=lambda item: item[0].encode("utf-8")
            ):
                result = writer.write_regular_file(
                    _stage_reference(relative), raw, mode=mode
                )
                if not result.is_ok:
                    raise cast(ForgeError, result.error)
    except (ForgeError, OSError, TypeError, ValueError) as exc:
        raise _error(
            "bundle.stage_invalid", "Bundle stage could not be written safely."
        ) from exc

    manifest_digest = hashlib.sha256(bundle_manifest_bytes).hexdigest()
    evidence = ArtifactEvidence(
        payload_digest=rendered.rendered_payload_digest,
        manifest_digest=manifest_digest,
        member_count=len(rendered.entries),
        aggregate_size=sum(entry.size for entry in rendered.entries),
    )
    return StagedMarketplace(
        marketplace_relative=_stage_reference(_MARKETPLACE_PATH),
        plugin_root_relative=_stage_reference(_PLUGIN_ROOT),
        plugin_entries=rendered.entries,
        bundle_manifest_bytes=bundle_manifest_bytes,
        rendered_payload_digest=rendered.rendered_payload_digest,
        install_version=rendered.install_version,
        evidence=evidence,
        stage_path=path,
        stage_identity=path.leaf_identity,
        _token=_BUNDLE_TOKEN,
    )


def verify_staged_marketplace(
    staged: StagedMarketplace, expected: RenderedBundle
) -> ArtifactEvidence:
    """Verify that staged evidence is bound to the exact rendered projection."""

    expected_manifest = (
        canonical_bundle_json_bytes(_rendered_manifest(expected), final_newline=True)
        if _is_rendered(expected)
        else b""
    )
    expected_evidence = (
        ArtifactEvidence(
            payload_digest=expected.rendered_payload_digest,
            manifest_digest=hashlib.sha256(expected_manifest).hexdigest(),
            member_count=len(expected.entries),
            aggregate_size=sum(entry.size for entry in expected.entries),
        )
        if _is_rendered(expected)
        else None
    )
    if (
        not _is_staged(staged)
        or not _is_rendered(expected)
        or not isinstance(staged._stage_path, PathProof)
        or staged._stage_path.leaf_identity != staged._stage_identity
        or staged.marketplace_relative.value != _MARKETPLACE_PATH
        or staged.plugin_root_relative.value != _PLUGIN_ROOT
        or staged.plugin_entries != expected.entries
        or staged.rendered_payload_digest != expected.rendered_payload_digest
        or staged.install_version != expected.install_version
        or staged.bundle_manifest_bytes != expected_manifest
        or staged.evidence != expected_evidence
    ):
        raise _error("bundle.digest_mismatch", "Staged marketplace identity differs.")

    expected_files = _stage_files(expected)
    expected_directories = _stage_directories(expected_files)
    try:
        observed_files, observed_directories = _read_staged_inventory(
            staged._stage_path,
            expected_identity=staged._stage_identity,
            maximum_entries=len(expected_files) + len(expected_directories),
        )
    except (ForgeError, OSError, TypeError, UnicodeError, ValueError) as exc:
        raise _error(
            "bundle.digest_mismatch", "Staged marketplace content differs."
        ) from exc
    if (
        set(observed_files) != set(expected_files)
        or observed_directories != expected_directories
    ):
        raise _error("bundle.digest_mismatch", "Staged marketplace content differs.")
    for relative, (raw, mode) in expected_files.items():
        observed_file = observed_files[relative]
        if (
            observed_file.raw != raw
            or observed_file.size != len(raw)
            or observed_file.sha256 != hashlib.sha256(raw).hexdigest()
            or (os.name != "nt" and observed_file.mode != mode)
        ):
            raise _error(
                "bundle.digest_mismatch", "Staged marketplace content differs."
            )
    return staged.evidence
