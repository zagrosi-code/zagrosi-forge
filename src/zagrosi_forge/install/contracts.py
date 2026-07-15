"""Immutable installer contracts and canonical persistent decoding."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import hashlib
from importlib import resources
from importlib.metadata import version as distribution_version
import json
import math
import re
from types import MappingProxyType
from typing import Any, Generic, Mapping, TypeVar, cast


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_RELEASE_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_CONTRACT_VERSION = re.compile(r"[a-z][a-z0-9-]*-v(?:0|[1-9][0-9]*)\Z")
_MAX_CANONICAL_DEPTH = 32
_MAX_RECORD_BYTES = 256 * 1024
_MAX_JSON_MEMBERS = 512
_MAX_FINDING_TEXT_BYTES = 4_096
_MAX_FINDING_KEY_BYTES = 128
T = TypeVar("T")


def _freeze_value(value: object) -> object:
    """Recursively freeze JSON-shaped values without losing canonical content."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in sorted(value.items())}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _expected_install_version(base_version: str, base_payload_digest: str) -> str:
    return f"{base_version}+codex.local-{base_payload_digest[:32]}"


def _finding_value_rejected(message: str) -> ForgeError:
    return ForgeError("diagnostic.value_rejected", 10, message)


def _bounded_utf8(value: str, *, limit: int) -> bool:
    if len(value) > limit:
        return False
    try:
        return len(value.encode("utf-8")) <= limit
    except UnicodeEncodeError:
        return False


def _validate_finding_details(details: object) -> Mapping[str, object]:
    if not isinstance(details, Mapping) or len(details) > _MAX_JSON_MEMBERS:
        raise _finding_value_rejected("Diagnostic details exceed the trusted limit.")
    for key, value in details.items():
        if (
            not isinstance(key, str)
            or not key
            or not _bounded_utf8(key, limit=_MAX_FINDING_KEY_BYTES)
        ):
            raise _finding_value_rejected("Diagnostic detail key is invalid.")
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            if 0 <= value <= 2**31 - 1:
                continue
            raise _finding_value_rejected("Diagnostic detail integer is out of range.")
        if isinstance(value, str) and _bounded_utf8(
            value, limit=_MAX_FINDING_TEXT_BYTES
        ):
            continue
        raise _finding_value_rejected("Diagnostic detail value is invalid.")
    return details


def parse_release_version(value: object) -> tuple[int, int, int]:
    """Parse the canonical release-only SemVer subset used by installer records."""

    if (
        not isinstance(value, str)
        or (match := _RELEASE_VERSION.fullmatch(value)) is None
    ):
        raise ValueError("release_version")
    return (int(match[1]), int(match[2]), int(match[3]))


def _release_version(value: object) -> tuple[int, int, int]:
    try:
        return parse_release_version(value)
    except ValueError as exc:
        raise ForgeError(
            "record.invalid", 10, "Persistent record reader version is invalid."
        ) from exc


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: str
    message: str
    subject: str
    authority: str
    authority_version: str
    remediation: str
    details: Mapping[str, object]

    def __post_init__(self) -> None:
        for name in (
            "code",
            "severity",
            "message",
            "subject",
            "authority",
            "authority_version",
            "remediation",
        ):
            if not getattr(self, name):
                raise ValueError(name)
        details = _validate_finding_details(self.details)
        object.__setattr__(self, "details", _freeze_value(details))


class ForgeError(Exception):
    """Structured error with read-only contract fields and mutable VM traceback state."""

    _READ_ONLY_FIELDS = frozenset(
        {"code", "exit_category", "safe_message", "findings", "recovery_instructions"}
    )

    def __init__(
        self,
        code: str,
        exit_category: int,
        safe_message: str,
        findings: tuple[Finding, ...] = (),
        recovery_instructions: tuple[str, ...] = (),
    ) -> None:
        Exception.__init__(self, safe_message)
        self.code = code
        self.exit_category = exit_category
        self.safe_message = safe_message
        self.findings = tuple(sorted(findings, key=_finding_sort_key))
        self.recovery_instructions = tuple(recovery_instructions)

    def __setattr__(self, name: str, value: object) -> None:
        if name in self._READ_ONLY_FIELDS and name in self.__dict__:
            raise AttributeError(f"{name} is read-only")
        Exception.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if name in self._READ_ONLY_FIELDS and name in self.__dict__:
            raise AttributeError(f"{name} is read-only")
        Exception.__delattr__(self, name)


