"""Native mount grants only; these static controls never qualify an execution."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_isolation import derive_layout, execute_isolated
from test_trial_suite_isolation import tree_state
from test_trial_suite_native_runtime import make_native, prepare_auth, forbid_credential_reads


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Deriving native grants must not launch a process")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def layout(fixture, *, role="writer", auth_file=None):
    return derive_layout(fixture["suite"], fixture["suite_root"], "normalize", fixture["arm"],
                         fixture["roots"], role=role, auth_file=auth_file)


def state(fixture):
    # The synthetic external auth file is outside both trees and is never read.
    return tree_state(fixture["suite_root"]), tree_state(fixture["native"].parent)


@pytest.mark.parametrize("plain", [False, True])
@pytest.mark.parametrize("with_auth", [False, True])
def test_native_writer_has_exact_runtime_grants_without_private_controls(tmp_path, monkeypatch, plain, with_auth):
    fixture = make_native(tmp_path, plain=plain)
    auth = prepare_auth(fixture, tmp_path) if with_auth else None
    before, suite_before = state(fixture), deepcopy(fixture["suite"])
    if auth is not None:
        forbid_credential_reads(monkeypatch, auth)
    roots, native = fixture["roots"], fixture["native"]
    mounts = [{"source": roots["workspace"], "target": "/workspace", "read_only": False},
              {"source": roots["product"], "target": "/product", "read_only": True},
              {"source": str(native / "home"), "target": "/home/forge", "read_only": False},
              {"source": str(native / "codex"), "target": "/codex", "read_only": False},
              {"source": str(native / "plugins"), "target": "/codex/plugins", "read_only": True},
              {"source": str(native / "marketplace"), "target": "/marketplace", "read_only": True}]
    if auth is not None:
        mounts.append({"source": str(auth.resolve()), "target": "/codex/auth.json", "read_only": True})
    assert layout(fixture, auth_file=auth) == {
        "role": "writer", "task": "normalize", "arm": fixture["arm"], "user": "1000:1000", "network": "none",
        "mounts": sorted(mounts, key=lambda row: row["target"])}
    assert state(fixture) == before
    assert fixture["suite"] == suite_before


@pytest.mark.parametrize("change", ["missing-runtime", "malformed-runtime", "stale-cache"])
def test_native_layout_requires_current_valid_runtime_before_granting_mounts(tmp_path, change):
    fixture = make_native(tmp_path)
    if change == "missing-runtime":
        fixture["roots"]["native_runtime"] = None
    elif change == "malformed-runtime":
        (fixture["native"] / "runtime.json").write_text("not JSON", encoding="utf-8")
    else:
        entry = fixture["native"] / "plugins/cache/forge-evaluator/neutral-product/2.7.1" / fixture["runtime"]["selected_entry"]
        entry.write_text("Changed frozen installed entry", encoding="utf-8")
    before = state(fixture)
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        layout(fixture)
    assert state(fixture) == before


def test_runtime_mount_cannot_contain_a_declared_private_root(tmp_path):
    fixture = make_native(tmp_path)
    fixture["roots"]["private"].append(str(fixture["native"] / "home/future-private"))
    before = state(fixture)
    with pytest.raises(ValueError):
        layout(fixture)
    assert state(fixture) == before


@pytest.mark.parametrize("role", ["native", "worker"])
@pytest.mark.parametrize("exposure", ["native-runtime", "auth"])
def test_assessment_grants_never_admit_native_runtime_or_credentials(tmp_path, monkeypatch, role, exposure):
    fixture = make_native(tmp_path)
    roots = fixture["roots"]
    runtime_root = roots["native_runtime"]
    auth = prepare_auth(fixture, tmp_path) if exposure == "auth" else None
    if auth is not None:
        forbid_credential_reads(monkeypatch, auth)
    roots["native_runtime"] = None
    checks = fixture["suite"]["tasks"]["normalize"]["checks"]
    commands = checks["native"] if role == "native" else [checks["worker"]]
    resources = {name for command in commands for name in [command["entry"], *command["support"]] if name is not None}
    for name in resources:
        target = Path(roots["public"]) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fixture["suite_root"] / name, target)
    for name in ("workspace", "generated"):
        (Path(roots[name]) / ".cache").mkdir()
    # First prove that the same assessment projection is otherwise valid.
    assert layout(fixture, role=role) == {
        "role": role, "task": "normalize", "arm": fixture["arm"], "user": "1000:1000", "network": "none",
        "mounts": [{"source": roots["public"], "target": "/checks", "read_only": True},
                   {"source": roots["workspace"], "target": "/workspace", "read_only": True},
                   {"source": str(Path(roots["generated"]) / ".cache"), "target": "/workspace/.cache", "read_only": False}]}
    if exposure == "native-runtime":
        roots["native_runtime"] = runtime_root
    before = state(fixture)
    with pytest.raises(ValueError):
        layout(fixture, role=role, auth_file=auth)
    assert state(fixture) == before


def test_fixture_writer_cannot_receive_a_prepared_native_runtime(tmp_path):
    fixture = make_native(tmp_path)
    suite, roots = fixture["suite"], fixture["roots"]
    runtime_root = roots["native_runtime"]
    suite["purpose"] = "synthetic"
    suite["host"].update(adapter="fixture", executable=sys.executable, version=sys.version.split()[0],
                         model=None, effort=None, credentials=None, fixture_argv=["{python}", "-c", "pass"])
    for arm in suite["arms"].values():
        arm["loading"] = {"adapter": "none", "receipt": None}
    roots["native_runtime"] = None
    assert layout(fixture)["mounts"] == [
        {"source": roots["product"], "target": "/product", "read_only": True},
        {"source": roots["workspace"], "target": "/workspace", "read_only": False}]
    roots["native_runtime"] = runtime_root
    before = state(fixture)
    with pytest.raises(ValueError):
        layout(fixture)
    assert state(fixture) == before


def test_auth_alias_is_rejected_before_reading_suite_resources(tmp_path, monkeypatch):
    fixture = make_native(tmp_path)
    prepare_auth(fixture, tmp_path)
    brief = fixture["suite_root"] / "brief.md"
    forbid_credential_reads(monkeypatch, brief)
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        layout(fixture, auth_file=brief)


def test_execution_preserves_native_loading_error_before_creating_evidence(tmp_path):
    fixture = make_native(tmp_path)
    grants = layout(fixture)
    (fixture["native"] / "runtime.json").write_text("not JSON", encoding="utf-8")
    evidence = fixture["native"].parent / "private/execution"
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        execute_isolated(fixture["suite"], fixture["suite_root"], grants, ["never-run"],
                         roots=fixture["roots"], cwd="/workspace", env={}, prompt=None,
                         timeout=1, output_limit=1024, evidence_dir=evidence)
    assert not evidence.exists()
