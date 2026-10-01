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
from . import child_process as _child_process
from . import output as _output
from . import policy as _policy
from . import session as _session
from . import storage as _storage

def bounded_output_tail(value: Any, limit: int | None = 1000) -> str:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    elif value is None:
        text = ""
    else:
        text = str(value)
    return text[-limit:] if limit is not None else text


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


def read_only_gate(name: str, command: list[str]) -> bool:
    if not command or name != command[0] or name not in _policy.LOCAL_GATE_COMMANDS:
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


def local_gate_available(name: str, command: list[str]) -> bool:
    context = _session._CLI_CONTEXT.get()
    return bool(context and context["local_gates"] and read_only_gate(name, command) and (
        context.get("gate_worker") or (
            current_thread() is main_thread() and hasattr(signal, "setitimer")
            and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        )
    ))


def run_local_gate(command: list[str], timeout_seconds: int | None) -> subprocess.CompletedProcess:
    context = _session._CLI_CONTEXT.get()
    assert context is not None
    stdout, stderr = io.StringIO(), io.StringIO()
    capture = _session._GATE_STREAMS.set((stdout, stderr))
    payload: dict[str, Any] = {}
    quality_capture = _session._QUALITY_CAPTURE.set(payload)
    previous_handler = signal.getsignal(signal.SIGALRM) if timeout_seconds is not None else None

    def expire(_signum: int, _frame: Any) -> None:
        raise subprocess.TimeoutExpired(command, timeout_seconds, stdout.getvalue(), stderr.getvalue())

    try:
        if timeout_seconds is not None:
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
        output = stdout.getvalue()
        # Quality handlers return their payload directly; other CLI output stays bounded
        # by the existing capture/error path. Mixing both outputs is invalid gate JSON.
        output = (json.dumps(payload) + output if output else payload) if payload else output
        return subprocess.CompletedProcess(command, returncode, output, stderr.getvalue())
    finally:
        if timeout_seconds is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
        _session._GATE_STREAMS.reset(capture)
        _session._QUALITY_CAPTURE.reset(quality_capture)


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
            context = _session._CLI_CONTEXT.get()
            result = run_local_gate(command, None if context.get("gate_worker") else timeout_seconds)
        else:
            result = subprocess.run(
                [sys.executable, str(Path(str(CLI_PATH)).resolve()), *command, "--full-output"],
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
                "stdout": bounded_output_tail(exc.stdout, None),
            },
            "stderr_tail": bounded_output_tail(exc.stderr, None),
        }
    payload: dict[str, Any]
    valid_json_object = False
    try:
        decoded = result.stdout if isinstance(result.stdout, dict) else (json.loads(result.stdout) if result.stdout.strip() else None)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, dict):
        payload = decoded
        valid_json_object = True
    else:
        payload = {"error_code": "invalid-gate-json", "stdout": result.stdout}
    context = _session._CLI_CONTEXT.get()
    if (
        local and result.returncode == (0 if payload.get("success") is True else 1)
        and context is not None and (inputs := context.get("score_inputs")) is not None
    ):
        inputs.record(name, payload)
    command_success = result.returncode == 0 and valid_json_object and payload.get("success", True) is not False
    return compact_gate_record(
        {
            "name": name,
            "required": required,
            "success": command_success,
            "returncode": result.returncode,
            "command": " ".join(command),
            "payload": payload,
            "stderr_tail": result.stderr,
        }
    )


def batch_score_inputs(jobs, context):
    """Share findings only for one complete, consistently configured plan batch."""
    names = [name for name, _command, _required in jobs]
    if names[-1] != "forge-score" or len(set(names)) != len(names):
        return None
    from . import scoring

    if not scoring.FlightScoreInputs.GATES.keys() <= set(names):
        return None
    # Invalid options must retain the normal per-gate diagnostic and exit behavior.
    capture = _session._GATE_STREAMS.set((io.StringIO(), io.StringIO()))
    try:
        commands = [context["parser"].parse_args(command) for _name, command, _required in jobs]
    except SystemExit:
        return None
    finally:
        _session._GATE_STREAMS.reset(capture)
    score = commands[-1]
    if score.depth not in {"standard", "deep"} or score.max_files != 8:
        return None
    planning_dir = _storage.resolve_path(score.planning_dir)
    for command in commands:
        if (not getattr(command, "planning_dir", None)
                or _storage.resolve_path(command.planning_dir) != planning_dir
                or getattr(command, "depth", score.depth) != score.depth
                or getattr(command, "profile", None) != score.profile
                or getattr(command, "max_files", 8) != 8):
            return None
    return scoring.FlightScoreInputs(planning_dir, score.depth, context["texts"])


