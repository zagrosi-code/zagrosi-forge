"""Independent suite manifest contract regressions."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_manifest import read_suite, validate_suite
from trial_suite_fixtures import digest, link, make_suite, regular_entries, write_files


@pytest.fixture
def suite(tmp_path):
    from trial_suite_fixtures import make_suite
    return make_suite(tmp_path / "suite")


def replace(data, path, value):
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def contents(root):
    return {path.relative_to(root).as_posix():
            (stat.S_IMODE(path.lstat().st_mode), None if path.is_dir() else path.read_bytes())
            for path in root.rglob("*")}


def test_complete_synthetic_three_arm_manifest_is_neutral_and_side_effect_free(suite):
    path, data = suite
    before, before_files = deepcopy(data), contents(path.parent)
    validated = validate_suite(data, path.parent)
    read = read_suite(path)
    assert set(read["arms"]) == {"alpha", "bravo", "charlie"}
    assert read["arms"]["alpha"]["product"] is None
    assert read["arms"]["charlie"]["artifacts"] == [".third-work"]
    assert read["arms"]["charlie"]["workflow"] == "unmeasured"
    assert not (path.parent / "products/charlie/scripts/zagrosi_skills.py").exists()
    task = read["tasks"]["normalize"]
    assert task["scope"]["implementation"] == ["backend/app"]
    assert task["dependencies"]["allow_lock_changes"] is False
    assert set(task["dependencies"]["locks"]) <= set(task["scope"]["protected"])
    assert "from packaging.version import Version" in (path.parent / "export/backend/app/service.py").read_text()
    assert read["host"]["isolation"] is None and task["admission"] is None
    validated["arms"]["bravo"]["configuration"]["depth"] = "changed locally"
    assert data == before
    assert contents(path.parent) == before_files


def test_repeated_read_observes_manifest_and_resource_changes(suite):
    path, data = suite
    assert read_suite(path)["repeats"] == 1
    data["repeats"] = 2
    path.write_text(json.dumps(data))
    assert read_suite(path)["repeats"] == 2
    (path.parent / "export/settings.toml").write_text('style = "changed"\n')
    with pytest.raises(ValueError, match="suite-invalid"):
        read_suite(path)


def test_command_arguments_are_literal_and_remain_caller_owned(suite):
    path, data = suite
    literal = "$(must-not-run); a value with spaces"
    argv = data["tasks"]["normalize"]["checks"]["native"][0]["argv"]
    argv.append(literal)
    result = validate_suite(data, path.parent)
    selected = result["tasks"]["normalize"]["checks"]["native"][0]["argv"]
    assert selected[-1] == literal
    selected.append("local change")
    assert argv[-1] == literal


@pytest.mark.parametrize("raw", ["[]", "null", "{malformed"])
def test_manifest_must_be_a_valid_json_object(suite, raw):
    path, _ = suite
    path.write_text(raw)
    with pytest.raises(ValueError, match="suite-invalid"):
        read_suite(path)


@pytest.mark.parametrize("location", ["root", "nested"])
def test_duplicate_json_keys_are_rejected_instead_of_last_value_wins(suite, location):
    path, data = suite
    raw = json.dumps(data)
    if location == "root":
        raw = '{"id":"shadow",' + raw[1:]
    else:
        raw = raw.replace('"kind": "fixture"', '"kind": "git", "kind": "fixture"', 1)
    path.write_text(raw)
    with pytest.raises(ValueError, match="suite-invalid"):
        read_suite(path)


@pytest.mark.parametrize("keys,value", [
    (("schema",), "coding-trial-suite/v999"),
    (("unexpected",), True),
    (("repeats",), True),
    (("execution_seed",), True),
    (("repeats",), 0),
    (("tasks", "normalize", "cleanup_required"), 1),
    (("tasks", "normalize", "dependencies", "allow_lock_changes"), "false"),
    (("host", "capabilities", "subagents"), 1),
    (("host", "credentials"), {"token": "synthetic-invalid-value"}),
    (("host", "unexpected"), "value"),
    (("tasks", "normalize", "checks", "oracle", "timeout_seconds"), float("nan")),
    (("tasks", "normalize", "checks", "oracle", "timeout_seconds"), 0),
    (("tasks", "normalize", "checks", "oracle", "output_bytes"), 0),
    (("tasks", "normalize", "checks", "oracle", "argv"), ["python", 3]),
    (("tasks", "normalize", "checks", "oracle", "argv"), []),
    (("tasks", "normalize", "checks", "oracle", "entry"), None),
    (("tasks", "normalize", "checks", "worker", "argv"), ["{python}", "{entry}", "{receipt}"]),
    (("tasks", "normalize", "checks", "native", 0, "argv"), ["{python}", "{entry}"]),
    (("tasks", "normalize", "checks", "preservation_ids"), ["normalize-prefix"]),
    (("tasks", "normalize", "checks", "feature_ids"), ["normalize-prefix", "normalize-prefix"]),
])
def test_invalid_contracts_fail_without_mutating_inputs_or_resources(suite, keys, value):
    path, data = suite
    replace(data, keys, value)
    before = json.dumps(data, sort_keys=True)
    before_files = contents(path.parent)
    with pytest.raises(ValueError, match="suite-invalid"):
        validate_suite(data, path.parent)
    assert json.dumps(data, sort_keys=True) == before
    assert contents(path.parent) == before_files


@pytest.mark.parametrize("identifier", ["../escape", "a/b", "a\\b", "1leading", ""])
def test_task_and_arm_ids_cannot_be_paths(suite, identifier):
    path, data = suite
    for field, original in (("tasks", "normalize"), ("arms", "charlie")):
        changed = deepcopy(data)
        changed[field][identifier] = changed[field].pop(original)
        with pytest.raises(ValueError, match="suite-invalid"):
            validate_suite(changed, path.parent)


@pytest.mark.parametrize("reference", ["../private.md", "/absolute.md", "C:\\absolute.md", "./brief.md", "entries/../brief.md", ""])
def test_resource_paths_must_be_normalized_and_contained(suite, reference):
    path, data = suite
    data["tasks"]["normalize"]["brief"] = reference
    with pytest.raises(ValueError, match="suite-invalid"):
        validate_suite(data, path.parent)


def test_resource_link_cannot_escape_the_suite(suite, tmp_path):
    path, data = suite
    outside = tmp_path / "private.md"
    outside.write_text("separate evaluator bytes")
    link(path.parent / "escaped.md", str(outside))
    data["tasks"]["normalize"]["brief"] = "escaped.md"
    with pytest.raises(ValueError, match="suite-invalid"):
        validate_suite(data, path.parent)
    assert outside.read_text() == "separate evaluator bytes"


@pytest.mark.parametrize("mutation", ["baseline", "payload-metadata", "missing-worker", "missing-support", "cwd-escape", "workflow-overlap", "generated-baseline", "duplicate-command"])
def test_source_scope_and_command_boundaries_are_validated(suite, mutation):
    path, data = suite
    task = data["tasks"]["normalize"]
    if mutation == "baseline":
        task["source"]["baseline_sha256"] = "0" * 64
    elif mutation == "payload-metadata":
        (path.parent / "products/charlie/plugin.json").write_text('{"changed":true}\n')
    elif mutation == "missing-worker":
        (path.parent / "checks/worker.py").unlink()
    elif mutation == "missing-support":
        (path.parent / "checks/support.py").unlink()
    elif mutation == "cwd-escape":
        task["checks"]["native"][0]["cwd"] = "../checks"
    elif mutation == "workflow-overlap":
        data["arms"]["charlie"]["artifacts"] = ["backend"]
    elif mutation == "generated-baseline":
        task["scope"]["generated"] = ["deps.lock"]
    else:
        task["checks"]["native"].append(deepcopy(task["checks"]["native"][0]))
    with pytest.raises(ValueError, match="suite-invalid"):
        validate_suite(data, path.parent)


def test_supplied_broken_qualification_is_not_treated_as_absent(suite):
    path, data = suite
    proof = path.parent / "environment.json"
    proof.write_text('{"schema":"coding-trial-qualification/v1","kind":"environment"}\n')
    data["tasks"]["normalize"]["dependencies"]["environment"] = "environment.json"
    with pytest.raises(ValueError, match="suite-invalid"):
        validate_suite(data, path.parent)


@pytest.mark.parametrize("policy", ["protected-exception", "nested-role", "nested-generated"])
def test_task_scope_accepts_nested_roles_and_protected_exceptions(suite, policy):
    path, data = suite
    scope = data["tasks"]["normalize"]["scope"]
    if policy == "protected-exception":
        scope["protected"].append("backend/app/__init__.py")
    elif policy == "nested-role":
        scope["config"].append("backend/app/__init__.py")
    else:
        scope["generated"].append("backend/app/__pycache__")
    assert validate_suite(data, path.parent)["tasks"]["normalize"]["scope"] == scope


def test_resource_read_tolerates_access_time_updates(tmp_path):
    """Reading stable bytes may update filesystem atime without changing source."""
    import os
    from coding_trial_qualification import read_bytes

    path = tmp_path / "stable.txt"
    path.write_bytes(b"unchanged source\n")
    os.utime(path, ns=(1, path.stat().st_mtime_ns))
    assert read_bytes(tmp_path.resolve(), path.name) == b"unchanged source\n"


@pytest.mark.parametrize("role", ["native", "oracle", "worker"])
@pytest.mark.parametrize("field,maximum", [("timeout_seconds", 86400), ("output_bytes", 8 * 1024 * 1024)])
def test_check_limits_match_the_shared_executor(suite, role, field, maximum):
    path, data = suite
    checks = data["tasks"]["normalize"]["checks"]
    command = checks[role][0] if role == "native" else checks[role]
    command[field] = maximum
    assert validate_suite(data, path.parent)["id"] == data["id"]
    command[field] = maximum + 1
    with pytest.raises(ValueError, match="suite-invalid"):
        validate_suite(data, path.parent)


# Static host profiles and loading receipts (no native processes).

def byte_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return byte_hash(path)


def make_profile_shape(root, *, network="none", subagents=False):
    path, suite = make_suite(root)
    suite["purpose"] = "prospective"
    suite["arms"] = {key: suite["arms"][key] for key in ("alpha", "bravo")}
    image = "example.invalid/shape-fixture@sha256:" + "a" * 64
    profile = {
        "schema": "coding-trial-docker-profile/v1",
        "docker_executable": str((root / "never-run/docker").resolve()),
        "context": "fixture-context", "endpoint": "unix:///never-used/docker.sock",
        "server_id": "fixture-server", "server_version": "fixture-server-v1",
        "image_digest": image, "platform": "linux/amd64", "user": "1000:1000",
        "limits": {"memory_bytes": 67108864, "pids": 32, "cpus": 0.5},
        "network": network, "runtime_paths": ["/fixture/bin/codex", "/usr/bin/python3"],
        "network_checks": ["network-denied" if network == "none" else "network-mode-confirmed"],
    }
    profile_name = "proof/profile.json"
    profile_hash = save(root, profile_name, profile)
    suite["host"].update(
        adapter="codex", executable="/fixture/bin/codex", version="fixture-codex-v1",
        model="unavailable-fixture-model", effort="high", fixture_argv=None,
        capabilities={"tools": ["shell"], "subagents": subagents, "network": network},
        isolation={"adapter": "docker-v1", "profile": profile_name, "image_digest": image,
                   "probe_receipt": "proof/isolation.json"})

    def receipt(kind, bindings, checks, name):
        raw = f"proof/{name}-raw.txt"
        write_files(root, {raw: "Fictional shape-only transcript; no process ran or outcome was measured.\n"})
        return {
            "schema": "coding-trial-qualification/v1", "kind": kind, "bindings": bindings,
            "checks": [{"id": check, "status": "passed", "evidence": [raw]} for check in checks],
            "producer": {"name": "fictional-shape-fixture", "independent": False, "purpose": "actual"},
            "execution": {"argv": ["/never-run/qualification"], "executable_sha256": "1" * 64,
                          "version": "fixture-version", "platform": "linux/amd64",
                          "started_at": "2026-10-09T10:00:00Z", "ended_at": "2026-10-09T10:00:01Z",
                          "returncode": 0},
            "evidence": [{"path": raw, "sha256": byte_hash(root / raw)}],
        }

    isolation = receipt("isolation", {
        "docker_executable_sha256": "1" * 64, "docker_version": "fixture-version",
        **{key: profile[key] for key in ("context", "endpoint", "server_id", "server_version",
                                         "image_digest", "platform", "network")},
        # These current-execution identities cannot be recomputed by static
        # manifest validation. Their well-formed values are deliberately fictional.
        "adapter_sha256": "2" * 64, "probe_sha256": "3" * 64, "layout_sha256": "4" * 64,
        "profile_sha256": profile_hash, "limits_sha256": digest(profile["limits"]),
    }, ["task-read-write", "product-read-only", "private-read-denied", "private-write-denied",
        "relative-symlink-denied", "child-private-denied", "detached-child-stopped",
        "owned-container-removed", *profile["network_checks"]], "isolation")
    save(root, "proof/isolation.json", isolation)
    loading = {}
    for name, arm in suite["arms"].items():
        product = arm["product"]
        if product is not None:
            product.update(source_commit="5" * 40, source_tree="6" * 40)
        arm["loading"] = {"adapter": "codex-plain-v1" if product is None else "codex-plugin-v1",
                          "receipt": f"proof/loading-{name}.json"}
        loading[name] = receipt("loading", {
            "image_digest": image, "host_executable_sha256": "1" * 64,
            "host_version": suite["host"]["version"], "model": suite["host"]["model"],
            "effort": suite["host"]["effort"],
            "product_sha256": digest({}) if product is None else product["inventory_sha256"],
            "entry_sha256": byte_hash(root / arm["entry"]),
            "configuration_sha256": digest(arm["configuration"]),
            "capabilities_sha256": digest(suite["host"]["capabilities"]),
            "profile_sha256": profile_hash,
        }, ["native-discovery", "entry-routing", "required-tools", "task-read-write",
            "private-access-denied", "termination", *(["subagent-execution"] if subagents else [])],
            f"loading-{name}")
        loading[name]["execution"]["version"] = suite["host"]["version"]
        save(root, arm["loading"]["receipt"], loading[name])
    save(root, "suite.json", suite)
    return {"path": path, "suite": suite, "profile": profile, "isolation": isolation, "loading": loading}


@pytest.fixture
def profiles(tmp_path):
    return make_profile_shape(tmp_path / "suite")


def persist(shape):
    root, suite = shape["path"].parent, shape["suite"]
    save(root, suite["host"]["isolation"]["probe_receipt"], shape["isolation"])
    for name, receipt in shape["loading"].items():
        save(root, suite["arms"][name]["loading"]["receipt"], receipt)


def invalid(shape):
    with pytest.raises(ValueError, match="suite-invalid"):
        validate_suite(shape["suite"], shape["path"].parent)


@pytest.mark.parametrize("network", ["none", "outbound-enabled"])
@pytest.mark.parametrize("subagents", [False, True])
def test_static_profile_shapes_require_no_available_daemon_image_or_native_host(tmp_path, monkeypatch, network, subagents):
    shape = make_profile_shape(tmp_path / "suite", network=network, subagents=subagents)
    # On Windows this is a native drive-qualified host path, while the
    # prospective executable/runtime paths still belong to a Linux image.
    assert Path(shape["profile"]["docker_executable"]).is_absolute()
    before = deepcopy(shape["suite"])

    def no_process(*args, **kwargs):
        pytest.fail("Static receipt validation must not execute Docker, a host, or a qualification")

    monkeypatch.setattr(subprocess, "run", no_process)
    monkeypatch.setattr(subprocess, "Popen", no_process)
    result = validate_suite(shape["suite"], shape["path"].parent)
    assert read_suite(shape["path"]) == result
    assert result["host"]["executable"] == "/fixture/bin/codex"
    assert result["tasks"]["normalize"]["admission"] is None
    assert result["tasks"]["normalize"]["dependencies"]["environment"] is None
    assert shape["suite"] == before


@pytest.mark.parametrize("field", ["context", "endpoint", "server_id", "server_version", "image_digest",
                                    "platform", "network", "limits_sha256", "profile_sha256"])
def test_isolation_receipt_binds_static_profile_fields(profiles, field):
    bindings = profiles["isolation"]["bindings"]
    if field.endswith("sha256"):
        bindings[field] = "f" * 64
    elif field == "network":
        bindings[field] = "outbound-enabled"
    elif field == "image_digest":
        bindings[field] = "example.invalid/other@sha256:" + "b" * 64
    else:
        bindings[field] += "-changed"
    persist(profiles)
    invalid(profiles)


@pytest.mark.parametrize("arm", ["alpha", "bravo"])
@pytest.mark.parametrize("field", ["host_version", "model", "effort", "image_digest", "profile_sha256",
                                    "product_sha256", "entry_sha256", "configuration_sha256", "capabilities_sha256"])
def test_loading_receipt_binds_selected_host_and_arm(profiles, arm, field):
    bindings = profiles["loading"][arm]["bindings"]
    if field.endswith("sha256"):
        bindings[field] = "f" * 64
    elif field == "image_digest":
        bindings[field] = "example.invalid/other@sha256:" + "b" * 64
    else:
        bindings[field] += "-changed"
    persist(profiles)
    invalid(profiles)


@pytest.mark.parametrize("change", ["host", "capabilities", "entry", "configuration", "product", "profile-bytes"])
def test_current_loading_resources_cannot_reuse_an_old_receipt(profiles, change):
    root, suite = profiles["path"].parent, profiles["suite"]
    arm = suite["arms"]["bravo"]
    if change == "host":
        suite["host"]["version"] += "-changed"
    elif change == "capabilities":
        suite["host"]["capabilities"]["tools"].append("read-file")
    elif change == "entry":
        (root / arm["entry"]).write_text("A changed product entry.\n")
    elif change == "configuration":
        arm["configuration"]["depth"] = "deep"
    elif change == "profile-bytes":
        profile_path = root / suite["host"]["isolation"]["profile"]
        profile_path.write_text(json.dumps(profiles["profile"], separators=(",", ":")))
    else:
        payload = root / arm["product"]["payload"]
        (payload / "plugin.json").write_text('{"name":"bravo","changed":true}\n')
        arm["product"]["inventory_sha256"] = digest(regular_entries(payload, ("SKILL.md", "plugin.json")))
    invalid(profiles)


@pytest.mark.parametrize("kind", ["isolation", "loading"])
@pytest.mark.parametrize("change", ["missing-check", "extra-check", "duplicate-check", "failed-check",
                                    "synthetic-producer", "changed-raw", "empty-bound-raw", "candidate-evidence"])
def test_profile_receipts_require_exact_checks_and_bound_assessor_evidence(profiles, kind, change):
    receipt = profiles[kind] if kind == "isolation" else profiles[kind]["bravo"]
    root = profiles["path"].parent
    if change == "missing-check":
        receipt["checks"].pop()
    elif change in ("extra-check", "duplicate-check"):
        receipt["checks"].append(deepcopy(receipt["checks"][0]))
        if change == "extra-check":
            receipt["checks"][-1]["id"] = "unrequested-check"
    elif change == "failed-check":
        receipt["checks"][0]["status"] = "failed"
    elif change == "synthetic-producer":
        receipt["producer"]["purpose"] = "synthetic"
    elif change == "candidate-evidence":
        name = "export/AGENTS.md"
        receipt["evidence"] = [{"path": name, "sha256": byte_hash(root / name)}]
        for check in receipt["checks"]:
            check["evidence"] = [name]
    else:
        path = root / receipt["evidence"][0]["path"]
        path.write_bytes(b"Changed transcript" if change == "changed-raw" else b"")
        if change == "empty-bound-raw":
            receipt["evidence"][0]["sha256"] = byte_hash(path)
    persist(profiles)
    invalid(profiles)


@pytest.mark.parametrize("kind,field,value", [
    ("isolation", "docker_executable_sha256", True), ("isolation", "adapter_sha256", "short"),
    ("isolation", "probe_sha256", "A" * 64), ("isolation", "layout_sha256", []),
    ("loading", "host_executable_sha256", "short"),
])
def test_dynamic_execution_bindings_still_require_digest_types(profiles, kind, field, value):
    receipt = profiles[kind] if kind == "isolation" else profiles[kind]["bravo"]
    receipt["bindings"][field] = value
    persist(profiles)
    invalid(profiles)


@pytest.mark.parametrize("change", ["unknown", "boolean-memory", "zero-pids", "nan-cpus", "relative-runtime",
                                    "non-numeric-user", "wrong-network-check"])
def test_profile_schema_rejects_malformed_values_even_without_receipts(profiles, change):
    suite, profile = profiles["suite"], profiles["profile"]
    suite["host"]["isolation"]["probe_receipt"] = None
    for arm in suite["arms"].values():
        arm["loading"]["receipt"] = None
    if change == "unknown":
        profile["arbitrary_mounts"] = []
    elif change == "boolean-memory":
        profile["limits"]["memory_bytes"] = True
    elif change == "zero-pids":
        profile["limits"]["pids"] = 0
    elif change == "nan-cpus":
        profile["limits"]["cpus"] = float("nan")
    elif change == "relative-runtime":
        profile["runtime_paths"] = ["relative/codex"]
    elif change == "non-numeric-user":
        profile["user"] = "root"
    else:
        profile["network_checks"] = ["network-mode-confirmed"]
    save(profiles["path"].parent, suite["host"]["isolation"]["profile"], profile)
    invalid(profiles)


def test_declared_subagents_require_their_loading_check(tmp_path):
    shape = make_profile_shape(tmp_path / "suite", subagents=True)
    checks = shape["loading"]["bravo"]["checks"]
    checks[:] = [check for check in checks if check["id"] != "subagent-execution"]
    persist(shape)
    invalid(shape)
