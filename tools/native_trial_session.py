"""Bounded native model sessions with observed checkpoint interruption and retained streams."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from coding_trial_process import _execute
from coding_trial_runner import ClaudeTelemetry, Telemetry, writer_command
from native_plugin_smoke import live_flags

_processes = importlib.import_module(_execute.__module__)
EXPECTED_SKILLS = {"zagrosi-forge:" + name for name in
                   ("zagrosi-forge", "zagrosi-cleanup", "zagrosi-plan", "zagrosi-project", "zagrosi-implement")}
PROGRESS = ".planning/implementation/forge-progress.json"


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def red_event(workspace):
    progress = read_json(workspace / PROGRESS, {})
    for event in progress.get("events", []) if isinstance(progress, dict) else []:
        if isinstance(event, dict) and event.get("stage") == "red" and isinstance(event.get("snapshot"), dict):
            return event
    return None


def native_command(host, model, effort, installation):
    command = writer_command(host, host, model, effort, {"plugin_root": installation[host + "_plugin"]})
    if host == "codex":
        # Match app-server discovery's native configuration layers for this acceptance test.
        command.remove("--ignore-user-config")
        command[-1:-1] = live_flags(installation)
    return command


def run_session(command, workspace, destination, prompt, *, host, plugin_root, timeout, interrupt=False, output_limit=16_000_000):
    """Kill only after a new persisted checkpoint; callers independently verify its red behavior."""
    if not 0 < timeout <= 3600 or not 0 < output_limit <= 64_000_000:
        raise ValueError("A bounded session timeout and output budget are required")
    if interrupt and red_event(workspace):
        raise ValueError("Interruption requires a new model-written checkpoint")
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "prompt.md").write_text(prompt)
    version = _execute([command[0], "--version"], workspace, timeout=10)
    telemetry = ClaudeTelemetry() if host == "claude" else Telemetry()
    evidence = {"registered_skills": [], "plugin_actions": [], "invalid_events": 0, "stdout_bytes": 0,
                "partial_event": False, "turn_completed": False, "terminal_error": None, "reader_error": None, "final_text": ""}
    stderr = _processes._Tail(12000)
    start = time.monotonic()
    stop, checkpoint, termination_error = None, None, None

    def observe(event):
        telemetry.observe(event, time.monotonic() - start)
        if event.get("type") in {"error", "turn.failed"} or (event.get("type") == "result" and event.get("is_error")):
            evidence["terminal_error"] = event["type"]
        terminal = "result" if host == "claude" else "turn.completed"
        evidence["turn_completed"] |= event.get("type") == terminal
        if host == "claude" and event.get("type") == "result":
            evidence["final_text"] = event.get("result") if isinstance(event.get("result"), str) else ""
        if event.get("type") == "system" and event.get("subtype") == "init":
            evidence["registered_skills"] = sorted({name for key in ("skills", "slash_commands")
                for name in event.get(key, []) if isinstance(name, str)} & EXPECTED_SKILLS)
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            evidence["final_text"] = item.get("text") if isinstance(item.get("text"), str) else ""
        if item.get("type") == "command_execution" and str(plugin_root) in item.get("command", ""):
            evidence["plugin_actions"].append({"kind": "command", "id": item.get("id")})
        message = event.get("message") or {}
        for block in message.get("content", []) if isinstance(message, dict) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            values = block.get("input") or {}
            if not isinstance(values, dict):
                continue
            if block.get("name") == "Skill" and values.get("skill") in EXPECTED_SKILLS:
                evidence["plugin_actions"].append({"kind": "skill", "name": values["skill"]})
            elif str(plugin_root) in str(values):
                evidence["plugin_actions"].append({"kind": "tool", "name": block.get("name")})

    def collect(stream):
        with (destination / "events.jsonl").open("wb") as log:
            for line in iter(lambda: stream.readline(1_000_000), b""):
                room = max(0, output_limit - evidence["stdout_bytes"])
                log.write(line[:room]); log.flush()
                evidence["stdout_bytes"] += len(line)
                try:
                    event = json.loads(line)
                except ValueError:
                    if line.endswith(b"\n"):
                        evidence["invalid_events"] += 1
                    else:
                        evidence["partial_event"] = True
                    continue
                if not isinstance(event, dict):
                    evidence["invalid_events"] += 1
                    continue
                if not isinstance(event.get("item", {}), dict):
                    evidence["invalid_events"] += 1
                    continue
                observe(event)

    def read_events(stream):
        try:
            collect(stream)
        except Exception as exc:
            evidence["reader_error"] = type(exc).__name__
        finally:
            stream.close()

    with tempfile.TemporaryFile() as stdin:
        stdin.write(prompt.encode()); stdin.seek(0)
        process = subprocess.Popen(command, cwd=workspace, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=os.name == "posix", env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONOPTIMIZE": "0"})
        threads = [threading.Thread(target=read_events, args=(process.stdout,), daemon=True),
                   threading.Thread(target=stderr.drain, args=(process.stderr,), daemon=True)]
        for thread in threads:
            thread.start()
        try:
            while process.poll() is None or any(thread.is_alive() for thread in threads):
                if interrupt and process.poll() is None and (checkpoint := red_event(workspace)):
                    stop = "checkpoint"
                elif time.monotonic() - start >= timeout:
                    stop = "timeout"
                elif evidence["stdout_bytes"] > output_limit:
                    stop = "output_limit"
                elif evidence["reader_error"] or evidence["terminal_error"]:
                    stop = "invalid_stream"
                if stop:
                    termination_error = _processes._terminate(process)
                    break
                time.sleep(.01)
            if not stop and os.name == "posix":
                termination_error = _processes._terminate(process)
        except BaseException:
            _processes._terminate(process)
            raise
        finally:
            for thread in threads:
                thread.join(timeout=1)
        if any(thread.is_alive() for thread in threads):
            termination_error = "Session output streams did not close after cleanup"
    stderr_text, stderr_bytes = stderr.snapshot()
    exceeded = evidence["stdout_bytes"] > output_limit
    (destination / "stderr.txt").write_text(stderr_text)
    report = {"host": host, "command": command, "cli_version": version["stdout"].strip() or None, "seconds": round(time.monotonic() - start, 3),
              "returncode": process.returncode, "stop": stop, "termination_error": termination_error,
              "checkpoint": checkpoint, "stderr_bytes": stderr_bytes, "output_exceeded": exceeded,
              **evidence, "telemetry": telemetry.summary()}
    report["success"] = (process.returncode == 0 and not stop and not termination_error
                         and evidence["turn_completed"] and not evidence["invalid_events"]
                         and not evidence["reader_error"] and not evidence["terminal_error"] and not evidence["partial_event"] and not exceeded)
    if host == "claude":
        report["success"] &= telemetry.result is not None and not telemetry.result.get("is_error")
    (destination / "session.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
