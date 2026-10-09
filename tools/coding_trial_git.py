"""Isolated baseline initialization and private Git provenance for coding trials."""
from __future__ import annotations

import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from coding_trial_inventory import contains_path, copy_snapshot, fingerprint, relative_path
from coding_trial_process import execute
from coding_trial_qualification import read_bytes, require


def _literal_exclusion(name):
    if "\n" in name or "\r" in name:
        raise ValueError("Git artifact exclusions cannot contain line breaks")
    escaped = "".join("\\" + char if char in "\\*?[]!# " else char for char in name)
    return "/" + escaped + "\n"


def initialize_repository(workspace: Path, baseline: dict[str, str], *,
                          artifact_exclusions: tuple[str, ...] | None = None) -> None:
    """Commit trusted fixture bytes without inheriting another repository or hooks."""
    if artifact_exclusions is not None:
        for name in artifact_exclusions:
            relative_path(name)
            _literal_exclusion(name)
            if any(contains_path(name, original) for original in baseline):
                raise ValueError("Artifact exclusions hide baseline material")
    metadata = workspace / ".git"
    if metadata.exists() or metadata.is_symlink():
        raise ValueError("Trial fixture must not contain Git metadata")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_ATTR_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_DATE="2000-01-01T00:00:00+00:00",
               GIT_COMMITTER_DATE="2000-01-01T00:00:00+00:00")

    def git(*args):
        subprocess.run(["git", *args], cwd=workspace, env=env, check=True,
                       capture_output=True, text=True, timeout=30)

    try:
        git("init", "--quiet", "--template=", "--initial-branch=trial")
        hooks = metadata / "disabled-hooks"
        hooks.mkdir()
        for name, value in (("user.name", "Forge Trial"), ("user.email", "forge-trial@example.invalid"),
                            ("core.hooksPath", hooks.as_posix()), ("commit.gpgSign", "false"),
                            ("maintenance.auto", "false"), ("gc.auto", "0"),
                            ("core.autocrlf", "false"), ("core.excludesFile", os.devnull),
                            ("core.attributesFile", os.devnull)):
            git("config", "--local", name, value)
        (metadata / "info").mkdir(exist_ok=True)
        exclusions = ("/.planning/\n__pycache__/\n.pytest_cache/\n*.pyc\n*.pyo\n"
                      if artifact_exclusions is None else "".join(map(_literal_exclusion, artifact_exclusions)))
        (metadata / "info/exclude").write_text(exclusions)
        git("add", "--force", "--", *(name for name in sorted(baseline)
              if artifact_exclusions is not None or not name.startswith(".planning/")))
        git("commit", "--quiet", "--no-gpg-sign", "-m", "Trial baseline")
    except FileNotFoundError as exc:
        raise ValueError("Git is required to prepare an isolated trial workspace") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Git baseline preparation failed: {exc}") from exc


def _metadata(path):
    """Reject external/history indirections before Git can interpret metadata."""
    from coding_trial_reservation import _git_entries

    require(path.is_absolute() and path.resolve(strict=True) == path, "Git metadata path was redirected")
    info, parent = path.lstat(), path.parent.lstat()
    require(stat.S_ISDIR(info.st_mode), "Git metadata is not an ordinary directory")
    entries = _git_entries(path)
    require("info/grafts" not in entries, "Git graft history is unsupported")
    require(not any(contains_path("refs/replace", name) and item["type"] != "directory"
                    for name, item in entries.items()), "Git replacement refs are unsupported")
    if "packed-refs" in entries:
        lines = read_bytes(path, "packed-refs").splitlines()
        require(not any(len(parts := line.split()) == 2 and parts[1].startswith(b"refs/replace/")
                        for line in lines), "Packed Git replacement refs are unsupported")
    require(not any(name.startswith("objects/pack/") and name.endswith(".promisor") for name in entries),
            "Promisor object storage is unsupported")
    for name in ("config", "config.worktree"):
        if name not in entries:
            continue
        parser = configparser.RawConfigParser(strict=False, allow_no_value=True)
        parser.read_string(read_bytes(path, name).decode("utf-8"))
        for section in parser.sections():
            base = section.split()[0].split(".")[0].casefold()
            lazy = (base == "extensions" and parser.has_option(section, "partialclone")
                    or base == "remote" and any(parser.has_option(section, key)
                                               for key in ("promisor", "partialclonefilter")))
            require(not lazy, "Lazy-fetch Git configuration is unsupported")
    identity = (info.st_dev, info.st_ino, info.st_mode, parent.st_dev, parent.st_ino)
    return entries, identity


