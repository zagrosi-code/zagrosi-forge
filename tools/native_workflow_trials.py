#!/usr/bin/env python3
"""Prepare, run one native workflow, and report a retained host/depth acceptance matrix.

Model calls occur only with `run --case ID`. Each row is attempted once. Use a new
directory after fixing a shared failure; previous attempts remain failed evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

from coding_trial_evidence import files, plugin_files
from coding_trial_process import execute
from native_plugin_smoke import codex_skills, live_flags, prepare_live
from native_trial_session import EXPECTED_SKILLS, native_command, read_json, run_session
from native_workflow_checks import completed, interrupted, planned

ROOT = Path(__file__).resolve().parents[1]


def evaluator_identity():
    names = ("native_workflow_trials.py", "native_workflow_checks.py", "native_trial_session.py", "native_plugin_smoke.py",
             "coding_trial_process.py", "coding_trial_runner.py", "coding_trial_evidence.py")
    return {**plugin_files(ROOT), **{f"tools/{name}": hashlib.sha256((ROOT / "tools" / name).read_bytes()).hexdigest() for name in names},
            **{f"examples/first-task/{name}": value for name, value in files(ROOT / "examples/first-task").items()}}


def matrix():
    rows = [{"id": f"{host}-{depth}-{trigger}", "host": host, "depth": depth, "trigger": trigger,
             "resume_host": host if trigger == "indirect" else ("claude" if host == "codex" else "codex")}
            for host in ("codex", "claude") for depth in ("lean", "standard", "deep") for trigger in ("direct", "indirect")]
    return rows + [{"id": host + "-unsupported", "host": host, "trigger": "unsupported"} for host in ("codex", "claude")]


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def prepare(directory, root, models, efforts, *, timeout=600, total_timeout=1500):
    if not 0 < timeout <= 3600 or not timeout <= total_timeout <= 10800:
        raise ValueError("Use positive bounded per-session and total-row deadlines")
    directory.mkdir(parents=True, exist_ok=False)
    installation = prepare_live(root, directory / "native")
    discovery = codex_skills("codex", directory, os.environ, flags=live_flags(installation),
                             plugin_id="zagrosi-forge@" + installation["marketplace_name"])
    if {row["name"] for row in discovery if row.get("enabled")} != EXPECTED_SKILLS:
        raise ValueError("Native Codex did not register all five requested skills")
    if any(not Path(row["path"]).resolve().is_relative_to(Path(installation["codex_plugin"]).resolve()) for row in discovery):
        raise ValueError("Native skills resolved outside the exact staged package")
    rows = matrix()
    record = {"plugin_root": str(root), "plugin_sha256": plugin_files(root), "installation": installation,
              "evaluator_root": str(ROOT), "evaluator_sha256": evaluator_identity(),
              "models": models, "efforts": efforts, "timeout": timeout, "total_timeout": total_timeout,
              "rows": rows, "codex_discovery": [{key: row.get(key) for key in ("name", "path", "pluginId", "enabled")} for row in discovery],
              "planned_model_calls": 41, "maximum_model_calls": 50, "followups": "fresh sessions using persisted plan", "model_calls": 0,
              "codex_configuration": "Native user configuration plus the same per-process plugin overrides used for discovery",
              "cli_versions": {host: execute([host, "--version"], directory, timeout=15)["stdout"].strip() for host in ("codex", "claude")}}
    for row in rows:
        trial = directory / row["id"]
        shutil.copytree(ROOT / "examples/first-task", trial / "workspace", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (trial / "workspace/unrelated.txt").write_text("Keep this user's unrelated work unchanged.\n")
        write(trial / "baseline.json", files(trial / "workspace"))
    write(directory / "matrix.json", record)
    return record


def prompt(row, phase):
    prefix = ("Use $zagrosi-forge:zagrosi-forge. " if row["host"] == "codex" else "/zagrosi-forge:zagrosi-forge ") if row["trigger"] == "direct" else ""
    if row["trigger"] == "unsupported":
        return "Translate 'hello' into French. Reply only Bonjour. Do not modify files or run a software workflow."
    request = {"plan": "Create an admitted plan for requirements.md. Plan only; do not implement or edit code/tests.",
               "implement": "Continue the existing plan and implement requirements.md. First run the existing failing tests and persist a red checkpoint before source edits, then continue to completion.",
               "resume": "Continue the interrupted task from its existing plan and checkpoint. Preserve the original regression evidence and finish implementation."}[phase]
    return (prefix + request + f" Keep the requested {row['depth']} depth and one compact section. "
            "Use .planning itself as the planning root, with .planning/sections/index.md and one canonical section; do not create a nested planning project. "
            "Operator choices: local mutable planning, manual Git, no commits/pushes/deployment. "
            "Preserve requirements.md, README.md, unrelated.txt and the existing tests/test_labels.py exactly; new tests may be separate files. "
            "Change only labels.py, new tests, and .planning/. Use the standard library. "
            "Capture the final real unittest run with verification and complete the saved records. "
            "Do not inspect external trial tools, other candidates, host configuration or credentials.")


def authenticated(host):
    command = ["codex", "login", "status"] if host == "codex" else ["claude", "auth", "status", "--json"]
    result = execute(command, ROOT, timeout=15)
    if host == "claude":
        try:
            return result["returncode"] == 0 and json.loads(result["stdout"]).get("loggedIn") is True
        except (ValueError, AttributeError):
            return False
    return result["returncode"] == 0


def unattempted(path):
    previous = read_json(path, {})
    return not path.exists() or (previous.get("status") == "blocked" and not previous.get("sessions"))


def run(directory, identity, *, resume_host=None):
    record = read_json(directory / "matrix.json")
    row = next((item for item in record["rows"] if item["id"] == identity), None)
    if row is None:
        raise ValueError("Unknown matrix row")
    trial = directory / identity
    result_path = trial / "result.json"
    if result_path.exists():
        if not unattempted(result_path):
            raise ValueError("Attempt already retained; prepare a new matrix instead of overwriting it")
        result_path.rename(trial / f"blocked-{len(list(trial.glob('blocked-*.json'))) + 1:03}.json")
    result = {"id": identity, "success": False, "status": "running", "sessions": [], "checks": {}}
    write(result_path, result)
    actual_resume = resume_host or row.get("resume_host", row["host"])
    hosts = {row["host"], actual_resume}
    missing = [host for host in sorted(hosts) if not record["models"].get(host) or not authenticated(host)]
    if missing:
        result.update(status="blocked", blocked_hosts=missing, success=None)
        write(result_path, result)
        return result
    root, workspace = Path(record["plugin_root"]), trial / "workspace"
    baseline = read_json(trial / "baseline.json")
    installation = record["installation"]
    start = time.monotonic()
    checkpoint = None
    try:
        if evaluator_identity() != record.get("evaluator_sha256"):
            raise ValueError("Evaluator source changed; prepare a fresh matrix from frozen tools")
        for phase in (["unsupported"] if row["trigger"] == "unsupported" else ["plan", "implement", "resume"]):
            for host in hosts:
                if plugin_files(Path(installation[host + "_plugin"])) != record["plugin_sha256"]:
                    raise ValueError("Staged native package changed")
            remaining = record["total_timeout"] - (time.monotonic() - start)
            if remaining <= 0:
                raise ValueError("Total workflow deadline exhausted")
            host = actual_resume if phase == "resume" else row["host"]
            stage_row = {**row, "host": host}
            session = run_session(native_command(host, record["models"][host], record["efforts"][host], installation),
                workspace, trial / phase, prompt(stage_row, phase), host=host, plugin_root=Path(installation[host + "_plugin"]),
                timeout=min(record["timeout"], remaining), interrupt=phase == "implement")
            result["sessions"].append({"phase": phase, **session})
            native = host == "codex" or set(session["registered_skills"]) == EXPECTED_SKILLS
            if phase == "unsupported":
                check = {"success": files(workspace) == baseline and not session["plugin_actions"]
                         and session["final_text"].strip() == "Bonjour"}
            elif phase == "plan":
                check = planned(root, workspace, row["depth"], baseline)
                check["success"] &= bool(session["plugin_actions"])
            elif phase == "implement":
                check = interrupted(root, workspace, row["depth"], baseline, session)
                checkpoint = session["checkpoint"]
                if check["success"]:
                    shutil.copytree(workspace, trial / "checkpoint/workspace", symlinks=True)
                    write(trial / "checkpoint/evidence.json", check)
            else:
                check = completed(root, workspace, row["depth"], baseline, checkpoint)
            check["success"] &= native and (session["success"] if phase != "implement" else True)
            check["package_unchanged"] = all(plugin_files(path) == record["plugin_sha256"]
                                             for path in (root, *(Path(installation[host + "_plugin"]) for host in hosts)))
            check["success"] &= check["package_unchanged"] and time.monotonic() - start <= record["total_timeout"]
            check["evaluator_unchanged"] = evaluator_identity() == record["evaluator_sha256"]
            check["success"] &= check["evaluator_unchanged"]
            result["checks"][phase] = check
            write(result_path, result)
            if not check["success"]:
                result["status"] = "failed"
                break
        else:
            pending = row.get("resume_host", actual_resume) != actual_resume
            result.update(success=None if pending else True, status="partial" if pending else "passed",
                          completed_host=actual_resume, cross_host_pending=pending)
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        result.update(status="failed", error=str(exc))
    result["seconds"] = round(time.monotonic() - start, 3)
    write(result_path, result)
    return result


def resume(directory, identity, *, model=None):
    """Continue the retained exact checkpoint at its original path after native sign-in."""
    record = read_json(directory / "matrix.json")
    row = next(item for item in record["rows"] if item["id"] == identity)
    trial = directory / identity
    prior = read_json(trial / "result.json", {})
    if not prior.get("cross_host_pending") or (trial / "cross-host-result.json").exists():
        raise ValueError("No unattempted cross-host continuation remains")
    host = row["resume_host"]
    selected_model = model or record["models"].get(host)
    if not selected_model or not authenticated(host):
        return {"success": None, "status": "blocked", "blocked_hosts": [host]}
    installation = record["installation"]
    if evaluator_identity() != record.get("evaluator_sha256"):
        raise ValueError("Evaluator source changed; resume with the frozen tools recorded in this matrix")
    if plugin_files(Path(installation[host + "_plugin"])) != record["plugin_sha256"]:
        raise ValueError("Staged native package changed")
    evidence = read_json(trial / "checkpoint/evidence.json")
    saved = trial / "checkpoint/workspace"
    if files(saved) != evidence["workspace_sha256"]:
        raise ValueError("Retained checkpoint changed")
    workspace = trial / "workspace"
    workspace.rename(trial / "previous-completion")
    shutil.copytree(saved, workspace, symlinks=True)
    result_path = trial / "cross-host-result.json"
    write(result_path, {"success": False, "status": "running", "host": host})
    start = time.monotonic()
    try:
        root = Path(record["plugin_root"])
        baseline = read_json(trial / "baseline.json")
        retained_session = next(item for item in prior["sessions"] if item["phase"] == "implement")
        restoration = interrupted(root, workspace, row["depth"], baseline, retained_session)
        if not restoration["success"]:
            raise ValueError("Restored checkpoint no longer satisfies the original red contract")
        session = run_session(native_command(host, selected_model, record["efforts"][host], installation), workspace,
            trial / "cross-resume", prompt({**row, "host": host}, "resume"), host=host,
            plugin_root=Path(installation[host + "_plugin"]), timeout=min(record["timeout"], record["total_timeout"] - (time.monotonic() - start)))
        checks = completed(root, workspace, row["depth"], baseline, evidence["checkpoint"])
        native = host == "codex" or set(session["registered_skills"]) == EXPECTED_SKILLS
        unchanged = all(plugin_files(path) == record["plugin_sha256"] for path in (root, Path(installation[host + "_plugin"])))
        evaluator_unchanged = evaluator_identity() == record["evaluator_sha256"]
        success = session["success"] and native and checks["success"] and unchanged and evaluator_unchanged and time.monotonic() - start <= record["total_timeout"]
        result = {"success": success, "status": "passed" if success else "failed", "session": session, "checks": checks,
                  "restored_checkpoint": restoration, "package_unchanged": unchanged, "evaluator_unchanged": evaluator_unchanged}
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        result = {"success": False, "status": "failed", "error": str(exc)}
    write(result_path, result)
    return result


def report(directory, host=None):
    record = read_json(directory / "matrix.json")
    rows = []
    for row in record["rows"]:
        if host and row["host"] != host:
            continue
        pending = "unexecuted" if record["models"].get(row["host"]) else "blocked"
        result = {**row, **read_json(directory / row["id"] / "result.json", {"status": pending, "success": None})}
        cross = read_json(directory / row["id"] / "cross-host-result.json")
        if cross:
            result.update(cross_host=cross, success=cross["success"], status=cross["status"])
        rows.append(result)
    return {"success": all(row["success"] is True for row in rows), "host_filter": host, "rows": rows,
            "model_sessions": sum(len(row.get("sessions", [])) + int("session" in row.get("cross_host", {})) for row in rows),
            "limits": "A small deterministic fixture tests native workflow contracts, not general code quality. Follow-ups and continuations use fresh sessions. Native authentication remains owned by each CLI. Unsupported requests are tested once per host."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "run", "resume", "report"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--plugin-root", type=Path, default=ROOT)
    parser.add_argument("--case")
    parser.add_argument("--host", choices=("codex", "claude"), help="Run unattempted rows for one host, stopping at the first failure; or filter report")
    parser.add_argument("--resume-host", choices=("codex", "claude"), help="Also verify same-host continuation while preserving cross-host work as pending")
    parser.add_argument("--codex-model")
    parser.add_argument("--claude-model")
    parser.add_argument("--effort", choices=("low", "medium", "high"), default="medium")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--total-timeout", type=int, default=1500)
    args = parser.parse_args()
    try:
        if args.operation == "prepare":
            result = prepare(args.directory.resolve(), args.plugin_root.resolve(), {"codex": args.codex_model or "gpt-5.5", "claude": args.claude_model},
                             {host: args.effort for host in ("codex", "claude")}, timeout=args.timeout, total_timeout=args.total_timeout)
        elif args.operation in {"run", "resume"}:
            directory = args.directory.resolve()
            record = read_json(directory / "matrix.json")
            for host, model in (("codex", args.codex_model), ("claude", args.claude_model)):
                if model:
                    if record["models"].get(host) not in (None, model):
                        parser.error("Changing an already pinned model requires a fresh matrix")
                    record["models"][host] = model
            write(directory / "matrix.json", record)
            if args.operation == "resume":
                if not args.case:
                    parser.error("resume requires --case")
                result = resume(directory, args.case)
            elif args.case:
                result = run(directory, args.case, resume_host=args.resume_host)
            elif args.host:
                for row in record["rows"]:
                    if row["host"] != args.host:
                        continue
                    path = directory / row["id"] / "result.json"
                    if read_json(path, {}).get("status") in {"failed", "running"}:
                        break
                    if unattempted(path) and run(directory, row["id"], resume_host=args.resume_host)["status"] in {"failed", "blocked"}:
                        break
                result = report(directory, args.host)
            else:
                parser.error("run requires --case or --host")
        else:
            result = report(args.directory.resolve(), args.host)
        print(json.dumps(result, indent=2))
        return 0 if result.get("success", True) is not False else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
