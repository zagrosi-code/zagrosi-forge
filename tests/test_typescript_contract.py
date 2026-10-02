"""External consumer compilation preserves APIs that runtime stripping cannot see."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/evals/coding/typescript-access"
ORACLE = ROOT / "tools/typescript_trial_checks.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node24 is needed for the TypeScript fixture")


def execute(workspace, *arguments):
    return subprocess.run(["node", *map(str, arguments)], cwd=workspace,
                          capture_output=True, text=True, timeout=30)


@pytest.fixture
def workspace(tmp_path):
    return Path(shutil.copytree(FIXTURE, tmp_path / "workspace with spaces"))


@pytest.mark.parametrize("mutation", ["return", "actor", "nullable", "export", "any", "generic"])
def test_type_only_regressions_fail_despite_passing_runtime_tests(workspace, mutation):
    path = workspace / "src/access.ts"
    source = path.read_text()
    if mutation == "return":
        source = source.replace("role: string): Member", "role: string): Member | undefined")
    elif mutation == "actor":
        source = source.replace("type Actor = {", "type Actor = { region: string;")
    elif mutation == "nullable":
        source = source.replace("actor: Actor", "actor: Exclude<Actor, null>")
    elif mutation == "export":
        source = source.replace("export type Actor", "type Actor")
    elif mutation == "any":
        source = "// @ts-nocheck\n" + source.replace("role: string): Member", "role: string): any")
    else:
        source = source.replace("transaction<T>(work: () => T): T", "transaction(work: () => Member): Member")
    path.write_text(source)
    runtime = execute(workspace, "--test", "tests/access.test.ts")
    assert runtime.returncode == 0, runtime.stderr
    result = execute(workspace, ORACLE, workspace, "typescript-access")
    assert result.returncode != 0, result.stdout
    assert "TypeScript public contract" in result.stderr


@pytest.mark.parametrize("split", [False, True])
def test_baseline_and_cohesive_module_split_preserve_all_contracts(workspace, split):
    if split:
        path = workspace / "src/access.ts"
        source, policy = path.read_text().split("export function canManage", 1)
        path.write_text(source + "export { canManage } from './policy.ts';\n")
        (workspace / "src/policy.ts").write_text(
            "import type { Actor, Member } from './access.ts';\nexport function canManage" + policy)
    before = {path.relative_to(workspace): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    result = execute(workspace, ORACLE, workspace, "typescript-access")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"case": "typescript-access", "assertions": 394}
    assert {path.relative_to(workspace): path.read_bytes() for path in workspace.rglob("*") if path.is_file()} == before


def test_candidate_tests_and_settings_cannot_disable_external_contract(workspace):
    path = workspace / "src/access.ts"
    path.write_text(path.read_text().replace("role: string): Member", "role: string): any"))
    (workspace / "tests/access.test.ts").write_text("// Runtime tests cannot replace the external contract.\n")
    (workspace / "tests/typescript_public_contract.ts").write_text("export {};\n")
    (workspace / "tsconfig.json").write_text('{"compilerOptions":{"noCheck":true,"strict":false},"files":[]}')
    assert execute(workspace, "--test", "tests/access.test.ts").returncode == 0
    result = execute(workspace, ORACLE, workspace, "typescript-access")
    assert result.returncode != 0, result.stdout
    assert "TypeScript public contract" in result.stderr


@pytest.mark.parametrize("version", [None, "0.0.0"])
def test_missing_or_unpinned_compiler_fails_with_setup_guidance(workspace, tmp_path, version):
    tools = tmp_path / "isolated-tools"
    tools.mkdir()
    for name in ("typescript_type_checks.mjs", "package.json"):
        shutil.copyfile(ROOT / "tools" / name, tools / name)
    if version:
        compiler = tools / "node_modules/typescript/bin/tsc"
        compiler.parent.mkdir(parents=True)
        compiler.write_text("throw new Error('wrong compiler executed');")
        (compiler.parent.parent / "package.json").write_text(json.dumps({"version": version}))
    result = execute(workspace, "--input-type=module", "--eval",
                     "const {checkPublicTypes} = await import(process.argv[1]); checkPublicTypes(process.argv[2]);",
                     (tools / "typescript_type_checks.mjs").as_uri(), workspace)
    assert result.returncode != 0
    assert "requires compiler 7.0.2" in result.stderr
    assert "npm ci --prefix tools" in result.stderr
    assert "wrong compiler executed" not in result.stderr


def test_type_check_runs_before_candidate_module_execution(workspace):
    path = workspace / "src/access.ts"
    path.write_text(path.read_text().replace("role: string): Member", "role: string): any"))
    with path.open("a") as source:
        source.write("\nthrow new Error('candidate executed');\n")
    result = execute(workspace, ORACLE, workspace, "typescript-access")
    assert result.returncode != 0
    assert "TypeScript public contract" in result.stderr
    assert "candidate executed" not in result.stderr


def test_trial_acceptance_requires_static_contract(tmp_path):
    from test_coding_trials import trials

    trial = tmp_path / "trial"
    trials.prepare(trial, "typescript-access", plain_agent=True)
    path = trial / "workspace/src/access.ts"
    path.write_text(path.read_text().replace("role: string): Member", "role: string): any"))
    result = trials.check(trial)
    assert result["tests"]["returncode"] == 0
    assert result["oracle"]["returncode"] != 0
    assert "TypeScript public contract" in result["oracle"]["stderr"]
    assert not result["behavior"]["success"]
    assert not result["success"]
