"""Forge quality."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json

from . import markdown as _markdown
from . import models as _models
from . import output as _output
from . import policy as _policy
from . import session as _session
from . import storage as _storage

def finding(
    severity: str,
    code: str,
    message: str,
    path: Path | str | None = None,
    recommendation: str | None = None,
    category: str | None = None,
) -> _models.Finding:
    return _models.Finding(
        severity=severity,
        code=code,
        message=message,
        path=str(path) if path else None,
        recommendation=recommendation,
        category=category or category_for_code(code),
    )


def category_for_code(code: str) -> str:
    if any(term in code for term in ("security", "privacy", "auth", "permission")):
        return "security"
    if any(term in code for term in ("traceability", "requirement", "orphan")):
        return "traceability"
    if any(term in code for term in ("test", "tdd")):
        return "testing"
    if any(term in code for term in ("scope", "section-too", "vague", "file-path")):
        return "scope"
    if any(term in code for term in ("migration", "rollout", "rollback")):
        return "migration"
    if any(term in code for term in ("readiness", "state", "missing")):
        return "readiness"
    return "general"


def quality_score(findings: list[_models.Finding], profile: str = "solo") -> int:
    penalties = {"critical": 35, "high": 20, "medium": 10, "low": 4}
    profile_weights = _policy.QUALITY_PROFILES.get(profile, _policy.QUALITY_PROFILES["solo"])
    total_penalty = 0
    for item in findings:
        weight = profile_weights.get(item.category, profile_weights["general"])
        total_penalty += round(penalties.get(item.severity, 0) * weight)
    score = 100 - total_penalty
    return max(0, min(100, score))


def quality_payload(
    name: str,
    findings: list[_models.Finding],
    extras: dict[str, Any] | None = None,
    profile: str = "solo",
    strict: bool = False,
) -> dict[str, Any]:
    score = quality_score(findings, profile)
    blocking_severities = {"critical", "high"}
    if strict:
        blocking_severities.add("medium")
    blocking = [item for item in findings if item.severity in blocking_severities]
    payload: dict[str, Any] = {
        "success": not blocking,
        "gate": name,
        "profile": profile,
        "strict": strict,
        "score": score,
        "finding_count": len(findings),
        "findings": [item.to_dict() for item in findings],
    }
    if extras:
        payload.update(extras)
    return payload


def write_findings_export(payload: dict[str, Any], output_path: Path, export_format: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    findings = payload.get("findings", [])
    if export_format == "jsonl":
        output_path.write_text(
            "\n".join(json.dumps(item, sort_keys=True) for item in findings) + ("\n" if findings else ""),
            encoding="utf-8",
        )
        return
    if export_format == "sarif":
        sarif = {
            "version": "2.1.0",
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "Zagrosi Forge",
                            "informationUri": "https://github.com/zagrosi-code/zagrosi-forge",
                        }
                    },
                    "results": [
                        {
                            "ruleId": item["code"],
                            "level": {
                                "critical": "error",
                                "high": "error",
                                "medium": "warning",
                                "low": "note",
                            }.get(item["severity"], "warning"),
                            "message": {"text": item["message"]},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": item.get("path", "")}
                                    }
                                }
                            ],
                        }
                        for item in findings
                    ],
                }
            ],
        }
        output_path.write_text(json.dumps(sarif, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    raise ValueError(f"Unsupported export format: {export_format}")


def emit_payload(payload: dict[str, Any], args: argparse.Namespace, exit_code: int | None = None) -> int:
    if exit_code is None:
        exit_code = 0 if payload.get("success", False) else 1
    captured = _session._QUALITY_CAPTURE.get()
    if captured is not None:
        if captured:
            raise ValueError("Gate emitted more than one quality payload")
        captured.update(payload)
        return exit_code
    export_path = getattr(args, "export", None)
    if export_path:
        write_findings_export(payload, _storage.resolve_path(export_path), getattr(args, "export_format", "jsonl"))
    return _output.print_json(payload, exit_code)


def quality_from_args(
    name: str,
    findings: list[_models.Finding],
    args: argparse.Namespace,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return quality_payload(
        name,
        findings,
        extras,
        profile=getattr(args, "profile", "solo"),
        strict=getattr(args, "strict", False),
    )


def emit_quality(
    name: str,
    findings: list[_models.Finding],
    args: argparse.Namespace,
    extras: dict[str, Any] | None = None,
    exit_code: int | None = None,
) -> int:
    return emit_payload(quality_from_args(name, findings, args, extras), args, exit_code)


def add_budget_finding(
    findings: list[_models.Finding],
    actual_words: int,
    budget_words: int,
    artifact_label: str,
    code: str,
    path: Path,
) -> None:
    if actual_words > budget_words:
        findings.append(
            finding(
                "medium",
                code,
                f"{artifact_label} has {actual_words} words; budget is {budget_words}.",
                path,
                "Delete repetition; keep only decisions, evidence, contracts, tests, risks, and acceptance criteria.",
            )
        )


def require_terms(
    findings: list[_models.Finding],
    text: str,
    groups: dict[str, list[str]],
    path: Path,
    severity: str = "medium",
) -> None:
    for label, terms in groups.items():
        if not _markdown.contains_any(text, terms):
            findings.append(
                finding(
                    severity,
                    f"missing-{label}",
                    f"Missing coverage for {label.replace('-', ' ')}.",
                    path,
                    f"Add a concrete {label.replace('-', ' ')} section or equivalent prose.",
                )
            )


def add_term_findings(findings: list[_models.Finding], text: str, groups: dict[str, list[str]], path: Path, severity: str) -> None:
    for label, terms in groups.items():
        if not _markdown.contains_any(text, terms):
            findings.append(
                finding(
                    severity,
                    f"missing-{label}",
                    f"Missing {label.replace('-', ' ')} evidence.",
                    path,
                    f"Add concrete {label.replace('-', ' ')} details backed by files, commands, contracts, or tests.",
                )
            )
