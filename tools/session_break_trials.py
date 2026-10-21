#!/usr/bin/env python3
"""Session-break trials: work until a break after section 2, then resume in a fresh session.

Implements examples/evals/session-break-protocol.md. Model calls occur only in `run`, which
attempts each scheduled row once; after fixing a shared failure, prepare a new directory.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
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
from coding_trial_outcomes import known_total  # noqa: E402
from coding_trial_process import execute  # noqa: E402
from coding_trial_runner import EFFORT_CHOICES, require_effort  # noqa: E402
from session_break_checks import CLAUSES, derived_brief  # noqa: E402
from session_break_evidence import assertion_counts, regressions, session_evidence  # noqa: E402
from session_break_report import ARMS, TOKENS, build_report  # noqa: E402
from trial_matrix import read, schedule  # noqa: E402

PROTOCOL = "session-break/v1"
CHECKS = ROOT / "tools/session_break_checks.py"
# Every module whose bytes shape an attempt or its analysis, besides the shared trial harness.
SOURCES = (Path(__file__).resolve(), CHECKS, *(ROOT / "tools" / f"session_break_{name}.py" for name in ("evidence", "report")))
POLL_SECONDS = 0.5
NOTES = ("Keep `.planning/NOTES.md` as your durable working notes: the plan, each section's status, decisions and "
         "clarifications, verification results and the next step. Update it whenever one of these changes. When "
         "continuing work, read it before acting.")
STATE_FIRST = "Use Forge's opt-in state-first lean mode: pass `--state-first` to `plan-setup` and follow {reference}."
STATE_REFERENCE = "skills/zagrosi-implement/references/state-first.md"
# Each Forge arm loads its own frozen snapshot, recorded under this identity part.
FORGE_ARMS = {"forge-lean": "plugin", "forge-state": "state_plugin"}
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
SESSION_ONE = ("session-1.md", "session-1-events.jsonl", "session-1-telemetry.json", "checkpoint.json", "checkpoint")
MODES, LOCK = "hidden-modes.json", "run.lock"


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def last_json(text: str):
    lines = [line for line in (text or "").splitlines() if line.strip()]
    try:
        return json.loads(lines[-1]) if lines else None
    except ValueError:
        return None


def standing_instruction(arm: str, roots: dict) -> str | None:
    """Arm text at the start of both session prompts, as a repository instruction file would be."""
    if arm == "notes":
        return NOTES
    return STATE_FIRST.format(reference=roots["state_plugin"] / STATE_REFERENCE) if arm == "forge-state" else None


def session_prompt(standing: str | None, prompt: str, case: str, session: int, clauses=()) -> str:
    """Standing instruction, case prompt and shared plan; clarifications reach session 1 only."""
    parts = [standing] if standing else []
    parts += [prompt.rstrip(), "Section plan:\n" + "\n".join(f"{number}. {text}" for number, text
                                                             in enumerate(PLANS[case], 1))]
    if session == 1 and clauses:
        parts.append("The user's answers to earlier questions; they are binding and not in the brief:\n"
                     + "\n".join("- " + clause["clarification"] for clause in clauses))
    parts.append(FIRST_SESSION if session == 1 else SECOND_SESSION)
    return "\n\n".join(parts) + "\n"


def analyzer() -> dict:
    return {path.relative_to(ROOT).as_posix(): sha256(path) for path in SOURCES}


def identity(roots: dict, cases) -> dict:
    """Evaluator, plugin and fixture bytes that every attempt must share."""
    evaluator = analyzer()
    for case in cases:
        oracle = ROOT / coding_trials.CASES[case]["oracle"]
        evaluator.update(evaluator_files(ROOT, oracle, coding_trials.PACK / "cases.json"))
    return {"evaluator": evaluator, **{part: plugin_files(root) for part, root in roots.items()},
            "fixtures": {case: files(coding_trials.PACK / coding_trials.CASES[case]["fixture"]) for case in cases}}


def plugin_roots(manifest: dict) -> dict:
    """Snapshot per identity part; a matrix without forge-state records only the forge-lean root."""
    return {"plugin": Path(manifest["plugin_root"]),
            **({"state_plugin": Path(manifest["state_plugin_root"])} if manifest.get("state_plugin_root") else {})}


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
    """One JSON report line, kept whole however many tests a candidate adds; None when unobserved."""
    process = execute([sys.executable, "-B", str(CHECKS), operation, str(workspace), *([case] if case else [])],
                      workspace, timeout=300, output_limit=8 * 1024 * 1024)
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

    def _changed(self) -> bool | None:
        try:
            if code_snapshot(self.workspace) == self.baseline:
                return False
            # The edit's own event precedes the change, so it lies within this size.
            size = self.events.stat().st_size if self.events.exists() else 0
        except OSError:
            return None
        self.found = {"seconds": round(time.monotonic() - self.started, 1), "event_bytes": size}
        return True

    def _poll(self):
        while not self.stop.wait(POLL_SECONDS):
            if self._changed():
                return

    def finish(self) -> dict:
        self.stop.set()
        self.thread.join()
        if self.found is None and self._changed():
            self.found["after_exit"] = True  # Within the last poll: seconds are an upper bound.
        return self.found or {"seconds": None, "event_bytes": None}


def restore_hidden(directory: Path) -> None:
    """Undo every recorded mode change; one missing path cannot keep the rest unreadable."""
    record = directory / MODES
    if not record.exists():
        return
    failures = []
    for path, mode in json.loads(record.read_text()).items():
        try:
            os.chmod(path, mode)
        except FileNotFoundError:
            continue
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    if failures:
        raise OSError("Cannot restore hidden paths: " + "; ".join(failures))
    record.unlink()


@contextlib.contextmanager
def hidden(directory: Path, trial: Path):
    """While session 2 runs, only its own workspace, trial record and prompt stay readable in the matrix.

    Other attempts' prompts hold the withheld clauses, and their evidence holds probe results. The modes
    are recorded first, so a later run restores them even after this process was killed.
    """
    paths = [trial / name for name in SESSION_ONE] + [
        path for path in directory.iterdir() if path != trial and path.name not in (MODES, LOCK)]
    modes = {str(path): path.lstat().st_mode & 0o7777 for path in paths if path.exists() and not path.is_symlink()}
    write(directory / MODES, modes)
    try:
        for path in modes:
            os.chmod(path, 0)
        yield
    finally:
        restore_hidden(directory)


@contextlib.contextmanager
def locked(directory: Path):
    """One run per matrix: attempts stay serial and none can start twice."""
    try:
        descriptor = os.open(directory / LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError(f"Another run holds {directory / LOCK}; delete it only if no run is active") from None
    with os.fdopen(descriptor, "w") as handle:
        handle.write(f"{os.getpid()}\n")
    previous = {}
    if threading.current_thread() is threading.main_thread():
        # Turn termination into an exception so session processes stop and file modes are restored.
        for number in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
            if number is not None:
                previous[number] = signal.signal(number, signal.default_int_handler)
    try:
        restore_hidden(directory)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        (directory / LOCK).unlink(missing_ok=True)


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


def failed(session: dict) -> bool:
    return session["process"].get("returncode") != 0 and not session["process"].get("timed_out")


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


def combine_telemetry(trial: Path, sessions: int) -> dict:
    """Both sessions' runner-reported usage; one missing session leaves every total unknown."""
    rows = [read(trial / f"session-{number}-telemetry.json") for number in range(1, sessions + 1)]
    totals = {key: known_total([(row.get("totals") or {}).get(key) for row in rows]) if all(rows) else None
              for key in TOKENS}
    totals["total_tokens"] = known_total([totals["input_tokens"], totals["output_tokens"]])
    record = {"source": "session-break sessions", "sessions": rows, "totals": totals,
              "reported_cost_usd": known_total([row.get("reported_cost_usd") for row in rows]) if all(rows) else None,
              "missing_tools": [row.get("missing_tools") for row in rows]}
    write(trial / "telemetry.json", record)
    return record


