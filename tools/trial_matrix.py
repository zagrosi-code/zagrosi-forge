#!/usr/bin/env python3
"""Run repeated complete coding trials and report every scheduled attempt."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time


def read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def report(directory: Path) -> dict:
    manifest = read(directory / "matrix.json")
    if not manifest.get("trials"):
        raise ValueError("A nonempty matrix.json is required")
    groups = defaultdict(list)
    for item in manifest["trials"]:
        trial = directory / item["id"]
        result, attempt = read(trial / "result.json"), read(trial / "attempt.json")
        runner = result.get("runner") or read(trial / "trial.json").get("runner") or {}
        complete = result.get("success") is True and bool(attempt) and runner.get("returncode") == 0
        status = "passed" if complete else "failed" if result or attempt else "cancelled" if manifest.get("cancelled") else "pending"
        groups[(item["case"], item["depth"])].append({
            "id": item["id"], "status": status, "runner_seconds": runner.get("seconds"),
            "attempt_seconds": attempt.get("seconds"), "timed_out": runner.get("timed_out"),
            "returncode": attempt.get("returncode"),
            "reported_telemetry": result.get("reported_telemetry") or read(trial / "telemetry.json") or None,
            "behavior": result.get("behavior", {}).get("success"),
            "workflow": result.get("workflow", {}).get("success"),
            "cleanup": result.get("cleanup", {}).get("success"),
            "provenance": result.get("plugin_provenance", {}).get("status"),
            "before": result.get("before"), "after": result.get("after"),
        })
    summaries = []
    for (case, depth), attempts in sorted(groups.items()):
        times = [row["runner_seconds"] for row in attempts if row["runner_seconds"] is not None]
        summaries.append({"case": case, "depth": depth, "scheduled": len(attempts),
                          "outcomes": dict(Counter(row["status"] for row in attempts)),
                          "timed_attempts": len(times),
                          "median_runner_seconds": statistics.median(times) if times else None,
                          "attempts": attempts})
    return {"success": all(row["status"] == "passed" for rows in groups.values() for row in rows),
            "plugin_root": manifest["plugin_root"], "runner": manifest["runner"], "groups": summaries,
            "cancelled": manifest.get("cancelled"),
            "limits": "All scheduled attempts are included, including failures and timeouts. Runner time includes planning, coding and agent verification; external oracle/review time is separate. Usage is runner-reported or unknown. Repetitions are fresh workspaces, not retries; no speedup or cross-model comparison is inferred. Reports aggregate saved results; rerun each trial check after candidate or review edits."}


def run_trial(directory: Path, root: Path, item: dict, runner: list[str], timeout: int) -> None:
    start = time.monotonic()
    trial = directory / item["id"]
    command = [sys.executable, str(root / "tools/coding_trials.py"), "run", str(trial),
               "--case", item["case"], "--depth", item["depth"], "--timeout", str(timeout), "--runner", *runner]
    # The trial process owns its runner's deadline and descendant cleanup.
    with (directory / (item["id"] + ".log")).open("w") as log:
        completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    trial.mkdir(exist_ok=True)
    (trial / "attempt.json").write_text(json.dumps({"returncode": completed.returncode,
        "seconds": round(time.monotonic() - start, 3)}) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("run", "report"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--plugin-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cases", nargs="+", default=["summary", "cleanup", "resume"])
    parser.add_argument("--depths", nargs="+", choices=("lean", "standard", "deep"), default=["lean", "standard", "deep"])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--runner", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    directory, root = args.directory.resolve(), args.plugin_root.resolve()
    if args.operation == "run":
        if not args.runner or min(args.jobs, args.repeats, args.timeout) < 1:
            parser.error("run requires --runner and positive jobs, repeats and timeout")
        cases = read(root / "examples/evals/coding/cases.json")
        if set(args.cases) - cases.keys() or len(set(args.cases)) != len(args.cases) or len(set(args.depths)) != len(args.depths):
            parser.error("cases must exist; cases and depths must not repeat")
        directory.mkdir(parents=True, exist_ok=False)
        # Alternate depths/cases across repetitions; never overwrite an attempt.
        items = [{"id": f"{case}-{depth}-{repeat}", "case": case, "depth": depth}
                 for repeat in range(1, args.repeats + 1) for depth in args.depths for case in args.cases]
        (directory / "matrix.json").write_text(json.dumps({"plugin_root": str(root), "runner": args.runner,
            "timeout": args.timeout, "trials": items}, indent=2) + "\n")
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            list(executor.map(lambda item: run_trial(directory, root, item, args.runner, args.timeout), items))
    result = report(directory)
    (directory / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
