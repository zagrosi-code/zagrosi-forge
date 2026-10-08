"""Small content observations for mutable completion and interrupted work."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from . import artifacts, context, ownership, sections, session, storage

_IGNORED_SOURCE_DIRECTORIES = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def prepared_contract(planning_dir: Path, target_dir: Path | None = None) -> dict | None:
    """Revalidate outside analysis caches; stale contracts invalidate saved evidence."""
    from . import team_plans, team_state

    try:
        return team_plans.validate(planning_dir, target_dir)
    except team_state.TeamError as exc:
        raise ValueError(str(exc)) from exc


def target_directory(planning_dir: Path, target_dir: Path | None = None) -> Path:
    from .state import load_implementation_config

    if target_dir is not None:
        return storage.resolve_path(target_dir)
    config = load_implementation_config(planning_dir)
    if config.get("target_dir"):
        return storage.resolve_path(config["target_dir"])
    return Path.cwd().resolve()


def _code_observation(target_dir: Path, path: Path) -> str | None:
    """Hash an owned tree, retaining directory links without traversing them."""
    name = path.relative_to(target_dir).as_posix()
    if path != target_dir and not path.parent.resolve().is_relative_to(target_dir):
        raise ValueError(f"Observed code path must stay within the target directory: {name}")
    if path.is_symlink():
        try:
            linked_mode = path.stat().st_mode
        except FileNotFoundError:
            linked_mode = 0
        if not linked_mode or stat.S_ISDIR(linked_mode):
            return "link:" + digest({"link": os.readlink(path), "target_type": "directory" if linked_mode else "missing"})
    if not path.resolve().is_relative_to(target_dir):
        raise ValueError(f"Observed code path must stay within the target directory: {name}")
    try:
        if path.is_dir():
            return "directory:" + digest({
                child.name: _code_observation(target_dir, child)
                for child in sorted(path.iterdir())
                if not (child.name in _IGNORED_SOURCE_DIRECTORIES and child.is_dir())
                and not child.name.endswith((".pyc", ".pyo"))
            })
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError(f"Observed code path is not a regular file: {name}")
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except FileNotFoundError:
        return None


def code_observations(target_dir: Path, paths) -> dict[str, str | None]:
    result = {}
    for name in sorted(set(paths)):
        relative = Path(name)
        normalized = relative.as_posix()
        if not name or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Observed code path must stay within the target directory: {name}")
        result[normalized] = _code_observation(target_dir, target_dir / normalized)
    return result


def regular_tree_observations(target_dir: Path, paths, *, allow_missing: bool = False) -> tuple[dict, set]:
    """Observe explicit compatibility inputs, including ignored files and file modes.

    Links and special files are unsupported rather than partially attested. Only
    known cache/dependency directories are omitted while traversing a tree; all
    regular files and explicit roots are observed. Inodes detect source/check
    aliases in memory; portable receipts contain hashes.
    """
    target_dir = target_dir.resolve()
    inodes = set()

    def observe(path: Path):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"Compatibility inputs must not contain symlinks: {path}")
        if stat.S_ISDIR(info.st_mode):
            children = {child.name: observe(child) for child in sorted(path.iterdir())
                        if not (child.name in _IGNORED_SOURCE_DIRECTORIES
                                and stat.S_ISDIR(child.lstat().st_mode))}
            return {"mode": stat.S_IMODE(info.st_mode), "children": children}
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Compatibility inputs must be regular files: {path}")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Compatibility inputs must be regular files: {path}")
            inodes.add((info.st_dev, info.st_ino))
            return {"mode": stat.S_IMODE(info.st_mode), "content": hashlib.file_digest(handle, "sha256").hexdigest()}

    observations = {}
    for name in paths:
        relative = Path(name)
        if not name or relative.anchor or ".." in relative.parts:
            raise ValueError(f"Compatibility inputs must stay within the target directory: {name}")
        path = target_dir / relative
        for parent in (path, *path.parents):
            if parent == target_dir:
                break
            if parent.is_symlink():
                raise ValueError(f"Compatibility inputs must not contain symlinks: {parent}")
        try:
            observations[relative.as_posix()] = digest(observe(path))
        except FileNotFoundError as exc:
            if not allow_missing:
                raise ValueError(f"Compatibility input is missing: {name}") from exc
            observations[relative.as_posix()] = None
    return observations, inodes


def contract_inputs(planning_dir: Path, section: str) -> tuple[dict, set[str]]:
    return _bound_contract_inputs(planning_dir, section, prepared_contract(planning_dir))


def _bound_contract_inputs(planning_dir: Path, section: str, prepared: dict | None) -> tuple[dict, set[str]]:
    """Share one freshly validated descriptor within a single input observation."""
    contract, owned = session.cached_analysis("completion_contract", (planning_dir, section),
                                             lambda observe: _contract_inputs(planning_dir, section, observe))
    return ({**contract, "shared_plan": digest(prepared)} if prepared is not None else contract), owned


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
    target = target_directory(planning_dir, target_dir)
    contract, owned = _bound_contract_inputs(planning_dir, section, prepared_contract(planning_dir, target))
    return {"version": 1, "contract": contract, "target_dir": str(target),
            "code": code_observations(target, owned | set(files))}


def filesystem_paths(target_dir: Path) -> list[Path]:
    paths = []
    for directory, directories, files in os.walk(target_dir):
        directories[:] = [name for name in directories if name not in _IGNORED_SOURCE_DIRECTORIES]
        paths.extend(Path(directory) / name for name in directories if (Path(directory) / name).is_symlink())
        paths.extend(Path(directory) / name for name in files if not name.endswith((".pyc", ".pyo")))
    return paths


def source_paths(target_dir: Path) -> dict[Path, list[str]]:
    """Map source paths to staged gitlink identities, never follow directory links."""
    try:
        result = subprocess.run(["git", "ls-files", "-z", "-t", "--stage", "--cached", "--others", "--exclude-standard", "--", "."],
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
        paths = {}
        for entry in filter(None, result.stdout.split(b"\0")):
            tag, separator, name = entry.partition(b" ")
            metadata = None
            if not separator or tag not in {b"?", b"H", b"S", b"M"}:
                raise ValueError("Invalid tagged Git source inventory.")
            if tag != b"?":
                header, separator, name = name.partition(b"\t")
                fields = header.split()
                if not separator or len(fields) != 3:
                    raise ValueError("Invalid staged Git source inventory.")
                if fields[0] == b"160000":
                    metadata = header.decode("ascii")
            relative = Path(os.fsdecode(name))
            if not relative.parts or relative.anchor or ".." in relative.parts or any(
                (target_dir / parent).is_symlink() for parent in relative.parents if parent.parts
            ):
                raise ValueError(f"Observed repository boundary must stay within the target directory: {relative}")
            path = target_dir / relative
            paths.setdefault(path, [])
            if metadata:
                paths[path].append(metadata)
        for path in list(paths):
            if not path.is_symlink() and path.is_dir():
                resolved = path.resolve()
                if resolved == target_dir or not resolved.is_relative_to(target_dir):
                    raise ValueError(f"Observed repository boundary must stay within the target directory: {path}")
                nested = source_paths(path) if (path / ".git").exists() else {child: [] for child in filesystem_paths(path)}
                for child, metadata in nested.items():
                    paths.setdefault(child, []).extend(metadata)
    else:
        paths = {path: [] for path in filesystem_paths(target_dir)}
    return paths


def verification_snapshot(planning_dir: Path, target_dir: Path, section: str | None = None) -> dict:
    """Bind verification to source files and link identities, without traversing linked directories."""
    prepared = prepared_contract(planning_dir, target_dir)
    names = sections.check_section_progress(planning_dir).get("sections", [])
    if not names or (section and section not in names):
        raise ValueError("Verification requires a complete section index.")
    contracts = {name: _bound_contract_inputs(planning_dir, name, prepared)[0] for name in names}
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
    paths = source_paths(target_dir)
    observed = [path.relative_to(target_dir).as_posix() for path in paths
                if not any(path == excluded_path or path.is_relative_to(excluded_path) for excluded_path in excluded)]
    identities, files = {}, []
    for name in observed:
        path = target_dir / name
        if path.is_symlink():
            if not path.parent.resolve().is_relative_to(target_dir):
                raise ValueError(f"Observed code path must stay within the target directory: {name}")
            try:
                linked_mode = path.stat().st_mode
            except FileNotFoundError:
                linked_mode = 0
            if not linked_mode or stat.S_ISDIR(linked_mode):
                # Bind the entry, never enumerate linked directories or read outside source roots.
                identities[name] = {"content": None, "mode": stat.S_IMODE(path.lstat().st_mode),
                                    "link": os.readlink(path), "target_type": "directory" if linked_mode else "missing"}
                continue
        elif path.is_dir():
            identities[name] = {"content": None, "mode": stat.S_IMODE(path.stat().st_mode), "target_type": "directory"}
            continue
        files.append(name)
    for name, content in code_observations(target_dir, files).items():
        path = target_dir / name
        identities[name] = {"content": content, "mode": stat.S_IMODE(path.lstat().st_mode) if content is not None else None,
                            "link": os.readlink(path) if path.is_symlink() else None}
    for name, identity in identities.items():
        if metadata := paths[target_dir / name]:
            identity["gitlink"] = sorted(metadata)
    return {"version": 1, "planning_dir": str(planning_dir), "target_dir": str(target_dir),
            "section": section, "contract_digest": digest(contracts), "source_digest": digest(identities),
            "source_file_count": len(identities)}
