"""Forge gates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import current_thread, main_thread
from typing import Any
import argparse
import io
import json
import signal
import subprocess
import sys

from . import CLI_PATH
from . import policy as _policy
from . import session as _session
from . import storage as _storage

def sanitize_gate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(payload)
    for key in ("content", "stdout_tail", "stderr_tail"):
        if key in cleaned and isinstance(cleaned[key], str) and len(cleaned[key]) > 500:
            cleaned[key] = cleaned[key][:500] + "...[truncated]"
    if "findings" in cleaned and isinstance(cleaned["findings"], list) and len(cleaned["findings"]) > 12:
        cleaned["findings"] = cleaned["findings"][:12]
        cleaned["findings_truncated"] = True
    return cleaned


def bounded_output_tail(value: Any, limit: int = 1000) -> str:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    elif value is None:
        text = ""
    else:
        text = str(value)
    return text[-limit:]


def compact_gate_record(gate: dict[str, Any]) -> dict[str, Any]:
    compact = dict(gate)
    if compact.get("success") is True:
        if isinstance(compact.get("payload"), dict):
            payload = compact["payload"]
            compact["payload"] = {
                key: payload[key]
                for key in _policy.SUCCESS_GATE_PAYLOAD_KEYS
                if key in payload
            }
        compact.pop("command", None)
        compact.pop("returncode", None)
        if not compact.get("stderr_tail"):
            compact.pop("stderr_tail", None)
        if not compact.get("payload"):
            compact.pop("payload", None)
    return compact


def local_gate_available(name: str, command: list[str]) -> bool:
    context = _session._CLI_CONTEXT.get()
    if (
        context is None or not context["local_gates"] or not command
        or name != command[0] or name not in _policy.LOCAL_GATE_COMMANDS
        or current_thread() is not main_thread() or not hasattr(signal, "setitimer")
        or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0)
    ):
        return False
    # Only generated read-only options with absolute paths preserve child cwd semantics.
    options = iter(command[1:])
    for option in options:
        if option == "--strict":
            continue
        if option not in _policy.LOCAL_GATE_VALUE_OPTIONS:
            return False
        value = next(options, None)
        if value is None or value.startswith("--"):
            return False
        if option in {"--planning-dir", "--plugin-root", "--path", "--target-dir"} and not Path(value).is_absolute():
            return False
    return True


def run_local_gate(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    context = _session._CLI_CONTEXT.get()
    assert context is not None
    stdout, stderr = io.StringIO(), io.StringIO()
    capture = _session._GATE_STREAMS.set((stdout, stderr))
    previous_handler = signal.getsignal(signal.SIGALRM)

    def expire(_signum: int, _frame: Any) -> None:
        raise subprocess.TimeoutExpired(command, timeout_seconds, stdout.getvalue(), stderr.getvalue())

    try:
        signal.signal(signal.SIGALRM, expire)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        try:
            args = context["parser"].parse_args(command)
            returncode = args.func(args)
        except SystemExit as exc:
            returncode = exc.code if isinstance(exc.code, int) else 1
        except subprocess.TimeoutExpired:
            raise
        except Exception as exc:
            returncode = 1
            stderr.write(f"{type(exc).__name__}: {exc}\n")
        return subprocess.CompletedProcess(command, returncode, stdout.getvalue(), stderr.getvalue())
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        _session._GATE_STREAMS.reset(capture)


def run_internal_gate(
    name: str,
    command: list[str],
    *,
    required: bool = True,
    cwd: Path | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    local = cwd is None and timeout_seconds == 120 and local_gate_available(name, command)
    try:
        if local:
            result = run_local_gate(command, timeout_seconds)
        else:
            result = subprocess.run(
                [sys.executable, str(Path(str(CLI_PATH)).resolve()), *command],
                cwd=cwd or _storage.current_plugin_root(),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "required": required,
            "success": False,
            "returncode": 124,
            "command": " ".join(command),
            "payload": {
                "error_code": "gate-timeout",
                "timeout_seconds": timeout_seconds,
                "stdout": bounded_output_tail(exc.stdout),
            },
            "stderr_tail": bounded_output_tail(exc.stderr),
        }
    payload: dict[str, Any]
    valid_json_object = False
    try:
        decoded = json.loads(result.stdout) if result.stdout.strip() else None
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, dict):
        payload = decoded
        valid_json_object = True
    else:
        payload = {"error_code": "invalid-gate-json", "stdout": result.stdout[-1000:]}
    context = _session._CLI_CONTEXT.get()
    if (
        local and result.returncode == (0 if payload.get("success") is True else 1)
        and context is not None and (inputs := context.get("score_inputs")) is not None
    ):
        inputs.record(name, payload)
    command_success = result.returncode == 0 and valid_json_object and payload.get("success", True) is not False
    cleaned_payload = sanitize_gate_payload(payload)
    return compact_gate_record(
        {
            "name": name,
            "required": required,
            "success": command_success,
            "returncode": result.returncode,
            "command": " ".join(command),
            "payload": cleaned_payload,
            "stderr_tail": result.stderr[-1000:],
        }
    )


def run_internal_gate_batch(jobs: list[tuple[str, list[str], bool]]) -> list[dict[str, Any]]:
    if not jobs:
        return []

    def run(job: tuple[str, list[str], bool]) -> dict[str, Any]:
        name, command, required = job
        return run_internal_gate(name, command, required=required)

    local = {index for index, (name, command, _) in enumerate(jobs) if local_gate_available(name, command)}
    if len(local) == len(jobs):
        return [run(job) for job in jobs]
    with ThreadPoolExecutor(max_workers=min(4, len(jobs) - len(local)), thread_name_prefix="forge-gate") as executor:
        pending = {index: executor.submit(run, job) for index, job in enumerate(jobs) if index not in local}
        results = {index: run(jobs[index]) for index in sorted(local)}
        results.update((index, future.result()) for index, future in pending.items())
    return [results[index] for index in range(len(jobs))]


def direct_gate(name: str, success: bool, payload: dict[str, Any], *, required: bool = True) -> dict[str, Any]:
    return compact_gate_record(
        {
            "name": name,
            "required": required,
            "success": success,
            "returncode": 0 if success else 1,
            "command": "internal",
            "payload": sanitize_gate_payload(payload),
            "stderr_tail": "",
        }
    )


def effective_flight_mode(args: argparse.Namespace) -> str:
    mode = getattr(args, "flight_mode", None) or getattr(args, "flight", None) or "auto"
    if mode == "strict" or getattr(args, "strict", False):
        return "strict"
    return mode


def append_strict(command: list[str], mode: str) -> list[str]:
    if mode == "strict" and "--strict" not in command:
        return [*command, "--strict"]
    return command


def flight_payload(
    *,
    phase: str,
    stage: str,
    mode: str,
    gates: list[dict[str, Any]],
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gates = [compact_gate_record(gate) for gate in gates]
    if mode == "off":
        return {
            "success": True,
            "phase": phase,
            "stage": stage,
            "mode": mode,
            "gates": [],
            "blocking_gates": [],
        }
    blocking = [
        gate["name"]
        for gate in gates
        if gate.get("required", True) and not gate.get("success", False) and mode != "advisory"
    ]
    payload: dict[str, Any] = {
        "success": not blocking,
        "phase": phase,
        "stage": stage,
        "mode": mode,
        "gates": gates,
        "blocking_gates": blocking,
    }
    if extras:
        payload.update(extras)
    return payload
