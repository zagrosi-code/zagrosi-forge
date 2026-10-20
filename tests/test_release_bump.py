"""Version-pinned hosts receive runtime changes only under a new release version."""
import json
from pathlib import Path
import subprocess
import sys

from forge_test_helpers import ROOT

TOOL = ROOT / "tools" / "sync_release.py"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=Forge", "-c", "user.email=forge@example.invalid", *args],
                   cwd=repo, check=True, capture_output=True)


def release(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(f'[project]\nname = "example"\nversion = "{version}"\n')
    for name in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        (repo / name).write_text(json.dumps({"name": "example", "version": version}) + "\n")


def commit(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"Change {path}")


def check(repo: Path, base: str) -> tuple[int, dict]:
    result = subprocess.run([sys.executable, str(TOOL), "--plugin-root", str(repo), "--check", "--base", base],
                            capture_output=True, text=True, check=False)
    assert "Traceback" not in result.stderr
    return result.returncode, json.loads(result.stdout)


def released_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "plugin with spaces"
    for name in (".codex-plugin", ".claude-plugin"):
        (repo / name).mkdir(parents=True)
    git(repo, "init", "-q")
    release(repo, "1.0.0")
    commit(repo, "skills/example/SKILL.md", "original\n")
    git(repo, "branch", "base")
    return repo


def test_runtime_change_requires_version_bump(tmp_path):
    repo = released_repo(tmp_path)
    commit(repo, "tests/test_example.py", "def test_example():\n    pass\n")
    assert check(repo, "base") == (0, {"success": True, "changed": [], "unreleased": []})

    commit(repo, "skills/example/SKILL.md", "changed\n")
    code, report = check(repo, "base")
    assert code == 1 and report["success"] is False
    assert report["unreleased"] == ["skills/example/SKILL.md"]
    assert "1.0.0" in report["error"]

    release(repo, "1.0.1")
    git(repo, "commit", "-qam", "Release 1.0.1")
    assert check(repo, "base") == (0, {"success": True, "changed": [], "unreleased": []})

    code, report = check(repo, "missing-base")
    assert code == 1 and report["success"] is False and "missing-base" in report["error"]


def test_non_ascii_runtime_paths_require_version_bump(tmp_path):
    repo = released_repo(tmp_path)
    commit(repo, "skills/\u00fcnicode/SKILL.md", "new\n")
    code, report = check(repo, "base")
    assert code == 1 and report["unreleased"] == ["skills/\u00fcnicode/SKILL.md"]
