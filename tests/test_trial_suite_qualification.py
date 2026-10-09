"""Independent qualification-shape controls; no host or qualification is executed.

Draft for tests/test_trial_suite_manifest.py. Receipt statements are synthetic
test data, never evidence that a real runtime, package or provider was qualified.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_manifest import read_suite, validate_suite
from trial_suite_fixtures import digest, link, make_suite, regular_entries, write_files


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return file_digest(path)


@pytest.fixture
def qualified_shape(tmp_path):
    """A self-consistent synthetic environment/task proof with actual fixture bytes."""
    path, suite = make_suite(tmp_path / "suite")
    root = path.parent
    task = suite["tasks"]["normalize"]
    baseline = task["source"]["baseline_sha256"]
    write_files(root, {
        "clarifications.md": "Preserve normal versions; the task is synthetic.\n",
        "qualification/preparation.txt": "Synthetic identity preparation; bytes unchanged.\n",
        "qualification/shape-only.txt":
            "Synthetic qualification-shape input. No qualification command was run.\n",
        "qualification/package-fixture/module.py": "SYNTHETIC_PACKAGE = True\n",
        "checks/native.py": "from native_support import MARKER\nassert MARKER == 'synthetic'\n",
        "checks/native_support.py": "MARKER = 'synthetic'\n",
    })
    native = task["checks"]["native"][0]
    native.update(entry="checks/native.py", support=["checks/native_support.py"],
                  argv=["{python}", "-B", "{entry}"])
    task["checks"]["worker"]["support"] = ["checks/native_support.py"]
    task["clarifications"] = "clarifications.md"
    task["source"]["preparation"] = {
        "description": "Identity preparation for a synthetic format control",
        "input_sha256": baseline, "output_sha256": baseline,
        "evidence": "qualification/preparation.txt",
    }
    # The runtime's resolved-package inventory describes only these synthetic
    # bytes. It is not an attestation about the machine's installed packaging.
    package = regular_entries(root / "qualification/package-fixture", ("module.py",))
    runtime = {
        "executable_sha256": file_digest(Path(sys.executable)),
        "version": "synthetic-runtime-v1", "platform": "synthetic-platform",
        "image_digest": None, "public_environment": suite["host"]["environment"],
        "dependencies": {"fixture-package": {
            "version": "synthetic-v1", "inventory_sha256": digest(package)}},
    }
    dependencies = regular_entries(root / "export", (
        "pyproject.toml", "deps.lock", "settings.toml", "AGENTS.md"))
    runtime_path = "qualification/objects/a17.json"
    dependency_path = "qualification/objects/b24.json"
    save_json(root, runtime_path, runtime)
    save_json(root, dependency_path, dependencies)
    save_json(root, "qualification/package-files.json", package)
    environment_path = "qualification/environment-proof.json"
    task_path = "qualification/task-proof.json"

    def proof(kind, bindings, check_ids):
        names = ("qualification/shape-only.txt", dependency_path, runtime_path,
                 "qualification/package-files.json", "qualification/preparation.txt")
        return {
            "schema": "coding-trial-qualification/v1", "kind": kind,
            "bindings": bindings,
            "checks": [{"id": check_id, "status": "passed",
                        "evidence": ["qualification/shape-only.txt"]}
                       for check_id in check_ids],
            "producer": {"name": "independent-synthetic-fixture", "independent": True,
                         "purpose": "synthetic"},
            "execution": {
                "argv": [sys.executable, "-c", "pass"],
                "executable_sha256": file_digest(Path(sys.executable)),
                "version": "synthetic-runtime-v1", "platform": "synthetic-platform",
                "started_at": "2026-10-09T10:00:00Z",
                "ended_at": "2026-10-09T10:00:01Z", "returncode": 0,
            },
            "evidence": [{"path": name, "sha256": file_digest(root / name)} for name in names],
        }

    environment = proof("environment", {
        "image_digest": None, "runtime_sha256": digest(runtime),
        "dependency_sha256": digest(dependencies),
        "preparation_sha256": digest(task["source"]["preparation"]),
    }, ("runtime-identity", "locked-dependencies", "native-baseline"))
    environment_sha256 = save_json(root, environment_path, environment)
    task["dependencies"]["environment"] = environment_path
    commands = [*task["checks"]["native"], task["checks"]["oracle"], task["checks"]["worker"]]
    check_resources = sorted({name for command in commands
                              for name in [command["entry"], *command["support"]] if name is not None})
    admission = proof("task", {
        "source_sha256": baseline, "preparation_sha256": digest(task["source"]["preparation"]),
        "baseline_sha256": baseline, "brief_sha256": file_digest(root / task["brief"]),
        "clarifications_sha256": file_digest(root / task["clarifications"]),
        "check_ids_sha256": digest({key: task["checks"][key]
                                    for key in ("feature_ids", "preservation_ids")}),
        "checks_sha256": digest(regular_entries(root, tuple(check_resources))),
        "environment_receipt_sha256": environment_sha256,
        "task_policy_sha256": digest({key: value for key, value in task.items() if key != "admission"}),
    }, ("contract-review", "baseline-preservation", "intended-feature-failures",
        "known-correct", "alternative-legal", "negative-controls"))
    save_json(root, task_path, admission)
    task["admission"] = task_path
    save_json(root, "suite.json", suite)
    return {"path": path, "suite": suite, "environment": environment, "task": admission,
            "runtime": runtime, "dependencies": dependencies, "runtime_path": runtime_path,
            "dependency_path": dependency_path}


def persist_proofs(shape):
    """Keep the receipt-byte dependency current when changing inner proof data."""
    root, suite = shape["path"].parent, shape["suite"]
    task = suite["tasks"]["normalize"]
    environment_hash = save_json(root, task["dependencies"]["environment"], shape["environment"])
    shape["task"]["bindings"]["environment_receipt_sha256"] = environment_hash
    save_json(root, task["admission"], shape["task"])


def replace_object(shape, key, value):
    """Update all byte bindings, so structural/content checks remain necessary."""
    name = shape[f"{key}_path"]
    file_hash = save_json(shape["path"].parent, name, value)
    for kind in ("environment", "task"):
        for item in shape[kind]["evidence"]:
            if item["path"] == name:
                item["sha256"] = file_hash
    binding = "runtime_sha256" if key == "runtime" else "dependency_sha256"
    shape["environment"]["bindings"][binding] = digest(value)
    persist_proofs(shape)


def assert_invalid(shape):
    with pytest.raises(ValueError, match="suite-invalid"):
        validate_suite(shape["suite"], shape["path"].parent)


def test_valid_nonnull_synthetic_proofs_remain_static_and_side_effect_free(qualified_shape, monkeypatch):
    shape = qualified_shape
    before = deepcopy(shape["suite"])
    root = shape["path"].parent
    before_files = {path.relative_to(root).as_posix(): file_digest(path)
                    for path in root.rglob("*") if path.is_file()}

    def no_process(*args, **kwargs):
        pytest.fail("Manifest validation must not launch a host or qualification process")

    monkeypatch.setattr(subprocess, "run", no_process)
    monkeypatch.setattr(subprocess, "Popen", no_process)
    validated = validate_suite(shape["suite"], shape["path"].parent)
    assert read_suite(shape["path"]) == validated
    assert validated["purpose"] == "synthetic"
    task = validated["tasks"]["normalize"]
    assert task["admission"] == "qualification/task-proof.json"
    assert task["dependencies"]["environment"] == "qualification/environment-proof.json"
    assert all(arm["loading"] == {"adapter": "none", "receipt": None}
               for arm in validated["arms"].values())
    validated["tasks"]["normalize"]["dependencies"]["locks"].clear()
    assert shape["suite"] == before
    assert {path.relative_to(root).as_posix(): file_digest(path)
            for path in root.rglob("*") if path.is_file()} == before_files


def test_environment_producer_can_be_nonindependent_while_task_review_is_independent(qualified_shape):
    shape = qualified_shape
    shape["environment"]["producer"]["independent"] = False
    persist_proofs(shape)
    validate_suite(shape["suite"], shape["path"].parent)


@pytest.mark.parametrize("key", ["runtime", "dependency"])
@pytest.mark.parametrize("mutation", ["missing", "ambiguous", "changed-bytes"])
def test_qualification_objects_are_selected_by_unique_verified_digest(qualified_shape, key, mutation):
    shape = qualified_shape
    name = shape[f"{key}_path"]
    if mutation == "changed-bytes":
        (shape["path"].parent / name).write_text("{}\n")
    else:
        evidence = shape["environment"]["evidence"]
        if mutation == "missing":
            evidence[:] = [item for item in evidence if item["path"] != name]
        else:
            original = shape["path"].parent / name
            alias = "qualification/same-object-different-bytes.json"
            value = json.loads(original.read_text())
            alias_path = shape["path"].parent / alias
            alias_path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
            alias_hash = file_digest(alias_path)
            evidence.append({"path": alias, "sha256": alias_hash})
        persist_proofs(shape)
    assert_invalid(shape)


def test_duplicate_json_keys_in_bound_runtime_evidence_are_rejected(qualified_shape):
    shape = qualified_shape
    name = shape["runtime_path"]
    path = shape["path"].parent / name
    path.write_text('{"version":"shadow",' + path.read_text().lstrip()[1:])
    for kind in ("environment", "task"):
        for item in shape[kind]["evidence"]:
            if item["path"] == name:
                item["sha256"] = file_digest(path)
    persist_proofs(shape)
    assert_invalid(shape)


@pytest.mark.parametrize("field,value", [
    ("unexpected", "field"), ("executable_sha256", True), ("version", 1),
    ("platform", []), ("image_digest", 1), ("public_environment", {"FLAG": False}),
    ("dependencies", []),
    ("dependencies", {"fixture-package": {"version": 1, "inventory_sha256": "a" * 64}}),
    ("dependencies", {"fixture-package": {"version": "one", "inventory_sha256": "short"}}),
])
def test_runtime_shape_is_checked_after_all_digest_bindings_match(qualified_shape, field, value):
    shape = qualified_shape
    runtime = deepcopy(shape["runtime"])
    runtime[field] = value
    replace_object(shape, "runtime", runtime)
    assert_invalid(shape)


def test_dependency_object_must_equal_current_lock_and_config_projection(qualified_shape):
    shape = qualified_shape
    dependencies = deepcopy(shape["dependencies"])
    del dependencies["deps.lock"]
    replace_object(shape, "dependency", dependencies)
    assert_invalid(shape)


@pytest.mark.parametrize("change", ["allow-lock-changes", "locks"])
def test_task_admission_binds_both_dependency_policy_fields(qualified_shape, change):
    shape = qualified_shape
    dependencies = shape["suite"]["tasks"]["normalize"]["dependencies"]
    if change == "allow-lock-changes":
        dependencies["allow_lock_changes"] = True
    else:
        # Both lock files remain scope.config: the projected bytes are identical,
        # so only the explicit admitted-policy binding catches this change.
        dependencies["locks"] = ["deps.lock"]
    assert_invalid(shape)


def assert_unadmitted_shape_valid(shape):
    """Separate stale-review rejection from ordinary task-shape rejection."""
    unadmitted = deepcopy(shape["suite"])
    unadmitted["tasks"]["normalize"]["admission"] = None
    validate_suite(unadmitted, shape["path"].parent)


@pytest.mark.parametrize("kind", ["native", "oracle", "worker"])
@pytest.mark.parametrize("field", ["argv", "env", "cwd", "timeout_seconds", "output_bytes", "support"])
def test_task_policy_binds_each_command_and_its_execution_settings(qualified_shape, kind, field):
    shape = qualified_shape
    checks = shape["suite"]["tasks"]["normalize"]["checks"]
    command = checks[kind][0] if kind == "native" else checks[kind]
    if field == "argv":
        command[field] = ["{python}", "-c", "pass"]
    elif field == "env":
        command[field] = {**command[field], "SYNTHETIC_CHECK_FLAG": "changed"}
    elif field == "cwd":
        command[field] = "checks" if kind == "oracle" else "backend"
    elif field == "support":
        command[field] = []
    else:
        command[field] *= 2
    assert_unadmitted_shape_valid(shape)
    assert_invalid(shape)


@pytest.mark.parametrize("change", ["implementation", "allowed-changes", "protected", "generated",
                                    "cleanup", "commits", "source-export", "source-metadata"])
def test_task_policy_binds_scope_delivery_rules_and_source_declaration(qualified_shape, change):
    shape = qualified_shape
    task = shape["suite"]["tasks"]["normalize"]
    if change == "implementation":
        task["scope"]["implementation"] = ["backend"]
    elif change == "allowed-changes":
        task["scope"]["allowed_changes"].append("AGENTS.md")
    elif change == "protected":
        task["scope"]["protected"].remove("AGENTS.md")
    elif change == "generated":
        task["scope"]["generated"] = [".different-cache"]
    elif change == "cleanup":
        task["cleanup_required"] = True
    elif change == "commits":
        task["local_commits"] = "forbid"
    elif change == "source-export":
        root = shape["path"].parent
        shutil.copytree(root / "export", root / "same-baseline")
        task["source"]["export"] = "same-baseline"
    else:
        # Synthetic metadata exercises static binding only: no claim that this
        # example.invalid remote or these illustrative objects actually exist.
        task["source"].update(kind="git", url="https://example.invalid/synthetic-task.git",
                              commit="1" * 40, tree="2" * 40)
    assert_unadmitted_shape_valid(shape)
    assert_invalid(shape)


def test_task_policy_excludes_only_the_receipt_location_to_avoid_a_content_cycle(qualified_shape):
    shape = qualified_shape
    root = shape["path"].parent
    original = root / shape["suite"]["tasks"]["normalize"]["admission"]
    relocated = "qualification/relocated-task-proof.json"
    (root / relocated).write_bytes(original.read_bytes())
    shape["suite"]["tasks"]["normalize"]["admission"] = relocated
    validate_suite(shape["suite"], root)


@pytest.mark.parametrize("change", ["baseline", "brief", "clarifications", "native-entry", "native-support",
                                    "oracle", "worker", "support", "check-ids", "preparation"])
def test_task_proof_binds_every_task_input(qualified_shape, change):
    shape = qualified_shape
    task = shape["suite"]["tasks"]["normalize"]
    resources = {"brief": "brief.md", "clarifications": "clarifications.md",
                 "native-entry": "checks/native.py", "native-support": "checks/native_support.py",
                 "oracle": "checks/oracle.py", "worker": "checks/worker.py", "support": "checks/support.py"}
    if change == "baseline":
        root = shape["path"].parent / "export"
        (root / "backend/app/service.py").write_text("def normalize(value): return value\n")
        updated = digest(regular_entries(root, ("backend/app/__init__.py", "backend/app/service.py",
                                               "specs/test_service.py", "pyproject.toml", "deps.lock",
                                               "settings.toml", "AGENTS.md")))
        task["source"]["baseline_sha256"] = updated
        task["source"]["preparation"]["output_sha256"] = updated
        # Keep the environment's transform reference current; the task's
        # source/baseline/preparation proof must still invalidate independently.
        shape["environment"]["bindings"]["preparation_sha256"] = digest(task["source"]["preparation"])
        persist_proofs(shape)
    elif change in resources:
        (shape["path"].parent / resources[change]).write_text("Changed task material.\n")
        assert_unadmitted_shape_valid(shape)
    elif change == "check-ids":
        task["checks"]["feature_ids"].append("new-unqualified-feature")
    else:
        task["source"]["preparation"]["description"] += " changed"
    assert_invalid(shape)


@pytest.mark.parametrize("kind", ["environment", "task"])
@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "failed", "empty-evidence", "unknown-evidence"])
def test_qualification_checks_require_exact_ids_passes_and_bound_evidence(qualified_shape, kind, change):
    shape = qualified_shape
    checks = shape[kind]["checks"]
    if change == "missing":
        checks.pop()
    elif change in ("extra", "duplicate"):
        checks.append(deepcopy(checks[0]))
        if change == "extra":
            checks[-1]["id"] = "unrequested-check"
    elif change == "failed":
        checks[0]["status"] = "failed"
    else:
        checks[0]["evidence"] = [] if change == "empty-evidence" else ["qualification/unlisted.txt"]
    persist_proofs(shape)
    assert_invalid(shape)


@pytest.mark.parametrize("change", ["nonzero", "boolean-return", "reversed-time", "not-independent", "empty-raw", "candidate-evidence"])
def test_task_receipt_requires_honest_execution_and_assessor_owned_evidence(qualified_shape, change):
    shape = qualified_shape
    receipt = shape["task"]
    if change == "nonzero":
        receipt["execution"]["returncode"] = 1
    elif change == "boolean-return":
        receipt["execution"]["returncode"] = False
    elif change == "reversed-time":
        receipt["execution"]["ended_at"] = "2026-10-09T09:00:00Z"
    elif change == "not-independent":
        receipt["producer"]["independent"] = False
    else:
        name = "qualification/empty.txt" if change == "empty-raw" else "export/AGENTS.md"
        if change == "empty-raw":
            (shape["path"].parent / name).write_bytes(b"")
        receipt["evidence"].append({"path": name, "sha256": file_digest(shape["path"].parent / name)})
        receipt["checks"][0]["evidence"] = [name]
    persist_proofs(shape)
    assert_invalid(shape)


def test_synthetic_loading_cannot_relabel_a_shape_receipt_as_native_proof(qualified_shape):
    shape = qualified_shape
    shape["suite"]["arms"]["bravo"]["loading"]["receipt"] = "qualification/task-proof.json"
    assert_invalid(shape)


@pytest.mark.parametrize("field,reference", [
    pytest.param("entry", "export/backend/app/service.py", id="export-entry"),
    pytest.param("support", "export/AGENTS.md", id="export-support"),
    pytest.param("entry", "products/bravo/SKILL.md", id="product-entry"),
    pytest.param("entry", "checks/worker.py", id="worker-entry"),
    pytest.param("support", "checks/native_support.py", id="native-support"),
    pytest.param("entry", "entries/alpha.md", id="arm-prompt"),
    pytest.param("entry", "brief.md", id="task-brief"),
    pytest.param("support", "clarifications.md", id="task-clarifications"),
])
def test_oracle_resources_cannot_also_be_public(qualified_shape, field, reference):
    shape = qualified_shape
    task = shape["suite"]["tasks"]["normalize"]
    task["admission"] = None  # Exercise privacy, not an unrelated stale policy digest.
    task["checks"]["oracle"][field] = [reference] if field == "support" else reference
    with pytest.raises(ValueError, match="suite-invalid"):
        validate_suite(shape["suite"], shape["path"].parent)


def test_public_resource_alias_cannot_resolve_to_private_oracle(qualified_shape):
    shape = qualified_shape
    root = shape["path"].parent
    task = shape["suite"]["tasks"]["normalize"]
    task["admission"] = None
    link(root / "checks/public-alias.py", "oracle.py")
    task["checks"]["worker"]["support"] = ["checks/public-alias.py"]
    with pytest.raises(ValueError, match="suite-invalid"):
        validate_suite(shape["suite"], root)


@pytest.mark.parametrize("exposure", ["receipt-arm-prompt", "evidence-brief", "evidence-worker-support"])
def test_qualification_resources_cannot_be_public_files(qualified_shape, exposure):
    shape = qualified_shape
    suite = shape["suite"]
    task = suite["tasks"]["normalize"]
    task["admission"] = None
    if exposure == "receipt-arm-prompt":
        suite["arms"]["alpha"]["entry"] = task["dependencies"]["environment"]
    elif exposure == "evidence-brief":
        task["brief"] = shape["runtime_path"]
    else:
        task["checks"]["worker"]["support"] = [shape["runtime_path"]]
    with pytest.raises(ValueError, match="suite-invalid"):
        validate_suite(suite, shape["path"].parent)


@pytest.mark.parametrize("shared_public_helper", [False, True])
def test_private_resources_allow_separate_and_shared_public_helpers(qualified_shape, shared_public_helper):
    shape = qualified_shape
    suite = shape["suite"]
    task = suite["tasks"]["normalize"]
    task["admission"] = None
    task["checks"]["worker"]["support"] = (
        task["checks"]["native"][0]["support"][:] if shared_public_helper else [])
    assert validate_suite(suite, shape["path"].parent) == suite