def _git(path, *args, acceptable=(0,)):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_ATTR_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
               GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
    process = execute(
        ["git", "--no-replace-objects", "--git-dir", str(path), "-c", f"core.hooksPath={os.devnull}",
         "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", *args],
        path.parent, timeout=30, output_limit=8388608, env=env, inherit_env=False)
    require(process["returncode"] in acceptable and not any(process.get(key) for key in
            ("timed_out", "cancelled", "termination_error", "stdout_truncated", "stderr_truncated")),
            "Git object/history query failed or was incomplete: " + process.get("stderr", ""))
    return process


def _object(path, expression):
    value = _git(path, "rev-parse", "--verify", "--end-of-options", expression)["stdout"].strip()
    require(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value), "Invalid Git object identity")
    return value


def _history(path, commit):
    return _git(path, "rev-list", "--parents", commit, "--")["stdout"].encode("ascii")


def _save_bytes(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
    return {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()}


def initial_git(workspace: Path, history_path: Path) -> dict:
    """Record the initialized baseline using object-only, read-only Git commands."""
    metadata = workspace / ".git"
    before = _metadata(metadata)
    commit = _object(metadata, "HEAD^{commit}")
    tree = _object(metadata, "HEAD^{tree}")
    history = _history(metadata, commit)
    require(_metadata(metadata) == before, "Git metadata changed while reading provenance")
    return {"commit": commit, "tree": tree, "history": _save_bytes(history_path, history)}


def _capture_git(workspace: Path, initial: dict, initial_history: bytes,
                 destination: Path, *, local_commits: str) -> dict:
    """Freeze safe metadata and retain a failed audit when Git provenance is invalid."""
    require(local_commits in {"allow", "forbid"}, "scope-invalid: Unknown local commit policy")
    workspace, destination = Path(workspace).absolute(), Path(destination).absolute()
    if os.path.lexists(destination):
        raise ValueError(f"input-exists: {destination}")
    require(destination.resolve() == destination and not destination.is_relative_to(workspace),
            "suite-invalid: Git evidence must be an unaliased private destination")
    destination.mkdir(mode=0o700)
    snapshot = None
    audit = {"schema": "coding-trial-git-audit/v1", "status": "failed", "code": "scope-invalid",
             "detail": "Git provenance was not established", "policy": local_commits,
             "expected": {"commit": initial["commit"], "tree": initial["tree"],
                          "history_sha256": initial["history"]["sha256"]},
             "terminal": {"commit": None, "tree": None, "history": None}}
    try:
        require(hashlib.sha256(initial_history).hexdigest() == initial["history"]["sha256"],
                "Initial Git history bytes changed")
        for key in ("commit", "tree"):
            require(isinstance(initial[key], str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", initial[key]),
                    "Invalid initial Git identity")
        source = workspace / ".git"
        entries, anchor = _metadata(source)
        target = destination / "metadata"
        copy_snapshot(source, target, entries)
        snapshot = {"path": "metadata", "inventory_sha256": fingerprint(entries)}
        frozen = _metadata(target)
        terminal = audit["terminal"]
        terminal["commit"] = _object(target, "HEAD^{commit}")
        terminal["tree"] = _object(target, "HEAD^{tree}")
        history = _history(target, terminal["commit"])
        terminal["history"] = _save_bytes(destination / "history.txt", history)
        baseline = initial["commit"]
        require(_object(target, baseline + "^{commit}") == baseline
                and _object(target, baseline + "^{tree}") == initial["tree"]
                and _history(target, baseline) == initial_history, "Original Git baseline/history changed")
        if local_commits == "forbid":
            require(terminal["commit"] == baseline and history == initial_history, "Local commits are forbidden")
        else:
            require(_git(target, "merge-base", "--is-ancestor", baseline, terminal["commit"],
                         acceptable=(0, 1))["returncode"] == 0, "Terminal HEAD does not descend from the original baseline")
        require(_metadata(source) == (entries, anchor) and _metadata(target) == frozen,
                "Git metadata changed during terminal capture")
        audit.update(status="passed", code=None, detail="Original baseline/history and local commit policy verified")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, configparser.Error) as exc:
        audit["detail"] = str(exc)
    raw = (json.dumps(audit, indent=2, allow_nan=False) + "\n").encode("utf-8")
    return {"snapshot": snapshot, "audit": _save_bytes(destination / "audit.json", raw)}
