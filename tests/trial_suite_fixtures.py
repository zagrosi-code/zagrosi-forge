"""Small synthetic task inputs; these fixtures never qualify a real provider."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

import pytest


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_files(root: Path, values: dict[str, str]) -> None:
    for name, text in values.items():
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")


def remove_owned_tree(root: Path) -> None:
    """Remove a temporary fixture tree, including read-only Windows Git objects."""
    def retry_readonly(function, name, error):
        failure = error[1]
        if (os.name != "nt" or function not in (os.unlink, os.remove)
                or not isinstance(failure, PermissionError)):
            raise failure
        path = Path(name)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & stat.S_IWRITE:
            raise failure
        path.chmod(stat.S_IMODE(info.st_mode) | stat.S_IWRITE)
        function(name)
    # onerror retains the package's Python 3.11 compatibility.
    shutil.rmtree(root, onerror=retry_readonly)
    assert not os.path.lexists(root), "The fixture must actually remove its requested tree"


def regular_entries(root: Path, names: tuple[str, ...]) -> dict:
    """Expected entries for explicitly named fixture files, not a tree scanner."""
    paths = set()
    for name in names:
        path = Path(name)
        paths.add(path)
        paths.update(parent for parent in path.parents if parent != Path("."))
    entries = {}
    for relative in sorted(paths):
        path = root / relative
        entry = {"type": "directory" if path.is_dir() else "file",
                 "mode": stat.S_IMODE(path.lstat().st_mode)}
        if entry["type"] == "file":
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        entries[relative.as_posix()] = entry
    return entries


def make_suite(root: Path) -> tuple[Path, dict]:
    """Three opaque arms share one non-src task; every qualification is unmeasured."""
    from importlib.metadata import version
    import sys

    package_version = version("packaging")  # Already installed with pytest; never install here.
    task_files = {
        "backend/app/__init__.py": "",
        "backend/app/service.py": "from packaging.version import Version\n\ndef normalize(value):\n    return value\n",
        "specs/test_service.py": (
            "import unittest\nfrom app.service import normalize\n\n"
            "class Versions(unittest.TestCase):\n"
            "    def test_normal_version(self):\n"
            "        self.assertEqual(normalize('1.0'), '1.0')\n"),
        "pyproject.toml": '[project]\nname = "neutral-fixture"\nversion = "0.0.0"\n'
                          f'dependencies = ["packaging=={package_version}"]\n',
        "deps.lock": f"packaging=={package_version}\n",
        "settings.toml": 'style = "normalized"\n',
        "AGENTS.md": "Preserve existing valid version behavior.\n",
    }
    write_files(root / "export", task_files)
    write_files(root, {
        "brief.md": "Normalize a version such as v1.0 using the existing packaging dependency.\n",
        "entries/alpha.md": "Use your normal workflow in {workspace}.\n",
        "entries/bravo.md": "Use the provided product in {product}, with task files in {workspace}.\n",
        "entries/charlie.md": "Follow this fixture product in {product}; work in {workspace}.\n",
        "checks/oracle.py": "# Synthetic private entry; no executed qualification is asserted.\n",
        "checks/worker.py": "# Synthetic public entry; no executed qualification is asserted.\n",
        "checks/support.py": "# Frozen helper resource.\n",
    })
    arms = {}
    for name, workflow, artifacts in (("alpha", "none", []),
                                      ("bravo", "forge-v1", [".planning"]),
                                      ("charlie", "unmeasured", [".third-work"])):
        product = None
        if name != "alpha":
            payload = f"products/{name}"
            payload_files = {"SKILL.md": "Synthetic payload; never a live loading receipt.\n",
                             "plugin.json": json.dumps({"name": name}) + "\n"}
            write_files(root / payload, payload_files)
            product = {"source_commit": None, "source_tree": None, "payload": payload,
                       "inventory_sha256": digest(regular_entries(root / payload, tuple(payload_files)))}
        arms[name] = {"product": product, "entry": f"entries/{name}.md",
                      "configuration": {"depth": "standard"} if name == "bravo" else {},
                      "workflow": workflow, "artifacts": artifacts,
                      "loading": {"adapter": "none", "receipt": None}}

    def command(name, entry, argv):
        return {"id": name, "entry": entry, "support": [], "argv": argv, "cwd": ".",
                "env": {}, "timeout_seconds": 10, "output_bytes": 4096}

    native = command("native", None, ["{python}", "-B", "-m", "unittest", "discover", "-s", "specs"])
    native["env"] = {"PYTHONPATH": "backend"}
    oracle = command("oracle", "checks/oracle.py", ["{python}", "-I", "-B", "{entry}", "{assessment}", "{receipt}"])
    oracle["support"] = ["checks/support.py"]
    worker = command("worker", "checks/worker.py", ["{python}", "-I", "-B", "{entry}"])
    suite = {
        "schema": "coding-trial-suite/v1", "id": "neutral-fixture", "purpose": "synthetic",
        "repeats": 1, "execution_seed": 17, "blind_seed": 29, "arms": arms,
        "host": {"adapter": "fixture", "executable": sys.executable, "version": sys.version.split()[0],
                 "model": None, "effort": None, "isolation": None,
                 "capabilities": {"tools": [], "subagents": False, "network": "none"},
                 "environment": {}, "credentials": None, "fixture_argv": ["{python}", "-c", "pass"]},
        "tasks": {"normalize": {
            "source": {"kind": "fixture", "url": None, "commit": None, "tree": None,
                       "export": "export", "preparation": None,
                       "baseline_sha256": digest(regular_entries(root / "export", tuple(task_files)))},
            "brief": "brief.md", "clarifications": None,
            "scope": {"implementation": ["backend/app"], "tests": ["specs"],
                      "config": ["pyproject.toml", "deps.lock", "settings.toml", "AGENTS.md"],
                      "allowed_changes": ["backend/app", "specs", "settings.toml"],
                      "protected": ["pyproject.toml", "deps.lock", "AGENTS.md"], "generated": [".cache"]},
            "dependencies": {"environment": None, "locks": ["pyproject.toml", "deps.lock"],
                             "allow_lock_changes": False},
            "checks": {"feature_ids": ["normalize-prefix"], "preservation_ids": ["normal-version"],
                       "native": [native], "oracle": oracle, "worker": worker},
            "cleanup_required": False, "local_commits": "allow", "admission": None,
        }},
    }
    path = root / "suite.json"
    path.write_text(json.dumps(suite, indent=2) + "\n", encoding="utf-8")
    return path, suite


def link(path: Path, target: str, *, directory=False) -> None:
    try:
        path.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks are unavailable: {exc}")
