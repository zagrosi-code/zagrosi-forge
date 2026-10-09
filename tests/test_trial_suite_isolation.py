"""Independent process, fixture and pure-layout contracts; no native smoke claims."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import coding_trial_process as process
from coding_trial_isolation import derive_layout
from coding_trial_runner import run_suite_writer
from trial_suite_fixtures import digest, link, make_suite, write_files


def clean_environment(**values):
    # Windows may require this explicit nonsecret host setting to start Python.
    return ({"SYSTEMROOT": os.environ["SYSTEMROOT"]} if "SYSTEMROOT" in os.environ else {}) | values


def tree_state(root):
    result = {}
    for path in root.rglob("*"):
        value = os.readlink(path) if path.is_symlink() else None if path.is_dir() else path.read_bytes()
        result[path.relative_to(root).as_posix()] = (stat.S_IMODE(path.lstat().st_mode), value)
    return result


def forbid_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Read-only layout or failed preflight must not start a process")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def attempt(tmp_path):
    path, suite = make_suite(tmp_path / "assessor")
    base = tmp_path / "attempt"
    base.mkdir()
    shutil.copytree(path.parent / "export", base / "workspace")
    for name in ("product", "public", "generated"):
        (base / name).mkdir()
    private = tmp_path / "private"
    private.mkdir()
    roots = {key: str((base / key).resolve()) for key in ("workspace", "product", "public", "generated")}
    roots.update(native_runtime=None, private=[str(path.parent.resolve()), str(private.resolve())])
    return {"base": tmp_path, "suite_root": path.parent, "suite": suite,
            "roots": roots, "evidence": private / "writer"}


def with_profile(attempt):
    root, suite = attempt["suite_root"], attempt["suite"]
    image = "example.invalid/layout-fixture@sha256:" + "a" * 64
    profile = {"schema": "coding-trial-docker-profile/v1",
               "docker_executable": str((root / "never-run/docker").resolve()),
               "context": "fixture", "endpoint": "unix:///never-used/docker.sock",
               "server_id": "fixture", "server_version": "fixture-v1", "image_digest": image,
               "platform": "linux/amd64", "user": "1000:1000",
               "limits": {"memory_bytes": 67108864, "pids": 32, "cpus": 0.5},
               "network": "none", "runtime_paths": ["/usr/bin/python3"], "network_checks": ["network-denied"]}
    (root / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    suite["host"]["isolation"] = {"adapter": "docker-v1", "profile": "profile.json",
                                  "image_digest": image, "probe_receipt": None}
    return attempt


def prepare_assessment(attempt, role):
    with_profile(attempt)
    root, suite, roots = attempt["suite_root"], attempt["suite"], attempt["roots"]
    checks = suite["tasks"]["normalize"]["checks"]
    write_files(root, {"checks/native.py": "# Public native entry.\n",
                       "checks/native-second.py": "# Second public native entry.\n",
                       "checks/public_helper.py": "VALUE = 1\n"})
    native = checks["native"][0]
    native.update(entry="checks/native.py", support=["checks/public_helper.py"], argv=["{python}", "{entry}"])
    second = deepcopy(native)
    second.update(id="native-second", entry="checks/native-second.py")
    checks["native"].append(second)
    checks["worker"]["support"] = ["checks/public_helper.py"]
    commands = checks["native"] if role == "native" else [checks["worker"]]
    resources = sorted({name for command in commands for name in [command["entry"], *command["support"]]})
    for name in resources:
        target = Path(roots["public"]) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
    suite["tasks"]["normalize"]["scope"]["generated"] = [
        ".cache", ".cache/nested", "backend/build", "backend/build/deep"]
    for name in (".cache", "backend/build"):
        for key in ("generated", "workspace"):
            (Path(roots[key]) / name).mkdir(parents=True)
    return attempt


def test_process_wrapper_forwards_explicit_policy_without_replacing_it(tmp_path, monkeypatch):
    calls, sentinel = [], {"untouched": "executor result"}
    def capture(argv, workspace, **options):
        calls.append((argv, workspace, options))
        return sentinel
    monkeypatch.setattr(process, "_execute", capture)
    env = {"PYTHONPATH": "backend", "EXPLICIT": "yes"}
    argv = [sys.executable, "-c", "pass"]
    assert process.execute(argv, tmp_path, prompt="literal", timeout=1.25, env=env,
                           output_limit=321, inherit_env=False) is sentinel
    assert calls == [(argv, tmp_path, {"prompt": "literal", "timeout": 1.25, "env": env,
                                      "output_limit": 321, "inherit_env": False})]
    assert env == {"PYTHONPATH": "backend", "EXPLICIT": "yes"}


def test_explicit_clean_environment_allows_non_src_imports(tmp_path, monkeypatch):
    write_files(tmp_path, {"backend/example.py": "VALUE = 'backend import'\n"})
    monkeypatch.setenv("FORGE_AMBIENT_SENTINEL", "must not leak")
    monkeypatch.setenv("PYTHONOPTIMIZE", "2")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "ambient")
    script = ("import example,json,os; print(json.dumps([example.VALUE,"
              "os.getenv('FORGE_AMBIENT_SENTINEL'),os.getenv('PYTHONOPTIMIZE'),"
              "os.getenv('PYTHONDONTWRITEBYTECODE')]))")
    result = process.execute([sys.executable, "-c", script], tmp_path,
                             env=clean_environment(PYTHONPATH="backend"), inherit_env=False)
    assert result["returncode"] == 0
    assert json.loads(result["stdout"]) == ["backend import", None, None, None]


def test_explicit_output_bound_applies_to_both_streams(tmp_path):
    script = "import sys; sys.stdout.write('a'*512+'END'); sys.stderr.write('b'*512+'ERR')"
    result = process.execute([sys.executable, "-c", script], tmp_path, env=clean_environment(),
                             inherit_env=False, output_limit=64)
    assert result["returncode"] == 0
    assert result["stdout"] == "a" * 61 + "END"
    assert result["stderr"] == "b" * 61 + "ERR"
    assert result["stdout_truncated"] and result["stderr_truncated"]


def test_fixture_writer_uses_literal_arguments_candidate_cwd_and_clean_environment(attempt, monkeypatch):
    suite, roots = attempt["suite"], attempt["roots"]
    monkeypatch.setenv("FORGE_AMBIENT_SENTINEL", "must not leak")
    suite["host"]["environment"] = clean_environment(PYTHONPATH="backend", FIXTURE_MARKER="declared")
    script = ("from app.service import normalize; from pathlib import Path; import json,os,sys; "
              "Path('fixture-ran.txt').write_text('ok'); print(json.dumps({"
              "'cwd':str(Path.cwd()),'args':sys.argv[1:],'value':normalize('1.0'),"
              "'ambient':os.getenv('FORGE_AMBIENT_SENTINEL'),'marker':os.getenv('FIXTURE_MARKER'),"
              "'optimize':os.getenv('PYTHONOPTIMIZE'),'dontwrite':os.getenv('PYTHONDONTWRITEBYTECODE')}))")
    literal = "$(must-not-run); prefix:{workspace}"
    suite["host"]["fixture_argv"] = ["{python}", "-c", script, "{workspace}", "{product}", literal]
    before = deepcopy(suite)
    result = run_suite_writer(suite, attempt["suite_root"], "normalize", "alpha", roots, attempt["evidence"])
    assert result["schema"] == "coding-trial-writer/v1"
    assert result["suite_sha256"] == digest(before)
    assert result["task"] == "normalize" and result["arm"] == "alpha"
    assert result["process"]["returncode"] == 0
    assert json.loads(result["process"]["stdout"]) == {
        "cwd": roots["workspace"], "args": [roots["workspace"], roots["product"], literal],
        "value": "1.0", "ambient": None, "marker": "declared", "optimize": "0", "dontwrite": "1"}
    assert result["command"] == [sys.executable, "-c", script, roots["workspace"], roots["product"], literal]
    assert result["environment"] == suite["host"]["environment"] | {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONOPTIMIZE": "0"}
    assert result["telemetry"] is None
    isolation = result["isolation"]
    assert isolation["adapter"] is None and isolation["status"] == "unmeasured"
    assert isolation["qualification"] is None and isolation["container_id"] is None
    assert isolation["lifecycle"]["status"] == "unverified"
    assert isolation["lifecycle"]["reason"] and isolation["lifecycle"]["evidence"] == []
    assert (Path(roots["workspace"]) / "fixture-ran.txt").read_text() == "ok"
    assert suite == before


def test_fixture_writer_forwards_the_fixed_budget_and_preserves_process_result(attempt, monkeypatch):
    original, calls = process._execute, []
    def capture(argv, workspace, **options):
        # Run a harmless short process to obtain the real result schema, while
        # observing the writer's declared budget without waiting 900 seconds.
        result = original([sys.executable, "-c", "pass"], workspace, timeout=5,
                          env=clean_environment(), inherit_env=False, output_limit=64)
        calls.append((argv, workspace, deepcopy(options), deepcopy(result)))
        return result
    monkeypatch.setattr(process, "_execute", capture)
    result = run_suite_writer(attempt["suite"], attempt["suite_root"], "normalize", "alpha",
                              attempt["roots"], attempt["evidence"])
    assert len(calls) == 1
    assert calls[0][2]["timeout"] == 900 and calls[0][2]["output_limit"] == 8388608
    assert calls[0][2]["inherit_env"] is False
    assert result["budget"] == {"timeout_seconds": 900, "output_bytes": 8388608}
    assert result["process"] == calls[0][3]


@pytest.mark.parametrize("collision", ["file", "symlink"])
def test_writer_does_not_overwrite_process_created_evidence(attempt, collision):
    protected = attempt["base"] / "user.txt"
    protected.write_text("existing user bytes", encoding="utf-8")
    if collision == "symlink":
        # Check platform support before putting the same operation in the child.
        probe = attempt["base"] / "link-probe"
        link(probe, str(protected))
        probe.unlink()
    record_path = attempt["evidence"] / "writer.json"
    action = (f"p.symlink_to({str(protected)!r})" if collision == "symlink"
              else "p.write_text('process-owned bytes')")
    attempt["suite"]["host"]["fixture_argv"] = [
        "{python}", "-c", f"from pathlib import Path; p=Path({str(record_path)!r}); {action}"]
    try:
        with pytest.raises(ValueError, match="^input-exists:"):
            run_suite_writer(attempt["suite"], attempt["suite_root"], "normalize", "alpha",
                             attempt["roots"], attempt["evidence"])
    finally:
        assert protected.read_text(encoding="utf-8") == "existing user bytes"
        if collision == "file":
            assert record_path.read_text(encoding="utf-8") == "process-owned bytes"


def test_writer_rejects_replaced_evidence_directory(attempt):
    user_directory = attempt["base"] / "user-directory"
    user_directory.mkdir()
    probe = attempt["base"] / "directory-link-probe"
    link(probe, str(user_directory), directory=True)
    probe.unlink()
    script = (f"from pathlib import Path; p=Path({str(attempt['evidence'])!r}); "
              f"p.rmdir(); p.symlink_to({str(user_directory)!r}, target_is_directory=True)")
    attempt["suite"]["host"]["fixture_argv"] = ["{python}", "-c", script]
    try:
        with pytest.raises(ValueError):
            run_suite_writer(attempt["suite"], attempt["suite_root"], "normalize", "alpha",
                             attempt["roots"], attempt["evidence"])
    finally:
        assert list(user_directory.iterdir()) == []


def test_prompt_expansion_keeps_placeholder_text_inside_runtime_paths(attempt):
    old_workspace = Path(attempt["roots"]["workspace"])
    workspace = old_workspace.with_name("workspace-{product}")
    old_workspace.rename(workspace)
    attempt["roots"]["workspace"] = str(workspace)
    attempt["suite"]["host"]["fixture_argv"] = [
        "{python}", "-c", "import json,sys; print(json.dumps(sys.stdin.read()))"]
    result = run_suite_writer(attempt["suite"], attempt["suite_root"], "normalize", "alpha",
                             attempt["roots"], attempt["evidence"])
    brief = (attempt["suite_root"] / "brief.md").read_text(encoding="utf-8")
    assert result["process"]["returncode"] == 0
    assert json.loads(result["process"]["stdout"]) == f"Use your normal workflow in {workspace}.\n\n\n{brief}"


@pytest.mark.parametrize("failure", ["existing-evidence", "unknown-task", "unknown-arm"])
def test_writer_preflight_rejects_before_mutation(attempt, monkeypatch, failure):
    task, arm, code = "normalize", "alpha", "suite-invalid"
    if failure == "existing-evidence":
        write_files(attempt["evidence"], {"user.txt": "preserve me"})
        code = "input-exists"
    elif failure == "unknown-task":
        task = "unknown"
    else:
        arm = "unknown"
    before = tree_state(attempt["base"])
    forbid_processes(monkeypatch)
    with pytest.raises(ValueError, match=f"^{code}:"):
        run_suite_writer(attempt["suite"], attempt["suite_root"], task, arm, attempt["roots"], attempt["evidence"])
    assert tree_state(attempt["base"]) == before


def test_missing_native_gates_never_invoke_a_provider(attempt, monkeypatch):
    suite = attempt["suite"]
    suite["purpose"] = "prospective"
    suite["arms"] = {"alpha": suite["arms"]["alpha"]}
    suite["arms"]["alpha"]["loading"] = {"adapter": "codex-plain-v1", "receipt": None}
    suite["host"].update(adapter="codex", executable="/fixture/bin/codex", version="fixture-v1",
                          model="unavailable-fixture-model", effort="high", fixture_argv=None)
    forbid_processes(monkeypatch)
    before = tree_state(attempt["base"])
    with pytest.raises(ValueError, match=r"^(unsupported-profile|environment-unqualified|task-unqualified|isolation-unqualified|loading-unqualified):"):
        run_suite_writer(suite, attempt["suite_root"], "normalize", "alpha", attempt["roots"], attempt["evidence"])
    assert tree_state(attempt["base"]) == before


def test_pure_writer_layout_grants_only_workspace_and_selected_product(attempt, monkeypatch):
    with_profile(attempt)
    forbid_processes(monkeypatch)
    before = tree_state(attempt["base"])
    result = derive_layout(attempt["suite"], attempt["suite_root"], "normalize", "alpha", attempt["roots"], role="writer")
    assert result == {"role": "writer", "task": "normalize", "arm": "alpha", "user": "1000:1000", "network": "none",
                      "mounts": [{"source": attempt["roots"]["product"], "target": "/product", "read_only": True},
                                 {"source": attempt["roots"]["workspace"], "target": "/workspace", "read_only": False}]}
    assert tree_state(attempt["base"]) == before


@pytest.mark.parametrize("role", ["native", "worker"])
def test_assessment_layout_uses_exact_public_projection_and_outermost_scratch(attempt, monkeypatch, role):
    prepare_assessment(attempt, role)
    roots = attempt["roots"]
    forbid_processes(monkeypatch)
    before = tree_state(attempt["base"])
    layout = derive_layout(attempt["suite"], attempt["suite_root"], "normalize", "alpha", roots, role=role)
    assert layout == {"role": role, "task": "normalize", "arm": "alpha", "user": "1000:1000", "network": "none",
                      "mounts": [{"source": roots["public"], "target": "/checks", "read_only": True},
                                 {"source": roots["workspace"], "target": "/workspace", "read_only": True},
                                 {"source": str(Path(roots["generated"]) / ".cache"), "target": "/workspace/.cache", "read_only": False},
                                 {"source": str(Path(roots["generated"]) / "backend/build"), "target": "/workspace/backend/build", "read_only": False}]}
    assert tree_state(attempt["base"]) == before


@pytest.mark.parametrize("change", ["private-descendant", "private-ancestor", "root-alias", "public-overlap"])
def test_layout_rejects_private_ancestors_aliases_and_overlapping_public_roots(attempt, monkeypatch, change):
    with_profile(attempt)
    roots = attempt["roots"]
    if change == "private-descendant":
        roots["private"].append(str(Path(roots["workspace"]) / "future-evidence"))
    elif change == "private-ancestor":
        roots["private"].append(str(Path(roots["workspace"]).parent))
    elif change == "public-overlap":
        roots["product"] = roots["workspace"]
    else:
        alias = attempt["base"] / "private-alias"
        link(alias, roots["private"][0], directory=True)
        roots["workspace"] = str(alias)
    forbid_processes(monkeypatch)
    before = tree_state(attempt["base"])
    with pytest.raises(ValueError):
        derive_layout(attempt["suite"], attempt["suite_root"], "normalize", "alpha", roots, role="writer")
    assert tree_state(attempt["base"]) == before


@pytest.mark.parametrize("change", ["missing-public", "changed-public", "extra-private", "missing-scratch", "missing-mountpoint"])
def test_assessment_layout_rejects_incomplete_or_excess_grants_without_repair(attempt, monkeypatch, change):
    prepare_assessment(attempt, "worker")
    roots = attempt["roots"]
    public = Path(roots["public"])
    if change == "missing-public":
        (public / "checks/worker.py").unlink()
    elif change == "changed-public":
        (public / "checks/public_helper.py").write_text("VALUE = 2\n")
    elif change == "extra-private":
        shutil.copy2(attempt["suite_root"] / "checks/oracle.py", public / "checks/oracle.py")
    else:
        key = "generated" if change == "missing-scratch" else "workspace"
        (Path(roots[key]) / ".cache").rmdir()
    forbid_processes(monkeypatch)
    before = tree_state(attempt["base"])
    with pytest.raises(ValueError):
        derive_layout(attempt["suite"], attempt["suite_root"], "normalize", "alpha", roots, role="worker")
    assert tree_state(attempt["base"]) == before


def test_layout_requires_an_explicit_profile_without_mutating_fixture_roots(attempt, monkeypatch):
    forbid_processes(monkeypatch)
    before = tree_state(attempt["base"])
    with pytest.raises(ValueError, match="^unsupported-profile:"):
        derive_layout(attempt["suite"], attempt["suite_root"], "normalize", "alpha", attempt["roots"], role="writer")
    assert tree_state(attempt["base"]) == before
