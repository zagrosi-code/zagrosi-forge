"""Windows partitions preserve the compatibility suite and its compiler setup."""
from collections import Counter
import re
import shlex

from forge_test_helpers import ROOT

WORKFLOW = ROOT / ".github/workflows/validate.yml"


def compatibility():
    text = WORKFLOW.read_text().split("  compatibility:\n", 1)[1].split("  native-plugins:\n", 1)[0]
    matrix = text.split("include:\n", 1)[1].split("    runs-on:", 1)[0]
    rows = [dict(re.findall(r"^\s*(os|python|group): [\"']?([^\s\"']+)", item, re.M))
            for item in re.split(r"^          - ", matrix, flags=re.M)[1:]]
    steps = re.split(r"^      - ", text.split("    steps:\n", 1)[1], flags=re.M)[1:]
    return rows, steps


def test_windows_partitions_cover_linux_and_macos_once():
    rows, steps = compatibility()
    assert [(row["os"], row.get("group")) for row in rows] == [
        ("ubuntu-latest", "all"), ("macos-latest", "all"),
        ("windows-latest", "shared"), ("windows-latest", "portable")]
    selected = {}
    for group in {row["group"] for row in rows}:
        tests = []
        for step in steps:
            command = re.search(r"^        run: python -m pytest -q (.+)$", step, re.M)
            if not command or "runner.os == 'macOS'" in step:
                continue
            condition = re.search(r"^        if: (.+)$", step, re.M)
            if condition:
                excluded = re.fullmatch(r"matrix.group != '([^']+)'", condition[1])
                assert excluded, "Update this coverage check when adding a new partition condition"
                if group == excluded[1]:
                    continue
            tests.extend(shlex.split(command[1]))
        selected[group] = Counter(tests)
    assert selected["shared"] and selected["portable"]
    assert not (selected["shared"] & selected["portable"])
    assert selected["shared"] + selected["portable"] == selected["all"]
    assert all(count == 1 for count in selected["all"].values())
    assert {
        "tests/test_compatibility_checks.py", "tests/test_compatibility_workflows.py",
        "tests/test_compatibility_admission.py", "tests/test_heldout_contracts.py",
    } <= selected["all"].keys()


def test_type_contract_compiler_is_installed_for_full_and_compatibility_tests():
    text = WORKFLOW.read_text()
    for name, following in (("validate", "compatibility"), ("compatibility", "native-plugins")):
        job = text.split(f"  {name}:\n", 1)[1].split(f"  {following}:\n", 1)[0]
        install = re.search(r"^      - name: Install trial compiler\n        run: npm ci --prefix tools$", job, re.M)
        assert install, f"{name} must install the pinned compiler without a partition guard"
        assert install.start() < job.index("-m pytest")
