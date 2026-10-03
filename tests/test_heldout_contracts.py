"""Independent task oracles accept working controls and reject semantic regressions."""
import json
import os
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
    ("atomic-publish", "buffered-path"),
    ("atomic-publish", "descriptor"),
    ("atomic-publish", "descriptor-aliases"),
])
def test_working_control_passes_candidate_tests_and_external_oracle(tmp_path, family, variant):
    workspace = tmp_path / "workspace"
    shutil.copytree(trials.PACK / trials.CASES[family]["fixture"], workspace)
    before = trials.files(workspace)
    shutil.copytree(controls(family) / "good", workspace, dirs_exist_ok=True)
    if variant != "original":
        source = workspace / "src" / ("settings.py" if family == "config-layers" else "publisher.py")
        original = source.read_text()
        if variant in {"buffered-path", "descriptor", "descriptor-aliases"}:
            staging = '''            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
            if previous[name] is not None:
                stage.chmod(previous[name][1])'''
            replacement = '''            os.close(descriptor)
            stage.write_bytes(data)
            if previous[name] is not None:
                stage.chmod(previous[name][1])'''
            if variant.startswith("descriptor"):
                replacement = '''            try:
                remaining = memoryview(data)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if not written:
                        raise OSError("staging write made no progress")
                    remaining = remaining[written:]
                if previous[name] is not None:
                    if hasattr(os, "fchmod"):
                        os.fchmod(descriptor, previous[name][1])
                    else:
                        stage.chmod(previous[name][1])
            finally:
                os.close(descriptor)'''
            assert original.count(staging) == 1
            changed = original.replace(staging, replacement)
            if variant == "descriptor-aliases":
                changed = changed.replace("import os\n", "import os\nfrom os import write as write_data\nfchmod = getattr(os, \"fchmod\", None)\n")
                changed = changed.replace("os.write(", "write_data(").replace("os.fchmod(", "fchmod(")
        elif variant == "import-aliases":
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
    if family == "atomic-publish":
        coverage = json.loads(result["stderr"])["staging_fault_coverage"]
        assert coverage == {"write": [True] * 3, "permissions": [True] * 3}, result
    after = trials.files(workspace)
    assert all(before[name] == after[name] for name in trials.CASES[family]["protected_paths"])


MUTANTS = [
    pytest.param(family, mutation, id=f"{family}-{mutation['name']}",
                 marks=pytest.mark.skipif(os.name == "nt" and mutation["name"] == "masks-special-permission-bits",
                                          reason="Windows does not preserve POSIX special permission bits"))
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


@pytest.mark.parametrize("windows_error", [False, True])
@pytest.mark.parametrize("temp_prefix_mutant", [False, True])
def test_publication_oracle_uses_baseline_filename_limit(tmp_path, windows_error, temp_prefix_mutant):
    workspace = tmp_path / "workspace"
    shutil.copytree(trials.PACK / "atomic-publish/fixture", workspace)
    shutil.copytree(controls("atomic-publish") / "good", workspace, dirs_exist_ok=True)
    if temp_prefix_mutant:
        source = workspace / "src/publisher.py"
        source.write_text(source.read_text().replace('prefix=".publish-"', 'prefix=f".{name}-"'))
    script = '''import errno, importlib.util, json, os, pathlib, sys
spec = importlib.util.spec_from_file_location("oracle", sys.argv[1])
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)
original_write, original_open = pathlib.Path.write_bytes, os.open
attempts, accepted = [], []
def limit(path):
    if len(pathlib.Path(path).name) > 220:
        error = OSError(errno.ENAMETOOLONG, "controlled component limit")
        if sys.argv[3] == "True":
            error = OSError(errno.EINVAL, "controlled Windows path limit")
            error.winerror = 206
        raise error
def write(path, data):
    if set(path.name) == {"n"}:
        attempts.append(len(path.name))
    limit(path)
    result = original_write(path, data)
    if set(path.name) == {"n"}:
        accepted.append(len(path.name))
    return result
def opening(path, *args, **kwargs):
    limit(path)
    return original_open(path, *args, **kwargs)
pathlib.Path.write_bytes, os.open = write, opening
try:
    count = oracle.verify(pathlib.Path(sys.argv[2]))
finally:
    print(json.dumps({"attempts": attempts, "accepted": accepted}))
assert count == 543
'''
    result = trials.execute([sys.executable, "-B", "-c", script,
                             str(trials.ROOT / "tools/publication_trial_checks.py"), str(workspace),
                             str(windows_error)], workspace)
    assert (result["returncode"] != 0) == temp_prefix_mutant, result
    observation = json.loads(result["stdout"])
    assert any(length > 220 for length in observation["attempts"]), result
    assert len(observation["accepted"]) == (1 if temp_prefix_mutant else 3), result
    assert all(1 <= length <= 220 for length in observation["accepted"]), result
    if temp_prefix_mutant:
        assert "AssertionError: A valid long filename stopped working" in result["stderr"], result


def test_publication_oracle_preserves_unrelated_baseline_failure(tmp_path):
    workspace = tmp_path / "workspace"
    shutil.copytree(trials.PACK / "atomic-publish/fixture", workspace)
    shutil.copytree(controls("atomic-publish") / "good", workspace, dirs_exist_ok=True)
    script = '''import errno, importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("oracle", sys.argv[1])
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)
marker = PermissionError(errno.EACCES, "unrelated permission error")
attempts = []
def fail(path, data):
    attempts.append(len(path.name))
    raise marker
pathlib.Path.write_bytes = fail
try:
    oracle.verify(pathlib.Path(sys.argv[2]))
except PermissionError as error:
    assert error is marker and attempts == [250]
else:
    raise AssertionError("Unrelated filesystem error was hidden")
'''
    result = trials.execute([sys.executable, "-B", "-c", script,
                             str(trials.ROOT / "tools/publication_trial_checks.py"), str(workspace)], workspace)
    assert result["returncode"] == 0, result
