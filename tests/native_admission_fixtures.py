"""Fictional bound inputs for orchestration unit tests, never admission evidence.

The prospective wire requires producer.purpose=actual. Those values below are
explicit test statements, not claims that any command or qualification ran.
Actual execution boundaries must be replaced by named test doubles separately.
"""
from __future__ import annotations

import json

from test_trial_suite_native_runtime import byte_hash, save
from trial_suite_fixtures import digest, regular_entries, write_files


def attach_static_admission(fixture):
    root, suite = fixture["suite_root"], fixture["suite"]
    task, host = suite["tasks"]["normalize"], suite["host"]
    profile = json.loads((root / host["isolation"]["profile"]).read_text(encoding="utf-8"))
    write_files(root, {"qualification/fictional.txt": "Fictional static control; no qualification was executed.\n",
                       "qualification/package/module.py": "# Synthetic package bytes, never imported.\n"})
    package = regular_entries(root / "qualification/package", ("module.py",))
    runtime = {"executable_sha256": fixture["runtime"]["host_executable_sha256"],
               "version": host["version"], "platform": profile["platform"], "image_digest": profile["image_digest"],
               "public_environment": host["environment"], "dependencies": {"packaging": {
                   "version": (root / "export/deps.lock").read_text(encoding="utf-8").strip().split("==")[1],
                   "inventory_sha256": digest(package)}}}
    dependency_paths = tuple(sorted(set(task["dependencies"]["locks"] + task["scope"]["config"])))
    dependencies = regular_entries(root / task["source"]["export"], dependency_paths)
    save(root / "qualification/runtime.json", runtime)
    save(root / "qualification/dependencies.json", dependencies)
    save(root / "qualification/package-files.json", package)
    evidence_paths = ("qualification/fictional.txt", "qualification/runtime.json",
                      "qualification/dependencies.json", "qualification/package-files.json")

    def proof(kind, bindings, checks):
        return {"schema": "coding-trial-qualification/v1", "kind": kind, "bindings": bindings,
                "checks": [{"id": name, "status": "passed", "evidence": ["qualification/fictional.txt"]}
                           for name in checks],
                "producer": {"name": "fictional-static-test-control", "independent": True, "purpose": "actual"},
                "execution": {"argv": ["/never-run/fixture-proof"], "executable_sha256": "1" * 64,
                              "version": "fictional-version", "platform": profile["platform"],
                              "started_at": "2026-10-09T10:00:00Z", "ended_at": "2026-10-09T10:00:01Z",
                              "returncode": 0},
                "evidence": [{"path": name, "sha256": byte_hash(root / name)} for name in evidence_paths]}

    environment = proof("environment", {
        "image_digest": profile["image_digest"], "runtime_sha256": digest(runtime),
        "dependency_sha256": digest(dependencies), "preparation_sha256": None,
    }, ("runtime-identity", "locked-dependencies", "native-baseline"))
    environment_path = "qualification/environment.json"
    save(root / environment_path, environment)
    task["dependencies"]["environment"] = environment_path
    commands = [*task["checks"]["native"], task["checks"]["oracle"], task["checks"]["worker"]]
    resources = tuple(sorted({name for command in commands
                              for name in [command["entry"], *command["support"]] if name is not None}))
    admission = proof("task", {
        "source_sha256": task["source"]["baseline_sha256"], "preparation_sha256": None,
        "baseline_sha256": task["source"]["baseline_sha256"], "brief_sha256": byte_hash(root / task["brief"]),
        "clarifications_sha256": None,
        "check_ids_sha256": digest({key: task["checks"][key] for key in ("feature_ids", "preservation_ids")}),
        "checks_sha256": digest(regular_entries(root, resources)),
        "environment_receipt_sha256": byte_hash(root / environment_path),
        "task_policy_sha256": digest({key: value for key, value in task.items() if key != "admission"}),
    }, ("contract-review", "baseline-preservation", "intended-feature-failures", "known-correct",
        "alternative-legal", "negative-controls"))
    task["admission"] = "qualification/task.json"
    save(root / task["admission"], admission)
    # Historical isolation/loading remain absent. These shape controls cannot
    # replace the fresh real execution boundary required by either public API.
    save(root / "suite.json", suite)
    fixture["static_proofs"] = {"environment": environment, "task": admission}
