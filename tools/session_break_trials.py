#!/usr/bin/env python3
"""Session-break trials: work until a break after section 2, then resume in a fresh session.

Implements examples/evals/session-break-protocol.md. Model calls occur only in `run`, which
attempts each scheduled row once; after fixing a shared failure, prepare a new directory.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import coding_trials  # noqa: E402
from coding_trial_comparison import packets, reviews  # noqa: E402
from coding_trial_evidence import evaluator_files, files, plugin_files  # noqa: E402
from coding_trial_outcomes import accepted_outcomes, known_total  # noqa: E402
from coding_trial_process import execute  # noqa: E402
from coding_trial_runner import EFFORT_CHOICES, require_effort  # noqa: E402
from session_break_checks import CLAUSES, derived_brief  # noqa: E402
from trial_matrix import read, schedule  # noqa: E402

PROTOCOL = "session-break/v1"
ARMS = ("forge-lean", "notes", "plain")
CHECKS = ROOT / "tools/session_break_checks.py"
POLL_SECONDS = 0.5
NOTES = ("Keep `.planning/NOTES.md` as your durable working notes: the plan, each section's status, decisions and "
         "clarifications, verification results and the next step. Update it whenever one of these changes. When "
         "continuing work, read it before acting.")
FIRST_SESSION = "In this session, complete sections 1 and 2 only, then stop."
SECOND_SESSION = ("An earlier session that you cannot see worked on this task in this workspace. Complete every "
                  "remaining section and the task's acceptance requirements.")
PLANS = {
    "godfile": (
        "Characterize the current public behavior of `dispatch` with regression tests: validation, totals and "
        "shipping, rendering and CSV/JSON, file access, and order transitions.",
        "Move validation and money/shipping calculations into focused modules; `dispatch` keeps every public name.",
        "Move rendering, serialization and file access into focused modules.",
        "Move `OrderBook` workflow state and transitions into its own module.",
        "Add `summarize_orders(orders)` and `OrderBook.summary()` with tests.",
    ),
    "import-preview": (
        "Characterize `import_orders`, pricing and receipts, including errors, with regression tests.",
        "Consolidate the CSV parsing and validation duplicated in `orders` and `receipts` so the preview can reuse "
        "it; keep public behavior.",
        "Add `preview_import` counts: `order_count`, `item_count` and `sku_quantities`.",
        "Add `subtotal`, `tax` and `total`.",
        "Match `import_orders` errors, keep inputs unchanged and handle header-only batches.",
    ),
}
TOKENS = ("input_tokens", "cached_input_tokens", "cache_creation_input_tokens", "uncached_input_tokens",
          "output_tokens", "reasoning_output_tokens")
EDIT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit", "Write"}
HIDDEN = ("session-1.md", "session-1-events.jsonl", "session-1-telemetry.json", "checkpoint.json", "checkpoint")


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def last_json(text: str):
    lines = [line for line in (text or "").splitlines() if line.strip()]
    try:
        return json.loads(lines[-1]) if lines else None
    except ValueError:
        return None


def session_prompt(arm: str, prompt: str, case: str, session: int, clauses=()) -> str:
    """Standing instruction, case prompt and shared plan; clarifications reach session 1 only."""
    parts = [NOTES] if arm == "notes" else []
    parts += [prompt.rstrip(), "Section plan:\n" + "\n".join(f"{number}. {text}" for number, text
                                                             in enumerate(PLANS[case], 1))]
    if session == 1 and clauses:
        parts.append("The user's answers to earlier questions; they are binding and not in the brief:\n"
                     + "\n".join("- " + clause["clarification"] for clause in clauses))
    parts.append(FIRST_SESSION if session == 1 else SECOND_SESSION)
    return "\n\n".join(parts) + "\n"


def identity(plugin_root: Path, cases) -> dict:
    """Evaluator, plugin and fixture bytes that every attempt must share."""
    evaluator = {}
    for case in cases:
        oracle = ROOT / coding_trials.CASES[case]["oracle"]
        evaluator.update(evaluator_files(ROOT, oracle, coding_trials.PACK / "cases.json"))
    for path in (Path(__file__).resolve(), CHECKS):
        evaluator[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"evaluator": evaluator, "plugin": plugin_files(plugin_root),
            "fixtures": {case: files(coding_trials.PACK / coding_trials.CASES[case]["fixture"]) for case in cases}}


def cli_versions(hosts, executables: dict) -> dict:
    versions = {}
    for host in sorted(hosts):
        process = execute([executables[host], "--version"], ROOT, timeout=15)
        if process["returncode"] != 0 or not process["stdout"].strip():
            raise ValueError(f"Cannot read the {host} CLI version from {executables[host]!r}")
        versions[host] = process["stdout"].strip()
    return versions


def runner(host: str, model: str, effort: str, executables: dict) -> list[str]:
    return [sys.executable, str(ROOT / "tools/coding_trial_runner.py"), "--host", host, "--model", model,
            "--effort", effort, "--codex", executables["codex"], "--claude", executables["claude"]]


def run_oracle(workspace: Path, case: str, *, compatibility: bool, expected: int | None) -> dict:
    """Run the unchanged case oracle; a premature exit or another assertion count fails."""
    argv = [sys.executable, "-B", str(ROOT / coding_trials.CASES[case]["oracle"]), str(workspace), case]
    process = execute(argv + (["--compatibility-only"] if compatibility else []), workspace, timeout=300)
    report = last_json(process["stdout"])
    count = report.get("assertions") if isinstance(report, dict) and report.get("case") == case else None
    passed = process["returncode"] == 0 and type(count) is int and expected in (None, count)
    errors = [line for line in (process["stderr"] or "").splitlines() if line.strip()]
    return {"passed": passed, "assertions": count, "returncode": process["returncode"],
            "error": None if passed else (errors[-1][:300] if errors else "no oracle report")}


def run_checks(workspace: Path, operation: str, case: str | None = None):
    process = execute([sys.executable, "-B", str(CHECKS), operation, str(workspace), *([case] if case else [])],
                      workspace, timeout=300)
    report = last_json(process["stdout"])
    return report if process["returncode"] == 0 and isinstance(report, dict) else None


@contextlib.contextmanager
def scratch_copy(workspace: Path, tests: Path | None = None):
    """Run candidate code on a disposable copy so the frozen candidate and checkpoint stay unchanged."""
    with tempfile.TemporaryDirectory(prefix="session-break-") as temporary:
        copy = Path(temporary) / "workspace"
        shutil.copytree(workspace, copy, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        if tests is not None:
            shutil.rmtree(copy / "tests", ignore_errors=True)
            if tests.is_dir():
                shutil.copytree(tests, copy / "tests", symlinks=True)
        yield copy


def observe(workspace: Path, case: str, compatibility_assertions: int) -> dict:
    with scratch_copy(workspace) as copy:
        return {"tests": run_checks(copy, "tests"), "probes": run_checks(copy, "probe", case),
                "compatibility": run_oracle(copy, case, compatibility=True, expected=compatibility_assertions),
                "oracle": run_oracle(copy, case, compatibility=False,
                                     expected=coding_trials.CASES[case]["assertions"])}


def planning_stats(workspace: Path) -> dict:
    sizes = []
    for folder, _, names in os.walk(workspace / ".planning"):
        sizes += [os.lstat(os.path.join(folder, name)).st_size for name in names]
    return {"files": len(sizes), "bytes": sum(sizes)}


def code_snapshot(workspace: Path) -> dict:
    return {f"{folder}/{name}": digest for folder in ("src", "tests") if (workspace / folder).is_dir()
            for name, digest in files(workspace / folder).items()}


def changed_files(before: dict, after: dict) -> list[str]:
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


class FirstEdit:
    """Poll src/ and tests/ for the first change; the log size then locates it among the session's events."""

    def __init__(self, workspace: Path, events: Path):
        self.workspace, self.events = workspace, events
        self.baseline = code_snapshot(workspace)
        self.found = None
        self.started = time.monotonic()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._poll, daemon=True)
        self.thread.start()

    def _poll(self):
        while not self.stop.wait(POLL_SECONDS):
            try:
                if code_snapshot(self.workspace) == self.baseline:
                    continue
                # The edit's own event precedes the change, so it lies within this size.
                size = self.events.stat().st_size if self.events.exists() else 0
            except OSError:
                continue
            self.found = {"seconds": round(time.monotonic() - self.started, 1), "event_bytes": size}
            return

    def finish(self) -> dict:
        self.stop.set()
        self.thread.join()
        return self.found or {"seconds": None, "event_bytes": None,
                              "changed": code_snapshot(self.workspace) != self.baseline}


