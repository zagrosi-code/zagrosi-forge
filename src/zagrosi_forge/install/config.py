"""Pure, ownership-aware semantic planning for Codex config.toml."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
import copy
import ctypes
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
import errno
import hashlib
import hmac
import json
import math
import os
import re
import stat
import sys
import tomllib
from typing import Any, Never, cast

from zagrosi_forge._vendor import tomlkit

from . import paths as _paths
from .contracts import (
    Finding,
    ForgeError,
    InstallIdentity,
    ManagedConfigNode,
    ManagedConfigValueKind,
    Result,
    canonical_json_bytes,
    install_identity_digest,
)
from .ownership import (
    LegacyInstallCatalog,
    LegacyRecognition,
    ValidatedInstallRelation,
    match_legacy_install,
)
from .paths import ConfigPathProof, PlannedOwnedPath, SafeRelativePath
from .policies import LIMIT_POLICY, LimitPolicy


_SNAPSHOT_TOKEN = object()
_PLAN_TOKEN = object()
_CANDIDATE_TOKEN = object()
_DIGEST = "0123456789abcdef"
_ALTERNATE_ID = re.compile(r"zagrosi-local-[0-9a-f]{24}\Z")
_RUNNER_VERSION = "0.2.0"
_POLICY_VERSION = "1.0"
_XATTR_VALUE_LIMIT = 64 * 1024
_MANUAL_CONFIG_REMEDIATION = (
    "redacted manual config: preserve all other nodes; set only "
    "marketplaces.<effective-id>.source_type=local, "
    "marketplaces.<effective-id>.source=<immutable-owned-source>, and "
    'plugins."zagrosi-forge@<effective-id>".enabled=true'
)


class ConfigClassification(str, Enum):
    EXACT_MANAGED = "exact_managed"
    RECOGNIZED_LEGACY = "recognized_legacy"
    UNMANAGED_COLLISION = "unmanaged_collision"
    ABSENT = "absent"
    INVALID_UNSUPPORTED = "invalid_unsupported"


class ConfigOperation(str, Enum):
    NO_OP = "no_op"
    ADD = "add"
    UPDATE_OWNED = "update_owned"
    ADOPT_RECOGNIZED_LEGACY = "adopt_recognized_legacy"
    COLLISION_ALTERNATIVE = "collision_alternative"
    REJECT = "reject"


class CollisionPolicy(str, Enum):
    REJECT = "reject"
    ALTERNATE = "alternate"


@dataclass(frozen=True, slots=True)
class _TomlTaggedScalar:
    kind: str
    value: str


@dataclass(frozen=True, slots=True)
class ConfigMetadataPolicy:
    """Closed ordinary-user metadata profile supported by v1."""

    version: str = "1.0"
    posix_modes: tuple[int, ...] = (0o600, 0o644)

    def __post_init__(self) -> None:
        if self.version != "1.0" or self.posix_modes != (0o600, 0o644):
            raise ValueError("config metadata policy")


CONFIG_METADATA_POLICY = ConfigMetadataPolicy()


def _error(code: str, message: str) -> ForgeError:
    if code == "config.representation_unsupported":
        finding = Finding(
            code=code,
            severity="error",
            message="The managed config projection requires a manual edit.",
            subject="config.managed_projection",
            authority="zagrosi-forge-config-policy",
            authority_version=_POLICY_VERSION,
            remediation=_MANUAL_CONFIG_REMEDIATION,
            details={"preview": _MANUAL_CONFIG_REMEDIATION},
        )
        return ForgeError(
            code,
            13,
            message,
            findings=(finding,),
            recovery_instructions=(_MANUAL_CONFIG_REMEDIATION,),
        )
    return ForgeError(
        code,
        13,
        message,
        recovery_instructions=("Refresh the config plan and retry safely.",),
    )


def _is_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in _DIGEST for character in value)
    )


def _trusted_limits(limits: object) -> bool:
    if type(limits) is not LimitPolicy or limits.version != LIMIT_POLICY.version:
        return False
    try:
        return set(limits.values) == set(LIMIT_POLICY.values) and all(
            type(value) is int and 0 < value <= LIMIT_POLICY.value(name)
            for name, value in limits.values.items()
        )
    except (ForgeError, TypeError, ValueError):
        return False


def _limit_binding(limits: LimitPolicy) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(limits.values.items()))


def _trusted_metadata_policy(policy: object) -> bool:
    return (
        type(policy) is ConfigMetadataPolicy
        and policy.version == CONFIG_METADATA_POLICY.version
        and policy.posix_modes == CONFIG_METADATA_POLICY.posix_modes
    )


def _semantic_value(
    value: object, *, depth: int, counters: list[int], limits: LimitPolicy
) -> object:
    if depth > limits.value("toml_depth"):
        raise _error("config.limit_exceeded", "Config semantic depth exceeds policy.")
    counters[0] += 1
    if counters[0] > limits.value("toml_nodes"):
        raise _error("config.limit_exceeded", "Config semantic size exceeds policy.")
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if math.isnan(value):
            return _TomlTaggedScalar("float", "nan")
        if math.isinf(value):
            return _TomlTaggedScalar("float", "+inf" if value > 0 else "-inf")
        return value
    if isinstance(value, (datetime, date, time)):
        return _TomlTaggedScalar(type(value).__name__, value.isoformat())
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise _error("config.parse_failed", "Config contains an invalid key.")
        return {
            cast(str, key): _semantic_value(
                item, depth=depth + 1, counters=counters, limits=limits
            )
            for key, item in sorted(value.items(), key=lambda pair: cast(str, pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [
            _semantic_value(item, depth=depth + 1, counters=counters, limits=limits)
            for item in value
        ]
    raise _error("config.parse_failed", "Config contains an unsupported TOML value.")


def _semantic_tree(
    value: Mapping[str, object], limits: LimitPolicy
) -> Mapping[str, object]:
    normalized = _semantic_value(value, depth=0, counters=[0], limits=limits)
    if not isinstance(normalized, Mapping):
        raise _error("config.parse_failed", "Config root is invalid.")
    return cast(Mapping[str, object], normalized)


def _semantic_digest(value: Mapping[str, object]) -> str:
    def canonical(item: object) -> object:
        if isinstance(item, _TomlTaggedScalar):
            return ["tagged", item.kind, item.value]
        if item is None:
            return ["none"]
        if type(item) is bool:
            return ["bool", item]
        if type(item) is int:
            return ["int", str(item)]
        if type(item) is float:
            return ["float", item.hex()]
        if type(item) is str:
            return ["str", item]
        if isinstance(item, Mapping):
            entries: list[list[object]] = []
            for key, child in item.items():
                if type(key) is not str:
                    raise TypeError("normalized TOML key is not a string")
                entries.append([key, canonical(child)])
            entries.sort(key=lambda entry: cast(str, entry[0]))
            return ["map", entries]
        if isinstance(item, (list, tuple)):
            return ["list", [canonical(child) for child in item]]
        raise TypeError("unsupported normalized TOML semantic value")

    encoded = json.dumps(
        canonical(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _trusted_identity(value: object) -> bool:
    if type(value) is not InstallIdentity:
        return False
    identity = value
    try:
        rebuilt = InstallIdentity(
            marketplace_id=identity.marketplace_id,
            plugin_id=identity.plugin_id,
            base_version=identity.base_version,
            install_version=identity.install_version,
            base_payload_digest=identity.base_payload_digest,
            rendered_payload_digest=identity.rendered_payload_digest,
            policy_digest=identity.policy_digest,
            transformation_profile=identity.transformation_profile,
            contract_versions=identity.contract_versions,
        )
    except (TypeError, ValueError):
        return False
    return (
        rebuilt == identity
        and identity.marketplace_id == "zagrosi"
        and identity.plugin_id == "zagrosi-forge"
    )


def _trusted_source_reference(value: object) -> bool:
    if type(value) is not PlannedOwnedPath:
        return False
    source = value
    try:
        source._require_current()
        namespace = source._namespace
        expected_native = os.path.join(
            source._home_native,
            "plugins",
            *source._relative.components,
        )
        return (
            _paths._safe_reference_invariants(source._relative)
            and len(source._relative.components) == source._expected_depth
            and source._native_source == expected_native
            and source._root_identity == namespace._plugins_identity
            and source._home_identity == namespace._home_identity
            and source._filesystem_guard is namespace._filesystem_guard
            and source._windows is namespace._windows
            and not namespace._closed
        )
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        return False


def _parse_dual(
    raw: bytes, limits: LimitPolicy
) -> tuple[Any, Mapping[str, object], str]:
    if type(raw) is not bytes or len(raw) > limits.value("toml_bytes"):
        raise _error("config.limit_exceeded", "Config bytes exceed policy.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _error("config.parse_failed", "Config is not valid UTF-8.") from None
    try:
        kit_document = tomlkit.parse(text)
        stdlib_document = tomllib.loads(text)
    except RecursionError:
        raise _error(
            "config.limit_exceeded", "Config nesting exceeds policy."
        ) from None
    except (tomllib.TOMLDecodeError, ValueError, TypeError):
        raise _error("config.parse_failed", "Config TOML parsing failed.") from None
    try:
        kit_semantic = _semantic_tree(
            cast(Mapping[str, object], kit_document.unwrap()), limits
        )
        stdlib_semantic = _semantic_tree(
            cast(Mapping[str, object], stdlib_document), limits
        )
    except RecursionError:
        raise _error(
            "config.limit_exceeded", "Config nesting exceeds policy."
        ) from None
    except (AttributeError, TypeError, ValueError):
        raise _error(
            "config.parse_failed", "Config TOML semantics are invalid."
        ) from None
    if kit_semantic != stdlib_semantic:
        raise _error(
            "config.parser_disagreement", "Trusted TOML parsers disagree on config."
        )
    return kit_document, stdlib_semantic, text


def _without_managed(
    semantic: Mapping[str, object], effective_id: str
) -> Mapping[str, object]:
    rendered = copy.deepcopy(dict(semantic))
    plugin_key = f"zagrosi-forge@{effective_id}"
    marketplaces = rendered.get("marketplaces")
    if isinstance(marketplaces, dict):
        marketplace = marketplaces.get(effective_id)
        if isinstance(marketplace, dict):
            marketplace.pop("source_type", None)
            marketplace.pop("source", None)
            if not marketplace:
                marketplaces.pop(effective_id, None)
        if not marketplaces:
            rendered.pop("marketplaces", None)
    plugins = rendered.get("plugins")
    if isinstance(plugins, dict):
        plugin = plugins.get(plugin_key)
        if isinstance(plugin, dict):
            plugin.pop("enabled", None)
            if not plugin:
                plugins.pop(plugin_key, None)
        if not plugins:
            rendered.pop("plugins", None)
    return rendered


def _descriptor_xattr_value(descriptor: int, name: bytes) -> bytes:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        function = libc.fgetxattr
    except AttributeError as exc:
        raise OSError(
            errno.ENOSYS,
            "descriptor xattr reads are unavailable",
        ) from exc
    if sys.platform == "darwin":
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.c_int,
        ]
        suffix: tuple[object, ...] = (0, 0)
    elif sys.platform.startswith("linux"):
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        suffix = ()
    else:
        raise OSError(errno.ENOSYS, "descriptor xattr reads are unavailable")
    function.restype = ctypes.c_ssize_t
    for _attempt in range(3):
        size = int(function(descriptor, name, None, 0, *suffix))
        if size < 0:
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number))
        if size > _XATTR_VALUE_LIMIT:
            raise OSError(errno.E2BIG, "extended attribute value exceeds policy")
        if size == 0:
            return b""
        buffer = ctypes.create_string_buffer(size)
        result = int(function(descriptor, name, ctypes.byref(buffer), size, *suffix))
        if result >= 0:
            return bytes(buffer.raw[:result])
        number = ctypes.get_errno()
        if number != errno.ERANGE:
            raise OSError(number, os.strerror(number))
    raise OSError(errno.ERANGE, "extended attribute changed during inspection")


def _descriptor_xattrs(descriptor: int) -> tuple[tuple[bytes, bytes], ...]:
    for _attempt in range(3):
        names = _paths._descriptor_xattr_names(descriptor)
        observed = tuple(
            (name, _descriptor_xattr_value(descriptor, name)) for name in names
        )
        if names == _paths._descriptor_xattr_names(descriptor) and observed == tuple(
            (name, _descriptor_xattr_value(descriptor, name)) for name in names
        ):
            if sum(len(name) + len(value) for name, value in observed) > (
                _XATTR_VALUE_LIMIT
            ):
                raise OSError(
                    errno.E2BIG,
                    "extended attribute metadata exceeds policy",
                )
            return observed
    raise OSError(errno.ERANGE, "extended attributes changed during inspection")


def _xattr_projection(
    xattrs: tuple[tuple[bytes, bytes], ...],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (name.decode("ascii"), hashlib.sha256(value).hexdigest())
        for name, value in xattrs
    )


WindowsAuthorizationProjection = tuple[
    str,
    str,
    str,
    tuple[tuple[str, ...], ...],
]


def _windows_metadata_projection(
    descriptor: int,
) -> tuple[int, WindowsAuthorizationProjection]:
    status = _paths._windows_handle_status(descriptor)
    owner, group, control, aces = _paths._parse_windows_authorization_sddl(
        _paths._windows_security_sddl(descriptor)
    )
    normalized_aces = tuple(
        sorted(
            (
                kind,
                flags,
                rights,
                object_guid,
                inherited_guid,
                _paths._windows_canonical_sddl_sid(trustee),
            )
            for kind, flags, rights, object_guid, inherited_guid, trustee in aces
        )
    )
    return status.attributes, (
        _paths._windows_canonical_sddl_sid(owner),
        _paths._windows_canonical_sddl_sid(group),
        control,
        normalized_aces,
    )


def _metadata_snapshot(
    path: ConfigPathProof, policy: ConfigMetadataPolicy
) -> tuple[
    str,
    int | None,
    tuple[tuple[bytes, bytes], ...],
    int | None,
    WindowsAuthorizationProjection | None,
]:
    opened = path.open_leaf()
    if opened is None:
        return (
            hashlib.sha256(
                canonical_json_bytes(
                    {"parent_identity": path.parent_identity, "state": "absent"}
                )
            ).hexdigest(),
            None,
            (),
            None,
            None,
        )
    descriptor = 0 if os.name == "nt" else -1
    try:
        descriptor = opened._duplicate_descriptor()
        if os.name == "nt":
            windows_status = _paths._windows_handle_status(descriptor)
            if (
                windows_status.is_directory
                or windows_status.is_reparse
                or windows_status.link_count != 1
                or windows_status.attributes & ~(0x00000020 | 0x00000080)
                or not _paths._windows_private_authorization(descriptor, exact=True)
            ):
                raise _error(
                    "config.unsupported_metadata",
                    "Config security metadata is unsupported.",
                )
            windows_attributes, windows_authorization = _windows_metadata_projection(
                descriptor
            )
            domain: Mapping[str, object] = {
                "attributes": windows_attributes,
                "authorization": windows_authorization,
                "identity": windows_status.identity,
                "link_count": windows_status.link_count,
                "state": "present",
            }
            return (
                hashlib.sha256(canonical_json_bytes(domain)).hexdigest(),
                None,
                (),
                windows_attributes,
                windows_authorization,
            )
        posix_status = os.fstat(descriptor)
        mode = stat.S_IMODE(posix_status.st_mode)
        if (
            not stat.S_ISREG(posix_status.st_mode)
            or posix_status.st_uid != os.geteuid()
            or posix_status.st_gid != os.getegid()
            or posix_status.st_nlink != 1
            or mode not in policy.posix_modes
            or not _paths._posix_security_metadata_supported(descriptor, posix_status)
        ):
            raise _error(
                "config.unsupported_metadata",
                "Config security metadata is unsupported.",
            )
        posix_xattrs = _descriptor_xattrs(descriptor)
        domain = {
            "flags": getattr(posix_status, "st_flags", 0),
            "gid": posix_status.st_gid,
            "identity": (posix_status.st_dev, posix_status.st_ino),
            "mode": mode,
            "state": "present",
            "uid": posix_status.st_uid,
            "xattrs": _xattr_projection(posix_xattrs),
        }
        return (
            hashlib.sha256(canonical_json_bytes(domain)).hexdigest(),
            mode,
            posix_xattrs,
            None,
            None,
        )
    finally:
        if os.name == "nt":
            if descriptor:
                _paths._windows_close(descriptor)
        elif descriptor >= 0:
            os.close(descriptor)
        opened.close()


class ConfigSnapshot:
    __slots__ = (
        "_binding_digest",
        "_document",
        "_limit_values",
        "_posix_xattrs",
        "_raw",
        "_seal",
        "_semantic",
        "_windows_attributes",
        "_windows_authorization",
        "byte_digest",
        "final_newline",
        "leaf_identity",
        "metadata_fingerprint",
        "mode",
        "newline",
        "parent_identity",
        "present",
        "semantic_digest",
        "snapshot_digest",
    )

    parent_identity: tuple[int, int]
    leaf_identity: tuple[int, int] | None
    present: bool
    byte_digest: str
    semantic_digest: str
    metadata_fingerprint: str
    mode: int | None
    newline: str
    final_newline: bool
    snapshot_digest: str
    _raw: bytes
    _document: Any
    _limit_values: tuple[tuple[str, int], ...]
    _posix_xattrs: tuple[tuple[bytes, bytes], ...]
    _semantic: Mapping[str, object]
    _windows_attributes: int | None
    _windows_authorization: WindowsAuthorizationProjection | None
    _binding_digest: str
    _seal: object

    def __init__(
        self,
        *,
        parent_identity: tuple[int, int],
        leaf_identity: tuple[int, int] | None,
        raw: bytes,
        document: Any,
        semantic: Mapping[str, object],
        limit_values: tuple[tuple[str, int], ...],
        metadata_fingerprint: str,
        mode: int | None,
        posix_xattrs: tuple[tuple[bytes, bytes], ...],
        windows_attributes: int | None,
        windows_authorization: WindowsAuthorizationProjection | None,
        newline: str,
        final_newline: bool,
        _token: object,
    ) -> None:
        if _token is not _SNAPSHOT_TOKEN:
            raise TypeError("ConfigSnapshot is created only by snapshot_config")
        present = leaf_identity is not None
        byte_digest = hashlib.sha256(raw).hexdigest()
        semantic_digest = _semantic_digest(semantic)
        domain = {
            "byte_digest": byte_digest,
            "final_newline": final_newline,
            "leaf_identity": leaf_identity,
            "limit_values": limit_values,
            "metadata_fingerprint": metadata_fingerprint,
            "mode": mode,
            "newline": newline,
            "parent_identity": parent_identity,
            "posix_xattrs": _xattr_projection(posix_xattrs),
            "present": present,
            "semantic_digest": semantic_digest,
            "windows_attributes": windows_attributes,
            "windows_authorization": windows_authorization,
        }
        snapshot_digest = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        for name, value in {
            "parent_identity": parent_identity,
            "leaf_identity": leaf_identity,
            "present": present,
            "byte_digest": byte_digest,
            "semantic_digest": semantic_digest,
            "metadata_fingerprint": metadata_fingerprint,
            "mode": mode,
            "newline": newline,
            "final_newline": final_newline,
            "snapshot_digest": snapshot_digest,
            "_raw": raw,
            "_document": document,
            "_limit_values": limit_values,
            "_posix_xattrs": posix_xattrs,
            "_semantic": semantic,
            "_windows_attributes": windows_attributes,
            "_windows_authorization": windows_authorization,
            "_binding_digest": snapshot_digest,
            "_seal": _SNAPSHOT_TOKEN,
        }.items():
            object.__setattr__(self, name, value)

    def _require_valid(self) -> None:
        try:
            domain = {
                "byte_digest": hashlib.sha256(self._raw).hexdigest(),
                "final_newline": self.final_newline,
                "leaf_identity": self.leaf_identity,
                "limit_values": self._limit_values,
                "metadata_fingerprint": self.metadata_fingerprint,
                "mode": self.mode,
                "newline": self.newline,
                "parent_identity": self.parent_identity,
                "posix_xattrs": _xattr_projection(self._posix_xattrs),
                "present": self.present,
                "semantic_digest": _semantic_digest(self._semantic),
                "windows_attributes": self._windows_attributes,
                "windows_authorization": self._windows_authorization,
            }
            expected = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
            bound_document = tomlkit.dumps(self._document).encode("utf-8") == self._raw
        except (
            AttributeError,
            ForgeError,
            OverflowError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            raise _error(
                "config.external_change",
                "Config snapshot authority changed.",
            ) from None
        if (
            self._seal is not _SNAPSHOT_TOKEN
            or self._binding_digest != expected
            or self.snapshot_digest != expected
            or self.byte_digest != domain["byte_digest"]
            or self.semantic_digest != domain["semantic_digest"]
            or self.present != (self.leaf_identity is not None)
            or not bound_document
        ):
            raise _error("config.external_change", "Config snapshot authority changed.")

    def __repr__(self) -> str:
        return (
            "ConfigSnapshot(present="
            f"{self.present!r}, byte_digest={self.byte_digest!r}, "
            f"semantic_digest={self.semantic_digest!r})"
        )

    def __setattr__(self, _name: str, _value: object) -> Never:
        raise AttributeError("ConfigSnapshot is immutable")

    def __delattr__(self, _name: str) -> Never:
        raise AttributeError("ConfigSnapshot is immutable")

    def __reduce__(self) -> Never:
        raise TypeError("config snapshots are not serializable")


def snapshot_config(
    path: ConfigPathProof,
    limits: LimitPolicy = LIMIT_POLICY,
    metadata_policy: ConfigMetadataPolicy = CONFIG_METADATA_POLICY,
) -> Result[ConfigSnapshot]:
    """Read one exact config capability into a sealed, non-exporting snapshot."""

    if type(path) is not ConfigPathProof:
        return Result.failure(
            _error("config.external_change", "Config snapshot input is invalid.")
        )
    if not _trusted_limits(limits):
        return Result.failure(
            _error("config.limit_exceeded", "Config limit authority is invalid.")
        )
    if not _trusted_metadata_policy(metadata_policy):
        return Result.failure(
            _error("config.unsupported_metadata", "Config metadata policy is invalid.")
        )
    opened = None
    try:
        path._require_current()
        try:
            (
                metadata_fingerprint,
                mode,
                posix_xattrs,
                windows_attributes,
                windows_authorization,
            ) = _metadata_snapshot(path, metadata_policy)
        except OSError:
            raise _error(
                "config.unsupported_metadata",
                "Config metadata could not be inspected safely.",
            ) from None
        opened = path.open_leaf()
        if opened is not None and opened.size > limits.value("toml_bytes"):
            raise _error("config.limit_exceeded", "Config bytes exceed policy.")
        raw = (
            b""
            if opened is None
            else opened.read_bytes(limit=limits.value("toml_bytes"))
        )
        document, semantic, text = _parse_dual(raw, limits)
        path._require_current()
        newline = "\r\n" if "\r\n" in text else "\n"
        final_newline = text.endswith(("\n", "\r"))
        snapshot = ConfigSnapshot(
            parent_identity=path.parent_identity,
            leaf_identity=path.leaf_identity,
            raw=raw,
            document=document,
            semantic=semantic,
            limit_values=_limit_binding(limits),
            metadata_fingerprint=metadata_fingerprint,
            mode=mode,
            posix_xattrs=posix_xattrs,
            windows_attributes=windows_attributes,
            windows_authorization=windows_authorization,
            newline=newline,
            final_newline=final_newline,
            _token=_SNAPSHOT_TOKEN,
        )
        snapshot._require_valid()
        return Result.success(snapshot)
    except ForgeError as exc:
        code = exc.code if exc.exit_category == 13 else "config.external_change"
        return Result.failure(_error(code, "Config snapshot could not be established."))
    except RecursionError:
        return Result.failure(
            _error("config.limit_exceeded", "Config nesting exceeds policy.")
        )
    except (OSError, TypeError, ValueError):
        return Result.failure(
            _error("config.parse_failed", "Config snapshot could not be parsed safely.")
        )
    finally:
        if opened is not None:
            opened.close()


def _relevant_table(
    semantic: Mapping[str, object], root: str, key: str
) -> Mapping[str, object] | None:
    collection = semantic.get(root)
    if not isinstance(collection, Mapping):
        return None
    value = collection.get(key)
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _has_relevant_name(semantic: Mapping[str, object], effective_id: str) -> bool:
    marketplaces = semantic.get("marketplaces")
    plugins = semantic.get("plugins")
    plugin_key = f"zagrosi-forge@{effective_id}"
    return (isinstance(marketplaces, Mapping) and effective_id in marketplaces) or (
        isinstance(plugins, Mapping) and plugin_key in plugins
    )


def _legacy_recognition(
    snapshot: ConfigSnapshot,
    candidate: InstallIdentity,
    legacy: LegacyInstallCatalog,
) -> LegacyRecognition | None:
    marketplace = _relevant_table(snapshot._semantic, "marketplaces", "zagrosi")
    plugin = _relevant_table(snapshot._semantic, "plugins", "zagrosi-forge@zagrosi")
    if marketplace is None or plugin is None:
        return None
    cache = _paths.validate_reference(
        f"cache/zagrosi/zagrosi-forge/{candidate.base_version}",
        role="legacy-cache",
        limits=LIMIT_POLICY,
    ).unwrap()
    return match_legacy_install(
        legacy,
        marketplace_id="zagrosi",
        marketplace_table=marketplace,
        plugin_key="zagrosi-forge@zagrosi",
        plugin_table=plugin,
        cache_relative=cache,
    ).unwrap()


def _require_legacy_catalog(
    candidate: InstallIdentity, legacy: LegacyInstallCatalog
) -> None:
    cache = _paths.validate_reference(
        f"cache/zagrosi/zagrosi-forge/{candidate.base_version}",
        role="legacy-cache",
        limits=LIMIT_POLICY,
    ).unwrap()
    validated = match_legacy_install(
        legacy,
        marketplace_id="zagrosi",
        marketplace_table={},
        plugin_key="zagrosi-forge@zagrosi",
        plugin_table={},
        cache_relative=cache,
    )
    if not validated.is_ok:
        raise _error("config.owner_collision", "Legacy catalog authority is invalid.")


def _source_prefix(source: PlannedOwnedPath) -> str:
    native = source._config_source_value()
    suffix = os.path.join("plugins", *source.relative.components)
    if not native.endswith(suffix):
        raise _error(
            "config.owner_collision", "Owned source authority is inconsistent."
        )
    return native[: -len(suffix)].rstrip("/\\")


def _native_source_for(source: PlannedOwnedPath, relative: str) -> str:
    prefix = _source_prefix(source)
    return os.path.join(prefix, "plugins", *relative.split("/"))


def _managed_values_match(
    snapshot: ConfigSnapshot,
    effective_id: str,
    expected_source: str,
) -> bool:
    marketplace = _relevant_table(snapshot._semantic, "marketplaces", effective_id)
    plugin_key = f"zagrosi-forge@{effective_id}"
    plugin = _relevant_table(snapshot._semantic, "plugins", plugin_key)
    return (
        marketplace is not None
        and plugin is not None
        and type(marketplace.get("source_type")) is str
        and marketplace.get("source_type") == "local"
        and type(marketplace.get("source")) is str
        and marketplace.get("source") == expected_source
        and type(plugin.get("enabled")) is bool
        and plugin.get("enabled") is True
    )


def _adoption_domain(
    snapshot: ConfigSnapshot,
    candidate: InstallIdentity,
    source: PlannedOwnedPath,
    recognition: LegacyRecognition,
    *,
    runner_version: str,
    policy_version: str,
) -> Mapping[str, object]:
    return {
        "base_payload_digest": candidate.base_payload_digest,
        "base_version": candidate.base_version,
        "candidate_identity_digest": install_identity_digest(candidate),
        "effective_marketplace_id": "zagrosi",
        "install_version": candidate.install_version,
        "legacy_catalog_digest": recognition.catalog_digest,
        "legacy_projection_digest": recognition.projection_digest,
        "operation": ConfigOperation.ADOPT_RECOGNIZED_LEGACY.value,
        "policy_version": policy_version,
        "proposed_source_digest": hashlib.sha256(
            source._config_source_value().encode("utf-8")
        ).hexdigest(),
        "proposed_source_relative": source.relative.value,
        "rendered_payload_digest": candidate.rendered_payload_digest,
        "runner_version": runner_version,
        "snapshot_digest": snapshot.snapshot_digest,
        "source_root_identity": source.root_identity,
    }


def create_adoption_token(
    snapshot: ConfigSnapshot,
    candidate: InstallIdentity,
    source_reference: PlannedOwnedPath,
    *,
    legacy: LegacyInstallCatalog,
    runner_version: str = _RUNNER_VERSION,
    policy_version: str = _POLICY_VERSION,
) -> Result[str]:
    """Create the fixed digest challenge for an exact recognized legacy state."""

    try:
        if (
            type(snapshot) is not ConfigSnapshot
            or runner_version != _RUNNER_VERSION
            or policy_version != _POLICY_VERSION
        ):
            raise _error("config.adoption_stale", "Adoption input is invalid.")
        snapshot._require_valid()
        if not _trusted_identity(candidate) or not _trusted_source_reference(
            source_reference
        ):
            raise _error("config.adoption_stale", "Adoption input is invalid.")
        if source_reference._home_identity != snapshot.parent_identity:
            raise _error("config.adoption_stale", "Adoption input is invalid.")
        recognition = _legacy_recognition(snapshot, candidate, legacy)
        if recognition is None:
            raise _error("config.owner_collision", "Config is not exact legacy state.")
        token = hashlib.sha256(
            canonical_json_bytes(
                _adoption_domain(
                    snapshot,
                    candidate,
                    source_reference,
                    recognition,
                    runner_version=runner_version,
                    policy_version=policy_version,
                )
            )
        ).hexdigest()
        return Result.success(token)
    except ForgeError as exc:
        return Result.failure(
            exc
            if exc.exit_category == 13
            else _error("config.adoption_stale", "Adoption authority changed.")
        )
    except (OSError, TypeError, ValueError):
        return Result.failure(
            _error("config.adoption_stale", "Adoption challenge could not be created.")
        )


def _managed_nodes(
    effective_id: str, source: SafeRelativePath
) -> tuple[ManagedConfigNode, ...]:
    return (
        ManagedConfigNode(
            ("marketplaces", effective_id, "source_type"),
            ManagedConfigValueKind.STRING,
            "local",
        ),
        ManagedConfigNode(
            ("marketplaces", effective_id, "source"),
            ManagedConfigValueKind.OWNED_SOURCE,
            source.value,
        ),
        ManagedConfigNode(
            ("plugins", f"zagrosi-forge@{effective_id}", "enabled"),
            ManagedConfigValueKind.BOOLEAN,
            True,
        ),
    )


def _ensure_table(container: Any, key: str) -> Any:
    try:
        current = container.get(key)
    except (AttributeError, TypeError):
        raise _error(
            "config.representation_unsupported",
            "Config table representation cannot be edited safely.",
        ) from None
    if current is None:
        table = tomlkit.table()
        container[key] = table
        return table
    if not hasattr(current, "__setitem__") or isinstance(current, list):
        raise _error(
            "config.representation_unsupported",
            "Config table representation cannot be edited safely.",
        )
    return current


def _apply_managed(document: Any, effective_id: str, source_value: str) -> None:
    marketplaces = _ensure_table(document, "marketplaces")
    marketplace = _ensure_table(marketplaces, effective_id)
    plugins = _ensure_table(document, "plugins")
    plugin = _ensure_table(plugins, f"zagrosi-forge@{effective_id}")
    marketplace["source_type"] = "local"
    marketplace["source"] = source_value
    plugin["enabled"] = True


def _normalize_rendered_text(text: str, snapshot: ConfigSnapshot) -> str:
    original = snapshot._raw.decode("utf-8")
    without_crlf = original.replace("\r\n", "")
    if "\r\n" in original and "\n" in without_crlf:
        raise _error(
            "config.representation_unsupported",
            "Mixed config line endings cannot be preserved safely.",
        )
    if snapshot.newline == "\r\n":
        text = re.sub(r"(?<!\r)\n", "\r\n", text)
    if snapshot.final_newline:
        if not text.endswith(("\n", "\r")):
            text += snapshot.newline
    elif text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith(("\n", "\r")):
        text = text[:-1]
    return text


def _representation_paths(document: Any, effective_id: str) -> frozenset[str]:
    paths: set[str] = set()
    for root, child in (
        ("marketplaces", effective_id),
        ("plugins", f"zagrosi-forge@{effective_id}"),
    ):
        try:
            collection = document.get(root)
        except (AttributeError, TypeError):
            continue
        if isinstance(collection, Mapping):
            paths.add(root)
            if isinstance(collection.get(child), Mapping):
                paths.add(f"{root}/{child}")
    return frozenset(paths)


def _representation_projection(
    raw: bytes,
    effective_id: str,
    snapshot: ConfigSnapshot,
    preserved_paths: frozenset[str],
) -> bytes:
    try:
        document = tomlkit.parse(raw.decode("utf-8"))
        for root, child, keys in (
            ("marketplaces", effective_id, ("source_type", "source")),
            ("plugins", f"zagrosi-forge@{effective_id}", ("enabled",)),
        ):
            collection = document.get(root)
            if not isinstance(collection, MutableMapping):
                continue
            table = collection.get(child)
            if not isinstance(table, MutableMapping):
                continue
            for key in keys:
                if key in table:
                    del table[key]
            child_path = f"{root}/{child}"
            if not table and child_path not in preserved_paths:
                del collection[child]
            if not collection and root not in preserved_paths:
                del document[root]
        rendered = _normalize_rendered_text(tomlkit.dumps(document), snapshot)
        return rendered.encode("utf-8")
    except ForgeError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise _error(
            "config.representation_unsupported",
            "Config unmanaged representation cannot be proven unchanged.",
        ) from None


def _render_document(
    snapshot: ConfigSnapshot,
    effective_id: str,
    source_value: str,
    limits: LimitPolicy,
) -> tuple[bytes, Mapping[str, object]]:
    if _managed_values_match(snapshot, effective_id, source_value):
        return snapshot._raw, snapshot._semantic
    try:
        document = tomlkit.parse(snapshot._raw.decode("utf-8"))
        _apply_managed(document, effective_id, source_value)
        rendered_text = _normalize_rendered_text(tomlkit.dumps(document), snapshot)
        rendered = rendered_text.encode("utf-8")
        _again_document, semantic, _text = _parse_dual(rendered, limits)
    except ForgeError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise _error(
            "config.representation_unsupported",
            "Config representation cannot preserve managed edits.",
        ) from None
    before_unmanaged = _without_managed(snapshot._semantic, effective_id)
    after_unmanaged = _without_managed(semantic, effective_id)
    if before_unmanaged != after_unmanaged:
        raise _error(
            "config.representation_unsupported",
            "Config unmanaged semantics cannot be preserved.",
        )
    preserved_paths = _representation_paths(snapshot._document, effective_id)
    before_representation = _representation_projection(
        snapshot._raw,
        effective_id,
        snapshot,
        preserved_paths,
    )
    after_representation = _representation_projection(
        rendered,
        effective_id,
        snapshot,
        preserved_paths,
    )
    added_trailing = after_representation[len(before_representation) :]
    representation_preserved = after_representation == before_representation or (
        after_representation.startswith(before_representation)
        and bool(added_trailing)
        and not added_trailing.replace(b"\r", b"").replace(b"\n", b"")
    )
    if not representation_preserved:
        raise _error(
            "config.representation_unsupported",
            "Config unmanaged representation cannot be preserved.",
        )
    try:
        second_document = tomlkit.parse(rendered_text)
        _apply_managed(second_document, effective_id, source_value)
        if tomlkit.dumps(second_document).encode("utf-8") != rendered:
            raise _error(
                "config.representation_unsupported",
                "Config managed edit is not byte-idempotent.",
            )
    except ForgeError:
        raise
    except (TypeError, ValueError):
        raise _error(
            "config.representation_unsupported",
            "Config managed edit is not byte-idempotent.",
        ) from None
    return rendered, semantic


class ConfigPlan:
    __slots__ = (
        "_binding_digest",
        "_desired_source",
        "_limit_values",
        "_seal",
        "candidate_semantic_digest",
        "classification",
        "effective_marketplace_id",
        "identity",
        "managed_nodes",
        "operation",
        "persistent_backup",
        "plugin_key",
        "preview",
        "reason",
        "findings",
        "snapshot_digest",
        "source_root_identity",
        "unmanaged_semantic_digest",
    )

    snapshot_digest: str
    classification: ConfigClassification
    operation: ConfigOperation
    effective_marketplace_id: str
    plugin_key: str
    identity: InstallIdentity
    managed_nodes: tuple[ManagedConfigNode, ...]
    candidate_semantic_digest: str
    unmanaged_semantic_digest: str
    source_root_identity: tuple[int, int]
    preview: str
    reason: str
    findings: tuple[Finding, ...]
    persistent_backup: bool
    _desired_source: str
    _limit_values: tuple[tuple[str, int], ...]
    _binding_digest: str
    _seal: object

    def __init__(
        self,
        *,
        snapshot_digest: str,
        classification: ConfigClassification,
        operation: ConfigOperation,
        effective_marketplace_id: str,
        identity: InstallIdentity,
        managed_nodes: tuple[ManagedConfigNode, ...],
        candidate_semantic_digest: str,
        unmanaged_semantic_digest: str,
        source_root_identity: tuple[int, int],
        preview: str,
        reason: str,
        findings: tuple[Finding, ...],
        persistent_backup: bool,
        desired_source: str,
        limit_values: tuple[tuple[str, int], ...],
        _token: object,
    ) -> None:
        if _token is not _PLAN_TOKEN:
            raise TypeError("ConfigPlan is created only by plan_config")
        plugin_key = f"zagrosi-forge@{effective_marketplace_id}"
        domain = {
            "candidate_semantic_digest": candidate_semantic_digest,
            "classification": classification.value,
            "desired_source_digest": hashlib.sha256(
                desired_source.encode("utf-8")
            ).hexdigest(),
            "effective_marketplace_id": effective_marketplace_id,
            "identity_digest": install_identity_digest(identity),
            "limit_values": limit_values,
            "managed_nodes": managed_nodes,
            "operation": operation.value,
            "persistent_backup": persistent_backup,
            "plugin_key": plugin_key,
            "preview": preview,
            "reason": reason,
            "findings": findings,
            "snapshot_digest": snapshot_digest,
            "source_root_identity": source_root_identity,
            "unmanaged_semantic_digest": unmanaged_semantic_digest,
        }
        binding = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        for name, value in {
            "snapshot_digest": snapshot_digest,
            "classification": classification,
            "operation": operation,
            "effective_marketplace_id": effective_marketplace_id,
            "plugin_key": plugin_key,
            "identity": identity,
            "managed_nodes": managed_nodes,
            "candidate_semantic_digest": candidate_semantic_digest,
            "unmanaged_semantic_digest": unmanaged_semantic_digest,
            "source_root_identity": source_root_identity,
            "preview": preview,
            "reason": reason,
            "findings": findings,
            "persistent_backup": persistent_backup,
            "_desired_source": desired_source,
            "_limit_values": limit_values,
            "_binding_digest": binding,
            "_seal": _PLAN_TOKEN,
        }.items():
            object.__setattr__(self, name, value)

    def _require_valid(self) -> None:
        try:
            domain = {
                "candidate_semantic_digest": self.candidate_semantic_digest,
                "classification": self.classification.value,
                "desired_source_digest": hashlib.sha256(
                    self._desired_source.encode("utf-8")
                ).hexdigest(),
                "effective_marketplace_id": self.effective_marketplace_id,
                "identity_digest": install_identity_digest(self.identity),
                "limit_values": self._limit_values,
                "managed_nodes": self.managed_nodes,
                "operation": self.operation.value,
                "persistent_backup": self.persistent_backup,
                "plugin_key": self.plugin_key,
                "preview": self.preview,
                "reason": self.reason,
                "findings": self.findings,
                "snapshot_digest": self.snapshot_digest,
                "source_root_identity": self.source_root_identity,
                "unmanaged_semantic_digest": self.unmanaged_semantic_digest,
            }
            expected = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        except (
            AttributeError,
            ForgeError,
            OverflowError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            raise _error(
                "config.adoption_stale",
                "Config plan authority changed.",
            ) from None
        if self._seal is not _PLAN_TOKEN or self._binding_digest != expected:
            raise _error("config.adoption_stale", "Config plan authority changed.")

    def __repr__(self) -> str:
        return (
            "ConfigPlan(classification="
            f"{self.classification!r}, operation={self.operation!r}, "
            f"effective_marketplace_id={self.effective_marketplace_id!r}, "
            f"snapshot_digest={self.snapshot_digest!r}, preview={self.preview!r})"
        )

    def __eq__(self, other: object) -> bool:
        if type(other) is not ConfigPlan:
            return False
        self._require_valid()
        other._require_valid()
        return hmac.compare_digest(self._binding_digest, other._binding_digest)

    def __setattr__(self, _name: str, _value: object) -> Never:
        raise AttributeError("ConfigPlan is immutable")

    def __delattr__(self, _name: str) -> Never:
        raise AttributeError("ConfigPlan is immutable")

    def __reduce__(self) -> Never:
        raise TypeError("config plans are not serializable")


def _preview(effective_id: str, source: PlannedOwnedPath) -> str:
    return (
        "redacted managed config: "
        f"marketplaces.{effective_id}.source_type=local; "
        f"marketplaces.{effective_id}.source=<owned-source:{source.relative.value}>; "
        f'plugins."zagrosi-forge@{effective_id}".enabled=true'
    )


def _plan_reason(operation: ConfigOperation) -> str:
    return f"config.plan.{operation.value}"


def plan_config(
    snapshot: ConfigSnapshot,
    candidate: InstallIdentity,
    source_reference: PlannedOwnedPath,
    *,
    receipt: ValidatedInstallRelation | None,
    legacy: LegacyInstallCatalog,
    collision_policy: CollisionPolicy = CollisionPolicy.REJECT,
    adoption_token: str | None = None,
    persistent_backup: bool = True,
    runner_version: str = _RUNNER_VERSION,
    policy_version: str = _POLICY_VERSION,
    limits: LimitPolicy = LIMIT_POLICY,
) -> Result[ConfigPlan]:
    """Classify one snapshot and produce a deterministic, effect-free plan."""

    try:
        if runner_version != _RUNNER_VERSION or policy_version != _POLICY_VERSION:
            raise _error(
                "config.adoption_stale",
                "Config plan authority version is not trusted.",
            )
        if (
            type(snapshot) is not ConfigSnapshot
            or not _trusted_identity(candidate)
            or not _trusted_source_reference(source_reference)
            or type(collision_policy) is not CollisionPolicy
            or type(persistent_backup) is not bool
        ):
            raise _error("config.owner_collision", "Config plan input is invalid.")
        if not _trusted_limits(limits):
            raise _error("config.limit_exceeded", "Config limit authority is invalid.")
        snapshot._require_valid()
        if source_reference._home_identity != snapshot.parent_identity:
            raise _error(
                "config.owner_collision",
                "Config and source authorities belong to different homes.",
            )
        if _limit_binding(limits) != snapshot._limit_values:
            raise _error(
                "config.limit_exceeded",
                "Config plan limits do not match the snapshot authority.",
            )
        _require_legacy_catalog(candidate, legacy)
        if adoption_token is not None and (
            receipt is not None or collision_policy is CollisionPolicy.ALTERNATE
        ):
            raise _error(
                "config.adoption_stale",
                "Adoption cannot combine with this ownership or collision state.",
            )
        classification: ConfigClassification
        operation: ConfigOperation
        effective_id = "zagrosi"

        if receipt is not None:
            if type(receipt) is not ValidatedInstallRelation:
                raise _error(
                    "config.owner_collision", "Config ownership proof is invalid."
                )
            active = receipt.active
            current_source = _native_source_for(
                source_reference, active.source_generation
            )
            effective_id = active.effective_marketplace_id
            if (
                not _trusted_identity(active.identity)
                or (
                    effective_id != "zagrosi"
                    and _ALTERNATE_ID.fullmatch(effective_id) is None
                )
                or active.identity.marketplace_id != candidate.marketplace_id
                or active.identity.plugin_id != candidate.plugin_id
                or source_reference.relative.components[:3]
                != ("sources", effective_id, candidate.plugin_id)
                or receipt.config_after_snapshot_digest != snapshot.snapshot_digest
                or not _managed_values_match(snapshot, effective_id, current_source)
            ):
                raise _error(
                    "config.owner_collision", "Receipt and config projection disagree."
                )
            classification = ConfigClassification.EXACT_MANAGED
            desired_source = source_reference._config_source_value()
            operation = (
                ConfigOperation.NO_OP
                if active.identity == candidate
                and active.source_generation == source_reference.relative.value
                and current_source == desired_source
                else ConfigOperation.UPDATE_OWNED
            )
        else:
            recognition = _legacy_recognition(snapshot, candidate, legacy)
            if recognition is not None:
                classification = ConfigClassification.RECOGNIZED_LEGACY
                if collision_policy is CollisionPolicy.ALTERNATE:
                    effective_id = (
                        "zagrosi-local-" + install_identity_digest(candidate)[:24]
                    )
                    if _has_relevant_name(snapshot._semantic, effective_id):
                        raise _error(
                            "config.alternate_collision",
                            "The deterministic alternate config name is occupied.",
                        )
                    operation = ConfigOperation.COLLISION_ALTERNATIVE
                    desired_source = source_reference._config_source_value()
                else:
                    expected_token = hashlib.sha256(
                        canonical_json_bytes(
                            _adoption_domain(
                                snapshot,
                                candidate,
                                source_reference,
                                recognition,
                                runner_version=runner_version,
                                policy_version=policy_version,
                            )
                        )
                    ).hexdigest()
                    if adoption_token is None:
                        raise _error(
                            "config.adoption_required",
                            "Exact legacy config requires explicit adoption.",
                        )
                    if not hmac.compare_digest(adoption_token, expected_token):
                        raise _error(
                            "config.adoption_stale",
                            "The legacy adoption token is stale.",
                        )
                    operation = ConfigOperation.ADOPT_RECOGNIZED_LEGACY
                    desired_source = source_reference._config_source_value()
            elif adoption_token is not None:
                raise _error(
                    "config.adoption_stale",
                    "Adoption token does not match exact recognized legacy state.",
                )
            elif _has_relevant_name(snapshot._semantic, "zagrosi"):
                classification = ConfigClassification.UNMANAGED_COLLISION
                if collision_policy is CollisionPolicy.REJECT:
                    raise _error(
                        "config.owner_collision",
                        "An unmanaged config name is occupied.",
                    )
                effective_id = (
                    "zagrosi-local-" + install_identity_digest(candidate)[:24]
                )
                if _has_relevant_name(snapshot._semantic, effective_id):
                    raise _error(
                        "config.alternate_collision",
                        "The deterministic alternate config name is occupied.",
                    )
                operation = ConfigOperation.COLLISION_ALTERNATIVE
                desired_source = source_reference._config_source_value()
            else:
                classification = ConfigClassification.ABSENT
                operation = ConfigOperation.ADD
                desired_source = source_reference._config_source_value()

        if source_reference.relative.components != (
            "sources",
            effective_id,
            candidate.plugin_id,
            candidate.install_version,
            "marketplace",
        ):
            raise _error(
                "config.owner_collision",
                "Proposed config source does not match the selected install identity.",
            )

        managed_nodes = _managed_nodes(effective_id, source_reference.relative)
        rendered, candidate_semantic = _render_document(
            snapshot, effective_id, desired_source, limits
        )
        del rendered
        candidate_semantic_digest = _semantic_digest(candidate_semantic)
        unmanaged_digest = _semantic_digest(
            _without_managed(candidate_semantic, effective_id)
        )
        plan = ConfigPlan(
            snapshot_digest=snapshot.snapshot_digest,
            classification=classification,
            operation=operation,
            effective_marketplace_id=effective_id,
            identity=candidate,
            managed_nodes=managed_nodes,
            candidate_semantic_digest=candidate_semantic_digest,
            unmanaged_semantic_digest=unmanaged_digest,
            source_root_identity=source_reference.root_identity,
            preview=_preview(effective_id, source_reference),
            reason=_plan_reason(operation),
            findings=(),
            persistent_backup=persistent_backup,
            desired_source=desired_source,
            limit_values=_limit_binding(limits),
            _token=_PLAN_TOKEN,
        )
        plan._require_valid()
        return Result.success(plan)
    except ForgeError as exc:
        return Result.failure(
            exc
            if exc.exit_category == 13
            else _error("config.owner_collision", "Config authority changed.")
        )
    except (OSError, TypeError, ValueError):
        return Result.failure(
            _error("config.owner_collision", "Config plan could not be established.")
        )


class ConfigCandidate:
    __slots__ = (
        "_binding_digest",
        "_raw",
        "_seal",
        "byte_digest",
        "changed",
        "metadata_fingerprint",
        "persistent_backup",
        "preview",
        "semantic_digest",
        "snapshot_digest",
        "unmanaged_semantic_digest",
    )

    snapshot_digest: str
    byte_digest: str
    semantic_digest: str
    unmanaged_semantic_digest: str
    metadata_fingerprint: str
    changed: bool
    preview: str
    persistent_backup: bool
    _raw: bytes
    _binding_digest: str
    _seal: object

    def __init__(
        self,
        *,
        snapshot: ConfigSnapshot,
        plan: ConfigPlan,
        raw: bytes,
        semantic: Mapping[str, object],
        _token: object,
    ) -> None:
        if _token is not _CANDIDATE_TOKEN:
            raise TypeError("ConfigCandidate is created only by rendering")
        byte_digest = hashlib.sha256(raw).hexdigest()
        semantic_digest = _semantic_digest(semantic)
        unmanaged_digest = _semantic_digest(
            _without_managed(semantic, plan.effective_marketplace_id)
        )
        domain = {
            "byte_digest": byte_digest,
            "changed": raw != snapshot._raw,
            "metadata_fingerprint": snapshot.metadata_fingerprint,
            "preview": plan.preview,
            "persistent_backup": plan.persistent_backup,
            "semantic_digest": semantic_digest,
            "snapshot_digest": snapshot.snapshot_digest,
            "unmanaged_semantic_digest": unmanaged_digest,
        }
        binding = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        for name, value in {
            "snapshot_digest": snapshot.snapshot_digest,
            "byte_digest": byte_digest,
            "semantic_digest": semantic_digest,
            "unmanaged_semantic_digest": unmanaged_digest,
            "metadata_fingerprint": snapshot.metadata_fingerprint,
            "changed": raw != snapshot._raw,
            "preview": plan.preview,
            "persistent_backup": plan.persistent_backup,
            "_raw": raw,
            "_binding_digest": binding,
            "_seal": _CANDIDATE_TOKEN,
        }.items():
            object.__setattr__(self, name, value)

    def _require_valid(self) -> None:
        try:
            domain = {
                "byte_digest": hashlib.sha256(self._raw).hexdigest(),
                "changed": self.changed,
                "metadata_fingerprint": self.metadata_fingerprint,
                "preview": self.preview,
                "persistent_backup": self.persistent_backup,
                "semantic_digest": self.semantic_digest,
                "snapshot_digest": self.snapshot_digest,
                "unmanaged_semantic_digest": self.unmanaged_semantic_digest,
            }
            expected = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        except (
            AttributeError,
            ForgeError,
            OverflowError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            raise _error(
                "config.external_change",
                "Config candidate authority changed.",
            ) from None
        if (
            self._seal is not _CANDIDATE_TOKEN
            or self._binding_digest != expected
            or self.byte_digest != domain["byte_digest"]
        ):
            raise _error(
                "config.external_change", "Config candidate authority changed."
            )

    def __repr__(self) -> str:
        return (
            "ConfigCandidate(byte_digest="
            f"{self.byte_digest!r}, changed={self.changed!r}, preview={self.preview!r})"
        )

    def __setattr__(self, _name: str, _value: object) -> Never:
        raise AttributeError("ConfigCandidate is immutable")

    def __delattr__(self, _name: str) -> Never:
        raise AttributeError("ConfigCandidate is immutable")

    def __reduce__(self) -> Never:
        raise TypeError("config candidates are not serializable")


def render_config_candidate(
    snapshot: ConfigSnapshot,
    plan: ConfigPlan,
    limits: LimitPolicy = LIMIT_POLICY,
) -> Result[ConfigCandidate]:
    """Render and dual-validate a plan without performing filesystem effects."""

    try:
        if type(snapshot) is not ConfigSnapshot or type(plan) is not ConfigPlan:
            raise _error("config.external_change", "Config render input is invalid.")
        if not _trusted_limits(limits):
            raise _error("config.limit_exceeded", "Config limit authority is invalid.")
        snapshot._require_valid()
        plan._require_valid()
        if (
            _limit_binding(limits) != snapshot._limit_values
            or plan._limit_values != snapshot._limit_values
        ):
            raise _error(
                "config.limit_exceeded",
                "Config render limits do not match the snapshot authority.",
            )
        if plan.snapshot_digest != snapshot.snapshot_digest:
            raise _error("config.external_change", "Config plan snapshot is stale.")
        raw, semantic = _render_document(
            snapshot,
            plan.effective_marketplace_id,
            plan._desired_source,
            limits,
        )
        if (
            _semantic_digest(semantic) != plan.candidate_semantic_digest
            or _semantic_digest(
                _without_managed(semantic, plan.effective_marketplace_id)
            )
            != plan.unmanaged_semantic_digest
        ):
            raise _error(
                "config.representation_unsupported",
                "Rendered config does not match the semantic plan.",
            )
        candidate = ConfigCandidate(
            snapshot=snapshot,
            plan=plan,
            raw=raw,
            semantic=semantic,
            _token=_CANDIDATE_TOKEN,
        )
        candidate._require_valid()
        return Result.success(candidate)
    except ForgeError as exc:
        return Result.failure(exc)
    except (TypeError, ValueError):
        return Result.failure(
            _error(
                "config.representation_unsupported",
                "Config candidate could not be rendered safely.",
            )
        )


def _candidate_bytes(candidate: ConfigCandidate) -> bytes:
    candidate._require_valid()
    return candidate._raw


def _snapshot_bytes(snapshot: ConfigSnapshot) -> bytes:
    snapshot._require_valid()
    return snapshot._raw


def _validate_candidate_bytes(candidate: ConfigCandidate, raw: bytes) -> bool:
    try:
        candidate._require_valid()
        _document, semantic, _text = _parse_dual(raw, LIMIT_POLICY)
        return (
            hashlib.sha256(raw).hexdigest() == candidate.byte_digest
            and _semantic_digest(semantic) == candidate.semantic_digest
        )
    except ForgeError:
        return False
