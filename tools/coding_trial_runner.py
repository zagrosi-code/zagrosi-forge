#!/usr/bin/env python3
"""Pinned Codex adapter; observe commands and usage without claiming phase tokens."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import subprocess
import sys
import time


def command_phase(command: str) -> str:
    if re.search(r"\b(plan-setup|lint-plan|lint-evidence)\b|--phase[ =]+plan\b", command):
        return "forge_planning"
    if re.search(r"\b(implement-setup|implement-record-section|implement-progress)\b|--phase[ =]+implement\b", command):
        return "forge_implementation"
    if re.search(r"\b(pytest|unittest|node --test|npm test|cargo test|go test)\b", command):
        return "verification"
    return "other"


class Telemetry:
    def __init__(self):
        self.usage = []
        self.events = 0
        self.started = {}
        self.failed = set()
        self.phases = defaultdict(Counter)
        self.command_retries = 0

    def observe(self, event: dict, elapsed: float):
        self.events += 1
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            self.usage.append(event["usage"])
        item = event.get("item", {})
        if item.get("type") != "command_execution":
            return
        identity = item.get("id")
        if event.get("type") == "item.started":
            self.started[identity] = elapsed
        elif event.get("type") == "item.completed":
            command = item.get("command", "")
            phase = self.phases[command_phase(command)]
            phase["commands"] += 1
            phase["observed_output_bytes"] += len(item.get("aggregated_output", "").encode())
            if identity in self.started:
                phase["observed_command_seconds"] += max(0, elapsed - self.started.pop(identity))
            else:
                phase["commands_without_start"] += 1
            if command in self.failed:
                self.command_retries += 1
            if item.get("exit_code") not in (None, 0):
                phase["failed_commands"] += 1
                self.failed.add(command)
            else:
                self.failed.discard(command)

    def summary(self) -> dict:
        totals = {}
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"):
            values = [row.get(key) for row in self.usage]
            totals[key] = sum(values) if values and all(type(v) is int and v >= 0 for v in values) else None
        input_tokens, cached = totals["input_tokens"], totals["cached_input_tokens"]
        totals["uncached_input_tokens"] = input_tokens - cached if input_tokens is not None and cached is not None and input_tokens >= cached else None
        return {"source": "codex exec JSONL", "usage": self.usage or None, "totals": totals,
                "events": self.events, "commands_by_phase": dict(self.phases),
                "observed_command_retries": self.command_retries, "api_retries": None,
                "limits": "Phases classify observed commands, not model thinking or phase tokens. Command durations are event receipt intervals and may overlap. Output byte counts cover emitted event text, which may be truncated by Codex. Retries count identical commands after a failed exit; API retries are unknown. Requested model identity is not an attested backend version."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True, choices=("low", "medium", "high", "xhigh"))
    parser.add_argument("--codex", default="codex")
    args = parser.parse_args()
    trial = Path.cwd().parent
    command = [args.codex, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--json",
               "--sandbox", "workspace-write", "--skip-git-repo-check", "--model", args.model,
               "-c", 'approval_policy="never"', "-c", f'model_reasoning_effort="{args.effort}"', "-"]
    version = subprocess.run([args.codex, "--version"], capture_output=True, text=True, timeout=10)
    telemetry = Telemetry()
    def persist(code=None):
        report = {**telemetry.summary(), "model": args.model, "effort": args.effort,
                  "command": command, "codex_version": version.stdout.strip(), "returncode": code}
        temporary = trial / "telemetry.tmp"
        temporary.write_text(json.dumps(report) + "\n")
        temporary.replace(trial / "telemetry.json")

    start = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    process.stdin.write(sys.stdin.read())
    process.stdin.close()
    with (trial / "agent-events.jsonl").open("w") as log:
        for line in process.stdout:
            log.write(line)
            log.flush()
            try:
                event = json.loads(line)
            except ValueError:
                continue
            telemetry.observe(event, time.monotonic() - start)
            item = event.get("item", {})
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                print(item.get("text", ""), flush=True)
            # Persist partial observations even when the supervising timeout kills this runner.
            persist()
    code = process.wait()
    persist(code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
