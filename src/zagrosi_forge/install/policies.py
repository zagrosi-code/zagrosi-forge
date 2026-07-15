"""Trusted package policy loading and fixed v1 limits."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import resources
import re
from types import MappingProxyType
from typing import Mapping, cast

from .contracts import ForgeError, load_trusted_policy


_POLICY_SCHEMAS = {
    "limit-policy.json": (
        "schemas/limit-policy-v1.schema.json",
        "a16877ff4755133f03f5bacc6fd88a061b49607945dc99d2efc4277218b895dc",
    ),
    "native-isolation-policy.json": (
        "schemas/native-isolation-policy-v1.schema.json",
        "dcdf065657cb09ca940efdc209a7e2c28b6bbf7bdb38316b37d3ae1f66d8014c",
    ),
    "native-support-policy.json": (
        "schemas/native-support-policy-v1.schema.json",
        "5c23d5106aeb96c00fd132193d3d0a52b055158486d443af01aba6df5a2a96c1",
    ),
    "recovery-retention-policy.json": (
        "schemas/recovery-retention-policy-v1.schema.json",
        "f7b9245cb10205b7bb1eb21c95abfb241d79aeb86f2ad90be2dfc0e5e9d9bf50",
    ),
}

_COMMON_POLICY_KEYS = frozenset(
    {
        "schema_version",
        "schema_digest",
        "writer_version",
        "minimum_reader_version",
        "record_digest",
        "limit_policy_version",
        "policy_version",
    }
)
_LIMIT_KEYS = frozenset(
    {
        "identifier_bytes",
        "alternate_id_hex_chars",
        "cachebuster_hex_chars",
        "path_bytes",
        "path_components",
        "path_component_bytes",
        "json_record_bytes",
        "json_depth",
        "json_members",
        "toml_bytes",
        "toml_depth",
        "toml_nodes",
        "bundle_files",
        "bundle_member_bytes",
        "bundle_total_bytes",
        "archive_compressed_bytes",
        "archive_expanded_bytes",
        "archive_ratio",
        "journal_records",
        "journal_record_bytes",
        "journal_total_bytes",
        "subprocess_channel_bytes",
        "subprocess_tail_bytes",
        "subprocess_timeout_seconds",
        "subprocess_total_seconds",
        "lock_default_seconds",
        "lock_max_seconds",
        "evidence_records",
        "evidence_bytes",
        "evidence_warning_days",
        "private_recovery_records",
        "private_recovery_bytes",
        "private_recovery_warning_hours",
        "backup_records",
        "backup_bytes",
        "backup_days",
        "property_ci_examples",
        "state_machine_sequences",
        "state_machine_steps",
    }
)
_POLICY_KEYS = {
    "limit-policy.json": _COMMON_POLICY_KEYS | {"values"},
    "native-isolation-policy.json": _COMMON_POLICY_KEYS | {"required", "not_claimed"},
    "native-support-policy.json": _COMMON_POLICY_KEYS
    | {"codex_versions", "platforms", "manifest_scripts_field"},
    "recovery-retention-policy.json": _COMMON_POLICY_KEYS
    | {"generation_retention", "private_recovery", "backups"},
}
_PRIVATE_RECOVERY_KEYS = frozenset({"max_bytes", "max_records", "warning_hours"})
_BACKUP_KEYS = frozenset({"max_bytes", "max_days", "max_records"})
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_RELEASE_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")


def _policy_invalid(message: str) -> ForgeError:
    return ForgeError("policy.record_invalid", 10, message)


def _string_sequence(value: object, *, field: str) -> tuple[str, ...]:
    if (
        not isinstance(value, (list, tuple))
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise _policy_invalid(f"Trusted policy {field} is invalid.")
    rendered = tuple(value)
    if len(set(rendered)) != len(rendered):
        raise _policy_invalid(f"Trusted policy {field} contains duplicates.")
    return rendered


def _positive_integer_map(
    value: object, *, keys: frozenset[str], field: str
) -> Mapping[str, int]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _policy_invalid(f"Trusted policy {field} keys are invalid.")
    if any(
        not isinstance(item, int) or isinstance(item, bool) or item < 1
        for item in value.values()
    ):
        raise _policy_invalid(f"Trusted policy {field} values are invalid.")
    return cast(Mapping[str, int], value)


def _validate_policy_record(resource_name: str, record: Mapping[str, object]) -> None:
    """Apply the closed manual v1 schema before any policy field is interpreted."""

    try:
        expected_keys = _POLICY_KEYS[resource_name]
        expected_schema_digest = _POLICY_SCHEMAS[resource_name][1]
    except KeyError as exc:
        raise _policy_invalid("Trusted policy resource is not supported.") from exc
    if set(record) != expected_keys:
        raise _policy_invalid("Trusted policy fields do not match the v1 schema.")
    if (
        record.get("schema_version") != "1.0"
        or record.get("schema_digest") != expected_schema_digest
        or record.get("policy_version") != "1.0"
        or record.get("limit_policy_version") != "1.0"
    ):
        raise _policy_invalid("Trusted policy header is incompatible.")
    if not isinstance(record.get("record_digest"), str) or not _DIGEST.fullmatch(
        cast(str, record["record_digest"])
    ):
        raise _policy_invalid("Trusted policy record digest is invalid.")
    for field in ("writer_version", "minimum_reader_version"):
        value = record.get(field)
        if not isinstance(value, str) or not _RELEASE_VERSION.fullmatch(value):
            raise _policy_invalid(f"Trusted policy {field} is invalid.")

    if resource_name == "limit-policy.json":
        _positive_integer_map(record.get("values"), keys=_LIMIT_KEYS, field="limits")
        return
    if resource_name == "native-support-policy.json":
        _string_sequence(record.get("codex_versions"), field="Codex versions")
        _string_sequence(record.get("platforms"), field="platforms")
        if record.get("manifest_scripts_field") != "accepted_no_execution_observed":
            raise _policy_invalid("Trusted policy scripts outcome is invalid.")
        return
    if resource_name == "native-isolation-policy.json":
        required = _string_sequence(record.get("required"), field="requirements")
        not_claimed = _string_sequence(
            record.get("not_claimed"), field="unclaimed capabilities"
        )
        if set(required) & set(not_claimed):
            raise _policy_invalid("Trusted isolation capabilities overlap.")
        return
    if record.get("generation_retention") != "unbounded":
        raise _policy_invalid("Trusted recovery generation retention is invalid.")
    _positive_integer_map(
        record.get("private_recovery"),
        keys=_PRIVATE_RECOVERY_KEYS,
        field="private recovery",
    )
    _positive_integer_map(record.get("backups"), keys=_BACKUP_KEYS, field="backups")


def load_policy(resource_name: str) -> Mapping[str, object]:
    """Load a supported installed policy after checking its packaged schema."""

    try:
        schema_resource, expected_digest = _POLICY_SCHEMAS[resource_name]
    except KeyError as exc:
        raise ForgeError(
            "policy.resource_rejected", 10, "Trusted policy is not supported."
        ) from exc
    schema = (
        resources.files("zagrosi_forge.install").joinpath(schema_resource).read_bytes()
    )
    if hashlib.sha256(schema).hexdigest() != expected_digest:
        raise ForgeError(
            "policy.schema_mismatch",
            10,
            "Packaged policy schema digest does not match.",
        )
    record = cast(
        Mapping[str, object], load_trusted_policy(resource_name, expected_digest)
    )
    _validate_policy_record(resource_name, record)
    return record


@dataclass(frozen=True, slots=True)
class LimitPolicy:
    version: str
    values: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.version != "1.0":
            raise ValueError("limit policy version is invalid")
        _positive_integer_map(self.values, keys=_LIMIT_KEYS, field="limits")
        object.__setattr__(
            self, "values", MappingProxyType(dict(sorted(self.values.items())))
        )

    def value(self, resource: str) -> int:
        try:
            return self.values[resource]
        except KeyError as exc:
            raise ForgeError(
                "limit.unknown", 10, "Requested limit is not defined."
            ) from exc


@dataclass(frozen=True, slots=True)
class NativeSupportPolicy:
    version: str
    codex_versions: tuple[str, ...]
    platforms: tuple[str, ...]
    manifest_scripts_field: str

    def __post_init__(self) -> None:
        if not isinstance(self.codex_versions, (list, tuple)) or any(
            not isinstance(value, str) or not value for value in self.codex_versions
        ):
            raise ValueError("native support Codex versions are invalid")
        if not isinstance(self.platforms, (list, tuple)) or any(
            not isinstance(value, str) or not value for value in self.platforms
        ):
            raise ValueError("native support platforms are invalid")
        codex_versions = tuple(self.codex_versions)
        platforms = tuple(self.platforms)
        if self.version != "1.0" or not codex_versions or not platforms:
            raise ValueError("native support policy fields must be non-empty")
        if self.manifest_scripts_field != "accepted_no_execution_observed":
            raise ValueError("native support policy scripts outcome is invalid")
        if len(set(codex_versions)) != len(codex_versions):
            raise ValueError("native support policy has duplicate Codex versions")
        if len(set(platforms)) != len(platforms):
            raise ValueError("native support policy has duplicate platforms")
        object.__setattr__(self, "codex_versions", codex_versions)
        object.__setattr__(self, "platforms", platforms)


@dataclass(frozen=True, slots=True)
class NativeIsolationPolicy:
    version: str
    required: tuple[str, ...]
    not_claimed: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.required, (list, tuple)) or any(
            not isinstance(value, str) or not value for value in self.required
        ):
            raise ValueError("native isolation requirements are invalid")
        if not isinstance(self.not_claimed, (list, tuple)) or any(
            not isinstance(value, str) or not value for value in self.not_claimed
        ):
            raise ValueError("native isolation unclaimed capabilities are invalid")
        required = tuple(self.required)
        not_claimed = tuple(self.not_claimed)
        if self.version != "1.0" or not required:
            raise ValueError("native isolation policy fields must be non-empty")
        if len(set(required)) != len(required) or len(set(not_claimed)) != len(
            not_claimed
        ):
            raise ValueError("native isolation policy has duplicate capabilities")
        if set(required) & set(not_claimed):
            raise ValueError("native isolation policy capabilities overlap")
        object.__setattr__(self, "required", required)
        object.__setattr__(self, "not_claimed", not_claimed)


@dataclass(frozen=True, slots=True)
class RecoveryRetentionPolicy:
    version: str
    generation_retention: str
    private_recovery: Mapping[str, int]
    backups: Mapping[str, int]

    def __post_init__(self) -> None:
        private_recovery = dict(self.private_recovery)
        backups = dict(self.backups)
        if not self.version or self.generation_retention != "unbounded":
            raise ValueError("recovery retention policy header is invalid")
        if set(private_recovery) != {"max_bytes", "max_records", "warning_hours"}:
            raise ValueError("private recovery limits are incomplete")
        if set(backups) != {"max_bytes", "max_days", "max_records"}:
            raise ValueError("backup limits are incomplete")
        if any(value < 1 for value in (*private_recovery.values(), *backups.values())):
            raise ValueError("recovery retention limits must be positive")
        object.__setattr__(
            self,
            "private_recovery",
            MappingProxyType(dict(sorted(private_recovery.items()))),
        )
        object.__setattr__(
            self, "backups", MappingProxyType(dict(sorted(backups.items())))
        )


def _required_string(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise ForgeError(
            "policy.record_invalid", 10, "Trusted policy field is invalid."
        )
    return value


def _required_strings(record: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = record.get(key)
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ForgeError("policy.record_invalid", 10, "Trusted policy list is invalid.")
    return tuple(value)


def _required_limits(
    record: Mapping[str, object], key: str, required: frozenset[str]
) -> Mapping[str, int]:
    value = record.get(key)
    if not isinstance(value, Mapping) or set(value) != required:
        raise ForgeError(
            "policy.record_invalid", 10, "Trusted policy limits are invalid."
        )
    if any(
        not isinstance(item, int) or isinstance(item, bool) or item < 1
        for item in value.values()
    ):
        raise ForgeError(
            "policy.record_invalid", 10, "Trusted policy limit is invalid."
        )
    return MappingProxyType(dict(cast(Mapping[str, int], value)))


_LIMIT_RECORD = load_policy("limit-policy.json")
_LIMIT_VALUES = cast(Mapping[str, int], _LIMIT_RECORD["values"])
LIMIT_POLICY = LimitPolicy(
    version=cast(str, _LIMIT_RECORD["policy_version"]), values=_LIMIT_VALUES
)

_NATIVE_SUPPORT_RECORD = load_policy("native-support-policy.json")
NATIVE_SUPPORT_POLICY = NativeSupportPolicy(
    version=_required_string(_NATIVE_SUPPORT_RECORD, "policy_version"),
    codex_versions=_required_strings(_NATIVE_SUPPORT_RECORD, "codex_versions"),
    platforms=_required_strings(_NATIVE_SUPPORT_RECORD, "platforms"),
    manifest_scripts_field=_required_string(
        _NATIVE_SUPPORT_RECORD, "manifest_scripts_field"
    ),
)

_NATIVE_ISOLATION_RECORD = load_policy("native-isolation-policy.json")
NATIVE_ISOLATION_POLICY = NativeIsolationPolicy(
    version=_required_string(_NATIVE_ISOLATION_RECORD, "policy_version"),
    required=_required_strings(_NATIVE_ISOLATION_RECORD, "required"),
    not_claimed=_required_strings(_NATIVE_ISOLATION_RECORD, "not_claimed"),
)

_RECOVERY_RETENTION_RECORD = load_policy("recovery-retention-policy.json")
RECOVERY_RETENTION_POLICY = RecoveryRetentionPolicy(
    version=_required_string(_RECOVERY_RETENTION_RECORD, "policy_version"),
    generation_retention=_required_string(
        _RECOVERY_RETENTION_RECORD, "generation_retention"
    ),
    private_recovery=_required_limits(
        _RECOVERY_RETENTION_RECORD,
        "private_recovery",
        frozenset({"max_bytes", "max_records", "warning_hours"}),
    ),
    backups=_required_limits(
        _RECOVERY_RETENTION_RECORD,
        "backups",
        frozenset({"max_bytes", "max_days", "max_records"}),
    ),
)


def enforce_limit(resource: str, amount: int) -> None:
    """Reject negative or over-limit measurements without adapting limits."""

    if amount < 0 or amount > LIMIT_POLICY.value(resource):
        raise ForgeError(
            "limit.exceeded", 10, "Input exceeds a trusted resource limit."
        )
