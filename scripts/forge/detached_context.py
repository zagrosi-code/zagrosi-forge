"""Forge detached context."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, NamedTuple
import os
import time

from . import authority as _authority
from . import detached_authority as _detached_authority
from . import detached_contract as _detached_contract
from . import detached_state as _detached_state
from . import locks as _locks
from . import models as _models
from . import planning_snapshot as _planning_snapshot
from . import recovery as _recovery
from . import sections as _sections
from . import sources as _sources
from . import storage as _storage

class DetachedContext(NamedTuple):
    root: Path
    root_fd: int
    config: dict[str, Any]
    guard: _planning_snapshot.FrozenPlanningTree
    require_lock_authority: Callable[..., None]


@contextmanager
def open_detached_context(
    planning_dir: Path | None,
    raw_implementation_root: str,
    *,
    sections_dir: Path | None = None,
) -> Iterator[DetachedContext]:
    root_fd: int | None = None
    guard: _planning_snapshot.FrozenPlanningTree | None = None
    try:
        with ExitStack() as lock_context:
            lock_deadline = time.monotonic() + _detached_contract.DETACHED_LOCK_TIMEOUT_SECONDS
            require_global_authority = lock_context.enter_context(_locks.detached_global_lock(lock_deadline))
            require_global_authority()
            if planning_dir is None:
                planning_dir = _detached_authority.recover_planning_dir_from_detached_root(raw_implementation_root)
            guard = _planning_snapshot.FrozenPlanningTree.open(planning_dir)
            root, root_fd = _authority.ensure_detached_root(
                planning_dir,
                raw_implementation_root,
                create=False,
                planning_root_fd=guard.root_fd,
            )
            require_root_authority = lock_context.enter_context(
                _locks.section_record_lock(
                    root_fd,
                    root,
                    timeout_seconds=max(0.0, lock_deadline - time.monotonic()),
                )
            )

            def require_lock_authority(
                *,
                create_marker: bool = False,
                require_marker: bool = False,
            ) -> None:
                require_global_authority()
                require_root_authority(
                    create_marker=create_marker,
                    require_marker=require_marker,
                )
                require_global_authority()

            require_lock_authority()
            _detached_state.require_detached_top_level_inventory(
                root_fd,
                complete=True,
                allow_recoverable_temps=True,
            )
            _authority.require_planning_implementation_disjoint(planning_dir, guard.root_fd, root, root_fd)
            config = _detached_authority.load_detached_config(root_fd, planning_dir, root)
            _detached_state.require_detached_root_identity_through_recoverable_temps(
                root_fd,
                config.get("detached_implementation_root_identity_digest"),
            )
            _detached_state.load_detached_state(root_fd, config)
            _detached_state.load_detached_progress(root_fd, config)
            _detached_authority.verify_target_root_authority(planning_dir, guard.root_fd, root, root_fd, config)
            if config.get("planning_tree_sha256") != guard.digest:
                raise _models.DetachedImplementationError(
                    "planning-tree-drift",
                    "Frozen planning tree does not match the digest recorded by implement-setup.",
                    expected_planning_tree_sha256=config.get("planning_tree_sha256"),
                    actual_planning_tree_sha256=guard.digest,
                )
            if sections_dir is not None and config.get("sections_dir") != str(_storage.absolute_path_no_follow(sections_dir)):
                raise _models.DetachedImplementationError(
                    "invalid-detached-config",
                    "Command sections directory does not match detached implement-setup.",
                    expected_sections_dir=config.get("sections_dir"),
                    actual_sections_dir=str(_storage.absolute_path_no_follow(sections_dir)),
                )
            _, _, _, admission_state_sha256 = _authority.reopen_admission_pinner(
                planning_dir,
                root,
                str(config["admission_pinner_path"]),
                expected_sha256=str(config["admission_pinner_sha256"]),
                planning_root_fd=guard.root_fd,
                implementation_root_fd=root_fd,
            )
            if config.get("admission_state_sha256") != admission_state_sha256:
                raise _models.DetachedImplementationError(
                    "invalid-detached-config",
                    "Detached config admission state does not equal the reopened final pinner START/END A digest.",
                    expected_admission_state_sha256=admission_state_sha256,
                    actual_admission_state_sha256=config.get("admission_state_sha256"),
                )
            _sources.verify_implementation_sources(config)
            _detached_authority.reopen_detached_config_exact(root_fd, config)
            guard.verify_unchanged()
            _detached_authority.verify_target_root_authority(planning_dir, guard.root_fd, root, root_fd, config)
            _, _, _, admission_state_sha256 = _authority.reopen_admission_pinner(
                planning_dir,
                root,
                str(config["admission_pinner_path"]),
                expected_sha256=str(config["admission_pinner_sha256"]),
                planning_root_fd=guard.root_fd,
                implementation_root_fd=root_fd,
            )
            if config.get("admission_state_sha256") != admission_state_sha256:
                raise _models.DetachedImplementationError(
                    "invalid-detached-config",
                    "Detached config admission state changed before authenticated temp recovery.",
                )
            require_lock_authority()
            _detached_state.recover_detached_root_temps_locked(root_fd)
            if _detached_state.detached_implementation_root_identity_digest(root_fd) != config.get(
                "detached_implementation_root_identity_digest"
            ):
                raise _models.DetachedImplementationError(
                    "detached-root-identity-drift",
                    "Detached implementation root identity changed during authenticated temp recovery.",
                )
            _detached_authority.verify_detached_authorities(planning_dir, root, root_fd, config, guard)
            _recovery.recover_section_record_transaction_locked(
                planning_dir,
                root,
                root_fd,
                config,
                guard,
                _sections.check_section_progress(planning_dir),
                require_lock_authority,
            )
            _detached_authority.verify_detached_authorities(planning_dir, root, root_fd, config, guard)
            require_lock_authority()
            yield DetachedContext(root, root_fd, config, guard, require_lock_authority)
    finally:
        try:
            if guard is not None:
                guard.close()
        finally:
            if root_fd is not None:
                os.close(root_fd)
