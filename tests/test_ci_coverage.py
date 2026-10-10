"""Windows partitions preserve the compatibility suite and its compiler setup."""
from collections import Counter
import re
import shlex

import pytest

from forge_test_helpers import ROOT

WORKFLOW = ROOT / ".github/workflows/validate.yml"


def job_steps(text):
    return re.split(r"^      - ", text.split("    steps:\n", 1)[1], flags=re.M)[1:]


def pytest_selectors(steps, name, prefix, condition=None, *, full_suite=False):
    matches = [step for step in steps if step.splitlines()[0] == f"name: {name}"]
    assert len(matches) == 1, f"Expected exactly one pytest step: {name}"
    lines = matches[0].rstrip().splitlines()
    metadata = [f"name: {name}"]
    if condition:
        metadata.append(f"        if: {condition}")
    assert lines[:-1] == metadata, f"{name}: preserve the condition and single-line command"
    command = lines[-1].removeprefix("        run: ")
    assert lines[-1].startswith("        run: ") and (
        command == prefix or command.startswith(prefix + " ")
    ), f"{name}: expected command prefix {prefix!r}"
    try:
        args = shlex.split(command[len(prefix):])
    except ValueError as error:
        raise AssertionError(f"{name}: malformed command quoting: {error}") from error
    allowed_reports = Counter(("--durations=20", "--durations-min=1"))
    reports = Counter(arg for arg in args if arg in allowed_reports)
    assert not reports or reports == allowed_reports, f"{name}: use the exact reporting pair once each"
    selectors = [arg for arg in args if arg not in allowed_reports]
    assert all(re.fullmatch(r"tests/[A-Za-z0-9_/-]+\.py(?:::[A-Za-z_][A-Za-z_0-9]*)?", arg)
               for arg in selectors), f"{name}: unsupported pytest argument; preserve literal test selectors"
    if full_suite:
        assert not selectors, f"{name}: full validation must not select individual tests"
    else:
        assert selectors, f"{name}: compatibility commands must select tests explicitly"
    return selectors


def compatibility(text):
    text = text.split("  compatibility:\n", 1)[1].split("  native-plugins:\n", 1)[0]
    matrix = text.split("include:\n", 1)[1].split("    runs-on:", 1)[0]
    rows = [dict(re.findall(r"^\s*(os|python|group): [\"']?([^\s\"']+)", item, re.M))
            for item in re.split(r"^          - ", matrix, flags=re.M)[1:]]
    return rows, job_steps(text)


def test_windows_partitions_cover_linux_and_macos_once():
    text = WORKFLOW.read_text()
    validation = text.split("  validate:\n", 1)[1].split("  compatibility:\n", 1)[0]
    pytest_selectors(job_steps(validation), "Run tests", "uv run --with pytest python -m pytest",
                     full_suite=True)
    rows, steps = compatibility(text)
    assert [(row["os"], row.get("group")) for row in rows] == [
        ("ubuntu-latest", "all"), ("macos-latest", "all"),
        ("windows-latest", "shared"), ("windows-latest", "portable")]
    commands = []
    for name, excluded in (
        ("Check shared host packaging and review", "portable"),
        ("Check explicitly owned verification inputs", "portable"),
        ("Check portable workflows and runtime loading", "shared"),
    ):
        selectors = pytest_selectors(steps, name, "python -m pytest -q", f"matrix.group != '{excluded}'")
        commands.append((excluded, selectors))
    pytest_selectors(steps, "Check native processes on macOS", "python -m pytest -q",
                     "runner.os == 'macOS'")
    selected = {}
    for group in {row["group"] for row in rows}:
        tests = []
        for excluded, selectors in commands:
            if group != excluded:
                tests.extend(selectors)
        selected[group] = Counter(tests)
    assert selected["shared"] and selected["portable"]
    assert not (selected["shared"] & selected["portable"])
    assert selected["shared"] + selected["portable"] == selected["all"]
    assert all(count == 1 for count in selected["all"].values())
    assert {
        "tests/test_compatibility_checks.py", "tests/test_compatibility_workflows.py",
        "tests/test_compatibility_admission.py", "tests/test_heldout_contracts.py",
        "tests/test_team_state.py", "tests/test_team_git.py", "tests/test_team_config.py",
        "tests/test_team_collaboration.py", "tests/test_team_workflows.py", "tests/test_team_guidance.py",
        "tests/test_team_plan_claims.py", "tests/test_team_plans.py",
        "tests/test_team_plan_workflows.py", "tests/test_team_plan_integration.py",
    } <= selected["all"].keys()


def test_type_contract_compiler_is_installed_for_full_and_compatibility_tests():
    text = WORKFLOW.read_text()
    for name, following in (("validate", "compatibility"), ("compatibility", "native-plugins")):
        job = text.split(f"  {name}:\n", 1)[1].split(f"  {following}:\n", 1)[0]
        install = re.search(r"^      - name: Install trial compiler\n        run: npm ci --prefix tools$", job, re.M)
        assert install, f"{name} must install the pinned compiler without a partition guard"
        assert install.start() < job.index("-m pytest")


