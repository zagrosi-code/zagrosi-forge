"""Render the delivery status projection as a reviewer-facing Markdown handoff."""
from __future__ import annotations

import re
import sys
import unicodedata
from operator import itemgetter


STAGE_LABELS = {"review": "Review", "merge": "Merge", "ci": "CI", "deploy": "Deployment",
                "live": "Live check", "acceptance": "Acceptance"}


def _literal(value: str) -> str:
    """Keep user-supplied names on one line and inert inside a Markdown code span."""
    text = "".join(" " if unicodedata.category(char).startswith("C") else char for char in value)
    ticks = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (ticks + 1)
    return f"{fence} {text} {fence}" if ticks else f"`{text}`"


def _test_label(test):
    group = test.get("group")
    if group:
        sections = ", ".join(map(_literal, group["sections"]))
        return f"{sections} (run {_literal(group['run_id'])})"
    return _literal(test["check"])


def _verification(row, actions):
    lines = ["### Verification", ""]
    checks = row["verification"]["checks"]
    landing = row["merged_sha"]
    upstream_changed = {"base_sha", "base_ref", "ancestors"}.intersection(row["verification"]["changed"])
    if upstream_changed and not landing:
        ref = _literal(row["base_ref"])
        base = row["base_sha"]
        if base:
            actions.append(f"Update the child to include {ref} at {_literal(base)} and its tracked parents before retesting.")
        else:
            actions.append(f"Refresh or retarget the missing base {ref} before retesting.")
    if not checks:
        lines.append("No checks recorded.")
        actions.append("Record a verification run.")
    for check in checks:
        test = check["test"]
        label = _test_label(test)
        status = check["status"]
        lines.append(f"- {label} — {status} at {_literal(test['tested_sha'])}")
        if status != "current":
            target = f"landing {_literal(landing)}" if landing else "the updated PR"
            actions.append(f"Rerun {label} on {target}.")
    return lines


def _stages(row, actions):
    lines = ["### Delivery", ""]
    for stage, label in STAGE_LABELS.items():
        observed = row["stages"][stage]
        lines.append(f"- {label} — {observed['outcome']}")
        for name, evidence in observed.get("requirements", {}).items():
            outcome = evidence["outcome"]
            lines.append(f"  - {_literal(name)} — {outcome}")
            if outcome != ("accepted" if stage == "acceptance" else "passed"):
                action = "Obtain a decision for" if stage == "acceptance" else "Check"
                actions.append(f"{action} {_literal(name)} ({outcome}).")
    return lines


def _artifacts(receipts):
    lines = ["### Artifacts", ""]
    for artifact in receipts:
        if artifact["kind"] == "image":
            identity = _literal(artifact["image"])
        else:
            identity = f"{_literal(artifact['digest'])}, {artifact['size_bytes']} bytes"
        lines.append(f"- {_literal(artifact['name'])} — {identity}, "
                     f"{_literal(artifact['stage'])} in {_literal(artifact['environment'])}, "
                     f"{artifact['status']} at {_literal(artifact['sha'])}")
    return lines


def _history(row):
    lines = ["### Historical evidence", ""]
    events = [(test["observed_at"], f"Test {_test_label(test)}", test["outcome"], test["tested_sha"])
              for test in row["tests"]]
    for event in row["observations"]:
        label = STAGE_LABELS[event["stage"]]
        if name := event.get("name"):
            label += f" {_literal(name)}"
        if environment := event["environment"]:
            label += f" in {_literal(environment)}"
        events.append((event["observed_at"], label, event["outcome"], event["sha"]))
    if not events:
        return [*lines, "No tests or delivery observations recorded."]
    for when, label, outcome, sha in sorted(events, key=itemgetter(0)):
        lines.append(f"- {_literal(when)} — {label} — {outcome} at {_literal(sha)}")
    return lines


def markdown(report: dict) -> str:
    lines = []
    for pr, row in report["prs"].items():
        if lines:
            lines.append("")
        lines.append(f"## PR {_literal(pr)}")
        if report["environment"]:
            lines.append(f"Environment {_literal(report['environment'])}")
        elif row["environments"]:
            lines.append("Available environments: " + ", ".join(map(_literal, row["environments"])))
        if row["head_sha"]:
            lines.append(f"PR head {_literal(row['head_sha'])}")
        if row["merged_sha"]:
            lines.append(f"Landed at {_literal(row['merged_sha'])}")

        actions = []
        lines.append("")
        lines.extend(_verification(row, actions))
        lines.append("")
        lines.extend(_stages(row, actions))
        if row.get("artifacts"):
            lines.extend(["", *_artifacts(row["artifacts"])])
        lines.extend(["", "### Next action", ""])
        if actions:
            lines.extend(f"- {action}" for action in actions)
        else:
            lines.append("Review the recorded evidence and outstanding delivery stages.")
        if "tests" in row:
            lines.extend(["", *_history(row)])
    return "\n".join(lines) if lines else "No tracked pull requests."


def print_markdown(report: dict) -> None:
    """Write redirected handoffs as UTF-8 regardless of the host code page."""
    text = markdown(report)
    binary = getattr(sys.stdout, "buffer", None)
    if binary is not None and not sys.stdout.isatty():
        binary.write((text + "\n").encode("utf-8"))
    else:
        print(text)
