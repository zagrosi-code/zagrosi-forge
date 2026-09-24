"""Repository evidence follows actual packages and test configuration."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest
from forge_test_helpers import load_zagrosi_module, run_raw


@pytest.fixture
def evidence():
    return load_zagrosi_module().evidence


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def commands(evidence, root):
    return evidence.repository_commands(root, evidence.evidence_files(root))


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is unavailable")
def test_git_discovery_prunes_ignored_archives_and_keeps_tracked_ignored_files(evidence, tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    write(tmp_path, ".gitignore", "docs/development/\n*.local.py\n")
    write(tmp_path, "scripts/runtime.py", "")
    write(tmp_path, "src/tracked.local.py", "")
    write(tmp_path, "src/new.py", "")
    write(tmp_path, "src/deleted.py", "")
    write(tmp_path, "src/private.local.py", "")
    write(tmp_path, "node_modules/vendor.js", "")
    write(tmp_path, "docs/development/trials/workspace/pyproject.toml", "[tool.pytest.ini_options]\n")
    for index in range(90):
        write(tmp_path, f"docs/development/trials/workspace/src/fixture{index}.py", "")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "src/tracked.local.py", "src/deleted.py"], check=True)
    (tmp_path / "src/deleted.py").unlink()
    monkeypatch.setattr(evidence.os, "walk", lambda *_: pytest.fail("Git inventory must not walk ignored archives"))
    names = {path.as_posix() for path in evidence.evidence_files(tmp_path)}
    assert names == {".gitignore", "scripts/runtime.py", "src/tracked.local.py", "src/new.py"}
    assert commands(evidence, tmp_path) == []
    assert evidence.evidence_files(tmp_path / "src") == [Path("new.py"), Path("tracked.local.py")]


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is unavailable")
@pytest.mark.parametrize("boundary", ["nested-repository", "submodule"])
def test_git_discovery_includes_repository_boundaries(evidence, tmp_path, monkeypatch, boundary):
    root = tmp_path / "root"
    source = tmp_path / "source" if boundary == "submodule" else root / "services/billing"
    for path in (root, source):
        subprocess.run(["git", "init", "-q", str(path)], check=True)
    write(source, ".gitignore", "archives/\n*.local.py\n")
    write(source, "pyproject.toml", "[project]\nname='billing'\n")
    write(source, "app.py", "")
    write(source, "tests/test_app.py", "from unittest import TestCase\n")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Forge", "-c", "user.email=forge@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], check=True)
    if boundary == "submodule":
        subprocess.run(["git", "-C", str(root), "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                        str(source), "services/billing"], check=True)
    nested = root / "services/billing"
    write(nested, "archives/snapshot/app.py", "")
    write(nested, "private.local.py", "")
    write(nested, "node_modules/vendor.js", "")
    monkeypatch.setattr(evidence.os, "walk", lambda *_: pytest.fail("Repository inventories must prune ignored trees"))
    expected = {Path("services/billing") / name for name in (".gitignore", "app.py", "pyproject.toml", "tests/test_app.py")}
    if boundary == "submodule":
        expected.add(Path(".gitmodules"))
    assert set(evidence.evidence_files(root)) == expected
    assert commands(evidence, root) == ["cd services/billing && python -m unittest discover -s tests"]
    if boundary == "submodule":
        subprocess.run(["git", "-C", str(root), "submodule", "--quiet", "deinit", "-f", "--", "services/billing"], check=True)
        assert evidence.evidence_files(root) == [Path(".gitmodules")]


def test_git_discovery_rejects_self_parent_and_symlink_boundaries(evidence, tmp_path, monkeypatch):
    root = tmp_path / "root"
    write(root, "service/app.py", "")
    try:
        (root / "alias").symlink_to(root, target_is_directory=True)
        (root / "external").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks unavailable")
    calls = []

    def inventory(argv, *, cwd, **kwargs):
        calls.append(cwd)
        assert len(calls) <= 2, "Self/parent/symlink entries must not recurse"
        names = b".\0..\0alias\0external/root/service\0service\0" if cwd == root else b"app.py\0"
        return subprocess.CompletedProcess(argv, 0, names)

    monkeypatch.setattr(evidence.subprocess, "run", inventory)
    assert evidence.evidence_files(root) == [Path("service/app.py")]
    assert calls == [root, root / "service"]


@pytest.mark.parametrize("failure", [FileNotFoundError, subprocess.TimeoutExpired])
def test_git_unavailable_or_timed_out_keeps_filtered_filesystem_fallback(evidence, tmp_path, monkeypatch, failure):
    write(tmp_path, "src/runtime.py", "")
    write(tmp_path, "node_modules/vendor.js", "")

    def unavailable(*args, **kwargs):
        raise failure("git", 10) if failure is subprocess.TimeoutExpired else failure("git")

    monkeypatch.setattr(evidence.subprocess, "run", unavailable)
    assert evidence.evidence_files(tmp_path) == [Path("src/runtime.py")]


@pytest.mark.parametrize("manager,lock", [("pnpm", "pnpm-lock.yaml"), ("yarn", "yarn.lock"), ("bun", "bun.lock")])
def test_workspace_commands_inherit_manager(evidence, tmp_path, manager, lock):
    write(tmp_path, lock, "")
    write(tmp_path, "package.json", json.dumps({"scripts": {"test": "run-tests"}}))
    write(tmp_path, "packages/invoices/package.json", json.dumps({"scripts": {"test:unit": "run-tests"}}))
    assert commands(evidence, tmp_path) == sorted([f"cd packages/invoices && {manager} run test:unit", f"{manager} run test"])


def test_explicit_package_manager_and_nested_override(evidence, tmp_path):
    write(tmp_path, "yarn.lock", "")
    write(tmp_path, "package.json", json.dumps({"packageManager": "pnpm@10.0.0", "scripts": {"test": "run-tests"}}))
    write(tmp_path, "packages/a b/package.json", json.dumps({"packageManager": "npm@11", "scripts": {"lint": "lint"}}))
    assert commands(evidence, tmp_path) == ["cd 'packages/a b' && npm run lint", "pnpm run test"]


def test_unconfigured_python_does_not_invent_pytest(evidence, tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname='invoices'\n")
    assert commands(evidence, tmp_path) == []
    write(tmp_path, "tests/test_invoice.py", "import unittest\n")
    assert commands(evidence, tmp_path) == ["python -m unittest discover -s tests"]


@pytest.mark.parametrize("configuration", [
    "[tool.pytest.ini_options]\ntestpaths=['tests']\n",
    "[project.optional-dependencies]\ntest=['pytest>=8']\n",
    "[dependency-groups]\ntest=['pytest>=8']\n",
])
def test_python_uses_configured_runner(evidence, tmp_path, configuration):
    write(tmp_path, "pyproject.toml", configuration)
    assert commands(evidence, tmp_path) == ["python -m pytest"]
    write(tmp_path, "uv.lock", "")
    assert commands(evidence, tmp_path) == ["uv run pytest"]


def test_monorepo_sources_and_test_names(evidence, tmp_path, capsys):
    from argparse import Namespace

    for path in ("packages/invoices/src/index.ts", "packages/invoices/src/speculation.ts",
                 "packages/invoices/tests/invoice.test.ts", "node_modules/vendor/src/ignored.ts"):
        write(tmp_path, path, "")
    evidence.codebase_evidence(Namespace(target_dir=str(tmp_path), planning_dir=None, max_tests=80, write=False))
    result = json.loads(capsys.readouterr().out)
    assert result["source_files"] == ["packages/invoices/src/index.ts", "packages/invoices/src/speculation.ts"]
    assert result["test_files"] == ["packages/invoices/tests/invoice.test.ts"]


def test_invalid_configuration_does_not_abort_evidence(evidence, tmp_path):
    write(tmp_path, "package.json", "{invalid")
    write(tmp_path, "pyproject.toml", "[unfinished")
    write(tmp_path, "services/invoices/go.mod", "module invoices")
    assert commands(evidence, tmp_path) == ["cd services/invoices && go test ./..."]


@pytest.mark.parametrize("name,content", [
    ("pytest.ini", "[pytest]\ntestpaths=tests\n"),
    ("pytest.ini", ""),
    ("setup.cfg", "[tool:pytest]\ntestpaths=tests\n"),
    ("tox.ini", "[pytest]\ntestpaths=tests\n"),
])
def test_standalone_pytest_configuration_is_discovered(evidence, tmp_path, name, content):
    write(tmp_path, name, content)
    assert commands(evidence, tmp_path) == ["python -m pytest"]


def test_unittest_without_packaging_metadata_is_discovered(evidence, tmp_path):
    write(tmp_path, "tests/test_invoice.py", "from unittest import TestCase\n")
    assert commands(evidence, tmp_path) == ["python -m unittest discover -s tests"]


def test_nested_python_package_inherits_uv_workspace(evidence, tmp_path):
    write(tmp_path, "pyproject.toml", "[tool.uv.workspace]\nmembers=['packages/*']\n")
    write(tmp_path, "uv.lock", "")
    write(tmp_path, "packages/invoices/pyproject.toml", "[dependency-groups]\ndev=['pytest']\n")
    assert commands(evidence, tmp_path) == ["cd packages/invoices && uv run pytest"]


def test_malformed_table_shapes_and_unreadable_tests_do_not_abort_discovery(evidence, tmp_path):
    write(tmp_path, "pyproject.toml", "project='invalid'\ntool=42\ndependency-groups=[]\n")
    write(tmp_path, "services/invoices/go.mod", "module invoices")
    write(tmp_path, "tests/test_invoice.py", "")
    (tmp_path / "tests/test_invoice.py").write_bytes(b"\xff")
    assert commands(evidence, tmp_path) == ["cd services/invoices && go test ./..."]
    write(tmp_path, "pyproject.toml", "[project]\nname='invoices'\n")
    assert commands(evidence, tmp_path) == ["cd services/invoices && go test ./..."]


def test_source_packages_do_not_require_conventional_directory_names(evidence, tmp_path, capsys):
    from argparse import Namespace

    for path in ("invoices/model.py", "internal/invoices/service.go", "internal/invoices/service_test.go",
                 "dist/invoices/generated.py"):
        write(tmp_path, path, "")
    evidence.codebase_evidence(Namespace(target_dir=str(tmp_path), planning_dir=None, max_tests=80, write=False))
    result = json.loads(capsys.readouterr().out)
    assert result["source_files"] == ["internal/invoices/service.go", "invoices/model.py"]
    assert result["test_files"] == ["internal/invoices/service_test.go"]


@pytest.mark.parametrize("runner", ["unittest", "npm", "bun"])
def test_discovered_commands_satisfy_evidence_command_gate(evidence, tmp_path, capsys, runner):
    from argparse import Namespace

    if runner == "unittest":
        write(tmp_path, "tests/test_invoice.py", "from unittest import TestCase\n")
    else:
        write(tmp_path, "package.json", json.dumps({"packageManager": runner + "@1", "scripts": {"test": "run-tests"}}))
    write(tmp_path, "codex-plan.md", "REQ-001: verify invoice normalization.\n")
    evidence.codebase_evidence(Namespace(target_dir=str(tmp_path), planning_dir=str(tmp_path), max_tests=80, write=True))
    capsys.readouterr()
    result = json.loads(run_raw("lint-evidence", "--planning-dir", str(tmp_path)).stdout)
    assert "missing-command-evidence" not in {finding["code"] for finding in result["findings"]}


DISCOVERY = (
    "Test discovery: tests/test_ledger.py uses unittest.TestCase; test_total and test_receipt "
    "are the only discovered cases. The exact discovery command above ran 2 tests successfully "
    "before edits; no other test runners/configuration exist."
)


def discovery_findings(text):
    runtime = load_zagrosi_module()
    findings = []
    runtime.quality.add_term_findings(
        findings, text, {"test-discovery": runtime.policy.EVIDENCE_TERMS["test-discovery"]}, "plan.md", "medium",
    )
    return findings


@pytest.mark.parametrize("text", [
    DISCOVERY,
    DISCOVERY.replace("Test discovery:", "**Test discovery:**"),
    DISCOVERY.replace("Test discovery:", "- **Test discovery**:"),
    "Test discovery: `test_total` and `test_receipt` pass under unittest.",
    "Test discovery: `src/ledger.test.ts` contains the current Vitest cases.",
    "Existing tests: tests/test_ledger.py; test command recorded in Tests first. Tests discovered: 2, both passed.",
])
def test_equivalent_named_discovery_evidence_is_accepted(text):
    assert not discovery_findings(text)


@pytest.mark.parametrize("text", [
    "<!--\n" + DISCOVERY + "\n-->",
    "```text\n" + DISCOVERY + "\n```",
    "```text\n" + DISCOVERY,
    "Test discovery: pending",
    "Test discovery: tests/test_ledger.py; pending",
    "Test discovery: fixtures pending",
    "Test discovery:",
    "Test discovery tests/test_ledger.py",
    "Test discovery: complete",
    "Test discovery: src/ledger.py",
])
def test_hidden_malformed_or_placeholder_discovery_is_rejected(text):
    assert {finding.code for finding in discovery_findings(text)} == {"missing-test-discovery"}


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_named_discovery_is_shared_by_lint_and_score(tmp_path, capsys, depth):
    from argparse import Namespace
    from test_compact_plan import SECTION, make_plan

    runtime = load_zagrosi_module()
    planning = make_plan(tmp_path / "planning", depth)
    section = planning / "sections" / f"{SECTION}.md"
    text = section.read_text().replace("existing tests in", "inspected")
    section.write_text(text + "\n" + DISCOVERY + "\n")
    assert runtime.validation.lint_evidence(Namespace(planning_dir=str(planning), min_files=3, profile="solo", strict=True)) == 0
    assert not json.loads(capsys.readouterr().out)["findings"]
    assert not runtime.scoring.evidence_findings_for_score(planning, 3)
