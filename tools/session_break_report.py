"""Session-break reports: every scheduled attempt, per-arm summaries and the protocol's decision rule."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import statistics

from coding_trial_comparison import applied_review, reviews
from coding_trial_outcomes import accepted_outcomes, known_total
from trial_matrix import read

ARMS = ("forge-lean", "forge-state", "notes", "plain")
# H0–H2 compare these three; forge-state, the state-first proposal's fourth arm, adds its adoption test.
CORE_ARMS = ("forge-lean", "notes", "plain")
TOKENS =("input_tokens", "cached_input_tokens", "cache_creation_input_tokens", "uncached_input_tokens",
          "output_tokens", "reasoning_output_tokens")


def attempt_row(directory: Path, item: dict, sections: int | None = None) -> dict:
    trial = directory / item["id"]
    attempt, result, analysis, checkpoint, telemetry = (read(trial / name) for name in (
        "attempt.json", "result.json", "session-break.json", "checkpoint.json", "telemetry.json"))
    status = attempt.get("status", "pending")
    if status == "running":
        status = "interrupted"
    quality = (result.get("common_quality") or {}).get("success")
    common = ("pending" if status == "pending" else "passed" if quality is True and status == "completed"
              else "failed" if quality is False or status != "completed" else "unknown")
    sessions = analysis.get("sessions") or []
    seconds = [row.get("seconds") for row in (result.get("runner") or {}).get("sessions") or []]
    final = analysis.get("final") or {}
    return {
        "id": item["id"], "arm": item["arm"], "case": item["case"], "block": item["block"], "status": status,
        "common_quality": common, "workflow": (result.get("workflow") or {}).get("success"),
        "behavior": (result.get("behavior") or {}).get("success"),
        "cleanup": (result.get("cleanup") or {}).get("success"),
        "reported_telemetry": {"totals": telemetry.get("totals") or dict.fromkeys((*TOKENS, "total_tokens")),
                               "reported_cost_usd": telemetry.get("reported_cost_usd")},
        "attempt_seconds": known_total([row.get("process", {}).get("seconds") for row in
                                        (result.get("runner") or {}).get("sessions") or []]) if seconds else None,
        "sessions": [{key: row.get(key) for key in ("seconds", "returncode", "timed_out", "commands", "helper_calls")}
                     for row in sessions],
        # A session without logged events leaves the attempt's helper calls unknown rather than zero.
        "helper_calls": sum(row["helper_calls"] for row in sessions)
        if sessions and all(row.get("events") for row in sessions) else None,
        # The state-first proposal's allowance: one helper call per section plus three.
        "helper_budget": None if sections is None else sections + 3,
        "missing_tools": telemetry.get("missing_tools"),
        "reorientation": sessions[1].get("reorientation") if len(sessions) == 2 else None,
        "regressions": analysis.get("regressions"),
        "probes": final.get("probes"), "checkpoint_unchanged": analysis.get("checkpoint_unchanged"),
        "checkpoint": {key: checkpoint.get(key) for key in ("changed_files", "planning", "probes", "compatibility")},
        "final_planning": final.get("planning"),
        "outside_references": sum(len(row.get("outside_references") or []) for row in sessions) if sessions else None,
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
    if len(runner_record.get("sessions") or []) == 1:
        reasons.append("session 1 failure")
    elif runner_record.get("returncode") != 0:
        reasons.append("session 2 timeout" if runner_record.get("timed_out") else "session 2 failure")
    final = analysis.get("final") or {}
    probes = list((final.get("probes") or {}).values())
    if final and not (final.get("compatibility") or {}).get("passed"):
        reasons.append("compatibility regression" if (analysis.get("regressions") or {}).get("compatibility")
                       else "compatibility")
    elif final and not (final.get("oracle") or {}).get("passed"):
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
    observed = [row["regressions"] for row in rows if row["regressions"] and row["regressions"]["any"] is not None]
    # Per-attempt figures use attempts whose sessions both ended on their own; a cut-short session undercounts.
    calls = [row for row in rows if row["helper_calls"] is not None and all(
        session["returncode"] == 0 and not session["timed_out"] for session in row["sessions"])]
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
        "regressions": {"attempts": sum(value["any"] for value in observed),
                        "compatibility": sum(value["compatibility"] is True for value in observed),
                        "tests": sum(bool(value["tests"]) and any(value["tests"].values()) for value in observed),
                        "withheld_clauses": sum(bool(value["withheld_clauses"]) for value in observed),
                        "observed": len(observed), "unobserved": len(rows) - len(observed)},
        "helper_calls": sum(row["helper_calls"] or 0 for row in rows),
        "helper_calls_per_attempt": {
            "median": statistics.median(row["helper_calls"] for row in calls) if calls else None,
            "max": max((row["helper_calls"] for row in calls), default=None),
            "median_over_budget": statistics.median(row["helper_calls"] - row["helper_budget"] for row in calls)
            if calls and all(row["helper_budget"] is not None for row in calls) else None,
            "observed": len(calls)},
        "outside_references":known_total([row["outside_references"] for row in rows]),
        "rejections": dict(Counter(reason for row in rows for reason in row["rejection"])),
    }


def beats(first: dict, second: dict) -> bool:
    """More accepted without more regressed attempts, or fewer regressed without fewer accepted."""
    more, fewer = first["accepted"], first["regressions"]["attempts"]
    other, regressed = second["accepted"], second["regressions"]["attempts"]
    return (more > other and fewer <= regressed) or (fewer < regressed and more >= other)


def token_ratio(first: dict, second: dict) -> float | None:
    tokens, other = first["total_tokens"]["total"], second["total_tokens"]["total"]
    return tokens / other if tokens is not None and other else None


def state_first(arms: dict, pairs: dict) -> dict:
    """H2 with forge-state in forge-lean's place, and H3: the state-first proposal's bar for the default lean."""
    state, lean, notes = (arms[arm] for arm in ("forge-state", "forge-lean", "notes"))
    ratio = token_ratio(state, notes)
    per_state, per_lean = state["total_tokens"]["per_accepted"], lean["total_tokens"]["per_accepted"]
    # At most half of today's lean tokens per accepted result; an arm that accepted nothing has no such cost.
    tokens = (None if state["total_tokens"]["total"] is None or lean["total_tokens"]["total"] is None
              else False if not state["accepted"] else True if not lean["accepted"] else per_state <= per_lean / 2)
    over = state["helper_calls_per_attempt"]["median_over_budget"]
    criteria = {"accepted": state["accepted"] >= max(lean["accepted"], notes["accepted"]),
                "regressions": state["regressions"]["attempts"] <= min(lean["regressions"]["attempts"],
                                                                    notes["regressions"]["attempts"]),
                "tokens_per_accepted": tokens, "helper_calls": None if over is None else over <= 0}
    values = list(criteria.values())
    return {"H2": pairs["forge-state>notes"] and (None if ratio is None else ratio <= 1.5),
            "H3": False if False in values else None if None in values else True, "criteria": criteria,
            "state_to_notes_tokens": ratio,
            "state_to_lean_tokens_per_accepted": per_state / per_lean if per_state is not None and per_lean else None,
            "median_helper_calls": state["helper_calls_per_attempt"]["median"]}


def decide(arms: dict, blockers: list[str]) -> dict:
    """Apply the protocol's decision rule to complete observations only; small samples cannot establish an effect."""
    if not set(CORE_ARMS) <= set(arms):
        blockers = [*blockers, "the decision rule needs the forge-lean, notes and plain arms"]
    if blockers:
        return {"complete": False, "blockers": blockers}
    present = [arm for arm in ARMS if arm in arms]
    pairs = {f"{first}>{second}": beats(arms[first], arms[second]) for first in present for second in present
             if first != second}
    ratio = token_ratio(arms["forge-lean"], arms["notes"])
    result = {"complete": True, "blockers": [], "H1": pairs["forge-lean>plain"] and pairs["notes>plain"],
              "H2": pairs["forge-lean>notes"] and (None if ratio is None else ratio <= 1.5),
              "H0": not any(pairs[f"{first}>{second}"] for first in CORE_ARMS for second in CORE_ARMS if first != second),
              "pairs": pairs, "forge_to_notes_tokens": ratio}
    if "forge-state" in arms:
        result["state_first"] = state_first(arms, pairs)
    return result