@contextlib.contextmanager
def hidden(trial: Path):
    """Session 2 cannot read session-1 prompts, transcripts or checkpoint results beside its workspace."""
    modes = {path: path.stat().st_mode & 0o7777 for path in (trial / name for name in HIDDEN) if path.exists()}
    try:
        for path in modes:
            path.chmod(0)
        yield
    finally:
        for path, mode in modes.items():
            path.chmod(mode)


def run_session(trial: Path, number: int, argv: list[str], timeout: int, *, watch: bool = False) -> dict:
    for name in ("telemetry.json", "agent-events.jsonl"):
        if (trial / name).exists():
            raise ValueError(f"Earlier session evidence was not retained: {trial / name}")
    watcher = FirstEdit(trial / "workspace", trial / "agent-events.jsonl") if watch else None
    try:
        process = execute(argv, trial / "workspace", prompt=(trial / f"session-{number}.md").read_text(),
                          timeout=timeout)
    finally:
        first_edit = watcher.finish() if watcher else None
    # The runner always writes these names, so each session's copy moves aside before the next starts.
    for name, suffix in (("telemetry.json", "telemetry.json"), ("agent-events.jsonl", "events.jsonl")):
        if (trial / name).exists():
            (trial / name).replace(trial / f"session-{number}-{suffix}")
    return {"session": number, "argv": argv, "process": process,
            **({"first_edit": first_edit} if watch else {})}


