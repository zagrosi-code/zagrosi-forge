"""Forge projects."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json
import re

from . import artifacts as _artifacts
from . import markdown as _markdown
from . import models as _models
from . import ownership as _ownership
from . import policy as _policy
from . import quality as _quality
from . import storage as _storage

def ensure_markdown_file(path: Path, label: str) -> tuple[bool, str]:
    if not path.exists():
        return False, f"{label} not found: {path}"
    if not path.is_file():
        return False, f"Expected {label} file, got directory: {path}"
    if path.suffix.lower() != ".md":
        return False, f"Expected {label} to end with .md: {path}"
    if not _storage.read_text(path).strip():
        return False, f"{label} is empty: {path}"
    return True, ""


def unique_markdown_path(directory: Path, stem: str) -> Path:
    candidate = directory / f"{stem}.md"
    if not candidate.exists() or not _storage.read_text(candidate).strip():
        return candidate
    counter = 2
    while True:
        candidate = directory / f"{stem}-{counter}.md"
        if not candidate.exists() or not _storage.read_text(candidate).strip():
            return candidate
        counter += 1


def write_chat_requirements(planning_dir: Path, brief: str) -> tuple[Path, bool]:
    planning_dir.mkdir(parents=True, exist_ok=True)
    path = unique_markdown_path(planning_dir, "requirements")
    content = (
        "# Project Brief\n\n"
        "Source: chat brief captured by `$zagrosi-forge:zagrosi-project`.\n\n"
        f"{brief.strip()}\n\n"
        "## Interview Notes\n\n"
        "Detailed interview answers belong in `zagrosi_project_interview.md`.\n"
    )
    path.write_text(content, encoding="utf-8")
    return path, True


def existing_project_initial_file(planning_dir: Path) -> Path | None:
    for state_path in (planning_dir / ".zagrosi-project" / "session.json", planning_dir / ".deep-project" / "session.json"):
        if not state_path.exists():
            continue
        try:
            state = _storage.load_json(state_path)
        except (OSError, json.JSONDecodeError):
            continue
        initial_file = state.get("initial_file")
        if isinstance(initial_file, str):
            candidate = _storage.resolve_path(initial_file)
            if candidate.exists() and candidate.is_file():
                return candidate
    return None


def resolve_project_input(args: argparse.Namespace, *, materialize_chat: bool = True) -> tuple[_models.ProjectInput | None, str | None]:
    file_arg = getattr(args, "file", None)
    brief = (getattr(args, "brief", None) or "").strip()
    planning_dir_arg = getattr(args, "planning_dir", None)

    if file_arg and brief:
        return None, "Use either --file or --brief for project setup, not both."

    if file_arg:
        input_file = _storage.resolve_path(file_arg)
        ok, error = ensure_markdown_file(input_file, "requirements file")
        if not ok:
            return None, error
        return _models.ProjectInput(
            planning_dir=input_file.parent,
            input_file=input_file,
            input_mode="file",
            generated_file=False,
            brief_word_count=_markdown.word_count(_storage.read_text(input_file)),
        ), None

    if not brief:
        return None, "Project setup needs either --file PATH or --brief TEXT from the chat."

    planning_dir = _storage.resolve_path(planning_dir_arg) if planning_dir_arg else Path.cwd().resolve()
    input_file: Path | None = None
    generated = False
    warnings: tuple[str, ...] = ()
    if materialize_chat:
        existing_file = existing_project_initial_file(planning_dir)
        if existing_file:
            input_file = existing_file
            warnings = ("Existing project session found; reusing its initial requirements file.",)
        else:
            input_file, generated = write_chat_requirements(planning_dir, brief)
    return _models.ProjectInput(
        planning_dir=planning_dir,
        input_file=input_file,
        input_mode="chat",
        generated_file=generated,
        brief_word_count=_markdown.word_count(brief),
        warnings=warnings,
    ), None


def interview_findings(planning_dir: Path, phase: str) -> tuple[list[_models.Finding], dict[str, Any]]:
    names = _policy.INTERVIEW_FILES[phase]
    path = _artifacts.interview_artifact(planning_dir, phase)
    expected_path = planning_dir / names[0]
    findings: list[_models.Finding] = []
    if not path:
        findings.append(
            _quality.finding(
                "medium",
                "missing-interview",
                f"{phase} interview artifact is missing.",
                expected_path,
                f"Interview the user and write {names[0]}, or set interview_mode: skipped_with_reason with skip_reason.",
            )
        )
        return findings, {
            "planning_dir": str(planning_dir),
            "phase": phase,
            "interview": None,
            "user_interviewed": False,
            "interview_mode": None,
        }

    text = _storage.read_text(path)
    user_interviewed = re.search(r"(?im)^\s*user_interviewed\s*:\s*true\s*$", text) is not None
    skipped = re.search(r"(?im)^\s*interview_mode\s*:\s*skipped_with_reason\s*$", text) is not None
    reason_match = re.search(r"(?im)^\s*(?:skip_reason|reason)\s*:\s*(.+?)\s*$", text)
    skip_reason = reason_match.group(1).strip() if reason_match else ""

    if not text.strip():
        findings.append(_quality.finding("high", "empty-interview", "Interview artifact is empty.", path))
    if _policy.INTERVIEW_PLACEHOLDER_RE.search(text):
        findings.append(
            _quality.finding(
                "high",
                "placeholder-interview",
                "Interview artifact appears to be placeholder, fake, or synthetic.",
                path,
                "Replace it with actual user questions and answers, or explicitly skip with a concrete reason.",
            )
        )
    if user_interviewed and skipped:
        findings.append(
            _quality.finding(
                "medium",
                "conflicting-interview-mode",
                "Interview artifact says the user was interviewed and also says the interview was skipped.",
                path,
            )
        )
    if not user_interviewed and not skipped:
        findings.append(
            _quality.finding(
                "medium",
                "missing-interview-confirmation",
                "Interview artifact must include user_interviewed: true or interview_mode: skipped_with_reason.",
                path,
            )
        )
    if user_interviewed and not _artifacts.has_interview_exchange(text):
        findings.append(
            _quality.finding(
                "medium",
                "missing-interview-exchange",
                "Interview artifact marks user_interviewed: true but has no clear question/answer exchange.",
                path,
                "Record at least one Q:/A: pair or a Question/Answer table.",
            )
        )
    if skipped and (not skip_reason or _policy.INTERVIEW_PLACEHOLDER_RE.search(skip_reason)):
        findings.append(
            _quality.finding(
                "high",
                "missing-skip-reason",
                "Skipped interviews must include a concrete skip_reason.",
                path,
            )
        )

    return findings, {
        "planning_dir": str(planning_dir),
        "phase": phase,
        "interview": str(path),
        "user_interviewed": user_interviewed,
        "interview_mode": "skipped_with_reason" if skipped else ("completed" if user_interviewed else None),
        "skip_reason": skip_reason or None,
        "word_count": _markdown.word_count(text),
    }


def interview_warning_messages(planning_dir: Path, phase: str) -> list[str]:
    findings, _ = interview_findings(planning_dir, phase)
    return [f"Interview gate: {item.code} - {item.message}" for item in findings if item.severity in {"critical", "high", "medium"}]


def lint_interview(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    findings, extras = interview_findings(planning_dir, args.phase)
    return _quality.emit_quality("interview", findings, args, extras)


def project_manifest_rows(text: str) -> list[dict[str, str]] | None:
    aliases = {
        "split": {"split", "unit"},
        "requirements": {"req", "reqs", "requirement", "requirements"},
        "dependencies": {"depends on", "dependencies", "dependency"},
        "boundary": {"owns boundary", "owns", "boundary", "owned boundary"},
        "command": {"next command", "command"},
    }
    for table in _markdown.markdown_tables(text):
        if not table:
            continue
        normalized = [re.sub(r"[^a-z]+", " ", cell.lower()).strip() for cell in table[0]]
        indexes: dict[str, int] = {}
        for name, choices in aliases.items():
            match = next((index for index, value in enumerate(normalized) if value in choices), None)
            if match is None:
                break
            indexes[name] = match
        if len(indexes) != len(aliases):
            continue
        return [
            {name: row[index].strip() if index < len(row) else "" for name, index in indexes.items()}
            for row in table[1:]
        ]
    return None


def graph_cycle(graph: dict[str, list[str]]) -> list[str]:
    visited: set[str] = set()
    active: list[str] = []

    def visit(node: str) -> list[str]:
        if node in active:
            start = active.index(node)
            return [*active[start:], node]
        if node in visited:
            return []
        active.append(node)
        for dependency in graph.get(node, []):
            if cycle := visit(dependency):
                return cycle
        active.pop()
        visited.add(node)
        return []

    for node in graph:
        if cycle := visit(node):
            return cycle
    return []


def project_requirement_source(planning_dir: Path, meta: dict[str, Any] | None) -> Path | None:
    candidates: list[Path] = []
    for state_path in (planning_dir / ".zagrosi-project" / "session.json", planning_dir / ".deep-project" / "session.json"):
        if not state_path.exists():
            continue
        try:
            initial = _storage.load_json(state_path).get("initial_file")
        except (OSError, ValueError, json.JSONDecodeError):
            initial = None
        if isinstance(initial, str) and initial.strip():
            candidate = Path(initial).expanduser()
            candidates.append(candidate if candidate.is_absolute() else planning_dir / candidate)
    if isinstance(meta, dict) and isinstance(meta.get("source"), str):
        candidates.append(planning_dir / meta["source"])
    candidates.append(planning_dir / "requirements.md")
    return next((path.resolve() for path in candidates if path.exists() and path.is_file()), None)


def compact_project_session(planning_dir: Path) -> bool:
    for path in (planning_dir / ".zagrosi-project" / "session.json", planning_dir / ".deep-project" / "session.json"):
        if not path.exists():
            continue
        try:
            state = _storage.load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        return isinstance(state, dict) and state.get("contract_version") == _policy.PROJECT_CONTRACT_VERSION
    return False


def project_artifact_depth(planning_dir: Path, meta: dict[str, Any] | None) -> str:
    session_depth = _artifacts.project_depth(planning_dir, "")
    if session_depth in _policy.DEPTH_MODES:
        return session_depth
    meta_depth = meta.get("depth_mode") if isinstance(meta, dict) else None
    return meta_depth if isinstance(meta_depth, str) and meta_depth in _policy.DEPTH_MODES else _policy.DEFAULT_DEPTH


def split_spec_declarations(text: str) -> tuple[list[str], list[str]]:
    dependencies = re.findall(r"(?im)^\s*dependencies?\s*:\s*(.+?)\s*$", text)
    boundaries = re.findall(r"(?im)^\s*boundary\s*:\s*(.+?)\s*$", text)
    return ([item.strip() for item in dependencies], [item.strip() for item in boundaries])


def normalized_boundary(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("`", "").strip().lower()).removeprefix("owns ")


def lint_project_manifest(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    manifest_path = planning_dir / "project-manifest.md"
    findings: list[_models.Finding] = []
    splits: list[str] = []

    if not manifest_path.exists():
        findings.append(_quality.finding("critical", "missing-manifest", "project-manifest.md is missing.", manifest_path))
        return _quality.emit_quality("project-manifest", findings, args)

    interview_extras: dict[str, Any] = {"mode": "optional"}
    if _artifacts.interview_artifact(planning_dir, "project"):
        interview_gate_findings, interview_extras = interview_findings(planning_dir, "project")
        findings.extend(interview_gate_findings)

    text = _storage.read_text(manifest_path)
    meta: dict[str, Any] | None = None
    if _policy.FORGE_META_START in text or _policy.LEGACY_META_START in text:
        meta, meta_errors = _markdown.parse_forge_meta(text)
        for error in meta_errors:
            findings.append(_quality.finding("low", "metadata", error, manifest_path))
    if meta and meta.get("artifact_type") != "project_manifest":
        findings.append(_quality.finding("medium", "metadata-type", "FORGE_META artifact_type should be project_manifest.", manifest_path))

    splits, manifest_errors = _markdown.parse_numbered_manifest(text, "SPLIT_MANIFEST", _policy.SPLIT_RE)
    for error in manifest_errors:
        findings.append(_quality.finding("critical", "manifest-format", error, manifest_path))

    rows = project_manifest_rows(text)
    modern_contract = rows is not None or compact_project_session(planning_dir)
    depth = project_artifact_depth(planning_dir, meta)
    budgets = _policy.PROJECT_WORD_BUDGETS[depth]
    if modern_contract:
        _quality.add_budget_finding(findings, _markdown.word_count(text), budgets["manifest"], "Project manifest", "project-manifest-too-large", manifest_path)
    owned_requirements: dict[str, set[str]] = {split: set() for split in splits}
    requirement_owners: dict[str, set[str]] = {}
    dependencies: dict[str, list[str]] = {split: [] for split in splits}
    boundaries: dict[str, str] = {}
    boundary_owners: dict[str, set[str]] = {}
    if rows is None:
        if compact_project_session(planning_dir):
            findings.append(
                _quality.finding(
                    "high",
                    "missing-ownership-table",
                    "Manifest lacks the compact Split/REQ/Depends on/Owns boundary/Next command table.",
                    manifest_path,
                )
            )
    else:
        seen_rows: set[str] = set()
        for row in rows:
            row_splits = _policy.SPLIT_TOKEN_RE.findall(row["split"])
            if not row_splits:
                findings.append(_quality.finding("high", "invalid-split-row", "Manifest table row has no valid split.", manifest_path))
                continue
            split = row_splits[0]
            if split not in owned_requirements:
                findings.append(_quality.finding("high", "unknown-split-row", f"Manifest table names unknown split: {split}.", manifest_path))
                continue
            if split in seen_rows:
                findings.append(_quality.finding("high", "duplicate-split-row", f"Manifest table repeats split: {split}.", manifest_path))
            seen_rows.add(split)

            reqs = set(_markdown.requirement_ids(row["requirements"]))
            if not reqs:
                findings.append(_quality.finding("medium", "split-without-requirements", f"{split} owns no REQ-* IDs.", manifest_path))
            owned_requirements[split].update(reqs)
            for req_id in reqs:
                requirement_owners.setdefault(req_id, set()).add(split)

            deps = _policy.SPLIT_TOKEN_RE.findall(row["dependencies"])
            dependencies[split] = deps
            boundaries[split] = row["boundary"]
            unknown = sorted(set(deps) - set(splits))
            if unknown:
                findings.append(
                    _quality.finding(
                        "high",
                        "unknown-split-dependency",
                        f"{split} depends on unknown split(s): {', '.join(unknown)}.",
                        manifest_path,
                    )
                )

            for path in _ownership.extract_file_paths(row["boundary"]):
                boundary_owners.setdefault(path, set()).add(split)
            if "zagrosi-plan" not in row["command"].lower() or split not in row["command"] or "spec.md" not in row["command"]:
                findings.append(
                    _quality.finding(
                        "medium",
                        "invalid-next-command",
                        f"{split} lacks its exact Zagrosi Plan spec command.",
                        manifest_path,
                    )
                )

        for split in sorted(set(splits) - seen_rows):
            findings.append(_quality.finding("high", "missing-split-row", f"Manifest table omits split: {split}.", manifest_path))
        for req_id, owners in sorted(requirement_owners.items()):
            if len(owners) > 1:
                findings.append(
                    _quality.finding(
                        "high",
                        "duplicate-requirement-owner",
                        f"{req_id} has multiple split owners: {', '.join(sorted(owners))}.",
                        manifest_path,
                    )
                )
        for path, owners in sorted(boundary_owners.items()):
            if len(owners) > 1:
                findings.append(
                    _quality.finding(
                        "high",
                        "cross-split-path-collision",
                        f"{path} is owned by multiple splits: {', '.join(sorted(owners))}.",
                        manifest_path,
                    )
                )
        if cycle := graph_cycle(dependencies):
            findings.append(
                _quality.finding(
                    "high",
                    "split-dependency-cycle",
                    f"Split dependency cycle: {' -> '.join(cycle)}.",
                    manifest_path,
                )
            )

    source_path = project_requirement_source(planning_dir, meta)
    source_ids = _markdown.requirement_ids(_storage.read_text(source_path)) if source_path else []
    missing_source_ids = sorted(set(source_ids) - set(requirement_owners)) if rows is not None else []
    if missing_source_ids:
        findings.append(
            _quality.finding(
                "high",
                "unowned-source-requirements",
                f"Source requirements lack split owners: {', '.join(missing_source_ids)}.",
                source_path or manifest_path,
            )
        )

    _quality.require_terms(
        findings,
        text,
        {
            "dependencies": ["dependency", "dependencies", "depends on", "blocks"],
            "execution-order": ["execution order", "run order", "sequence"],
            "parallelization": ["parallel", "concurrent"],
            "zagrosi-plan-commands": ["$zagrosi-plan", "zagrosi-plan", "$deep-plan", "deep-plan"],
            "cross-cutting-concerns": ["cross-cutting", "shared", "common"],
        },
        manifest_path,
    )

    for split in splits:
        split_dir = planning_dir / split
        spec_path = split_dir / "spec.md"
        if not split_dir.exists():
            findings.append(_quality.finding("medium", "missing-split-dir", f"Split directory is missing: {split}", split_dir))
            continue
        if not spec_path.exists() or not _storage.read_text(spec_path).strip():
            findings.append(_quality.finding("medium", "missing-split-spec", f"Split spec is missing or empty: {split}/spec.md", spec_path))
            continue
        spec_text = _storage.read_text(spec_path)
        if modern_contract:
            _quality.add_budget_finding(findings, _markdown.word_count(spec_text), budgets["spec"], f"{split}/spec.md", "split-spec-too-large", spec_path)
        spec_ids = set(_markdown.requirement_ids(spec_text))
        missing_owned = sorted(owned_requirements.get(split, set()) - spec_ids)
        if missing_owned:
            findings.append(
                _quality.finding(
                    "high",
                    "split-spec-missing-requirements",
                    f"{split}/spec.md omits owned IDs: {', '.join(missing_owned)}.",
                    spec_path,
                )
            )
        foreign = sorted(
            req_id
            for req_id in spec_ids - owned_requirements.get(split, set())
            if req_id in requirement_owners
        )
        if foreign:
            findings.append(
                _quality.finding(
                    "high",
                    "split-spec-foreign-requirements",
                    f"{split}/spec.md claims IDs owned elsewhere: {', '.join(foreign)}.",
                    spec_path,
                )
            )
        if rows is not None:
            dependency_declarations, boundary_declarations = split_spec_declarations(spec_text)
            if not dependency_declarations:
                findings.append(_quality.finding("high", "missing-spec-dependencies", f"{split}/spec.md lacks a Dependencies: declaration.", spec_path))
            elif len(dependency_declarations) != 1:
                findings.append(_quality.finding("high", "duplicate-spec-dependencies", f"{split}/spec.md must contain exactly one Dependencies: declaration.", spec_path))
            else:
                dependency_declaration = dependency_declarations[0]
                actual_dependencies = set(_policy.SPLIT_TOKEN_RE.findall(dependency_declaration))
                expected_dependencies = set(dependencies.get(split, []))
                if actual_dependencies != expected_dependencies:
                    findings.append(
                        _quality.finding(
                            "high",
                            "split-spec-dependency-mismatch",
                            f"{split}/spec.md dependencies differ from manifest: expected {sorted(expected_dependencies)}, got {sorted(actual_dependencies)}.",
                            spec_path,
                        )
                    )
            if not boundary_declarations:
                findings.append(_quality.finding("high", "missing-spec-boundary", f"{split}/spec.md lacks a Boundary: declaration.", spec_path))
            elif len(boundary_declarations) != 1:
                findings.append(_quality.finding("high", "duplicate-spec-boundary", f"{split}/spec.md must contain exactly one Boundary: declaration.", spec_path))
            else:
                boundary_declaration = boundary_declarations[0]
                expected_boundary = boundaries.get(split, "")
                boundary_matches = normalized_boundary(boundary_declaration) == normalized_boundary(expected_boundary)
                if not boundary_matches:
                    findings.append(
                        _quality.finding(
                            "high",
                            "split-spec-boundary-mismatch",
                            f"{split}/spec.md boundary differs from the manifest.",
                            spec_path,
                        )
                    )
        _quality.require_terms(
            findings,
            spec_text,
            {
                "acceptance-criteria": ["acceptance criteria", "done when", "success criteria"],
                "scope": ["in scope", "out of scope", "non-goals"],
                "testing": ["test", "tests", "verification"],
                "dependencies": ["dependency", "dependencies", "depends on", "input", "output"],
                "boundary": ["boundary", "owns", "ownership"],
                "risks-or-stops": ["risk", "open question", "unknown", "assumption", "stop"],
            },
            spec_path,
            "low",
        )

    payload = _quality.quality_from_args(
        "project-manifest",
        findings,
        args,
        {
            "planning_dir": str(planning_dir),
            "manifest": str(manifest_path),
            "source": str(source_path) if source_path else None,
            "splits": splits,
            "requirement_owners": {req_id: sorted(owners) for req_id, owners in sorted(requirement_owners.items())},
            "dependencies": dependencies,
            "depth_mode": depth,
            "word_budgets": budgets,
            "interview": interview_extras,
        },
    )
    return _quality.emit_payload(payload, args)
