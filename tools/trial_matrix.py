#!/usr/bin/env python3
"""Run complete tasks under one evaluator; retain every scheduled attempt."""
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_comparison import packets, reviews  # noqa: E402


def read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def schedule(cases: list[str], depths: list[str], repeats: int, comparison: bool) -> list[dict]:
    items = []
    for repeat in range(1, repeats + 1):
        for depth in depths:
            for case in cases:
                block = f"{case}-{depth}-{repeat}"
                arms = ["previous", "current", "plain"] if comparison else ["current"]
                offset = (len(items) // len(arms)) % len(arms)
                for arm in arms[offset:] + arms[:offset]:
                    items.append({"id": f"{block}-{arm}" if comparison else block,
                                  "case": case, "depth": depth, "arm": arm, "block": block})
    return items


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
        groups[(item.get("arm", "current"), item["case"], item["depth"])].append({
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
    for (arm, case, depth), attempts in sorted(groups.items()):
        times = [row["runner_seconds"] for row in attempts if row["runner_seconds"] is not None]
        summaries.append({"arm": arm, "case": case, "depth": depth, "scheduled": len(attempts),
                          "outcomes": dict(Counter(row["status"] for row in attempts)),
                          "timed_attempts": len(times),
                          "median_runner_seconds": statistics.median(times) if times else None,
                          "attempts": attempts})
    comparative = reviews(directory)
    expected = {item["block"] for item in manifest["trials"]} if manifest.get("comparison") else set()
    completed = {row["block"] for row in comparative if row["valid"]}
    quality_complete = not manifest.get("comparison") or (completed == expected and len(comparative) == len(expected))
    return {"success": quality_complete and all(row["status"] == "passed" for rows in groups.values() for row in rows),
            "plugin_root": manifest["plugin_root"], "evaluator_root": manifest.get("evaluator_root"),
            "runner": manifest["runner"], "settings": manifest.get("settings"), "groups": summaries,
            "comparative_quality": comparative,
            "comparative_review": {"expected": len(expected), "completed": len(completed), "complete": quality_complete},
            "cancelled": manifest.get("cancelled"),
            "limits": "All scheduled attempts include failures and timeouts. One evaluator judges all arms. Runner time includes planning/coding/verification; external oracle/review time is separate. Plain-agent workflow is not applicable. Usage is runner-reported; phase counts classify commands only, not model tokens. Requested models/settings are pinned, backend identity is not attested. Repetitions are fresh workspaces, not retries. Small samples establish observations, not speedup. Reports use saved results; recheck edited candidates/reviews."}


def run_trial(directory: Path, plugin_root: Path, item: dict, runner: list[str], timeout: int) -> None:
    start = time.monotonic()
    trial = directory / item["id"]
    command = [sys.executable, str(ROOT / "tools/coding_trials.py"), "run", str(trial),
               "--plugin-root", str(plugin_root), "--case", item["case"], "--depth", item["depth"],
               "--timeout", str(timeout)]
    if item.get("arm") == "plain":
        command.append("--plain-agent")
    # The fixed trial process owns the runner deadline and descendant cleanup.
    with (directory / (item["id"] + ".log")).open("w") as log:
        completed = subprocess.run([*command, "--runner", *runner], stdout=log, stderr=subprocess.STDOUT)
    trial.mkdir(exist_ok=True)
    (trial / "attempt.json").write_text(json.dumps({"returncode": completed.returncode,
        "seconds": round(time.monotonic() - start, 3)}) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("run", "compare", "report", "blind", "apply-reviews"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--plugin-root", type=Path, default=ROOT)
    parser.add_argument("--previous-root", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh"))
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--depths", nargs="+", choices=("lean", "standard", "deep"))
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--seed", type=int, default=0, help="Reproducible blinded candidate ordering")
    parser.add_argument("--runner", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    directory, root = args.directory.resolve(), args.plugin_root.resolve()
    if args.operation in {"run", "compare"}:
        comparison = args.operation == "compare"
        if min(args.jobs, args.repeats, args.timeout) < 1:
            parser.error("jobs, repeats and timeout must be positive")
        if comparison:
            if not args.previous_root or not args.model or not args.effort or args.jobs != 1 or args.runner:
                parser.error("compare requires --previous-root, --model, --effort, serial --jobs 1 and the pinned built-in runner")
            runner = [sys.executable, str(ROOT / "tools/coding_trial_runner.py"), "--model", args.model,
                      "--effort", args.effort, "--codex", args.codex]
        elif not args.runner:
            parser.error("run requires --runner")
        else:
            runner = args.runner
        cases = read(ROOT / "examples/evals/coding/cases.json")
        selected = args.cases or (["godfile", "import-preview"] if comparison else ["summary", "cleanup", "resume"])
        depths = args.depths or (["standard"] if comparison else ["lean", "standard", "deep"])
        if set(selected) - cases.keys() or len(set(selected)) != len(selected) or len(set(depths)) != len(depths):
            parser.error("cases must exist in the fixed evaluator; cases and depths must not repeat")
        if comparison and "resume" in selected:
            parser.error("Forge's persisted resume checkpoint has no comparable plain-agent arm")
        roots = {"current": root, "previous": args.previous_root.resolve() if args.previous_root else root, "plain": root}
        if any(not (path / "scripts/zagrosi_skills.py").is_file() for path in set(roots.values())):
            parser.error("plugin roots must contain scripts/zagrosi_skills.py")
        directory.mkdir(parents=True, exist_ok=False)
        items = schedule(selected, depths, args.repeats, comparison)
        (directory / "matrix.json").write_text(json.dumps({"plugin_root": str(root), "evaluator_root": str(ROOT),
            "roots": {arm: str(path) for arm, path in roots.items()}, "runner": runner,
            "comparison": comparison, "seed": args.seed, "cases": cases,
            "settings": {"model": args.model, "effort": args.effort, "jobs": args.jobs},
            "timeout": args.timeout, "trials": items}, indent=2) + "\n")
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            list(executor.map(lambda item: run_trial(directory, roots[item["arm"]], item, runner, args.timeout), items))
    elif args.operation == "blind":
        print(json.dumps(packets(directory), indent=2))
        return 0
    elif args.operation == "apply-reviews":
        reviews(directory, apply=True)
        manifest = read(directory / "matrix.json")
        for item in manifest["trials"]:
            trial = directory / item["id"]
            subprocess.run([sys.executable, str(ROOT / "tools/coding_trials.py"), "check", str(trial),
                            "--review", str(trial / "review.json")], stdout=subprocess.DEVNULL, check=False)
    result = report(directory)
    (directory / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
