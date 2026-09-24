"""Forge sources."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import os
import re

from . import CLI_PATH, verify_sources
from . import detached_contract as _detached_contract
from . import handoff_wire as _handoff_wire
from . import models as _models
from . import secure_io as _secure_io
from . import storage as _storage

# BEGIN TEST MANIFEST
TEST_MANIFEST = {
    "detached_test_support.py": "5deabb6b1dadbc155d450981e2836e730574f41f83e3feb53d7d462681467295",
    "fault_injection_support.py": "c7b34e4cf48b7b86d9f79b937e34eddbb26f9c4e16598a67960b3a2eb6659083",
    "forge_test_helpers.py": "6e0acf3a98f73ed47bf61262a0f261c754d4a44809de99d88576fa00591f1eea",
    "runtime_support.py": "195586d290650a6add408c880164538450bdfbc346fc8971470e622d211446a1",
    "test_adapter_loading.py": "d8c4086464c3cd401caa2d6887b6f4ebbb44d863620f02086507d90da60a8b7c",
    "test_benchmark_forge.py": "1436b31c5fc07824a903fb4a2f5bccf565059dbffa67151da7819b932c2383eb",
    "test_cache_publication.py": "218df403a40b42fe170ee816d0dd5de2df7f58106ac79e7d4dd55983f0698aff",
    "test_capabilities.py": "e579ca105e8dd4c08f476e407f4fe6ebaccd4df8ba7c2b2126ed4f5489e63b88",
    "test_coding_trials.py": "a98905cb273a0d8a1db79d43fdd128149a272c717107c45094cbc6ea9ab2bddb",
    "test_compact_evaluations.py": "0b6c8c448711a03e7a6586fece8c99f18fd4ff902c6399af6977511f7346f9d1",
    "test_compact_helpers.py": "cd01296d153b48d035cc31b4a95b9ca5536f963abe8e9e8cdbdd5438e214d1a8",
    "test_compact_plan.py": "bcdc03a4016c7ba2aaee540c7fc1aa3338c3fd9dea2f825d5efa274e5e7780e9",
    "test_completion_contracts.py": "0b8b071616684f6ff5191d62936d4f8d656517c8c67059f2b2b42c0fad2d92cc",
    "test_completion_records.py": "d468980a810229b5207cd0460f2d9ca0ecce81ada6af74b67b4872d5b6aec24f",
    "test_context_packets.py": "a2a81f174ebe35875dee36b661bc7c80567ae4cd5c386e99c50276e20866bdf8",
    "test_controlled_trials.py": "1979662c0f01b2141688fb07a0ebebad2a4a6f71f55d38f3ffe6c33e7549da03",
    "test_detached_authorities.py": "a8175a97dc7a6d201014364ef1a5ec4e821a48566e611e1831010d70c45d5233",
    "test_detached_dependencies.py": "4d69af9b83ead7a8f78a94ce43d4ccf275664d1b1958e54dd7d32cd226767dd2",
    "test_detached_locks.py": "dcbdc69d937f92b0ccf98661ec5c59bc5591221a676263aecb8fa4a8c2d83e2a",
    "test_detached_recording.py": "9801dc1698bb642a9b0dda4f7e18ff699a490f9de83b0f2c430a2e699cb6915e",
    "test_detached_recovery.py": "ec67066cbf4f0c505aa5573d2848bd0f332fc11a8ad0a82359dab36b5f09b9c0",
    "test_detached_resources.py": "87b343b4576d73408a641313eb881ce25ec76a2e22185121d9943abaddd617b7",
    "test_detached_setup.py": "c60606369d002b64c9b4c8cbc9c904f97eabed46c75a4ed39f8410c5d4814ba2",
    "test_evaluations.py": "117143ac576128b27aec09a5b7939884763dbffca6e8e15787e10f66d1d8a775",
    "test_evidence_discovery.py": "1e2671eacebdbadda57d9f87a4570d7e1eb633574b8a01c78690d75d7bbcf0ce",
    "test_gate_reports.py": "4986d356f1261f95771993ea10a82956895f11313b595f302766d04868317b4f",
    "test_handoff_authority.py": "f8915d3bccbb20751c48ae3c95bd80bea29389691a76fa054c4507bfd519b09f",
    "test_handoff_failures.py": "228106db7ad96d04057647fffce839721b449e8a2884626ba55eea7264beb6f0",
    "test_handoff_readiness.py": "b1eb9f207136d7d397887b26a6a2fa204493f89ec3cf56e641e02412ac7b5925",
    "test_handoff_transport.py": "104517f15aa1c085e9da2fd45bb104ea1f021f77b851cb5e8fb7d592948e8cae",
    "test_handoff_wire.py": "4c2fb34168a5dae808b37dfd9d75840da93dc9ee93bc73200db80b90c7e27b72",
    "test_implementation_drift.py": "12f4cb0977c8557718015851ff1ae1a8bd7d305957d8b8e205942e453ba1fe7a",
    "test_implementation_phase_reuse.py": "e7c4f02b85a0e6cefa7b36b6584b337533b7a4e1efaef3b4ab42f57bc05e5002",
    "test_install_config_safety.py": "4fb7c61851dd4fea53f08c6e6a3feff53372d023c68526bb318d16d4ab8663a5",
    "test_installation.py": "691c59f3de9930b21d257798e1b505c73d1ff9ad59116de03a56808ef3c614bd",
    "test_interview.py": "8e32056374b22a4247f916c3da205ceebd084ce66da0d79a2edebf4f0df4198b",
    "test_mutable_state.py": "d6b1c1e74021625347517cd80af787d4317643e87b4d2dd2a4c70bdfce1dae02",
    "test_native_process.py": "5757b225cc4aac67c81fc61b0e5f498d3ab2364c9ef0c76d7a0760bbfc84c07f",
    "test_parsed_plan_reuse.py": "eb023236aa41d1d6d2308ad0c1d371701b574ad7e0f8470558fd1407c73117b6",
    "test_patch_scope.py": "82bd87e4c55bc14b72966437b3b1c223c976157b9048262f01663b6a8d0161f6",
    "test_pinner_reads.py": "ebaf66728c0dabbf4c47d0bb67e8d634b04cf8542bf0fead02227973690cd32b",
    "test_plan_modes.py": "d01941dac0c93fb6a96f26fa594ccf4055854151ad9c2fc156bcae931e95e33f",
    "test_planning_contract.py": "a538b7e7435b97a800412ecb7e229dc0d5610c42f6852bf8d334cf2a1fefe80d",
    "test_project_commands.py": "42af3eeb31124a9c71be9348f9bf40693e94b8e622bcc5eb098639bac12ff951",
    "test_project_contracts.py": "c7d695bb85222caa6c68a555f34a2de30e378f8ff04f43f23a58b905095d5af3",
    "test_quality_gates.py": "99cdaefc06c01ca10d97b391fa35696a9ef8c537fdafb7601efc58e1001b8b52",
    "test_release.py": "d16f0ad9b4997bf97bf5b7115fce6c69fc1e81644a5e3f99807fee4cec740d55",
    "test_resume_context.py": "db5c0977fde839344d88d15d0d61bbb0fa090291ef6249db9200faa5473f35b7",
    "test_resume_guidance.py": "0bcda1750ba9ed519c08ecf8f0a8c9d5d0c5a544a6c4aa00a8d1775a0f638f32",
    "test_runtime_binding.py": "5f10520b26f31e8c5af87ceb65b72e11ef2ac5807e98902620e90fbede6989b2",
    "test_runtime_loading.py": "b8b77461891f3cad76e1f5842a838d6f408f7b8e8e0e1fc45b13d003b95e668a",
    "test_runtime_performance.py": "69c3448016f2644a44a1805691198b5b1dfd842b2c7bcf5e6e513c15ff0db53b",
    "test_runtime_reuse.py": "91fe91c5ccbd9077fc4965fd3d2f547ef683a69d524ceddb654e801e36425856",
    "test_section_contracts.py": "de911d33ee7d8e71ba372fe540e476aac41663cce4cfc7c3a9ec100b3943ca6d",
    "test_test_binding.py": "d08862ff2eee423190dd6878e12da54b2af6e47080848f1655a14cbe703a7723",
    "test_transaction_publication.py": "fc44fe564cc1641b4bbfd53e2c5bb95dbb637d1e27bc115ada4b634cf28b64f9",
    "test_transaction_rollback.py": "cdab79a27436bf25551a604b61d3fda77d0290de80f41214e0b5faa13e5f86f5",
    "test_trial_matrix.py": "8059a36d0fbcb9364a43959b219c8705598f780de47e3347acc98f629844f262",
    "test_trial_realism.py": "178cc1b10d2568905ead122e29b8ecf2cdffa0a5fa75f7a96f119a84e90f9928",
    "test_verification_contracts.py": "76bb8151b2e64010f25b1d7cc60a96f4b4acba65cb7da1c257140546e5977314",
    "test_workflow_admission.py": "743f728364eaa57a70b0db9b4c3bd525b45a76845e8df4f889af265434c173d7",
    "test_workflow_efficiency.py": "b48fd05bf0dca666881f7d9b69e459549805f7e1a80ba695b767df304f10595c",
    "test_zagrosi_skills.py": "ad6c111542f09a12e10c158268b57998c5ae7eabf8a371bc92e5c9197a263bf4"
}
# END TEST MANIFEST


def implementation_source_paths() -> dict[str, Path]:
    running_tool = _storage.absolute_path_no_follow(str(CLI_PATH))
    plugin_root = running_tool.parent.parent
    paths = {
        "tool": plugin_root / "scripts" / "zagrosi_skills.py",
        "skill": plugin_root / "skills" / "zagrosi-implement" / "SKILL.md",
        "test": plugin_root / "tests" / "test_zagrosi_skills.py",
    }
    if running_tool != paths["tool"]:
        raise _models.DetachedImplementationError(
            "unsafe-implement-source",
            "Detached mode must run from the fixed scripts/zagrosi_skills.py plugin path.",
            implement_source="tool",
            expected_implement_source_path=str(paths["tool"]),
            actual_implement_source_path=str(running_tool),
        )
    return paths


def reopen_implementation_source(source: str, path: Path) -> dict[str, Any]:
    parent_fd: int | None = None
    reopened_parent_fd: int | None = None
    try:
        parent_fd = _secure_io.open_directory_chain_no_follow(path.parent)
        parent_stat = os.fstat(parent_fd)
        raw = _secure_io.read_single_link_regular_at(parent_fd, path.name, cap=_detached_contract.IMPLEMENTATION_SOURCE_CAP)
        reopened_parent_fd = _secure_io.open_directory_chain_no_follow(path.parent)
        reopened_parent_stat = os.fstat(reopened_parent_fd)
        reopened_raw = _secure_io.read_single_link_regular_at(reopened_parent_fd, path.name, cap=_detached_contract.IMPLEMENTATION_SOURCE_CAP)
        if (
            (parent_stat.st_dev, parent_stat.st_ino) != (reopened_parent_stat.st_dev, reopened_parent_stat.st_ino)
            or raw != reopened_raw
        ):
            raise _models.DetachedImplementationError(
                "implement-source-changed",
                f"Implementation {source} source changed while its complete bytes were reopened.",
                implement_source=source,
                implement_source_path=str(path),
            )
        return {"path": str(path), "sha256": _handoff_wire.sha256_digest(raw), "size": len(raw)}
    except _models.DetachedImplementationError as exc:
        if exc.code == "implement-source-changed":
            raise
        raise _models.DetachedImplementationError(
            "unsafe-implement-source",
            f"Implementation {source} source must be a component-wise no-follow regular single-link file.",
            implement_source=source,
            implement_source_path=str(path),
            source_error_code=exc.code,
        ) from exc
    except OSError as exc:
        raise _models.DetachedImplementationError(
            "unsafe-implement-source",
            f"Implementation {source} source could not be reopened safely.",
            implement_source=source,
            implement_source_path=str(path),
        ) from exc
    finally:
        if reopened_parent_fd is not None:
            os.close(reopened_parent_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def verify_detached_contract_reference(skill_path: Path) -> dict[str, Any]:
    contract_path = skill_path.parent / _detached_contract.DETACHED_CONTRACT_RELATIVE_PATH
    record = reopen_implementation_source("contract", contract_path)
    if record["sha256"] != _detached_contract.DETACHED_CONTRACT_SHA256:
        raise _models.DetachedImplementationError(
            "implement-contract-drift",
            "Detached implementation contract bytes do not match the tool-pinned complete-file sha256.",
            implement_source="contract",
            implement_source_path=record["path"],
            expected_implement_source_sha256=_detached_contract.DETACHED_CONTRACT_SHA256,
            actual_implement_source_sha256=record["sha256"],
        )
    return record


def expected_implementation_source_hashes(args: argparse.Namespace) -> dict[str, str]:
    expected: dict[str, str] = {}
    for source in _detached_contract.IMPLEMENTATION_SOURCE_NAMES:
        argument = f"--expected-implement-{source}-sha256"
        value = getattr(args, f"expected_implement_{source}_sha256", None)
        if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise _models.DetachedImplementationError(
                "missing-implement-source-hash",
                f"Detached frozen-planning mode requires {argument} with an exact sha256 digest.",
                implement_source=source,
                required_argument=argument,
            )
        expected[source] = value
    return expected


def verify_implementation_tests(test_path: Path, anchor_record: dict[str, Any]) -> None:
    if not isinstance(TEST_MANIFEST, dict) or test_path.name not in TEST_MANIFEST or any(
        not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_]+\.py", name) is None
        or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        for name, digest in TEST_MANIFEST.items()
    ):
        raise _models.DetachedImplementationError(
            "unsafe-implement-source", "Implementation test manifest is invalid.", implement_source="test",
        )
    parent_fd = _secure_io.open_directory_chain_no_follow(test_path.parent)
    try:
        for name, digest in TEST_MANIFEST.items():
            record = anchor_record if name == test_path.name else reopen_implementation_source("test", test_path.parent / name)
            if record["sha256"] != f"sha256:{digest}":
                raise _models.DetachedImplementationError(
                    "implement-source-drift", "Implementation test source no longer matches the tool-pinned manifest.",
                    implement_source="test", implement_source_path=record["path"],
                )
        reopened_fd = _secure_io.open_directory_chain_no_follow(test_path.parent)
        try:
            if _secure_io._fd_identity(reopened_fd) != _secure_io._fd_identity(parent_fd):
                raise _models.DetachedImplementationError(
                    "implement-source-changed", "Implementation test directory changed during source verification.",
                    implement_source="test", implement_source_path=str(test_path.parent),
                )
            if {name for name in os.listdir(reopened_fd) if name.endswith(".py")} != set(TEST_MANIFEST):
                raise _models.DetachedImplementationError(
                    "implement-source-drift", "Implementation test source inventory no longer matches the tool-pinned manifest.",
                    implement_source="test",
                )
        finally:
            os.close(reopened_fd)
    finally:
        os.close(parent_fd)


def reopen_implementation_sources(*, expected_hashes: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    try:
        verify_sources()
    except ImportError as exc:
        raise _models.DetachedImplementationError(
            "implement-source-drift", "Implementation runtime modules no longer match the tool-pinned manifest.",
            implement_source="tool",
        ) from exc
    records: dict[str, dict[str, Any]] = {}
    paths = implementation_source_paths()
    verify_detached_contract_reference(paths["skill"])
    for source, path in paths.items():
        record = reopen_implementation_source(source, path)
        expected_sha256 = expected_hashes.get(source) if expected_hashes is not None else None
        if expected_sha256 is not None and record["sha256"] != expected_sha256:
            raise _models.DetachedImplementationError(
                "implement-source-drift",
                f"Implementation {source} source bytes do not match the required complete-file sha256.",
                implement_source=source,
                implement_source_path=record["path"],
                expected_implement_source_sha256=expected_sha256,
                actual_implement_source_sha256=record["sha256"],
            )
        records[source] = record
    verify_implementation_tests(paths["test"], records["test"])
    return records


def implementation_source_config_fields(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for source in _detached_contract.IMPLEMENTATION_SOURCE_NAMES:
        record = records[source]
        fields[f"implement_{source}_path"] = record["path"]
        fields[f"implement_{source}_sha256"] = record["sha256"]
        fields[f"implement_{source}_size"] = record["size"]
    return fields


def verify_implementation_sources(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    paths = implementation_source_paths()
    verify_detached_contract_reference(paths["skill"])
    expected_hashes: dict[str, str] = {}
    for source in _detached_contract.IMPLEMENTATION_SOURCE_NAMES:
        path_field = f"implement_{source}_path"
        hash_field = f"implement_{source}_sha256"
        size_field = f"implement_{source}_size"
        expected_path = str(paths[source])
        expected_sha256 = config.get(hash_field)
        expected_size = config.get(size_field)
        if config.get(path_field) != expected_path:
            raise _models.DetachedImplementationError(
                "invalid-detached-config",
                f"Detached config does not bind the exact current implementation {source} source path.",
                implement_source=source,
                expected_implement_source_path=expected_path,
                actual_implement_source_path=config.get(path_field),
            )
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_sha256):
            raise _models.DetachedImplementationError(
                "invalid-detached-config",
                f"Detached config implementation {source} source sha256 is invalid.",
                implement_source=source,
            )
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
            raise _models.DetachedImplementationError(
                "invalid-detached-config",
                f"Detached config implementation {source} source size is invalid.",
                implement_source=source,
            )
        expected_hashes[source] = expected_sha256
    records = reopen_implementation_sources(expected_hashes=expected_hashes)
    for source, record in records.items():
        expected_size = config[f"implement_{source}_size"]
        if record["size"] != expected_size:
            raise _models.DetachedImplementationError(
                "implement-source-drift",
                f"Implementation {source} source size no longer matches implement-setup.",
                implement_source=source,
                implement_source_path=record["path"],
                expected_implement_source_size=expected_size,
                actual_implement_source_size=record["size"],
            )
    return records