def make_checkpoint(trial: Path, case: str, manifest: dict) -> dict:
    target = trial / "checkpoint/workspace"
    shutil.copytree(trial / "workspace", target, symlinks=True)
    baseline = json.loads((trial / "trial.json").read_text())["baseline_files"]
    snapshot = files(target)
    record = {"files": snapshot, "changed_files": changed_files(baseline, snapshot),
              "planning": planning_stats(target),
              **observe(target, case, manifest["compatibility_assertions"][case])}
    write(trial / "checkpoint.json", record)
    return record


def assertion_counts(tests: Path) -> dict:
    """Assert statements and assert*/fail* calls per unittest method ID."""
    counts = {}
    for path in sorted(tests.rglob("*.py")) if tests.is_dir() else []:
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError, UnicodeError, ValueError):
            continue
        module = ".".join(path.relative_to(tests).with_suffix("").parts)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test"):
                    counts[f"{module}.{node.name}.{method.name}"] = sum(
                        isinstance(item, ast.Assert) or isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
                        and item.func.attr.startswith(("assert", "fail")) for item in ast.walk(method))
    return counts


def event_rows(path: Path) -> list[tuple[int, dict]]:
    """Parsed events with the byte offset at which each line ends."""
    rows, offset = [], 0
    if not path.is_file():
        return rows
    with path.open("rb") as handle:
        for line in handle:
            offset += len(line)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                rows.append((offset, event))
    return rows


def actions(event: dict) -> list[tuple[str, str]]:
    """Commands, other tool inputs and edited paths an event starts, for Codex and Claude logs."""
    item = event.get("item")
    if isinstance(item, dict):
        if event.get("type") != "item.started":
            return []
        if item.get("type") == "command_execution" and isinstance(item.get("command"), str):
            return [("command", item["command"])]
        if item.get("type") == "file_change" and isinstance(item.get("changes"), list):
            return [("edit", change["path"]) for change in item["changes"]
                    if isinstance(change, dict) and isinstance(change.get("path"), str)]
        return []
    message = event.get("message")
    content = message.get("content") if event.get("type") == "assistant" and isinstance(message, dict) else None
    rows = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "tool_use" or not isinstance(block.get("input"), dict):
            continue
        name, data = block.get("name"), block["input"]
        if name == "Bash" and isinstance(data.get("command"), str):
            rows.append(("command", data["command"]))
        elif name in EDIT_TOOLS and isinstance(data.get("file_path"), str):
            rows.append(("edit", data["file_path"]))
        else:
            rows.append(("tool", json.dumps(data, sort_keys=True)))
    return rows


def spellings(path: Path) -> list[str]:
    """A path as given and resolved; hosts may log either, for example /tmp or /private/tmp on macOS."""
    return list(dict.fromkeys((os.path.normpath(path), os.path.normpath(path.resolve()))))


def code_edit(path: str, workspace: Path) -> bool:
    candidate = os.path.normpath(path if os.path.isabs(path) else workspace / path)
    for root in spellings(workspace):
        if os.path.commonpath([candidate, root]) == root:
            return Path(os.path.relpath(candidate, root)).parts[:1] in (("src",), ("tests",))
    return False


def outside_reference(text: str, workspace: Path) -> bool:
    """A command or tool input that names the trial directory or its parents beyond the workspace."""
    for root in spellings(workspace):
        text = text.replace(root, "")
    return any(marker in text for marker in ("../", "..\\", *spellings(workspace.parent)))


