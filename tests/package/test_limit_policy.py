from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]


EXPECTED_LIMITS = {
    "identifier_bytes": 63,
    "alternate_id_hex_chars": 24,
    "cachebuster_hex_chars": 32,
    "path_bytes": 240,
    "path_components": 16,
    "path_component_bytes": 63,
    "json_record_bytes": 256 * 1024,
    "json_depth": 32,
    "json_members": 512,
    "toml_bytes": 2 * 1024 * 1024,
    "toml_depth": 64,
    "toml_nodes": 4_096,
    "bundle_files": 1_024,
    "bundle_member_bytes": 16 * 1024 * 1024,
    "bundle_total_bytes": 64 * 1024 * 1024,
    "archive_compressed_bytes": 64 * 1024 * 1024,
    "archive_expanded_bytes": 128 * 1024 * 1024,
    "archive_ratio": 20,
    "journal_records": 32,
    "journal_record_bytes": 512 * 1024,
    "journal_total_bytes": 8 * 1024 * 1024,
    "subprocess_channel_bytes": 1024 * 1024,
    "subprocess_tail_bytes": 16 * 1024,
    "subprocess_timeout_seconds": 90,
    "subprocess_total_seconds": 600,
    "lock_default_seconds": 30,
    "lock_max_seconds": 300,
    "evidence_records": 128,
    "evidence_bytes": 32 * 1024 * 1024,
    "evidence_warning_days": 30,
    "private_recovery_records": 16,
    "private_recovery_bytes": 32 * 1024 * 1024,
    "private_recovery_warning_hours": 24,
    "backup_records": 5,
    "backup_bytes": 10 * 1024 * 1024,
    "backup_days": 30,
    "property_ci_examples": 500,
    "state_machine_sequences": 100,
    "state_machine_steps": 30,
}


@pytest.mark.parametrize(("resource", "limit"), EXPECTED_LIMITS.items())
def test_limit_policy_accepts_limit_and_rejects_limit_plus_one(
    resource: str, limit: int
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.policies import LIMIT_POLICY, enforce_limit

    assert LIMIT_POLICY.value(resource) == limit
    enforce_limit(resource, limit)
    with pytest.raises(ForgeError) as caught:
        enforce_limit(resource, limit + 1)
    assert caught.value.code == "limit.exceeded"
    assert caught.value.exit_category == 10


def test_all_schemas_and_runtime_decoders_use_limit_policy_version() -> None:
    from zagrosi_forge.install.contracts import canonical_json_bytes
    from zagrosi_forge.install.policies import LIMIT_POLICY, load_policy

    assert LIMIT_POLICY.version == "1.0"
    schema_paths = sorted((ROOT / "src/zagrosi_forge/install/schemas").glob("*.json"))
    assert schema_paths
    for path in schema_paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["x-limit-policy-version"] == LIMIT_POLICY.version, path.name
    for resource in (
        "limit-policy.json",
        "native-support-policy.json",
        "native-isolation-policy.json",
        "recovery-retention-policy.json",
    ):
        record = load_policy(resource)
        assert record["limit_policy_version"] == LIMIT_POLICY.version
        assert ROOT.joinpath(
            "src/zagrosi_forge/install", resource
        ).read_bytes() == canonical_json_bytes(dict(record), final_newline=True)


def test_native_and_retention_policies_are_typed_and_immutable() -> None:
    from zagrosi_forge.install.policies import (
        NATIVE_ISOLATION_POLICY,
        NATIVE_SUPPORT_POLICY,
        RECOVERY_RETENTION_POLICY,
        NativeIsolationPolicy,
        NativeSupportPolicy,
        RecoveryRetentionPolicy,
    )

    assert isinstance(NATIVE_SUPPORT_POLICY, NativeSupportPolicy)
    assert NATIVE_SUPPORT_POLICY.codex_versions == ("0.144.4",)
    assert "linux-x86_64" in NATIVE_SUPPORT_POLICY.platforms
    assert (
        NATIVE_SUPPORT_POLICY.manifest_scripts_field == "accepted_no_execution_observed"
    )
    assert isinstance(NATIVE_ISOLATION_POLICY, NativeIsolationPolicy)
    assert "process_tree_termination" in NATIVE_ISOLATION_POLICY.required
    assert isinstance(RECOVERY_RETENTION_POLICY, RecoveryRetentionPolicy)
    assert RECOVERY_RETENTION_POLICY.generation_retention == "unbounded"
    with pytest.raises(TypeError):
        RECOVERY_RETENTION_POLICY.backups["max_records"] = 99  # type: ignore[index]


def test_native_policy_constructors_normalize_mutable_sequences() -> None:
    from typing import cast

    from zagrosi_forge.install.policies import (
        NativeIsolationPolicy,
        NativeSupportPolicy,
    )

    codex_versions = ["0.144.4"]
    platforms = ["linux-x86_64"]
    support = NativeSupportPolicy(
        version="1.0",
        codex_versions=cast(tuple[str, ...], codex_versions),
        platforms=cast(tuple[str, ...], platforms),
        manifest_scripts_field="accepted_no_execution_observed",
    )
    required = ["credential_isolation"]
    not_claimed = ["egress_denial"]
    isolation = NativeIsolationPolicy(
        version="1.0",
        required=cast(tuple[str, ...], required),
        not_claimed=cast(tuple[str, ...], not_claimed),
    )

    codex_versions.append("candidate-version")
    platforms.append("candidate-platform")
    required.append("candidate-capability")
    not_claimed.append("candidate-capability")
    assert support.codex_versions == ("0.144.4",)
    assert support.platforms == ("linux-x86_64",)
    assert isolation.required == ("credential_isolation",)
    assert isolation.not_claimed == ("egress_denial",)


def test_loaded_policy_records_are_recursively_immutable() -> None:
    from zagrosi_forge.install.policies import load_policy

    record = load_policy("limit-policy.json")
    values = record["values"]
    assert isinstance(values, Mapping)
    with pytest.raises(TypeError):
        values["identifier_bytes"] = 999


@pytest.mark.parametrize(
    ("resource", "mutation"),
    (
        ("limit-policy.json", "extra_top_level"),
        ("limit-policy.json", "missing_limit"),
        ("limit-policy.json", "boolean_limit"),
        ("native-support-policy.json", "bad_scripts_outcome"),
        ("native-isolation-policy.json", "overlapping_capability"),
        ("recovery-retention-policy.json", "missing_nested_limit"),
    ),
)
def test_policy_v1_manual_decoders_reject_invalid_closed_shapes(
    resource: str, mutation: str
) -> None:
    from zagrosi_forge.install.contracts import (
        ForgeError,
        canonical_json_bytes,
    )
    from zagrosi_forge.install.policies import _validate_policy_record, load_policy

    record = json.loads(canonical_json_bytes(dict(load_policy(resource))))
    if mutation == "extra_top_level":
        record["candidate_field"] = True
    elif mutation == "missing_limit":
        del record["values"]["identifier_bytes"]
    elif mutation == "boolean_limit":
        record["values"]["identifier_bytes"] = True
    elif mutation == "bad_scripts_outcome":
        record["manifest_scripts_field"] = "ignored"
    elif mutation == "overlapping_capability":
        record["not_claimed"].append(record["required"][0])
    elif mutation == "missing_nested_limit":
        del record["backups"]["max_records"]
    else:  # pragma: no cover - parameter contract
        raise AssertionError(mutation)

    with pytest.raises(ForgeError) as caught:
        _validate_policy_record(resource, record)
    assert caught.value.code == "policy.record_invalid"
    assert caught.value.exit_category == 10
