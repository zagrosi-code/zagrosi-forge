"""Small content observations for mutable completion and interrupted work."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from . import artifacts, context, ownership, sections, storage


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
        normalized = ownership.normalize_owned_path(name)
        if not normalized or not (target_dir / normalized).resolve().is_relative_to(target_dir):
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
