from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from forge_test_helpers import (
    load_zagrosi_module,
    run_cmd,
    run_raw,
    write_single_section_fixture,
)


def test_patch_scope_preserves_long_file_extensions(tmp_path: Path) -> None:
    section_file = tmp_path / "section-01-snapshots.md"
    section_file.write_text(
        "# Section\n\n"
        "Update `examples/evals/suite.json`, `src/ui/Widget.jsx`, `src/ui/App.tsx`, and `config/settings.yaml`.\n"
    )
    diff_file = tmp_path / "scope.diff"
    diff_file.write_text(
        "diff --git a/examples/evals/suite.json b/examples/evals/suite.json\n"
        "+++ b/examples/evals/suite.json\n"
        "diff --git a/src/ui/Widget.jsx b/src/ui/Widget.jsx\n"
        "+++ b/src/ui/Widget.jsx\n"
        "diff --git a/src/ui/App.tsx b/src/ui/App.tsx\n"
        "+++ b/src/ui/App.tsx\n"
        "diff --git a/config/settings.yaml b/config/settings.yaml\n"
        "+++ b/config/settings.yaml\n"
    )

    scope = run_cmd("patch-scope", "--section-file", str(section_file), "--diff-file", str(diff_file), "--strict")

    assert scope["success"] is True
    assert scope["declared_files"] == [
        "config/settings.yaml",
        "examples/evals/suite.json",
        "src/ui/App.tsx",
        "src/ui/Widget.jsx",
    ]
    assert scope["out_of_scope"] == []


def test_patch_scope_accepts_declared_frontend_assets(tmp_path: Path) -> None:
    section_file = tmp_path / "section-01-ui.md"
    section_file.write_text("# Section\n\nUpdate `index.html`, `src/App.css`, and `public/logo.svg`.\n")
    diff_file = tmp_path / "scope.diff"
    diff_file.write_text(
        "diff --git a/index.html b/index.html\n"
        "+++ b/index.html\n"
        "diff --git a/src/App.css b/src/App.css\n"
        "+++ b/src/App.css\n"
        "diff --git a/public/logo.svg b/public/logo.svg\n"
        "+++ b/public/logo.svg\n"
    )

    scope = run_cmd("patch-scope", "--section-file", str(section_file), "--diff-file", str(diff_file), "--strict")

    assert scope["success"] is True
    assert scope["declared_files"] == ["index.html", "public/logo.svg", "src/App.css"]
    assert scope["out_of_scope"] == []


def test_owned_paths_aggregate_all_plaintext_fences() -> None:
    module = load_zagrosi_module()

    declared = module.ownership.extract_section_owned_paths(
        "# Toolchain\n\n"
        "## Exact path ownership\n\n"
        "Production paths:\n\n"
        "```text\n"
        "src/toolchain.py\n"
        "contracts/toolchain.json\n"
        "```\n\n"
        "Test paths:\n\n"
        "```plaintext\n"
        "tests/test_toolchain.py\n"
        "tests/fixtures/toolchain.json\n"
        "```\n"
    )

    assert declared == [
        "contracts/toolchain.json",
        "src/toolchain.py",
        "tests/fixtures/toolchain.json",
        "tests/test_toolchain.py",
    ]


@pytest.mark.parametrize("trailing_closer", ("", "~~~\n"))
def test_owned_paths_ignore_unclosed_trailing_plaintext_fence(
    trailing_closer: str,
) -> None:
    module = load_zagrosi_module()

    declared = module.ownership.extract_section_owned_paths(
        "# Toolchain\n\n"
        "## Exact path ownership\n\n"
        "```text\n"
        "src/toolchain.py\n"
        "```\n\n"
        "```text\n"
        "src/unplanned.py\n"
        f"{trailing_closer}"
    )

    assert declared == ["src/toolchain.py"]


def test_owned_paths_do_not_fall_back_to_prose_after_rejected_fence() -> None:
    module = load_zagrosi_module()

    declared = module.ownership.extract_section_owned_paths(
        "# Toolchain\n\n"
        "## Exact path ownership\n\n"
        "The rollout mentions `docs/incidental.md`, but it is not ownership.\n\n"
        "```text\n"
        "src/unclosed.py\n"
    )

    assert declared == []


