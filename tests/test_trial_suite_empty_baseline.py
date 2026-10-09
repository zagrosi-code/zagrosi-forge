"""Empty suite baselines are valid inputs, not failed writer outcomes.

Only temporary Git and the controller version query run when activated. No
writer, provider, candidate check or container executes during preparation.
"""
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_git import initialize_repository
from coding_trial_inventory import fingerprint, inventory
from coding_trial_manifest import validate_suite
from coding_trial_suite import prepare_suite
from trial_suite_fixtures import regular_entries, write_files
from trial_suite_prepare_cases import PYTHON, bytes_at, git, marked_suite, read_reference, save_manifest


@pytest.mark.parametrize("kind", ["empty", "directories-only", "original-files"])
def test_prepare_accepts_empty_git_trees_and_preserves_the_original_file_baseline(tmp_path, kind):
    path, suite, marker = marked_suite(tmp_path)
    export = path.parent / "export"
    task = suite["tasks"]["normalize"]
    if kind != "original-files":
        shutil.rmtree(export)  # This is the fixture's freshly created export only.
        export.mkdir()
        names = () if kind == "empty" else ("backend/app", "specs")
        for name in names:
            (export / name).mkdir(parents=True)
        expected = regular_entries(export, names)
        task["scope"].update(config=[], protected=[], allowed_changes=["backend/app", "specs"])
        task["dependencies"]["locks"] = []
        task["source"]["baseline_sha256"] = fingerprint(expected)
        save_manifest(path, suite)
    else:
        expected = regular_entries(export, tuple(bytes_at(export)))
    # Prove the public manifest contract admits this shape before preparation.
    assert validate_suite(suite, path.parent) == suite
    curator_before = inventory(path.parent)
    trial = tmp_path / "trial"
    attempt = prepare_suite(trial, path, "normalize", "alpha", assessor_python=PYTHON)
    assert attempt["status"] == "prepared" and attempt["error"] is None
    assert attempt["runner"] is attempt["candidate"] is attempt["native"] is None
    assert attempt["assessments"] == [] and not marker.exists()
    assert attempt["identity"]["baseline_sha256"] == fingerprint(expected)
    workspace = trial / "workspace"
    assert inventory(workspace, excluded=(".git",)) == expected
    initial = attempt["initial_git"]
    assert initial["commit"] == git(workspace, "rev-parse", "HEAD^{commit}")
    assert initial["tree"] == git(workspace, "rev-parse", "HEAD^{tree}")
    assert git(workspace, "rev-list", "--parents", "HEAD") == initial["commit"]
    assert git(workspace, "show", "-s", "--format=%s", "HEAD") == "Trial baseline"
    _, history = read_reference(trial, initial["history"])
    assert history == (initial["commit"] + "\n").encode("ascii")
    expected_files = sorted(name for name, entry in expected.items() if entry["type"] == "file")
    assert git(workspace, "ls-tree", "-r", "--name-only", "HEAD").splitlines() == expected_files
    if not expected_files:
        assert git(workspace, "cat-file", "-s", "HEAD^{tree}") == "0"
    else:
        for name in expected_files:
            assert (workspace / name).read_bytes() == (export / name).read_bytes()
    assert git(workspace, "status", "--porcelain") == ""
    assert (workspace / ".git/info/exclude").read_text() == "/.cache\n"
    assert inventory(path.parent) == curator_before


@pytest.mark.parametrize("legacy", [False, True])
def test_empty_selected_baseline_never_stages_unlisted_workspace_files(tmp_path, legacy):
    workspace = tmp_path / "workspace"
    write_files(workspace, {"unlisted.txt": "User-owned diagnostic, outside the supplied baseline.\n",
                            ".planning/notes.md": "Legacy workflow material.\n",
                            ".cache/marker.txt": "Generated cache.\n"})
    before = bytes_at(workspace)
    baseline = {".planning/notes.md": before[".planning/notes.md"]} if legacy else {}
    exclusions = None if legacy else (".cache",)
    initialize_repository(workspace, baseline, artifact_exclusions=exclusions)
    assert git(workspace, "cat-file", "-s", "HEAD^{tree}") == "0"
    commit = git(workspace, "rev-parse", "HEAD")
    assert git(workspace, "rev-list", "--parents", "HEAD") == commit
    assert git(workspace, "ls-files") == ""
    assert {name: (workspace / name).read_bytes() for name in before} == before
    expected = "/.planning/\n__pycache__/\n.pytest_cache/\n*.pyc\n*.pyo\n" if legacy else "/.cache\n"
    assert (workspace / ".git/info/exclude").read_text() == expected
