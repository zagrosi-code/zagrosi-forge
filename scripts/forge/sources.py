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
    "forge_test_helpers.py": "50bf063d1f753fdcbcf7db92008f2bcfc8a871a7460c4f2c9da7af7a0c7c38bb",
    "runtime_support.py": "195586d290650a6add408c880164538450bdfbc346fc8971470e622d211446a1",
    "test_adapter_loading.py": "d8c4086464c3cd401caa2d6887b6f4ebbb44d863620f02086507d90da60a8b7c",
    "test_benchmark_forge.py": "1436b31c5fc07824a903fb4a2f5bccf565059dbffa67151da7819b932c2383eb",
    "test_cache_publication.py": "218df403a40b42fe170ee816d0dd5de2df7f58106ac79e7d4dd55983f0698aff",
    "test_capabilities.py": "1d30df82e81ab304ae6c11991a9290bd34385bd533176704b56853243218d1e5",
    "test_claude_plugin.py": "ab11034b0c63b81ead2e75358f67f4232580470c2c66363b8a6fb892732d5828",
    "test_coding_trials.py": "3541354468e8072c73df3edf76db6605f014411b82cfc8f5b9295cc6bc77ec01",
    "test_compact_evaluations.py": "0b6c8c448711a03e7a6586fece8c99f18fd4ff902c6399af6977511f7346f9d1",
    "test_compact_helpers.py": "cd01296d153b48d035cc31b4a95b9ca5536f963abe8e9e8cdbdd5438e214d1a8",
    "test_compact_plan.py": "bcdc03a4016c7ba2aaee540c7fc1aa3338c3fd9dea2f825d5efa274e5e7780e9",
    "test_complete_plan_admission.py": "388d6949bcb8b93079acb4c4a6aa33363bae663ff3ef34091108a0f4903870e3",
    "test_completion_contracts.py": "00bc8b153463486b062ceae517d7e8499a510643dcf9120296e48631c87fed24",
    "test_completion_records.py": "dddafb5bce1aaecde79e2cdd984e310143c5eae0e2fa299c02e804047ef111e0",
    "test_context_packets.py": "a2a81f174ebe35875dee36b661bc7c80567ae4cd5c386e99c50276e20866bdf8",
    "test_controlled_trials.py": "a0f26586c960806c692b88c60579a8047322753b2934790c9fad3a0538dcf80d",
    "test_cross_host_resume.py": "b4308e20b080cb61bbce8f2a569104d7d26a0217de734630f9c2847ee1e62958",
    "test_detached_authorities.py": "9df8993eebb931d4211201f4d2d1b76fc5f35f64e147dcb7aeb4d087d8b47d3b",
    "test_detached_dependencies.py": "8fb4070e2a9507c0d3248b0e047525cb35728bd08be846584fbc7339828f1878",
    "test_detached_locks.py": "dcbdc69d937f92b0ccf98661ec5c59bc5591221a676263aecb8fa4a8c2d83e2a",
    "test_detached_recording.py": "9801dc1698bb642a9b0dda4f7e18ff699a490f9de83b0f2c430a2e699cb6915e",
    "test_detached_recovery.py": "ec67066cbf4f0c505aa5573d2848bd0f332fc11a8ad0a82359dab36b5f09b9c0",
    "test_detached_resources.py": "87b343b4576d73408a641313eb881ce25ec76a2e22185121d9943abaddd617b7",
    "test_detached_setup.py": "c60606369d002b64c9b4c8cbc9c904f97eabed46c75a4ed39f8410c5d4814ba2",
    "test_evaluations.py": "5a4f4ce677bcb8e4f1af7fa74e24f8e4b01b56f665c1621c3dec44c37458ee7f",
    "test_evidence_discovery.py": "1e2671eacebdbadda57d9f87a4570d7e1eb633574b8a01c78690d75d7bbcf0ce",
    "test_gate_reports.py": "4986d356f1261f95771993ea10a82956895f11313b595f302766d04868317b4f",
    "test_handoff_authority.py": "f8915d3bccbb20751c48ae3c95bd80bea29389691a76fa054c4507bfd519b09f",
    "test_handoff_failures.py": "228106db7ad96d04057647fffce839721b449e8a2884626ba55eea7264beb6f0",
    "test_handoff_readiness.py": "b1eb9f207136d7d397887b26a6a2fa204493f89ec3cf56e641e02412ac7b5925",
    "test_handoff_transport.py": "104517f15aa1c085e9da2fd45bb104ea1f021f77b851cb5e8fb7d592948e8cae",
    "test_handoff_wire.py": "4c2fb34168a5dae808b37dfd9d75840da93dc9ee93bc73200db80b90c7e27b72",
    "test_implementation_drift.py": "12f4cb0977c8557718015851ff1ae1a8bd7d305957d8b8e205942e453ba1fe7a",
    "test_implementation_phase_reuse.py": "f79a211e105becb4c23da1ea06a842d2fe1617bbb99fc1644195b7a6d13167db",
    "test_install_config_safety.py": "7214d15f4410b4b5d49bf1be558c83c2e713d2c2d7bb2c90f776814b53d247ba",
    "test_installation.py": "159c62a94871576af98ca74e40d748633b650ef6d42685c991de2aadeb13d986",
    "test_interview.py": "8e32056374b22a4247f916c3da205ceebd084ce66da0d79a2edebf4f0df4198b",
    "test_mutable_state.py": "c2a1bd08ce23094d4001a7e1998beac79c4c4ecf06f39dcf84a821ad86287b9e",
    "test_native_process.py": "5757b225cc4aac67c81fc61b0e5f498d3ab2364c9ef0c76d7a0760bbfc84c07f",
    "test_parallel_ownership.py": "e0265620b989847bb0dc8cf60d8d5d12f62941a357c145d8da7e21cb0b2a1224",
    "test_parsed_plan_reuse.py": "eb023236aa41d1d6d2308ad0c1d371701b574ad7e0f8470558fd1407c73117b6",
    "test_patch_scope.py": "82bd87e4c55bc14b72966437b3b1c223c976157b9048262f01663b6a8d0161f6",
    "test_pinner_reads.py": "ebaf66728c0dabbf4c47d0bb67e8d634b04cf8542bf0fead02227973690cd32b",
    "test_plan_modes.py": "d01941dac0c93fb6a96f26fa594ccf4055854151ad9c2fc156bcae931e95e33f",
    "test_planning_contract.py": "a538b7e7435b97a800412ecb7e229dc0d5610c42f6852bf8d334cf2a1fefe80d",
    "test_portable_gate_batch.py": "a94a1bb94f261fa271a70b4f271ec72c68b78eaa50a80088144219206a1c06d8",
    "test_project_commands.py": "07f9a43b2455005164260571a45b178714c9519f8fb937f7b5f4052d3240f9f1",
    "test_project_contracts.py": "c7d695bb85222caa6c68a555f34a2de30e378f8ff04f43f23a58b905095d5af3",
    "test_providers.py": "e087b71baf06be8f23175510a63be07e94e5d0a4a837f8f1691791419488e57c",
    "test_quality_gates.py": "99cdaefc06c01ca10d97b391fa35696a9ef8c537fdafb7601efc58e1001b8b52",
    "test_release.py": "d16f0ad9b4997bf97bf5b7115fce6c69fc1e81644a5e3f99807fee4cec740d55",
    "test_release_identity.py": "2a2bbbd03bc07e7ece8b6c5f1f4c6ae7d271b7bfa41a0f8fe218da14c2e232db",
    "test_resume_context.py": "5aa47690740f0d1db589d1ea526f99c54de7541ae038641967aae610cc2df9b7",
    "test_resume_guidance.py": "40a776c2b9369e0b2fc0902ff4b287b7211639b5007f1f2d9d7ce66cb9f91bba",
    "test_runtime_binding.py": "5f10520b26f31e8c5af87ceb65b72e11ef2ac5807e98902620e90fbede6989b2",
    "test_runtime_loading.py": "b8b77461891f3cad76e1f5842a838d6f408f7b8e8e0e1fc45b13d003b95e668a",
    "test_runtime_performance.py": "4f7cf467082e3eca7b2548dff0a7aa6239270c9118e0f5f8a2cd6a5bc084a5d7",
    "test_runtime_reuse.py": "91fe91c5ccbd9077fc4965fd3d2f547ef683a69d524ceddb654e801e36425856",
    "test_section_contracts.py": "de911d33ee7d8e71ba372fe540e476aac41663cce4cfc7c3a9ec100b3943ca6d",
    "test_test_binding.py": "d08862ff2eee423190dd6878e12da54b2af6e47080848f1655a14cbe703a7723",
    "test_transaction_publication.py": "fc44fe564cc1641b4bbfd53e2c5bb95dbb637d1e27bc115ada4b634cf28b64f9",
    "test_transaction_rollback.py": "cdab79a27436bf25551a604b61d3fda77d0290de80f41214e0b5faa13e5f86f5",
    "test_trial_hosts.py": "80e67273c9a9ecf9ba173da01f97942cec4d731d6130579b78d68bb10a93fa4a",
    "test_trial_matrix.py": "8059a36d0fbcb9364a43959b219c8705598f780de47e3347acc98f629844f262",
    "test_trial_realism.py": "0e1ac97be61f9281083521a65cb445e9c2d7080e5089c434b27e6de35e72ac13",
    "test_verification_contracts.py": "76bb8151b2e64010f25b1d7cc60a96f4b4acba65cb7da1c257140546e5977314",
    "test_verification_receipts.py": "db7cedc292d431ea18323d9da502c6d7adb9041159b9ad85289726de132acd0b",
    "test_workflow_admission.py": "a02a13a709a13c8553891a8431a0b7c11063cf72d9bb321f397d5e121b111cba",
    "test_workflow_efficiency.py": "2027dd50adac459168ae9d18334e2121b136a1d8addc8dc84c8884abd3411f48",
    "test_zagrosi_skills.py": "d2e0e0c6c4d1f2efb620ccadb0377473aa2f1898bb58d0b78b7cfbfa3c686cf0"
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
