"""Portable lock-release controls using the bounded private-controller fixture."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from trial_suite_observation_client_cases import (
    Call, controller, descriptor, echo, publish, request, response,
)


def deny_unlink_while_open(monkeypatch, lock, after_close=None):
    original_open, original_close, original_unlink = os.open, os.close, Path.unlink
    owned = set()
    closed = []

    def open_file(path, flags, *args, **kwargs):
        handle = original_open(path, flags, *args, **kwargs)
        if not isinstance(path, int) and Path(path) == lock and flags & os.O_EXCL:
            owned.add(handle)
        return handle

    def close_file(handle):
        is_lock = handle in owned
        original_close(handle)
        if is_lock:
            owned.remove(handle)
            closed.append(handle)
            if after_close is not None:
                after_close()

    def unlink_file(path, *args, **kwargs):
        if path == lock and owned:
            raise PermissionError(13, "simulated Windows sharing violation: lock is open", str(path))
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_file)
    monkeypatch.setattr(os, "close", close_file)
    monkeypatch.setattr(Path, "unlink", unlink_file)
    return closed


class TestObservationClientLock:
    def test_closed_handle_keeps_exclusion_until_owned_lock_is_removed(self, tmp_path, monkeypatch):
        path, mailbox = descriptor(tmp_path)
        lock = mailbox / "client.lock"

        def compete_after_close():
            assert lock.is_file(), "Closing the handle must not release the sentinel"
            with pytest.raises(ValueError, match="Invalid observation mailbox"):
                Call(path, request("competitor", 2)).result()
            assert not (mailbox / "000002").exists()

        closed = deny_unlink_while_open(monkeypatch, lock, compete_after_close)
        with controller(mailbox, [echo]):
            assert Call(path, request("first", 1)).result() == response("first", 1)
        assert len(closed) == 1
        assert not lock.exists()
        assert sorted(item.name for item in mailbox.iterdir()) == ["000001"]

    def test_release_preserves_original_response_error(self, tmp_path, monkeypatch):
        path, mailbox = descriptor(tmp_path)
        lock = mailbox / "client.lock"
        closed = deny_unlink_while_open(monkeypatch, lock)
        with controller(mailbox, [lambda directory, value: publish(directory, response("wrong-id"))]):
            with pytest.raises(ValueError, match="Observation response identity differs"):
                Call(path, request()).result()
        assert len(closed) == 1
        assert not lock.exists()
        assert (mailbox / "000001/response.json").is_file()

    def test_lock_replaced_after_close_is_rejected_and_preserved(self, tmp_path, monkeypatch):
        path, mailbox = descriptor(tmp_path)
        lock = mailbox / "client.lock"
        replacement = b"another owner's replacement lock"

        def replace_after_close():
            lock.unlink()
            lock.write_bytes(replacement)

        closed = deny_unlink_while_open(monkeypatch, lock, replace_after_close)
        with controller(mailbox, [echo]):
            with pytest.raises(ValueError, match="Observation client lock changed"):
                Call(path, request()).result()
        assert len(closed) == 1
        assert lock.read_bytes() == replacement
