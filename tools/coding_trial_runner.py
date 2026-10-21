#!/usr/bin/env python3
"""Pinned native writer adapters; observed usage is not proof of accepted work."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

# Claude Code warns and ignores an unknown --effort, so the harness rejects it before launch.
EFFORTS = {"codex": ("low", "medium", "high", "xhigh"), "claude": ("low", "medium", "high", "xhigh", "max")}
EFFORT_CHOICES = tuple(dict.fromkeys(effort for efforts in EFFORTS.values() for effort in efforts))
# One list for --tools and --allowedTools. Claude Code 2.1.304 keeps TodoWrite off by default and drops
# unknown or disabled names silently, so telemetry compares the init event's tools with this list.
CLAUDE_TOOLS = "Read,Edit,Write,Bash,Glob,Grep,Skill,Agent,TaskCreate,TaskGet,TaskList,TaskUpdate"
# The init event reports the Agent tool as Task.
REQUESTED_CLAUDE_TOOLS = frozenset("Task" if name == "Agent" else name for name in CLAUDE_TOOLS.split(","))
# A writer started from inside Claude Code would inherit that session's variables: its session and messaging
# ids, entrypoint and plugin data, plus the effort and model aliases from its settings. Only credentials and
# the config location pass through.
CLAUDE_PASSTHROUGH = frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                                "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR"})


def require_effort(host: str, effort: str) -> str:
    if effort not in EFFORTS[host]:
        raise ValueError(f"{host} does not accept {effort!r} effort; choose one of {', '.join(EFFORTS[host])}")
    return effort


def writer_environment(host: str, environ) -> tuple[dict, list[str]]:
    """The writer's environment and the inherited names it drops.

    Claude auto-memory is on by default and keyed by the workspace path, so a second session in the same
    workspace would read what the first one saved; it stays off for every Claude writer.
    """
    if host != "claude":
        return dict(environ), []
    removed = sorted(name for name in environ
                     if name.startswith(("CLAUDE", "ANTHROPIC_")) and name not in CLAUDE_PASSTHROUGH)
    environment = {name: value for name, value in environ.items() if name not in removed}
    environment["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    return environment, removed


def input_totals(uncached, cached, written) -> dict:
    """Split input usage; a missing or invalid count stays unknown instead of zero."""
    valid = [type(value) is int and value >= 0 for value in (uncached, cached, written)]
    return {"input_tokens": uncached + cached + written if all(valid) else None,
            "cached_input_tokens": cached if valid[1] else None,
            "cache_creation_input_tokens": written if valid[2] else None,
            "uncached_input_tokens": uncached if valid[0] else None}


def model_usage_totals(model_usage) -> dict:
    """Sum the result's per-model usage, which also counts subagents; invalid values stay unknown."""
    rows = list(model_usage.values()) if isinstance(model_usage, dict) else []

    def total(key):
        values = [row.get(key) if isinstance(row, dict) else None for row in rows]
        return sum(values) if values and all(type(value) is int and value >= 0 for value in values) else None

    return {**input_totals(*map(total, ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"))),
            "output_tokens": total("outputTokens")}


def qualify_native_loading(suite, suite_root, task_id, arm_id, roots, evidence_dir, *, auth_file=None):
    """Explicit native bootstrap; keep suite dependencies out of legacy imports."""
    from coding_trial_loading import qualify_native_loading as qualify
    return qualify(suite, suite_root, task_id, arm_id, roots, evidence_dir, auth_file=auth_file)


def run_suite_writer(suite: dict, suite_root: Path, task_id: str, arm_id: str,
                     roots: dict, evidence_dir: Path, *, auth_file=None) -> dict:
    """Run a suite writer with explicit inputs and controller-owned evidence."""
    if (type(suite) is dict and type(suite.get("host")) is dict
            and suite["host"].get("adapter") == "codex"):
        from coding_trial_loading import run_native_writer
        return run_native_writer(suite, suite_root, task_id, arm_id, roots, evidence_dir, auth_file=auth_file)
    import stat
    from coding_trial_inventory import fingerprint
    from coding_trial_isolation import derive_layout, execute_isolated, fresh_evidence_path, suite_context
    from coding_trial_process import execute
    from coding_trial_qualification import read_bytes

    suite, paths = suite_context(suite, suite_root, task_id, arm_id, roots)
    evidence_dir = fresh_evidence_path(evidence_dir, paths)
    host = suite["host"]
    task = suite["tasks"][task_id]
    arm = suite["arms"][arm_id]
    if paths["native_runtime"] is not None or auth_file is not None:
        raise ValueError("suite-invalid: Fixtures cannot receive native runtime or credentials")
    layout = None if host["isolation"] is None else derive_layout(
        suite, suite_root, task_id, arm_id, roots, role="writer")
    tokens = {"{python}": host["executable"],
              "{workspace}": str(paths["workspace"]) if layout is None else "/workspace",
              "{product}": str(paths["product"]) if layout is None else "/product"}
    command = [tokens.get(argument, argument) for argument in host["fixture_argv"]]
    environment = {**host["environment"], "PYTHONDONTWRITEBYTECODE": "1", "PYTHONOPTIMIZE": "0"}
    resource_root = Path(suite_root).resolve(strict=True)
    entry = read_bytes(resource_root, arm["entry"]).decode("utf-8")
    entry = re.sub(r"\{(?:workspace|product)\}", lambda match: tokens[match.group()], entry)
    materials = [entry, read_bytes(resource_root, task["brief"]).decode("utf-8")]
    if task["clarifications"] is not None:
        materials.append(read_bytes(resource_root, task["clarifications"]).decode("utf-8"))
    try:
        evidence_dir.mkdir()
    except FileExistsError as exc:
        raise ValueError(f"input-exists: {evidence_dir}") from exc
    before = evidence_dir.lstat()
    identity = (before.st_dev, before.st_ino, before.st_mode)
    if not stat.S_ISDIR(before.st_mode) or evidence_dir.resolve() != evidence_dir:
        raise ValueError(f"suite-invalid: Evidence directory changed before execution: {evidence_dir}")
    directory_fd = None
    if os.name == "posix":
        directory_fd = os.open(evidence_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened = os.fstat(directory_fd)
        if (opened.st_dev, opened.st_ino, opened.st_mode) != identity:
            os.close(directory_fd)
            raise ValueError(f"suite-invalid: Evidence directory changed before opening: {evidence_dir}")
    try:
        if layout is None:
            process = execute(command, paths["workspace"], prompt="\n\n".join(materials), timeout=900,
                              env=environment, output_limit=8388608, inherit_env=False)
            isolation = {"adapter": None, "status": "unmeasured", "qualification": None, "container_id": None,
                         "lifecycle": {"status": "unverified", "reason": "Host fixture has no whole-process isolation", "evidence": []}}
        else:
            result = execute_isolated(suite, suite_root, layout, command, roots=roots, cwd="/workspace",
                                      env=environment, prompt="\n\n".join(materials), timeout=900, output_limit=8388608,
                                      evidence_dir=evidence_dir / "isolation")
            process, isolation = result["process"], result["isolation"]
        record = {
            "schema": "coding-trial-writer/v1", "suite_sha256": fingerprint(suite), "task": task_id, "arm": arm_id,
            "budget": {"timeout_seconds": 900, "output_bytes": 8388608}, "command": command,
            "environment": environment, "process": process, "telemetry": None,
            "isolation": isolation,
        }
        current = evidence_dir.lstat()
        if evidence_dir.resolve() != evidence_dir or (current.st_dev, current.st_ino, current.st_mode) != identity:
            raise ValueError(f"suite-invalid: Evidence directory changed during execution: {evidence_dir}")
        try:
            if directory_fd is None:
                stream = (evidence_dir / "writer.json").open("x", encoding="utf-8")
            else:
                handle = os.open("writer.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory_fd)
                stream = os.fdopen(handle, "w", encoding="utf-8")
            with stream:
                stream.write(json.dumps(record, indent=2) + "\n")
        except FileExistsError as exc:
            raise ValueError(f"input-exists: {evidence_dir / 'writer.json'}") from exc
        current = evidence_dir.lstat()
        if evidence_dir.resolve() != evidence_dir or (current.st_dev, current.st_ino, current.st_mode) != identity:
            raise ValueError(f"suite-invalid: Evidence directory changed during persistence: {evidence_dir}")
        return record
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


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


class ClaudeTelemetry(Telemetry):
    def __init__(self):
        super().__init__()
        self.commands = {}
        self.models = set()
        self.result = None
        self.tools = None

    def observe(self, event: dict, elapsed: float):
        self.events += 1
        message = event.get("message")
        message = message if isinstance(message, dict) else {}
        if isinstance(message.get("model"), str):
            self.models.add(message["model"])
        if event.get("type") == "system" and event.get("subtype") == "init" and isinstance(event.get("tools"), list):
            self.tools = sorted({name for name in event["tools"] if isinstance(name, str)})
        if event.get("type") == "result":
            self.result = event
            self.usage = [event["usage"]] if isinstance(event.get("usage"), dict) else []
        for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                command = block.get("input")
                command = command.get("command") if isinstance(command, dict) else None
                if isinstance(block.get("id"), str) and isinstance(command, str):
                    self.commands[block["id"]] = (command, elapsed)
            elif block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str) and block["tool_use_id"] in self.commands:
                command, started = self.commands.pop(block["tool_use_id"])
                phase = self.phases[command_phase(command)]
                phase["commands"] += 1
                phase["observed_command_seconds"] += max(0, elapsed - started)
                phase["observed_output_bytes"] += len(json.dumps(block.get("content", "")).encode())
                if command in self.failed:
                    self.command_retries += 1
                if block.get("is_error"):
                    phase["failed_commands"] += 1
                    self.failed.add(command)
                else:
                    self.failed.discard(command)

    def summary(self) -> dict:
        summary = super().summary()
        usage = self.usage[-1] if self.usage else {}
        totals = summary["totals"]
        totals.update(input_totals(*(usage.get(key) for key in
                                     ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))))
        top_level = dict(totals)
        result = self.result or {}
        cost = result.get("total_cost_usd")
        model_usage = result.get("modelUsage") if isinstance(result.get("modelUsage"), dict) and result["modelUsage"] else None
        if model_usage:
            totals.update(model_usage_totals(model_usage))
        stats = result.get("subagent_stats")
        return {**summary, "source": "claude print stream-json", "observed_models": sorted(self.models),
                "reported_cost_usd": cost if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0 else None,
                "cost_basis": "CLI estimate, not subscription billing", "result_received": self.result is not None,
                "permission_denials": result.get("permission_denials"),
                "totals_source": None if self.result is None else "modelUsage" if model_usage else "result usage",
                "top_level_totals": top_level, "model_usage": model_usage,
                "subagent_stats": stats if isinstance(stats, dict) else None, "registered_tools": self.tools,
                "missing_tools": None if self.tools is None else sorted(REQUESTED_CLAUDE_TOOLS - set(self.tools)),
                "limits": "Final result usage only; assistant usage is not added twice. Totals use the result's per-model usage, which includes subagents, when the CLI reports it; top_level_totals keep the result usage of the top-level loop. Input totals include uncached, cache reads and cache writes. Command timing uses received tool events. Missing fields remain unknown. CLI costs are estimates, not charges; model names are reported, not independently attested."}


def writer_command(host: str, executable: str, model: str, effort: str, record: dict) -> list[str]:
    require_effort(host, effort)
    if host == "codex":
        return [executable, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--json",
                "--sandbox", "workspace-write", "--skip-git-repo-check", "--model", model,
                "-c", 'approval_policy="never"', "-c", f'model_reasoning_effort="{effort}"', "-"]
    command = [executable, "--print", "--output-format", "stream-json", "--verbose", "--model", model,
               "--effort", effort, "--restricted", "--setting-sources", "", "--no-session-persistence", "--strict-mcp-config",
               "--mcp-config", '{"mcpServers":{}}', "--permission-mode", "acceptEdits",
               "--permission-prompts", "none", "--tools", CLAUDE_TOOLS, "--allowedTools", CLAUDE_TOOLS]
    if record.get("plugin_root") and not record.get("plain_agent"):
        command.extend(["--plugin-dir", record["plugin_root"]])
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("codex", "claude"), default="codex")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True, choices=EFFORT_CHOICES)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--claude", default="claude")
    args = parser.parse_args()
    trial = Path.cwd().parent
    record_path = trial / "trial.json"
    record = json.loads(record_path.read_text()) if record_path.exists() else {}
    executable = args.claude if args.host == "claude" else args.codex
    try:
        command = writer_command(args.host, executable, args.model, args.effort, record)
    except ValueError as exc:
        parser.error(str(exc))
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
    environment, removed = writer_environment(args.host, os.environ)
    telemetry = ClaudeTelemetry() if args.host == "claude" else Telemetry()
    def persist(code=None):
        report = {**telemetry.summary(), "host": args.host, "model": args.model, "effort": args.effort,
                  "command": command, args.host + "_version": version.stdout.strip(), "returncode": code,
                  "removed_environment": removed}
        temporary = trial / "telemetry.tmp"
        temporary.write_text(json.dumps(report) + "\n")
        temporary.replace(trial / "telemetry.json")

    start = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=environment)
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
            if not isinstance(event, dict):
                continue
            telemetry.observe(event, time.monotonic() - start)
            item = event.get("item") or {}
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                print(item.get("text", ""), flush=True)
            elif args.host == "claude" and event.get("type") == "result":
                print(event.get("result", ""), flush=True)
            persist()
    code = process.wait()
    if args.host == "claude" and code == 0 and (telemetry.result is None or telemetry.result.get("is_error")):
        code = 1
    persist(code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