_REPORT_PAIR = " --durations=20 --durations-min=1"
_CI_TEST_STEPS = (
    "Run tests",
    "Check shared host packaging and review",
    "Check explicitly owned verification inputs",
    "Check portable workflows and runtime loading",
    "Check native processes on macOS",
)
_SHARED = _CI_TEST_STEPS[1]
_EXISTING_NODE = (
    "tests/test_installation.py::"
    "test_update_check_invalid_package_returns_json_without_mutation"
)


def _ci_run_line(text, step):
    marker = f"      - name: {step}\n"
    assert text.count(marker) == 1, f"Fixture needs exactly one {step!r} step"
    body = text.split(marker, 1)[1].split("\n      - ", 1)[0]
    lines = [line for line in body.splitlines() if line.startswith("        run: ")]
    assert len(lines) == 1, f"Fixture needs one run line for {step!r}"
    return lines[0]


def _ci_replace_run(text, step, replacement):
    line = _ci_run_line(text, step)
    assert text.count(line) == 1, "Fixture must replace only its selected command"
    return text.replace(line, replacement, 1)


def _ci_append(text, step, suffix):
    return _ci_replace_run(text, step, _ci_run_line(text, step) + suffix)


@pytest.fixture
def ci_guard_workflow(tmp_path, monkeypatch):
    original = WORKFLOW.read_text()
    # Keep the counter-inputs identical before and after reporting is installed.
    # Only the literal approved suffix is removed; unexpected flags are not erased.
    for step in _CI_TEST_STEPS:
        line = _ci_run_line(original, step)
        original = _ci_replace_run(original, step, line.removesuffix(_REPORT_PAIR))
    fixture = tmp_path / "validate.yml"

    def check(text):
        fixture.write_text(text)
        monkeypatch.setitem(globals(), "WORKFLOW", fixture)
        test_windows_partitions_cover_linux_and_macos_once()

    check(original)  # Every negative starts from a genuinely accepted workflow.
    return original, check


def test_ci_reporting_pair_keeps_current_commands_and_selectors(ci_guard_workflow):
    original, check = ci_guard_workflow
    assert original.count(_EXISTING_NODE) == 1
    reported = original
    for step in _CI_TEST_STEPS:
        reported = _ci_append(reported, step, _REPORT_PAIR)
    assert reported.count(_EXISTING_NODE) == 1
    # This proves the fixture is a reporting-only edit, not a changed selection.
    assert reported.replace(_REPORT_PAIR, "") == original
    check(reported)


def test_ci_guard_keeps_distinct_node_ids(ci_guard_workflow):
    original, check = ci_guard_workflow
    # A second real node in the other partition distinguishes retaining node IDs
    # from silently reducing every selector to its Python filename.
    other = "tests/test_installation.py::test_install_codex_updates_config"
    assert other not in original and _EXISTING_NODE in original
    check(_ci_append(original, _SHARED, " " + other))


@pytest.mark.parametrize(("step", "suffix"), [
    (_SHARED, " -k test_cross_host_resume"),
    (_SHARED, " --deselect=tests/test_cross_host_resume.py"),
    ("Run tests", " -k test_cross_host_resume"),
    ("Check native processes on macOS", " -k test_native_process"),
])
def test_ci_guard_rejects_selection_options(ci_guard_workflow, step, suffix):
    original, check = ci_guard_workflow
    with pytest.raises(AssertionError, match=".+"):
        check(_ci_append(original, step, suffix))


@pytest.mark.parametrize("suffix", [
    " --durations=0 --durations-min=1",
    _REPORT_PAIR + " --durations=20",
    " --durations 20 --durations-min=1",
    " --durations=20",
])
def test_ci_guard_rejects_malformed_reporting(ci_guard_workflow, suffix):
    original, check = ci_guard_workflow
    with pytest.raises(AssertionError, match=".+"):
        check(_ci_append(original, _SHARED, suffix))


@pytest.mark.parametrize("suffix", [" unexpected-operand", " && echo skipped", " '"])
def test_ci_guard_rejects_nonselector_tokens(ci_guard_workflow, suffix):
    original, check = ci_guard_workflow
    with pytest.raises(AssertionError, match=".+"):
        check(_ci_append(original, _SHARED, suffix))


def test_ci_guard_still_rejects_a_duplicate_selector(ci_guard_workflow):
    original, check = ci_guard_workflow
    with pytest.raises(AssertionError):
        check(_ci_append(original, _SHARED, " tests/test_cross_host_resume.py"))


def test_ci_guard_cannot_ignore_a_multiline_pytest_step(ci_guard_workflow):
    original, check = ci_guard_workflow
    changed = _ci_replace_run(original, "Check explicitly owned verification inputs",
        "        run: |\n          python -m pytest -q tests/test_owned_verification.py")
    with pytest.raises(AssertionError, match=".+"):
        check(changed)