def gate_batch_worker(_args: argparse.Namespace) -> int:
    """Run only approved read-only commands; the parent bounds the entire process."""
    try:
        jobs = json.loads(sys.stdin.read(1024 * 1024))
        if not isinstance(jobs, list) or not 1 <= len(jobs) <= 64 or any(
            not isinstance(job, list) or len(job) != 3 or not isinstance(job[0], str)
            or not isinstance(job[1], list) or not all(isinstance(arg, str) for arg in job[1])
            or not isinstance(job[2], bool) or not read_only_gate(job[0], job[1]) for job in jobs
        ):
            raise ValueError("Batch requires 1-64 approved read-only gate commands.")
    except (ValueError, TypeError) as exc:
        return _output.print_json({"success": False, "error": str(exc)}, 1)
    with _session.read_phase():
        context = _session._CLI_CONTEXT.get()
        context["gate_worker"] = True
        try:
            context["score_inputs"] = batch_score_inputs(jobs, context)
        except (OSError, ValueError, RuntimeError):
            # Let individual gates report unreadable inputs through their usual contract.
            context["score_inputs"] = None
        results = [run_internal_gate(name, command, required=required) for name, command, required in jobs]
    return _output.print_json({"success": True, "gates": results})


def run_gate_worker(jobs: list[tuple[str, list[str], bool]]) -> list[dict[str, Any]]:
    result = _child_process.execute(
        [sys.executable, str(Path(str(CLI_PATH)).resolve()), "gate-batch", "--full-output"],
        _storage.current_plugin_root(), prompt=json.dumps(jobs), timeout=120, output_limit=2 * 1024 * 1024,
    )
    if result["timed_out"]:
        payload = {"error_code": "gate-timeout", "timeout_seconds": 120, "timeout_scope": "batch"}
    else:
        try:
            gates = json.loads(result["stdout"])["gates"]
            complete = not (result.get("stdout_truncated") or result.get("stderr_truncated"))
            if result["returncode"] == 0 and complete and isinstance(gates, list) and len(gates) == len(jobs) and all(
                isinstance(gate, dict) and gate.get("name") == job[0] and gate.get("required") is job[2]
                and isinstance(gate.get("success"), bool) for gate, job in zip(gates, jobs)
            ):
                return gates
        except (ValueError, TypeError, KeyError):
            pass
        payload = {"error_code": "invalid-gate-batch", "stdout": bounded_output_tail(result["stdout"])}
    stderr, returncode = bounded_output_tail(result["stderr"]), result["returncode"] or 1
    return [{"name": name, "required": required, "success": False, "returncode": returncode,
             "command": " ".join(command), "payload": payload, "stderr_tail": stderr}
            for name, command, required in jobs]


def run_internal_gate_batch(jobs: list[tuple[str, list[str], bool]]) -> list[dict[str, Any]]:
    if not jobs:
        return []

    def run(job: tuple[str, list[str], bool]) -> dict[str, Any]:
        name, command, required = job
        return run_internal_gate(name, command, required=required)

    local = {index for index, (name, command, _) in enumerate(jobs) if local_gate_available(name, command)}
    if len(local) == len(jobs):
        return [run(job) for job in jobs]
    context = _session._CLI_CONTEXT.get()
    portable = {index for index, (name, command, _) in enumerate(jobs)
                if index not in local and context and context["local_gates"] and read_only_gate(name, command)}
    with ThreadPoolExecutor(max_workers=min(4, len(jobs) - len(local)), thread_name_prefix="forge-gate") as executor:
        worker = executor.submit(run_gate_worker, [jobs[index] for index in sorted(portable)]) if portable else None
        pending = {index: executor.submit(run, job) for index, job in enumerate(jobs) if index not in local | portable}
        results = {index: run(jobs[index]) for index in sorted(local)}
        if worker:
            results.update(zip(sorted(portable), worker.result()))
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
            "payload": payload,
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
