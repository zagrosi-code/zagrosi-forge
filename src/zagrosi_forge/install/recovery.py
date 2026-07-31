"""Pure rollback-only classification for immutable transaction evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import hmac
import re
from threading import Lock
from typing import NamedTuple, Never
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


RECOVERY_POLICY_VERSION = "1.0"
_SNAPSHOT_TOKEN = object()
_PLAN_TOKEN = object()
_LOCKED_PLAN_TOKEN = object()
_LIVE_OBSERVATION_TOKEN = object()
_TRANSACTION_ID = re.compile(r"tx-[0-9a-f]{32}\Z")
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
    next_action_index: int | None


class _SnapshotCapture(NamedTuple):
    journals: tuple[_JournalCapture, ...]
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
        "head_digest": journal.head_digest,
        "head_state": journal.head_state,
        "journal_plan_digest": journal.plan_digest,
        "record_digests": journal.record_digests,
        "rollback_actions": tuple(
            _rollback_capture_projection(action) for action in journal.rollback_actions
        ),
        "next_action_index": journal.next_action_index,
        "transaction_id": journal.transaction_id,
    }


def _snapshot_projection(capture: _SnapshotCapture) -> dict[str, object]:
    return {
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
        next_action_index=_next_rollback_action_index(journal),
    )


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
    current_config: JournalConfigIdentity | None,
    inventory_digest: str | None,
) -> _SnapshotCapture:
    return _SnapshotCapture(
        journals=tuple(_capture_journal(journal) for journal in journals),
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
        and _captured_root_rollback_is_exact(
            value.rollback_actions,
            transaction_id=value.transaction_id,
        )
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
        or (
            value.inventory_digest is not None
            and not _valid_digest(value.inventory_digest)
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
    inventory_digest: str | None,
) -> _SnapshotCapture:
    if (
        type(journals) is not tuple
        or len(journals) > LIMIT_POLICY.value("journal_records")
        or any(type(journal) is not LoadedJournal for journal in journals)
        or (
            current_config is not None
            and type(current_config) is not JournalConfigIdentity
        )
        or (inventory_digest is not None and not _valid_digest(inventory_digest))
    ):
        raise TypeError("RecoverySnapshot evidence is invalid")
    try:
        for journal in journals:
            journal._require_valid()
        if current_config is not None:
            current_config.__post_init__()
        captured = _capture_snapshot(journals, current_config, inventory_digest)
        for journal in journals:
            journal._require_valid()
        if current_config is not None:
            current_config.__post_init__()
        confirmed = _capture_snapshot(journals, current_config, inventory_digest)
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
    _observation_seal: object | None
    _seal: object

    def __init__(
        self,
        *,
        journals: tuple[LoadedJournal, ...],
        current_config: JournalConfigIdentity | None,
        _inventory_digest: str | None = None,
        _observation_token: object | None = None,
    ) -> None:
        if (_inventory_digest is None and _observation_token is not None) or (
            _inventory_digest is not None
            and (
                not _valid_digest(_inventory_digest)
                or _observation_token is not _LIVE_OBSERVATION_TOKEN
            )
        ):
            raise TypeError(
                "live recovery snapshots are minted only by recovery observation"
            )
        capture = _stable_snapshot_capture(
            journals,
            current_config,
            _inventory_digest,
        )
        digest = _snapshot_digest(capture)
        object.__setattr__(self, "journals", journals)
        object.__setattr__(self, "current_config", current_config)
        object.__setattr__(self, "snapshot_digest", digest)
        object.__setattr__(self, "_capture", capture)
        object.__setattr__(self, "_inventory_digest", _inventory_digest)
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
                self.current_config,
                self._inventory_digest,
            )
            expected = _snapshot_digest(capture)
        except (AttributeError, TypeError, ValueError):
            raise TypeError("RecoverySnapshot authority changed") from None
        if (
            self._seal is not _SNAPSHOT_TOKEN
            or (self._inventory_digest is None and self._observation_seal is not None)
            or (
                self._inventory_digest is not None
                and (
                    not _valid_digest(self._inventory_digest)
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
    if not capture.journals:
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
            next_action_index=journal.next_action_index,
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


def _observed_inventory_digest(
    observations: tuple[object, ...],
    journals: tuple[LoadedJournal, ...],
) -> str:
    from . import ownership as _ownership

    if (
        type(observations) is not tuple
        or type(journals) is not tuple
        or len(observations) != len(journals)
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
        for journal in journals:
            journal._require_valid()
    except (AttributeError, ForgeError, TypeError, ValueError):
        raise TypeError("recovery inventory evidence is invalid") from None
    return hashlib.sha256(
        canonical_json_bytes(
            {
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


def observe_recovery_snapshot(
    *,
    authority: PlatformPathAuthority,
    owned_root: OwnedRoot,
) -> RecoverySnapshot:
    """Observe one stable, effect-free rollback snapshot from live authorities."""

    if not _matching_path_authority(authority, owned_root):
        raise TypeError("recovery observation requires matching path authority")
    first_inventory = _discover_inventory(owned_root)
    first_journals = load_pending(owned_root)
    first_config = observe_current_config_identity(
        authority=authority,
        owned_root=owned_root,
    )
    second_inventory = _discover_inventory(owned_root)
    second_journals = load_pending(owned_root)
    second_config = observe_current_config_identity(
        authority=authority,
        owned_root=owned_root,
    )
    final_inventory = _discover_inventory(owned_root)
    try:
        first_inventory_digest = _observed_inventory_digest(
            first_inventory,
            first_journals,
        )
        second_inventory_digest = _observed_inventory_digest(
            second_inventory,
            second_journals,
        )
        final_inventory_digest = _observed_inventory_digest(
            final_inventory,
            second_journals,
        )
    except TypeError:
        raise _plan_changed(
            "Recovery inventory changed while it was observed."
        ) from None
    if (
        first_inventory != second_inventory
        or second_inventory != final_inventory
        or first_inventory_digest != second_inventory_digest
        or second_inventory_digest != final_inventory_digest
        or first_journals != second_journals
        or first_config != second_config
    ):
        raise _plan_changed("Recovery evidence changed while it was observed.")
    return RecoverySnapshot(
        journals=second_journals,
        current_config=second_config,
        _inventory_digest=final_inventory_digest,
        _observation_token=_LIVE_OBSERVATION_TOKEN,
    )


def _reproduce_locked_plan(
    expected: RecoveryPlan,
    *,
    authority: PlatformPathAuthority,
    owned_root: OwnedRoot,
) -> RecoveryPlan:
    expected._require_valid()
    observed = observe_recovery_snapshot(
        authority=authority,
        owned_root=owned_root,
    )
    reproduced = plan_recovery(observed)
    reproduced._require_valid()
    expected._require_valid()
    if not hmac.compare_digest(expected.plan_digest, reproduced.plan_digest):
        raise _plan_changed()
    return reproduced


class _LockedRecoveryLease:
    """Registry-retained originals for one locked recovery authority."""

    _authority: PlatformPathAuthority
    _binding_digest: str
    _closed: bool
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
