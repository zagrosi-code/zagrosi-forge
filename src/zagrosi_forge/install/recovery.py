"""Pure rollback-only classification for immutable transaction evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import hmac
import re
from typing import NamedTuple, Never
import unicodedata

from .contracts import canonical_json_bytes
from .journal import (
    JOURNAL_STATE_MACHINE_VERSION,
    JournalConfigIdentity,
    JournalState,
    LoadedJournal,
    RollbackAction,
)
from .policies import LIMIT_POLICY


RECOVERY_POLICY_VERSION = "1.0"
_SNAPSHOT_TOKEN = object()
_PLAN_TOKEN = object()
_TRANSACTION_ID = re.compile(r"tx-[0-9a-f]{32}\Z")
_ROLLBACK_STATES = frozenset(
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
_NO_RECOVERY_STATES = frozenset(
    {
        JournalState.FINALIZED.value,
        JournalState.ROLLED_BACK.value,
    }
)


class RecoveryDisposition(str, Enum):
    NO_RECOVERY = "no_recovery"
    ROLLBACK_CANDIDATE = "rollback_candidate"
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
    head_state: str
    record_digests: tuple[str, ...]
    before_config: _ConfigCapture
    candidate_config: _ConfigCapture
    rollback_actions: tuple[_RollbackCapture, ...]


class _SnapshotCapture(NamedTuple):
    journals: tuple[_JournalCapture, ...]
    current_config: _ConfigCapture | None


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
        "head_digest": journal.head_digest,
        "head_state": journal.head_state,
        "journal_plan_digest": journal.plan_digest,
        "record_digests": journal.record_digests,
        "rollback_actions": tuple(
            _rollback_capture_projection(action) for action in journal.rollback_actions
        ),
        "transaction_id": journal.transaction_id,
    }


def _snapshot_projection(capture: _SnapshotCapture) -> dict[str, object]:
    return {
        "current_config": _config_projection(capture.current_config),
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


def _capture_journal(journal: LoadedJournal) -> _JournalCapture:
    record = journal.records[-1]
    prepared = record.prepared
    return _JournalCapture(
        transaction_id=record.transaction_id,
        plan_digest=record.plan_digest,
        head_digest=record.record_digest,
        head_state=record.state.value,
        record_digests=tuple(item.record_digest for item in journal.records),
        before_config=_capture_config(prepared.before_config),
        candidate_config=_capture_config(prepared.candidate_config),
        rollback_actions=tuple(
            _RollbackCapture(
                action=action.action,
                relative_path=action.relative_path,
                expected_identity=action.expected_identity,
            )
            for action in prepared.rollback_actions
        ),
    )


def _capture_snapshot(
    journals: tuple[LoadedJournal, ...],
    current_config: JournalConfigIdentity | None,
) -> _SnapshotCapture:
    return _SnapshotCapture(
        journals=tuple(_capture_journal(journal) for journal in journals),
        current_config=_capture_optional_config(current_config),
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


def _portable_collision_key(value: str) -> str:
    return "/".join(
        unicodedata.normalize("NFKC", component).casefold()
        for component in value.split("/")
    )


def _valid_journal_capture(value: object) -> bool:
    return (
        type(value) is _JournalCapture
        and type(value.transaction_id) is str
        and _TRANSACTION_ID.fullmatch(value.transaction_id) is not None
        and _valid_digest(value.plan_digest)
        and _valid_digest(value.head_digest)
        and type(value.head_state) is str
        and value.head_state in {state.value for state in JournalState}
        and type(value.record_digests) is tuple
        and bool(value.record_digests)
        and all(_valid_digest(digest) for digest in value.record_digests)
        and value.record_digests[-1] == value.head_digest
        and _valid_config_capture(value.before_config)
        and _valid_config_capture(value.candidate_config)
        and value.before_config.target_metadata_digest is None
        and value.candidate_config.target_metadata_digest is not None
        and _valid_rollback_captures(value.rollback_actions)
    )


def _valid_snapshot_capture(value: object) -> bool:
    if (
        type(value) is not _SnapshotCapture
        or type(value.journals) is not tuple
        or len(value.journals) > LIMIT_POLICY.value("journal_records")
        or any(not _valid_journal_capture(journal) for journal in value.journals)
        or (
            value.current_config is not None
            and not _valid_config_capture(value.current_config)
        )
    ):
        return False
    transaction_ids = tuple(journal.transaction_id for journal in value.journals)
    return transaction_ids == tuple(sorted(transaction_ids)) and len(
        set(transaction_ids)
    ) == len(transaction_ids)


def _stable_snapshot_capture(
    journals: tuple[LoadedJournal, ...],
    current_config: JournalConfigIdentity | None,
) -> _SnapshotCapture:
    if (
        type(journals) is not tuple
        or len(journals) > LIMIT_POLICY.value("journal_records")
        or any(type(journal) is not LoadedJournal for journal in journals)
        or (
            current_config is not None
            and type(current_config) is not JournalConfigIdentity
        )
    ):
        raise TypeError("RecoverySnapshot evidence is invalid")
    try:
        for journal in journals:
            journal._require_valid()
        if current_config is not None:
            current_config.__post_init__()
        captured = _capture_snapshot(journals, current_config)
        for journal in journals:
            journal._require_valid()
        if current_config is not None:
            current_config.__post_init__()
        confirmed = _capture_snapshot(journals, current_config)
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
    _seal: object

    def __init__(
        self,
        *,
        journals: tuple[LoadedJournal, ...],
        current_config: JournalConfigIdentity | None,
    ) -> None:
        capture = _stable_snapshot_capture(journals, current_config)
        digest = _snapshot_digest(capture)
        object.__setattr__(self, "journals", journals)
        object.__setattr__(self, "current_config", current_config)
        object.__setattr__(self, "snapshot_digest", digest)
        object.__setattr__(self, "_capture", capture)
        object.__setattr__(self, "_seal", _SNAPSHOT_TOKEN)

    def _require_valid(self) -> None:
        try:
            capture = _stable_snapshot_capture(
                self.journals,
                self.current_config,
            )
            expected = _snapshot_digest(capture)
        except (AttributeError, TypeError, ValueError):
            raise TypeError("RecoverySnapshot authority changed") from None
        if (
            self._seal is not _SNAPSHOT_TOKEN
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
    error_code: str | None,
) -> dict[str, object]:
    return {
        "disposition": disposition.value,
        "error_code": error_code,
        "recovery_policy_version": RECOVERY_POLICY_VERSION,
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
        return not transaction_ids and not rollback_actions and error_code is None
    if disposition is RecoveryDisposition.ROLLBACK_CANDIDATE:
        return (
            len(transaction_ids) == 1 and bool(rollback_actions) and error_code is None
        )
    return (
        disposition is RecoveryDisposition.OPERATOR_CONFLICT
        and bool(transaction_ids)
        and not rollback_actions
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
    error_code: str | None
    _seal: object

    def __init__(
        self,
        *,
        snapshot_digest: str,
        disposition: RecoveryDisposition,
        transaction_ids: tuple[str, ...],
        rollback_actions: tuple[RollbackAction, ...],
        error_code: str | None,
        _token: object,
    ) -> None:
        if _token is not _PLAN_TOKEN or not _valid_plan_fields(
            snapshot_digest=snapshot_digest,
            disposition=disposition,
            transaction_ids=transaction_ids,
            rollback_actions=rollback_actions,
            error_code=error_code,
        ):
            raise TypeError("RecoveryPlan is created only by plan_recovery")
        domain = _plan_projection(
            snapshot_digest=snapshot_digest,
            disposition=disposition,
            transaction_ids=transaction_ids,
            rollback_actions=rollback_actions,
            error_code=error_code,
        )
        plan_digest = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        object.__setattr__(self, "snapshot_digest", snapshot_digest)
        object.__setattr__(self, "plan_digest", plan_digest)
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "transaction_ids", transaction_ids)
        object.__setattr__(self, "rollback_actions", rollback_actions)
        object.__setattr__(self, "error_code", error_code)
        object.__setattr__(self, "_seal", _PLAN_TOKEN)

    def _require_valid(self) -> None:
        try:
            if not _valid_plan_fields(
                snapshot_digest=self.snapshot_digest,
                disposition=self.disposition,
                transaction_ids=self.transaction_ids,
                rollback_actions=self.rollback_actions,
                error_code=self.error_code,
            ):
                raise TypeError
            domain = _plan_projection(
                snapshot_digest=self.snapshot_digest,
                disposition=self.disposition,
                transaction_ids=self.transaction_ids,
                rollback_actions=self.rollback_actions,
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


def _journal_disposition(
    journal: _JournalCapture,
    current_config: _ConfigCapture | None,
) -> RecoveryDisposition:
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
    if not capture.journals:
        return _make_plan(
            snapshot,
            capture,
            capture_digest,
            disposition=RecoveryDisposition.NO_RECOVERY,
            transaction_ids=(),
            rollback_actions=(),
            error_code=None,
        )

    decisions = tuple(
        _journal_disposition(journal, capture.current_config)
        for journal in capture.journals
    )
    all_transaction_ids = tuple(journal.transaction_id for journal in capture.journals)
    if all(disposition is RecoveryDisposition.NO_RECOVERY for disposition in decisions):
        return _make_plan(
            snapshot,
            capture,
            capture_digest,
            disposition=RecoveryDisposition.NO_RECOVERY,
            transaction_ids=(),
            rollback_actions=(),
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
            error_code="recovery.operator_conflict",
        )
    journal = capture.journals[0]
    if decisions[0] is RecoveryDisposition.ROLLBACK_CANDIDATE:
        return _make_plan(
            snapshot,
            capture,
            capture_digest,
            disposition=RecoveryDisposition.ROLLBACK_CANDIDATE,
            transaction_ids=(journal.transaction_id,),
            rollback_actions=tuple(
                _rollback_action(action) for action in journal.rollback_actions
            ),
            error_code=None,
        )
    return _make_plan(
        snapshot,
        capture,
        capture_digest,
        disposition=RecoveryDisposition.OPERATOR_CONFLICT,
        transaction_ids=(journal.transaction_id,),
        rollback_actions=(),
        error_code="recovery.operator_conflict",
    )
