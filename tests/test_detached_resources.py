"""Detached resource lifetimes survive acquisition failures and caller exits."""

from __future__ import annotations

import os
from contextlib import contextmanager

import pytest
from detached_test_support import make_detached_record_fixture
from forge_test_helpers import SCRIPT
from runtime_support import load_runtime


@pytest.fixture
def resources(tmp_path, monkeypatch):
    fixture = make_detached_record_fixture(tmp_path)
    context = load_runtime(SCRIPT).detached_context
    events, descriptors = [], []

    def track_lock(name, original):
        @contextmanager
        def tracked(*args, **kwargs):
            with original(*args, **kwargs) as require:
                events.append(f"{name}-acquired")
                try:
                    yield require
                finally:
                    events.append(f"{name}-released")
        return tracked

    for name in ("detached_global_lock", "section_record_lock"):
        original = getattr(context._locks, name)
        monkeypatch.setattr(context._locks, name, track_lock(name, original))

    tree = context._planning_snapshot.FrozenPlanningTree
    original_open, original_close = tree.open, tree.close

    def open_tree(*args, **kwargs):
        guard = original_open(*args, **kwargs)
        descriptors.append(guard.root_fd)
        events.append("planning-acquired")
        return guard

    def close_tree(guard):
        events.append("planning-released")
        original_close(guard)

    monkeypatch.setattr(tree, "open", open_tree)
    monkeypatch.setattr(tree, "close", close_tree)
    original_root = context._authority.ensure_detached_root

    def open_root(*args, **kwargs):
        root, fd = original_root(*args, **kwargs)
        descriptors.append(fd)
        return root, fd

    monkeypatch.setattr(context._authority, "ensure_detached_root", open_root)
    return fixture, context, events, descriptors


def assert_released(events, descriptors):
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)
    acquired = [event.removesuffix("-acquired") for event in events if event.endswith("-acquired")]
    expected = [name for name in ("section_record_lock", "detached_global_lock", "planning") if name in acquired]
    assert [event.removesuffix("-released") for event in events if event.endswith("-released")] == expected


@pytest.mark.parametrize("exit_kind", ["normal", "return", "exception", "interrupt"])
def test_detached_context_releases_resources_and_reacquires_locks(resources, exit_kind):
    fixture, context, events, descriptors = resources

    def command():
        with context.open_detached_context(fixture.planning, str(fixture.implementation_root)) as opened:
            assert opened.root == fixture.implementation_root
            assert opened.config["planning_tree_sha256"] == opened.guard.digest
            opened.require_lock_authority()
            if exit_kind == "return":
                return "early"
            if exit_kind == "exception":
                raise RuntimeError("caller failed")
            if exit_kind == "interrupt":
                raise KeyboardInterrupt
        return "finished"

    if exit_kind in {"exception", "interrupt"}:
        with pytest.raises(RuntimeError if exit_kind == "exception" else KeyboardInterrupt):
            command()
    else:
        assert command() == ("early" if exit_kind == "return" else "finished")
    assert_released(events, descriptors)
    events.clear()
    descriptors.clear()
    with context.open_detached_context(fixture.planning, str(fixture.implementation_root)) as reopened:
        reopened.require_lock_authority()
    assert_released(events, descriptors)


@pytest.mark.parametrize("failure", ["global-lock", "planning", "root", "root-lock", "config", "recovery"])
def test_detached_context_closes_partial_acquisitions(resources, monkeypatch, failure):
    fixture, context, events, descriptors = resources
    owner, name = {
        "global-lock": (context._locks, "detached_global_lock"),
        "planning": (context._planning_snapshot.FrozenPlanningTree, "open"),
        "root": (context._authority, "ensure_detached_root"),
        "root-lock": (context._locks, "section_record_lock"),
        "config": (context._detached_authority, "load_detached_config"),
        "recovery": (context._recovery, "recover_section_record_transaction_locked"),
    }[failure]
    original = getattr(owner, name)

    def failed(*args, **kwargs):
        raise RuntimeError(failure)

    monkeypatch.setattr(owner, name, failed)
    with pytest.raises(RuntimeError, match=failure):
        with context.open_detached_context(fixture.planning, str(fixture.implementation_root)):
            pytest.fail("Failed acquisition yielded a context")
    assert_released(events, descriptors)
    monkeypatch.setattr(owner, name, original)
    events.clear()
    descriptors.clear()
    with context.open_detached_context(fixture.planning, str(fixture.implementation_root)) as reopened:
        reopened.require_lock_authority()
    assert_released(events, descriptors)
