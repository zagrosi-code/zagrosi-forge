"""Both hosts use the same declared package and portable workflow runtime."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from forge_test_helpers import ROOT
from test_compact_plan import SECTION, make_plan

MANIFEST = ".codex-plugin/package-files.json"
CLAUDE_FILES = {f".claude-plugin/{name}.json" for name in ("plugin", "marketplace")}


@pytest.fixture
def package(tmp_path):
    package = tmp_path / "plugin cache with spaces"
    members = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    assert CLAUDE_FILES <= set(members)
    for name in members:
        destination = package / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    return package


def run(command, cwd, *, success=True):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    assert (result.returncode == 0) is success, result.stdout + result.stderr
    assert "Traceback" not in result.stderr and not result.stdout.startswith("Traceback")
    return json.loads(result.stdout)


def helper(package, *args):
    return [sys.executable, str(package / "scripts/zagrosi_skills.py"), *map(str, args)]


@pytest.mark.parametrize("name", ["plugin", "marketplace"])
@pytest.mark.parametrize("contents", [None, "{", "[]", '"not an object"'])
def test_doctor_reports_missing_or_invalid_claude_metadata(package, name, contents):
    path = package / ".claude-plugin" / f"{name}.json"
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents, encoding="utf-8")
    result = run(helper(package, "doctor", "--plugin-root", package, "--strict"), package, success=False)
    code = "missing-package-file" if contents is None else f"invalid-claude-{name}-json"
    assert any(item["code"] == code and Path(item["path"]) == path for item in result["findings"])


@pytest.mark.parametrize("name,change,code", [
    ("plugin", {"name": "other"}, "claude-plugin-name"),
    ("plugin", {"skills": "./other-skills/"}, "claude-skill-root"),
    ("marketplace", {"name": "other"}, "claude-marketplace-name"),
    ("marketplace", {"plugins": []}, "claude-marketplace-plugin"),
    ("marketplace", {"plugins": [{"name": "zagrosi-forge", "source": "./other"}]}, "claude-marketplace-source"),
])
def test_doctor_rejects_claude_metadata_that_cannot_load_shared_skills(package, name, change, code):
    path = package / ".claude-plugin" / f"{name}.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**metadata, **change}), encoding="utf-8")
    result = run(helper(package, "doctor", "--plugin-root", package, "--strict"), package, success=False)
    assert code in {item["code"] for item in result["findings"]}


def test_release_check_validates_both_claude_files(package):
    for name in CLAUDE_FILES:
        (package / name).write_text("{", encoding="utf-8")
    result = run(helper(package, "release-check", "--plugin-root", package), package, success=False)
    assert {"validate-claude-plugin", "validate-claude-marketplace"} <= set(result["failed_checks"])


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_copied_package_runs_shared_workflows_and_resumes(package, tmp_path, depth):
    target = tmp_path / "separate target with spaces"
    target.mkdir()
    project = target / "project planning"
    setup = run(helper(package, "project-setup", "--brief", "REQ-001: Trim label edges; preserve internal whitespace and case.",
                       "--planning-dir", project, "--depth", depth), target)
    assert setup["depth_mode"] == depth
    assert Path(setup["initial_file"]).is_file()
    planning = project / "01-normalize"
    planning.mkdir()
    spec = planning / "spec.md"
    spec.write_text("REQ-001: Strip label edge whitespace; preserve internal whitespace and case.\n", encoding="utf-8")
    original_spec = spec.read_bytes()
    plan = run(helper(package, "plan-setup", "--file", spec, "--plugin-root", package,
                      "--target-dir", target, "--depth", depth), target)
    assert plan["depth_mode"] == depth and plan["scaffold"]["created"]

    # Supply a reviewed contract in place of the generated unfinished draft.
    shutil.rmtree(planning / "sections")
    make_plan(planning, depth)
    test_command = "python -m unittest discover -s tests"
    for path in (planning / "sections").glob("*.md"):
        path.write_text(path.read_text(encoding="utf-8").replace("uv run pytest tests/test_labels.py", test_command), encoding="utf-8")
    for name in ("src", "tests"):
        (target / name).mkdir()
    source = target / "src/labels.py"
    source.write_text("def normalize(value):\n    return value\n", encoding="utf-8")
    (target / "tests/test_labels.py").write_text(
        "import unittest\nfrom src.labels import normalize\n\n"
        "class LabelTests(unittest.TestCase):\n"
        "    def test_trim_edges(self):\n"
        "        self.assertEqual(normalize(' Ada  Lovelace '), 'Ada  Lovelace')\n", encoding="utf-8")

    def follow(command):
        assert command[:2] == helper(package)
        return run(command, target)

    follow(plan["commands"]["verify_plan"])
    entry = follow(plan["commands"]["implement_after_pass"])
    assert entry["next_section"] == SECTION
    test_argv = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
    assert subprocess.run(test_argv, cwd=target, capture_output=True).returncode != 0
    source.write_text("def normalize(value):\n    return value.strip()\n", encoding="utf-8")
    verified = run(helper(package, "implement-verify", "--planning-dir", planning, "--target-dir", target,
                          "--section", SECTION, "--integration", "--", *test_argv), target)
    assert verified["outcome"] == "passed"
    values = {"<review-status>": "pass", "<verification>": test_command, "<changed-file>": "src/labels.py"}
    record_command = [values.get(value, value) for value in entry["commands"]["record"]]
    record = follow(record_command)
    assert record["recorded"] is True
    follow(record["commands"]["postflight"])
    resumed = run(helper(package, "status", "--path", planning), target)
    assert resumed["remaining_sections"] == []
    assert spec.read_bytes() == original_spec
    assert not (package / "project planning").exists()


def test_copied_package_preserves_codex_configuration_and_is_idempotent(package, tmp_path):
    config = tmp_path / "codex home with spaces" / "config.toml"
    config.parent.mkdir()
    unrelated = b'# keep user settings\r\n[plugins."other@example"]\r\nenabled = true\r\n'
    config.write_bytes(unrelated)
    command = helper(package, "install", "--plugin-root", package, "--config", config, "--no-verify-codex")
    installed = run(command, tmp_path)
    assert unrelated in config.read_bytes()
    cache = Path(installed["cache"]["path"])
    for name in [*CLAUDE_FILES, ".codex-plugin/plugin.json", ".agents/plugins/marketplace.json",
                 *(f"skills/zagrosi-{skill}/SKILL.md" for skill in ("project", "plan", "implement"))]:
        assert (cache / name).read_bytes() == (package / name).read_bytes()
    before = config.read_bytes()
    assert run(command, tmp_path)["changed"] is False
    assert config.read_bytes() == before
    assert run(helper(cache, "doctor", "--plugin-root", cache, "--strict"), tmp_path)["success"]


def test_package_inventory_admits_only_staged_claude_files(tmp_path):
    root = tmp_path / "package source with spaces"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / ".codex-plugin").mkdir()
    (root / MANIFEST).write_text("[]\n", encoding="utf-8")
    (root / ".claude-plugin").mkdir()
    for name in [*CLAUDE_FILES, ".claude-plugin/local-settings.json"]:
        (root / name).write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", MANIFEST, *sorted(CLAUDE_FILES)], cwd=root, check=True, capture_output=True)
    command = [sys.executable, str(ROOT / "tools/update_package_manifest.py"), "--root", str(root)]
    subprocess.run(command, check=True, capture_output=True)
    assert set(json.loads((root / MANIFEST).read_text(encoding="utf-8"))) == {MANIFEST, *CLAUDE_FILES}
    subprocess.run([*command, "--check"], check=True, capture_output=True)
