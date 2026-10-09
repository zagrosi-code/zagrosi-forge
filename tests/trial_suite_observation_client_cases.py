"""Independent process-free mailbox controls; no execution during authoring.

The controller below is a bounded test thread, never a provider or Docker double.
Its fictional descriptor grants no assessment or native qualification.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_observation_client import observe
from trial_suite_fixtures import link


SCHEMA = "coding-trial-observation/v1"
WAIT = 5  # Test-harness watchdog only; not a production observation deadline.


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()


def request(identifier="opaque-id", value=None):
    return {"schema": SCHEMA, "id": identifier, "input": value}


def response(identifier="opaque-id", value=None, error=None):
    return {"schema": SCHEMA, "id": identifier, "result": value, "error": error}


def descriptor(tmp_path, limit=4096):
    evidence = tmp_path / "evidence"
    (evidence / "oracle-ipc").mkdir(parents=True)
    private = tmp_path / "private"
    private.mkdir()
    roots = {}
    for name in ("workspace", "product", "public", "generated"):
        root = tmp_path / name
        root.mkdir()
        roots[name] = str(root.resolve())
    roots.update(native_runtime=None, private=[str(private.resolve()), str(evidence.resolve())])
    command = {"id": "worker", "entry": "worker.py", "support": [],
               "argv": ["{python}", "{entry}"], "cwd": ".", "env": {},
               "timeout_seconds": 10, "output_bytes": limit}
    identity = {"suite_sha256": "1" * 64, "task": "fixture", "arm": "plain",
                "baseline_sha256": "2" * 64, "configuration_sha256": "3" * 64}
    value = {"schema": "coding-trial-assessment-input/v1", "identity": identity,
             "suite_root": str(private.resolve()),
             "manifest": {"path": "fixture.json", "sha256": "4" * 64, "suite_sha256": "1" * 64},
             "input_inventory_sha256": "5" * 64, "evaluator_sha256": "6" * 64,
             "controller": {"executable": str(Path(sys.executable).resolve()),
                            "executable_sha256": "7" * 64, "version": "fictional-test-only"},
             "observation_client": {"path": str(private / "observation_client.py"), "sha256": "8" * 64},
             "candidate": {"path": roots["workspace"], "full_inventory_sha256": "9" * 64,
                           "assessed_sha256": "a" * 64},
             "worker": {"command": command, "roots": roots, "protocol": SCHEMA},
             "evidence_dir": str(evidence.resolve())}
    path = private / "assessment.json"
    path.write_bytes(encoded(value))
    return path, evidence / "oracle-ipc"


class Call:
    """A watchdog makes a broken waiting implementation fail instead of hanging pytest."""

    def __init__(self, assessment, value):
        self.outcome = queue.Queue()
        def run():
            try:
                self.outcome.put((observe(assessment, value), None))
            except BaseException as exc:
                self.outcome.put((None, exc))
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def result(self):
        self.thread.join(WAIT)
        assert not self.thread.is_alive(), "Observation client did not finish after a published response/rejection"
        value, error = self.outcome.get_nowait()
        if error is not None:
            raise error
        return value


def publish(directory, value):
    raw = value if isinstance(value, bytes) else encoded(value)
    with (directory / "response.json").open("xb") as stream:
        stream.write(raw)
    (directory / "response.ready").touch(exist_ok=False)


@contextmanager
def controller(mailbox, handlers):
    stop = threading.Event()
    failures = []
    observed = []
    def service():
        try:
            for number, handler in enumerate(handlers, 1):
                directory = mailbox / f"{number:06d}"
                deadline = time.monotonic() + WAIT
                while not (directory / "request.ready").exists():
                    if stop.wait(0.005):
                        return
                    if time.monotonic() >= deadline:
                        raise AssertionError("Expected ready request was never published")
                assert (directory / "request.ready").read_bytes() == b""
                raw = (directory / "request.json").read_bytes()
                assert raw.endswith(b"\n")
                value = json.loads(raw)
                observed.append((directory, raw, value))
                handler(directory, value)
        except BaseException as exc:
            failures.append(exc)
    thread = threading.Thread(target=service, daemon=False)
    thread.start()
    try:
        yield observed
    finally:
        stop.set()
        thread.join(WAIT)
        assert not thread.is_alive(), "Bounded controller fixture did not stop"
        if failures:
            raise failures[0]


def echo(directory, value):
    publish(directory, response(value["id"], value["input"]))


class TestObservationClient:
    def test_opaque_id_and_finite_data_round_trip_use_only_numbered_directories(self, tmp_path):
        path, mailbox = descriptor(tmp_path)
        value = request("../../not-a-path/λ", {"items": [None, True, 12, 0.5, "雪"]})
        with controller(mailbox, [echo]) as observed:
            assert Call(path, value).result() == response(value["id"], value["input"])
        assert observed[0][2] == value and len(observed[0][1]) <= 4096
        assert [item.name for item in mailbox.iterdir()] == ["000001"]
        assert not (tmp_path / "not-a-path").exists()

    def test_sequential_requests_preserve_prior_mailbox_and_advance_sequence(self, tmp_path):
        path, mailbox = descriptor(tmp_path)
        with controller(mailbox, [echo, echo]) as observed:
            assert Call(path, request("first", 1)).result() == response("first", 1)
            first = {p.name: p.read_bytes() for p in (mailbox / "000001").iterdir()}
            assert Call(path, request("second", 2)).result() == response("second", 2)
        assert [row[0].name for row in observed] == ["000001", "000002"]
        assert {p.name: p.read_bytes() for p in (mailbox / "000001").iterdir()} == first

    def test_valid_domain_error_remains_data(self, tmp_path):
        path, mailbox = descriptor(tmp_path)
        expected = response(error={"type": "ValueError", "message": "unsupported input"})
        with controller(mailbox, [lambda directory, value: publish(directory, expected)]):
            assert Call(path, request()).result() == expected

    @pytest.mark.parametrize("value", [
        {}, {"schema": SCHEMA, "id": "id"},
        {"schema": SCHEMA, "id": "id", "input": None, "extra": True},
        {"schema": "other", "id": "id", "input": None},
        request(""), request(7), request(value=float("nan")), request(value=float("inf")),
        request(value={"not-json"}),
    ])
    def test_invalid_request_is_refused_before_any_mailbox_mutation(self, tmp_path, value):
        path, mailbox = descriptor(tmp_path)
        with pytest.raises(ValueError):
            Call(path, value).result()
        assert list(mailbox.iterdir()) == []

    def test_oversized_utf8_request_is_refused_before_any_mailbox_mutation(self, tmp_path):
        path, mailbox = descriptor(tmp_path, limit=256)
        with pytest.raises(ValueError):
            Call(path, request(value="雪" * 100)).result()
        assert list(mailbox.iterdir()) == []

    @pytest.mark.parametrize("extra", [0, 1])
    def test_response_byte_bound_includes_all_retained_bytes(self, tmp_path, extra):
        limit = 256
        path, mailbox = descriptor(tmp_path, limit=limit)
        expected = response(value="exact")
        raw = encoded(expected)
        raw += b" " * (limit + extra - len(raw))
        with controller(mailbox, [lambda directory, value: publish(directory, raw)]):
            call = Call(path, request())
            if extra:
                with pytest.raises(ValueError):
                    call.result()
            else:
                assert call.result() == expected
        assert (mailbox / "000001/response.json").read_bytes() == raw

    @pytest.mark.parametrize("raw", [
        b"", b"{", encoded(response()) + b"{}\n",
        encoded({**response(), "extra": True}),
        encoded({key: value for key, value in response().items() if key != "error"}),
        encoded(response("another-id")), encoded({**response(), "schema": "other"}),
        encoded(response(value=True, error={"type": "Error", "message": "conflict"})),
        encoded(response(error={"type": "Error"})),
        encoded(response(error={"type": "Error", "message": "bad", "extra": 1})),
        b'{"schema":"coding-trial-observation/v1","id":"opaque-id","result":NaN,"error":null}\n',
        b'{"schema":"coding-trial-observation/v1","id":"wrong","id":"opaque-id","result":null,"error":null}\n',
    ])
    def test_malformed_response_is_rejected_without_erasing_raw_evidence(self, tmp_path, raw):
        path, mailbox = descriptor(tmp_path)
        with controller(mailbox, [lambda directory, value: publish(directory, raw)]):
            with pytest.raises(ValueError):
                Call(path, request()).result()
        assert (mailbox / "000001/response.json").read_bytes() == raw

    @pytest.mark.parametrize("aliased", ["response.json", "response.ready"])
    @pytest.mark.parametrize("kind", ["symlink", "hardlink"])
    def test_response_files_and_markers_cannot_alias_other_files(self, tmp_path, aliased, kind):
        path, mailbox = descriptor(tmp_path)
        external = tmp_path / "untouched"
        external.write_bytes(encoded(response()) if aliased == "response.json" else b"")
        original = external.read_bytes()
        capability = tmp_path / "link-capability"
        if kind == "symlink":
            link(capability, str(external))
        else:
            try:
                os.link(external, capability)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"Hard links are unavailable: {exc}")
        capability.unlink()
        def send(directory, value):
            target = directory / aliased
            if aliased == "response.ready":
                (directory / "response.json").write_bytes(encoded(response()))
            if kind == "symlink":
                target.symlink_to(external)
            else:
                os.link(external, target)
            if aliased == "response.json":
                (directory / "response.ready").touch(exist_ok=False)
        with controller(mailbox, [send]):
            with pytest.raises(ValueError):
                Call(path, request()).result()
        assert external.read_bytes() == original

    @pytest.mark.parametrize("target", ["mailbox", "next-directory"])
    def test_mailbox_directory_alias_is_refused_without_following_it(self, tmp_path, target):
        path, mailbox = descriptor(tmp_path)
        other = tmp_path / "owned-elsewhere"
        other.mkdir()
        (other / "keep").write_bytes(b"keep")
        alias = mailbox if target == "mailbox" else mailbox / "000001"
        if target == "mailbox":
            mailbox.rmdir()
        link(alias, str(other), directory=True)
        with pytest.raises(ValueError):
            Call(path, request()).result()
        assert {p.name: p.read_bytes() for p in other.iterdir()} == {"keep": b"keep"}

    def test_response_file_is_not_ready_until_empty_marker_is_published(self, tmp_path):
        path, mailbox = descriptor(tmp_path)
        written, release = threading.Event(), threading.Event()
        def delayed_marker(directory, value):
            (directory / "response.json").write_bytes(encoded(response(value["id"], 1)))
            written.set()
            assert release.wait(WAIT), "Test never released the readiness marker"
            (directory / "response.ready").touch(exist_ok=False)
        with controller(mailbox, [delayed_marker]):
            call = Call(path, request())
            try:
                assert written.wait(WAIT)
                call.thread.join(0.05)
                assert call.thread.is_alive(), "Response content alone cannot authorize readiness"
            finally:
                release.set()
            assert call.result() == response(value=1)

    def test_one_outstanding_call_is_enforced_without_disturbing_first(self, tmp_path):
        path, mailbox = descriptor(tmp_path)
        entered, release = threading.Event(), threading.Event()
        def delayed(directory, value):
            entered.set()
            assert release.wait(WAIT), "Test never released first response"
            echo(directory, value)
        with controller(mailbox, [delayed]):
            first = Call(path, request("first", 1))
            try:
                assert entered.wait(WAIT)
                with pytest.raises(ValueError):
                    Call(path, request("second", 2)).result()
                assert not (mailbox / "000002").exists()
            finally:
                release.set()
            assert first.result() == response("first", 1)
