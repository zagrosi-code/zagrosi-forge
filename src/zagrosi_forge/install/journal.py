"""Capability-bound immutable transaction journal records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import errno
import hashlib
import hmac
from importlib import resources
import json
import math
import os
import re
import stat
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Never, cast

from . import paths as _paths
from .contracts import (
    ForgeError,
    InstallIdentity,
    RunnerOperation,
    RunnerProvenance,
    RunnerState,
    require_runner_authority,
)
from .paths import FileIdentity, PathProof, validate_reference
from .policies import LIMIT_POLICY

if TYPE_CHECKING:
    from .atomic_file import ConfigRecoveryDescriptor
    from .ownership import PendingTransactionObservation


JOURNAL_SCHEMA_DIGEST = (
    "a9e78030123ef3ff1c9b8940c526245252f5b0c857ad76a0910bd2f08616cde6"
)
JOURNAL_SCHEMA_VERSION = "1.0"
JOURNAL_WRITER_VERSION = "0.2.0"
JOURNAL_MINIMUM_READER_VERSION = "0.2.0"
JOURNAL_STATE_MACHINE_VERSION = "1.0"
JOURNAL_POLICY_VERSION = "1.0"
_SCHEMA_RESOURCE = "schemas/transaction-journal-v1.schema.json"
_RECORD_NAME = re.compile(r"journal-([0-9]{8})\.json\Z")
_TRANSACTION_ID = re.compile(r"tx-[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_ROLE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_ZERO_DIGEST = "0" * 64
_RECORD_TOKEN = object()
_HEAD_TOKEN = object()
_LOADED_TOKEN = object()
_STORE_TOKEN = object()


def _error(code: str, message: str) -> ForgeError:
    return ForgeError(
        code,
        14,
        message,
        recovery_instructions=("Preserve the transaction directory for inspection.",),
    )


def _corrupt(message: str = "The transaction journal is corrupt.") -> ForgeError:
    return _error("journal.corrupt", message)


def _unsupported() -> ForgeError:
    return _error(
        "journal.unsupported_schema",
        "The transaction journal requires an unsupported reader.",
    )


def _limit() -> ForgeError:
    return _error(
        "journal.limit_exceeded",
        "The pending transaction journal quota is exceeded.",
    )


def _valid_digest(value: object, *, optional: bool = False) -> bool:
    return (optional and value is None) or (
        type(value) is str and _DIGEST.fullmatch(value) is not None
    )


def _valid_file_identity(value: object, *, optional: bool = False) -> bool:
    return (optional and value is None) or (
        type(value) is tuple
        and len(value) == 2
        and all(type(item) is int and item >= 0 for item in value)
    )


def _validated_reference(value: object) -> str:
    if type(value) is not str:
        raise ValueError("relative path")
    result = validate_reference(value, role="journal", limits=LIMIT_POLICY)
    if not result.is_ok:
        result = _paths._validate_internal_reference(
            value,
            role="journal",
            limits=LIMIT_POLICY,
        )
    if not result.is_ok or result.unwrap().value != value:
        raise ValueError("relative path")
    return value


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in sorted(value.items())}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _json_value(
    value: object,
    *,
    depth: int = 0,
    members: list[int] | None = None,
) -> object:
    if members is None:
        members = [0]
    if depth > LIMIT_POLICY.value("json_depth"):
        raise ValueError("journal JSON depth")
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("journal JSON number")
        return value
    if isinstance(value, Enum):
        return _json_value(value.value, depth=depth, members=members)
    if is_dataclass(value) and not isinstance(value, type):
        value = {field.name: getattr(value, field.name) for field in fields(value)}
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("journal JSON key")
        members[0] += len(value)
        if members[0] > LIMIT_POLICY.value("json_members"):
            raise ValueError("journal JSON members")
        return {
            key: _json_value(item, depth=depth + 1, members=members)
            for key, item in sorted(cast(Mapping[str, object], value).items())
        }
    if isinstance(value, (list, tuple)):
        members[0] += len(value)
        if members[0] > LIMIT_POLICY.value("json_members"):
            raise ValueError("journal JSON members")
        return [_json_value(item, depth=depth + 1, members=members) for item in value]
    raise ValueError("journal JSON value")


def _render(value: object, *, final_newline: bool) -> bytes:
    try:
        raw = json.dumps(
            _json_value(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (OverflowError, RecursionError, TypeError, UnicodeEncodeError, ValueError):
        raise _corrupt(
            "The transaction journal cannot be rendered canonically."
        ) from None
    return raw + (b"\n" if final_newline else b"")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _corrupt("The transaction journal contains a duplicate key.")
        value[key] = item
    return value


def _decode_json(raw: bytes) -> Mapping[str, object]:
    if len(raw) > LIMIT_POLICY.value("journal_record_bytes"):
        raise _limit()
    try:
        decoded = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except ForgeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise _corrupt() from None
    if not isinstance(decoded, dict):
        raise _corrupt()
    try:
        _json_value(decoded)
    except ValueError:
        raise _corrupt() from None
    return cast(Mapping[str, object], decoded)


def _exact_mapping(
    value: object,
    keys: frozenset[str],
    *,
    field: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(field)
    return cast(Mapping[str, object], value)


def _decoded_identity(value: object, *, optional: bool = False) -> FileIdentity | None:
    if optional and value is None:
        return None
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(type(item) is not int or item < 0 for item in value)
    ):
        raise ValueError("file identity")
    return cast(tuple[int, int], tuple(value))


@dataclass(frozen=True, slots=True)
class JournalConfigIdentity:
    parent_identity: FileIdentity
    leaf_identity: FileIdentity | None
    byte_digest: str
    semantic_digest: str
    metadata_fingerprint: str
    snapshot_digest: str
    target_metadata_digest: str | None

    def __post_init__(self) -> None:
        if not _valid_file_identity(self.parent_identity) or not _valid_file_identity(
            self.leaf_identity, optional=True
        ):
            raise ValueError("config identity")
        if any(
            not _valid_digest(getattr(self, field))
            for field in (
                "byte_digest",
                "semantic_digest",
                "metadata_fingerprint",
                "snapshot_digest",
            )
        ) or not _valid_digest(self.target_metadata_digest, optional=True):
            raise ValueError("config identity digest")


@dataclass(frozen=True, slots=True)
class JournalPathIdentity:
    role: str
    relative_path: str
    parent_identity: FileIdentity
    leaf_identity: FileIdentity | None
    content_digest: str | None

    def __post_init__(self) -> None:
        if type(self.role) is not str or _ROLE.fullmatch(self.role) is None:
            raise ValueError("journal identity role")
        _validated_reference(self.relative_path)
        if not _valid_file_identity(self.parent_identity) or not _valid_file_identity(
            self.leaf_identity, optional=True
        ):
            raise ValueError("journal path identity")
        if not _valid_digest(self.content_digest, optional=True):
            raise ValueError("journal path digest")


@dataclass(frozen=True, slots=True)
class TransactionOwnedPath:
    role: str
    relative_path: str
    expected_identity: FileIdentity | None

    def __post_init__(self) -> None:
        if type(self.role) is not str or _ROLE.fullmatch(self.role) is None:
            raise ValueError("transaction path role")
        _validated_reference(self.relative_path)
        if not _valid_file_identity(self.expected_identity, optional=True):
            raise ValueError("transaction path identity")


@dataclass(frozen=True, slots=True)
class RollbackAction:
    action: str
    relative_path: str
    expected_identity: FileIdentity | None

    def __post_init__(self) -> None:
        if self.action not in {"quarantine-if-owned", "retain"}:
            raise ValueError("rollback action")
        _validated_reference(self.relative_path)
        if not _valid_file_identity(
            self.expected_identity, optional=self.action == "retain"
        ):
            raise ValueError("rollback identity")


@dataclass(frozen=True, slots=True)
class PreparedTransaction:
    transaction_id: str
    effective_marketplace_id: str
    config_transaction_digest: str
    plan_digest: str
    runner_provenance: RunnerProvenance
    install_identity: InstallIdentity
    before_relation_digest: str | None
    candidate_relation_digest: str
    before_config: JournalConfigIdentity
    candidate_config: JournalConfigIdentity
    identities: tuple[JournalPathIdentity, ...]
    transaction_owned_paths: tuple[TransactionOwnedPath, ...]
    rollback_actions: tuple[RollbackAction, ...]
    prepared_receipt: Mapping[str, object]
    verification_evidence_digest: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.transaction_id) is not str
            or _TRANSACTION_ID.fullmatch(self.transaction_id) is None
            or type(self.effective_marketplace_id) is not str
            or _IDENTIFIER.fullmatch(self.effective_marketplace_id) is None
            or not _valid_digest(self.config_transaction_digest)
            or self.config_transaction_digest
            != hashlib.sha256(self.transaction_id.encode("utf-8")).hexdigest()
            or not _valid_digest(self.plan_digest)
            or type(self.runner_provenance) is not RunnerProvenance
            or type(self.install_identity) is not InstallIdentity
            or not _valid_digest(self.before_relation_digest, optional=True)
            or not _valid_digest(self.candidate_relation_digest)
            or type(self.before_config) is not JournalConfigIdentity
            or type(self.candidate_config) is not JournalConfigIdentity
            or self.before_config.target_metadata_digest is not None
            or self.candidate_config.target_metadata_digest is None
            or not _valid_digest(self.verification_evidence_digest, optional=True)
        ):
            raise ValueError("prepared transaction")
        require_runner_authority(self.runner_provenance, RunnerOperation.MUTATE)
        if (
            type(self.identities) is not tuple
            or not self.identities
            or any(type(item) is not JournalPathIdentity for item in self.identities)
            or type(self.transaction_owned_paths) is not tuple
            or not self.transaction_owned_paths
            or any(
                type(item) is not TransactionOwnedPath
                for item in self.transaction_owned_paths
            )
            or type(self.rollback_actions) is not tuple
            or not self.rollback_actions
            or any(type(item) is not RollbackAction for item in self.rollback_actions)
        ):
            raise ValueError("prepared transaction collections")
        identities = tuple(
            sorted(self.identities, key=lambda item: (item.role, item.relative_path))
        )
        owned = tuple(
            sorted(
                self.transaction_owned_paths,
                key=lambda item: (item.role, item.relative_path),
            )
        )
        rollback = tuple(
            sorted(
                self.rollback_actions,
                key=lambda item: (item.action, item.relative_path),
            )
        )
        if (
            len({(item.role, item.relative_path) for item in identities})
            != len(identities)
            or len({(item.role, item.relative_path) for item in owned}) != len(owned)
            or len({item.relative_path for item in rollback}) != len(rollback)
            or not all(
                any(
                    path.relative_path == action.relative_path
                    and path.expected_identity == action.expected_identity
                    for path in owned
                )
                for action in rollback
            )
        ):
            raise ValueError("prepared transaction paths")
        expected_root = f".zagrosi/transactions/{self.transaction_id}"
        roots = tuple(
            item
            for item in identities
            if item.role == "transaction-root"
            and item.relative_path == expected_root
            and item.leaf_identity is not None
        )
        owned_roots = tuple(
            item
            for item in owned
            if item.role == "transaction-root"
            and item.relative_path == expected_root
            and item.expected_identity is not None
        )
        if len(roots) != 1 or len(owned_roots) != 1:
            raise ValueError("persistent transaction root")
        if (
            roots[0].leaf_identity != owned_roots[0].expected_identity
            or not isinstance(self.prepared_receipt, Mapping)
            or not self.prepared_receipt
        ):
            raise ValueError("prepared transaction binding")
        try:
            prepared_receipt = cast(
                Mapping[str, object], _freeze(_json_value(self.prepared_receipt))
            )
            receipt_raw = _render(prepared_receipt, final_newline=True)
            from .ownership import _decode_committed_receipt

            decoded_receipt = _decode_committed_receipt(receipt_raw)
            transaction = cast(
                Mapping[str, object], decoded_receipt.record["transaction"]
            )
            lineage = transaction["lineage"]
            if (
                decoded_receipt.identity != self.install_identity
                or decoded_receipt.effective_marketplace_id
                != self.effective_marketplace_id
                or transaction["id"] != self.transaction_id
                or not isinstance(lineage, tuple)
                or lineage[-1] != self.transaction_id
                or decoded_receipt.config.before_digest
                != self.before_config.byte_digest
                or decoded_receipt.config.after_digest
                != self.candidate_config.byte_digest
            ):
                raise ValueError("prepared receipt binding")
            source_paths = tuple(
                item for item in identities if item.role == "source-generation"
            )
            cache_paths = tuple(
                item for item in identities if item.role == "cache-generation"
            )
            if (
                len(source_paths) != 1
                or source_paths[0].relative_path != decoded_receipt.source.relative_path
                or source_paths[0].content_digest
                != decoded_receipt.source.manifest_digest
                or len(cache_paths) != 1
                or cache_paths[0].relative_path != decoded_receipt.cache.relative_path
                or cache_paths[0].content_digest
                != decoded_receipt.cache.manifest_digest
            ):
                raise ValueError("prepared generation binding")
        except (ForgeError, KeyError, TypeError, ValueError):
            raise ValueError("prepared receipt") from None
        object.__setattr__(self, "identities", identities)
        object.__setattr__(self, "transaction_owned_paths", owned)
        object.__setattr__(self, "rollback_actions", rollback)
        object.__setattr__(self, "prepared_receipt", prepared_receipt)


class JournalState(str, Enum):
    PREPARED = "PREPARED"
    STAGED = "STAGED"
    VERIFIED = "VERIFIED"
    SOURCE_PUBLISHED = "SOURCE_PUBLISHED"
    CACHE_PUBLISHED = "CACHE_PUBLISHED"
    PUBLISHED = "PUBLISHED"
    COMMIT_INTENT = "COMMIT_INTENT"
    CONFIG_COMMITTED = "CONFIG_COMMITTED"
    RECEIPT_COMMITTED = "RECEIPT_COMMITTED"
    FINALIZED = "FINALIZED"
    ROLLED_BACK = "ROLLED_BACK"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


_FORWARD = {
    JournalState.PREPARED: JournalState.STAGED,
    JournalState.STAGED: JournalState.VERIFIED,
    JournalState.VERIFIED: JournalState.SOURCE_PUBLISHED,
    JournalState.SOURCE_PUBLISHED: JournalState.CACHE_PUBLISHED,
    JournalState.CACHE_PUBLISHED: JournalState.PUBLISHED,
    JournalState.PUBLISHED: JournalState.COMMIT_INTENT,
    JournalState.COMMIT_INTENT: JournalState.CONFIG_COMMITTED,
    JournalState.CONFIG_COMMITTED: JournalState.RECEIPT_COMMITTED,
    JournalState.RECEIPT_COMMITTED: JournalState.FINALIZED,
}
_PRE_COMMIT = frozenset(
    {
        JournalState.PREPARED,
        JournalState.STAGED,
        JournalState.VERIFIED,
        JournalState.SOURCE_PUBLISHED,
        JournalState.CACHE_PUBLISHED,
        JournalState.PUBLISHED,
        JournalState.COMMIT_INTENT,
    }
)
_TERMINAL = frozenset(
    {
        JournalState.FINALIZED,
        JournalState.ROLLED_BACK,
        JournalState.RECOVERY_REQUIRED,
    }
)


@dataclass(frozen=True, slots=True)
class JournalTransition:
    state: JournalState
    identities: tuple[JournalPathIdentity, ...] | None = None
    verification_evidence_digest: str | None = None
    config_recovery: ConfigRecoveryDescriptor | None = None
    source_result: JournalPathIdentity | None = None
    cache_result: JournalPathIdentity | None = None
    config_result: JournalConfigIdentity | None = None
    receipt_result: JournalPathIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, JournalState):
            raise ValueError("journal transition state")
        if self.identities is not None and (
            type(self.identities) is not tuple
            or not self.identities
            or any(type(item) is not JournalPathIdentity for item in self.identities)
        ):
            raise ValueError("journal transition identities")
        if not _valid_digest(self.verification_evidence_digest, optional=True):
            raise ValueError("journal verification digest")
        if any(
            value is not None and type(value) is not expected
            for value, expected in (
                (self.source_result, JournalPathIdentity),
                (self.cache_result, JournalPathIdentity),
                (self.config_result, JournalConfigIdentity),
                (self.receipt_result, JournalPathIdentity),
            )
        ):
            raise ValueError("journal transition result")
        if self.config_recovery is not None:
            from .atomic_file import ConfigRecoveryDescriptor

            if type(self.config_recovery) is not ConfigRecoveryDescriptor:
                raise ValueError("config recovery descriptor")
            self.config_recovery._require_valid()


@dataclass(frozen=True, slots=True, init=False)
class JournalRecord:
    state: JournalState
    sequence: int
    previous_record_digest: str
    record_digest: str
    transaction_id: str
    plan_digest: str
    prepared: PreparedTransaction
    identities: tuple[JournalPathIdentity, ...]
    verification_evidence_digest: str | None
    config_recovery: Mapping[str, object] | None
    committed_config: JournalConfigIdentity | None
    record: Mapping[str, object]
    raw_size: int
    _binding_digest: str
    _seal: object

    def __init__(
        self,
        *,
        state: JournalState,
        sequence: int,
        previous_record_digest: str,
        record_digest: str,
        transaction_id: str,
        plan_digest: str,
        prepared: PreparedTransaction,
        identities: tuple[JournalPathIdentity, ...],
        verification_evidence_digest: str | None,
        config_recovery: Mapping[str, object] | None,
        committed_config: JournalConfigIdentity | None,
        record: Mapping[str, object],
        raw_size: int,
        _token: object,
    ) -> None:
        if _token is not _RECORD_TOKEN:
            raise TypeError("journal records are loaded only by JournalStore")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "previous_record_digest", previous_record_digest)
        object.__setattr__(self, "record_digest", record_digest)
        object.__setattr__(self, "transaction_id", transaction_id)
        object.__setattr__(self, "plan_digest", plan_digest)
        object.__setattr__(self, "prepared", prepared)
        object.__setattr__(self, "identities", identities)
        object.__setattr__(
            self,
            "verification_evidence_digest",
            verification_evidence_digest,
        )
        object.__setattr__(self, "config_recovery", config_recovery)
        object.__setattr__(self, "committed_config", committed_config)
        object.__setattr__(self, "record", record)
        object.__setattr__(self, "raw_size", raw_size)
        _require_journal_record(self)
        object.__setattr__(
            self,
            "_binding_digest",
            _journal_record_binding_digest(self),
        )
        object.__setattr__(self, "_seal", _RECORD_TOKEN)
        self._require_valid()

    def _require_valid(self) -> None:
        try:
            _require_journal_record(self)
            expected = _journal_record_binding_digest(self)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise TypeError("journal record authority changed") from None
        if (
            self._seal is not _RECORD_TOKEN
            or not _valid_digest(self._binding_digest)
            or not hmac.compare_digest(self._binding_digest, expected)
        ):
            raise TypeError("journal record authority changed")

    def __reduce__(self) -> Never:
        raise TypeError("journal evidence is not serializable")


@dataclass(frozen=True, slots=True, init=False)
class JournalHead:
    transaction_binding_digest: str
    sequence: int
    state: JournalState
    record_digest: str
    _seal: object

    def __init__(
        self,
        *,
        transaction_binding_digest: str,
        sequence: int,
        state: JournalState,
        record_digest: str,
        _token: object,
    ) -> None:
        if (
            _token is not _HEAD_TOKEN
            or not _valid_digest(transaction_binding_digest)
            or type(sequence) is not int
            or sequence < 0
            or not isinstance(state, JournalState)
            or not _valid_digest(record_digest)
        ):
            raise TypeError("journal heads are loaded only by JournalStore")
        object.__setattr__(
            self, "transaction_binding_digest", transaction_binding_digest
        )
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "record_digest", record_digest)
        object.__setattr__(self, "_seal", _HEAD_TOKEN)

    def _require_valid(self) -> None:
        if (
            self._seal is not _HEAD_TOKEN
            or not _valid_digest(self.transaction_binding_digest)
            or type(self.sequence) is not int
            or self.sequence < 0
            or not isinstance(self.state, JournalState)
            or not _valid_digest(self.record_digest)
        ):
            raise TypeError("journal head authority changed")

    def __reduce__(self) -> Never:
        raise TypeError("journal heads are not serializable")


@dataclass(frozen=True, slots=True, init=False)
class LoadedJournal:
    head: JournalHead
    records: tuple[JournalRecord, ...]
    byte_size: int
    _binding_digest: str
    _seal: object

    def __init__(
        self,
        *,
        head: JournalHead,
        records: tuple[JournalRecord, ...],
        byte_size: int,
        _token: object,
    ) -> None:
        if _token is not _LOADED_TOKEN:
            raise TypeError("journal evidence is loaded only by JournalStore")
        object.__setattr__(self, "head", head)
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "byte_size", byte_size)
        object.__setattr__(
            self,
            "_binding_digest",
            _loaded_journal_binding_digest(self),
        )
        object.__setattr__(self, "_seal", _LOADED_TOKEN)
        self._require_valid()

    def _require_valid(self) -> None:
        try:
            _require_loaded_journal(self)
            expected = _loaded_journal_binding_digest(self)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise TypeError("loaded journal authority changed") from None
        if (
            self._seal is not _LOADED_TOKEN
            or not _valid_digest(self._binding_digest)
            or not hmac.compare_digest(self._binding_digest, expected)
        ):
            raise TypeError("loaded journal authority changed")

    def __reduce__(self) -> Never:
        raise TypeError("journal evidence is not serializable")


_RECORD_KEYS = frozenset(
    {
        "before_config",
        "before_relation_digest",
        "candidate_config",
        "candidate_relation_digest",
        "committed_config",
        "config_recovery",
        "config_transaction_digest",
        "effective_marketplace_id",
        "identities",
        "install_identity",
        "limit_policy_version",
        "minimum_reader_version",
        "plan_digest",
        "policy_version",
        "prepared_receipt",
        "previous_record_digest",
        "record_digest",
        "rollback_actions",
        "runner_provenance",
        "schema_digest",
        "schema_version",
        "sequence",
        "state",
        "state_machine_version",
        "transaction_binding",
        "transaction_id",
        "transaction_owned_paths",
        "verification_evidence_digest",
        "writer_version",
    }
)


def _runner_projection(value: RunnerProvenance) -> Mapping[str, object]:
    return {
        "artifact_digest": value.artifact_digest,
        "origin": value.origin,
        "policy_digest": value.policy_digest,
        "runner_version": value.runner_version,
        "state": value.state.value,
        "verification_authority": value.verification_authority,
    }


def _install_projection(value: InstallIdentity) -> Mapping[str, object]:
    return {
        "base_payload_digest": value.base_payload_digest,
        "base_version": value.base_version,
        "contract_versions": value.contract_versions,
        "install_version": value.install_version,
        "marketplace_id": value.marketplace_id,
        "plugin_id": value.plugin_id,
        "policy_digest": value.policy_digest,
        "rendered_payload_digest": value.rendered_payload_digest,
        "transformation_profile": value.transformation_profile,
    }


def _config_projection(value: JournalConfigIdentity) -> Mapping[str, object]:
    return {
        "byte_digest": value.byte_digest,
        "leaf_identity": value.leaf_identity,
        "metadata_fingerprint": value.metadata_fingerprint,
        "parent_identity": value.parent_identity,
        "semantic_digest": value.semantic_digest,
        "snapshot_digest": value.snapshot_digest,
        "target_metadata_digest": value.target_metadata_digest,
    }


def _path_projection(value: JournalPathIdentity) -> Mapping[str, object]:
    return {
        "content_digest": value.content_digest,
        "leaf_identity": value.leaf_identity,
        "parent_identity": value.parent_identity,
        "relative_path": value.relative_path,
        "role": value.role,
    }


def _owned_projection(value: TransactionOwnedPath) -> Mapping[str, object]:
    return {
        "expected_identity": value.expected_identity,
        "relative_path": value.relative_path,
        "role": value.role,
    }


def _rollback_projection(value: RollbackAction) -> Mapping[str, object]:
    return {
        "action": value.action,
        "expected_identity": value.expected_identity,
        "relative_path": value.relative_path,
    }


def _prepared_projection(value: PreparedTransaction) -> Mapping[str, object]:
    return {
        "before_config": _config_projection(value.before_config),
        "before_relation_digest": value.before_relation_digest,
        "candidate_config": _config_projection(value.candidate_config),
        "candidate_relation_digest": value.candidate_relation_digest,
        "config_transaction_digest": value.config_transaction_digest,
        "effective_marketplace_id": value.effective_marketplace_id,
        "identities": tuple(_path_projection(item) for item in value.identities),
        "install_identity": _install_projection(value.install_identity),
        "plan_digest": value.plan_digest,
        "prepared_receipt": value.prepared_receipt,
        "rollback_actions": tuple(
            _rollback_projection(item) for item in value.rollback_actions
        ),
        "runner_provenance": _runner_projection(value.runner_provenance),
        "transaction_id": value.transaction_id,
        "transaction_owned_paths": tuple(
            _owned_projection(item) for item in value.transaction_owned_paths
        ),
        "verification_evidence_digest": value.verification_evidence_digest,
    }


def _journal_record_binding_projection(
    value: JournalRecord,
) -> Mapping[str, object]:
    return {
        "committed_config": (
            None
            if value.committed_config is None
            else _config_projection(value.committed_config)
        ),
        "config_recovery": value.config_recovery,
        "identities": tuple(_path_projection(item) for item in value.identities),
        "plan_digest": value.plan_digest,
        "prepared": _prepared_projection(value.prepared),
        "previous_record_digest": value.previous_record_digest,
        "raw_size": value.raw_size,
        "record": value.record,
        "record_digest": value.record_digest,
        "sequence": value.sequence,
        "state": value.state.value,
        "transaction_id": value.transaction_id,
        "verification_evidence_digest": value.verification_evidence_digest,
    }


def _journal_record_binding_digest(value: JournalRecord) -> str:
    return hashlib.sha256(
        _render(_journal_record_binding_projection(value), final_newline=False)
    ).hexdigest()


def _require_journal_record(value: JournalRecord) -> None:
    if (
        type(value.state) is not JournalState
        or type(value.sequence) is not int
        or value.sequence < 0
        or not _valid_digest(value.previous_record_digest)
        or not _valid_digest(value.record_digest)
        or type(value.transaction_id) is not str
        or _TRANSACTION_ID.fullmatch(value.transaction_id) is None
        or not _valid_digest(value.plan_digest)
        or type(value.prepared) is not PreparedTransaction
        or type(value.identities) is not tuple
        or any(type(item) is not JournalPathIdentity for item in value.identities)
        or not _valid_digest(value.verification_evidence_digest, optional=True)
        or (
            value.config_recovery is not None
            and type(value.config_recovery) is not MappingProxyType
        )
        or (
            value.committed_config is not None
            and type(value.committed_config) is not JournalConfigIdentity
        )
        or type(value.record) is not MappingProxyType
        or set(value.record) != _RECORD_KEYS
        or type(value.raw_size) is not int
        or value.raw_size <= 0
        or value.raw_size > LIMIT_POLICY.value("journal_record_bytes")
    ):
        raise TypeError("journal record evidence")

    raw = _render(value.record, final_newline=True)
    if value.raw_size != len(raw):
        raise ValueError("journal record size")
    digest_input = dict(value.record)
    embedded_digest = digest_input.pop("record_digest")
    if (
        embedded_digest != value.record_digest
        or hashlib.sha256(_render(digest_input, final_newline=False)).hexdigest()
        != value.record_digest
    ):
        raise ValueError("journal record digest")

    prepared = _prepared_from_record(value.record)
    prepared_projection = _json_value(_prepared_projection(prepared))
    if (
        _json_value(_prepared_projection(value.prepared)) != prepared_projection
        or {
            key: _json_value(value.record[key])
            for key in _prepared_projection(prepared)
        }
        != prepared_projection
        or value.transaction_id != prepared.transaction_id
        or value.plan_digest != prepared.plan_digest
        or value.identities != prepared.identities
        or value.verification_evidence_digest != prepared.verification_evidence_digest
        or value.state.value != value.record["state"]
        or value.sequence != value.record["sequence"]
        or value.previous_record_digest != value.record["previous_record_digest"]
    ):
        raise ValueError("journal prepared evidence")

    config_recovery_value = value.record["config_recovery"]
    config_recovery = _decode_config_recovery(
        (None if config_recovery_value is None else _json_value(config_recovery_value)),
        prepared=prepared,
        transaction_digest=prepared.config_transaction_digest,
    )
    committed_value = value.record["committed_config"]
    committed_config = (
        None if committed_value is None else _parse_config(committed_value)
    )
    if _json_value(value.config_recovery) != _json_value(config_recovery) or (
        None
        if value.committed_config is None
        else _json_value(_config_projection(value.committed_config))
    ) != (
        None
        if committed_config is None
        else _json_value(_config_projection(committed_config))
    ):
        raise ValueError("journal transition evidence")


def _loaded_journal_binding_projection(
    value: LoadedJournal,
) -> Mapping[str, object]:
    return {
        "byte_size": value.byte_size,
        "head": {
            "record_digest": value.head.record_digest,
            "sequence": value.head.sequence,
            "state": value.head.state.value,
            "transaction_binding_digest": value.head.transaction_binding_digest,
        },
        "record_binding_digests": tuple(
            record._binding_digest for record in value.records
        ),
    }


def _loaded_journal_binding_digest(value: LoadedJournal) -> str:
    return hashlib.sha256(
        _render(_loaded_journal_binding_projection(value), final_newline=False)
    ).hexdigest()


def _require_loaded_journal(value: LoadedJournal) -> None:
    if (
        type(value.head) is not JournalHead
        or type(value.records) is not tuple
        or not value.records
        or any(type(record) is not JournalRecord for record in value.records)
        or type(value.byte_size) is not int
        or value.byte_size <= 0
        or value.byte_size > LIMIT_POLICY.value("journal_total_bytes")
    ):
        raise TypeError("loaded journal evidence")
    value.head._require_valid()
    for record in value.records:
        record._require_valid()
    if not _records_form_chain(value.records):
        raise ValueError("loaded journal chain")
    last = value.records[-1]
    binding = value.records[0].record["transaction_binding"]
    binding_digest = hashlib.sha256(_render(binding, final_newline=False)).hexdigest()
    if (
        value.byte_size != sum(record.raw_size for record in value.records)
        or value.head.sequence != last.sequence
        or value.head.state is not last.state
        or value.head.record_digest != last.record_digest
        or not hmac.compare_digest(
            value.head.transaction_binding_digest,
            binding_digest,
        )
    ):
        raise ValueError("loaded journal head")


def _record_projection(
    prepared: PreparedTransaction,
    *,
    transaction_binding: Mapping[str, object],
    state: JournalState,
    sequence: int,
    previous_record_digest: str,
) -> dict[str, object]:
    return {
        **_prepared_projection(prepared),
        "committed_config": None,
        "config_recovery": None,
        "limit_policy_version": LIMIT_POLICY.version,
        "minimum_reader_version": JOURNAL_MINIMUM_READER_VERSION,
        "policy_version": JOURNAL_POLICY_VERSION,
        "previous_record_digest": previous_record_digest,
        "schema_digest": JOURNAL_SCHEMA_DIGEST,
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "sequence": sequence,
        "state": state.value,
        "state_machine_version": JOURNAL_STATE_MACHINE_VERSION,
        "transaction_binding": transaction_binding,
        "writer_version": JOURNAL_WRITER_VERSION,
    }


def _seal_record(record: Mapping[str, object]) -> bytes:
    sealed = dict(record)
    sealed["record_digest"] = hashlib.sha256(
        _render(record, final_newline=False)
    ).hexdigest()
    return _render(sealed, final_newline=True)


def _parse_config(value: object) -> JournalConfigIdentity:
    record = _exact_mapping(
        value,
        frozenset(
            {
                "parent_identity",
                "leaf_identity",
                "byte_digest",
                "semantic_digest",
                "metadata_fingerprint",
                "snapshot_digest",
                "target_metadata_digest",
            }
        ),
        field="config identity",
    )
    return JournalConfigIdentity(
        parent_identity=cast(
            FileIdentity, _decoded_identity(record["parent_identity"])
        ),
        leaf_identity=_decoded_identity(record["leaf_identity"], optional=True),
        byte_digest=cast(str, record["byte_digest"]),
        semantic_digest=cast(str, record["semantic_digest"]),
        metadata_fingerprint=cast(str, record["metadata_fingerprint"]),
        snapshot_digest=cast(str, record["snapshot_digest"]),
        target_metadata_digest=cast(str | None, record["target_metadata_digest"]),
    )


def _parse_path_identity(value: object) -> JournalPathIdentity:
    record = _exact_mapping(
        value,
        frozenset(
            {
                "role",
                "relative_path",
                "parent_identity",
                "leaf_identity",
                "content_digest",
            }
        ),
        field="path identity",
    )
    return JournalPathIdentity(
        role=cast(str, record["role"]),
        relative_path=cast(str, record["relative_path"]),
        parent_identity=cast(
            FileIdentity, _decoded_identity(record["parent_identity"])
        ),
        leaf_identity=_decoded_identity(record["leaf_identity"], optional=True),
        content_digest=cast(str | None, record["content_digest"]),
    )


def _parse_owned(value: object) -> TransactionOwnedPath:
    record = _exact_mapping(
        value,
        frozenset({"role", "relative_path", "expected_identity"}),
        field="owned path",
    )
    return TransactionOwnedPath(
        role=cast(str, record["role"]),
        relative_path=cast(str, record["relative_path"]),
        expected_identity=_decoded_identity(record["expected_identity"], optional=True),
    )


def _parse_rollback(value: object) -> RollbackAction:
    record = _exact_mapping(
        value,
        frozenset({"action", "relative_path", "expected_identity"}),
        field="rollback action",
    )
    return RollbackAction(
        action=cast(str, record["action"]),
        relative_path=cast(str, record["relative_path"]),
        expected_identity=_decoded_identity(record["expected_identity"], optional=True),
    )


def _parse_sequence(value: object, parser: Any) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("journal sequence")
    return tuple(parser(item) for item in value)


def _parse_runner(value: object) -> RunnerProvenance:
    record = _exact_mapping(
        value,
        frozenset(
            {
                "state",
                "origin",
                "artifact_digest",
                "runner_version",
                "verification_authority",
                "policy_digest",
            }
        ),
        field="runner provenance",
    )
    return RunnerProvenance(
        state=RunnerState(cast(str, record["state"])),
        origin=cast(str, record["origin"]),
        artifact_digest=cast(str, record["artifact_digest"]),
        runner_version=cast(str, record["runner_version"]),
        verification_authority=cast(str, record["verification_authority"]),
        policy_digest=cast(str, record["policy_digest"]),
    )


def _parse_install(value: object) -> InstallIdentity:
    record = _exact_mapping(
        value,
        frozenset(
            {
                "marketplace_id",
                "plugin_id",
                "base_version",
                "install_version",
                "base_payload_digest",
                "rendered_payload_digest",
                "policy_digest",
                "transformation_profile",
                "contract_versions",
            }
        ),
        field="install identity",
    )
    versions = record["contract_versions"]
    if not isinstance(versions, (list, tuple)):
        raise ValueError("contract versions")
    return InstallIdentity(
        marketplace_id=cast(str, record["marketplace_id"]),
        plugin_id=cast(str, record["plugin_id"]),
        base_version=cast(str, record["base_version"]),
        install_version=cast(str, record["install_version"]),
        base_payload_digest=cast(str, record["base_payload_digest"]),
        rendered_payload_digest=cast(str, record["rendered_payload_digest"]),
        policy_digest=cast(str, record["policy_digest"]),
        transformation_profile=cast(str, record["transformation_profile"]),
        contract_versions=tuple(cast(list[str], versions)),
    )


def _version(value: object) -> tuple[int, int, int]:
    if type(value) is not str or _VERSION.fullmatch(value) is None:
        raise ValueError("version")
    return cast(tuple[int, int, int], tuple(int(part) for part in value.split(".")))


def _transition_allowed(before: JournalState, after: JournalState) -> bool:
    if before in _TERMINAL:
        return False
    if _FORWARD.get(before) is after:
        return True
    if after is JournalState.ROLLED_BACK:
        return before in _PRE_COMMIT
    return after is JournalState.RECOVERY_REQUIRED


def _identities_advance(
    before: tuple[JournalPathIdentity, ...],
    after: tuple[JournalPathIdentity, ...],
) -> bool:
    if len(before) != len(after):
        return False
    for old, new in zip(before, after, strict=True):
        if (
            old.role != new.role
            or old.relative_path != new.relative_path
            or old.parent_identity != new.parent_identity
            or old.content_digest != new.content_digest
            or (
                old.leaf_identity is not None and old.leaf_identity != new.leaf_identity
            )
        ):
            return False
    return True


def _replace_path_result(
    identities: tuple[JournalPathIdentity, ...],
    result: JournalPathIdentity,
    *,
    role: str,
) -> tuple[JournalPathIdentity, ...] | None:
    matches = tuple(item for item in identities if item.role == role)
    if (
        result.role != role
        or len(matches) != 1
        or matches[0].relative_path != result.relative_path
        or matches[0].parent_identity != result.parent_identity
        or matches[0].content_digest != result.content_digest
        or matches[0].leaf_identity is not None
        or result.leaf_identity is None
    ):
        return None
    return tuple(
        sorted(
            (result if item == matches[0] else item for item in identities),
            key=lambda item: (item.role, item.relative_path),
        )
    )


def _append_receipt_result(
    identities: tuple[JournalPathIdentity, ...],
    result: JournalPathIdentity,
    prepared: PreparedTransaction,
) -> tuple[JournalPathIdentity, ...] | None:
    from .contracts import canonical_json_bytes
    from .ownership import committed_receipt_reference

    expected_reference = committed_receipt_reference(
        prepared.effective_marketplace_id,
        prepared.install_identity,
    ).value
    if (
        result.role != "committed-receipt"
        or result.relative_path != expected_reference
        or result.leaf_identity is None
        or result.content_digest
        != hashlib.sha256(
            canonical_json_bytes(prepared.prepared_receipt, final_newline=True)
        ).hexdigest()
        or any(
            item.role == result.role or item.relative_path == result.relative_path
            for item in identities
        )
    ):
        return None
    return tuple(
        sorted((*identities, result), key=lambda item: (item.role, item.relative_path))
    )


def _config_result_matches(
    candidate: JournalConfigIdentity,
    descriptor: Mapping[str, object] | None,
    result: JournalConfigIdentity,
) -> bool:
    if descriptor is None:
        return False
    return (
        result.parent_identity == candidate.parent_identity
        and result.leaf_identity == descriptor.get("candidate_identity")
        and result.byte_digest == candidate.byte_digest
        and result.semantic_digest == candidate.semantic_digest
        and result.target_metadata_digest == candidate.target_metadata_digest
    )


def _config_recovery_matches(
    prepared: PreparedTransaction,
    descriptor: ConfigRecoveryDescriptor,
) -> bool:
    before = prepared.before_config
    candidate = prepared.candidate_config
    return (
        descriptor.parent_identity == before.parent_identity
        and descriptor.parent_identity == candidate.parent_identity
        and descriptor.before_identity == before.leaf_identity
        and descriptor.before_byte_digest == before.byte_digest
        and descriptor.before_snapshot_digest == before.snapshot_digest
        and descriptor.before_snapshot_digest == candidate.snapshot_digest
        and descriptor.metadata_fingerprint == before.metadata_fingerprint
        and descriptor.metadata_fingerprint == candidate.metadata_fingerprint
        and descriptor.candidate_byte_digest == candidate.byte_digest
        and descriptor.target_metadata_digest == candidate.target_metadata_digest
    )


def _state_evidence_advances(
    previous: JournalRecord,
    current: JournalRecord,
) -> bool:
    before = previous.identities
    after = current.identities
    if current.state is JournalState.SOURCE_PUBLISHED:
        expected = next(
            (item for item in after if item.role == "source-generation"), None
        )
        if (
            expected is None
            or _replace_path_result(before, expected, role="source-generation") != after
        ):
            return False
    elif current.state is JournalState.CACHE_PUBLISHED:
        expected = next(
            (item for item in after if item.role == "cache-generation"), None
        )
        if (
            expected is None
            or _replace_path_result(before, expected, role="cache-generation") != after
        ):
            return False
    elif current.state is JournalState.RECEIPT_COMMITTED:
        additions = tuple(item for item in after if item not in before)
        try:
            prepared = _prepared_from_record(previous.record)
        except (KeyError, TypeError, ValueError):
            return False
        if (
            len(additions) != 1
            or _append_receipt_result(
                before,
                additions[0],
                prepared,
            )
            != after
        ):
            return False
    elif after != before:
        return False

    before_config = previous.record["committed_config"]
    after_config = current.record["committed_config"]
    if current.state is JournalState.CONFIG_COMMITTED:
        if before_config is not None or after_config is None:
            return False
        try:
            planned = _parse_config(current.record["candidate_config"])
            committed = _parse_config(after_config)
        except (KeyError, TypeError, ValueError):
            return False
        return _config_result_matches(
            planned,
            current.config_recovery,
            committed,
        )
    return after_config == before_config


def _complete_committed_evidence(record: JournalRecord) -> bool:
    roles = {
        role: tuple(item for item in record.identities if item.role == role)
        for role in ("source-generation", "cache-generation", "committed-receipt")
    }
    return (
        all(
            len(items) == 1
            and items[0].leaf_identity is not None
            and items[0].content_digest is not None
            for items in roles.values()
        )
        and record.committed_config is not None
        and record.committed_config.leaf_identity is not None
        and record.verification_evidence_digest is not None
        and record.config_recovery is not None
    )


def _windows_record_unchanged(
    before: _paths._WindowsHandleStatus,
    after: _paths._WindowsHandleStatus,
) -> bool:
    """Compare the complete stable Windows handle projection."""

    return before.identity == after.identity and before.fingerprint == after.fingerprint


@dataclass(frozen=True, slots=True)
class _JournalRecordObservation:
    name: str
    identity: FileIdentity
    fingerprint: tuple[int, ...]
    raw_digest: str
    raw_size: int


def _prepared_from_record(record: Mapping[str, object]) -> PreparedTransaction:
    receipt = record["prepared_receipt"]
    if not isinstance(receipt, Mapping) or not receipt:
        raise ValueError("prepared receipt")
    return PreparedTransaction(
        transaction_id=cast(str, record["transaction_id"]),
        effective_marketplace_id=cast(str, record["effective_marketplace_id"]),
        config_transaction_digest=cast(str, record["config_transaction_digest"]),
        plan_digest=cast(str, record["plan_digest"]),
        runner_provenance=_parse_runner(record["runner_provenance"]),
        install_identity=_parse_install(record["install_identity"]),
        before_relation_digest=cast(str | None, record["before_relation_digest"]),
        candidate_relation_digest=cast(str, record["candidate_relation_digest"]),
        before_config=_parse_config(record["before_config"]),
        candidate_config=_parse_config(record["candidate_config"]),
        identities=cast(
            tuple[JournalPathIdentity, ...],
            _parse_sequence(record["identities"], _parse_path_identity),
        ),
        transaction_owned_paths=cast(
            tuple[TransactionOwnedPath, ...],
            _parse_sequence(record["transaction_owned_paths"], _parse_owned),
        ),
        rollback_actions=cast(
            tuple[RollbackAction, ...],
            _parse_sequence(record["rollback_actions"], _parse_rollback),
        ),
        prepared_receipt=cast(Mapping[str, object], receipt),
        verification_evidence_digest=cast(
            str | None, record["verification_evidence_digest"]
        ),
    )


def _decode_config_recovery(
    value: object,
    *,
    prepared: PreparedTransaction,
    transaction_digest: str,
) -> Mapping[str, object] | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError("config recovery record")
    from .atomic_file import decode_config_recovery_descriptor

    result = decode_config_recovery_descriptor(value)
    if not result.is_ok:
        raise ValueError("config recovery record")
    descriptor = result.unwrap()
    descriptor._require_valid()
    if (
        descriptor.transaction_digest != transaction_digest
        or _json_value(descriptor.to_record()) != value
        or not _config_recovery_matches(prepared, descriptor)
    ):
        raise ValueError("config recovery binding")
    return cast(Mapping[str, object], _freeze(value))


def _decode_record(
    raw: bytes,
    *,
    expected_binding: Mapping[str, object],
) -> JournalRecord:
    record = _decode_json(raw)
    try:
        schema_version = record.get("schema_version")
        if type(schema_version) is not str or not re.fullmatch(
            r"[0-9]+(?:\.[0-9]+)*", schema_version
        ):
            raise ValueError("schema version")
        if int(schema_version.split(".", 1)[0]) != 1:
            raise _unsupported()
        minimum_reader = record.get("minimum_reader_version")
        if _version(minimum_reader) > _version(JOURNAL_WRITER_VERSION):
            raise _unsupported()
        if set(record) != _RECORD_KEYS:
            raise ValueError("record keys")
        if schema_version != JOURNAL_SCHEMA_VERSION:
            raise _unsupported()
        if record["schema_digest"] != JOURNAL_SCHEMA_DIGEST:
            raise ValueError("schema digest")
        if (
            record["state_machine_version"] != JOURNAL_STATE_MACHINE_VERSION
            or record["policy_version"] != JOURNAL_POLICY_VERSION
            or record["limit_policy_version"] != LIMIT_POLICY.version
        ):
            raise _unsupported()
        _version(record["writer_version"])
        if not _valid_digest(record["record_digest"]):
            raise ValueError("record digest")
        expected_binding_value = _json_value(expected_binding)
        if record["transaction_binding"] != expected_binding_value:
            raise ValueError("transaction binding")
        sequence = record["sequence"]
        if type(sequence) is not int or sequence < 0:
            raise ValueError("sequence")
        previous = record["previous_record_digest"]
        if not _valid_digest(previous):
            raise ValueError("previous digest")
        state = JournalState(cast(str, record["state"]))
        prepared = _prepared_from_record(record)
        config_recovery = _decode_config_recovery(
            record["config_recovery"],
            prepared=prepared,
            transaction_digest=prepared.config_transaction_digest,
        )
        committed_config = (
            None
            if record["committed_config"] is None
            else _parse_config(record["committed_config"])
        )
        if state is JournalState.PREPARED and config_recovery is not None:
            raise ValueError("early config recovery")
        if _json_value(_prepared_projection(prepared)) != {
            key: record[key] for key in _prepared_projection(prepared)
        }:
            raise ValueError("prepared projection")
        digest_input = dict(record)
        record_digest = cast(str, digest_input.pop("record_digest"))
        if (
            hashlib.sha256(_render(digest_input, final_newline=False)).hexdigest()
            != record_digest
        ):
            raise ValueError("record digest")
        if raw != _render(record, final_newline=True):
            raise ValueError("canonical bytes")
        return JournalRecord(
            state=state,
            sequence=sequence,
            previous_record_digest=cast(str, previous),
            record_digest=record_digest,
            transaction_id=prepared.transaction_id,
            plan_digest=prepared.plan_digest,
            prepared=prepared,
            identities=prepared.identities,
            verification_evidence_digest=prepared.verification_evidence_digest,
            config_recovery=config_recovery,
            committed_config=committed_config,
            record=cast(Mapping[str, object], _freeze(record)),
            raw_size=len(raw),
            _token=_RECORD_TOKEN,
        )
    except ForgeError as exc:
        if exc.code.startswith("journal."):
            raise
        raise _corrupt() from None
    except (KeyError, TypeError, ValueError):
        raise _corrupt() from None


def _records_form_chain(records: tuple[JournalRecord, ...]) -> bool:
    if (
        not records
        or records[0].sequence != 0
        or records[0].state is not JournalState.PREPARED
    ):
        return False
    if (
        records[0].previous_record_digest != _ZERO_DIGEST
        or records[0].verification_evidence_digest is not None
        or records[0].config_recovery is not None
        or records[0].committed_config is not None
    ):
        return False
    static_keys = _RECORD_KEYS - {
        "committed_config",
        "identities",
        "config_recovery",
        "previous_record_digest",
        "record_digest",
        "sequence",
        "state",
        "verification_evidence_digest",
    }
    for previous, current in zip(records, records[1:]):
        if (
            current.sequence != previous.sequence + 1
            or current.previous_record_digest != previous.record_digest
            or current.transaction_id != previous.transaction_id
            or current.plan_digest != previous.plan_digest
            or not _transition_allowed(previous.state, current.state)
            or not _state_evidence_advances(previous, current)
            or any(current.record[key] != previous.record[key] for key in static_keys)
            or (
                previous.verification_evidence_digest is not None
                and current.verification_evidence_digest
                != previous.verification_evidence_digest
            )
            or (
                previous.verification_evidence_digest is None
                and current.verification_evidence_digest is not None
                and current.state is not JournalState.VERIFIED
            )
            or (
                previous.config_recovery is None
                and current.config_recovery is not None
                and current.state is not JournalState.STAGED
            )
            or (
                current.state is JournalState.STAGED and current.config_recovery is None
            )
            or (
                previous.config_recovery is not None
                and current.config_recovery != previous.config_recovery
            )
        ):
            return False
        if (
            current.state is JournalState.VERIFIED
            and current.verification_evidence_digest is None
        ):
            return False
        if current.state in {
            JournalState.RECEIPT_COMMITTED,
            JournalState.FINALIZED,
        } and not _complete_committed_evidence(current):
            return False
    return True


class JournalStore:
    """A sealed journal writer bound to one persistent transaction capability."""

    __slots__ = (
        "_access",
        "_binding",
        "_binding_digest",
        "_closed",
        "_descriptor",
        "_filesystem_guard",
        "_path",
        "_seal",
        "_writer",
    )

    def __init__(
        self,
        access: object,
        path: PathProof | _paths._WindowsPathProof | None = None,
    ) -> None:
        from . import ownership as _ownership

        if type(access) is not _ownership.TransactionJournalAccess:
            raise TypeError("JournalStore requires transaction journal access")
        journal_access = access
        binding = journal_access.binding
        read_only = journal_access.read_only
        if read_only:
            if path is not None:
                raise TypeError("A quarantined JournalStore cannot accept a PathProof")
        elif type(path) not in {PathProof, _paths._WindowsPathProof}:
            raise TypeError("A live JournalStore requires a transaction PathProof")
        if not _ownership._persistent_binding_invariants(binding) or (
            path is not None
            and (
                path.relative.value != binding.root_relative
                or path.owned_ancestor_identity != binding.plugins_identity
                or not path.leaf_exists
                or path.leaf_identity != binding.transaction_identity
                or len(path.absolute_ancestry) < 2
                or path.absolute_ancestry[-2] != binding.store_identity
            )
        ):
            raise TypeError("JournalStore transaction binding does not match")
        schema = (
            resources.files("zagrosi_forge.install")
            .joinpath(_SCHEMA_RESOURCE)
            .read_bytes()
        )
        if hashlib.sha256(schema).hexdigest() != JOURNAL_SCHEMA_DIGEST:
            raise _unsupported()
        writer: _paths.OwnedDirectoryWriter | None = None
        descriptor = 0 if os.name == "nt" else -1
        try:
            journal_access._require_journal_access(write=not read_only)
            if path is not None:
                writer = path._open_owned_directory_writer().unwrap()
                writer._require_open()
            descriptor = journal_access._duplicate_journal_descriptor(
                write=not read_only
            )
            binding_projection = cast(
                Mapping[str, object],
                _freeze(_json_value(binding.canonical_projection())),
            )
            binding_digest = hashlib.sha256(
                _render(binding_projection, final_newline=False)
            ).hexdigest()
        except BaseException:
            if writer is not None:
                writer.close()
            if os.name == "nt":
                if descriptor:
                    _paths._windows_close(descriptor)
            elif descriptor >= 0:
                os.close(descriptor)
            raise
        self._access = journal_access
        self._binding = binding_projection
        self._binding_digest = binding_digest
        self._descriptor = descriptor
        self._filesystem_guard = journal_access._filesystem_guard
        self._path = path
        self._writer = writer
        self._closed = False
        self._seal = _STORE_TOKEN

    def _require_open(self, *, write: bool = False) -> None:
        if self._closed or self._seal is not _STORE_TOKEN:
            raise _corrupt("The transaction journal capability is closed.")
        try:
            self._access._require_journal_access(write=write)
            if self._writer is not None:
                self._writer._require_open()
            if self._path is not None:
                self._path._require_open()
            self._access._require_journal_access(write=write)
        except ForgeError as exc:
            if write and exc.code == "ownership.unowned":
                raise
            raise _corrupt("The transaction journal root identity changed.") from None
        except OSError:
            raise _corrupt("The transaction journal root identity changed.") from None

    def _journal_names(self) -> tuple[str, ...]:
        self._require_open()
        try:
            if os.name == "nt":
                from .ownership import _windows_list_names

                names = _windows_list_names(
                    self._descriptor,
                    limit=LIMIT_POLICY.value("bundle_files"),
                )
            else:
                collected: list[str] = []
                with os.scandir(self._descriptor) as entries:
                    for entry in entries:
                        collected.append(entry.name)
                        if len(collected) > LIMIT_POLICY.value("bundle_files"):
                            raise OSError(errno.E2BIG, "transaction directory quota")
                names = tuple(sorted(collected))
        except OSError as exc:
            if exc.errno == errno.E2BIG:
                raise _limit() from None
            raise _corrupt(
                "The transaction directory cannot be enumerated safely."
            ) from None
        except ValueError:
            raise _corrupt(
                "The transaction directory cannot be enumerated safely."
            ) from None
        selected: list[tuple[int, str]] = []
        for name in names:
            match = _RECORD_NAME.fullmatch(name)
            if match is not None:
                selected.append((int(match.group(1)), name))
            elif name.startswith("journal"):
                raise _corrupt(
                    "The transaction journal contains an unknown record name."
                )
        selected.sort()
        if any(index != expected for expected, (index, _name) in enumerate(selected)):
            raise _corrupt("The transaction journal sequence contains a gap.")
        if len(selected) > LIMIT_POLICY.value("journal_records"):
            raise _limit()
        if len(selected) > len(JournalState):
            raise _corrupt("The transaction journal contains too many transitions.")
        self._require_open()
        return tuple(name for _index, name in selected)

    def _read_posix(
        self,
        name: str,
    ) -> tuple[bytes, _JournalRecordObservation]:
        descriptor = -1
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=self._descriptor,
            )
            before = os.fstat(descriptor)
            if before.st_size > LIMIT_POLICY.value("journal_record_bytes"):
                raise _limit()
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_gid != os.getegid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or before.st_dev != os.fstat(self._descriptor).st_dev
                or not self._filesystem_guard(descriptor)
                or not _paths._posix_security_metadata_supported(descriptor, before)
            ):
                raise OSError(errno.EPERM, "unsafe journal record")
            limit = LIMIT_POLICY.value("journal_record_bytes")
            chunks: list[bytes] = []
            offset = 0
            while offset <= limit:
                chunk = os.pread(descriptor, min(64 * 1024, limit + 1 - offset), offset)
                if not chunk:
                    break
                chunks.append(chunk)
                offset += len(chunk)
            raw = b"".join(chunks)
            after = os.fstat(descriptor)
            stable = (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_nlink,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) == (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if not stable or len(raw) != before.st_size or len(raw) > limit:
                raise OSError(errno.ESTALE, "journal record changed")
            fingerprint = (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            return raw, _JournalRecordObservation(
                name=name,
                identity=(after.st_dev, after.st_ino),
                fingerprint=fingerprint,
                raw_digest=hashlib.sha256(raw).hexdigest(),
                raw_size=len(raw),
            )
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _read_windows(
        self,
        name: str,
    ) -> tuple[bytes, _JournalRecordObservation]:
        handle = 0
        try:
            handle = _paths._windows_open_child(
                self._descriptor,
                name,
                directory=False,
                read_data=True,
            )
            before = _paths._windows_handle_status(handle)
            if before.size > LIMIT_POLICY.value("journal_record_bytes"):
                raise _limit()
            if (
                before.is_directory
                or before.is_reparse
                or before.link_count != 1
                or before.identity[0]
                != _paths._windows_handle_status(self._descriptor).identity[0]
                or not self._filesystem_guard(handle)
                or not _paths._windows_private_authorization(handle, exact=True)
            ):
                raise OSError(errno.EPERM, "unsafe journal record")
            raw = _paths._windows_read(
                handle,
                limit=LIMIT_POLICY.value("journal_record_bytes"),
            )
            after = _paths._windows_handle_status(handle)
            if (
                not _windows_record_unchanged(before, after)
                or len(raw) != before.size
                or len(raw) > LIMIT_POLICY.value("journal_record_bytes")
                or not self._filesystem_guard(handle)
                or not _paths._windows_private_authorization(handle, exact=True)
            ):
                raise OSError(errno.ESTALE, "journal record changed")
            return raw, _JournalRecordObservation(
                name=name,
                identity=after.identity,
                fingerprint=after.fingerprint,
                raw_digest=hashlib.sha256(raw).hexdigest(),
                raw_size=len(raw),
            )
        finally:
            if handle:
                _paths._windows_close(handle)

    def _read_record(
        self,
        name: str,
    ) -> tuple[bytes, _JournalRecordObservation]:
        try:
            return (
                self._read_windows(name) if os.name == "nt" else self._read_posix(name)
            )
        except ForgeError as exc:
            if exc.code.startswith("journal."):
                raise
            raise _corrupt(
                "A transaction journal record is not a safe regular file."
            ) from None
        except (OSError, ValueError):
            raise _corrupt(
                "A transaction journal record is not a safe regular file."
            ) from None

    def _read_record_set(
        self,
    ) -> tuple[
        tuple[bytes, ...],
        tuple[_JournalRecordObservation, ...],
    ]:
        names = self._journal_names()
        captured = tuple(self._read_record(name) for name in names)
        if self._journal_names() != names:
            raise _corrupt("The transaction journal changed while loading.")
        confirmed = tuple(self._read_record(name) for name in names)
        captured_observations = tuple(observation for _raw, observation in captured)
        confirmed_observations = tuple(observation for _raw, observation in confirmed)
        if confirmed_observations != captured_observations:
            raise _corrupt("A transaction journal record changed while loading.")
        self._require_open()
        return (
            tuple(raw for raw, _observation in confirmed),
            confirmed_observations,
        )

    def _journal_record_observations(
        self,
    ) -> tuple[_JournalRecordObservation, ...]:
        _raw, observations = self._read_record_set()
        return observations

    def _read_records(
        self,
    ) -> tuple[
        tuple[JournalRecord, ...],
        tuple[_JournalRecordObservation, ...],
    ]:
        raw_records, observations = self._read_record_set()
        records: list[JournalRecord] = []
        total = 0
        for raw in raw_records:
            total += len(raw)
            if total > LIMIT_POLICY.value("journal_total_bytes"):
                raise _limit()
            records.append(_decode_record(raw, expected_binding=self._binding))
        rendered = tuple(records)
        if rendered and not _records_form_chain(rendered):
            raise _corrupt("The transaction journal chain is invalid.")
        self._require_open()
        return rendered, observations

    def _head(self, record: JournalRecord) -> JournalHead:
        return JournalHead(
            transaction_binding_digest=self._binding_digest,
            sequence=record.sequence,
            state=record.state,
            record_digest=record.record_digest,
            _token=_HEAD_TOKEN,
        )

    def _load_with_observations(
        self,
    ) -> tuple[LoadedJournal, tuple[_JournalRecordObservation, ...]]:
        records, observations = self._read_records()
        if not records:
            raise _corrupt("The transaction journal is missing its prepared record.")
        loaded = LoadedJournal(
            head=self._head(records[-1]),
            records=records,
            byte_size=sum(record.raw_size for record in records),
            _token=_LOADED_TOKEN,
        )
        self._require_open()
        return loaded, observations

    def load(self) -> LoadedJournal:
        """Load and validate every immutable record without changing the directory."""

        loaded, _observations = self._load_with_observations()
        return loaded

    def _validate_prepared_binding(self, prepared: PreparedTransaction) -> None:
        transaction_id = cast(str, self._binding["transaction_id"])
        root_relative = cast(str, self._binding["root_relative"])
        store_identity = cast(FileIdentity, self._binding["store_identity"])
        transaction_identity = cast(FileIdentity, self._binding["transaction_identity"])
        roots = tuple(
            identity
            for identity in prepared.identities
            if identity.role == "transaction-root"
            and identity.relative_path == root_relative
        )
        owned = tuple(
            path
            for path in prepared.transaction_owned_paths
            if path.role == "transaction-root" and path.relative_path == root_relative
        )
        if (
            prepared.transaction_id != transaction_id
            or len(roots) != 1
            or roots[0].parent_identity != store_identity
            or roots[0].leaf_identity != transaction_identity
            or len(owned) != 1
            or owned[0].expected_identity != transaction_identity
        ):
            raise _corrupt("The prepared journal does not match transaction authority.")

    def _publish(self, raw: bytes, *, sequence: int, current_size: int) -> None:
        if len(raw) > LIMIT_POLICY.value("journal_record_bytes") or current_size + len(
            raw
        ) > LIMIT_POLICY.value("journal_total_bytes"):
            raise _limit()
        self._require_open(write=True)
        writer = self._writer
        if writer is None:
            raise _corrupt("The transaction journal has no write authority.")
        reference = validate_reference(
            f"journal-{sequence:08d}.json",
            role="journal-record",
            limits=LIMIT_POLICY,
        ).unwrap()
        try:
            self._require_open(write=True)
            if os.name == "nt":
                writer._write_windows(reference, raw, 0o600)
            else:
                writer._write_posix(reference, raw, 0o600)
            self._require_open(write=True)
        except FileExistsError:
            raise _corrupt(
                "The next transaction journal record already exists."
            ) from None
        except ForgeError as exc:
            if exc.code.startswith("journal.") or exc.code == "ownership.unowned":
                raise
            raise _corrupt(
                "The transaction journal could not be published safely."
            ) from None
        except (OSError, TypeError, ValueError):
            raise _corrupt(
                "The transaction journal could not be published safely."
            ) from None

    def create_prepared(self, prepared: PreparedTransaction) -> JournalHead:
        """Publish and fsync the unique PREPARED record before other effects."""

        self._require_open(write=True)
        if type(prepared) is not PreparedTransaction:
            raise TypeError("create_prepared requires PreparedTransaction")
        if prepared.verification_evidence_digest is not None:
            raise _corrupt("PREPARED cannot contain verification evidence.")
        self._validate_prepared_binding(prepared)
        records, _observations = self._read_records()
        if records:
            raise _corrupt("The transaction journal already exists.")
        record = _record_projection(
            prepared,
            transaction_binding=self._binding,
            state=JournalState.PREPARED,
            sequence=0,
            previous_record_digest=_ZERO_DIGEST,
        )
        raw = _seal_record(record)
        self._publish(raw, sequence=0, current_size=0)
        return self.load().head

    def append(self, head: JournalHead, transition: JournalTransition) -> JournalHead:
        """Append exactly one valid transition after revalidating the durable head."""

        self._require_open(write=True)
        if type(head) is not JournalHead or type(transition) is not JournalTransition:
            raise TypeError("append requires a loaded head and transition")
        loaded = self.load()
        current = loaded.records[-1]
        if (
            head._seal is not _HEAD_TOKEN
            or head.transaction_binding_digest != self._binding_digest
            or head != loaded.head
            or not _transition_allowed(current.state, transition.state)
        ):
            raise _corrupt("The requested transaction journal transition is invalid.")
        prepared = _prepared_from_record(current.record)
        identities = current.identities
        if transition.identities is not None:
            requested_identities = tuple(
                sorted(
                    transition.identities,
                    key=lambda item: (item.role, item.relative_path),
                )
            )
            if requested_identities != current.identities:
                raise _corrupt(
                    "The transaction journal identity transition is invalid."
                )
        for result, required_state in (
            (transition.source_result, JournalState.SOURCE_PUBLISHED),
            (transition.cache_result, JournalState.CACHE_PUBLISHED),
            (transition.config_result, JournalState.CONFIG_COMMITTED),
            (transition.receipt_result, JournalState.RECEIPT_COMMITTED),
        ):
            if (result is not None) != (transition.state is required_state):
                raise _corrupt("The transaction journal result transition is invalid.")
        if transition.source_result is not None:
            advanced = _replace_path_result(
                identities,
                transition.source_result,
                role="source-generation",
            )
            if advanced is None:
                raise _corrupt("The source publication evidence is invalid.")
            identities = advanced
        if transition.cache_result is not None:
            advanced = _replace_path_result(
                identities,
                transition.cache_result,
                role="cache-generation",
            )
            if advanced is None:
                raise _corrupt("The cache publication evidence is invalid.")
            identities = advanced
        committed_config = current.record["committed_config"]
        if transition.config_result is not None:
            if not _config_result_matches(
                prepared.candidate_config,
                current.config_recovery,
                transition.config_result,
            ):
                raise _corrupt("The config publication evidence is invalid.")
            committed_config = _config_projection(transition.config_result)
        if transition.receipt_result is not None:
            advanced = _append_receipt_result(
                identities,
                transition.receipt_result,
                prepared,
            )
            if advanced is None:
                raise _corrupt("The receipt publication evidence is invalid.")
            identities = advanced
        evidence = (
            current.verification_evidence_digest
            if transition.verification_evidence_digest is None
            else transition.verification_evidence_digest
        )
        if (
            transition.verification_evidence_digest is not None
            and (
                transition.state is not JournalState.VERIFIED
                or current.verification_evidence_digest is not None
            )
        ) or (
            transition.state is JournalState.VERIFIED
            and transition.verification_evidence_digest is None
        ):
            raise _corrupt("The verification evidence transition is invalid.")
        config_recovery = current.config_recovery
        if transition.config_recovery is not None:
            if (
                transition.state is not JournalState.STAGED
                or current.config_recovery is not None
                or transition.config_recovery.transaction_digest
                != cast(str, current.record["config_transaction_digest"])
            ):
                raise _corrupt("The config recovery transition is invalid.")
            try:
                transition.config_recovery._require_valid()
                if not _config_recovery_matches(
                    prepared,
                    transition.config_recovery,
                ):
                    raise ValueError("config recovery binding")
                recovery_record = transition.config_recovery.to_record()
                recovery_value = _json_value(recovery_record)
                config_recovery = _decode_config_recovery(
                    recovery_value,
                    prepared=prepared,
                    transaction_digest=cast(
                        str, current.record["config_transaction_digest"]
                    ),
                )
            except (ForgeError, TypeError, ValueError):
                raise _corrupt("The config recovery transition is invalid.") from None
        elif transition.state is JournalState.STAGED:
            raise _corrupt("STAGED requires config recovery evidence.")
        previous_record = current.record
        record = {
            key: previous_record[key]
            for key in previous_record
            if key != "record_digest"
        }
        record.update(
            {
                "identities": tuple(_path_projection(item) for item in identities),
                "committed_config": committed_config,
                "config_recovery": config_recovery,
                "previous_record_digest": current.record_digest,
                "sequence": current.sequence + 1,
                "state": transition.state.value,
                "verification_evidence_digest": evidence,
            }
        )
        raw = _seal_record(record)
        self._publish(raw, sequence=current.sequence + 1, current_size=loaded.byte_size)
        return self.load().head

    def close(self) -> None:
        if self._closed:
            return
        if self._writer is not None:
            self._writer.close()
        if os.name == "nt":
            _paths._windows_close(self._descriptor)
        else:
            os.close(self._descriptor)
        self._access.close()
        self._closed = True

    def __enter__(self) -> JournalStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __reduce__(self) -> Never:
        raise TypeError("journal stores are not serializable")


def _load_observed_pending(
    root: _paths.OwnedRoot,
    observations: tuple[PendingTransactionObservation, ...],
) -> tuple[LoadedJournal, ...]:
    from . import ownership as _ownership

    loaded: list[LoadedJournal] = []
    total = 0
    for observation in observations:
        access_result = _ownership.open_pending_transaction_journal_access(
            root,
            observation,
        )
        if not access_result.is_ok:
            raise _corrupt("A pending transaction journal identity changed.")
        access = access_result.unwrap()
        store: JournalStore | None = None
        try:
            store = JournalStore(access)
            before = store._journal_record_observations()
            journal, during = store._load_with_observations()
            after = store._journal_record_observations()
            if before != during or during != after:
                raise _corrupt("A pending transaction journal changed after loading.")
            total += journal.byte_size
            if total > LIMIT_POLICY.value("journal_total_bytes"):
                raise _limit()
            loaded.append(journal)
        finally:
            if store is not None:
                store.close()
            else:
                access.close()
    return tuple(loaded)


def load_pending(root: object) -> tuple[LoadedJournal, ...]:
    """Discover and twice-observe every pending journal without effects."""

    from . import ownership as _ownership

    if not isinstance(root, _paths.OwnedRoot):
        raise TypeError("load_pending requires an OwnedRoot capability")
    discovered = _ownership.discover_pending_transactions(root)
    if not discovered.is_ok:
        raise _corrupt("The pending transaction inventory is not trusted.")
    observations = discovered.unwrap()
    if len(observations) > LIMIT_POLICY.value("journal_records"):
        raise _limit()
    loaded = _load_observed_pending(root, observations)
    confirmed = _ownership.discover_pending_transactions(root)
    if not confirmed.is_ok or confirmed.unwrap() != observations:
        raise _corrupt("The pending transaction inventory changed while loading.")
    reloaded = _load_observed_pending(root, observations)
    final = _ownership.discover_pending_transactions(root)
    if reloaded != loaded or not final.is_ok or final.unwrap() != observations:
        raise _corrupt("The pending transaction evidence changed while loading.")
    return loaded
