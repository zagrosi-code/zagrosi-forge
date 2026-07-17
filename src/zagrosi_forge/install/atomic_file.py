"""Capability-bound atomic publication for a verified Codex config candidate."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from enum import Enum
import errno
import hashlib
import os
import secrets
import stat
import sys
from threading import Lock, RLock
from types import TracebackType
from typing import Never

from . import ownership as _ownership
from . import paths as _paths
from .config import (
    ConfigCandidate,
    ConfigSnapshot,
    _candidate_bytes,
    _descriptor_xattrs,
    _snapshot_bytes,
    _validate_candidate_bytes,
    _windows_metadata_projection,
)
from .contracts import ForgeError, Result, canonical_json_bytes
from .paths import ConfigPathProof, FileIdentity
from .policies import RECOVERY_RETENTION_POLICY


_TRANSACTION_TOKEN = object()
_PREPARED_TOKEN = object()
_PREPARATION_RECOVERY_TOKEN = object()
_PREPARATION_DESCRIPTOR_TOKEN = object()
_BACKUP_TOKEN = object()
_RECOVERY_DESCRIPTOR_TOKEN = object()
_COMMIT_TOKEN = object()
_CONFIG_NAME = "config.toml"
_PRIVATE_PREFIX = ".zagrosi-config-tx-"
_BACKUP_PREFIX = ".zagrosi-config-backup-"
_MAX_TRANSACTION_BYTES = 256


def _error(code: str, message: str) -> ForgeError:
    return ForgeError(
        code,
        13,
        message,
        recovery_instructions=("Inspect the retained config recovery state.",),
    )


def _valid_file_identity(value: object) -> bool:
    return (
        type(value) is tuple
        and len(value) == 2
        and all(type(part) is int and part >= 0 for part in value)
    )


class ConfigCommitState(str, Enum):
    BEFORE = "before"
    CANDIDATE = "candidate"
    THIRD_PARTY = "third_party"
    ABSENT = "absent"


class ConfigTransaction:
    """Single-use, digest-only transaction authority for config publication."""

    __slots__ = (
        "_consumed",
        "_lock",
        "_seal",
        "_backup_reference",
        "_backup_stage_reference",
        "_candidate_reference",
        "_snapshot_reference",
        "_transaction_digest",
    )

    def __init__(self, transaction_id: str, *, _token: object) -> None:
        if (
            _token is not _TRANSACTION_TOKEN
            or type(transaction_id) is not str
            or not transaction_id
            or "\0" in transaction_id
            or len(transaction_id.encode("utf-8")) > _MAX_TRANSACTION_BYTES
        ):
            raise TypeError("ConfigTransaction requires a bounded transaction id")
        digest = hashlib.sha256(transaction_id.encode("utf-8")).hexdigest()
        tag = digest[:24]
        self._transaction_digest = digest
        self._snapshot_reference = f"{_PRIVATE_PREFIX}{tag}.snapshot"
        self._candidate_reference = f"{_PRIVATE_PREFIX}{tag}.candidate"
        self._backup_stage_reference = f"{_PRIVATE_PREFIX}{tag}.backup"
        self._backup_reference = f"{_BACKUP_PREFIX}{tag}.toml"
        self._consumed = False
        self._lock = Lock()
        self._seal = _TRANSACTION_TOKEN

    def _expected_references(self) -> tuple[str, str, str, str]:
        tag = self._transaction_digest[:24]
        return (
            f"{_PRIVATE_PREFIX}{tag}.snapshot",
            f"{_PRIVATE_PREFIX}{tag}.candidate",
            f"{_PRIVATE_PREFIX}{tag}.backup",
            f"{_BACKUP_PREFIX}{tag}.toml",
        )

    def _valid_unlocked(self) -> bool:
        return (
            self._seal is _TRANSACTION_TOKEN
            and type(self._transaction_digest) is str
            and len(self._transaction_digest) == 64
            and all(
                character in "0123456789abcdef"
                for character in self._transaction_digest
            )
            and (
                self._snapshot_reference,
                self._candidate_reference,
                self._backup_stage_reference,
                self._backup_reference,
            )
            == self._expected_references()
        )

    def _consume(self) -> tuple[str, str, str, str, str] | None:
        with self._lock:
            if self._consumed or not self._valid_unlocked():
                return None
            self._consumed = True
            return (
                self._transaction_digest,
                self._snapshot_reference,
                self._candidate_reference,
                self._backup_stage_reference,
                self._backup_reference,
            )

    def _read(self, value: str) -> str:
        with self._lock:
            if not self._valid_unlocked():
                raise _error("config.external_change", "Config transaction changed.")
            return value

    @property
    def transaction_digest(self) -> str:
        return self._read(self._transaction_digest)

    @property
    def snapshot_reference(self) -> str:
        return self._read(self._snapshot_reference)

    @property
    def candidate_reference(self) -> str:
        return self._read(self._candidate_reference)

    @property
    def backup_stage_reference(self) -> str:
        return self._read(self._backup_stage_reference)

    @property
    def backup_reference(self) -> str:
        return self._read(self._backup_reference)

    def __repr__(self) -> str:
        return f"ConfigTransaction(transaction_digest={self.transaction_digest!r})"

    def __reduce__(self) -> Never:
        raise TypeError("config transactions are not serializable")


def begin_config_transaction(transaction_id: str) -> ConfigTransaction:
    return ConfigTransaction(transaction_id, _token=_TRANSACTION_TOKEN)


@dataclass(frozen=True, slots=True, init=False)
class ConfigPreparationRecoveryDescriptor:
    """Secret-free evidence for private stages retained after preparation failure."""

    transaction_digest: str
    parent_identity: FileIdentity
    stages: tuple[tuple[str, str, FileIdentity | None], ...]
    _binding_digest: str
    _seal: object

    def __init__(
        self,
        *,
        transaction_digest: str,
        parent_identity: FileIdentity,
        stages: tuple[tuple[str, str, FileIdentity | None], ...],
        _token: object,
    ) -> None:
        if (
            _token is not _PREPARATION_DESCRIPTOR_TOKEN
            or type(transaction_digest) is not str
            or len(transaction_digest) != 64
            or any(
                character not in "0123456789abcdef" for character in transaction_digest
            )
            or not _valid_file_identity(parent_identity)
            or type(stages) is not tuple
        ):
            raise TypeError("invalid config preparation recovery descriptor")
        for role, reference, identity in stages:
            if (
                role not in {"backup", "candidate", "snapshot"}
                or type(reference) is not str
                or not reference.startswith(_PRIVATE_PREFIX)
                or reference in {".", ".."}
                or "/" in reference
                or "\\" in reference
                or "\0" in reference
                or (identity is not None and not _valid_file_identity(identity))
            ):
                raise TypeError("invalid retained config preparation stage")
        domain = {
            "parent_identity": parent_identity,
            "stages": stages,
            "transaction_digest": transaction_digest,
        }
        binding = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        for name, value in {
            **domain,
            "_binding_digest": binding,
            "_seal": _PREPARATION_DESCRIPTOR_TOKEN,
        }.items():
            object.__setattr__(self, name, value)

    def _domain(self) -> dict[str, object]:
        return {
            "parent_identity": self.parent_identity,
            "stages": self.stages,
            "transaction_digest": self.transaction_digest,
        }

    def _require_valid(self) -> None:
        try:
            expected = hashlib.sha256(canonical_json_bytes(self._domain())).hexdigest()
        except (AttributeError, TypeError, ValueError):
            raise _error(
                "config.external_change",
                "Config preparation recovery descriptor changed.",
            ) from None
        if (
            self._seal is not _PREPARATION_DESCRIPTOR_TOKEN
            or self._binding_digest != expected
        ):
            raise _error(
                "config.external_change",
                "Config preparation recovery descriptor changed.",
            )

    def to_record(self) -> dict[str, object]:
        self._require_valid()
        return {
            "descriptor_digest": self._binding_digest,
            "parent_identity": self.parent_identity,
            "stages": tuple(
                {"identity": identity, "reference": reference, "role": role}
                for role, reference, identity in self.stages
            ),
            "transaction_digest": self.transaction_digest,
        }

    def __reduce__(self) -> Never:
        raise TypeError("config preparation recovery descriptors are not serializable")


@dataclass(slots=True)
class _PreparationStageCapability:
    role: str
    reference: str
    descriptor: int
    identity: FileIdentity | None
    removed: bool = False
    closed: bool = False


class ConfigPreparationRecovery:
    """Live handle authority for retrying incomplete preparation cleanup."""

    __slots__ = (
        "_binding_digest",
        "_closed",
        "_descriptor",
        "_lock",
        "_parent_descriptor",
        "_parent_identity",
        "_seal",
        "_stages",
        "_transaction_digest",
    )

    def __init__(
        self,
        *,
        transaction_digest: str,
        parent_descriptor: int,
        parent_identity: FileIdentity,
        stages: tuple[_PreparationStageCapability, ...],
        _token: object,
    ) -> None:
        if _token is not _PREPARATION_RECOVERY_TOKEN or not _descriptor_is_open(
            parent_descriptor
        ):
            raise TypeError("invalid config preparation recovery capability")
        self._transaction_digest = transaction_digest
        self._parent_descriptor = parent_descriptor
        self._parent_identity = parent_identity
        self._stages = stages
        self._closed = False
        self._seal = _PREPARATION_RECOVERY_TOKEN
        self._lock = RLock()
        self._descriptor = self._make_descriptor()
        self._binding_digest = hashlib.sha256(
            canonical_json_bytes(self._domain())
        ).hexdigest()
        self._require_bound()

    def _create_owned_stage(
        self,
        *,
        role: str,
        reference: str,
    ) -> tuple[int, _PreparationStageCapability]:
        """Create and bind a stage as one recovery-owned operation."""

        with self._lock:
            self._require_bound()
            if self._closed:
                raise _error(
                    "config.external_change",
                    "Config preparation stage authority is invalid.",
                )
            descriptor = _create_stage(self._parent_descriptor, reference)
            stage = _PreparationStageCapability(
                role=role,
                reference=reference,
                descriptor=descriptor,
                identity=None,
            )
            self._stages = (*self._stages, stage)
            self._rebind(refresh_descriptor=True)
            return descriptor, stage

    def _record_stage_identity(
        self,
        stage: _PreparationStageCapability,
        identity: FileIdentity,
    ) -> None:
        with self._lock:
            self._require_bound()
            if (
                self._closed
                or stage not in self._stages
                or stage.closed
                or not _valid_file_identity(identity)
            ):
                raise _error(
                    "config.external_change",
                    "Config preparation stage identity is invalid.",
                )
            stage.identity = identity
            self._rebind(refresh_descriptor=True)

    def _make_descriptor(self) -> ConfigPreparationRecoveryDescriptor:
        return ConfigPreparationRecoveryDescriptor(
            transaction_digest=self._transaction_digest,
            parent_identity=self._parent_identity,
            stages=tuple(
                (stage.role, stage.reference, stage.identity) for stage in self._stages
            ),
            _token=_PREPARATION_DESCRIPTOR_TOKEN,
        )

    def _domain(self) -> dict[str, object]:
        return {
            "closed": self._closed,
            "descriptor_binding": self._descriptor._binding_digest,
            "parent_descriptor": self._parent_descriptor,
            "stages": tuple(
                (
                    stage.role,
                    stage.reference,
                    stage.descriptor,
                    stage.identity,
                    stage.removed,
                    stage.closed,
                )
                for stage in self._stages
            ),
            "transaction_digest": self._transaction_digest,
        }

    def _rebind(self, *, refresh_descriptor: bool = False) -> None:
        if refresh_descriptor:
            self._descriptor = self._make_descriptor()
        self._binding_digest = hashlib.sha256(
            canonical_json_bytes(self._domain())
        ).hexdigest()

    def _require_bound(self) -> None:
        try:
            self._descriptor._require_valid()
            expected = hashlib.sha256(canonical_json_bytes(self._domain())).hexdigest()
        except (AttributeError, ForgeError, TypeError, ValueError):
            raise _error(
                "config.external_change",
                "Config preparation recovery authority changed.",
            ) from None
        if (
            self._seal is not _PREPARATION_RECOVERY_TOKEN
            or self._binding_digest != expected
        ):
            raise _error(
                "config.external_change",
                "Config preparation recovery authority changed.",
            )

    def _require_valid(self) -> None:
        self._require_bound()
        try:
            if self._closed:
                valid_parent = True
            elif os.name == "nt":
                windows_parent_status = _paths._windows_handle_status(
                    self._parent_descriptor
                )
                valid_parent = (
                    windows_parent_status.identity == self._parent_identity
                    and windows_parent_status.is_directory
                    and not windows_parent_status.is_reparse
                    and _paths._windows_private_directory(
                        self._parent_descriptor,
                        exact=False,
                    )
                )
            else:
                posix_parent_status = os.fstat(self._parent_descriptor)
                valid_parent = (
                    posix_parent_status.st_dev,
                    posix_parent_status.st_ino,
                ) == self._parent_identity and _paths._private_directory(
                    self._parent_descriptor,
                    posix_parent_status,
                    exact=False,
                )
            valid_stages = all(
                stage.closed
                or stage.identity is None
                or _identity_from_descriptor(stage.descriptor) == stage.identity
                for stage in self._stages
            )
        except (AttributeError, ForgeError, OSError, TypeError, ValueError):
            raise _error(
                "config.external_change",
                "Config preparation recovery authority changed.",
            ) from None
        if not valid_parent or not valid_stages:
            raise _error(
                "config.external_change",
                "Config preparation recovery authority changed.",
            )

    @property
    def recovery_descriptor(self) -> ConfigPreparationRecoveryDescriptor:
        with self._lock:
            self._require_bound()
            return self._descriptor

    def _cleanup(self) -> ConfigPreparationRecoveryDescriptor:
        with self._lock:
            return self._cleanup_locked()

    def _cleanup_locked(self) -> ConfigPreparationRecoveryDescriptor:
        self._require_valid()
        if self._closed:
            return self._descriptor
        for stage in self._stages:
            if stage.closed:
                continue
            if stage.removed:
                try:
                    _close_descriptor(stage.descriptor)
                except OSError:
                    continue
                stage.closed = True
                self._rebind()
                continue
            if stage.identity is None:
                try:
                    stage.identity = _identity_from_descriptor(stage.descriptor)
                except OSError:
                    continue
                self._rebind(refresh_descriptor=True)
            if not _unlink_owned(
                self._parent_descriptor,
                stage.reference,
                stage.descriptor,
                stage.identity,
            ):
                continue
            stage.removed = True
            self._rebind()
            try:
                _close_descriptor(stage.descriptor)
            except OSError:
                continue
            stage.closed = True
            self._rebind()
        if not all(stage.removed and stage.closed for stage in self._stages):
            raise _error(
                "config.commit_ambiguous",
                "Config preparation recovery cleanup remains ambiguous.",
            )
        try:
            _sync_parent(self._parent_descriptor)
            _close_descriptor(self._parent_descriptor)
        except OSError:
            raise _error(
                "config.commit_ambiguous",
                "Config preparation recovery durability remains ambiguous.",
            ) from None
        self._closed = True
        self._parent_descriptor = 0 if os.name == "nt" else -1
        self._rebind()
        return self._descriptor

    def __repr__(self) -> str:
        return (
            "ConfigPreparationRecovery(transaction_digest="
            f"{self._transaction_digest!r}, stages={len(self._stages)!r})"
        )

    def __reduce__(self) -> Never:
        raise TypeError("config preparation recovery capabilities are not serializable")


class ConfigPreparationError(ForgeError):
    """Preparation failure retaining a live, secret-free recovery authority."""

    _READ_ONLY_FIELDS = ForgeError._READ_ONLY_FIELDS | frozenset(
        {"_recovery", "_recovery_identity", "_seal"}
    )

    def __init__(self, recovery: ConfigPreparationRecovery) -> None:
        ForgeError.__init__(
            self,
            "config.commit_ambiguous",
            13,
            "Atomic config preparation cleanup is ambiguous.",
            recovery_instructions=(
                "Journal the config preparation recovery descriptor and retry cleanup.",
            ),
        )
        self._recovery = recovery
        self._recovery_identity = id(recovery)
        self._seal = _PREPARATION_RECOVERY_TOKEN

    def _require_valid(self) -> ConfigPreparationRecovery:
        try:
            recovery = self._recovery
            if (
                type(self) is not ConfigPreparationError
                or self._seal is not _PREPARATION_RECOVERY_TOKEN
                or type(recovery) is not ConfigPreparationRecovery
                or self._recovery_identity != id(recovery)
            ):
                raise AttributeError
            recovery.recovery_descriptor._require_valid()
            return recovery
        except (AttributeError, ForgeError, TypeError, ValueError):
            raise _error(
                "config.external_change",
                "Config preparation recovery error changed.",
            ) from None

    @property
    def recovery(self) -> ConfigPreparationRecovery:
        return self._require_valid()

    @property
    def recovery_descriptor(self) -> ConfigPreparationRecoveryDescriptor:
        return self.recovery.recovery_descriptor


def cleanup_config_preparation(
    failure: ConfigPreparationError,
) -> Result[ConfigPreparationRecoveryDescriptor]:
    """Retry cleanup through the retained preparation capability."""

    try:
        if type(failure) is not ConfigPreparationError:
            raise _error(
                "config.external_change",
                "Config preparation recovery input is invalid.",
            )
        return Result.success(failure.recovery._cleanup())
    except ForgeError as exc:
        return Result.failure(exc)
    except (AttributeError, OSError, TypeError, ValueError):
        return Result.failure(
            _error(
                "config.commit_ambiguous",
                "Config preparation recovery cleanup is ambiguous.",
            )
        )


@dataclass(frozen=True, slots=True, init=False)
class BackupRecord:
    transaction_digest: str
    relative_path: str
    backup_identity: tuple[int, int]
    original_identity: tuple[int, int] | None
    original_digest: str
    metadata_fingerprint: str
    retention_policy_version: str
    _binding_digest: str
    _seal: object

    def __init__(
        self,
        *,
        transaction_digest: str,
        relative_path: str,
        backup_identity: tuple[int, int],
        original_identity: tuple[int, int] | None,
        original_digest: str,
        metadata_fingerprint: str,
        _token: object,
    ) -> None:
        if _token is not _BACKUP_TOKEN:
            raise TypeError("BackupRecord is created only by atomic preparation")
        domain = {
            "backup_identity": backup_identity,
            "metadata_fingerprint": metadata_fingerprint,
            "original_digest": original_digest,
            "original_identity": original_identity,
            "relative_path": relative_path,
            "retention_policy_version": RECOVERY_RETENTION_POLICY.version,
            "transaction_digest": transaction_digest,
        }
        binding = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        for name, value in {
            **domain,
            "_binding_digest": binding,
            "_seal": _BACKUP_TOKEN,
        }.items():
            object.__setattr__(self, name, value)

    def _require_valid(self) -> None:
        domain = {
            "backup_identity": self.backup_identity,
            "metadata_fingerprint": self.metadata_fingerprint,
            "original_digest": self.original_digest,
            "original_identity": self.original_identity,
            "relative_path": self.relative_path,
            "retention_policy_version": self.retention_policy_version,
            "transaction_digest": self.transaction_digest,
        }
        expected = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        if self._seal is not _BACKUP_TOKEN or self._binding_digest != expected:
            raise _error("config.external_change", "Config backup record changed.")

    def __reduce__(self) -> Never:
        raise TypeError("config backup records are not serializable")


@dataclass(frozen=True, slots=True, init=False)
class ConfigRecoveryDescriptor:
    """Secret-free, journal-safe identity for retained config recovery files."""

    transaction_digest: str
    parent_identity: tuple[int, int]
    snapshot_reference: str
    snapshot_identity: tuple[int, int]
    candidate_reference: str
    candidate_identity: tuple[int, int]
    displaced_reference: str
    displaced_identity: tuple[int, int] | None
    backup_stage_reference: str
    backup_reference: str
    backup_identity: tuple[int, int] | None
    before_identity: tuple[int, int] | None
    before_snapshot_digest: str
    before_byte_digest: str
    before_mode: int | None
    candidate_byte_digest: str
    metadata_fingerprint: str
    persistent_backup: bool
    _binding_digest: str
    _seal: object

    def __init__(
        self,
        *,
        transaction_digest: str,
        parent_identity: tuple[int, int],
        snapshot_reference: str,
        snapshot_identity: tuple[int, int],
        candidate_reference: str,
        candidate_identity: tuple[int, int],
        displaced_reference: str,
        displaced_identity: tuple[int, int] | None,
        backup_stage_reference: str,
        backup_reference: str,
        backup_identity: tuple[int, int] | None,
        before_identity: tuple[int, int] | None,
        before_snapshot_digest: str,
        before_byte_digest: str,
        before_mode: int | None,
        candidate_byte_digest: str,
        metadata_fingerprint: str,
        persistent_backup: bool,
        _token: object,
    ) -> None:
        if _token is not _RECOVERY_DESCRIPTOR_TOKEN:
            raise TypeError("ConfigRecoveryDescriptor is created only by preparation")
        domain = {
            "backup_identity": backup_identity,
            "backup_reference": backup_reference,
            "backup_stage_reference": backup_stage_reference,
            "before_byte_digest": before_byte_digest,
            "before_identity": before_identity,
            "before_mode": before_mode,
            "before_snapshot_digest": before_snapshot_digest,
            "candidate_byte_digest": candidate_byte_digest,
            "candidate_identity": candidate_identity,
            "candidate_reference": candidate_reference,
            "displaced_identity": displaced_identity,
            "displaced_reference": displaced_reference,
            "metadata_fingerprint": metadata_fingerprint,
            "parent_identity": parent_identity,
            "persistent_backup": persistent_backup,
            "snapshot_identity": snapshot_identity,
            "snapshot_reference": snapshot_reference,
            "transaction_digest": transaction_digest,
        }
        binding = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        for name, value in {
            **domain,
            "_binding_digest": binding,
            "_seal": _RECOVERY_DESCRIPTOR_TOKEN,
        }.items():
            object.__setattr__(self, name, value)

    def _domain(self) -> dict[str, object]:
        return {
            "backup_identity": self.backup_identity,
            "backup_reference": self.backup_reference,
            "backup_stage_reference": self.backup_stage_reference,
            "before_byte_digest": self.before_byte_digest,
            "before_identity": self.before_identity,
            "before_mode": self.before_mode,
            "before_snapshot_digest": self.before_snapshot_digest,
            "candidate_byte_digest": self.candidate_byte_digest,
            "candidate_identity": self.candidate_identity,
            "candidate_reference": self.candidate_reference,
            "displaced_identity": self.displaced_identity,
            "displaced_reference": self.displaced_reference,
            "metadata_fingerprint": self.metadata_fingerprint,
            "parent_identity": self.parent_identity,
            "persistent_backup": self.persistent_backup,
            "snapshot_identity": self.snapshot_identity,
            "snapshot_reference": self.snapshot_reference,
            "transaction_digest": self.transaction_digest,
        }

    def _require_valid(self) -> None:
        try:
            expected = hashlib.sha256(canonical_json_bytes(self._domain())).hexdigest()
        except (AttributeError, TypeError, ValueError):
            raise _error(
                "config.external_change", "Config recovery descriptor changed."
            ) from None
        if (
            self._seal is not _RECOVERY_DESCRIPTOR_TOKEN
            or self._binding_digest != expected
        ):
            raise _error(
                "config.external_change", "Config recovery descriptor changed."
            )

    def to_record(self) -> dict[str, object]:
        self._require_valid()
        return {**self._domain(), "descriptor_digest": self._binding_digest}


@dataclass(frozen=True, slots=True, init=False)
class ConfigCommitResult:
    _state: ConfigCommitState
    _before_identity: tuple[int, int] | None
    _candidate_identity: tuple[int, int]
    _installed_identity: tuple[int, int] | None
    _durability_confirmed: bool
    _error_code: str | None
    _snapshot_reference: str
    _backup_record: BackupRecord | None
    _recovery: PreparedAtomicFile
    _binding_digest: str
    _seal: object

    def __init__(
        self,
        *,
        state: ConfigCommitState,
        before_identity: tuple[int, int] | None,
        candidate_identity: tuple[int, int],
        installed_identity: tuple[int, int] | None,
        durability_confirmed: bool,
        error_code: str | None,
        snapshot_reference: str,
        backup_record: BackupRecord | None,
        recovery: PreparedAtomicFile,
        _token: object,
    ) -> None:
        if _token is not _COMMIT_TOKEN:
            raise TypeError("ConfigCommitResult is created only by atomic commit")
        if backup_record is not None:
            backup_record._require_valid()
        domain = {
            "backup_record_binding": (
                None if backup_record is None else backup_record._binding_digest
            ),
            "before_identity": before_identity,
            "candidate_identity": candidate_identity,
            "durability_confirmed": durability_confirmed,
            "error_code": error_code,
            "installed_identity": installed_identity,
            "snapshot_reference": snapshot_reference,
            "state": state.value,
            "recovery_descriptor_binding": recovery.recovery_descriptor._binding_digest,
        }
        binding = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        for name, value in {
            "_state": state,
            "_before_identity": before_identity,
            "_candidate_identity": candidate_identity,
            "_installed_identity": installed_identity,
            "_durability_confirmed": durability_confirmed,
            "_error_code": error_code,
            "_snapshot_reference": snapshot_reference,
            "_backup_record": backup_record,
            "_recovery": recovery,
            "_binding_digest": binding,
            "_seal": _COMMIT_TOKEN,
        }.items():
            object.__setattr__(self, name, value)

    def _domain(self) -> dict[str, object]:
        return {
            "backup_record_binding": (
                None
                if self._backup_record is None
                else self._backup_record._binding_digest
            ),
            "before_identity": self._before_identity,
            "candidate_identity": self._candidate_identity,
            "durability_confirmed": self._durability_confirmed,
            "error_code": self._error_code,
            "installed_identity": self._installed_identity,
            "snapshot_reference": self._snapshot_reference,
            "state": self._state.value,
            "recovery_descriptor_binding": (
                self._recovery.recovery_descriptor._binding_digest
            ),
        }

    def _require_valid(self) -> None:
        try:
            if self._backup_record is not None:
                self._backup_record._require_valid()
            expected = hashlib.sha256(canonical_json_bytes(self._domain())).hexdigest()
        except (AttributeError, TypeError, ValueError):
            raise _error(
                "config.external_change", "Config commit result changed."
            ) from None
        if self._seal is not _COMMIT_TOKEN or self._binding_digest != expected:
            raise _error("config.external_change", "Config commit result changed.")

    @property
    def state(self) -> ConfigCommitState:
        self._require_valid()
        return self._state

    @property
    def before_identity(self) -> tuple[int, int] | None:
        self._require_valid()
        return self._before_identity

    @property
    def candidate_identity(self) -> tuple[int, int]:
        self._require_valid()
        return self._candidate_identity

    @property
    def installed_identity(self) -> tuple[int, int] | None:
        self._require_valid()
        return self._installed_identity

    @property
    def durability_confirmed(self) -> bool:
        self._require_valid()
        return self._durability_confirmed

    @property
    def error_code(self) -> str | None:
        self._require_valid()
        return self._error_code

    @property
    def snapshot_reference(self) -> str:
        self._require_valid()
        return self._snapshot_reference

    @property
    def backup_record(self) -> BackupRecord | None:
        self._require_valid()
        return self._backup_record

    @property
    def recovery(self) -> PreparedAtomicFile:
        self._require_valid()
        return self._recovery

    @property
    def recovery_descriptor(self) -> ConfigRecoveryDescriptor:
        self._require_valid()
        return self._recovery.recovery_descriptor

    def __reduce__(self) -> Never:
        raise TypeError("config commit results are not serializable")


def _identity_from_descriptor(descriptor: int) -> tuple[int, int]:
    if os.name == "nt":
        return _paths._windows_handle_status(descriptor).identity
    status = os.fstat(descriptor)
    return status.st_dev, status.st_ino


def _write_all(descriptor: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        written = os.write(descriptor, raw[offset : offset + 64 * 1024])
        if written <= 0:
            raise OSError(errno.EIO, "short config write")
        offset += written


def _sync_file(descriptor: int) -> None:
    os.fsync(descriptor)


def _sync_parent(descriptor: int) -> None:
    if os.name != "nt":
        os.fsync(descriptor)


def _read_posix(descriptor: int, limit: int) -> bytes:
    status = os.fstat(descriptor)
    if status.st_size > limit:
        raise OSError(errno.EFBIG, "config recovery file exceeds limit")
    raw = os.pread(descriptor, limit + 1, 0)
    if len(raw) != status.st_size or len(raw) > limit:
        raise OSError(errno.ESTALE, "config recovery file changed")
    return raw


def _private_posix_file(descriptor: int, *, mode: int) -> tuple[int, int]:
    status = os.fstat(descriptor)
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_uid != os.geteuid()
        or status.st_gid != os.getegid()
        or stat.S_IMODE(status.st_mode) != mode
        or status.st_nlink != 1
        or not _paths._posix_security_metadata_supported(descriptor, status)
    ):
        raise OSError(errno.EPERM, "config staging metadata is unsupported")
    return status.st_dev, status.st_ino


def _set_descriptor_xattr(descriptor: int, name: bytes, value: bytes) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        function = libc.fsetxattr
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, "descriptor xattr writes are unavailable") from exc
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
            ctypes.c_int,
        ]
        suffix = (0,)
    else:
        raise OSError(errno.ENOSYS, "descriptor xattr writes are unavailable")
    function.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(value) if value else None
    if function(descriptor, name, buffer, len(value), *suffix) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def _remove_descriptor_xattr(descriptor: int, name: bytes) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        function = libc.fremovexattr
    except AttributeError as exc:
        raise OSError(
            errno.ENOSYS,
            "descriptor xattr removal is unavailable",
        ) from exc
    if sys.platform == "darwin":
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        suffix: tuple[object, ...] = (0,)
    elif sys.platform.startswith("linux"):
        function.argtypes = [ctypes.c_int, ctypes.c_char_p]
        suffix = ()
    else:
        raise OSError(errno.ENOSYS, "descriptor xattr removal is unavailable")
    function.restype = ctypes.c_int
    if function(descriptor, name, *suffix) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def _apply_posix_xattrs(
    descriptor: int,
    expected: tuple[tuple[bytes, bytes], ...],
) -> None:
    desired = dict(expected)
    observed = dict(_descriptor_xattrs(descriptor))
    for name in observed.keys() - desired.keys():
        _remove_descriptor_xattr(descriptor, name)
    for name, value in desired.items():
        if observed.get(name) != value:
            _set_descriptor_xattr(descriptor, name, value)
    if _descriptor_xattrs(descriptor) != expected:
        raise OSError(errno.ESTALE, "config xattr metadata changed")


def _preserved_metadata_matches(
    descriptor: int,
    prepared: PreparedAtomicFile,
) -> bool:
    if os.name == "nt":
        try:
            _private_windows_file(descriptor)
            attributes, authorization = _windows_metadata_projection(descriptor)
            return (
                attributes == prepared._target_windows_attributes
                and authorization == prepared._target_windows_authorization
            )
        except (ForgeError, OSError, TypeError, ValueError):
            return False
    try:
        status = os.fstat(descriptor)
        return (
            stat.S_ISREG(status.st_mode)
            and status.st_uid == os.geteuid()
            and status.st_gid == os.getegid()
            and status.st_nlink == 1
            and stat.S_IMODE(status.st_mode) == prepared._target_mode
            and not bool(getattr(status, "st_flags", 0))
            and _paths._posix_security_metadata_supported(descriptor, status)
            and _descriptor_xattrs(descriptor) == prepared._target_posix_xattrs
        )
    except (ForgeError, OSError, TypeError, ValueError):
        return False


def _require_parent_authority(prepared: PreparedAtomicFile) -> None:
    path = prepared._path
    try:
        if (
            type(path) is not ConfigPathProof
            or path._closed
            or not path._namespace._validate_namespace_binding()
            or not _paths._absolute_home_binding_is_valid(
                path._home_native,
                path._home_ancestry,
                path._home_identity,
                path._filesystem_guard,
                windows=path._windows,
            )
            or _identity_from_descriptor(prepared._parent_descriptor)
            != prepared._snapshot.parent_identity
        ):
            raise OSError(errno.ESTALE, "config parent authority changed")
        if os.name == "nt":
            windows_status = _paths._windows_handle_status(prepared._parent_descriptor)
            valid = (
                windows_status.identity == prepared._snapshot.parent_identity
                and windows_status.is_directory
                and not windows_status.is_reparse
                and path._filesystem_guard(prepared._parent_descriptor)
                and _paths._windows_private_directory(
                    prepared._parent_descriptor,
                    exact=False,
                )
            )
        else:
            posix_status = os.fstat(prepared._parent_descriptor)
            valid = (
                (posix_status.st_dev, posix_status.st_ino)
                == prepared._snapshot.parent_identity
                and path._filesystem_guard(prepared._parent_descriptor)
                and _paths._private_directory(
                    prepared._parent_descriptor,
                    posix_status,
                    exact=False,
                )
            )
        if not valid or not path._namespace._validate_namespace_binding():
            raise OSError(errno.ESTALE, "config parent metadata changed")
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        raise _error(
            "config.external_change",
            "Config parent authority changed.",
        ) from None


def _create_posix_exclusive(parent: int, name: str) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    return os.open(name, flags, 0o600, dir_fd=parent)


def _populate_posix_stage(descriptor: int, raw: bytes, *, mode: int) -> None:
    _write_all(descriptor, raw)
    os.fchmod(descriptor, mode)
    _sync_file(descriptor)
    _private_posix_file(descriptor, mode=mode)


def _write_windows_all(handle: int, raw: bytes) -> None:
    _ownership._windows_write_all(handle, raw)
    _ownership._windows_flush(handle)


def _private_windows_file(handle: int) -> tuple[int, int]:
    status = _paths._windows_handle_status(handle)
    if (
        status.is_directory
        or status.is_reparse
        or status.link_count != 1
        or status.attributes & ~(0x00000020 | 0x00000080)
        or not _paths._windows_private_authorization(handle, exact=True)
    ):
        raise OSError(errno.EPERM, "config staging DACL is unsupported")
    return status.identity


def _create_windows_exclusive(parent: int, name: str) -> int:
    return _paths._windows_create_private_file(parent, name)


def _populate_windows_stage(handle: int, raw: bytes) -> None:
    _write_windows_all(handle, raw)
    _private_windows_file(handle)


def _name_binds(parent: int, name: str, identity: tuple[int, int]) -> bool:
    if os.name == "nt":
        opened = 0
        try:
            opened = _paths._windows_open_child(
                parent, name, directory=False, read_data=True
            )
            return _identity_from_descriptor(opened) == identity
        except (ForgeError, OSError, ValueError):
            return False
        finally:
            if opened:
                _paths._windows_close(opened)
    try:
        status = os.stat(name, dir_fd=parent, follow_symlinks=False)
        return (
            stat.S_ISREG(status.st_mode)
            and status.st_nlink == 1
            and (status.st_dev, status.st_ino) == identity
        )
    except OSError:
        return False


def _name_exists(parent: int, name: str) -> bool:
    if os.name == "nt":
        return name in _ownership._windows_list_names(parent, limit=4096)
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _unlink_owned(
    parent: int,
    name: str,
    descriptor: int,
    identity: tuple[int, int],
    *,
    allow_moved: bool = False,
) -> bool:
    try:
        if _identity_from_descriptor(descriptor) != identity:
            return False
    except OSError:
        return False
    if os.name == "nt":
        if allow_moved and not _name_exists(parent, name):
            return True
        try:
            if _private_windows_file(descriptor) != identity:
                return False
            _ownership._windows_delete_handle(descriptor)
        except OSError:
            return False
        return True
    if not _name_exists(parent, name):
        try:
            return allow_moved or os.fstat(descriptor).st_nlink == 0
        except OSError:
            return False
    if not _name_binds(parent, name, identity):
        return False
    quarantine = f"{_PRIVATE_PREFIX}cleanup-{secrets.token_hex(16)}"
    try:
        _paths._exclusive_posix_rename(parent, name, quarantine)
    except (ForgeError, OSError, ValueError):
        return False
    opened = -1
    exact = False
    try:
        opened = os.open(
            quarantine,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent,
        )
        status = os.fstat(opened)
        exact = (
            stat.S_ISREG(status.st_mode)
            and status.st_nlink == 1
            and (status.st_dev, status.st_ino) == identity
            and _identity_from_descriptor(descriptor) == identity
            and _name_binds(parent, quarantine, identity)
        )
        if exact:
            os.unlink(quarantine, dir_fd=parent)
            return os.fstat(descriptor).st_nlink == 0
    except OSError:
        pass
    finally:
        _close_descriptor(opened)
    try:
        _paths._exclusive_posix_rename(parent, quarantine, name)
    except (ForgeError, OSError, ValueError):
        return False
    return False


def _close_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        if descriptor:
            _paths._windows_close(descriptor)
    elif descriptor >= 0:
        os.close(descriptor)


def _descriptor_is_open(descriptor: int) -> bool:
    return descriptor != (0 if os.name == "nt" else -1)


def _backup_usage(parent: int) -> tuple[int, int]:
    records = 0
    total = 0
    if os.name == "nt":
        names = _ownership._windows_list_names(parent, limit=4096)
        for name in names:
            if not name.startswith(_BACKUP_PREFIX):
                continue
            records += 1
            handle = 0
            try:
                handle = _paths._windows_open_child(
                    parent, name, directory=False, read_data=True
                )
                total += _paths._windows_handle_status(handle).size
            finally:
                if handle:
                    _paths._windows_close(handle)
        return records, total
    for name in os.listdir(parent):
        if not name.startswith(_BACKUP_PREFIX):
            continue
        records += 1
        try:
            status = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISREG(status.st_mode):
            total += status.st_size
    return records, total


def _enforce_backup_quota(parent: int, incoming: int) -> None:
    records, total = _backup_usage(parent)
    policy = RECOVERY_RETENTION_POLICY.backups
    if records + 1 > policy["max_records"] or total + incoming > policy["max_bytes"]:
        raise _error(
            "config.backup_policy_exceeded", "Config backup policy is exhausted."
        )


def _validate_prepared_candidate(descriptor: int, candidate: ConfigCandidate) -> bool:
    if os.name == "nt":
        raw = _paths._windows_read(
            descriptor, limit=max(len(_candidate_bytes(candidate)), 1)
        )
    else:
        raw = _read_posix(descriptor, max(len(_candidate_bytes(candidate)), 1))
    return _validate_candidate_bytes(candidate, raw)


def _descriptor_bytes_match(descriptor: int, expected: bytes) -> bool:
    try:
        if os.name == "nt":
            raw = _paths._windows_read(descriptor, limit=max(len(expected), 1))
        else:
            raw = _read_posix(descriptor, max(len(expected), 1))
        return hashlib.sha256(raw).digest() == hashlib.sha256(expected).digest()
    except (ForgeError, OSError, TypeError, ValueError):
        return False


def _stage_metadata_digest(descriptor: int) -> str:
    if os.name == "nt":
        windows_status = _paths._windows_handle_status(descriptor)
        attributes, authorization = _windows_metadata_projection(descriptor)
        domain: dict[str, object] = {
            "attributes": attributes,
            "authorization": authorization,
            "identity": windows_status.identity,
            "link_count": windows_status.link_count,
            "platform": "windows",
        }
    else:
        posix_status = os.fstat(descriptor)
        domain = {
            "flags": getattr(posix_status, "st_flags", 0),
            "gid": posix_status.st_gid,
            "identity": (posix_status.st_dev, posix_status.st_ino),
            "link_count": posix_status.st_nlink,
            "mode": stat.S_IMODE(posix_status.st_mode),
            "platform": "posix",
            "uid": posix_status.st_uid,
            "xattrs": tuple(
                (name.decode("ascii"), hashlib.sha256(value).hexdigest())
                for name, value in _descriptor_xattrs(descriptor)
            ),
        }
    return hashlib.sha256(canonical_json_bytes(domain)).hexdigest()


class PreparedAtomicFile:
    """Retained same-directory publication and rollback capabilities."""

    __slots__ = (
        "_backup_descriptor",
        "_backup_identity",
        "_backup_promoted",
        "_backup_removed",
        "_backup_record",
        "_backup_reference",
        "_backup_stage_reference",
        "_binding_digest",
        "_candidate",
        "_candidate_descriptor",
        "_candidate_identity",
        "_candidate_published",
        "_closed",
        "_commit_attempted",
        "_displaced_descriptor",
        "_displaced_identity",
        "_displaced_reference",
        "_displaced_removed",
        "_parent_descriptor",
        "_path",
        "_recovery_descriptor",
        "_retain_recovery",
        "_replace_conflict",
        "_seal",
        "_snapshot",
        "_snapshot_descriptor",
        "_snapshot_identity",
        "_snapshot_removed",
        "_snapshot_stage_metadata",
        "_target_mode",
        "_target_posix_xattrs",
        "_target_windows_attributes",
        "_target_windows_authorization",
        "_candidate_stage_metadata",
        "_backup_stage_metadata",
        "candidate_reference",
        "snapshot_reference",
        "transaction_digest",
    )

    def __init__(
        self,
        *,
        path: ConfigPathProof,
        snapshot: ConfigSnapshot,
        candidate: ConfigCandidate,
        parent_descriptor: int,
        snapshot_descriptor: int,
        snapshot_identity: tuple[int, int],
        candidate_descriptor: int,
        candidate_identity: tuple[int, int],
        backup_descriptor: int,
        backup_identity: tuple[int, int] | None,
        transaction_digest: str,
        snapshot_reference: str,
        candidate_reference: str,
        backup_stage_reference: str,
        backup_reference: str,
        _token: object,
    ) -> None:
        if _token is not _PREPARED_TOKEN:
            raise TypeError("PreparedAtomicFile is created only by preparation")
        self.transaction_digest = transaction_digest
        self.snapshot_reference = snapshot_reference
        self.candidate_reference = candidate_reference
        self._backup_stage_reference = backup_stage_reference
        self._backup_reference = backup_reference
        self._backup_identity = backup_identity
        self._backup_record: BackupRecord | None = None
        self._backup_promoted = False
        self._backup_removed = False
        self._displaced_reference = (
            f"{_PRIVATE_PREFIX}{transaction_digest[:24]}.displaced"
            if os.name == "nt"
            else candidate_reference
        )
        self._displaced_descriptor = 0 if os.name == "nt" else -1
        self._displaced_identity: tuple[int, int] | None = None
        self._displaced_removed = False
        self._path = path
        self._snapshot = snapshot
        self._candidate = candidate
        self._parent_descriptor = parent_descriptor
        self._snapshot_descriptor = snapshot_descriptor
        self._snapshot_identity = snapshot_identity
        self._snapshot_removed = False
        self._candidate_descriptor = candidate_descriptor
        self._candidate_identity = candidate_identity
        self._candidate_published = False
        self._backup_descriptor = backup_descriptor
        self._snapshot_stage_metadata = _stage_metadata_digest(snapshot_descriptor)
        self._candidate_stage_metadata = _stage_metadata_digest(candidate_descriptor)
        self._backup_stage_metadata = (
            None
            if backup_identity is None
            else _stage_metadata_digest(backup_descriptor)
        )
        self._target_mode = snapshot.mode if snapshot.mode is not None else 0o600
        self._target_posix_xattrs = (
            ()
            if os.name == "nt"
            else (
                snapshot._posix_xattrs
                if snapshot.present
                else _descriptor_xattrs(candidate_descriptor)
            )
        )
        if os.name == "nt":
            if snapshot.present:
                self._target_windows_attributes = snapshot._windows_attributes
                self._target_windows_authorization = snapshot._windows_authorization
            else:
                (
                    self._target_windows_attributes,
                    self._target_windows_authorization,
                ) = _windows_metadata_projection(candidate_descriptor)
        else:
            self._target_windows_attributes = None
            self._target_windows_authorization = None
        self._recovery_descriptor = ConfigRecoveryDescriptor(
            transaction_digest=transaction_digest,
            parent_identity=snapshot.parent_identity,
            snapshot_reference=snapshot_reference,
            snapshot_identity=snapshot_identity,
            candidate_reference=candidate_reference,
            candidate_identity=candidate_identity,
            displaced_reference=self._displaced_reference,
            displaced_identity=None,
            backup_stage_reference=backup_stage_reference,
            backup_reference=backup_reference,
            backup_identity=backup_identity,
            before_identity=snapshot.leaf_identity,
            before_snapshot_digest=snapshot.snapshot_digest,
            before_byte_digest=snapshot.byte_digest,
            before_mode=snapshot.mode,
            candidate_byte_digest=candidate.byte_digest,
            metadata_fingerprint=snapshot.metadata_fingerprint,
            persistent_backup=candidate.persistent_backup,
            _token=_RECOVERY_DESCRIPTOR_TOKEN,
        )
        self._binding_digest = hashlib.sha256(
            canonical_json_bytes(self._domain())
        ).hexdigest()
        self._commit_attempted = False
        self._retain_recovery = False
        self._replace_conflict = False
        self._closed = False
        self._seal = _PREPARED_TOKEN

    @property
    def candidate_identity(self) -> tuple[int, int]:
        return self._candidate_identity

    @property
    def snapshot_identity(self) -> tuple[int, int]:
        return self._snapshot_identity

    @property
    def backup_record(self) -> BackupRecord | None:
        if self._backup_record is not None:
            self._backup_record._require_valid()
        return self._backup_record

    @property
    def recovery_descriptor(self) -> ConfigRecoveryDescriptor:
        self._recovery_descriptor._require_valid()
        return self._recovery_descriptor

    def _domain(self) -> dict[str, object]:
        return {
            "backup_identity": self._backup_identity,
            "backup_reference": self._backup_reference,
            "backup_stage_reference": self._backup_stage_reference,
            "backup_stage_metadata": self._backup_stage_metadata,
            "candidate_digest": self._candidate.byte_digest,
            "candidate_identity": self._candidate_identity,
            "candidate_reference": self.candidate_reference,
            "candidate_stage_metadata": self._candidate_stage_metadata,
            "displaced_identity": self._displaced_identity,
            "displaced_reference": self._displaced_reference,
            "parent_identity": self._snapshot.parent_identity,
            "recovery_descriptor_binding": self._recovery_descriptor._binding_digest,
            "snapshot_digest": self._snapshot.snapshot_digest,
            "snapshot_identity": self._snapshot_identity,
            "snapshot_reference": self.snapshot_reference,
            "snapshot_stage_metadata": self._snapshot_stage_metadata,
            "target_mode": self._target_mode,
            "target_posix_xattrs": tuple(
                (name.decode("ascii"), hashlib.sha256(value).hexdigest())
                for name, value in self._target_posix_xattrs
            ),
            "target_windows_attributes": self._target_windows_attributes,
            "target_windows_authorization": self._target_windows_authorization,
            "transaction_digest": self.transaction_digest,
        }

    def _bind_displaced(
        self,
        descriptor: int,
        identity: tuple[int, int],
    ) -> None:
        if self._displaced_identity is not None or not _descriptor_is_open(descriptor):
            raise _error(
                "config.commit_ambiguous",
                "Displaced config evidence could not be bound.",
            )
        self._displaced_descriptor = descriptor
        self._displaced_identity = identity
        self._recovery_descriptor = ConfigRecoveryDescriptor(
            transaction_digest=self.transaction_digest,
            parent_identity=self._snapshot.parent_identity,
            snapshot_reference=self.snapshot_reference,
            snapshot_identity=self._snapshot_identity,
            candidate_reference=self.candidate_reference,
            candidate_identity=self._candidate_identity,
            displaced_reference=self._displaced_reference,
            displaced_identity=identity,
            backup_stage_reference=self._backup_stage_reference,
            backup_reference=self._backup_reference,
            backup_identity=self._backup_identity,
            before_identity=self._snapshot.leaf_identity,
            before_snapshot_digest=self._snapshot.snapshot_digest,
            before_byte_digest=self._snapshot.byte_digest,
            before_mode=self._snapshot.mode,
            candidate_byte_digest=self._candidate.byte_digest,
            metadata_fingerprint=self._snapshot.metadata_fingerprint,
            persistent_backup=self._candidate.persistent_backup,
            _token=_RECOVERY_DESCRIPTOR_TOKEN,
        )
        self._binding_digest = hashlib.sha256(
            canonical_json_bytes(self._domain())
        ).hexdigest()

    def _require_bound(self) -> None:
        """Validate sealed exact evidence without requiring a live config value."""

        try:
            if self._closed or self._seal is not _PREPARED_TOKEN:
                raise AttributeError
            self._snapshot._require_valid()
            self._candidate._require_valid()
            self._recovery_descriptor._require_valid()
            expected = hashlib.sha256(canonical_json_bytes(self._domain())).hexdigest()
            if (
                self._binding_digest != expected
                or self._candidate.snapshot_digest != self._snapshot.snapshot_digest
            ):
                raise AttributeError
            _require_parent_authority(self)
        except (AttributeError, ForgeError, OSError, TypeError, ValueError):
            raise _error(
                "config.external_change",
                "Config recovery authority changed.",
            ) from None

    def _require_ready(self) -> None:
        if self._closed or self._seal is not _PREPARED_TOKEN:
            raise _error("config.external_change", "Prepared config state is closed.")
        self._snapshot._require_valid()
        self._candidate._require_valid()
        self._recovery_descriptor._require_valid()
        expected = hashlib.sha256(canonical_json_bytes(self._domain())).hexdigest()
        if (
            self._binding_digest != expected
            or self._candidate.snapshot_digest != self._snapshot.snapshot_digest
            or _identity_from_descriptor(self._parent_descriptor)
            != self._snapshot.parent_identity
            or _identity_from_descriptor(self._snapshot_descriptor)
            != self._snapshot_identity
            or _identity_from_descriptor(self._candidate_descriptor)
            != self._candidate_identity
            or (
                not self._snapshot_removed
                and not _name_binds(
                    self._parent_descriptor,
                    self.snapshot_reference,
                    self._snapshot_identity,
                )
            )
            or not _name_binds(
                self._parent_descriptor,
                self.candidate_reference,
                self._candidate_identity,
            )
            or self._displaced_identity is not None
            or (
                self._displaced_reference != self.candidate_reference
                and _name_exists(
                    self._parent_descriptor,
                    self._displaced_reference,
                )
            )
            or not _descriptor_bytes_match(
                self._snapshot_descriptor,
                _snapshot_bytes(self._snapshot),
            )
            or not _validate_prepared_candidate(
                self._candidate_descriptor,
                self._candidate,
            )
            or _stage_metadata_digest(self._snapshot_descriptor)
            != self._snapshot_stage_metadata
            or _stage_metadata_digest(self._candidate_descriptor)
            != self._candidate_stage_metadata
            or (
                self._backup_identity is not None
                and (
                    _identity_from_descriptor(self._backup_descriptor)
                    != self._backup_identity
                    or not _name_binds(
                        self._parent_descriptor,
                        self._backup_stage_reference,
                        self._backup_identity,
                    )
                    or not _descriptor_bytes_match(
                        self._backup_descriptor,
                        _snapshot_bytes(self._snapshot),
                    )
                    or _stage_metadata_digest(self._backup_descriptor)
                    != self._backup_stage_metadata
                )
            )
        ):
            raise _error("config.external_change", "Prepared config state changed.")
        try:
            if os.name == "nt":
                _private_windows_file(self._snapshot_descriptor)
                _private_windows_file(self._candidate_descriptor)
                if self._backup_identity is not None:
                    _private_windows_file(self._backup_descriptor)
            else:
                _private_posix_file(self._snapshot_descriptor, mode=0o600)
                _private_posix_file(self._candidate_descriptor, mode=0o600)
                if self._backup_identity is not None:
                    _private_posix_file(self._backup_descriptor, mode=0o600)
        except OSError:
            raise _error(
                "config.external_change", "Prepared config metadata changed."
            ) from None
        self._path._require_current()

    def _mark_commit_attempted(self) -> None:
        if self._commit_attempted:
            raise _error(
                "config.external_change", "Config commit was already attempted."
            )
        self._commit_attempted = True

    def _require_recovery_open(self) -> None:
        if self._closed or self._seal is not _PREPARED_TOKEN:
            raise _error("config.external_change", "Config recovery state is closed.")
        self._snapshot._require_valid()
        self._candidate._require_valid()
        self._recovery_descriptor._require_valid()
        expected = hashlib.sha256(canonical_json_bytes(self._domain())).hexdigest()
        if (
            self._backup_identity is not None
            and not self._backup_promoted
            and _name_binds(
                self._parent_descriptor,
                self._backup_reference,
                self._backup_identity,
            )
        ):
            self._mark_backup_promoted()
        backup_name = (
            self._backup_reference
            if self._backup_promoted
            else self._backup_stage_reference
        )
        if (
            self._binding_digest != expected
            or _identity_from_descriptor(self._parent_descriptor)
            != self._snapshot.parent_identity
            or _identity_from_descriptor(self._snapshot_descriptor)
            != self._snapshot_identity
            or _identity_from_descriptor(self._candidate_descriptor)
            != self._candidate_identity
            or (
                not self._snapshot_removed
                and not _name_binds(
                    self._parent_descriptor,
                    self.snapshot_reference,
                    self._snapshot_identity,
                )
            )
            or not _descriptor_bytes_match(
                self._snapshot_descriptor,
                _snapshot_bytes(self._snapshot),
            )
            or not _validate_prepared_candidate(
                self._candidate_descriptor,
                self._candidate,
            )
            or _stage_metadata_digest(self._snapshot_descriptor)
            != self._snapshot_stage_metadata
            or (
                self._displaced_identity is not None
                and (
                    _identity_from_descriptor(self._displaced_descriptor)
                    != self._displaced_identity
                    or (
                        not self._displaced_removed
                        and not _name_binds(
                            self._parent_descriptor,
                            self._displaced_reference,
                            self._displaced_identity,
                        )
                    )
                    or (
                        not self._replace_conflict
                        and not _descriptor_bytes_match(
                            self._displaced_descriptor,
                            _snapshot_bytes(self._snapshot),
                        )
                    )
                    or (
                        not self._replace_conflict
                        and not self._displaced_removed
                        and not _preserved_metadata_matches(
                            self._displaced_descriptor,
                            self,
                        )
                    )
                )
            )
            or (
                self._backup_identity is not None
                and (
                    _identity_from_descriptor(self._backup_descriptor)
                    != self._backup_identity
                    or (
                        not self._backup_removed
                        and not _name_binds(
                            self._parent_descriptor,
                            backup_name,
                            self._backup_identity,
                        )
                    )
                    or not _descriptor_bytes_match(
                        self._backup_descriptor,
                        _snapshot_bytes(self._snapshot),
                    )
                    or _stage_metadata_digest(self._backup_descriptor)
                    != self._backup_stage_metadata
                )
            )
        ):
            raise _error("config.external_change", "Config recovery state changed.")
        try:
            if os.name == "nt":
                _private_windows_file(self._snapshot_descriptor)
                _private_windows_file(self._candidate_descriptor)
                if self._backup_identity is not None:
                    _private_windows_file(self._backup_descriptor)
            else:
                _private_posix_file(self._snapshot_descriptor, mode=0o600)
                _private_posix_file(
                    self._candidate_descriptor,
                    mode=self._target_mode,
                )
                if self._backup_identity is not None:
                    _private_posix_file(self._backup_descriptor, mode=0o600)
        except OSError:
            raise _error(
                "config.external_change", "Config recovery metadata changed."
            ) from None
        if not _preserved_metadata_matches(self._candidate_descriptor, self):
            raise _error(
                "config.external_change",
                "Config recovery candidate metadata changed.",
            )
        _require_parent_authority(self)

    def _retain(self) -> None:
        self._retain_recovery = True

    def _mark_backup_promoted(self) -> None:
        if self._backup_identity is None or self._backup_promoted:
            return
        if not _name_binds(
            self._parent_descriptor,
            self._backup_reference,
            self._backup_identity,
        ):
            raise _error(
                "config.commit_ambiguous", "Config backup promotion is ambiguous."
            )
        self._backup_record = BackupRecord(
            transaction_digest=self.transaction_digest,
            relative_path=self._backup_reference,
            backup_identity=self._backup_identity,
            original_identity=self._snapshot.leaf_identity,
            original_digest=self._snapshot.byte_digest,
            metadata_fingerprint=self._snapshot.metadata_fingerprint,
            _token=_BACKUP_TOKEN,
        )
        self._backup_record._require_valid()
        self._backup_promoted = True

    def close(self) -> None:
        if self._closed:
            return
        if not self._retain_recovery:
            if self._replace_conflict:
                self._retain_recovery = True
                raise _error(
                    "config.commit_ambiguous",
                    "Displaced config evidence must be retained.",
                )
            if (
                self._displaced_identity is not None
                and self._displaced_reference == self.candidate_reference
            ):
                candidate_removed = _unlink_owned(
                    self._parent_descriptor,
                    self._displaced_reference,
                    self._displaced_descriptor,
                    self._displaced_identity,
                )
                if candidate_removed:
                    self._displaced_removed = True
            else:
                candidate_removed = _unlink_owned(
                    self._parent_descriptor,
                    self.candidate_reference,
                    self._candidate_descriptor,
                    self._candidate_identity,
                    allow_moved=self._candidate_published,
                )
            snapshot_removed = _unlink_owned(
                self._parent_descriptor,
                self.snapshot_reference,
                self._snapshot_descriptor,
                self._snapshot_identity,
            )
            if snapshot_removed:
                self._snapshot_removed = True
            removed = [candidate_removed, snapshot_removed]
            if (
                self._displaced_identity is not None
                and self._displaced_reference != self.candidate_reference
            ):
                displaced_removed = _unlink_owned(
                    self._parent_descriptor,
                    self._displaced_reference,
                    self._displaced_descriptor,
                    self._displaced_identity,
                )
                if displaced_removed:
                    self._displaced_removed = True
                removed.append(displaced_removed)
            if (
                self._backup_identity is not None
                and _descriptor_is_open(self._backup_descriptor)
                and not self._backup_promoted
            ):
                backup_removed = _unlink_owned(
                    self._parent_descriptor,
                    self._backup_stage_reference,
                    self._backup_descriptor,
                    self._backup_identity,
                )
                if backup_removed:
                    self._backup_removed = True
                removed.append(backup_removed)
            if not all(removed):
                self._retain_recovery = True
                raise _error(
                    "config.commit_ambiguous",
                    "Config recovery cleanup did not remove every owned stage.",
                )
            try:
                _sync_parent(self._parent_descriptor)
            except OSError:
                self._retain_recovery = True
                raise _error(
                    "config.commit_ambiguous",
                    "Config recovery cleanup durability is ambiguous.",
                ) from None
        for descriptor in (
            self._candidate_descriptor,
            self._snapshot_descriptor,
            self._backup_descriptor,
            self._displaced_descriptor,
            self._parent_descriptor,
        ):
            _close_descriptor(descriptor)
        self._closed = True

    def __enter__(self) -> PreparedAtomicFile:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            "PreparedAtomicFile(transaction_digest="
            f"{self.transaction_digest!r}, snapshot_reference={self.snapshot_reference!r}, "
            f"candidate_reference={self.candidate_reference!r})"
        )

    def __reduce__(self) -> Never:
        raise TypeError("prepared config capabilities are not serializable")


def _verify_current_matches_snapshot(
    path: ConfigPathProof, snapshot: ConfigSnapshot
) -> None:
    path._require_current()
    if (
        path.parent_identity != snapshot.parent_identity
        or path.leaf_identity != snapshot.leaf_identity
    ):
        raise _error("config.external_change", "Config path identity changed.")
    opened = path.open_leaf()
    try:
        if opened is None:
            if snapshot.present:
                raise _error("config.external_change", "Config disappeared.")
            return
        if not snapshot.present or opened.identity != snapshot.leaf_identity:
            raise _error("config.external_change", "Config identity changed.")
        raw = opened.read_bytes(limit=max(len(_snapshot_bytes(snapshot)), 1))
        if hashlib.sha256(raw).hexdigest() != snapshot.byte_digest:
            raise _error("config.external_change", "Config bytes changed.")
    finally:
        if opened is not None:
            opened.close()
    path._require_current()


def _create_stage(parent: int, name: str) -> int:
    if os.name == "nt":
        return _create_windows_exclusive(parent, name)
    return _create_posix_exclusive(parent, name)


def _populate_stage(descriptor: int, raw: bytes, *, mode: int) -> None:
    if os.name == "nt":
        _populate_windows_stage(descriptor, raw)
        return
    _populate_posix_stage(descriptor, raw, mode=mode)


def prepare_atomic_candidate(
    path: ConfigPathProof,
    snapshot: ConfigSnapshot,
    candidate: ConfigCandidate,
    transaction: ConfigTransaction,
) -> Result[PreparedAtomicFile]:
    """Create exclusive same-directory snapshot/candidate files before replace."""

    parent = 0 if os.name == "nt" else -1
    snapshot_descriptor = 0 if os.name == "nt" else -1
    candidate_descriptor = 0 if os.name == "nt" else -1
    backup_descriptor = 0 if os.name == "nt" else -1
    snapshot_identity: tuple[int, int] | None = None
    candidate_identity: tuple[int, int] | None = None
    backup_identity: tuple[int, int] | None = None
    transaction_digest = ""
    snapshot_reference = ""
    candidate_reference = ""
    backup_stage_reference = ""
    backup_reference = ""
    failure: ForgeError | None = None
    preparation_recovery: ConfigPreparationRecovery | None = None
    try:
        if type(transaction) is not ConfigTransaction:
            raise _error("config.external_change", "Atomic config input is invalid.")
        authority = transaction._consume()
        if (
            type(path) is not ConfigPathProof
            or type(snapshot) is not ConfigSnapshot
            or type(candidate) is not ConfigCandidate
            or authority is None
        ):
            raise _error("config.external_change", "Atomic config input is invalid.")
        (
            transaction_digest,
            snapshot_reference,
            candidate_reference,
            backup_stage_reference,
            backup_reference,
        ) = authority
        snapshot._require_valid()
        candidate._require_valid()
        if (
            candidate.snapshot_digest != snapshot.snapshot_digest
            or candidate.metadata_fingerprint != snapshot.metadata_fingerprint
        ):
            raise _error(
                "config.external_change", "Config candidate snapshot is stale."
            )
        _verify_current_matches_snapshot(path, snapshot)
        parent = path._duplicate_parent_descriptor()
        if _identity_from_descriptor(parent) != snapshot.parent_identity:
            raise _error("config.external_change", "Config parent identity changed.")
        preparation_recovery = ConfigPreparationRecovery(
            transaction_digest=transaction_digest,
            parent_descriptor=parent,
            parent_identity=snapshot.parent_identity,
            stages=(),
            _token=_PREPARATION_RECOVERY_TOKEN,
        )
        before_raw = _snapshot_bytes(snapshot)
        if candidate.persistent_backup:
            _enforce_backup_quota(parent, len(before_raw))
            if _name_exists(parent, backup_reference):
                raise _error(
                    "config.backup_policy_exceeded",
                    "Config backup destination is occupied.",
                )
        snapshot_descriptor, snapshot_stage = preparation_recovery._create_owned_stage(
            role="snapshot",
            reference=snapshot_reference,
        )
        snapshot_identity = _identity_from_descriptor(snapshot_descriptor)
        preparation_recovery._record_stage_identity(
            snapshot_stage,
            snapshot_identity,
        )
        _populate_stage(snapshot_descriptor, before_raw, mode=0o600)
        if candidate.persistent_backup:
            backup_descriptor, backup_stage = preparation_recovery._create_owned_stage(
                role="backup",
                reference=backup_stage_reference,
            )
            backup_identity = _identity_from_descriptor(backup_descriptor)
            preparation_recovery._record_stage_identity(
                backup_stage,
                backup_identity,
            )
            _populate_stage(backup_descriptor, before_raw, mode=0o600)
        candidate_descriptor, candidate_stage = (
            preparation_recovery._create_owned_stage(
                role="candidate",
                reference=candidate_reference,
            )
        )
        candidate_identity = _identity_from_descriptor(candidate_descriptor)
        preparation_recovery._record_stage_identity(
            candidate_stage,
            candidate_identity,
        )
        _populate_stage(
            candidate_descriptor,
            _candidate_bytes(candidate),
            mode=0o600,
        )
        if not _validate_prepared_candidate(candidate_descriptor, candidate):
            raise OSError(errno.EINVAL, "prepared candidate validation failed")
        _verify_current_matches_snapshot(path, snapshot)
        _sync_parent(parent)
        if snapshot_identity is None or candidate_identity is None:
            raise OSError(errno.ESTALE, "config staging identity unavailable")
        prepared = PreparedAtomicFile(
            path=path,
            snapshot=snapshot,
            candidate=candidate,
            parent_descriptor=parent,
            snapshot_descriptor=snapshot_descriptor,
            snapshot_identity=snapshot_identity,
            candidate_descriptor=candidate_descriptor,
            candidate_identity=candidate_identity,
            backup_descriptor=backup_descriptor,
            backup_identity=backup_identity,
            transaction_digest=transaction_digest,
            snapshot_reference=snapshot_reference,
            candidate_reference=candidate_reference,
            backup_stage_reference=backup_stage_reference,
            backup_reference=backup_reference,
            _token=_PREPARED_TOKEN,
        )
        prepared._require_ready()
        parent = 0 if os.name == "nt" else -1
        snapshot_descriptor = 0 if os.name == "nt" else -1
        candidate_descriptor = 0 if os.name == "nt" else -1
        backup_descriptor = 0 if os.name == "nt" else -1
        return Result.success(prepared)
    except ForgeError as exc:
        if exc.exit_category == 13:
            failure = exc
        else:
            failure = _error("config.external_change", "Config authority changed.")
    except FileExistsError:
        failure = _error(
            "config.atomic_write_failed", "Atomic config staging is occupied."
        )
    except (OSError, TypeError, ValueError):
        failure = _error("config.atomic_write_failed", "Atomic config staging failed.")
    finally:
        if failure is not None and preparation_recovery is not None:
            try:
                preparation_recovery._cleanup()
            except Exception:
                failure = ConfigPreparationError(preparation_recovery)
        elif failure is not None:
            cleanup_ok = True
            for descriptor in (
                candidate_descriptor,
                snapshot_descriptor,
                backup_descriptor,
            ):
                if not _descriptor_is_open(descriptor):
                    continue
                try:
                    _close_descriptor(descriptor)
                except OSError:
                    cleanup_ok = False
            if _descriptor_is_open(parent):
                try:
                    _close_descriptor(parent)
                except OSError:
                    cleanup_ok = False
            if not cleanup_ok:
                failure = _error(
                    "config.commit_ambiguous",
                    "Atomic config preparation cleanup is ambiguous.",
                )
    if failure is None:
        failure = _error("config.atomic_write_failed", "Atomic config staging failed.")
    return Result.failure(failure)


def _windows_replace_handle(source: int, parent: int, destination: str) -> None:
    from ctypes import wintypes

    class FileRenameInformation(ctypes.Structure):
        _fields_ = [
            ("replace_if_exists", ctypes.c_ubyte),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", ctypes.c_wchar * len(destination)),
        ]

    class StatusOrPointer(ctypes.Union):
        _fields_ = [("status", wintypes.LONG), ("pointer", wintypes.LPVOID)]

    class IoStatusBlock(ctypes.Structure):
        _anonymous_ = ("result",)
        _fields_ = [
            ("result", StatusOrPointer),
            ("information", ctypes.c_size_t),
        ]

    information = FileRenameInformation()
    information.replace_if_exists = 1
    information.root_directory = parent
    information.file_name_length = len(destination.encode("utf-16-le"))
    information.file_name = destination
    io_status = IoStatusBlock()
    ntdll = _paths._windows_dll("ntdll")
    ntdll.NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(IoStatusBlock),
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_int,
    ]
    ntdll.NtSetInformationFile.restype = wintypes.LONG
    result = int(
        ntdll.NtSetInformationFile(
            source,
            ctypes.byref(io_status),
            ctypes.byref(information),
            ctypes.sizeof(information),
            10,
        )
    )
    if result < 0:
        ntdll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
        ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
        number = int(ntdll.RtlNtStatusToDosError(result))
        raise _paths._windows_error(number)


def _windows_child_path(parent: int, child: str) -> str:
    from ctypes import wintypes

    if (
        not child
        or child in {".", ".."}
        or "/" in child
        or "\\" in child
        or "\0" in child
    ):
        raise ValueError("Windows child path requires one safe component")
    kernel32 = _paths._windows_dll("kernel32")
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    needed = int(kernel32.GetFinalPathNameByHandleW(parent, None, 0, 0))
    if needed <= 0:
        raise _paths._windows_error(_paths._windows_last_error())
    buffer = ctypes.create_unicode_buffer(needed + 1)
    written = int(kernel32.GetFinalPathNameByHandleW(parent, buffer, len(buffer), 0))
    if written <= 0 or written >= len(buffer):
        raise _paths._windows_error(_paths._windows_last_error())
    return buffer.value.rstrip("\\") + "\\" + child


def _windows_reopen_candidate(prepared: PreparedAtomicFile) -> None:
    for name in (_CONFIG_NAME, prepared.candidate_reference):
        descriptor = 0
        try:
            descriptor = _paths._windows_open_child(
                prepared._parent_descriptor,
                name,
                directory=False,
                read_data=True,
                write_data=True,
                delete_access=True,
            )
            if _identity_from_descriptor(
                descriptor
            ) == prepared._candidate_identity and _validate_prepared_candidate(
                descriptor, prepared._candidate
            ):
                prepared._candidate_descriptor = descriptor
                return
        except (ForgeError, OSError, TypeError, ValueError):
            pass
        if descriptor:
            _paths._windows_close(descriptor)
    raise OSError(errno.ESTALE, "prepared candidate location is ambiguous")


def _windows_replace_preserving(prepared: PreparedAtomicFile) -> None:
    from ctypes import wintypes

    if not prepared._snapshot.present:
        _paths._windows_rename_handle(
            prepared._candidate_descriptor,
            prepared._parent_descriptor,
            _CONFIG_NAME,
        )
        return
    if _name_exists(prepared._parent_descriptor, prepared._displaced_reference):
        raise FileExistsError(errno.EEXIST, "displaced evidence path is occupied")
    replaced = _windows_child_path(prepared._parent_descriptor, _CONFIG_NAME)
    replacement = _windows_child_path(
        prepared._parent_descriptor,
        prepared.candidate_reference,
    )
    displaced = _windows_child_path(
        prepared._parent_descriptor,
        prepared._displaced_reference,
    )
    _close_descriptor(prepared._candidate_descriptor)
    prepared._candidate_descriptor = 0
    kernel32 = _paths._windows_dll("kernel32")
    kernel32.ReplaceFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    kernel32.ReplaceFileW.restype = wintypes.BOOL
    succeeded = bool(
        kernel32.ReplaceFileW(
            replaced,
            replacement,
            displaced,
            0,
            None,
            None,
        )
    )
    number = 0 if succeeded else _paths._windows_last_error()
    try:
        _windows_reopen_candidate(prepared)
    except OSError:
        prepared._replace_conflict = True
        prepared._retain()
        raise
    if _name_exists(prepared._parent_descriptor, prepared._displaced_reference):
        _bind_displaced_after_replace(prepared)
    elif succeeded:
        prepared._replace_conflict = True
        prepared._retain()
        raise _error(
            "config.commit_ambiguous",
            "Windows replacement produced no displaced evidence.",
        )
    if not succeeded:
        raise _paths._windows_error(number)
    _ownership._windows_flush(prepared._candidate_descriptor)


def _posix_exchange(parent: int, first: str, second: str) -> None:
    for component in (first, second):
        if (
            not component
            or component in {".", ".."}
            or "/" in component
            or "\0" in component
        ):
            raise ValueError("exchange requires one safe component")
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        try:
            exchange = libc.renameat2
        except AttributeError as exc:
            raise OSError(errno.ENOSYS, "atomic exchange is unavailable") from exc
        exchange.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        arguments: tuple[object, ...] = (
            parent,
            os.fsencode(first),
            parent,
            os.fsencode(second),
            0x00000002,
        )
    elif sys.platform == "darwin":
        try:
            exchange = libc.renameatx_np
        except AttributeError as exc:
            raise OSError(errno.ENOSYS, "atomic exchange is unavailable") from exc
        exchange.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        arguments = (
            parent,
            os.fsencode(first),
            parent,
            os.fsencode(second),
            0x00000002,
        )
    else:
        raise OSError(errno.ENOSYS, "atomic exchange is unavailable")
    exchange.restype = ctypes.c_int
    if exchange(*arguments) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def _open_displaced(prepared: PreparedAtomicFile) -> tuple[int, tuple[int, int]]:
    if os.name == "nt":
        descriptor = _paths._windows_open_child(
            prepared._parent_descriptor,
            prepared._displaced_reference,
            directory=False,
            read_data=True,
        )
    else:
        descriptor = os.open(
            prepared._displaced_reference,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=prepared._parent_descriptor,
        )
    return descriptor, _identity_from_descriptor(descriptor)


def _bind_displaced_after_replace(prepared: PreparedAtomicFile) -> None:
    descriptor = 0 if os.name == "nt" else -1
    try:
        descriptor, identity = _open_displaced(prepared)
        exact_before = (
            prepared._snapshot.present
            and identity == prepared._snapshot.leaf_identity
            and _descriptor_bytes_match(
                descriptor,
                _snapshot_bytes(prepared._snapshot),
            )
            and _preserved_metadata_matches(descriptor, prepared)
        )
        prepared._bind_displaced(descriptor, identity)
        descriptor = 0 if os.name == "nt" else -1
        if exact_before:
            return
        prepared._replace_conflict = True
        prepared._retain()
        raise _error(
            "config.commit_ambiguous",
            "An intervening config value was retained as recovery evidence.",
        )
    finally:
        _close_descriptor(descriptor)


def _atomic_replace(prepared: PreparedAtomicFile) -> None:
    if os.name == "nt":
        _windows_replace_preserving(prepared)
        return
    if not prepared._snapshot.present:
        _paths._exclusive_posix_rename(
            prepared._parent_descriptor,
            prepared.candidate_reference,
            _CONFIG_NAME,
        )
        return
    _posix_exchange(
        prepared._parent_descriptor,
        prepared.candidate_reference,
        _CONFIG_NAME,
    )
    try:
        _bind_displaced_after_replace(prepared)
    except BaseException:
        prepared._replace_conflict = True
        prepared._retain()
        raise


def _apply_candidate_metadata(prepared: PreparedAtomicFile) -> None:
    if os.name == "nt":
        _private_windows_file(prepared._candidate_descriptor)
        return
    os.fchmod(prepared._candidate_descriptor, prepared._target_mode)
    _apply_posix_xattrs(
        prepared._candidate_descriptor,
        prepared._target_posix_xattrs,
    )
    _sync_file(prepared._candidate_descriptor)
    _private_posix_file(
        prepared._candidate_descriptor,
        mode=prepared._target_mode,
    )


def _promote_backup(prepared: PreparedAtomicFile) -> None:
    if prepared._backup_identity is None or prepared._backup_promoted:
        return
    if _name_binds(
        prepared._parent_descriptor,
        prepared._backup_reference,
        prepared._backup_identity,
    ):
        prepared._mark_backup_promoted()
        return
    if not _name_binds(
        prepared._parent_descriptor,
        prepared._backup_stage_reference,
        prepared._backup_identity,
    ) or not _descriptor_bytes_match(
        prepared._backup_descriptor,
        _snapshot_bytes(prepared._snapshot),
    ):
        raise _error("config.external_change", "Config backup stage changed.")
    if os.name == "nt":
        _paths._windows_rename_handle(
            prepared._backup_descriptor,
            prepared._parent_descriptor,
            prepared._backup_reference,
        )
    else:
        _paths._exclusive_posix_rename(
            prepared._parent_descriptor,
            prepared._backup_stage_reference,
            prepared._backup_reference,
        )
    prepared._mark_backup_promoted()


def _read_current(
    prepared: PreparedAtomicFile,
) -> tuple[ConfigCommitState, tuple[int, int] | None]:
    descriptor = 0 if os.name == "nt" else -1
    try:
        _require_parent_authority(prepared)
        if os.name == "nt":
            try:
                descriptor = _paths._windows_open_child(
                    prepared._parent_descriptor,
                    _CONFIG_NAME,
                    directory=False,
                    read_data=True,
                )
            except OSError as exc:
                if isinstance(exc, FileNotFoundError) or getattr(
                    exc, "winerror", None
                ) in {
                    2,
                    3,
                }:
                    return ConfigCommitState.ABSENT, None
                raise
            identity = _identity_from_descriptor(descriptor)
            raw = _paths._windows_read(
                descriptor,
                limit=max(
                    len(_candidate_bytes(prepared._candidate)),
                    len(_snapshot_bytes(prepared._snapshot)),
                    1,
                ),
            )
        else:
            try:
                descriptor = os.open(
                    _CONFIG_NAME,
                    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=prepared._parent_descriptor,
                )
            except FileNotFoundError:
                return ConfigCommitState.ABSENT, None
            identity = _identity_from_descriptor(descriptor)
            raw = _read_posix(
                descriptor,
                max(
                    len(_candidate_bytes(prepared._candidate)),
                    len(_snapshot_bytes(prepared._snapshot)),
                    1,
                ),
            )
        digest = hashlib.sha256(raw).hexdigest()
        metadata_matches = _preserved_metadata_matches(descriptor, prepared)
        _require_parent_authority(prepared)
        if (
            identity == prepared._candidate_identity
            and digest == prepared._candidate.byte_digest
            and metadata_matches
        ):
            return ConfigCommitState.CANDIDATE, identity
        if (
            prepared._snapshot.leaf_identity is not None
            and identity == prepared._snapshot.leaf_identity
            and digest == prepared._snapshot.byte_digest
            and metadata_matches
        ):
            return ConfigCommitState.BEFORE, identity
        return ConfigCommitState.THIRD_PARTY, identity
    finally:
        _close_descriptor(descriptor)


def _commit_result(
    prepared: PreparedAtomicFile,
    *,
    state: ConfigCommitState,
    identity: tuple[int, int] | None,
    durability_confirmed: bool,
    error_code: str | None,
) -> ConfigCommitResult:
    result = ConfigCommitResult(
        state=state,
        before_identity=prepared._snapshot.leaf_identity,
        candidate_identity=prepared._candidate_identity,
        installed_identity=identity,
        durability_confirmed=durability_confirmed,
        error_code=error_code,
        snapshot_reference=prepared.snapshot_reference,
        backup_record=prepared.backup_record,
        recovery=prepared,
        _token=_COMMIT_TOKEN,
    )
    result._require_valid()
    return result


def _is_exact_before(prepared: PreparedAtomicFile, state: ConfigCommitState) -> bool:
    return state is (
        ConfigCommitState.BEFORE
        if prepared._snapshot.present
        else ConfigCommitState.ABSENT
    )


def _unreadable_commit_state(
    prepared: PreparedAtomicFile,
) -> Result[ConfigCommitResult]:
    prepared._retain()
    return Result.failure(
        _error("config.commit_ambiguous", "Config commit state is ambiguous.")
    )


def _complete_candidate_commit(
    prepared: PreparedAtomicFile,
) -> Result[ConfigCommitResult]:
    prepared._retain()
    if prepared._replace_conflict:
        try:
            state, identity = _read_current(prepared)
        except (ForgeError, OSError, TypeError, ValueError):
            return _unreadable_commit_state(prepared)
        return Result.success(
            _commit_result(
                prepared,
                state=state,
                identity=identity,
                durability_confirmed=False,
                error_code="config.commit_ambiguous",
            )
        )
    try:
        _sync_parent(prepared._parent_descriptor)
        state, identity = _read_current(prepared)
    except (ForgeError, OSError, TypeError, ValueError):
        try:
            state, identity = _read_current(prepared)
        except (ForgeError, OSError, TypeError, ValueError):
            return _unreadable_commit_state(prepared)
        return Result.success(
            _commit_result(
                prepared,
                state=state,
                identity=identity,
                durability_confirmed=False,
                error_code="config.commit_ambiguous",
            )
        )
    if state is not ConfigCommitState.CANDIDATE:
        return Result.success(
            _commit_result(
                prepared,
                state=state,
                identity=identity,
                durability_confirmed=False,
                error_code="config.commit_ambiguous",
            )
        )
    return Result.success(
        _commit_result(
            prepared,
            state=state,
            identity=identity,
            durability_confirmed=True,
            error_code=None,
        )
    )


def _classify_commit_fault(
    prepared: PreparedAtomicFile,
) -> Result[ConfigCommitResult]:
    try:
        state, identity = _read_current(prepared)
    except (ForgeError, OSError, TypeError, ValueError):
        return _unreadable_commit_state(prepared)
    if prepared._replace_conflict:
        prepared._retain()
        return Result.success(
            _commit_result(
                prepared,
                state=state,
                identity=identity,
                durability_confirmed=False,
                error_code="config.commit_ambiguous",
            )
        )
    if state is ConfigCommitState.CANDIDATE:
        prepared._retain()
        return Result.success(
            _commit_result(
                prepared,
                state=state,
                identity=identity,
                durability_confirmed=False,
                error_code="config.commit_ambiguous",
            )
        )
    if not _is_exact_before(prepared, state):
        prepared._retain()
    return Result.success(
        _commit_result(
            prepared,
            state=state,
            identity=identity,
            durability_confirmed=False,
            error_code=(
                "config.atomic_write_failed"
                if _is_exact_before(prepared, state)
                else "config.commit_ambiguous"
            ),
        )
    )


def commit_atomic_candidate(
    prepared: PreparedAtomicFile,
    *,
    expected: ConfigSnapshot,
) -> Result[ConfigCommitResult]:
    """Replace config once, then classify exact before/candidate/third state."""

    try:
        if (
            type(prepared) is not PreparedAtomicFile
            or type(expected) is not ConfigSnapshot
        ):
            raise _error("config.external_change", "Atomic commit input is invalid.")
        prepared._require_ready()
        expected._require_valid()
        if expected.snapshot_digest != prepared._snapshot.snapshot_digest:
            raise _error("config.external_change", "Atomic commit snapshot is stale.")
        prepared._mark_commit_attempted()
    except ForgeError as exc:
        return Result.failure(exc)
    except (OSError, TypeError, ValueError):
        return Result.failure(
            _error("config.external_change", "Atomic commit input is invalid.")
        )

    try:
        _apply_candidate_metadata(prepared)
        _atomic_replace(prepared)
        state, identity = _read_current(prepared)
    except (OSError, TypeError, ValueError):
        return _classify_commit_fault(prepared)
    except ForgeError:
        return _classify_commit_fault(prepared)

    if state is ConfigCommitState.CANDIDATE:
        return _complete_candidate_commit(prepared)
    if not _is_exact_before(prepared, state):
        prepared._retain()
    return Result.success(
        _commit_result(
            prepared,
            state=state,
            identity=identity,
            durability_confirmed=False,
            error_code=(
                "config.atomic_write_failed"
                if _is_exact_before(prepared, state)
                else "config.commit_ambiguous"
            ),
        )
    )


def _validated_recovery(
    committed: ConfigCommitResult,
    *,
    state: ConfigCommitState,
) -> PreparedAtomicFile:
    if type(committed) is not ConfigCommitResult:
        raise _error("config.external_change", "Config recovery input is invalid.")
    committed._require_valid()
    if committed._state is not state:
        raise _error(
            "config.external_change", "Config recovery classification disagrees."
        )
    prepared = committed._recovery
    prepared._require_recovery_open()
    if prepared._replace_conflict:
        raise _error(
            "config.commit_ambiguous",
            "Intervening config evidence requires manual recovery.",
        )
    if (
        committed._candidate_identity != prepared._candidate_identity
        or committed._snapshot_reference != prepared.snapshot_reference
        or committed._before_identity != prepared._snapshot.leaf_identity
    ):
        raise _error("config.external_change", "Config recovery authority changed.")
    return prepared


def promote_config_backup(
    committed: ConfigCommitResult,
) -> Result[BackupRecord | None]:
    """Promote the optional backup only after the caller commits its receipt."""

    try:
        prepared = _validated_recovery(
            committed,
            state=ConfigCommitState.CANDIDATE,
        )
        state, identity = _read_current(prepared)
        if (
            state is not ConfigCommitState.CANDIDATE
            or identity != prepared._candidate_identity
        ):
            prepared._retain()
            raise _error(
                "config.commit_ambiguous", "Committed config identity changed."
            )
        if prepared._backup_identity is None:
            return Result.success(None)
        _promote_backup(prepared)
        _sync_parent(prepared._parent_descriptor)
        state, identity = _read_current(prepared)
        if (
            state is not ConfigCommitState.CANDIDATE
            or identity != prepared._candidate_identity
        ):
            prepared._retain()
            raise _error(
                "config.commit_ambiguous", "Committed config identity changed."
            )
        record = prepared.backup_record
        if record is None:
            raise _error(
                "config.commit_ambiguous", "Config backup promotion is incomplete."
            )
        return Result.success(record)
    except FileExistsError:
        return Result.failure(
            _error(
                "config.backup_policy_exceeded",
                "Config backup destination is occupied.",
            )
        )
    except ForgeError as exc:
        return Result.failure(exc)
    except (OSError, TypeError, ValueError):
        return Result.failure(
            _error("config.commit_ambiguous", "Config backup promotion is ambiguous.")
        )


def cleanup_config_recovery(
    committed: ConfigCommitResult,
) -> Result[ConfigRecoveryDescriptor]:
    """Delete only exact private evidence for an exact committed candidate."""

    try:
        prepared = _validated_recovery(
            committed,
            state=ConfigCommitState.CANDIDATE,
        )
        state, identity = _read_current(prepared)
        if (
            state is not ConfigCommitState.CANDIDATE
            or identity != prepared._candidate_identity
            or (prepared._backup_identity is not None and not prepared._backup_promoted)
        ):
            prepared._retain()
            raise _error(
                "config.commit_ambiguous", "Config recovery cleanup is not authorized."
            )
        descriptor = prepared.recovery_descriptor
        prepared._candidate_published = True
        prepared._retain_recovery = False
        prepared.close()
        return Result.success(descriptor)
    except ForgeError as exc:
        return Result.failure(exc)
    except (OSError, TypeError, ValueError):
        return Result.failure(
            _error("config.commit_ambiguous", "Config recovery cleanup is ambiguous.")
        )


def rollback_config_recovery(
    observed: ConfigCommitResult,
) -> Result[ConfigRecoveryDescriptor]:
    """Discard only private stages when config remains the exact before state."""

    try:
        if type(observed) is not ConfigCommitResult:
            raise _error("config.external_change", "Config recovery input is invalid.")
        observed._require_valid()
        prepared = observed._recovery
        expected_state = (
            ConfigCommitState.BEFORE
            if prepared._snapshot.present
            else ConfigCommitState.ABSENT
        )
        prepared = _validated_recovery(observed, state=expected_state)
        state, identity = _read_current(prepared)
        if state is not expected_state or identity != observed._installed_identity:
            prepared._retain()
            raise _error(
                "config.commit_ambiguous", "Config rollback cleanup is not authorized."
            )
        descriptor = prepared.recovery_descriptor
        prepared._retain_recovery = False
        prepared.close()
        return Result.success(descriptor)
    except ForgeError as exc:
        return Result.failure(exc)
    except (OSError, TypeError, ValueError):
        return Result.failure(
            _error("config.commit_ambiguous", "Config rollback cleanup is ambiguous.")
        )


def classify_config_after_replace(
    observed: ConfigCommitResult,
) -> Result[ConfigCommitState]:
    """Reclassify the installed config through sealed exact recovery evidence."""

    try:
        if type(observed) is not ConfigCommitResult:
            raise _error(
                "config.external_change",
                "Post-replace config evidence is invalid.",
            )
        observed._require_valid()
        prepared = observed._recovery
        prepared._require_bound()
        state, _identity = _read_current(prepared)
        return Result.success(state)
    except (ForgeError, OSError, TypeError, ValueError):
        return Result.failure(
            _error(
                "config.external_change", "Post-replace config cannot be classified."
            )
        )
