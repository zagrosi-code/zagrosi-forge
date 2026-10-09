"""Fault controls for qualification decisions, never proof of real isolation."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import posixpath
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import coding_trial_docker
import coding_trial_isolation
from coding_trial_inventory import copy_snapshot, inventory
from test_trial_suite_docker import docker_preflight
from test_trial_suite_isolation import attempt


def role_layout(shape, role):
    if role != "writer":
        checks = shape["suite"]["tasks"]["normalize"]["checks"]
        commands = checks["native"] if role == "native" else [checks["worker"]]
        resources = sorted({name for command in commands
                            for name in [command["entry"], *command["support"]] if name})
        # The fixture starts empty; copy only this role's admitted public files.
        public = Path(shape["roots"]["public"])
        public.rmdir()
        expected = inventory(shape["suite_root"], included=tuple(resources))
        copy_snapshot(shape["suite_root"], public, expected)
        for name in shape["suite"]["tasks"]["normalize"]["scope"]["generated"]:
            for key in ("workspace", "generated"):
                (Path(shape["roots"][key]) / name).mkdir(parents=True, exist_ok=True)
    return coding_trial_isolation.derive_layout(
        shape["suite"], shape["suite_root"], "normalize", "alpha", shape["roots"], role=role)


def failed_mechanism(monkeypatch, observations, *, inspect_access=None):
    """A fake lifecycle is deliberately unverified even for policy-positive cases."""
    def run_owned(profile, layout, argv, **kwargs):
        spec = json.loads(argv[-1])
        if argv[-2] == coding_trial_isolation.ACCESS_PROBE:
            if inspect_access is not None:
                inspect_access(spec)
            output = {
                "task_read": True, "task_write_policy": True, "scratch_write": True,
                "private_read": True, "private_write": True, "symlink_read": True,
                "relative_read": True, "relative_link_read": True,
                "readonly_roots": True, "public_read": True, "root_write": True,
                "docker_socket": True, "child_private": True, "network": True,
                "product_read_policy": True, "product_write_policy": True,
                **observations,
            }
        else:
            output = {"nonce": spec["nonce"], "pid": 17}
        return {"process": {"returncode": 124 if spec.get("wait") else 0,
                            "stdout": json.dumps(output), "stderr": "",
                            "timed_out": bool(spec.get("wait"))},
                "lifecycle": {"status": "unverified", "reason": "Synthetic control", "evidence": []}}
    monkeypatch.setattr(coding_trial_docker, "run_owned", run_owned)


def qualify(shape, layout):
    result = coding_trial_isolation.qualify_profile(
        shape["suite"], shape["suite_root"], layout, shape["evidence"], roots=shape["roots"])
    checks = {row["id"]: row["status"] for row in result["checks"]}
    assert checks["owned-container-removed"] == "failed", "A fake mechanism must remain unqualified"
    return checks


@pytest.mark.parametrize("role", ["native", "worker", "writer"])
@pytest.mark.parametrize("product_allowed", [False, True])
def test_product_check_uses_explicit_role_permissions(docker_preflight, monkeypatch, role, product_allowed):
    shape = docker_preflight
    layout = role_layout(shape, role)
    before = {key: inventory(Path(shape["roots"][key])) for key in ("workspace", "product", "public", "generated")}
    failed_mechanism(monkeypatch, {"product_read_policy": product_allowed})
    checks = qualify(shape, layout)
    assert checks["product-read-only"] == ("passed" if product_allowed else "failed")
    assert before == {key: inventory(Path(shape["roots"][key])) for key in before}


def test_relative_link_is_created_for_the_container_path(docker_preflight, monkeypatch):
    shape = docker_preflight
    observations = []
    def inspect_access(spec):
        link = Path(shape["roots"]["workspace"]) / spec["nonce"] / "relative-private-link"
        target = os.readlink(link)
        assert not posixpath.isabs(target)
        resolved = posixpath.normpath(posixpath.join("/workspace", spec["nonce"], target))
        assert resolved == spec["private"]
        observations.append(target)
    failed_mechanism(monkeypatch, {}, inspect_access=inspect_access)
    checks = qualify(shape, shape["layout"])
    assert observations, "No actual relative-link attempt was prepared"
    assert checks["relative-symlink-denied"] == "passed"


def test_relative_link_escape_cannot_pass_on_direct_and_absolute_denials(docker_preflight, monkeypatch):
    failed_mechanism(monkeypatch, {"relative_link_read": False})
    checks = qualify(docker_preflight, docker_preflight["layout"])
    assert checks["relative-symlink-denied"] == "failed"


def network_observation(interfaces, connection):
    return {"network_interfaces": [{"name": name, "flags": flags} for name, flags in interfaces],
            "network_connection": connection}


@pytest.mark.parametrize("mode,interfaces,connection,expected", [
    ("none", [("local", 0x9), ("unknown-tunnel", 0x80), ("another-link", 0x1002)], {"succeeded": False, "errno": 101}, True),
    ("none", [("local", 0x9), ("unknown-tunnel", 0x81)], {"succeeded": False, "errno": 101}, False),
    ("none", [("local", 0x9)], {"succeeded": True, "errno": None}, False),
    ("none", [("lo", 0x1)], {"succeeded": False, "errno": 101}, False),
    ("outbound-enabled", [("local", 0x9), ("unknown-link", 0x1)], None, True),
    ("outbound-enabled", [("local", 0x9), ("unknown-tunnel", 0x80)], None, False),
    ("outbound-enabled", [("local", 0x9)], None, False),
])
def test_network_policy_uses_interface_flags_and_actual_connection_result(mode, interfaces, connection, expected):
    facts = network_observation(interfaces, connection)
    before = deepcopy(facts)
    assert coding_trial_isolation.network_policy(mode, facts) is expected
    assert facts == before


@pytest.mark.parametrize("denial_errno", [101, 113, 13, 1])
def test_network_policy_accepts_explicit_linux_network_or_access_denial(denial_errno):
    # The probe runs in a Linux image, even when this controller runs on macOS.
    facts = network_observation([("local", 0x9)], {"succeeded": False, "errno": denial_errno})
    assert coding_trial_isolation.network_policy("none", facts) is True


@pytest.mark.parametrize("inconclusive_errno", [111, 115, 110, None, 9, 51])
def test_network_policy_rejects_refusal_unfinished_timeout_and_unrelated_errors(inconclusive_errno):
    facts = network_observation([("local", 0x9)], {"succeeded": False, "errno": inconclusive_errno})
    assert coding_trial_isolation.network_policy("none", facts) is False


@pytest.mark.parametrize("facts", [
    {"network": True},
    {"network_interfaces": [], "network_connection": {"succeeded": False, "errno": 101}},
    {"network_interfaces": None, "network_connection": {"succeeded": False, "errno": 101}},
    network_observation([("", 9)], {"succeeded": False, "errno": 101}),
    network_observation([("local", 9), ("local", 9)], {"succeeded": False, "errno": 101}),
    network_observation([("local", True)], {"succeeded": False, "errno": 101}),
    network_observation([("local", -1)], {"succeeded": False, "errno": 101}),
    network_observation([("local", "0x9")], {"succeeded": False, "errno": 101}),
    {"network_interfaces": [{"name": "local"}], "network_connection": {"succeeded": False, "errno": 101}},
    {"network_interfaces": [{"flags": 9}], "network_connection": {"succeeded": False, "errno": 101}},
    {"network_interfaces": [{"name": "local", "flags": 9}]},
    network_observation([("local", 9)], None),
    network_observation([("local", 9)], {"succeeded": 0, "errno": 101}),
    network_observation([("local", 9)], {"succeeded": False, "errno": True}),
    network_observation([("local", 9)], {"succeeded": False}),
    network_observation([("local", 9)], {"succeeded": False, "errno": 101, "unbound": True}),
])
def test_network_policy_rejects_missing_and_malformed_observations(facts):
    assert coding_trial_isolation.network_policy("none", facts) is False


@pytest.mark.parametrize("mode,facts", [
    ("unsupported", network_observation([("local", 9)], {"succeeded": False, "errno": 101})),
    ("outbound-enabled", {"network_interfaces": [{"name": "outside", "flags": 1}]}),
    ("outbound-enabled", network_observation([("outside", 1)], {"succeeded": False, "errno": 101})),
])
def test_network_policy_requires_the_declared_mode_and_its_observation_shape(mode, facts):
    assert coding_trial_isolation.network_policy(mode, facts) is False


def test_qualification_identity_covers_native_orchestration_dependencies(docker_preflight, monkeypatch):
    """Source closure is real; the deliberately failed mechanism proves no isolation."""
    from coding_trial_inventory import fingerprint

    shape = docker_preflight
    failed_mechanism(monkeypatch, {})
    result = coding_trial_isolation.qualify_profile(
        shape["suite"], shape["suite_root"], shape["layout"], shape["evidence"], roots=shape["roots"])
    saved = json.loads((shape["evidence"] / "qualification.json").read_text(encoding="utf-8"))
    adapter_sources = (
        "tools/coding_trial_isolation.py", "tools/coding_trial_docker.py", "tools/coding_trial_process.py",
        "tools/coding_trial_inventory.py", "tools/coding_trial_qualification.py", "tools/coding_trial_manifest.py",
        "tools/coding_trial_native.py", "tools/coding_trial_loading.py", "tools/coding_trial_reservation.py",
        "tools/coding_trial_loading_evidence.py", "scripts",
    )
    expected_adapter = fingerprint(inventory(ROOT, included=adapter_sources))
    expected_probe = fingerprint(inventory(ROOT, included=(
        "tools/coding_trial_isolation.py", "tools/coding_trial_docker.py")))
    for receipt in (result, saved):
        assert receipt["bindings"]["adapter_sha256"] == expected_adapter
        assert receipt["bindings"]["probe_sha256"] == expected_probe
        assert next(row for row in receipt["checks"] if row["id"] == "owned-container-removed")["status"] == "failed"