def _finding_sort_key(finding: Finding) -> bytes:
    return canonical_json_bytes(finding)


@dataclass(frozen=True, slots=True)
class ValidationResult(Generic[T]):
    """Closed success-or-error result with deterministic diagnostics."""

    value: T | None
    error: ForgeError | None
    findings: tuple[Finding, ...] = ()

    def __post_init__(self) -> None:
        if self.error is not None and not isinstance(self.error, ForgeError):
            raise TypeError("error")
        if self.error is not None and self.value is not None:
            raise ValueError("result")
        if not isinstance(self.findings, tuple) or any(
            not isinstance(finding, Finding) for finding in self.findings
        ):
            raise ValueError("findings")
        findings = tuple(sorted(self.findings, key=_finding_sort_key))
        if self.error is not None:
            if not findings:
                findings = self.error.findings
            elif findings != self.error.findings:
                raise ValueError("findings")
        object.__setattr__(self, "findings", findings)

    @classmethod
    def success(
        cls, value: T, *, findings: tuple[Finding, ...] = ()
    ) -> ValidationResult[T]:
        return cls(value=value, error=None, findings=findings)

    @classmethod
    def failure(
        cls, error: ForgeError, *, findings: tuple[Finding, ...] = ()
    ) -> ValidationResult[T]:
        if not isinstance(error, ForgeError):
            raise TypeError("error")
        return cls(value=None, error=error, findings=findings or error.findings)

    @property
    def is_ok(self) -> bool:
        return self.error is None

    def unwrap(self) -> T:
        if self.error is not None:
            raise self.error
        return cast(T, self.value)