def test_patch_scope_aggregates_owned_fences_and_excludes_other_mentions(
    tmp_path: Path,
) -> None:
    section_file = tmp_path / "section-01-toolchain.md"
    section_file.write_text(
        "# Toolchain\n\n"
        "## Exact path ownership\n\n"
        "The rollout notes mention `docs/toolchain.md`, but that prose is not ownership.\n\n"
        "```text\n"
        "src/toolchain.py\n"
        "```\n\n"
        "```python\n"
        "scripts/toolchain_example.py\n"
        "```\n\n"
        "```plaintext\n"
        "tests/test_toolchain.py\n"
        "```\n"
    )
    diff_file = tmp_path / "scope.diff"
    diff_file.write_text(
        "diff --git a/src/toolchain.py b/src/toolchain.py\n"
        "+++ b/src/toolchain.py\n"
        "diff --git a/tests/test_toolchain.py b/tests/test_toolchain.py\n"
        "+++ b/tests/test_toolchain.py\n"
        "diff --git a/docs/toolchain.md b/docs/toolchain.md\n"
        "+++ b/docs/toolchain.md\n"
        "diff --git a/scripts/toolchain_example.py b/scripts/toolchain_example.py\n"
        "+++ b/scripts/toolchain_example.py\n"
    )

    result = run_raw(
        "patch-scope",
        "--section-file",
        str(section_file),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert result.returncode != 0
    scope = json.loads(result.stdout)
    assert scope["declared_files"] == ["src/toolchain.py", "tests/test_toolchain.py"]
    assert scope["out_of_scope"] == ["docs/toolchain.md", "scripts/toolchain_example.py"]


def test_patch_scope_reports_untracked_files_by_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "src/auth").mkdir(parents=True)
    (repo / "src/auth/extra.py").write_text("SECRET = 'new file'\n")

    section_file = tmp_path / "section-01-auth.md"
    section_file.write_text("# Section\n\nModify `src/auth/oauth.py`.\n")

    scope = run_raw("patch-scope", "--section-file", str(section_file), "--repo", str(repo))

    assert scope.returncode != 0
    payload = json.loads(scope.stdout)
    assert "src/auth/extra.py" in payload["changed_files"]
    assert payload["out_of_scope"] == ["src/auth/extra.py"]
    assert any(item["code"] == "out-of-scope-file" for item in payload["findings"])


def test_patch_scope_staged_excludes_unstaged_worktree_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "src/auth").mkdir(parents=True)
    extra = repo / "src/auth/extra.py"
    extra.write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "src/auth/extra.py"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    extra.write_text("VALUE = 2\n")

    section_file = tmp_path / "section-01-auth.md"
    section_file.write_text("# Section\n\nModify `src/auth/oauth.py`.\n")

    scope = run_cmd("patch-scope", "--section-file", str(section_file), "--repo", str(repo), "--staged")

    assert scope["changed_files"] == []
    assert scope["out_of_scope"] == []


def test_implementation_drift_staged_excludes_unstaged_worktree_changes(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    write_single_section_fixture(planning, "section-01-auth")
    (planning / "sections" / "section-01-auth.md").write_text("# Section\n\nImplement `src/auth/oauth.py`.\n")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "src/auth").mkdir(parents=True)
    planned = repo / "src/auth/oauth.py"
    unrelated = repo / "src/auth/local.py"
    planned.write_text("VALUE = 1\n")
    unrelated.write_text("LOCAL = 1\n")
    subprocess.run(["git", "add", "src/auth/oauth.py", "src/auth/local.py"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    planned.write_text("VALUE = 2\n")
    subprocess.run(["git", "add", "src/auth/oauth.py"], cwd=repo, check=True, capture_output=True, text=True)
    unrelated.write_text("LOCAL = 2\n")

    drift = run_cmd("implementation-drift", "--planning-dir", str(planning), "--repo", str(repo), "--staged")

    assert drift["changed_files"] == ["src/auth/oauth.py"]
    assert drift["out_of_scope"] == []