def analyze_attempt(trial: Path, case: str, manifest: dict, sessions: list[dict]) -> dict:
    workspace = trial / "workspace"
    checkpoint = json.loads((trial / "checkpoint.json").read_text())
    final = observe(workspace, case, manifest["compatibility_assertions"][case])
    with scratch_copy(workspace, trial / "checkpoint/workspace/tests") as copy:
        on_final = run_checks(copy, "tests")
    analysis = {
        "analyzer": analyzer(), "analyzed": now(),
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
    write(trial / "session-break.json", analysis)
    return analysis


def finish_attempt(trial: Path, case: str, manifest: dict, sessions: list[dict]) -> str | None:
    """Record usage, check the candidate and analyze both sessions; each step runs whatever failed before it."""
    errors = []
    combine_telemetry(trial, len(sessions))
    for step in (lambda: coding_trials.check(trial, review=trial / "review.json" if (trial / "review.json").is_file() else None),
                 lambda: analyze_attempt(trial, case, manifest, sessions) if len(sessions) == 2 else None):
        try:
            step()
        except Exception as exc:  # Keep later evidence even when one step fails.
            errors.append(f"{type(exc).__name__}: {exc}")
    return "; ".join(errors) or None


def run_attempt(directory: Path, manifest: dict, item: dict) -> dict:
    trial = directory / item["id"]
    started = time.monotonic()
    try:
        with (trial / "attempt.json").open("x") as handle:  # Claim the attempt exactly once.
            handle.write(json.dumps({"status": "running", "started": now()}) + "\n")
    except FileExistsError:
        raise ValueError(f"{item['id']} was already attempted; prepare a new directory to repeat it") from None
    sessions, status, error = [], "completed", None
    try:
        sessions.append(run_session(trial, 1, manifest["runners"][0], manifest["timeout"]))
        if not failed(sessions[0]):  # A crashed first session is not a break; stop the attempt there.
            make_checkpoint(trial, item["case"], manifest)
            with hidden(directory, trial):
                sessions.append(run_session(trial, 2, manifest["runners"][1], manifest["timeout"], watch=True))
    except Exception as exc:  # Keep the attempt as failed evidence; never retry it in place.
        status, error = "error", f"{type(exc).__name__}: {exc}"
    record = json.loads((trial / "trial.json").read_text())
    seconds = [row["process"].get("seconds") for row in sessions]
    record["runner"] = {"returncode": sessions[-1]["process"]["returncode"] if sessions and status == "completed" else 1,
                        "seconds": known_total(seconds) if seconds else None,
                        "timed_out": bool(sessions) and bool(sessions[-1]["process"].get("timed_out")),
                        "sessions": sessions}
    if len(sessions) == 1 and status == "completed":
        record["runner"]["returncode"] = sessions[0]["process"]["returncode"] or 1
    write(trial / "trial.json", record)
    problem = finish_attempt(trial, item["case"], manifest, sessions) if sessions else None
    if problem:
        status, error = "error", error or problem
    outcome = {"status": status, "error": error, "seconds": round(time.monotonic() - started, 3), "finished": now(),
               "session_returncodes": [row["process"].get("returncode") for row in sessions],
               "failed_sessions": [row["session"] for row in sessions if failed(row)]}
    write(trial / "attempt.json", outcome)
    return outcome


def read_manifest(directory: Path) -> dict:
    manifest = read(directory / "matrix.json")
    if manifest.get("protocol") != PROTOCOL or not manifest.get("trials"):
        raise ValueError(f"{directory} has no prepared {PROTOCOL} matrix")
    return manifest


def require_identity(manifest: dict) -> None:
    """Refuse a run when evaluator, plugin, fixture or CLI bytes differ from preparation."""
    current = identity(plugin_roots(manifest), manifest["cases"])
    drift = [f"{part}:{name}" for part in current for name in changed_files(manifest["identity"][part], current[part])]
    if cli_versions(manifest["cli_versions"], manifest["executables"]) != manifest["cli_versions"]:
        drift.append("cli_versions")
    if drift:
        raise ValueError("Prepared inputs changed; prepare a new directory: " + ", ".join(drift[:12]))


def prepare(directory: Path, *, cases, arms, repeats: int, host: str, model: str, effort: str, plugin_root: Path,
            timeout: int, probe: bool = False, seed: int = 0, resume_host: str | None = None,
            resume_model: str | None = None, resume_effort: str | None = None, state_plugin_root: Path | None = None,
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
    # Without its own snapshot, forge-state uses the forge-lean tree, which then must carry the state-first mode.
    roots = {"plugin": plugin_root.resolve(),
             **({"state_plugin": (state_plugin_root or plugin_root).resolve()} if "forge-state" in arms else {})}
    if not (roots["plugin"] / "scripts/zagrosi_skills.py").is_file():
        raise ValueError("The plugin root must contain scripts/zagrosi_skills.py")
    if "state_plugin" in roots and not all((roots["state_plugin"] / name).is_file()
                                           for name in ("scripts/zagrosi_skills.py", STATE_REFERENCE)):
        raise ValueError(f"The state plugin root must contain scripts/zagrosi_skills.py and {STATE_REFERENCE}")
    if probe and any(arm in arms and any((roots[part] / name).exists() for name in ("examples", "tools", "tests"))
                     for arm, part in FORGE_ARMS.items()):
        # The Forge prompt points at this tree; an evaluator checkout holds the withheld clauses.
        raise ValueError("--probe needs runtime-only plugin snapshots without examples/, tools/ or tests/")
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
        trial, case, forge = directory / item["id"], item["case"], item["arm"] in FORGE_ARMS
        fixture = coding_trials.PACK / coding_trials.CASES[case]["fixture"]
        brief = derived_brief((fixture / "prompt.md").read_text(), case) if probe else None
        coding_trials.prepare(trial, case, "lean", plugin_root=roots[FORGE_ARMS[item["arm"]] if forge else "plugin"],
                              plain_agent=not forge, brief=brief)
        prompt, standing = (trial / "prompt.md").read_text(), standing_instruction(item["arm"], roots)
        for session in (1, 2):
            (trial / f"session-{session}.md").write_text(
                session_prompt(standing, prompt, case, session, CLAUSES[case] if probe else ()))
    manifest = {
        "protocol": PROTOCOL, "comparison": True, "seed": seed, "probe": probe, "timeout": timeout,
        "evaluator_root": str(ROOT), "plugin_root": str(roots["plugin"]),
        **({"state_plugin_root": str(roots["state_plugin"])} if "state_plugin" in roots else {}),
        "executables": executables,
        "settings": {"host": host, "model": model, "effort": effort, "resume_host": resume_host,
                     "resume_model": resume_model, "resume_effort": resume_effort},
        "runners": [runner(host, model, effort, executables), runner(resume_host, resume_model, resume_effort, executables)],
        "cli_versions": versions, "compatibility_assertions": compatibility,
        "cases": {case: coding_trials.CASES[case] for case in cases},
        "sections": {case: len(PLANS[case]) for case in cases},
        "identity": identity(roots, cases), "prepared": now(), "trials": items,
    }
    # Written last: a directory without matrix.json was never a runnable matrix.
    write(directory / "matrix.json", manifest)
    return {"directory": str(directory), "attempts": len(items), "planned_sessions": 2 * len(items),
            "cli_versions": versions}


def select(manifest: dict, attempt: str | None) -> list[dict]:
    items = [item for item in manifest["trials"] if attempt in (None, item["id"])]
    if not items:
        raise ValueError(f"Unknown attempt: {attempt}")
    return items


def run(directory: Path, attempt: str | None = None) -> dict:
    """Attempt pending rows serially; stop after a session fails without timing out."""
    manifest = read_manifest(directory)
    items = select(manifest, attempt)
    if attempt is not None and (directory / attempt / "attempt.json").exists():
        raise ValueError(f"{attempt} was already attempted; prepare a new directory to repeat it")
    halted = None
    with locked(directory):
        for item in items:
            if (directory / item["id"] / "attempt.json").exists():
                continue
            require_identity(manifest)
            outcome = run_attempt(directory, manifest, item)
            print(json.dumps({"attempt": item["id"], **outcome}), flush=True)
            if outcome["status"] != "completed" or outcome["failed_sessions"]:
                halted = {"attempt": item["id"], **{key: outcome[key] for key in
                                                    ("status", "error", "session_returncodes", "failed_sessions")}}
                break
    return {**report(directory), "halted": halted}


def analyze(directory: Path, attempt: str | None = None) -> dict:
    """Recompute usage, checks and session analysis for attempts whose sessions finished."""
    manifest = read_manifest(directory)
    with locked(directory):
        for item in select(manifest, attempt):
            trial = directory / item["id"]
            sessions = (read(trial / "trial.json").get("runner") or {}).get("sessions") or []
            if read(trial / "attempt.json").get("status") in ("completed", "error") and sessions:
                finish_attempt(trial, item["case"], manifest, sessions)
    return report(directory)


def report(directory: Path) -> dict:
    result = build_report(directory, read_manifest(directory))
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
    parser.add_argument("operation", choices=("prepare", "run", "analyze", "report", "blind", "apply-reviews"))
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
    parser.add_argument("--state-plugin-root", type=Path,
                        help="Frozen Forge snapshot with the state-first mode for the forge-state arm; defaults to --plugin-root")
    parser.add_argument("--timeout", type=int, default=1800, help="Seconds per session")
    parser.add_argument("--probe", action="store_true", help="Withhold decision clauses from the brief; give them in session 1")
    parser.add_argument("--seed", type=int, default=0, help="Blind packet ordering")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--attempt", help="Run or analyze one attempt")
    args = parser.parse_args()
    directory = Path(os.path.abspath(args.directory))
    try:
        if args.operation == "prepare":
            if not args.model or not args.effort:
                parser.error("prepare requires --model and --effort")
            result = prepare(directory, cases=args.cases, arms=args.arms, repeats=args.repeats, host=args.host,
                             model=args.model, effort=args.effort, plugin_root=args.plugin_root, timeout=args.timeout,
                             probe=args.probe, seed=args.seed, resume_host=args.resume_host,
                             resume_model=args.resume_model, resume_effort=args.resume_effort,
                             state_plugin_root=args.state_plugin_root, codex=args.codex, claude=args.claude)
        elif args.operation == "run":
            result = run(directory, args.attempt)
        elif args.operation == "analyze":
            result = analyze(directory, args.attempt)
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
