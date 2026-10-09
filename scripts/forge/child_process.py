"""Bounded subprocess output and deadline cleanup shared by checks and providers.

POSIX owns the process group. Windows uses taskkill while the leader is present;
descendants which detach or outlive that leader are not guaranteed to be stopped.
This executor is not an OS sandbox.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

OUTPUT_LIMIT = 12000


class _Tail:
    def __init__(self, limit):
        self.limit = limit
        self.data = bytearray()
        self.size = 0
        self.lock = threading.Lock()

    def drain(self, stream):
        try:
            while chunk := stream.read1(65536):
                with self.lock:
                    self.size += len(chunk)
                    self.data.extend(chunk)
                    del self.data[:-self.limit]
        finally:
            stream.close()

    def snapshot(self):
        with self.lock:
            return self.data.decode("utf-8", errors="replace"), self.size


def _terminate(process):
    """Kill the POSIX group, or ask Windows to terminate the process subtree."""
    error = None
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif os.name == "nt":
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"
            stopped = subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            if stopped.returncode:
                error = "Windows could not confirm process-tree termination"
                if process.poll() is None:
                    process.kill()
        else:
            process.kill()
            error = "Process-tree termination is unsupported on this platform"
    except ProcessLookupError:
        pass
    except (OSError, subprocess.TimeoutExpired) as exc:
        error = str(exc)
        if process.poll() is None:
            try:
                process.kill()
            except OSError as kill_error:
                error += "; leader termination failed: " + str(kill_error)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        error = "Process leader did not exit after termination"
    return error


def _cancelled_result(start):
    return {"returncode": 130, "seconds": round(time.monotonic() - start, 3),
            "cancelled": True, "timed_out": False, "stdout": "", "stderr": "",
            "stdout_bytes": 0, "stderr_bytes": 0, "stdout_truncated": False, "stderr_truncated": False}


def execute(argv: list[str], workspace: Path, *, prompt: str | None = None,
            timeout: float = 60, env: dict[str, str] | None = None,
            output_limit: int = OUTPUT_LIMIT, inherit_env: bool = True,
            cancel_event: threading.Event | None = None) -> dict:
    if not 0 < timeout <= 86400 or not 0 < output_limit <= 8 * 1024 * 1024:
        raise ValueError("A bounded positive timeout and output limit are required")
    start = time.monotonic()
    if cancel_event is not None and cancel_event.is_set():
        return _cancelled_result(start)
    env = {**os.environ, **(env or {})} if inherit_env or env is None else env
    tails = {name: _Tail(output_limit) for name in ("stdout", "stderr")}
    timed_out = False
    cancelled = False
    termination_error = None
    with tempfile.TemporaryFile() as stdin:
        stdin.write((prompt or "").encode("utf-8"))
        stdin.seek(0)
        if cancel_event is not None and cancel_event.is_set():
            return _cancelled_result(start)
        try:
            process = subprocess.Popen(argv, cwd=workspace, stdin=stdin, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=env,
                                       start_new_session=os.name == "posix")
        except OSError as exc:
            result = {"returncode": 127, "seconds": round(time.monotonic() - start, 3),
                      "stdout": "", "stderr": str(exc), "timed_out": False}
            if cancel_event is not None:
                result["cancelled"] = False
            return result
        readers = [threading.Thread(target=tails[name].drain, args=(getattr(process, name),),
                                    daemon=True) for name in tails]
        for reader in readers:
            reader.start()
        try:
            # Pipes inherited by descendants keep the deadline active after leader exit.
            while process.poll() is None or any(reader.is_alive() for reader in readers):
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    timed_out = True
                    termination_error = _terminate(process)
                    break
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    termination_error = _terminate(process)
                    break
                time.sleep(min(remaining, .01))
            if not timed_out and not cancelled and os.name == "posix":
                # A child can close or redirect both streams and outlive the leader.
                # Close the owned group before returning, even when its leader passed.
                termination_error = _terminate(process)
        except BaseException:
            _terminate(process)
            raise
        finally:
            for reader in readers:
                reader.join(timeout=1)
        if any(reader.is_alive() for reader in readers):
            termination_error = "Output pipes remain open; detached descendants may have escaped cleanup"
        returncode = 124 if timed_out else 130 if cancelled else process.returncode
        if termination_error and returncode == 0:
            returncode = 125
        result = {"returncode": returncode,
                  "seconds": round(time.monotonic() - start, 3), "timed_out": timed_out}
        if cancel_event is not None:
            result["cancelled"] = cancelled
        for name, tail in tails.items():
            result[name], result[name + "_bytes"] = tail.snapshot()
            result[name + "_truncated"] = result[name + "_bytes"] > output_limit
        if termination_error:
            result["termination_error"] = termination_error
        return result