def main_loop_usage(rows, boundary: int) -> dict | None:
    """Claude top-level assistant usage through the boundary, counted once per message ID."""
    messages = {}
    for offset, event in rows:
        message = event.get("message")
        if offset > boundary:
            break
        if (event.get("type") == "assistant" and not event.get("parent_tool_use_id") and isinstance(message, dict)
                and isinstance(message.get("usage"), dict)):
            messages[message.get("id") or offset] = message["usage"]
    if not messages:
        return None
    return {key: known_total([usage.get(key) for usage in messages.values()]) for key in
            ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")}


def session_evidence(path: Path, workspace: Path, first_edit: dict | None = None) -> dict:
    rows = event_rows(path)
    started = [(offset, kind, text) for offset, event in rows for kind, text in actions(event)]
    commands = [text for _, kind, text in started if kind == "command"]
    evidence = {"events": len(rows), "commands": len(commands),
                "helper_calls": sum("zagrosi_skills.py" in text for text in commands),
                "code_edits": sum(kind == "edit" and code_edit(text, workspace) for _, kind, text in started),
                "outside_references": [text[:240] for _, kind, text in started
                                       if kind != "edit" and outside_reference(text, workspace)][:20]}
    if first_edit is None:
        return evidence
    boundary, source = first_edit.get("event_bytes"), "poll"
    if boundary is None:  # A session shorter than one poll: use its first logged code edit instead.
        boundary = next((offset for offset, kind, text in started if kind == "edit" and code_edit(text, workspace)), None)
        source = None if boundary is None else "event"
    before = [(kind, text) for offset, kind, text in started if boundary is not None and offset <= boundary]
    evidence["reorientation"] = {
        "seconds": first_edit.get("seconds"), "boundary": source,
        "commands": sum(kind == "command" for kind, _ in before) if boundary is not None else None,
        "planning_read": any(kind != "edit" and ".planning" in text for kind, text in before) if boundary is not None else None,
        "tokens": main_loop_usage(rows, boundary) if boundary is not None else None}
    return evidence


def regressions(checkpoint: dict, final: dict, on_final: dict | None, before: dict, after: dict, probe: bool) -> dict:
    """(a) compatibility lost after the checkpoint; (b) checkpoint tests failing, deleted or weakened; (c) clauses."""
    passed = sorted(name for name, outcome in (checkpoint["tests"] or {}).items() if outcome == "passed")
    final_tests = final["tests"] or {}
    tests = {"failing": [name for name in passed if (on_final or {}).get(name) != "passed"],
             "deleted": [name for name in passed if name not in final_tests],
             "weakened": [name for name in passed if name in final_tests and (
                 final_tests[name] == "skipped" or after.get(name, 0) < before.get(name, 0))]}
    clauses = [name for name, status in (final["probes"] or {}).items() if status == "fail"]
    result = {"compatibility": checkpoint["compatibility"]["passed"] and not final["compatibility"]["passed"],
              "tests": tests, "withheld_clauses": clauses if probe else []}
    result["any"] = bool(result["compatibility"] or any(tests.values()) or result["withheld_clauses"])
    return result


def analyze(trial: Path, case: str, manifest: dict, sessions: list[dict]) -> dict:
    workspace = trial / "workspace"
    checkpoint = json.loads((trial / "checkpoint.json").read_text())
    final = observe(workspace, case, manifest["compatibility_assertions"][case])
    with scratch_copy(workspace, trial / "checkpoint/workspace/tests") as copy:
        on_final = run_checks(copy, "tests")
    return {
        "checkpoint_unchanged": files(trial / "checkpoint/workspace") == checkpoint["files"],
        "final": {**final, "planning": planning_stats(workspace)},
        "checkpoint_tests_on_final": on_final,
        "regressions": regressions(checkpoint, final, on_final, assertion_counts(trial / "checkpoint/workspace/tests"),
                                   assertion_counts(workspace / "tests"), manifest["probe"]),
        "sessions": [{"seconds": row["process"].get("seconds"), "returncode": row["process"].get("returncode"),
                      "timed_out": row["process"].get("timed_out"),
                      **session_evidence(trial / f"session-{row['session']}-events.jsonl", workspace,
                                         row.get("first_edit") if row["session"] == 2 else None)}
                     for row in sessions],
    }


def run_attempt(directory: Path, manifest: dict, item: dict) -> dict:
    trial = directory / item["id"]
    started = time.monotonic()
    write(trial / "attempt.json", {"status": "running", "started": now()})
    sessions, status, error = [], "completed", None
    try:
        sessions.append(run_session(trial, 1, manifest["runners"][0], manifest["timeout"]))
        make_checkpoint(trial, item["case"], manifest)
        with hidden(trial):
            sessions.append(run_session(trial, 2, manifest["runners"][1], manifest["timeout"], watch=True))
    except Exception as exc:  # Keep the attempt as failed evidence; never retry it in place.
        status, error = "error", f"{type(exc).__name__}: {exc}"
    record = json.loads((trial / "trial.json").read_text())
    seconds = [row["process"].get("seconds") for row in sessions]
    record["runner"] = {"returncode": sessions[1]["process"]["returncode"] if len(sessions) == 2 else 1,
                        "seconds": known_total(seconds) if seconds else None,
                        "timed_out": len(sessions) == 2 and bool(sessions[1]["process"].get("timed_out")),
                        "sessions": sessions}
    write(trial / "trial.json", record)
    try:
        coding_trials.check(trial)
        if status == "completed":
            analysis = analyze(trial, item["case"], manifest, sessions)
            write(trial / "session-break.json", analysis)
    except Exception as exc:
        status, error = "error", error or f"{type(exc).__name__}: {exc}"
    outcome = {"status": status, "error": error, "seconds": round(time.monotonic() - started, 3), "finished": now(),
               "session_returncodes": [row["process"].get("returncode") for row in sessions]}
    write(trial / "attempt.json", outcome)
    return outcome


def read_manifest(directory: Path) -> dict:
    manifest = read(directory / "matrix.json")
    if manifest.get("protocol") != PROTOCOL or not manifest.get("trials"):
        raise ValueError(f"{directory} has no prepared {PROTOCOL} matrix")
    return manifest


def require_identity(manifest: dict) -> None:
    """Refuse a run when evaluator, plugin, fixture or CLI bytes differ from preparation."""
    current = identity(Path(manifest["plugin_root"]), manifest["cases"])
    drift = [f"{part}:{name}" for part in current for name in changed_files(manifest["identity"][part], current[part])]
    if cli_versions(manifest["cli_versions"], manifest["executables"]) != manifest["cli_versions"]:
        drift.append("cli_versions")
    if drift:
        raise ValueError("Prepared inputs changed; prepare a new directory: " + ", ".join(drift[:12]))


def prepare(directory: Path, *, cases, arms, repeats: int, host: str, model: str, effort: str, plugin_root: Path,
            timeout: int, probe: bool = False, seed: int = 0, resume_host: str | None = None,
            resume_model: str | None = None, resume_effort: str | None = None,
            codex: str = "codex", claude: str = "claude") -> dict:
    cases, arms = list(cases), list(arms)
    if not cases or set(cases) - PLANS.keys() or len(set(cases)) != len(cases):
        raise ValueError(f"Cases must be distinct members of {sorted(PLANS)}")
    if not arms or set(arms) - set(ARMS) or len(set(arms)) != len(arms):
        raise ValueError(f"Arms must be distinct members of {list(ARMS)}")
    if repeats < 1 or not 0 < timeout <= 7200:
        raise ValueError("Use at least one repetition and a per-session timeout of 1 to 7200 seconds")
    resume_host = resume_host or host
    if resume_model is None and resume_host != host:
        raise ValueError("A host switch needs --resume-model")
    resume_model, resume_effort = resume_model or model, resume_effort or effort
    require_effort(host, effort)
    require_effort(resume_host, resume_effort)
    plugin_root = plugin_root.resolve()
    if not (plugin_root / "scripts/zagrosi_skills.py").is_file():
        raise ValueError("The plugin root must contain scripts/zagrosi_skills.py")
    executables = {"codex": codex, "claude": claude}
    versions = cli_versions({host, resume_host}, executables)
    compatibility = {}
    for case in cases:  # The unchanged fixture fixes each oracle's compatibility assertion count.
        with scratch_copy(coding_trials.PACK / coding_trials.CASES[case]["fixture"]) as copy:
            baseline = run_oracle(copy, case, compatibility=True, expected=None)
        if not baseline["passed"]:
            raise ValueError(f"The {case} fixture fails its own compatibility oracle: {baseline['error']}")
        compatibility[case] = baseline["assertions"]
    directory.mkdir(parents=True, exist_ok=False)
    items = schedule(cases, ["lean"], repeats, True, arms=arms)
    for item in items:
        trial, case = directory / item["id"], item["case"]
        fixture = coding_trials.PACK / coding_trials.CASES[case]["fixture"]
        brief = derived_brief((fixture / "prompt.md").read_text(), case) if probe else None
        coding_trials.prepare(trial, case, "lean", plugin_root=plugin_root,
                              plain_agent=item["arm"] != "forge-lean", brief=brief)
        prompt = (trial / "prompt.md").read_text()
        for session in (1, 2):
            (trial / f"session-{session}.md").write_text(
                session_prompt(item["arm"], prompt, case, session, CLAUSES[case] if probe else ()))
    manifest = {
        "protocol": PROTOCOL, "comparison": True, "seed": seed, "probe": probe, "timeout": timeout,
        "evaluator_root": str(ROOT), "plugin_root": str(plugin_root), "executables": executables,
        "settings": {"host": host, "model": model, "effort": effort, "resume_host": resume_host,
                     "resume_model": resume_model, "resume_effort": resume_effort},
        "runners": [runner(host, model, effort, executables), runner(resume_host, resume_model, resume_effort, executables)],
        "cli_versions": versions, "compatibility_assertions": compatibility,
        "clauses": {case: [clause["id"] for clause in CLAUSES[case]] for case in cases} if probe else {},
        "cases": {case: coding_trials.CASES[case] for case in cases},
        "identity": identity(plugin_root, cases), "prepared": now(), "trials": items,
    }
    # Written last: a directory without matrix.json was never a runnable matrix.
    write(directory / "matrix.json", manifest)
    return {"directory": str(directory), "attempts": len(items), "planned_sessions": 2 * len(items),
            "cli_versions": versions}


def run(directory: Path, attempt: str | None = None) -> dict:
    """Attempt pending rows serially; stop after a session fails without timing out."""
    manifest = read_manifest(directory)
    items = manifest["trials"]
    if attempt is not None:
        items = [item for item in items if item["id"] == attempt]
        if not items:
            raise ValueError(f"Unknown attempt: {attempt}")
        if (directory / attempt / "attempt.json").exists():
            raise ValueError(f"{attempt} was already attempted; prepare a new directory to repeat it")
    halted = None
    for item in items:
        if (directory / item["id"] / "attempt.json").exists():
            continue
        require_identity(manifest)
        outcome = run_attempt(directory, manifest, item)
        print(json.dumps({"attempt": item["id"], **outcome}), flush=True)
        failed = [code for code in outcome["session_returncodes"] if code not in (0, 124)]
        if outcome["status"] != "completed" or failed:
            halted = {"attempt": item["id"], "status": outcome["status"], "error": outcome["error"],
                      "session_returncodes": outcome["session_returncodes"]}
            break
    return {**report(directory), "halted": halted}


def attempt_row(directory: Path, item: dict) -> dict:
    trial = directory / item["id"]
    attempt, result, analysis, checkpoint = (read(trial / name) for name in (
        "attempt.json", "result.json", "session-break.json", "checkpoint.json"))
    status = attempt.get("status", "pending")
    if status == "running":
        status = "interrupted"
    telemetry = [read(trial / f"session-{number}-telemetry.json") for number in (1, 2)]
    totals = {key: known_total([(row.get("totals") or {}).get(key) for row in telemetry]) if all(telemetry) else None
              for key in TOKENS}
    totals["total_tokens"] = known_total([totals["input_tokens"], totals["output_tokens"]])
    quality = (result.get("common_quality") or {}).get("success")
    common = ("pending" if status == "pending" else "passed" if quality is True and status == "completed"
              else "failed" if quality is False or status != "completed" else "unknown")
    sessions = analysis.get("sessions") or []
    seconds = [row.get("seconds") for row in sessions]
    final = analysis.get("final") or {}
    return {
        "id": item["id"], "arm": item["arm"], "case": item["case"], "block": item["block"], "status": status,
        "common_quality": common, "workflow": (result.get("workflow") or {}).get("success"),
        "behavior": (result.get("behavior") or {}).get("success"),
        "cleanup": (result.get("cleanup") or {}).get("success"),
        "reported_telemetry": {"totals": totals, "reported_cost_usd": known_total(
            [row.get("reported_cost_usd") for row in telemetry]) if all(telemetry) else None},
        "attempt_seconds": known_total(seconds) if len(seconds) == 2 else None,
        "sessions": [{key: row.get(key) for key in ("seconds", "returncode", "timed_out", "commands", "helper_calls")}
                     for row in sessions],
        "missing_tools": [row.get("missing_tools") for row in telemetry],
        "reorientation": sessions[1].get("reorientation") if len(sessions) == 2 else None,
        "regressions": analysis.get("regressions"),
        "probes": final.get("probes"), "checkpoint_unchanged": analysis.get("checkpoint_unchanged"),
        "checkpoint": {key: checkpoint.get(key) for key in ("changed_files", "planning", "probes")},
        "final_planning": final.get("planning"),
        "outside_references": sum(len(row.get("outside_references") or []) for row in sessions),
        "rejection": rejection(result, analysis, status),
    }


def rejection(result: dict, analysis: dict, status: str) -> list[str]:
    """Mechanical, arm-blind reasons behind a rejected attempt."""
    if status != "completed":
        return [status]
    if (result.get("common_quality") or {}).get("success"):
        return []
    reasons = []
    runner_record = result.get("runner") or {}
    if runner_record.get("returncode") != 0:
        reasons.append("session 2 timeout" if runner_record.get("timed_out") else "session 2 failure")
    final = analysis.get("final") or {}
    probes = list((final.get("probes") or {}).values())
    if not (final.get("compatibility") or {}).get("passed"):
        reasons.append("compatibility regression" if (analysis.get("regressions") or {}).get("compatibility")
                       else "compatibility")
    elif not (final.get("oracle") or {}).get("passed"):
        reasons.append("unfinished feature" if "missing" in probes else
                       "clause" if any(value != "pass" for value in probes) else "feature")
    if (result.get("tests") or {}).get("returncode") != 0:
        reasons.append("candidate tests")
    if result.get("outside_scope"):
        reasons.append("scope")
    if result.get("evaluator_changed") or not (result.get("plugin_provenance") or {}).get("success"):
        reasons.append("provenance")
    if (result.get("cleanup") or {}).get("success") is False:
        reasons.append("cleanup review")
    return reasons


def summarize(rows: list[dict]) -> dict:
    common = accepted_outcomes(rows, status_key="common_quality")
    total = known_total([row["reported_telemetry"]["totals"]["total_tokens"] for row in rows])
    reoriented = [row["reorientation"] for row in rows if row["reorientation"]]
    seconds = [value["seconds"] for value in reoriented if value.get("seconds") is not None]
    regressions = [row["regressions"] for row in rows if row["regressions"]]
    return {
        "scheduled": common["scheduled"], "accepted": common["accepted"], "accepted_rate": common["accepted_rate"],
        "statuses": dict(Counter(row["status"] for row in rows)),
        "workflow": dict(Counter(str(row["workflow"]) for row in rows)),
        "total_tokens": {"total": total, "per_accepted": total / common["accepted"]
                         if total is not None and common["accepted"] else None},
        "tokens": common["tokens"], "reported_cost_usd": common["reported_cost_usd"],
        "elapsed_seconds": common["elapsed_seconds"],
        "reorientation": {"median_seconds": statistics.median(seconds) if seconds else None,
                          "planning_read": sum(value.get("planning_read") is True for value in reoriented),
                          "observed": len(reoriented)},
        "regressions": {"attempts": sum(value["any"] for value in regressions),
                        "compatibility": sum(bool(value["compatibility"]) for value in regressions),
                        "tests": sum(any(value["tests"].values()) for value in regressions),
                        "withheld_clauses": sum(bool(value["withheld_clauses"]) for value in regressions),
                        "observed": len(regressions)},
        "helper_calls": sum(sum(session.get("helper_calls") or 0 for session in row["sessions"]) for row in rows),
        "outside_references": sum(row["outside_references"] for row in rows),
        "rejections": dict(Counter(reason for row in rows for reason in row["rejection"])),
    }


def beats(first: dict, second: dict) -> bool:
    """More accepted without more regressed attempts, or fewer regressed without fewer accepted."""
    more, fewer = first["accepted"], first["regressions"]["attempts"]
    other, regressed = second["accepted"], second["regressions"]["attempts"]
    return (more > other and fewer <= regressed) or (fewer < regressed and more >= other)


def decide(arms: dict) -> dict | None:
    """Apply the protocol's decision rule to observed counts; small samples cannot establish an effect."""
    if set(arms) != set(ARMS):
        return None
    forge, notes, plain = (arms[name] for name in ARMS)
    forge_tokens, notes_tokens = forge["total_tokens"]["total"], notes["total_tokens"]["total"]
    ratio = forge_tokens / notes_tokens if forge_tokens is not None and notes_tokens else None
    pairs = {f"{first}>{second}": beats(arms[first], arms[second]) for first in ARMS for second in ARMS if first != second}
    h1 = pairs["forge-lean>plain"] and pairs["notes>plain"]
    h2 = pairs["forge-lean>notes"] and ratio is not None and ratio <= 1.5
    return {"H1": h1, "H2": h2, "H0": not any(pairs.values()), "pairs": pairs, "forge_to_notes_tokens": ratio}


def report(directory: Path) -> dict:
    manifest = read_manifest(directory)
    rows = [attempt_row(directory, item) for item in manifest["trials"]]
    keys = sorted({(row["arm"], row["case"]) for row in rows})
    groups = [{"arm": arm, "case": case, **summarize([row for row in rows if (row["arm"], row["case"]) == (arm, case)])}
              for arm, case in keys]
    arms = {arm: summarize([row for row in rows if row["arm"] == arm]) for arm in dict.fromkeys(row["arm"] for row in rows)}
    blocks = {item["block"] for item in manifest["trials"]}
    reviewed = [row for row in reviews(directory) if row["valid"]]
    result = {
        "protocol": PROTOCOL, "settings": manifest["settings"], "probe": manifest["probe"],
        "cli_versions": manifest["cli_versions"], "timeout": manifest["timeout"], "groups": groups, "arms": arms,
        "decision": decide(arms), "comparative_review": {"expected": len(blocks), "completed": len(reviewed)},
        "attempts": rows,
        "limits": "Every scheduled attempt counts, including failures, timeouts and pending work. Accepted means the "
                  "common-quality verdict, which needs an independent blind cleanup review. Tokens sum both sessions "
                  "and stay unknown when either session's usage is missing; Claude totals include subagents. "
                  "Re-orientation time is polled to the first src/ or tests/ change in session 2; Codex reports "
                  "usage per session only, so its tokens before that edit are unknown. A renamed test counts as "
                  "deleted. Probe failures are evidence, not certainty, and four repetitions per arm and case are "
                  "observations, not statistically established effects.",
    }
    write(directory / "report.json", result)
    return result


def apply_reviews(directory: Path) -> dict:
    manifest = read_manifest(directory)
    reviews(directory, apply=True)
    for item in manifest["trials"]:
        trial = directory / item["id"]
        if (trial / "review.json").is_file():
            coding_trials.check(trial, review=trial / "review.json")
    return report(directory)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "run", "report", "blind", "apply-reviews"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--cases", nargs="+", default=list(PLANS))
    parser.add_argument("--arms", nargs="+", default=list(ARMS))
    parser.add_argument("--repeats", type=int, default=4)
    parser.add_argument("--host", choices=("codex", "claude"), default="codex")
    parser.add_argument("--resume-host", choices=("codex", "claude"), help="Session-2 host for the host-switch variant")
    parser.add_argument("--model")
    parser.add_argument("--resume-model")
    parser.add_argument("--effort", choices=EFFORT_CHOICES)
    parser.add_argument("--resume-effort", choices=EFFORT_CHOICES)
    parser.add_argument("--plugin-root", type=Path, default=ROOT, help="Frozen Forge snapshot for the forge-lean arm")
    parser.add_argument("--timeout", type=int, default=1800, help="Seconds per session")
    parser.add_argument("--probe", action="store_true", help="Withhold decision clauses from the brief; give them in session 1")
    parser.add_argument("--seed", type=int, default=0, help="Blind packet ordering")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--attempt", help="Run one pending attempt")
    args = parser.parse_args()
    directory = args.directory.absolute()
    try:
        if args.operation == "prepare":
            if not args.model or not args.effort:
                parser.error("prepare requires --model and --effort")
            result = prepare(directory, cases=args.cases, arms=args.arms, repeats=args.repeats, host=args.host,
                             model=args.model, effort=args.effort, plugin_root=args.plugin_root, timeout=args.timeout,
                             probe=args.probe, seed=args.seed, resume_host=args.resume_host,
                             resume_model=args.resume_model, resume_effort=args.resume_effort,
                             codex=args.codex, claude=args.claude)
        elif args.operation == "run":
            result = run(directory, args.attempt)
        elif args.operation == "blind":
            read_manifest(directory)
            result = packets(directory)
        elif args.operation == "apply-reviews":
            result = apply_reviews(directory)
        else:
            result = report(directory)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 1 if args.operation == "run" and result.get("halted") else 0


if __name__ == "__main__":
    raise SystemExit(main())
