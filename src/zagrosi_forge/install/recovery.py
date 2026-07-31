"""Pure rollback-only classification for immutable transaction evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import hmac
import json
import re
from threading import Lock
from typing import TYPE_CHECKING, Callable, cast, NamedTuple, Never
import unicodedata
from weakref import finalize, WeakKeyDictionary, WeakSet

from .config import snapshot_config
from .contracts import (
    ForgeError,
    RunnerOperation,
    RunnerProvenance,
    canonical_json_bytes,
    require_runner_authority,
)
from .journal import (
    JOURNAL_STATE_MACHINE_VERSION,
    JournalConfigIdentity,
    JournalState,
    LoadedJournal,
    RollbackAction,
    load_pending,
)
from .lock import HeldInstallLock, acquire_install_lock
from .paths import OwnedRoot, PlatformPathAuthority
from .policies import LIMIT_POLICY

if TYPE_CHECKING:
    from .ownership import (
        PendingTransactionObservation,
        RecoveryCleanupObservation,
        RecoveryFinalizationObservation,
    )


RECOVERY_POLICY_VERSION = "1.2"
_SNAPSHOT_TOKEN = object()
_PLAN_TOKEN = object()
_LOCKED_PLAN_TOKEN = object()
_LIVE_OBSERVATION_TOKEN = object()
_TRANSACTION_ID = re.compile(r"tx-[0-9a-f]{32}\Z")
_DELETE_COMPONENT = re.compile(r"\.delete-[0-9a-f]{32}\.tmp\Z")
_PRECOMMIT_ROLLBACK_STATES = frozenset(
    {
        JournalState.PREPARED.value,
        JournalState.STAGED.value,
        JournalState.VERIFIED.value,
        JournalState.SOURCE_PUBLISHED.value,
        JournalState.CACHE_PUBLISHED.value,
        JournalState.PUBLISHED.value,
        JournalState.COMMIT_INTENT.value,
    }
)
_ROLLBACK_STATES = _PRECOMMIT_ROLLBACK_STATES | frozenset(
    {
        JournalState.ROLLBACK_ACTION_INTENT.value,
        JournalState.ROLLBACK_ACTION_COMPLETED.value,
    }
)
_NO_RECOVERY_STATES = frozenset(
    {
        JournalState.FINALIZED.value,
        JournalState.ROLLED_BACK.value,
    }
)


class RecoveryDisposition(str, Enum):
    NO_RECOVERY = "no_recovery"
    ROLLBACK_CANDIDATE = "rollback_candidate"
    FINALIZE_COMMITTED = "finalize_committed"
    CLEANUP_PENDING = "cleanup_pending"
    OPERATOR_CONFLICT = "operator_conflict"


class _ConfigCapture(NamedTuple):
    parent_identity: tuple[int, int]
    leaf_identity: tuple[int, int] | None
    byte_digest: str
    semantic_digest: str
    metadata_fingerprint: str
    snapshot_digest: str
    target_metadata_digest: str | None


class _RollbackCapture(NamedTuple):
    action: str
    relative_path: str
    expected_identity: tuple[int, int] | None


class _JournalCapture(NamedTuple):
    transaction_id: str
    plan_digest: str
    head_digest: str
    head_sequence: int
    head_state: str
    access_digest: str
    evidence_digest: str
    journal_location: str | None
    journal_relative: str | None
    record_digests: tuple[str, ...]
    before_config: _ConfigCapture
    candidate_config: _ConfigCapture
    committed_config: _ConfigCapture | None
    recovery_candidate_identity: tuple[int, int] | None
    rollback_actions: tuple[_RollbackCapture, ...]
    next_action_index: int | None


class _CleanupCapture(NamedTuple):
    transaction_id: str
    phase: str
    current_reference: str | None
    observation_digest: str
    location: str
    journal_relative: str
    journal_access_digest: str
    journal_evidence_digest: str
    journal_head_sequence: int
    journal_head_record_digest: str
    transaction_binding_digest: str
    delete_component: str
    authorization_digest: str


class _FinalizationCapture(NamedTuple):
    transaction_id: str
    journal_state: str
    receipt_status: str
    observation_digest: str
    journal_access_digest: str
    journal_evidence_digest: str
    journal_head_sequence: int
    journal_head_record_digest: str


class _SnapshotCapture(NamedTuple):
    journals: tuple[_JournalCapture, ...]
    cleanup_observations: tuple[_CleanupCapture, ...]
    finalization_observations: tuple[_FinalizationCapture, ...]
    current_config: _ConfigCapture | None
    inventory_digest: str | None


def _valid_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _config_projection(
    identity: _ConfigCapture | None,
) -> dict[str, object] | None:
    if identity is None:
        return None
    return {
        "byte_digest": identity.byte_digest,
        "leaf_identity": identity.leaf_identity,
        "metadata_fingerprint": identity.metadata_fingerprint,
        "parent_identity": identity.parent_identity,
        "semantic_digest": identity.semantic_digest,
        "snapshot_digest": identity.snapshot_digest,
        "target_metadata_digest": identity.target_metadata_digest,
    }


def _rollback_capture_projection(action: _RollbackCapture) -> dict[str, object]:
    return {
        "action": action.action,
        "expected_identity": action.expected_identity,
        "relative_path": action.relative_path,
    }


def _rollback_projection(action: RollbackAction) -> dict[str, object]:
    return {
        "action": action.action,
        "expected_identity": action.expected_identity,
        "relative_path": action.relative_path,
    }


def _journal_projection(journal: _JournalCapture) -> dict[str, object]:
    return {
        "before_config": _config_projection(journal.before_config),
        "candidate_config": _config_projection(journal.candidate_config),
        "committed_config": _config_projection(journal.committed_config),
        "evidence_digest": journal.evidence_digest,
        "access_digest": journal.access_digest,
        "head_digest": journal.head_digest,
        "head_sequence": journal.head_sequence,
        "head_state": journal.head_state,
        "journal_location": journal.journal_location,
        "journal_relative": journal.journal_relative,
        "journal_plan_digest": journal.plan_digest,
        "recovery_candidate_identity": journal.recovery_candidate_identity,
        "record_digests": journal.record_digests,
        "rollback_actions": tuple(
            _rollback_capture_projection(action) for action in journal.rollback_actions
        ),
        "next_action_index": journal.next_action_index,
        "transaction_id": journal.transaction_id,
    }


def _cleanup_projection(cleanup: _CleanupCapture) -> dict[str, object]:
    return {
        "authorization_digest": cleanup.authorization_digest,
        "current_reference": cleanup.current_reference,
        "delete_component": cleanup.delete_component,
        "journal_access_digest": cleanup.journal_access_digest,
        "journal_evidence_digest": cleanup.journal_evidence_digest,
        "journal_head_record_digest": cleanup.journal_head_record_digest,
        "journal_head_sequence": cleanup.journal_head_sequence,
        "journal_location": cleanup.location,
        "journal_relative": cleanup.journal_relative,
        "observation_digest": cleanup.observation_digest,
        "phase": cleanup.phase,
        "transaction_binding_digest": cleanup.transaction_binding_digest,
        "transaction_id": cleanup.transaction_id,
    }


def _finalization_projection(
    finalization: _FinalizationCapture,
) -> dict[str, object]:
    return {
        "journal_access_digest": finalization.journal_access_digest,
        "journal_evidence_digest": finalization.journal_evidence_digest,
        "journal_head_record_digest": finalization.journal_head_record_digest,
        "journal_head_sequence": finalization.journal_head_sequence,
        "journal_state": finalization.journal_state,
        "observation_digest": finalization.observation_digest,
        "receipt_status": finalization.receipt_status,
        "transaction_id": finalization.transaction_id,
    }


def _snapshot_projection(capture: _SnapshotCapture) -> dict[str, object]:
    return {
        "cleanup_observation_digests": tuple(
            hashlib.sha256(
                canonical_json_bytes(_cleanup_projection(cleanup))
            ).hexdigest()
            for cleanup in capture.cleanup_observations
        ),
        "finalization_observation_digests": tuple(
            hashlib.sha256(
                canonical_json_bytes(_finalization_projection(finalization))
            ).hexdigest()
            for finalization in capture.finalization_observations
        ),
        "current_config": _config_projection(capture.current_config),
        "inventory_digest": capture.inventory_digest,
        "journal_state_machine_version": JOURNAL_STATE_MACHINE_VERSION,
        "journal_digests": tuple(
            hashlib.sha256(
                canonical_json_bytes(_journal_projection(journal))
            ).hexdigest()
            for journal in capture.journals
        ),
        "recovery_policy_version": RECOVERY_POLICY_VERSION,
    }


def _snapshot_digest(capture: _SnapshotCapture) -> str:
    return hashlib.sha256(
        canonical_json_bytes(_snapshot_projection(capture))
    ).hexdigest()


def _capture_config(
    identity: JournalConfigIdentity,
) -> _ConfigCapture:
    return _ConfigCapture(
        parent_identity=identity.parent_identity,
        leaf_identity=identity.leaf_identity,
        byte_digest=identity.byte_digest,
        semantic_digest=identity.semantic_digest,
        metadata_fingerprint=identity.metadata_fingerprint,
        snapshot_digest=identity.snapshot_digest,
        target_metadata_digest=identity.target_metadata_digest,
    )


def _capture_optional_config(
    identity: JournalConfigIdentity | None,
) -> _ConfigCapture | None:
    return None if identity is None else _capture_config(identity)


def _capture_optional_identity(
    value: object,
) -> tuple[int, int] | None:
    if value is None:
        return None
    if not _valid_optional_identity(value):
        raise ValueError("recovery file identity")
    return cast(tuple[int, int], value)


def _valid_optional_identity(value: object) -> bool:
    return value is None or (
        type(value) is tuple
        and len(value) == 2
        and all(type(component) is int and component >= 0 for component in value)
    )


def _capture_journal(
    journal: LoadedJournal,
    observation: object | None,
) -> _JournalCapture:
    if observation is None:
        journal_location = None
        journal_relative = None
    else:
        from . import ownership as _ownership

        if (
            type(observation) is not _ownership.PendingTransactionObservation
            or observation._seal
            is not _ownership._PENDING_TRANSACTION_OBSERVATION_TOKEN
        ):
            raise ValueError("pending transaction observation")
        journal_location = observation.location.value
        journal_relative = observation.journal_relative
    record = journal.records[-1]
    prepared = record.prepared
    recovery_candidate_identity = (
        None
        if record.config_recovery is None
        else _capture_optional_identity(
            record.config_recovery.get("candidate_identity")
        )
    )
    return _JournalCapture(
        transaction_id=record.transaction_id,
        plan_digest=record.plan_digest,
        head_digest=record.record_digest,
        head_sequence=record.sequence,
        head_state=record.state.value,
        access_digest=journal.access_digest,
        evidence_digest=journal._binding_digest,
        journal_location=journal_location,
        journal_relative=journal_relative,
        record_digests=tuple(item.record_digest for item in journal.records),
        before_config=_capture_config(prepared.before_config),
        candidate_config=_capture_config(prepared.candidate_config),
        committed_config=_capture_optional_config(record.committed_config),
        recovery_candidate_identity=recovery_candidate_identity,
        rollback_actions=tuple(
            _RollbackCapture(
                action=action.action,
                relative_path=action.relative_path,
                expected_identity=action.expected_identity,
            )
            for action in prepared.rollback_actions
        ),
        next_action_index=_next_rollback_action_index(journal),
    )


def _capture_cleanup_observation(
    observation: object,
) -> _CleanupCapture:
    from . import ownership as _ownership

    if (
        type(observation) is not _ownership.RecoveryCleanupObservation
        or observation._seal is not _ownership._RECOVERY_CLEANUP_OBSERVATION_TOKEN
    ):
        raise ValueError("recovery cleanup observation")
    observation._require_valid()
    authorization = observation.authorization
    authorization._require_valid()
    captured = _CleanupCapture(
        transaction_id=authorization.transaction_id,
        phase=observation.phase,
        current_reference=observation.current_reference,
        observation_digest=observation.observation_digest,
        location=authorization.location.value,
        journal_relative=authorization.journal_relative,
        journal_access_digest=authorization.journal_access_digest,
        journal_evidence_digest=authorization.journal_evidence_digest,
        journal_head_sequence=authorization.journal_head_sequence,
        journal_head_record_digest=authorization.journal_head_record_digest,
        transaction_binding_digest=authorization.transaction_binding_digest,
        delete_component=authorization.delete_component,
        authorization_digest=authorization.authorization_digest,
    )
    authorization._require_valid()
    observation._require_valid()
    return captured


def _capture_finalization_observation(
    observation: object,
) -> _FinalizationCapture:
    from . import ownership as _ownership

    if (
        type(observation) is not _ownership.RecoveryFinalizationObservation
        or observation._seal is not _ownership._RECOVERY_FINALIZATION_OBSERVATION_TOKEN
    ):
        raise ValueError("recovery finalization observation")
    observation._require_valid()
    evidence = observation._capture
    captured = _FinalizationCapture(
        transaction_id=evidence.transaction_id,
        journal_state=evidence.journal_state,
        receipt_status=evidence.receipt_status,
        observation_digest=observation.observation_digest,
        journal_access_digest=evidence.journal_access_digest,
        journal_evidence_digest=evidence.journal_evidence_digest,
        journal_head_sequence=evidence.journal_head_sequence,
        journal_head_record_digest=evidence.journal_head_record_digest,
    )
    observation._require_valid()
    if evidence != observation._capture:
        raise ValueError("recovery finalization observation")
    return captured


def _next_rollback_action_index(journal: LoadedJournal) -> int | None:
    head = journal.records[-1]
    if head.state.value in _PRECOMMIT_ROLLBACK_STATES:
        return 0
    if head.state is JournalState.ROLLBACK_ACTION_INTENT:
        if head.rollback_event is None:
            raise ValueError("rollback intent evidence")
        return head.rollback_event.action_index
    if head.state is JournalState.ROLLBACK_ACTION_COMPLETED:
        if head.rollback_event is None:
            raise ValueError("rollback completion evidence")
        return head.rollback_event.action_index + 1
    return None


def _capture_snapshot(
    journals: tuple[LoadedJournal, ...],
    cleanup_observations: tuple[object, ...] | None,
    finalization_observations: tuple[object, ...] | None,
    current_config: JournalConfigIdentity | None,
    inventory_digest: str | None,
    observations: tuple[object, ...] | None,
) -> _SnapshotCapture:
    if observations is not None and len(observations) != len(journals):
        raise ValueError("pending transaction observations")
    return _SnapshotCapture(
        journals=tuple(
            _capture_journal(
                journal,
                None if observations is None else observations[index],
            )
            for index, journal in enumerate(journals)
        ),
        cleanup_observations=(
            ()
            if cleanup_observations is None
            else tuple(
                _capture_cleanup_observation(observation)
                for observation in cleanup_observations
            )
        ),
        finalization_observations=(
            ()
            if finalization_observations is None
            else tuple(
                _capture_finalization_observation(observation)
                for observation in finalization_observations
            )
        ),
        current_config=_capture_optional_config(current_config),
        inventory_digest=inventory_digest,
    )


def _valid_config_capture(value: object) -> bool:
    if type(value) is not _ConfigCapture:
        return False
    try:
        JournalConfigIdentity(
            parent_identity=value.parent_identity,
            leaf_identity=value.leaf_identity,
            byte_digest=value.byte_digest,
            semantic_digest=value.semantic_digest,
            metadata_fingerprint=value.metadata_fingerprint,
            snapshot_digest=value.snapshot_digest,
            target_metadata_digest=value.target_metadata_digest,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def _valid_rollback_captures(value: object) -> bool:
    if (
        type(value) is not tuple
        or not value
        or any(type(action) is not _RollbackCapture for action in value)
        or any(
            type(action.action) is not str
            or type(action.relative_path) is not str
            or (
                action.expected_identity is not None
                and (
                    type(action.expected_identity) is not tuple
                    or len(action.expected_identity) != 2
                    or any(
                        type(item) is not int or item < 0
                        for item in action.expected_identity
                    )
                )
            )
            for action in value
        )
    ):
        return False
    try:
        actions = tuple(
            RollbackAction(
                action=action.action,
                relative_path=action.relative_path,
                expected_identity=action.expected_identity,
            )
            for action in value
        )
    except (AttributeError, TypeError, ValueError):
        return False
    collision_keys = tuple(
        _portable_collision_key(action.relative_path) for action in actions
    )
    return len(collision_keys) == len(set(collision_keys))


def _captured_root_rollback_is_exact(
    actions: tuple[_RollbackCapture, ...],
    *,
    transaction_id: str,
) -> bool:
    reference = f".zagrosi/transactions/{transaction_id}"
    roots = tuple(action for action in actions if action.relative_path == reference)
    return (
        len(roots) == 1
        and roots[0].action == "quarantine-if-owned"
        and roots[0].expected_identity is not None
        and actions[-1] == roots[0]
    )


def _planned_root_rollback_is_exact(
    actions: tuple[RollbackAction, ...],
    *,
    transaction_id: str,
) -> bool:
    reference = f".zagrosi/transactions/{transaction_id}"
    roots = tuple(action for action in actions if action.relative_path == reference)
    return (
        len(roots) == 1
        and roots[0].action == "quarantine-if-owned"
        and roots[0].expected_identity is not None
        and actions[-1] == roots[0]
    )


def _portable_collision_key(value: str) -> str:
    return "/".join(
        unicodedata.normalize("NFKC", component).casefold()
        for component in value.split("/")
    )


def _valid_journal_capture(value: object) -> bool:
    if not (
        type(value) is _JournalCapture
        and type(value.transaction_id) is str
        and _TRANSACTION_ID.fullmatch(value.transaction_id) is not None
        and _valid_digest(value.plan_digest)
        and _valid_digest(value.head_digest)
        and type(value.head_sequence) is int
        and value.head_sequence >= 0
        and type(value.head_state) is str
        and value.head_state in {state.value for state in JournalState}
        and _valid_digest(value.access_digest)
        and _valid_digest(value.evidence_digest)
        and (
            (value.journal_location is None and value.journal_relative is None)
            or (
                value.journal_location in {"live", "quarantined"}
                and type(value.journal_relative) is str
                and bool(value.journal_relative)
            )
        )
        and type(value.record_digests) is tuple
        and bool(value.record_digests)
        and all(_valid_digest(digest) for digest in value.record_digests)
        and value.record_digests[-1] == value.head_digest
        and _valid_config_capture(value.before_config)
        and _valid_config_capture(value.candidate_config)
        and (
            value.committed_config is None
            or _valid_config_capture(value.committed_config)
        )
        and _valid_optional_identity(value.recovery_candidate_identity)
        and value.before_config.target_metadata_digest is None
        and value.candidate_config.target_metadata_digest is not None
        and _valid_rollback_captures(value.rollback_actions)
        and _captured_root_rollback_is_exact(
            value.rollback_actions,
            transaction_id=value.transaction_id,
        )
    ):
        return False
    committed_states = {
        JournalState.CONFIG_COMMITTED.value,
        JournalState.RECEIPT_COMMITTED.value,
        JournalState.FINALIZED.value,
    }
    if value.head_state != JournalState.RECOVERY_REQUIRED.value and (
        value.committed_config is not None
    ) != (value.head_state in committed_states):
        return False
    states_requiring_config_recovery = {
        JournalState.STAGED.value,
        JournalState.VERIFIED.value,
        JournalState.SOURCE_PUBLISHED.value,
        JournalState.CACHE_PUBLISHED.value,
        JournalState.PUBLISHED.value,
        JournalState.COMMIT_INTENT.value,
        JournalState.CONFIG_COMMITTED.value,
        JournalState.RECEIPT_COMMITTED.value,
        JournalState.FINALIZED.value,
    }
    if (
        value.head_state in states_requiring_config_recovery
        and value.recovery_candidate_identity is None
    ):
        return False
    if value.head_state in _ROLLBACK_STATES:
        if type(value.next_action_index) is not int:
            return False
        if value.head_state in _PRECOMMIT_ROLLBACK_STATES:
            return value.next_action_index == 0
        if value.head_state == JournalState.ROLLBACK_ACTION_INTENT.value:
            return 0 <= value.next_action_index < len(value.rollback_actions)
        return 1 <= value.next_action_index <= len(value.rollback_actions)
    return value.next_action_index is None


def _valid_cleanup_capture(value: object) -> bool:
    return (
        type(value) is _CleanupCapture
        and type(value.transaction_id) is str
        and _TRANSACTION_ID.fullmatch(value.transaction_id) is not None
        and value.phase in {"AUTHORIZED", "FINALIZING", "COMPLETE"}
        and (
            value.current_reference is None
            or (type(value.current_reference) is str and bool(value.current_reference))
        )
        and _valid_digest(value.observation_digest)
        and value.location == "quarantined"
        and type(value.journal_relative) is str
        and bool(value.journal_relative)
        and _valid_digest(value.journal_access_digest)
        and _valid_digest(value.journal_evidence_digest)
        and type(value.journal_head_sequence) is int
        and value.journal_head_sequence >= 0
        and _valid_digest(value.journal_head_record_digest)
        and _valid_digest(value.transaction_binding_digest)
        and type(value.delete_component) is str
        and _DELETE_COMPONENT.fullmatch(value.delete_component) is not None
        and _valid_digest(value.authorization_digest)
    )


def _valid_finalization_capture(value: object) -> bool:
    return (
        type(value) is _FinalizationCapture
        and type(value.transaction_id) is str
        and _TRANSACTION_ID.fullmatch(value.transaction_id) is not None
        and value.journal_state
        in {
            JournalState.COMMIT_INTENT.value,
            JournalState.CONFIG_COMMITTED.value,
            JournalState.RECEIPT_COMMITTED.value,
        }
        and value.receipt_status in {"absent", "matching"}
        and (
            value.journal_state != JournalState.COMMIT_INTENT.value
            or value.receipt_status == "absent"
        )
        and (
            value.journal_state != JournalState.RECEIPT_COMMITTED.value
            or value.receipt_status == "matching"
        )
        and _valid_digest(value.observation_digest)
        and _valid_digest(value.journal_access_digest)
        and _valid_digest(value.journal_evidence_digest)
        and type(value.journal_head_sequence) is int
        and value.journal_head_sequence >= 0
        and _valid_digest(value.journal_head_record_digest)
    )


def _finalization_matches_journal(
    finalization: _FinalizationCapture,
    journal: _JournalCapture,
) -> bool:
    return (
        finalization.transaction_id == journal.transaction_id
        and finalization.journal_state == journal.head_state
        and finalization.journal_access_digest == journal.access_digest
        and finalization.journal_evidence_digest == journal.evidence_digest
        and finalization.journal_head_sequence == journal.head_sequence
        and finalization.journal_head_record_digest == journal.head_digest
        and journal.journal_location == "live"
    )


def _valid_snapshot_capture(value: object) -> bool:
    if (
        type(value) is not _SnapshotCapture
        or type(value.journals) is not tuple
        or type(value.cleanup_observations) is not tuple
        or type(value.finalization_observations) is not tuple
        or len(value.journals) + len(value.cleanup_observations)
        > LIMIT_POLICY.value("journal_records")
        or any(not _valid_journal_capture(journal) for journal in value.journals)
        or any(
            not _valid_cleanup_capture(cleanup)
            for cleanup in value.cleanup_observations
        )
        or any(
            not _valid_finalization_capture(finalization)
            for finalization in value.finalization_observations
        )
        or (
            value.current_config is not None
            and not _valid_config_capture(value.current_config)
        )
        or (
            value.inventory_digest is not None
            and not _valid_digest(value.inventory_digest)
        )
    ):
        return False
    transaction_ids = tuple(journal.transaction_id for journal in value.journals)
    cleanup_ids = tuple(
        cleanup.transaction_id for cleanup in value.cleanup_observations
    )
    finalization_ids = tuple(
        finalization.transaction_id for finalization in value.finalization_observations
    )
    captured_locations = tuple(
        journal.journal_location is not None for journal in value.journals
    )
    return (
        transaction_ids == tuple(sorted(transaction_ids))
        and len(set(transaction_ids)) == len(transaction_ids)
        and cleanup_ids == tuple(sorted(cleanup_ids))
        and len(set(cleanup_ids)) == len(cleanup_ids)
        and finalization_ids == tuple(sorted(finalization_ids))
        and len(set(finalization_ids)) == len(finalization_ids)
        and not set(transaction_ids).intersection(cleanup_ids)
        and set(finalization_ids).issubset(transaction_ids)
        and not set(finalization_ids).intersection(cleanup_ids)
        and (
            not value.finalization_observations
            or (
                len(value.journals) == 1
                and len(value.finalization_observations) == 1
                and not value.cleanup_observations
                and value.current_config is not None
                and _finalization_matches_journal(
                    value.finalization_observations[0],
                    value.journals[0],
                )
            )
        )
        and (
            (
                value.inventory_digest is None
                and not any(captured_locations)
                and not value.cleanup_observations
            )
            or (value.inventory_digest is not None and all(captured_locations))
        )
    )


def _stable_snapshot_capture(
    journals: tuple[LoadedJournal, ...],
    cleanup_observations: tuple[object, ...] | None,
    finalization_observations: tuple[object, ...] | None,
    current_config: JournalConfigIdentity | None,
    inventory_digest: str | None,
    observations: tuple[object, ...] | None,
) -> _SnapshotCapture:
    if (
        type(journals) is not tuple
        or (
            len(journals)
            + (0 if cleanup_observations is None else len(cleanup_observations))
            > LIMIT_POLICY.value("journal_records")
        )
        or any(type(journal) is not LoadedJournal for journal in journals)
        or (
            current_config is not None
            and type(current_config) is not JournalConfigIdentity
        )
        or (inventory_digest is not None and not _valid_digest(inventory_digest))
        or (
            observations is not None
            and (type(observations) is not tuple or len(observations) != len(journals))
        )
        or (
            cleanup_observations is not None and type(cleanup_observations) is not tuple
        )
        or (
            finalization_observations is not None
            and type(finalization_observations) is not tuple
        )
        or (
            inventory_digest is None
            and (
                observations is not None
                or cleanup_observations is not None
                or finalization_observations is not None
            )
        )
        or (
            inventory_digest is not None
            and (
                observations is None
                or cleanup_observations is None
                or finalization_observations is None
            )
        )
    ):
        raise TypeError("RecoverySnapshot evidence is invalid")
    try:
        if observations is not None:
            if inventory_digest is None or not hmac.compare_digest(
                _observed_inventory_digest(
                    observations,
                    journals,
                    cleanup_observations or (),
                    finalization_observations or (),
                ),
                inventory_digest,
            ):
                raise ValueError
        for journal in journals:
            journal._require_valid()
        if current_config is not None:
            current_config.__post_init__()
        captured = _capture_snapshot(
            journals,
            cleanup_observations,
            finalization_observations,
            current_config,
            inventory_digest,
            observations,
        )
        for journal in journals:
            journal._require_valid()
        if current_config is not None:
            current_config.__post_init__()
        if observations is not None:
            if inventory_digest is None or not hmac.compare_digest(
                _observed_inventory_digest(
                    observations,
                    journals,
                    cleanup_observations or (),
                    finalization_observations or (),
                ),
                inventory_digest,
            ):
                raise ValueError
        confirmed = _capture_snapshot(
            journals,
            cleanup_observations,
            finalization_observations,
            current_config,
            inventory_digest,
            observations,
        )
        if (
            captured != confirmed
            or not _valid_snapshot_capture(captured)
            or not _valid_snapshot_capture(confirmed)
        ):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise TypeError("RecoverySnapshot evidence is invalid") from None
    return captured


@dataclass(frozen=True, slots=True, init=False)
class RecoverySnapshot:
    """Inert, effect-free evidence presented to the recovery classifier."""

    journals: tuple[LoadedJournal, ...]
    current_config: JournalConfigIdentity | None
    snapshot_digest: str
    _capture: _SnapshotCapture
    _inventory_digest: str | None
    _observations: tuple[object, ...] | None
    _cleanup_observations: tuple[object, ...] | None
    _finalization_observations: tuple[object, ...] | None
    _observation_seal: object | None
    _seal: object

    def __init__(
        self,
        *,
        journals: tuple[LoadedJournal, ...],
        current_config: JournalConfigIdentity | None,
        _inventory_digest: str | None = None,
        _observations: tuple[object, ...] | None = None,
        _cleanup_observations: tuple[object, ...] | None = None,
        _finalization_observations: tuple[object, ...] | None = None,
        _observation_token: object | None = None,
    ) -> None:
        if (
            _inventory_digest is None
            and (
                _observations is not None
                or _cleanup_observations is not None
                or _finalization_observations is not None
                or _observation_token is not None
            )
        ) or (
            _inventory_digest is not None
            and (
                not _valid_digest(_inventory_digest)
                or type(_observations) is not tuple
                or type(_cleanup_observations) is not tuple
                or type(_finalization_observations) is not tuple
                or _observation_token is not _LIVE_OBSERVATION_TOKEN
            )
        ):
            raise TypeError(
                "live recovery snapshots are minted only by recovery observation"
            )
        capture = _stable_snapshot_capture(
            journals,
            _cleanup_observations,
            _finalization_observations,
            current_config,
            _inventory_digest,
            _observations,
        )
        digest = _snapshot_digest(capture)
        object.__setattr__(self, "journals", journals)
        object.__setattr__(self, "current_config", current_config)
        object.__setattr__(self, "snapshot_digest", digest)
        object.__setattr__(self, "_capture", capture)
        object.__setattr__(self, "_inventory_digest", _inventory_digest)
        object.__setattr__(self, "_observations", _observations)
        object.__setattr__(
            self,
            "_cleanup_observations",
            _cleanup_observations,
        )
        object.__setattr__(
            self,
            "_finalization_observations",
            _finalization_observations,
        )
        object.__setattr__(
            self,
            "_observation_seal",
            (_LIVE_OBSERVATION_TOKEN if _inventory_digest is not None else None),
        )
        object.__setattr__(self, "_seal", _SNAPSHOT_TOKEN)

    def _require_valid(self) -> None:
        try:
            capture = _stable_snapshot_capture(
                self.journals,
                self._cleanup_observations,
                self._finalization_observations,
                self.current_config,
                self._inventory_digest,
                self._observations,
            )
            expected = _snapshot_digest(capture)
        except (AttributeError, TypeError, ValueError):
            raise TypeError("RecoverySnapshot authority changed") from None
        if (
            self._seal is not _SNAPSHOT_TOKEN
            or (
                self._inventory_digest is None
                and (
                    self._observations is not None
                    or self._cleanup_observations is not None
                    or self._finalization_observations is not None
                    or self._observation_seal is not None
                )
            )
            or (
                self._inventory_digest is not None
                and (
                    not _valid_digest(self._inventory_digest)
                    or type(self._observations) is not tuple
                    or type(self._cleanup_observations) is not tuple
                    or type(self._finalization_observations) is not tuple
                    or self._observation_seal is not _LIVE_OBSERVATION_TOKEN
                )
            )
            or not _valid_digest(self.snapshot_digest)
            or type(self._capture) is not _SnapshotCapture
            or not _valid_snapshot_capture(self._capture)
            or capture != self._capture
            or not hmac.compare_digest(self.snapshot_digest, expected)
        ):
            raise TypeError("RecoverySnapshot authority changed")

    def __reduce__(self) -> Never:
        raise TypeError("recovery snapshots are not serializable")


def _plan_projection(
    *,
    snapshot_digest: str,
    disposition: RecoveryDisposition,
    transaction_ids: tuple[str, ...],
    rollback_actions: tuple[RollbackAction, ...],
    next_action_index: int | None,
    error_code: str | None,
) -> dict[str, object]:
    return {
        "disposition": disposition.value,
        "error_code": error_code,
        "recovery_policy_version": RECOVERY_POLICY_VERSION,
        "next_action_index": next_action_index,
        "rollback_actions": tuple(
            _rollback_projection(action) for action in rollback_actions
        ),
        "snapshot_digest": snapshot_digest,
        "transaction_ids": transaction_ids,
    }


def _valid_plan_fields(
    *,
    snapshot_digest: object,
    disposition: object,
    transaction_ids: object,
    rollback_actions: object,
    next_action_index: object,
    error_code: object,
) -> bool:
    if (
        not _valid_digest(snapshot_digest)
        or type(disposition) is not RecoveryDisposition
        or type(transaction_ids) is not tuple
        or any(
            type(item) is not str or _TRANSACTION_ID.fullmatch(item) is None
            for item in transaction_ids
        )
        or transaction_ids != tuple(sorted(transaction_ids))
        or len(set(transaction_ids)) != len(transaction_ids)
        or type(rollback_actions) is not tuple
        or any(type(item) is not RollbackAction for item in rollback_actions)
        or any(
            type(item.action) is not str
            or type(item.relative_path) is not str
            or (
                item.expected_identity is not None
                and (
                    type(item.expected_identity) is not tuple
                    or len(item.expected_identity) != 2
                    or any(
                        type(identity_part) is not int or identity_part < 0
                        for identity_part in item.expected_identity
                    )
                )
            )
            for item in rollback_actions
        )
        or (next_action_index is not None and type(next_action_index) is not int)
        or (error_code is not None and type(error_code) is not str)
    ):
        return False
    try:
        for action in rollback_actions:
            action.__post_init__()
    except (AttributeError, TypeError, ValueError):
        return False
    collision_keys = tuple(
        _portable_collision_key(action.relative_path) for action in rollback_actions
    )
    if len(collision_keys) != len(set(collision_keys)):
        return False
    if disposition is RecoveryDisposition.NO_RECOVERY:
        return (
            not transaction_ids
            and not rollback_actions
            and next_action_index is None
            and error_code is None
        )
    if disposition is RecoveryDisposition.ROLLBACK_CANDIDATE:
        return (
            len(transaction_ids) == 1
            and bool(rollback_actions)
            and type(next_action_index) is int
            and 0 <= next_action_index <= len(rollback_actions)
            and _planned_root_rollback_is_exact(
                rollback_actions,
                transaction_id=transaction_ids[0],
            )
            and error_code is None
        )
    if disposition is RecoveryDisposition.FINALIZE_COMMITTED:
        return (
            len(transaction_ids) == 1
            and not rollback_actions
            and next_action_index is None
            and error_code is None
        )
    if disposition is RecoveryDisposition.CLEANUP_PENDING:
        return (
            len(transaction_ids) == 1
            and not rollback_actions
            and next_action_index is None
            and error_code is None
        )
    return (
        disposition is RecoveryDisposition.OPERATOR_CONFLICT
        and bool(transaction_ids)
        and not rollback_actions
        and next_action_index is None
        and error_code == "recovery.operator_conflict"
    )


@dataclass(frozen=True, slots=True, init=False)
class RecoveryPlan:
    """Sealed pure decision; later executors must reload and reproduce its digest."""

    snapshot_digest: str
    plan_digest: str
    disposition: RecoveryDisposition
    transaction_ids: tuple[str, ...]
    rollback_actions: tuple[RollbackAction, ...]
    next_action_index: int | None
    error_code: str | None
    _seal: object

    def __init__(
        self,
        *,
        snapshot_digest: str,
        disposition: RecoveryDisposition,
        transaction_ids: tuple[str, ...],
        rollback_actions: tuple[RollbackAction, ...],
        next_action_index: int | None,
        error_code: str | None,
        _token: object,
    ) -> None:
        if _token is not _PLAN_TOKEN or not _valid_plan_fields(
            snapshot_digest=snapshot_digest,
            disposition=disposition,
            transaction_ids=transaction_ids,
            rollback_actions=rollback_actions,
            next_action_index=next_action_index,
            error_code=error_code,
        ):
            raise TypeError("RecoveryPlan is created only by plan_recovery")
        domain = _plan_projection(
            snapshot_digest=snapshot_digest,
            disposition=disposition,
            transaction_ids=transaction_ids,
            rollback_actions=rollback_actions,
            next_action_index=next_action_index,
            error_code=error_code,
        )
        plan_digest = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        object.__setattr__(self, "snapshot_digest", snapshot_digest)
        object.__setattr__(self, "plan_digest", plan_digest)
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "transaction_ids", transaction_ids)
        object.__setattr__(self, "rollback_actions", rollback_actions)
        object.__setattr__(self, "next_action_index", next_action_index)
        object.__setattr__(self, "error_code", error_code)
        object.__setattr__(self, "_seal", _PLAN_TOKEN)

    def _require_valid(self) -> None:
        try:
            if not _valid_plan_fields(
                snapshot_digest=self.snapshot_digest,
                disposition=self.disposition,
                transaction_ids=self.transaction_ids,
                rollback_actions=self.rollback_actions,
                next_action_index=self.next_action_index,
                error_code=self.error_code,
            ):
                raise TypeError
            domain = _plan_projection(
                snapshot_digest=self.snapshot_digest,
                disposition=self.disposition,
                transaction_ids=self.transaction_ids,
                rollback_actions=self.rollback_actions,
                next_action_index=self.next_action_index,
                error_code=self.error_code,
            )
            expected = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        except (AttributeError, TypeError, ValueError):
            raise TypeError("RecoveryPlan authority changed") from None
        if (
            self._seal is not _PLAN_TOKEN
            or not _valid_digest(self.plan_digest)
            or not hmac.compare_digest(self.plan_digest, expected)
        ):
            raise TypeError("RecoveryPlan authority changed")

    def __eq__(self, other: object) -> bool:
        if type(other) is not RecoveryPlan:
            return False
        self._require_valid()
        other._require_valid()
        return hmac.compare_digest(self.plan_digest, other.plan_digest)

    def __reduce__(self) -> Never:
        raise TypeError("recovery plans are not serializable")


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    """Inert outcome from one consumed locked recovery execution."""

    executed_disposition: RecoveryDisposition
    final_disposition: RecoveryDisposition
    transaction_ids: tuple[str, ...]
    completed_action_count: int
    cleanup_removed: bool
    recovery_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.executed_disposition) is not RecoveryDisposition
            or self.executed_disposition
            not in {
                RecoveryDisposition.ROLLBACK_CANDIDATE,
                RecoveryDisposition.CLEANUP_PENDING,
            }
            or self.final_disposition is not RecoveryDisposition.NO_RECOVERY
            or type(self.transaction_ids) is not tuple
            or len(self.transaction_ids) != 1
            or _TRANSACTION_ID.fullmatch(self.transaction_ids[0]) is None
            or type(self.completed_action_count) is not int
            or self.completed_action_count < 0
            or (
                self.executed_disposition is RecoveryDisposition.CLEANUP_PENDING
                and self.completed_action_count != 0
            )
            or self.cleanup_removed is not True
            or type(self.recovery_references) is not tuple
            or len(self.recovery_references) != 1
            or type(self.recovery_references[0]) is not str
            or not self.recovery_references[0]
        ):
            raise ValueError("recovery result")


def _close_all_recovery_resources(
    *callbacks: Callable[[], None] | None,
) -> None:
    """Attempt every retained close, then propagate the first close failure."""

    first_failure: BaseException | None = None
    for callback in callbacks:
        if callback is None:
            continue
        try:
            callback()
        except BaseException as exc:
            if first_failure is None:
                first_failure = exc
    if first_failure is not None:
        raise first_failure


def _journal_disposition(
    journal: _JournalCapture,
    current_config: _ConfigCapture | None,
) -> RecoveryDisposition:
    if journal.head_state == JournalState.ROLLED_BACK.value:
        if journal.journal_location == "quarantined":
            return RecoveryDisposition.CLEANUP_PENDING
        return RecoveryDisposition.OPERATOR_CONFLICT
    if journal.head_state in _NO_RECOVERY_STATES:
        return RecoveryDisposition.NO_RECOVERY
    if journal.head_state == JournalState.RECOVERY_REQUIRED.value:
        return RecoveryDisposition.OPERATOR_CONFLICT
    if (
        journal.head_state in _ROLLBACK_STATES
        and current_config == journal.before_config
    ):
        return RecoveryDisposition.ROLLBACK_CANDIDATE
    return RecoveryDisposition.OPERATOR_CONFLICT


def _current_config_is_committed_candidate(
    journal: _JournalCapture,
    current_config: _ConfigCapture | None,
) -> bool:
    if current_config is None:
        return False
    if journal.head_state == JournalState.COMMIT_INTENT.value:
        candidate = journal.candidate_config
        return (
            journal.recovery_candidate_identity is not None
            and current_config.parent_identity == candidate.parent_identity
            and current_config.leaf_identity == journal.recovery_candidate_identity
            and current_config.byte_digest == candidate.byte_digest
            and current_config.semantic_digest == candidate.semantic_digest
            and current_config.target_metadata_digest
            == candidate.target_metadata_digest
        )
    return (
        journal.head_state
        in {
            JournalState.CONFIG_COMMITTED.value,
            JournalState.RECEIPT_COMMITTED.value,
        }
        and journal.committed_config is not None
        and current_config == journal.committed_config
    )


def _journal_disposition_with_finalization(
    journal: _JournalCapture,
    current_config: _ConfigCapture | None,
    finalization: _FinalizationCapture | None,
) -> RecoveryDisposition:
    baseline = _journal_disposition(journal, current_config)
    if (
        baseline is RecoveryDisposition.OPERATOR_CONFLICT
        and finalization is not None
        and _finalization_matches_journal(finalization, journal)
        and _current_config_is_committed_candidate(journal, current_config)
    ):
        return RecoveryDisposition.FINALIZE_COMMITTED
    return baseline


def _rollback_action(capture: _RollbackCapture) -> RollbackAction:
    return RollbackAction(
        action=capture.action,
        relative_path=capture.relative_path,
        expected_identity=capture.expected_identity,
    )


def _make_plan(
    snapshot: RecoverySnapshot,
    capture: _SnapshotCapture,
    capture_digest: str,
    *,
    disposition: RecoveryDisposition,
    transaction_ids: tuple[str, ...],
    rollback_actions: tuple[RollbackAction, ...],
    next_action_index: int | None,
    error_code: str | None,
) -> RecoveryPlan:
    snapshot._require_valid()
    if (
        capture != snapshot._capture
        or not hmac.compare_digest(capture_digest, snapshot.snapshot_digest)
        or not hmac.compare_digest(capture_digest, _snapshot_digest(capture))
    ):
        raise TypeError("RecoverySnapshot authority changed")
    plan = RecoveryPlan(
        snapshot_digest=capture_digest,
        disposition=disposition,
        transaction_ids=transaction_ids,
        rollback_actions=rollback_actions,
        next_action_index=next_action_index,
        error_code=error_code,
        _token=_PLAN_TOKEN,
    )
    snapshot._require_valid()
    if capture != snapshot._capture or not hmac.compare_digest(
        capture_digest, snapshot.snapshot_digest
    ):
        raise TypeError("RecoverySnapshot authority changed")
    plan._require_valid()
    return plan


def plan_recovery(snapshot: RecoverySnapshot) -> RecoveryPlan:
    """Classify safe rollback only; finalization requires live external evidence."""

    if type(snapshot) is not RecoverySnapshot:
        raise TypeError("plan_recovery requires RecoverySnapshot")
    snapshot._require_valid()
    capture = snapshot._capture
    capture_digest = _snapshot_digest(capture)
    snapshot._require_valid()
    if not capture.journals and not capture.cleanup_observations:
        return _make_plan(
            snapshot,
            capture,
            capture_digest,
            disposition=RecoveryDisposition.NO_RECOVERY,
            transaction_ids=(),
            rollback_actions=(),
            next_action_index=None,
            error_code=None,
        )

    finalization_by_transaction = {
        finalization.transaction_id: finalization
        for finalization in capture.finalization_observations
    }
    journal_decisions = tuple(
        _journal_disposition_with_finalization(
            journal,
            capture.current_config,
            finalization_by_transaction.get(journal.transaction_id),
        )
        for journal in capture.journals
    )
    journal_transaction_ids = tuple(
        journal.transaction_id for journal in capture.journals
    )
    cleanup_transaction_ids = tuple(
        cleanup.transaction_id for cleanup in capture.cleanup_observations
    )
    all_transaction_ids = tuple(
        sorted((*journal_transaction_ids, *cleanup_transaction_ids))
    )
    if capture.cleanup_observations:
        if not capture.journals and len(capture.cleanup_observations) == 1:
            return _make_plan(
                snapshot,
                capture,
                capture_digest,
                disposition=RecoveryDisposition.CLEANUP_PENDING,
                transaction_ids=cleanup_transaction_ids,
                rollback_actions=(),
                next_action_index=None,
                error_code=None,
            )
        return _make_plan(
            snapshot,
            capture,
            capture_digest,
            disposition=RecoveryDisposition.OPERATOR_CONFLICT,
            transaction_ids=all_transaction_ids,
            rollback_actions=(),
            next_action_index=None,
            error_code="recovery.operator_conflict",
        )
    if all(
        disposition is RecoveryDisposition.NO_RECOVERY
        for disposition in journal_decisions
    ):
        return _make_plan(
            snapshot,
            capture,
            capture_digest,
            disposition=RecoveryDisposition.NO_RECOVERY,
            transaction_ids=(),
            rollback_actions=(),
            next_action_index=None,
            error_code=None,
        )
    if len(capture.journals) != 1:
        return _make_plan(
            snapshot,
            capture,
            capture_digest,
            disposition=RecoveryDisposition.OPERATOR_CONFLICT,
            transaction_ids=all_transaction_ids,
            rollback_actions=(),
            next_action_index=None,
            error_code="recovery.operator_conflict",
        )
    journal = capture.journals[0]
    if journal_decisions[0] is RecoveryDisposition.ROLLBACK_CANDIDATE:
        return _make_plan(
            snapshot,
            capture,
            capture_digest,
            disposition=RecoveryDisposition.ROLLBACK_CANDIDATE,
            transaction_ids=(journal.transaction_id,),
            rollback_actions=tuple(
                _rollback_action(action) for action in journal.rollback_actions
            ),
            next_action_index=journal.next_action_index,
            error_code=None,
        )
    if journal_decisions[0] is RecoveryDisposition.FINALIZE_COMMITTED:
        return _make_plan(
            snapshot,
            capture,
            capture_digest,
            disposition=RecoveryDisposition.FINALIZE_COMMITTED,
            transaction_ids=(journal.transaction_id,),
            rollback_actions=(),
            next_action_index=None,
            error_code=None,
        )
    if journal_decisions[0] is RecoveryDisposition.CLEANUP_PENDING:
        return _make_plan(
            snapshot,
            capture,
            capture_digest,
            disposition=RecoveryDisposition.CLEANUP_PENDING,
            transaction_ids=(journal.transaction_id,),
            rollback_actions=(),
            next_action_index=None,
            error_code=None,
        )
    return _make_plan(
        snapshot,
        capture,
        capture_digest,
        disposition=RecoveryDisposition.OPERATOR_CONFLICT,
        transaction_ids=(journal.transaction_id,),
        rollback_actions=(),
        next_action_index=None,
        error_code="recovery.operator_conflict",
    )


def _plan_changed(
    message: str = "The recovery plan changed before execution.",
) -> ForgeError:
    return ForgeError(
        "transaction.plan_changed",
        14,
        message,
    )


def _matching_path_authority(
    authority: object,
    owned_root: object,
) -> bool:
    return (
        type(authority) is PlatformPathAuthority
        and isinstance(owned_root, OwnedRoot)
        and authority._origin is owned_root._origin
    )


def _discover_inventory(owned_root: OwnedRoot) -> tuple[object, ...]:
    from .ownership import discover_pending_transactions

    discovered = discover_pending_transactions(owned_root)
    if not discovered.is_ok:
        raise _plan_changed("The pending recovery inventory changed.")
    return discovered.unwrap()


def _discover_cleanup_inventory(owned_root: OwnedRoot) -> tuple[object, ...]:
    from .ownership import discover_recovery_cleanup_observations

    discovered = discover_recovery_cleanup_observations(owned_root)
    if not discovered.is_ok:
        raise _plan_changed("The pending cleanup inventory changed.")
    return discovered.unwrap()


def _observed_inventory_digest(
    observations: tuple[object, ...],
    journals: tuple[LoadedJournal, ...],
    cleanup_observations: tuple[object, ...] = (),
    finalization_observations: tuple[object, ...] = (),
) -> str:
    from . import ownership as _ownership

    if (
        type(observations) is not tuple
        or type(journals) is not tuple
        or type(cleanup_observations) is not tuple
        or type(finalization_observations) is not tuple
        or len(observations) != len(journals)
        or len(journals) + len(cleanup_observations)
        > LIMIT_POLICY.value("journal_records")
    ):
        raise TypeError("recovery inventory evidence is invalid")
    journal_ids: list[str] = []
    try:
        for journal in journals:
            if type(journal) is not LoadedJournal:
                raise ValueError
            journal._require_valid()
            journal_ids.append(journal.records[-1].transaction_id)
        observation_digests: list[str] = []
        observation_ids: list[str] = []
        for observation in observations:
            if (
                type(observation) is not _ownership.PendingTransactionObservation
                or observation._seal
                is not _ownership._PENDING_TRANSACTION_OBSERVATION_TOKEN
                or not _ownership._persistent_binding_invariants(observation.binding)
                or type(observation.location) is not _ownership.TransactionLocation
                or type(observation.journal_relative) is not str
            ):
                raise ValueError
            binding = observation.binding
            transaction_id = binding.transaction_id
            location = observation.location
            journal_relative = observation.journal_relative
            valid_relative = (
                location is _ownership.TransactionLocation.LIVE
                and journal_relative == binding.root_relative
            ) or (
                location is _ownership.TransactionLocation.QUARANTINED
                and _ownership._persistent_cleanup_reference_is_valid(
                    binding,
                    journal_relative,
                )
            )
            binding_projection = binding.canonical_projection()
            if (
                not valid_relative
                or not _ownership._persistent_binding_invariants(binding)
                or binding_projection != binding.canonical_projection()
                or transaction_id != binding.transaction_id
                or location is not observation.location
                or journal_relative != observation.journal_relative
            ):
                raise ValueError
            binding_digest = hashlib.sha256(
                canonical_json_bytes(binding_projection)
            ).hexdigest()
            observation_digest = hashlib.sha256(
                canonical_json_bytes(
                    {
                        "binding_digest": binding_digest,
                        "journal_relative": journal_relative,
                        "location": location.value,
                        "transaction_id": transaction_id,
                    }
                )
            ).hexdigest()
            journal = journals[len(observation_digests)]
            if not hmac.compare_digest(
                binding_digest,
                journal.head.transaction_binding_digest,
            ) or not hmac.compare_digest(
                observation_digest,
                journal.access_digest,
            ):
                raise ValueError
            observation_digests.append(observation_digest)
            observation_ids.append(transaction_id)
        if (
            tuple(observation_ids) != tuple(sorted(observation_ids))
            or len(set(observation_ids)) != len(observation_ids)
            or tuple(observation_ids) != tuple(journal_ids)
        ):
            raise ValueError
        cleanup_captures = tuple(
            _capture_cleanup_observation(observation)
            for observation in cleanup_observations
        )
        finalization_captures = tuple(
            _capture_finalization_observation(observation)
            for observation in finalization_observations
        )
        cleanup_ids = tuple(cleanup.transaction_id for cleanup in cleanup_captures)
        finalization_ids = tuple(
            finalization.transaction_id for finalization in finalization_captures
        )
        if (
            cleanup_ids != tuple(sorted(cleanup_ids))
            or len(set(cleanup_ids)) != len(cleanup_ids)
            or set(cleanup_ids).intersection(observation_ids)
            or finalization_ids != tuple(sorted(finalization_ids))
            or len(set(finalization_ids)) != len(finalization_ids)
            or not set(finalization_ids).issubset(observation_ids)
            or set(finalization_ids).intersection(cleanup_ids)
            or (
                bool(finalization_captures)
                and (
                    len(finalization_captures) != 1
                    or len(journals) != 1
                    or bool(cleanup_captures)
                    or not _finalization_matches_journal(
                        finalization_captures[0],
                        _capture_journal(journals[0], observations[0]),
                    )
                )
            )
        ):
            raise ValueError
        for journal in journals:
            journal._require_valid()
    except (AttributeError, ForgeError, TypeError, ValueError):
        raise TypeError("recovery inventory evidence is invalid") from None
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "cleanup_observation_digests": tuple(
                    hashlib.sha256(
                        canonical_json_bytes(_cleanup_projection(cleanup))
                    ).hexdigest()
                    for cleanup in cleanup_captures
                ),
                "finalization_observation_digests": tuple(
                    hashlib.sha256(
                        canonical_json_bytes(_finalization_projection(finalization))
                    ).hexdigest()
                    for finalization in finalization_captures
                ),
                "observation_digests": tuple(observation_digests),
            }
        )
    ).hexdigest()


def _observe_current_config_once(
    *,
    authority: PlatformPathAuthority,
    owned_root: OwnedRoot,
) -> JournalConfigIdentity:
    if not _matching_path_authority(authority, owned_root):
        raise TypeError("current config observation requires matching path authority")
    proof = authority.prove_config_path(owned_root).unwrap()
    try:
        snapshot = snapshot_config(proof).unwrap()
        snapshot._require_valid()
        observed = JournalConfigIdentity(
            parent_identity=snapshot.parent_identity,
            leaf_identity=snapshot.leaf_identity,
            byte_digest=snapshot.byte_digest,
            semantic_digest=snapshot.semantic_digest,
            metadata_fingerprint=snapshot.metadata_fingerprint,
            snapshot_digest=snapshot.snapshot_digest,
            target_metadata_digest=None,
        )
        snapshot._require_valid()
        return observed
    finally:
        proof.close()


def observe_current_config_identity(
    *,
    authority: PlatformPathAuthority,
    owned_root: OwnedRoot,
) -> JournalConfigIdentity:
    """Twice observe the live config as rollback-only journal identity evidence."""

    first = _observe_current_config_once(
        authority=authority,
        owned_root=owned_root,
    )
    second = _observe_current_config_once(
        authority=authority,
        owned_root=owned_root,
    )
    if first != second:
        raise _plan_changed("The current config changed while it was observed.")
    return second


def _eligible_finalization_pair(
    observations: tuple[object, ...],
    journals: tuple[LoadedJournal, ...],
    cleanup_observations: tuple[object, ...],
) -> tuple[PendingTransactionObservation, LoadedJournal] | None:
    from . import ownership as _ownership

    if len(observations) != 1 or len(journals) != 1 or cleanup_observations:
        return None
    observation = observations[0]
    journal = journals[0]
    if (
        type(observation) is not _ownership.PendingTransactionObservation
        or observation._seal is not _ownership._PENDING_TRANSACTION_OBSERVATION_TOKEN
        or observation.location is not _ownership.TransactionLocation.LIVE
        or journal.records[-1].state
        not in {
            JournalState.COMMIT_INTENT,
            JournalState.CONFIG_COMMITTED,
            JournalState.RECEIPT_COMMITTED,
        }
    ):
        return None
    return observation, journal


def _journal_identity_is_committed_candidate(
    journal: LoadedJournal,
    current: JournalConfigIdentity | None,
) -> bool:
    if current is None:
        return False
    captured = _capture_journal(journal, None)
    return _current_config_is_committed_candidate(
        captured,
        _capture_config(current),
    )


def _observe_recovery_config_once(
    *,
    authority: PlatformPathAuthority,
    owned_root: OwnedRoot,
    journal: LoadedJournal,
) -> JournalConfigIdentity | None:
    from . import atomic_file

    proof = authority.prove_config_path(owned_root).unwrap()
    reopened: atomic_file.ReopenedConfigRecovery | None = None
    try:
        snapshotted = snapshot_config(proof)
        if not snapshotted.is_ok:
            return None
        snapshot = snapshotted.unwrap()
        snapshot._require_valid()
        base = JournalConfigIdentity(
            parent_identity=snapshot.parent_identity,
            leaf_identity=snapshot.leaf_identity,
            byte_digest=snapshot.byte_digest,
            semantic_digest=snapshot.semantic_digest,
            metadata_fingerprint=snapshot.metadata_fingerprint,
            snapshot_digest=snapshot.snapshot_digest,
            target_metadata_digest=None,
        )
        head = journal.records[-1]
        if head.config_recovery is None:
            return base
        try:
            descriptor = atomic_file.decode_config_recovery_descriptor(
                json.loads(canonical_json_bytes(head.config_recovery))
            ).unwrap()
            reopened = atomic_file.reopen_config_recovery(
                proof,
                descriptor,
            ).unwrap()
            relation = atomic_file.classify_reopened_config_recovery(reopened).unwrap()
        except (ForgeError, TypeError, ValueError):
            return base
        if relation is not atomic_file.ConfigCommitState.CANDIDATE:
            return base
        candidate = JournalConfigIdentity(
            parent_identity=snapshot.parent_identity,
            leaf_identity=snapshot.leaf_identity,
            byte_digest=snapshot.byte_digest,
            semantic_digest=snapshot.semantic_digest,
            metadata_fingerprint=snapshot.metadata_fingerprint,
            snapshot_digest=snapshot.snapshot_digest,
            target_metadata_digest=descriptor.target_metadata_digest,
        )
        return (
            candidate
            if _journal_identity_is_committed_candidate(journal, candidate)
            else base
        )
    finally:
        _close_all_recovery_resources(
            None if reopened is None else reopened.close,
            proof.close,
        )


def _observe_inventory_current_config(
    *,
    authority: PlatformPathAuthority,
    owned_root: OwnedRoot,
    observations: tuple[object, ...],
    journals: tuple[LoadedJournal, ...],
    cleanup_observations: tuple[object, ...],
) -> JournalConfigIdentity | None:
    pair = _eligible_finalization_pair(
        observations,
        journals,
        cleanup_observations,
    )
    if pair is None:
        return observe_current_config_identity(
            authority=authority,
            owned_root=owned_root,
        )
    first = _observe_recovery_config_once(
        authority=authority,
        owned_root=owned_root,
        journal=pair[1],
    )
    second = _observe_recovery_config_once(
        authority=authority,
        owned_root=owned_root,
        journal=pair[1],
    )
    if first != second:
        raise _plan_changed("The current config changed while it was observed.")
    return second


def _observe_finalization_inventory(
    owned_root: OwnedRoot,
    *,
    observations: tuple[object, ...],
    journals: tuple[LoadedJournal, ...],
    cleanup_observations: tuple[object, ...],
    current_config: JournalConfigIdentity | None,
) -> tuple[RecoveryFinalizationObservation, ...]:
    from . import ownership as _ownership

    pair = _eligible_finalization_pair(
        observations,
        journals,
        cleanup_observations,
    )
    if pair is None or not _journal_identity_is_committed_candidate(
        pair[1],
        current_config,
    ):
        return ()
    observed = _ownership.observe_recovery_finalization(
        owned_root,
        observation=pair[0],
        journal=pair[1],
    )
    if not observed.is_ok:
        return ()
    finalization = observed.unwrap()
    finalization._require_valid()
    return (finalization,)


def observe_recovery_snapshot(
    *,
    authority: PlatformPathAuthority,
    owned_root: OwnedRoot,
) -> RecoverySnapshot:
    """Observe one stable, effect-free recovery snapshot from live authorities."""

    if not _matching_path_authority(authority, owned_root):
        raise TypeError("recovery observation requires matching path authority")
    first_inventory = _discover_inventory(owned_root)
    first_cleanup_inventory = _discover_cleanup_inventory(owned_root)
    first_journals = load_pending(owned_root)
    first_config = _observe_inventory_current_config(
        authority=authority,
        owned_root=owned_root,
        observations=first_inventory,
        journals=first_journals,
        cleanup_observations=first_cleanup_inventory,
    )
    first_finalization = _observe_finalization_inventory(
        owned_root,
        observations=first_inventory,
        journals=first_journals,
        cleanup_observations=first_cleanup_inventory,
        current_config=first_config,
    )
    second_inventory = _discover_inventory(owned_root)
    second_cleanup_inventory = _discover_cleanup_inventory(owned_root)
    second_journals = load_pending(owned_root)
    second_config = _observe_inventory_current_config(
        authority=authority,
        owned_root=owned_root,
        observations=second_inventory,
        journals=second_journals,
        cleanup_observations=second_cleanup_inventory,
    )
    second_finalization = _observe_finalization_inventory(
        owned_root,
        observations=second_inventory,
        journals=second_journals,
        cleanup_observations=second_cleanup_inventory,
        current_config=second_config,
    )
    final_inventory = _discover_inventory(owned_root)
    final_cleanup_inventory = _discover_cleanup_inventory(owned_root)
    final_journals = load_pending(owned_root)
    final_config = _observe_inventory_current_config(
        authority=authority,
        owned_root=owned_root,
        observations=final_inventory,
        journals=final_journals,
        cleanup_observations=final_cleanup_inventory,
    )
    final_finalization = _observe_finalization_inventory(
        owned_root,
        observations=final_inventory,
        journals=final_journals,
        cleanup_observations=final_cleanup_inventory,
        current_config=final_config,
    )
    confirmed_inventory = _discover_inventory(owned_root)
    confirmed_cleanup_inventory = _discover_cleanup_inventory(owned_root)
    confirmed_journals = load_pending(owned_root)
    confirmed_config = _observe_inventory_current_config(
        authority=authority,
        owned_root=owned_root,
        observations=confirmed_inventory,
        journals=confirmed_journals,
        cleanup_observations=confirmed_cleanup_inventory,
    )
    try:
        first_inventory_digest = _observed_inventory_digest(
            first_inventory,
            first_journals,
            first_cleanup_inventory,
            first_finalization,
        )
        second_inventory_digest = _observed_inventory_digest(
            second_inventory,
            second_journals,
            second_cleanup_inventory,
            second_finalization,
        )
        final_inventory_digest = _observed_inventory_digest(
            final_inventory,
            final_journals,
            final_cleanup_inventory,
            final_finalization,
        )
        confirmed_inventory_digest = _observed_inventory_digest(
            confirmed_inventory,
            confirmed_journals,
            confirmed_cleanup_inventory,
            final_finalization,
        )
    except TypeError:
        raise _plan_changed(
            "Recovery inventory changed while it was observed."
        ) from None
    if (
        first_inventory != second_inventory
        or second_inventory != final_inventory
        or first_cleanup_inventory != second_cleanup_inventory
        or second_cleanup_inventory != final_cleanup_inventory
        or final_cleanup_inventory != confirmed_cleanup_inventory
        or first_finalization != second_finalization
        or second_finalization != final_finalization
        or first_inventory_digest != second_inventory_digest
        or second_inventory_digest != final_inventory_digest
        or final_inventory_digest != confirmed_inventory_digest
        or first_journals != second_journals
        or second_journals != final_journals
        or final_journals != confirmed_journals
        or first_config != second_config
        or second_config != final_config
        or final_config != confirmed_config
        or final_inventory != confirmed_inventory
    ):
        raise _plan_changed("Recovery evidence changed while it was observed.")
    return RecoverySnapshot(
        journals=confirmed_journals,
        current_config=confirmed_config,
        _inventory_digest=confirmed_inventory_digest,
        _observations=confirmed_inventory,
        _cleanup_observations=confirmed_cleanup_inventory,
        _finalization_observations=final_finalization,
        _observation_token=_LIVE_OBSERVATION_TOKEN,
    )


def _reproduce_locked_plan(
    expected: RecoveryPlan,
    *,
    authority: PlatformPathAuthority,
    owned_root: OwnedRoot,
) -> RecoveryPlan:
    _snapshot, reproduced = _reproduce_locked_evidence(
        expected,
        authority=authority,
        owned_root=owned_root,
    )
    return reproduced


def _reproduce_locked_evidence(
    expected: RecoveryPlan,
    *,
    authority: PlatformPathAuthority,
    owned_root: OwnedRoot,
) -> tuple[RecoverySnapshot, RecoveryPlan]:
    expected._require_valid()
    try:
        observed = observe_recovery_snapshot(
            authority=authority,
            owned_root=owned_root,
        )
        reproduced = plan_recovery(observed)
        reproduced._require_valid()
    except (ForgeError, TypeError, ValueError):
        raise _plan_changed() from None
    expected._require_valid()
    if not hmac.compare_digest(expected.plan_digest, reproduced.plan_digest):
        raise _plan_changed()
    observed._require_valid()
    return observed, reproduced


def _journal_execution_evidence(
    snapshot: RecoverySnapshot,
    plan: RecoveryPlan,
) -> tuple[PendingTransactionObservation, LoadedJournal]:
    from . import ownership as _ownership

    snapshot._require_valid()
    plan._require_valid()
    observations = snapshot._observations
    cleanup_observations = snapshot._cleanup_observations
    if (
        len(plan.transaction_ids) != 1
        or type(observations) is not tuple
        or len(observations) != 1
        or type(cleanup_observations) is not tuple
        or cleanup_observations
        or len(snapshot.journals) != 1
        or snapshot._capture.cleanup_observations
    ):
        raise _plan_changed("The locked plan does not authorize cleanup.")
    observation = observations[0]
    journal = snapshot.journals[0]
    if (
        type(observation) is not _ownership.PendingTransactionObservation
        or observation._seal is not _ownership._PENDING_TRANSACTION_OBSERVATION_TOKEN
        or observation.binding.transaction_id != plan.transaction_ids[0]
        or observation.journal_relative
        != snapshot._capture.journals[0].journal_relative
        or journal.head.transaction_binding_digest
        != hashlib.sha256(
            canonical_json_bytes(observation.binding.canonical_projection())
        ).hexdigest()
    ):
        raise _plan_changed("The locked cleanup evidence changed.")
    try:
        if not hmac.compare_digest(
            _observed_inventory_digest(
                (observation,),
                (journal,),
                (),
                snapshot._finalization_observations or (),
            ),
            snapshot._inventory_digest or "",
        ):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise _plan_changed("The locked cleanup evidence changed.") from None
    snapshot._require_valid()
    plan._require_valid()
    journal._require_valid()
    return observation, journal


def _cleanup_execution_evidence(
    snapshot: RecoverySnapshot,
    plan: RecoveryPlan,
) -> tuple[
    PendingTransactionObservation | None,
    LoadedJournal | None,
    RecoveryCleanupObservation | None,
]:
    from . import ownership as _ownership

    snapshot._require_valid()
    plan._require_valid()
    if plan.disposition is not RecoveryDisposition.CLEANUP_PENDING:
        raise _plan_changed("The locked plan does not authorize cleanup.")
    if snapshot.journals:
        observation, journal = _journal_execution_evidence(snapshot, plan)
        if (
            observation.location is not _ownership.TransactionLocation.QUARANTINED
            or journal.head.state is not JournalState.ROLLED_BACK
        ):
            raise _plan_changed("The locked plan does not authorize cleanup.")
        return observation, journal, None
    cleanup_observations = snapshot._cleanup_observations
    if (
        type(cleanup_observations) is not tuple
        or len(cleanup_observations) != 1
        or snapshot._observations != ()
        or len(snapshot._capture.cleanup_observations) != 1
    ):
        raise _plan_changed("The locked plan does not authorize cleanup.")
    cleanup_observation = cleanup_observations[0]
    try:
        if (
            type(cleanup_observation) is not _ownership.RecoveryCleanupObservation
            or cleanup_observation._seal
            is not _ownership._RECOVERY_CLEANUP_OBSERVATION_TOKEN
            or cleanup_observation.phase not in {"AUTHORIZED", "FINALIZING", "COMPLETE"}
            or cleanup_observation.transaction_id != plan.transaction_ids[0]
            or _capture_cleanup_observation(cleanup_observation)
            != snapshot._capture.cleanup_observations[0]
            or not hmac.compare_digest(
                _observed_inventory_digest(
                    (),
                    (),
                    (cleanup_observation,),
                    snapshot._finalization_observations or (),
                ),
                snapshot._inventory_digest or "",
            )
        ):
            raise ValueError
        cleanup_observation._require_valid()
    except (AttributeError, TypeError, ValueError):
        raise _plan_changed("The locked cleanup evidence changed.") from None
    snapshot._require_valid()
    plan._require_valid()
    cleanup_observation._require_valid()
    return None, None, cleanup_observation


class _LockedRecoveryLease:
    """Registry-retained originals for one locked recovery authority."""

    _authority: PlatformPathAuthority
    _binding_digest: str
    _closed: bool
    _execution_consumed: bool
    _guard: Lock
    _held_lock: HeldInstallLock
    _object_identities: tuple[int, int, int, int, int]
    _owned_root: OwnedRoot
    _plan: RecoveryPlan
    _runner: RunnerProvenance

    __slots__ = (
        "_authority",
        "_binding_digest",
        "_closed",
        "_execution_consumed",
        "_guard",
        "_held_lock",
        "_object_identities",
        "_owned_root",
        "_plan",
        "_runner",
    )

    def __init__(
        self,
        *,
        plan: RecoveryPlan,
        authority: PlatformPathAuthority,
        owned_root: OwnedRoot,
        runner: RunnerProvenance,
        held_lock: HeldInstallLock,
    ) -> None:
        object.__setattr__(self, "_plan", plan)
        object.__setattr__(self, "_authority", authority)
        object.__setattr__(self, "_owned_root", owned_root)
        object.__setattr__(self, "_runner", runner)
        object.__setattr__(self, "_held_lock", held_lock)
        object.__setattr__(self, "_closed", False)
        object.__setattr__(self, "_execution_consumed", False)
        object.__setattr__(self, "_guard", Lock())
        object.__setattr__(
            self,
            "_object_identities",
            (
                id(plan),
                id(authority),
                id(owned_root),
                id(runner),
                id(held_lock),
            ),
        )
        object.__setattr__(self, "_binding_digest", self._current_binding_digest())
        with self._guard:
            self.require_open_locked()

    @property
    def guard(self) -> Lock:
        return self._guard

    @property
    def plan(self) -> RecoveryPlan:
        return self._plan

    @property
    def authority(self) -> PlatformPathAuthority:
        return self._authority

    @property
    def owned_root(self) -> OwnedRoot:
        return self._owned_root

    @property
    def closed(self) -> bool:
        with self._guard:
            return self._closed

    def _current_binding_digest(self) -> str:
        projection = {
            "authority_origin": id(self._authority._origin),
            "held_home_identity": self._held_lock.codex_home_identity,
            "owned_root": {
                "control_identity": self._owned_root.control_identity,
                "home_identity": self._owned_root.home_identity,
                "identity": self._owned_root.identity,
                "origin": id(self._owned_root._origin),
            },
            "plan_digest": self._plan.plan_digest,
            "runner": {
                "artifact_digest": self._runner.artifact_digest,
                "origin": self._runner.origin,
                "policy_digest": self._runner.policy_digest,
                "runner_version": self._runner.runner_version,
                "state": self._runner.state.value,
                "verification_authority": self._runner.verification_authority,
            },
        }
        return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()

    def require_open_locked(self) -> None:
        try:
            if (
                self._closed
                or self._execution_consumed
                or type(self._plan) is not RecoveryPlan
                or not _matching_path_authority(
                    self._authority,
                    self._owned_root,
                )
                or type(self._runner) is not RunnerProvenance
                or type(self._held_lock) is not HeldInstallLock
                or self._held_lock._released
                or self._held_lock.codex_home_identity != self._owned_root.home_identity
                or self._object_identities
                != (
                    id(self._plan),
                    id(self._authority),
                    id(self._owned_root),
                    id(self._runner),
                    id(self._held_lock),
                )
            ):
                raise ValueError
            self._plan._require_valid()
            self._runner.__post_init__()
            require_runner_authority(self._runner, RunnerOperation.RECOVER)
            self._owned_root._require_open()
            current_binding = self._current_binding_digest()
            if not hmac.compare_digest(self._binding_digest, current_binding):
                raise ValueError
        except (AttributeError, ForgeError, TypeError, ValueError):
            raise _plan_changed(
                "Locked recovery authority is closed or changed."
            ) from None

    def reproduce(self) -> RecoveryPlan:
        with self._guard:
            self.require_open_locked()
            reproduced = _reproduce_locked_plan(
                self._plan,
                authority=self._authority,
                owned_root=self._owned_root,
            )
            self.require_open_locked()
            return reproduced

    def consume_execution(self) -> None:
        self.require_open_locked()
        object.__setattr__(self, "_execution_consumed", True)

    def release(self) -> None:
        with self._guard:
            if self._closed:
                return
            try:
                self._held_lock.release()
            except BaseException:
                if self._held_lock._released:
                    object.__setattr__(self, "_closed", True)
                raise
            if not self._held_lock._released:
                raise _plan_changed("The recovery lock was not released.")
            else:
                object.__setattr__(self, "_closed", True)


class LockedRecoveryPlan:
    """Read-only handle for a registry-retained locked recovery lease."""

    __slots__ = ("__weakref__",)

    def __init__(
        self,
        *,
        plan: RecoveryPlan,
        authority: PlatformPathAuthority,
        owned_root: OwnedRoot,
        runner: RunnerProvenance,
        held_lock: HeldInstallLock,
        _token: object,
    ) -> None:
        if (
            _token is not _LOCKED_PLAN_TOKEN
            or type(plan) is not RecoveryPlan
            or not _matching_path_authority(authority, owned_root)
            or type(runner) is not RunnerProvenance
            or type(held_lock) is not HeldInstallLock
            or held_lock.codex_home_identity != owned_root.home_identity
        ):
            raise TypeError("LockedRecoveryPlan is created only by lock_recovery_plan")
        plan._require_valid()
        runner.__post_init__()
        require_runner_authority(runner, RunnerOperation.RECOVER)
        lease = _LockedRecoveryLease(
            plan=plan,
            authority=authority,
            owned_root=owned_root,
            runner=runner,
            held_lock=held_lock,
        )
        _register_locked_lease(self, lease)

    def _require_open(self) -> _LockedRecoveryLease:
        lease = _active_locked_lease(self)
        with lease.guard:
            lease.require_open_locked()
        return lease

    @property
    def plan(self) -> RecoveryPlan:
        """Return the diagnostic plan; effect methods must revalidate internally."""

        lease = self._require_open()
        return lease.plan

    @property
    def closed(self) -> bool:
        lease = _locked_lease_for_close(self)
        return lease is None or lease.closed

    def revalidate(self) -> RecoveryPlan:
        """Reload under the held lock and require the exact same pure plan digest."""

        return _active_locked_lease(self).reproduce()

    def close(self) -> None:
        lease = _locked_lease_for_close(self)
        if lease is None:
            return
        try:
            lease.release()
        finally:
            if lease.closed:
                _retire_locked_lease(self, lease)

    def __enter__(self) -> LockedRecoveryPlan:
        self._require_open()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __setattr__(self, _name: str, _value: object) -> Never:
        raise AttributeError("locked recovery plans are read-only")

    def __delattr__(self, _name: str) -> Never:
        raise AttributeError("locked recovery plans are read-only")

    def __reduce__(self) -> Never:
        raise TypeError("locked recovery plans are not serializable")


_LOCKED_RECOVERY_LEASES: WeakKeyDictionary[
    LockedRecoveryPlan,
    _LockedRecoveryLease,
] = WeakKeyDictionary()
_LOCKED_RECOVERY_FINALIZERS: WeakKeyDictionary[
    LockedRecoveryPlan,
    finalize[[_LockedRecoveryLease], LockedRecoveryPlan],
] = WeakKeyDictionary()
_CLOSED_LOCKED_RECOVERY_PLANS: WeakSet[LockedRecoveryPlan] = WeakSet()
_ABANDONED_LOCKED_RECOVERY_LEASES: set[_LockedRecoveryLease] = set()
_LOCKED_RECOVERY_LEASES_GUARD = Lock()


def _release_abandoned_locked_lease(lease: _LockedRecoveryLease) -> None:
    for _attempt in range(2):
        try:
            lease.release()
            return
        except BaseException:
            if lease.closed:
                return
    with _LOCKED_RECOVERY_LEASES_GUARD:
        _ABANDONED_LOCKED_RECOVERY_LEASES.add(lease)


def _retry_abandoned_locked_leases() -> None:
    with _LOCKED_RECOVERY_LEASES_GUARD:
        abandoned = tuple(_ABANDONED_LOCKED_RECOVERY_LEASES)
    for lease in abandoned:
        try:
            lease.release()
        except BaseException:
            continue
        if lease.closed:
            with _LOCKED_RECOVERY_LEASES_GUARD:
                _ABANDONED_LOCKED_RECOVERY_LEASES.discard(lease)


def _register_locked_lease(
    locked: LockedRecoveryPlan,
    lease: _LockedRecoveryLease,
) -> None:
    with _LOCKED_RECOVERY_LEASES_GUARD:
        if locked in _LOCKED_RECOVERY_LEASES or locked in _CLOSED_LOCKED_RECOVERY_PLANS:
            raise TypeError("locked recovery plan already registered")
        _LOCKED_RECOVERY_LEASES[locked] = lease
        _LOCKED_RECOVERY_FINALIZERS[locked] = finalize(
            locked,
            _release_abandoned_locked_lease,
            lease,
        )


def _active_locked_lease(locked: LockedRecoveryPlan) -> _LockedRecoveryLease:
    with _LOCKED_RECOVERY_LEASES_GUARD:
        lease = _LOCKED_RECOVERY_LEASES.get(locked)
    if lease is None:
        raise _plan_changed("Locked recovery authority is closed or changed.")
    return lease


def _locked_lease_for_close(
    locked: LockedRecoveryPlan,
) -> _LockedRecoveryLease | None:
    with _LOCKED_RECOVERY_LEASES_GUARD:
        lease = _LOCKED_RECOVERY_LEASES.get(locked)
        if lease is None and locked not in _CLOSED_LOCKED_RECOVERY_PLANS:
            raise _plan_changed("Locked recovery authority is closed or changed.")
    return lease


def _retire_locked_lease(
    locked: LockedRecoveryPlan,
    lease: _LockedRecoveryLease,
) -> None:
    with _LOCKED_RECOVERY_LEASES_GUARD:
        if _LOCKED_RECOVERY_LEASES.get(locked) is lease:
            del _LOCKED_RECOVERY_LEASES[locked]
        finalizer = _LOCKED_RECOVERY_FINALIZERS.pop(locked, None)
        if finalizer is not None:
            finalizer.detach()
        _CLOSED_LOCKED_RECOVERY_PLANS.add(locked)


def lock_recovery_plan(
    plan: RecoveryPlan,
    *,
    authority: PlatformPathAuthority,
    owned_root: OwnedRoot,
    runner: RunnerProvenance,
    timeout_seconds: float | int | None = None,
) -> LockedRecoveryPlan:
    """Acquire the home lock, reload live evidence, and reproduce one pure plan."""

    if (
        type(plan) is not RecoveryPlan
        or not _matching_path_authority(authority, owned_root)
        or type(runner) is not RunnerProvenance
    ):
        raise TypeError(
            "lock_recovery_plan requires sealed recovery authority and "
            "matching path authority"
        )
    plan._require_valid()
    runner.__post_init__()
    require_runner_authority(runner, RunnerOperation.RECOVER)
    _retry_abandoned_locked_leases()
    held = acquire_install_lock(
        owned_root,
        timeout_seconds=timeout_seconds,
    )
    locked: LockedRecoveryPlan | None = None
    try:
        if held.codex_home_identity != owned_root.home_identity:
            raise _plan_changed("The recovery lock identity changed.")
        reproduced = _reproduce_locked_plan(
            plan,
            authority=authority,
            owned_root=owned_root,
        )
        locked = LockedRecoveryPlan(
            plan=reproduced,
            authority=authority,
            owned_root=owned_root,
            runner=runner,
            held_lock=held,
            _token=_LOCKED_PLAN_TOKEN,
        )
        locked.revalidate()
        return locked
    except BaseException:
        if locked is not None:
            locked.close()
        else:
            held.release()
        raise


def _execute_cleanup_pending(
    lease: _LockedRecoveryLease,
    snapshot: RecoverySnapshot,
    plan: RecoveryPlan,
    *,
    consume_lease: bool,
    executed_disposition: RecoveryDisposition,
    completed_action_count: int,
) -> RecoveryResult:
    from . import ownership as _ownership

    observation, journal, cleanup_observation = _cleanup_execution_evidence(
        snapshot,
        plan,
    )
    authorization = (
        None if cleanup_observation is None else cleanup_observation.authorization
    )
    if observation is not None:
        recovery_reference = observation.journal_relative
    elif cleanup_observation is not None:
        recovery_reference = cleanup_observation.authorization.journal_relative
    else:
        raise _plan_changed("The locked plan does not authorize cleanup.")
    if consume_lease:
        lease.consume_execution()

    operation_message = "Recovery cleanup could not be authorized."
    try:
        if authorization is None:
            if observation is None or journal is None:
                raise TypeError("journal-backed cleanup evidence")
            authorized = _ownership.authorize_recovery_cleanup(
                lease.owned_root,
                observation=observation,
                journal=journal,
            )
            if authorized.is_ok:
                authorization = authorized.unwrap()
                if (
                    type(authorization) is not _ownership.RecoveryCleanupAuthorization
                    or authorization.binding != observation.binding
                    or authorization.location
                    is not _ownership.TransactionLocation.QUARANTINED
                    or authorization.journal_relative != recovery_reference
                    or authorization.journal_access_digest != journal.access_digest
                    or authorization.journal_evidence_digest != journal._binding_digest
                    or authorization.journal_head_sequence != journal.head.sequence
                    or authorization.journal_head_record_digest
                    != journal.head.record_digest
                    or authorization.transaction_binding_digest
                    != journal.head.transaction_binding_digest
                    or authorization.transaction_id != plan.transaction_ids[0]
                ):
                    authorization = None
                    operation_message = (
                        "Recovery cleanup authorization returned contradictory "
                        "evidence."
                    )
            if authorization is None:
                operation_message = "Recovery cleanup could not be authorized."
        if authorization is not None:
            operation_message = "The exact recovery cleanup remains incomplete."
            _ownership.resume_recovery_cleanup(
                lease.owned_root,
                authorization,
            )
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        operation_message = "The exact recovery cleanup remains incomplete."

    try:
        final_snapshot = observe_recovery_snapshot(
            authority=lease.authority,
            owned_root=lease.owned_root,
        )
        final_plan = plan_recovery(final_snapshot)
    except (ForgeError, TypeError, ValueError):
        raise ForgeError(
            "recovery.cleanup_incomplete",
            14,
            operation_message,
            recovery_instructions=(recovery_reference,),
        ) from None
    if (
        final_plan.disposition is not RecoveryDisposition.NO_RECOVERY
        or final_snapshot._capture.journals
        or final_snapshot._capture.cleanup_observations
        or final_plan.transaction_ids
        or final_plan.rollback_actions
        or final_plan.next_action_index is not None
        or final_plan.error_code is not None
    ):
        raise ForgeError(
            "recovery.cleanup_incomplete",
            14,
            operation_message,
            recovery_instructions=(recovery_reference,),
        )
    return RecoveryResult(
        executed_disposition=executed_disposition,
        final_disposition=final_plan.disposition,
        transaction_ids=plan.transaction_ids,
        completed_action_count=completed_action_count,
        cleanup_removed=True,
        recovery_references=(recovery_reference,),
    )


def _execute_rollback_candidate(
    lease: _LockedRecoveryLease,
    snapshot: RecoverySnapshot,
    plan: RecoveryPlan,
) -> RecoveryResult:
    from . import ownership as _ownership
    from .journal import (
        JournalRollbackEvent,
        JournalStore,
        JournalTransition,
    )

    observation, journal = _journal_execution_evidence(snapshot, plan)
    if (
        plan.disposition is not RecoveryDisposition.ROLLBACK_CANDIDATE
        or plan.next_action_index is None
        or not plan.rollback_actions
    ):
        raise _plan_changed("The locked plan does not authorize rollback.")
    binding = observation.binding
    transaction_result = _ownership.rebind_persistent_transaction_for_recovery(
        lease.owned_root,
        binding=binding,
    )
    if not transaction_result.is_ok:
        raise _plan_changed("The locked rollback target changed.")
    transaction = transaction_result.unwrap()
    path = None
    live_access = None
    live_store = None
    quarantine_ticket = None
    quarantine_transaction = None
    recovery_access = None
    recovery_store = None
    ownership_proof = None
    head = journal.head
    try:
        if observation.location is _ownership.TransactionLocation.LIVE:
            if (
                transaction.location is not _ownership.TransactionLocation.LIVE
                or transaction.binding != binding
                or transaction.ticket is not None
                or type(transaction.claim) is not _ownership.TransactionPathClaim
                or observation.journal_relative != binding.root_relative
            ):
                raise _plan_changed("The locked rollback target changed.")
            path_result = lease.authority.prove_descendant(
                lease.owned_root,
                transaction.claim.relative,
                expected_depth=3,
            )
            if not path_result.is_ok:
                raise _plan_changed("The locked rollback target changed.")
            path = path_result.unwrap()
            live_access_result = _ownership.open_transaction_journal_access(
                lease.owned_root,
                transaction,
            )
            if not live_access_result.is_ok:
                raise _plan_changed("The locked rollback journal changed.")
            live_access = live_access_result.unwrap()
            live_store = JournalStore(live_access, path)
            live_access = None
            loaded = live_store.load()
        elif observation.location is _ownership.TransactionLocation.QUARANTINED:
            ticket = transaction.ticket
            if (
                transaction.location is not _ownership.TransactionLocation.QUARANTINED
                or transaction.binding != binding
                or transaction.claim is not None
                or type(ticket) is not _ownership.QuarantineTicket
                or ticket.recovery_reference != binding.quarantine_relative
                or observation.journal_relative != binding.quarantine_relative
            ):
                raise _plan_changed("The locked rollback target changed.")
            recovery_access_result = (
                _ownership.open_quarantined_recovery_journal_access(
                    lease.owned_root,
                    transaction,
                )
            )
            if not recovery_access_result.is_ok:
                raise _plan_changed("The locked rollback journal changed.")
            recovery_access = recovery_access_result.unwrap()
            recovery_store = JournalStore.from_quarantined_recovery(recovery_access)
            recovery_access = None
            loaded = recovery_store.load()
        else:
            raise _plan_changed("The locked rollback target changed.")
        if loaded.head != journal.head or tuple(
            record.record_digest for record in loaded.records
        ) != tuple(record.record_digest for record in journal.records):
            raise _plan_changed("The locked rollback journal changed.")

        lease.consume_execution()
        action_index = plan.next_action_index
        while action_index < len(plan.rollback_actions):
            action = plan.rollback_actions[action_index]
            initial_event = journal.records[-1].rollback_event
            current_intent = (
                head.state is JournalState.ROLLBACK_ACTION_INTENT
                and head.record_digest == journal.head.record_digest
                and initial_event is not None
                and initial_event.action_index == action_index
            )
            if not current_intent:
                if live_store is None:
                    raise _plan_changed("The rollback intent location changed.")
                head = live_store.append(
                    head,
                    JournalTransition(
                        JournalState.ROLLBACK_ACTION_INTENT,
                        rollback_event=JournalRollbackEvent(
                            action_index=action_index,
                            action_digest=action.action_digest,
                        ),
                    ),
                )

            if action.action == "retain":
                if live_store is None:
                    raise _plan_changed("A retained rollback action moved.")
                head = live_store.append(
                    head,
                    JournalTransition(
                        JournalState.ROLLBACK_ACTION_COMPLETED,
                        rollback_event=JournalRollbackEvent(
                            action_index=action_index,
                            action_digest=action.action_digest,
                            outcome="retained",
                        ),
                    ),
                )
            else:
                if recovery_store is None:
                    if (
                        live_store is None
                        or path is None
                        or type(transaction.claim)
                        is not _ownership.TransactionPathClaim
                    ):
                        raise _plan_changed("The root rollback authority changed.")
                    proof_result = _ownership.prove_transaction_owned(
                        path,
                        claim=transaction.claim,
                    )
                    if not proof_result.is_ok:
                        raise _plan_changed("The root rollback authority changed.")
                    ownership_proof = proof_result.unwrap()
                    live_store.close()
                    live_store = None
                    transaction.close()
                    quarantine_result = _ownership.quarantine_owned(
                        ownership_proof,
                        transaction_id=binding.transaction_id,
                    )
                    ownership_proof.close()
                    ownership_proof = None
                    if not quarantine_result.is_ok:
                        error = quarantine_result.error
                        raise ForgeError(
                            "recovery.rollback_incomplete",
                            14,
                            "The exact transaction root could not be quarantined.",
                            recovery_instructions=(
                                error.recovery_instructions if error is not None else ()
                            ),
                        )
                    quarantine_ticket = quarantine_result.unwrap()
                    quarantine_result_rebound = (
                        _ownership.rebind_persistent_transaction(
                            lease.owned_root,
                            binding=binding,
                        )
                    )
                    if not quarantine_result_rebound.is_ok:
                        raise ForgeError(
                            "recovery.rollback_incomplete",
                            14,
                            "The quarantined transaction root could not be rebound.",
                            recovery_instructions=(binding.quarantine_relative,),
                        )
                    quarantine_transaction = quarantine_result_rebound.unwrap()
                    recovery_access_result = (
                        _ownership.open_quarantined_recovery_journal_access(
                            lease.owned_root,
                            quarantine_transaction,
                        )
                    )
                    if not recovery_access_result.is_ok:
                        raise ForgeError(
                            "recovery.rollback_incomplete",
                            14,
                            "The quarantined rollback journal could not be reopened.",
                            recovery_instructions=(binding.quarantine_relative,),
                        )
                    recovery_access = recovery_access_result.unwrap()
                    recovery_store = JournalStore.from_quarantined_recovery(
                        recovery_access
                    )
                    recovery_access = None
                head = recovery_store.append_recovery(
                    head,
                    JournalTransition(
                        JournalState.ROLLBACK_ACTION_COMPLETED,
                        rollback_event=JournalRollbackEvent(
                            action_index=action_index,
                            action_digest=action.action_digest,
                            outcome="quarantined",
                            observed_identity=action.expected_identity,
                            recovery_reference=binding.quarantine_relative,
                        ),
                    ),
                )
            action_index += 1

        if head.state is not JournalState.ROLLED_BACK:
            if recovery_store is None:
                raise _plan_changed("The terminal rollback location changed.")
            head = recovery_store.append_recovery(
                head,
                JournalTransition(JournalState.ROLLED_BACK),
            )
        if head.state is not JournalState.ROLLED_BACK:
            raise ForgeError(
                "recovery.rollback_incomplete",
                14,
                "The rollback terminal record was not durable.",
                recovery_instructions=(binding.quarantine_relative,),
            )

        if recovery_store is not None:
            recovery_store.close()
            recovery_store = None
        if recovery_access is not None:
            recovery_access.close()
            recovery_access = None
        if quarantine_transaction is not None:
            quarantine_transaction.close()
            quarantine_transaction = None
        if quarantine_ticket is not None:
            quarantine_ticket.close()
            quarantine_ticket = None
        if path is not None:
            path.close()
            path = None

        cleanup_snapshot = observe_recovery_snapshot(
            authority=lease.authority,
            owned_root=lease.owned_root,
        )
        cleanup_plan = plan_recovery(cleanup_snapshot)
        if (
            cleanup_plan.disposition is not RecoveryDisposition.CLEANUP_PENDING
            or cleanup_plan.transaction_ids != plan.transaction_ids
        ):
            raise ForgeError(
                "recovery.rollback_incomplete",
                14,
                "The terminal rollback cleanup plan is contradictory.",
                recovery_instructions=(binding.quarantine_relative,),
            )
        return _execute_cleanup_pending(
            lease,
            cleanup_snapshot,
            cleanup_plan,
            consume_lease=False,
            executed_disposition=RecoveryDisposition.ROLLBACK_CANDIDATE,
            completed_action_count=len(plan.rollback_actions),
        )
    finally:
        _close_all_recovery_resources(
            (
                recovery_store.close
                if recovery_store is not None
                else recovery_access.close
                if recovery_access is not None
                else None
            ),
            (
                quarantine_transaction.close
                if quarantine_transaction is not None
                else None
            ),
            quarantine_ticket.close if quarantine_ticket is not None else None,
            (
                live_store.close
                if live_store is not None
                else live_access.close
                if live_access is not None
                else None
            ),
            path.close if path is not None else None,
            ownership_proof.close if ownership_proof is not None else None,
            transaction.close,
        )


def execute_recovery(locked: LockedRecoveryPlan) -> RecoveryResult:
    """Consume one exact rollback or cleanup plan after locked reproduction."""

    if type(locked) is not LockedRecoveryPlan:
        raise TypeError("execute_recovery requires LockedRecoveryPlan")
    lease = _active_locked_lease(locked)
    try:
        with lease.guard:
            lease.require_open_locked()
            snapshot, plan = _reproduce_locked_evidence(
                lease.plan,
                authority=lease.authority,
                owned_root=lease.owned_root,
            )
            if plan.disposition is RecoveryDisposition.ROLLBACK_CANDIDATE:
                return _execute_rollback_candidate(lease, snapshot, plan)
            if plan.disposition is not RecoveryDisposition.CLEANUP_PENDING:
                raise ForgeError(
                    "recovery.operator_conflict",
                    14,
                    "The locked recovery plan does not authorize cleanup.",
                )
            return _execute_cleanup_pending(
                lease,
                snapshot,
                plan,
                consume_lease=True,
                executed_disposition=RecoveryDisposition.CLEANUP_PENDING,
                completed_action_count=0,
            )
    finally:
        locked.close()
