"""Small content observations for mutable completion and interrupted work."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from . import artifacts, context, ownership, sections, session, storage


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def target_directory(planning_dir: Path, target_dir: Path | None = None) -> Path:
    if target_dir is not None:
        return Path(target_dir).resolve()
    for name in ("zagrosi_implement_config.json", "deep_implement_config.json"):
        path = planning_dir / "implementation" / name
        if path.is_file():
            config = storage.load_json(path)
            if config.get("target_dir"):
                return storage.resolve_path(config["target_dir"])
    return Path.cwd().resolve()


def code_observations(target_dir: Path, paths) -> dict[str, str | None]:
    result = {}
    for name in sorted(set(paths)):
        relative = Path(name)
        normalized = relative.as_posix()
        if not name or relative.is_absolute() or ".." in relative.parts or not (target_dir / relative).resolve().is_relative_to(target_dir):
            raise ValueError(f"Observed code path must stay within the target directory: {name}")
        path = target_dir / normalized
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ValueError(f"Observed code path is not a regular file: {name}")
                result[normalized] = hashlib.file_digest(handle, "sha256").hexdigest()
        except FileNotFoundError:
            result[normalized] = None
    return result


def contract_inputs(planning_dir: Path, section: str) -> tuple[dict, set[str]]:
    return session.cached_analysis("completion_contract", (planning_dir, section),
                                   lambda observe: _contract_inputs(planning_dir, section, observe))


def _contract_inputs(planning_dir: Path, section: str, observe) -> tuple[dict, set[str]]:
    # Directory identities cover newly selected artifacts; reads observe exact files.
    for path in (planning_dir, planning_dir / "sections", planning_dir / "implementation"):
        observe(path)
    source = artifacts.planning_config(planning_dir).get("initial_file")
    if isinstance(source, str) and source.strip():
        path = Path(source).expanduser()
        observe(path if path.is_absolute() else planning_dir / path)
    progress = sections.check_section_progress(planning_dir)
    if progress.get("state") != "complete" or section not in progress["sections"]:
        raise ValueError(f"Cannot observe an absent section or incomplete index: {section}")
    dependencies = sections.dependency_graph(planning_dir, progress)
    predecessors = sections.transitive_section_predecessors(section, dependencies)
    selected = sorted({section, *predecessors})
    contract = {}
    requirements = set()
    owned = set()
    seeds = []
    for name in selected:
        text = storage.read_text(planning_dir / "sections" / f"{name}.md")
        seeds.append((planning_dir / "sections" / f"{name}.md", text))
        contract[f"section:{name}"] = digest(text)
        requirements.update(context.context_requirement_ids(text))
        if name == section:
            owned.update(ownership.extract_section_owned_paths(text))
    contract["configuration"] = digest({
        "depth": artifacts.planning_depth(planning_dir),
        "project": progress["project_config"],
        "dependencies": {name: dependencies.get(name, []) for name in selected},
    })
    sources = artifacts.planning_artifacts(planning_dir)
    sources["spec"] = artifacts.requirement_source_spec(planning_dir)
    section_paths = {planning_dir / "sections" / f"{name}.md" for name in selected}
    for name in ("spec", "plan", "tdd", "decisions", "risks"):
        path = sources.get(name)
        if not path or path in section_paths:
            continue
        text = artifacts.planning_artifact_text(planning_dir, name, path)
        blocks = [body for _, body, ids in context.context_blocks(text)
                  if not ids or not requirements or ids.intersection(requirements)]
        contract[name] = digest({"path": str(path.relative_to(planning_dir)) if path.is_relative_to(planning_dir) else str(path),
                                 "blocks": blocks})
        seeds.append((path, "\n\n".join(blocks)))
    from .context_links import linked_contracts

    known = section_paths | {path for path in sources.values() if path}
    for path, excerpts in linked_contracts(planning_dir, seeds=seeds, known_paths=known).items():
        label = str(path.relative_to(planning_dir)) if path.is_relative_to(planning_dir) else str(path)
        contract[f"link:{label}"] = digest([text for _, _, text in excerpts])
    return contract, owned


def contract_snapshot(planning_dir: Path, section: str, *, target_dir=None, files=()) -> dict:
    """Separate contract freshness from code observations changed by later sections."""
    contract, owned = contract_inputs(planning_dir, section)
    target = target_directory(planning_dir, target_dir)
    return {"version": 1, "contract": contract, "target_dir": str(target),
            "code": code_observations(target, owned | set(files))}


def verification_snapshot(planning_dir: Path, target_dir: Path, section: str | None = None) -> dict:
    """Bind verification to all source inputs, including newly added and removed files."""
    names = sections.check_section_progress(planning_dir).get("sections", [])
    if not names or (section and section not in names):
        raise ValueError("Verification requires a complete section index.")
    contracts = {name: contract_inputs(planning_dir, name)[0] for name in names}
    sources = artifacts.planning_artifacts(planning_dir)
    sources["spec"] = artifacts.requirement_source_spec(planning_dir)
    authoritative = {
        name: {"path": str(path.resolve()), "content": storage.read_text(path) if path.is_file() else None}
        for name, path in sources.items() if path and name != "traceability"
    }
    contracts["artifacts"] = authoritative
    contracts["planning_config"] = artifacts.planning_config(planning_dir)
    excluded = {planning_dir / name for name in (".zagrosi-project", ".deep-project", ".forge/scores", ".forge/report.html",
                                                "implementation/verification", "implementation/code_review")}
    excluded.update(planning_dir / "sections" / f"{name}.md" for name in ["index", *names])
    excluded.update(planning_dir / "implementation" / name for name in (
        "zagrosi_implement_config.json", "deep_implement_config.json", "zagrosi_implement_state.json",
        "deep_implement_state.json", "forge-progress.json", ".mutable-state.lock",
    ))
    excluded.update(path for path in artifacts.planning_artifacts(planning_dir).values() if path)
    excluded.update(planning_dir / name for name in (
        "zagrosi_plan_config.json", "deep_plan_config.json", "traceability.md", "forge-report.md",
    ))
    try:
        result = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "."],
                                cwd=target_dir, capture_output=True, timeout=10)
        if result.returncode == 0 and not (target_dir / ".git").exists():
            # An ignored nested workspace can return success with no source paths.
            # Check its parent so the target's own ignore rules remain authoritative.
            ignored_target = subprocess.run(["git", "check-ignore", "-q", "--no-index", "--", target_dir.name],
                                            cwd=target_dir.parent, capture_output=True, timeout=10)
            if ignored_target.returncode == 0:
                result = None
    except FileNotFoundError:
        result = None
    if result is not None and result.returncode == 0:
        paths = [target_dir / os.fsdecode(name) for name in result.stdout.split(b"\0") if name]
    else:
        ignored = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
        paths = []
        for directory, directories, files in os.walk(target_dir):
            directories[:] = [name for name in directories if name not in ignored]
            paths.extend(Path(directory) / name for name in directories if (Path(directory) / name).is_symlink())
            paths.extend(Path(directory) / name for name in files if not name.endswith((".pyc", ".pyo")))
    observed = [str(path.relative_to(target_dir)) for path in paths
                if not any(path == excluded_path or path.is_relative_to(excluded_path) for excluded_path in excluded)]
    code = code_observations(target_dir, observed)
    identities = {}
    for name, content in code.items():
        path = target_dir / name
        identities[name] = {"content": content, "mode": stat.S_IMODE(path.lstat().st_mode) if content is not None else None,
                            "link": os.readlink(path) if path.is_symlink() else None}
    return {"version": 1, "planning_dir": str(planning_dir), "target_dir": str(target_dir),
            "section": section, "contract_digest": digest(contracts), "source_digest": digest(identities),
            "source_file_count": len(identities)}