def unapplied(directory: Path, reviewed: list[dict]) -> int:
    """Attempts whose recorded verdict does not yet rest on their block's valid blind review."""
    key = read(directory / "blind-key.json")
    count = 0
    for block in reviewed:
        for label, row in block["review"]["candidates"].items():
            trial = directory / key[f"{block['block']}/{label}"]
            used = (read(trial / "result.json").get("cleanup") or {}).get("review_file")
            count += not (read(trial / "review.json") == applied_review(block["review"], row) and used
                          and Path(used).resolve() == (trial / "review.json").resolve())
    return count


def build_report(directory: Path, manifest: dict) -> dict:
    """Per arm and case, per arm overall, and the decision rule once every observation is complete."""
    sections = manifest.get("sections") or {}
    rows = [attempt_row(directory, item, sections.get(item["case"])) for item in manifest["trials"]]
    keys = sorted({(row["arm"], row["case"]) for row in rows})
    groups = [{"arm": arm, "case": case, **summarize([row for row in rows if (row["arm"], row["case"]) == (arm, case)])}
              for arm, case in keys]
    arms = {arm: summarize([row for row in rows if row["arm"] == arm]) for arm in dict.fromkeys(row["arm"] for row in rows)}
    blocks = {item["block"] for item in manifest["trials"]}
    reviewed = [row for row in reviews(directory) if row["valid"]]
    blockers = [text for count, text in (
        (sum(row["status"] in ("pending", "interrupted") for row in rows), "attempts not run to the end"),
        (len(blocks) - len(reviewed), "blocks without a valid blind review"),
        (unapplied(directory, reviewed), "attempts whose result predates their blind review"),
        (sum(summary["regressions"]["unobserved"] for summary in arms.values()), "attempts with unobserved regressions"),
    ) if count for text in [f"{count} {text}"]]
    result = {
        "protocol": manifest["protocol"], "settings": manifest["settings"], "probe": manifest["probe"],
        "cli_versions": manifest["cli_versions"], "timeout": manifest["timeout"], "groups": groups, "arms": arms,
        "decision": decide(arms, blockers), "comparative_review": {"expected": len(blocks), "completed": len(reviewed)},
        "attempts": rows,
        "limits": "Every scheduled attempt counts, including failures, timeouts and pending work. Accepted means the "
                  "common-quality verdict, which needs an independent blind cleanup review. Tokens sum both sessions "
                  "and stay unknown when either session's usage is missing; Claude totals include subagents. "
                  "Re-orientation time is polled to the first src/ or tests/ change in session 2; Codex reports "
                  "usage per session only, so its tokens before that edit are unknown. A renamed test counts as "
                  "deleted. Helper calls count commands naming zagrosi_skills.py. Probe failures are evidence, not "
                  "certainty, and four repetitions per arm and case are observations, not statistically established "
                  "effects.",
    }
    return result
