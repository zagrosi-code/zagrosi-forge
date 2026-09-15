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
    "fault_injection_support.py": "a8f60f84e9059323544da7933f6399233cfd09c165c1a73098de9025bb0c2f10",
    "forge_test_helpers.py": "673c34df90c2253ecd45a516d021433dac6d0b91369775905899bf573e44d45e",
    "runtime_support.py": "195586d290650a6add408c880164538450bdfbc346fc8971470e622d211446a1",
    "test_benchmark_forge.py": "1436b31c5fc07824a903fb4a2f5bccf565059dbffa67151da7819b932c2383eb",
    "test_capabilities.py": "e579ca105e8dd4c08f476e407f4fe6ebaccd4df8ba7c2b2126ed4f5489e63b88",
    "test_coding_trials.py": "1ab8937af4481cc7f8ffe8b377b50254ce9351d3cd3bf011ea35551ad42a3a99",
    "test_compact_evaluations.py": "0b6c8c448711a03e7a6586fece8c99f18fd4ff902c6399af6977511f7346f9d1",
    "test_compact_helpers.py": "cd01296d153b48d035cc31b4a95b9ca5536f963abe8e9e8cdbdd5438e214d1a8",
    "test_compact_plan.py": "bcdc03a4016c7ba2aaee540c7fc1aa3338c3fd9dea2f825d5efa274e5e7780e9",
    "test_completion_contracts.py": "0b8b071616684f6ff5191d62936d4f8d656517c8c67059f2b2b42c0fad2d92cc",
    "test_completion_records.py": "ac3b5211480adcd641baefc013da1340ef005592969086bd1174e63969016d38",
    "test_context_packets.py": "40914705d330a389869388bb7dad73d0249d81df182edca371cff5d4805a6b38",
    "test_detached_authorities.py": "a8175a97dc7a6d201014364ef1a5ec4e821a48566e611e1831010d70c45d5233",
    "test_detached_dependencies.py": "4d69af9b83ead7a8f78a94ce43d4ccf275664d1b1958e54dd7d32cd226767dd2",
    "test_detached_locks.py": "dcbdc69d937f92b0ccf98661ec5c59bc5591221a676263aecb8fa4a8c2d83e2a",
    "test_detached_recording.py": "9801dc1698bb642a9b0dda4f7e18ff699a490f9de83b0f2c430a2e699cb6915e",
    "test_detached_recovery.py": "ec67066cbf4f0c505aa5573d2848bd0f332fc11a8ad0a82359dab36b5f09b9c0",
    "test_detached_resources.py": "87b343b4576d73408a641313eb881ce25ec76a2e22185121d9943abaddd617b7",
    "test_detached_setup.py": "c60606369d002b64c9b4c8cbc9c904f97eabed46c75a4ed39f8410c5d4814ba2",
    "test_evaluations.py": "117143ac576128b27aec09a5b7939884763dbffca6e8e15787e10f66d1d8a775",
    "test_gate_reports.py": "3b3c863efd63a12d1e8aeb857c16cde033ebe9ead6535ea4cf666749e2699328",
    "test_handoff_authority.py": "0be15c2c0aa78fc104955dba8156ec6d6333556223e1e91dcfa4fa0d826c7279",
    "test_handoff_failures.py": "5c0cdfcae4a3c0382c3356557474faa76695f3d56a0099d0b7ea2dde1d151d72",
    "test_handoff_readiness.py": "b1eb9f207136d7d397887b26a6a2fa204493f89ec3cf56e641e02412ac7b5925",
    "test_handoff_transport.py": "104517f15aa1c085e9da2fd45bb104ea1f021f77b851cb5e8fb7d592948e8cae",
    "test_handoff_wire.py": "4c2fb34168a5dae808b37dfd9d75840da93dc9ee93bc73200db80b90c7e27b72",
    "test_implementation_drift.py": "12f4cb0977c8557718015851ff1ae1a8bd7d305957d8b8e205942e453ba1fe7a",
    "test_installation.py": "691c59f3de9930b21d257798e1b505c73d1ff9ad59116de03a56808ef3c614bd",
    "test_interview.py": "8e32056374b22a4247f916c3da205ceebd084ce66da0d79a2edebf4f0df4198b",
    "test_native_process.py": "5757b225cc4aac67c81fc61b0e5f498d3ab2364c9ef0c76d7a0760bbfc84c07f",
    "test_patch_scope.py": "82bd87e4c55bc14b72966437b3b1c223c976157b9048262f01663b6a8d0161f6",
    "test_pinner_reads.py": "ebaf66728c0dabbf4c47d0bb67e8d634b04cf8542bf0fead02227973690cd32b",
    "test_plan_modes.py": "d01941dac0c93fb6a96f26fa594ccf4055854151ad9c2fc156bcae931e95e33f",
    "test_project_commands.py": "2851fa0abf3b0a35030ba49c615fea2012c0d24338f626278c4f19ce771e0b82",
    "test_project_contracts.py": "c7d695bb85222caa6c68a555f34a2de30e378f8ff04f43f23a58b905095d5af3",
    "test_quality_gates.py": "7bdcdac409bb733ff80a5f2c50a8a05350085bff3fcfda18fb659dda37a64606",
    "test_release.py": "a2d36f28691d9f37006eec10167281b0511e485fcbe9289b1c343ce29b453451",
    "test_resume_guidance.py": "85958980a061d550f5b51dd9c01344c5c110a3ad0cca2779a899f1b6608d3724",
    "test_runtime_binding.py": "5f10520b26f31e8c5af87ceb65b72e11ef2ac5807e98902620e90fbede6989b2",
    "test_runtime_loading.py": "b8b77461891f3cad76e1f5842a838d6f408f7b8e8e0e1fc45b13d003b95e668a",
    "test_runtime_performance.py": "e24116a603ad97c3ebdf5121b25227974d8f6a50a51e43afb76522dd35683bc8",
    "test_section_contracts.py": "de911d33ee7d8e71ba372fe540e476aac41663cce4cfc7c3a9ec100b3943ca6d",
    "test_test_binding.py": "d08862ff2eee423190dd6878e12da54b2af6e47080848f1655a14cbe703a7723",
    "test_transaction_publication.py": "fc44fe564cc1641b4bbfd53e2c5bb95dbb637d1e27bc115ada4b634cf28b64f9",
    "test_transaction_rollback.py": "465b2ff62acb712a3bfbd19c8ff2684a5fd42775f48687cb538cb73d05a91b45",
    "test_verification_contracts.py": "ad010d6fa66d7d0530dbfe3c6d3f71629ba191637ed955ec009487f9f6440a2e",
    "test_workflow_efficiency.py": "f845077fb6d8b1323834deb3457bb4aa43db8a4ce85d54a92728d77f6ef0a84a",
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
