"""Repository evidence follows actual packages and test configuration."""

import json

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
