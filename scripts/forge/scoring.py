"""Forge scoring."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json

from . import artifacts as _artifacts
from . import markdown as _markdown
from . import models as _models
from . import ownership as _ownership
from . import policy as _policy
from . import quality as _quality
from . import sections as _sections
from . import session as _session
from . import storage as _storage
from . import traceability as _traceability
from . import validation as _validation

def findings_from_payload(payload: dict[str, Any]) -> list[_models.Finding]:
    return [_models.Finding(item["severity"], item["code"], item["message"], item.get("path"), item.get("recommendation"), item.get("category", "general")) for item in payload["findings"]]


class FlightScoreInputs:
    """Score the preceding local gates only while their observed inputs match."""

    GATES = {
        "lint-plan": "plan_depth",
        "lint-sections": "section_readiness",
        "traceability": "traceability",
        "lint-implementation-readiness": "implementation_readiness",
    }

    def __init__(self, planning_dir: Path, depth: str, texts: dict):
        self.planning_dir, self.depth, self.texts = planning_dir, depth, texts
        self.findings: dict[str, list[_models.Finding]] = {}
        # Directory identities cover newly selected plan/review/section/state files.
        directories = {planning_dir, *(planning_dir / name for name in ("sections", "reviews", "implementation"))}
        source = _artifacts.planning_config(planning_dir).get("initial_file")
        if isinstance(source, str) and source.strip():
            candidate = Path(source).expanduser()
            directories.add((candidate if candidate.is_absolute() else planning_dir / candidate).parent)
        self.signatures = {path: self.signature(path) for path in directories}
        self.signatures.update((path, item[0]) for path, item in texts.items())

    @staticmethod
    def signature(path: Path):
        try:
            return _storage.file_signature(path)
        except OSError:
            return None

    def record(self, name: str, payload: dict[str, Any]) -> None:
        if (
            name not in self.GATES or not isinstance(payload.get("findings"), list)
            or payload.get("finding_count") != len(payload["findings"])
        ):
            return
        self.findings[self.GATES[name]] = findings_from_payload(payload)
        for path, (signature, _) in self.texts.items():
            # Keep the earliest observation, including a rewrite seen by later gates.
            self.signatures.setdefault(path, signature)

    def reusable(self, planning_dir: Path, depth: str, max_files: int) -> dict[str, list[_models.Finding]]:
        if (
            planning_dir != self.planning_dir or depth != self.depth or max_files != 8
            or self.findings.keys() != set(self.GATES.values())
            or any(self.signature(path) != signature for path, signature in self.signatures.items())
        ):
            return {}
        return self.findings


def lint_implementation_readiness(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    findings, extras = implementation_readiness_analysis(planning_dir, args.max_files)
    return _quality.emit_quality("implementation-readiness", findings, args, extras)


def forge_score(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    findings, components, score = forge_score_analysis(
        planning_dir, args.depth, args.profile, min_files=args.min_files, max_files=args.max_files,
    )
    weights = _policy.FORGE_COMPONENT_WEIGHTS.get(args.profile, _policy.FORGE_COMPONENT_WEIGHTS["solo"])
    blocking_findings = [item for item in findings if item.severity in {"critical", "high"}]
    advisory_findings = [item for item in findings if item.severity in {"medium", "low"}]
    blocking_score = _quality.quality_score(blocking_findings, args.profile)
    advisory_score = _quality.quality_score(advisory_findings, args.profile)
    trend = None
    history_path = planning_dir / ".forge" / "scores" / "history.jsonl"
    if history_path.exists():
        previous_rows = [json.loads(line) for line in _storage.read_text(history_path).splitlines() if line.strip()]
        if previous_rows:
            trend = score - int(previous_rows[-1].get("forge_score", score))
    payload = _quality.quality_payload(
        "forge-score",
        findings,
        {
            "planning_dir": str(planning_dir),
            "depth_mode": args.depth,
            "components": components,
            "component_weights": weights,
            "forge_score": score,
            "blocking_score": blocking_score,
            "advisory_score": advisory_score,
            "trend_delta": trend,
            "grade": "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D",
        },
        profile=args.profile,
        strict=args.strict,
    )
    payload["score"] = score
    if args.write_history:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": _storage.now_iso(),
            "depth_mode": args.depth,
            "profile": args.profile,
            "forge_score": score,
            "components": components,
            "blocking_score": blocking_score,
            "advisory_score": advisory_score,
        }
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        payload["history_path"] = str(history_path)
    return _quality.emit_payload(payload, args)


def lint_findings_for_score(handler: Any, planning_dir: Path, depth: str) -> tuple[list[_models.Finding], dict[str, Any]]:
    args = argparse.Namespace(planning_dir=str(planning_dir), depth=depth, profile="solo", strict=False, export=None, export_format="jsonl")
    captured: dict[str, Any] = {}
    token = _session._QUALITY_CAPTURE.set(captured)
    try:
        handler(args)
    finally:
        _session._QUALITY_CAPTURE.reset(token)
    findings = findings_from_payload(captured)
    return findings, {key: value for key, value in captured.items() if key not in {"findings", "success", "score", "finding_count"}}


def plan_findings_for_score(planning_dir: Path, depth: str) -> tuple[list[_models.Finding], dict[str, Any]]:
    return lint_findings_for_score(_validation.lint_plan, planning_dir, depth)


def section_findings_for_score(planning_dir: Path, depth: str) -> tuple[list[_models.Finding], dict[str, Any]]:
    return lint_findings_for_score(_validation.lint_sections, planning_dir, depth)


def evidence_findings_for_score(planning_dir: Path, min_files: int) -> list[_models.Finding]:
    texts = _artifacts.existing_artifact_texts(planning_dir)
    combined = "\n\n".join(texts.values())
    path = _artifacts.planning_artifacts(planning_dir)["plan"] or planning_dir / "codex-plan.md"
    findings: list[_models.Finding] = []
    _quality.add_term_findings(findings, combined, _policy.EVIDENCE_TERMS, path, "medium")
    if len(_ownership.extract_file_paths(combined)) < min_files:
        findings.append(_quality.finding("medium", "thin-file-evidence", f"Fewer than {min_files} concrete file paths found.", path))
    return findings


def readiness_findings_for_score(planning_dir: Path, max_files: int) -> list[_models.Finding]:
    findings, _ = implementation_readiness_analysis(planning_dir, max_files)
    return findings


def implementation_readiness_analysis(
    planning_dir: Path,
    max_files: int,
) -> tuple[list[_models.Finding], dict[str, Any]]:
    progress = _sections.check_section_progress(planning_dir)
    if progress["state"] in {"invalid_index", "no_index"}:
        return [
            _quality.finding(
                "critical",
                "invalid-sections",
                "Readiness requires a valid sections/index.md.",
                planning_dir / "sections" / "index.md",
            )
        ], {"section_progress": progress}
    deps = _sections.dependency_graph(planning_dir, progress)
    findings = _artifacts.compact_plan_findings(planning_dir)
    section_payloads: list[dict[str, Any]] = []
    for section in progress["sections"]:
        path = planning_dir / "sections" / f"{section}.md"
        if not path.exists():
            findings.append(_quality.finding("critical", "missing-section-file", f"{section}.md is missing.", path))
            continue
        text = _storage.read_text(path)
        metrics = _sections.section_metrics(section, path, deps)
        section_payloads.append(metrics)
        verified = _markdown.has_verification(text)
        terms = {key: value for key, value in _policy.READINESS_TERMS.items() if not verified or key not in {"tdd", "commands"}}
        _quality.add_term_findings(findings, text, terms, path, "medium")
        if metrics["file_count"] == 0:
            findings.append(_quality.finding("medium", "no-file-ownership", f"{section} names no implementation files.", path))
        if metrics["file_count"] > max_files:
            findings.append(
                _quality.finding(
                    "high",
                    "too-many-owned-files",
                    f"{section} owns {metrics['file_count']} files; max readiness threshold is {max_files}.",
                    path,
                    "Split the section or narrow file ownership before implementation.",
                )
            )
        if not verified:
            findings.append(_quality.finding("medium", "missing-verification", f"{section} needs a regression case and command, or justified inspection with an expected result.", path))
    return findings, {
        "planning_dir": str(planning_dir),
        "section_progress": progress,
        "sections": section_payloads,
    }


def forge_score_analysis(
    planning_dir: Path, depth: str, profile: str, *, min_files: int = 3, max_files: int = 8,
) -> tuple[list[_models.Finding], dict[str, int], int]:
    context = _session._CLI_CONTEXT.get()
    inputs = context.get("score_inputs") if context is not None else None
    evidence_findings = evidence_findings_for_score(planning_dir, min_files) if inputs is not None else None
    reused = inputs.reusable(planning_dir, depth, max_files) if inputs is not None else {}
    if reused:
        plan_findings = reused["plan_depth"]
        section_findings = reused["section_readiness"]
        trace_findings = reused["traceability"]
        readiness_findings = reused["implementation_readiness"]
    else:
        plan_findings, _ = plan_findings_for_score(planning_dir, depth)
        section_findings, _ = section_findings_for_score(planning_dir, depth)
        trace_findings, _ = _traceability.traceability_analysis(planning_dir)
        if evidence_findings is None:
            evidence_findings = evidence_findings_for_score(planning_dir, min_files)
        readiness_findings = readiness_findings_for_score(planning_dir, max_files)
    components = {
        "plan_depth": _quality.quality_score(plan_findings, profile),
        "section_readiness": _quality.quality_score(section_findings, profile),
        "traceability": _quality.quality_score(trace_findings, profile),
        "evidence_quality": _quality.quality_score(evidence_findings, profile),
        "implementation_readiness": _quality.quality_score(readiness_findings, profile),
    }
    weights = _policy.FORGE_COMPONENT_WEIGHTS.get(profile, _policy.FORGE_COMPONENT_WEIGHTS["solo"])
    weight_total = sum(weights.get(key, 1.0) for key in components)
    score = round(sum(value * weights.get(key, 1.0) for key, value in components.items()) / weight_total)
    return plan_findings + section_findings + trace_findings + evidence_findings + readiness_findings, components, score
