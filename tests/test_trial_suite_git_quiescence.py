"""Real task-local Git initialization must not start automatic maintenance.

Trace2 observes actual child launches, so this does not depend on catching a
short-lived maintenance.lock. No Git command or trace is executed at authoring.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import coding_trial_git as git_source
from coding_trial_inventory import inventory
from trial_suite_fixtures import write_files


@pytest.mark.parametrize("exclusions", [None, (".scratch",)])
def test_baseline_commit_is_quiescent_and_preserves_original_git_provenance(tmp_path, monkeypatch, exclusions):
    workspace = tmp_path / "workspace"
    baseline = {"backend/app.py": "answer = 42\n", ".planning/notes.md": "Original fixture notes.\n"}
    write_files(workspace, baseline)
    trace = tmp_path / "git-trace2.jsonl"
    invocations = tmp_path / "git-invocations.jsonl"
    real_run, environments = subprocess.run, []

    def traced_run(argv, **kwargs):
        # Delegate the real command unchanged apart from an owned trace sink.
        env = dict(kwargs["env"])
        assert all(env[key] == os.devnull for key in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"))
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert not {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"} & env.keys()
        environments.append(dict(env))
        env["GIT_TRACE2_EVENT"] = str(trace.resolve())
        result = real_run(argv, **{**kwargs, "env": env})
        with invocations.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"argv": argv, "cwd": str(kwargs["cwd"]),
                                     "returncode": result.returncode,
                                     "stdout": result.stdout, "stderr": result.stderr}) + "\n")
        return result

    with monkeypatch.context() as observing:
        observing.setattr(git_source.subprocess, "run", traced_run)
        git_source.initialize_repository(workspace, baseline, artifact_exclusions=exclusions)

    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert any(row["event"] == "start" and "commit" in row.get("argv", []) for row in records)
    automatic = [row for row in records if row["event"] == "child_start"
                 and "--auto" in row.get("argv", [])
                 and any(command in row["argv"] for command in ("maintenance", "gc"))]
    assert automatic == [], f"Unowned automatic work was launched; raw trace: {trace}; events: {automatic}"

    def query(*args):
        return real_run(["git", "--no-replace-objects", "-c", "maintenance.auto=false",
                         "-c", "gc.auto=0", *args], cwd=workspace, env=environments[-1],
                        capture_output=True, text=True, check=True, timeout=10).stdout

    commit, tree = query("rev-parse", "HEAD").strip(), query("rev-parse", "HEAD^{tree}").strip()
    expected_paths = sorted(name for name in baseline if exclusions is not None or not name.startswith(".planning/"))
    assert query("ls-tree", "-r", "--name-only", "HEAD").splitlines() == expected_paths
    for name in expected_paths:
        assert query("cat-file", "blob", f"HEAD:{name}") == baseline[name]
    commit_object = query("cat-file", "commit", "HEAD")
    assert commit_object.startswith(f"tree {tree}\n") and "\nparent " not in commit_object
    assert "author Forge Trial <forge-trial@example.invalid> 946684800 +0000\n" in commit_object
    assert "committer Forge Trial <forge-trial@example.invalid> 946684800 +0000\n" in commit_object
    assert commit_object.endswith("\n\nTrial baseline\n")
    before = inventory(workspace / ".git")
    history_path = tmp_path / "initial-history.txt"
    initial = git_source.initial_git(workspace, history_path)
    assert initial["commit"] == commit and initial["tree"] == tree
    assert history_path.read_bytes() == (commit + "\n").encode("ascii")
    assert initial["history"] == {"path": history_path.name,
                                   "sha256": hashlib.sha256(history_path.read_bytes()).hexdigest()}
    assert inventory(workspace / ".git") == before
