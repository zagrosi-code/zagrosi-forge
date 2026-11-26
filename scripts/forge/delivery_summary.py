"""Render the delivery status projection as a reviewer-facing Markdown handoff."""
from __future__ import annotations

import re
import unicodedata


STAGE_LABELS = {"review": "Review", "merge": "Merge", "ci": "CI", "deploy": "Deployment",
                "live": "Live check", "acceptance": "Acceptance"}


def _literal(value: str) -> str:
    """Keep user-supplied names on one line and inert inside a Markdown code span."""
    text = "".join(" " if unicodedata.category(char).startswith("C") else char for char in value)
    ticks = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (ticks + 1)
    return f"{fence} {text} {fence}" if ticks else f"`{text}`"


def markdown(report: dict) -> str:
    lines = []
    for pr, row in report["prs"].items():
        if lines:
            lines.append("")
        lines.append(f"## PR {_literal(pr)}")
        if report.get("environment"):
            lines.append(f"Environment {_literal(report['environment'])}")
        if row["head_sha"]:
            lines.append(f"PR head {_literal(row['head_sha'])}")
        if row["merged_sha"]:
            lines.append(f"Landed at {_literal(row['merged_sha'])}")

        actions = []
        lines.extend(["", "### Verification", ""])
        checks = row["verification"]["checks"]
        if not checks:
            lines.append("No checks recorded.")
            actions.append("Record a check for the current revision.")
        for check in checks:
            test = check["test"]
            group = test.get("group")
            label = ", ".join(map(_literal, group["sections"])) if group else _literal(test["check"])
            if group:
                label += f" (run {_literal(group['run_id'])})"
            status = check["status"]
            lines.append(f"- {label} — {status} at {_literal(test['tested_sha'])}")
            if status in {"recheck", "failed", "timed_out", "skipped"}:
                actions.append(f"Rerun {label} on the current revision.")

        lines.extend(["", "### Delivery", ""])
        for stage, label in STAGE_LABELS.items():
            observed = row["stages"][stage]
            lines.append(f"- {label} — {observed['outcome']}")
            for name, evidence in observed.get("requirements", {}).items():
                outcome = evidence["outcome"]
                lines.append(f"  - {_literal(name)} — {outcome}")
                if outcome != ("accepted" if stage == "acceptance" else "passed"):
                    action = "Obtain a decision for" if stage == "acceptance" else "Check"
                    actions.append(f"{action} {_literal(name)} ({outcome}).")

        artifacts = row.get("artifacts", [])
        if artifacts:
            lines.extend(["", "### Artifacts", ""])
        for artifact in artifacts:
            size = f", {artifact['size_bytes']} bytes" if "size_bytes" in artifact else ""
            lines.append(f"- {_literal(artifact['name'])} — {_literal(artifact['digest'])}{size}, "
                         f"{_literal(artifact['environment'])}, {artifact['status']} at {_literal(artifact['sha'])}")

        lines.extend(["", "### Next action", ""])
        lines.extend(f"- {action}" for action in actions)
        if not actions:
            lines.append("Review the recorded evidence and outstanding delivery stages.")
    return "\n".join(lines) if lines else "No tracked pull requests."