Result = ValidationResult


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Effect-free inspection findings that never carry installer authority."""

    findings: tuple[Finding, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.findings, tuple) or any(
            not isinstance(finding, Finding) for finding in self.findings
        ):
            raise ValueError("findings")
        object.__setattr__(
            self, "findings", tuple(sorted(self.findings, key=_finding_sort_key))
        )

    @property
    def is_valid(self) -> bool:
        return not self.findings


@dataclass(frozen=True, slots=True)
class InstallIdentity:
    marketplace_id: str
    plugin_id: str
    base_version: str
    install_version: str
    base_payload_digest: str
    rendered_payload_digest: str
    policy_digest: str
    transformation_profile: str
    contract_versions: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "marketplace_id",
            "plugin_id",
            "base_version",
            "install_version",
            "transformation_profile",
        ):
            if not getattr(self, name):
                raise ValueError(name)
        for name in ("base_payload_digest", "rendered_payload_digest", "policy_digest"):
            if not _DIGEST.fullmatch(getattr(self, name)):
                raise ValueError(name)
        try:
            parse_release_version(self.base_version)
        except ValueError as exc:
            raise ValueError("base_version") from exc
        if self.install_version != _expected_install_version(
            self.base_version, self.base_payload_digest
        ):
            raise ValueError("install_version")
        if (
            not isinstance(self.contract_versions, tuple)
            or not self.contract_versions
            or any(
                not isinstance(value, str) or not _CONTRACT_VERSION.fullmatch(value)
                for value in self.contract_versions
            )
            or tuple(sorted(set(self.contract_versions))) != self.contract_versions
        ):
            raise ValueError("contract_versions")


@dataclass(frozen=True, slots=True)
class ActiveInstallRelation:
    effective_marketplace_id: str
    plugin_id: str
    managed_config_projection: tuple[tuple[str, str], ...]
    source_generation: str
    cache_generation: str
    base_version: str
    install_version: str
    base_payload_digest: str
    rendered_payload_digest: str
    committed_receipt_ref: str

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            if not getattr(self, name):
                raise ValueError(name)
        for name in ("base_payload_digest", "rendered_payload_digest"):
            if not _DIGEST.fullmatch(getattr(self, name)):
                raise ValueError(name)
        try:
            parse_release_version(self.base_version)
        except ValueError as exc:
            raise ValueError("base_version") from exc
        if self.install_version != _expected_install_version(
            self.base_version, self.base_payload_digest
        ):
            raise ValueError("install_version")


class RunnerState(str, Enum):
    VERIFIED_INSTALLED_DISTRIBUTION = "verified_installed_distribution"
    VERIFIED_RELEASE_ARTIFACT = "verified_release_artifact"
    RECEIPT_PROVEN_GENERATION = "receipt_proven_generation"
    OPERATOR_VERIFIED_COMMIT_ARTIFACT = "operator_verified_commit_artifact"
    UNVERIFIED_SELF_ROOT = "unverified_self_root"


class RunnerOperation(str, Enum):
    DIAGNOSTIC = "diagnostic"
    PLAN = "plan"
    MUTATE = "mutate"
    RECOVER = "recover"
    CLAIM_CANDIDATE_VALID = "claim_candidate_valid"
    CLAIM_RELEASE_VALID = "claim_release_valid"


@dataclass(frozen=True, slots=True)
class RunnerProvenance:
    state: RunnerState
    origin: str
    artifact_digest: str
    runner_version: str
    verification_authority: str
    policy_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.state, RunnerState):
            raise ForgeError(
                "runner.untrusted",
                15,
                "The current runner provenance state is not trusted.",
            )
        for name in ("origin", "runner_version", "verification_authority"):
            if not getattr(self, name):
                raise ValueError(name)
        for name in ("artifact_digest", "policy_digest"):
            if not _DIGEST.fullmatch(getattr(self, name)):
                raise ValueError(name)


def require_runner_authority(
    provenance: RunnerProvenance, operation: RunnerOperation
) -> None:
    """Reject operations outside the closed runner-provenance authority table."""

    trusted_states = frozenset(
        {
            RunnerState.VERIFIED_INSTALLED_DISTRIBUTION,
            RunnerState.VERIFIED_RELEASE_ARTIFACT,
            RunnerState.RECEIPT_PROVEN_GENERATION,
            RunnerState.OPERATOR_VERIFIED_COMMIT_ARTIFACT,
        }
    )
    if not isinstance(provenance, RunnerProvenance) or not isinstance(
        operation, RunnerOperation
    ):
        allowed = False
    elif provenance.state in trusted_states:
        allowed = operation in frozenset(RunnerOperation)
    elif provenance.state is RunnerState.UNVERIFIED_SELF_ROOT:
        allowed = operation in {
            RunnerOperation.DIAGNOSTIC,
            RunnerOperation.PLAN,
        }
    else:
        allowed = False
    if not allowed:
        raise ForgeError(
            code="runner.untrusted",
            exit_category=15,
            safe_message="The current runner is not authorized for this operation.",
            recovery_instructions=("Use a verified installer runner.",),
        )


def _consume_members(members: list[int], amount: int, *, record: bool) -> None:
    members[0] += amount
    if members[0] > _MAX_JSON_MEMBERS:
        if record:
            raise ForgeError(
                "record.limit_exceeded",
                10,
                "Persistent record contains too many members.",
            )
        raise ForgeError(
            "diagnostic.value_rejected", 10, "Value contains too many members."
        )


def _canonical_value(
    value: object, *, depth: int = 0, members: list[int] | None = None
) -> object:
    if members is None:
        members = [0]
    if depth > _MAX_CANONICAL_DEPTH:
        raise ForgeError(
            "diagnostic.value_rejected", 10, "Value nesting exceeds the trusted limit."
        )
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ForgeError(
                "diagnostic.value_rejected", 10, "Non-finite numbers are not allowed."
            )
        return value
    if isinstance(value, Enum):
        return _canonical_value(value.value, depth=depth, members=members)
    if is_dataclass(value) and not isinstance(value, type):
        value = {field.name: getattr(value, field.name) for field in fields(value)}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ForgeError(
                "diagnostic.value_rejected", 10, "Object keys must be strings."
            )
        _consume_members(members, len(value), record=False)
        return {
            key: _canonical_value(item, depth=depth + 1, members=members)
            for key, item in sorted(value.items())
        }
    if isinstance(value, (list, tuple)):
        _consume_members(members, len(value), record=False)
        return [
            _canonical_value(item, depth=depth + 1, members=members) for item in value
        ]
    raise ForgeError("diagnostic.value_rejected", 10, "Unsupported value type.")


def canonical_json_bytes(value: object, *, final_newline: bool = False) -> bytes:
    """Serialize a bounded JSON value in the one canonical Forge representation."""

    try:
        rendered = json.dumps(
            _canonical_value(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, UnicodeEncodeError, ValueError) as exc:
        raise ForgeError(
            "diagnostic.value_rejected", 10, "Value cannot be rendered safely."
        ) from exc
    result = rendered + (b"\n" if final_newline else b"")
    if len(result) > _MAX_RECORD_BYTES:
        raise ForgeError(
            "diagnostic.value_rejected", 10, "Value exceeds the trusted byte limit."
        )
    return result


def _validate_decoded_bounds(
    value: object, *, depth: int = 0, members: list[int] | None = None
) -> None:
    if members is None:
        members = [0]
    if depth > _MAX_CANONICAL_DEPTH:
        raise ForgeError(
            "record.limit_exceeded", 10, "Persistent record nesting is too deep."
        )
    if isinstance(value, Mapping):
        _consume_members(members, len(value), record=True)
        for item in value.values():
            _validate_decoded_bounds(item, depth=depth + 1, members=members)
    elif isinstance(value, list):
        _consume_members(members, len(value), record=True)
        for item in value:
            _validate_decoded_bounds(item, depth=depth + 1, members=members)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ForgeError(
                "record.duplicate_key",
                10,
                "Persistent record contains a duplicate key.",
            )
        value[key] = item
    return value


def decode_persistent_record(
    raw: bytes,
    *,
    supported_major: int = 1,
    reader_version: str | None = None,
) -> Mapping[str, object]:
    """Decode a persistent JSON record without performing filesystem effects."""

    if len(raw) > _MAX_RECORD_BYTES:
        raise ForgeError(
            "record.limit_exceeded", 10, "Persistent record exceeds the trusted limit."
        )
    try:
        decoded = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except ForgeError:
        raise
    except RecursionError as exc:
        raise ForgeError(
            "record.limit_exceeded", 10, "Persistent record nesting is too deep."
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ForgeError(
            "record.invalid", 10, "Persistent record is not valid canonical JSON."
        ) from exc
    _validate_decoded_bounds(decoded)
    if not isinstance(decoded, dict):
        raise ForgeError(
            "record.invalid", 10, "Persistent record root must be an object."
        )
    schema_version = decoded.get("schema_version")
    if not isinstance(schema_version, str) or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+)*", schema_version
    ):
        raise ForgeError(
            "record.invalid", 10, "Persistent record schema version is invalid."
        )
    if int(schema_version.split(".", 1)[0]) != supported_major:
        raise ForgeError(
            "record.reader_unsupported",
            10,
            "Persistent record requires an unsupported reader.",
        )
    required = {
        "schema_digest",
        "writer_version",
        "minimum_reader_version",
        "record_digest",
    }
    if any(
        not isinstance(decoded.get(name), str) or not decoded[name] for name in required
    ):
        raise ForgeError(
            "record.invalid", 10, "Persistent record header is incomplete."
        )
    if not _DIGEST.fullmatch(decoded["schema_digest"]) or not _DIGEST.fullmatch(
        decoded["record_digest"]
    ):
        raise ForgeError(
            "record.invalid", 10, "Persistent record digest header is invalid."
        )
    _release_version(decoded["writer_version"])
    running_version = (
        distribution_version("zagrosi-forge")
        if reader_version is None
        else reader_version
    )
    if _release_version(decoded["minimum_reader_version"]) > _release_version(
        running_version
    ):
        raise ForgeError(
            "record.reader_unsupported",
            10,
            "Persistent record requires a newer reader.",
        )
    expected_digest = decoded["record_digest"]
    digest_input = dict(decoded)
    del digest_input["record_digest"]
    actual_digest = hashlib.sha256(canonical_json_bytes(digest_input)).hexdigest()
    if expected_digest != actual_digest:
        raise ForgeError(
            "record.digest_mismatch", 10, "Persistent record digest does not match."
        )
    if raw != canonical_json_bytes(decoded, final_newline=True):
        raise ForgeError(
            "record.noncanonical",
            10,
            "Persistent record bytes are not canonical.",
        )
    return MappingProxyType(
        {key: _freeze_value(item) for key, item in sorted(decoded.items())}
    )


def load_trusted_policy(resource_name: str, expected_schema_digest: str) -> object:
    """Load policy only from installed package resources and bind its schema digest."""

    if not re.fullmatch(r"[a-z0-9-]+\.json", resource_name):
        raise ForgeError(
            "policy.resource_rejected", 10, "Trusted policy resource name is invalid."
        )
    raw = resources.files("zagrosi_forge.install").joinpath(resource_name).read_bytes()
    decoded = decode_persistent_record(raw)
    if decoded.get("schema_digest") != expected_schema_digest:
        raise ForgeError(
            "policy.schema_mismatch", 10, "Trusted policy schema digest does not match."
        )
    return decoded
