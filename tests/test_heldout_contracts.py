"""Independent task oracles accept working controls and reject semantic regressions."""
import json
import shutil
import sys

import pytest

from test_coding_trials import trials

FAMILIES = ("config-layers", "atomic-publish", "stream-records", "node-events")
BASELINE_FAILURES = {
    "config-layers": "Missing explain_config feature",
    "atomic-publish": "Each destination requires an atomic replacement",
    "stream-records": "invalid UTF-8",
    "node-events": "Add subscribeOnce",
}


def controls(family):
    return trials.PACK / family / "controls"


def oracle(workspace, family):
    case = trials.CASES[family]
    runner = ["node"] if case.get("runtime") == "node" else [sys.executable, "-B"]
    return trials.execute([*runner, str(trials.ROOT / case["oracle"]), str(workspace), family], workspace)


@pytest.mark.parametrize("family", FAMILIES)
def test_baseline_is_runnable_and_writer_receives_no_answers(tmp_path, family):
    trial = tmp_path / "trial"
    trials.prepare(trial, family, plain_agent=True)
    workspace = trial / "workspace"
    assert {path.name for path in workspace.iterdir()} == {".git", "src", "tests", "prompt.md"}
    tests = trials.execute(trials.test_command(trials.CASES[family]), workspace)
    assert tests["returncode"] == 0, tests
    result = oracle(workspace, family)
    assert result["returncode"] != 0, result
    assert BASELINE_FAILURES[family] in result["stderr"], result


@pytest.mark.parametrize("family,variant", [
    *((family, "original") for family in FAMILIES),
    ("config-layers", "annotations"), ("atomic-publish", "annotations"),
    ("atomic-publish", "import-aliases"),
])
def test_working_control_passes_candidate_tests_and_external_oracle(tmp_path, family, variant):
    workspace = tmp_path / "workspace"
    shutil.copytree(trials.PACK / trials.CASES[family]["fixture"], workspace)
    before = trials.files(workspace)
    shutil.copytree(controls(family) / "good", workspace, dirs_exist_ok=True)
    if variant != "original":
        source = workspace / "src" / ("settings.py" if family == "config-layers" else "publisher.py")
        original = source.read_text()
        if variant == "import-aliases":
            changed = original.replace("import os\n", "import os\nfrom os import replace as swap, open as open_file\n")
            changed = changed.replace("os.replace(", "swap(").replace("os.open(", "open_file(")
        else:
            signature = ("load_config(layers, environ=None)" if family == "config-layers"
                         else "publish_files(directory, documents, on_publish=None)")
            annotated = ("load_config(layers: object, environ: object = None) -> dict"
                         if family == "config-layers" else
                         "publish_files(directory: object, documents: object, on_publish: object = None) -> list")
            assert original.count(signature) == 1
            changed = original.replace(signature, annotated)
        assert changed != original
        source.write_text(changed)
    tests = trials.execute(trials.test_command(trials.CASES[family]), workspace)
    assert tests["returncode"] == 0, tests
    result = oracle(workspace, family)
    assert result["returncode"] == 0, result
    assert json.loads(result["stdout"]) == {"case": family, "assertions": trials.CASES[family]["assertions"]}
    after = trials.files(workspace)
    assert all(before[name] == after[name] for name in trials.CASES[family]["protected_paths"])


MUTANTS = [
    pytest.param(family, mutation, id=f"{family}-{mutation['name']}")
    for family in FAMILIES
    for mutation in json.loads((controls(family) / "mutants.json").read_text())
]


@pytest.mark.parametrize("family,mutation", MUTANTS)
def test_external_oracle_rejects_regression_even_without_candidate_tests(tmp_path, family, mutation):
    workspace = tmp_path / "workspace"
    shutil.copytree(trials.PACK / trials.CASES[family]["fixture"], workspace)
    shutil.copytree(controls(family) / "good", workspace, dirs_exist_ok=True)
    source = workspace / mutation["path"]
    original = source.read_text()
    assert original.count(mutation["old"]) == mutation["count"] > 0
    changed = original.replace(mutation["old"], mutation["new"])
    assert changed != original and mutation["violates"].strip()
    source.write_text(changed)
    shutil.rmtree(workspace / "tests")
    result = oracle(workspace, family)
    assert result["returncode"] != 0, result
    assert "AssertionError" in result["stderr"], result
