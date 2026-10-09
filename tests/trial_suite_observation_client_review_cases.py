"""Focused mailbox review regressions; authored without execution.

Integrate beside the existing non-default-collected client case module and run
explicitly. Its helpers provide bounded watchdogs and a private fake controller.
These cases make no worker, provider, or isolation claim.
"""
from __future__ import annotations

import builtins
import io
import os
from pathlib import Path
import threading

import pytest

from trial_suite_observation_client_cases import (
    Call, WAIT, controller, descriptor, echo, publish, request, response,
)
from trial_suite_fixtures import link


class TestObservationClientReview:
    def test_replaced_request_directory_cannot_supply_the_response(self, tmp_path, monkeypatch):
        path, mailbox = descriptor(tmp_path)
        directory = mailbox / "000001"
        retained = tmp_path / "retained-original-request"
        replaced = threading.Event()
        original_touch = Path.touch

        def ready_barrier(target, *args, **kwargs):
            result = original_touch(target, *args, **kwargs)
            if target == directory / "request.ready":
                assert replaced.wait(WAIT), "Controller did not finish directory replacement"
            return result

        def replace_after_request_ready(current, value):
            try:
                current.rename(retained)
                current.mkdir()
                for name in ("request.json", "request.ready"):
                    (current / name).write_bytes((retained / name).read_bytes())
                # Publish both responses before releasing the client. A descriptor-
                # pinned implementation cannot hang awaiting the original inode.
                publish(retained, response(value["id"], "original-directory"))
                publish(current, response(value["id"], "replacement-directory"))
            finally:
                replaced.set()

        monkeypatch.setattr(Path, "touch", ready_barrier)
        with controller(mailbox, [replace_after_request_ready]):
            with pytest.raises(ValueError):
                Call(path, request()).result()
        assert retained.is_dir() and directory.is_dir()
        assert (retained / "request.json").read_bytes() == (directory / "request.json").read_bytes()
        assert (retained / "response.json").read_bytes() != (directory / "response.json").read_bytes()

    def test_cyclic_mailbox_alias_uses_the_public_value_error_contract(self, tmp_path):
        path, mailbox = descriptor(tmp_path)
        original = path.read_bytes()
        mailbox.rmdir()
        link(mailbox, mailbox.name, directory=True)
        with pytest.raises(ValueError):
            Call(path, request()).result()
        assert mailbox.is_symlink()
        assert path.read_bytes() == original

    def test_next_call_does_not_reopen_completed_payload_bodies(self, tmp_path, monkeypatch):
        path, mailbox = descriptor(tmp_path)
        with controller(mailbox, [echo, echo]):
            assert Call(path, request("first", {"retained": "payload"})).result() == response(
                "first", {"retained": "payload"})
            prior = mailbox / "000001"
            original = {item.name: item.read_bytes() for item in prior.iterdir()}
            identities = {(info.st_dev, info.st_ino) for info in
                          ((prior / name).stat() for name in ("request.json", "response.json"))}

            def check_not_prior_payload(file, *, dir_fd=None):
                try:
                    info = (os.fstat(file) if isinstance(file, int)
                            else os.stat(file, dir_fd=dir_fd))
                except (OSError, TypeError, ValueError):
                    return
                assert (info.st_dev, info.st_ino) not in identities, (
                    "A subsequent call reopened an already completed payload body")

            def guard_stream_open(original_open):
                def guarded(file, *args, **kwargs):
                    check_not_prior_payload(file)
                    return original_open(file, *args, **kwargs)
                return guarded

            original_os_open = os.open
            def guarded_os_open(file, flags, *args, **kwargs):
                check_not_prior_payload(file, dir_fd=kwargs.get("dir_fd"))
                return original_os_open(file, flags, *args, **kwargs)

            # Restrict the guard to the next exchange; the final assertion below
            # deliberately reads retained bytes to prove they were not rewritten.
            with monkeypatch.context() as guarded:
                guarded.setattr(builtins, "open", guard_stream_open(builtins.open))
                guarded.setattr(io, "open", guard_stream_open(io.open))
                guarded.setattr(os, "open", guarded_os_open)
                assert Call(path, request("second", 2)).result() == response("second", 2)
        assert {item.name: item.read_bytes() for item in prior.iterdir()} == original
