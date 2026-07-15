from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from typing import cast

import pytest


def _identity(*, base_digest: str = "a" * 64, rendered_digest: str = "b" * 64):
    from zagrosi_forge.install.contracts import InstallIdentity
    from zagrosi_forge.install.version import derive_install_version

    return InstallIdentity(
        marketplace_id="zagrosi",
        plugin_id="zagrosi-forge",
        base_version="0.2.0",
        install_version=derive_install_version("0.2.0", base_digest),
        base_payload_digest=base_digest,
        rendered_payload_digest=rendered_digest,
        policy_digest="c" * 64,
        transformation_profile="plugin-v1",
        contract_versions=("finding-v1", "identity-v1"),
    )


def test_finding_serialization_is_canonical_and_redacted() -> None:
    from zagrosi_forge.install.contracts import Finding, canonical_json_bytes
    from zagrosi_forge.install.diagnostics import finding_to_dict

    finding = Finding(
        code="metadata.invalid",
        severity="error",
        message="TOKEN=hunter2 at /Users/alice/.codex/config.toml\ninvalid",
        subject="config:managed-marketplace",
        authority="metadata-schema",
        authority_version="1",
        remediation="Correct the managed entry.",
        details={"state": "rejected", "count": 1},
    )
    serialized = canonical_json_bytes(finding_to_dict(finding))
    assert serialized == canonical_json_bytes(json.loads(serialized))
    assert b"hunter2" not in serialized
    assert b"/Users/alice" not in serialized
    assert b"\\n" not in serialized
    assert serialized.index(b'"count"') < serialized.index(b'"state"')


@pytest.mark.parametrize(
    "details",
    (
        cast(dict[str, object], {1: "not-a-string-key"}),
        {"nested": [[[[["candidate"]]]]]},
        {"float": 1.5},
        {"oversize": "x" * 4_097},
    ),
)
def test_finding_rejects_hostile_details_before_freezing(
    details: dict[str, object],
) -> None:
    from zagrosi_forge.install.contracts import Finding, ForgeError

    with pytest.raises(ForgeError) as caught:
        Finding(
            code="metadata.invalid",
            severity="error",
            message="safe",
            subject="config:managed-marketplace",
            authority="metadata-schema",
            authority_version="1",
            remediation="safe",
            details=details,
        )
    assert caught.value.code == "diagnostic.value_rejected"
    assert caught.value.exit_category == 10


@pytest.mark.parametrize(
    ("unsafe", "secret"),
    (
        ("token: hunter2", "hunter2"),
        ("PASSWORD = swordfish", "swordfish"),
        ('{"api_key":"json-secret"}', "json-secret"),
        ("Authorization: Bearer bearer-secret", "bearer-secret"),
        (r"open \\server\share\private.txt", "private.txt"),
        ("open /private", "/private"),
        ("nul\x00 and ansi\x1b[31mred", "\x1b"),
    ),
)
def test_diagnostic_redaction_covers_supported_hostile_forms(
    unsafe: str, secret: str
) -> None:
    from zagrosi_forge.install.diagnostics import redact_text

    rendered = redact_text(unsafe)
    assert secret not in rendered
    assert all(ord(character) >= 32 for character in rendered)


def test_diagnostic_boundary_uses_closed_templates_and_rejects_oversize() -> None:
    from zagrosi_forge.install.contracts import Finding, ForgeError
    from zagrosi_forge.install.diagnostics import finding_to_dict, redact_text

    finding = Finding(
        code="metadata.invalid",
        severity="error",
        message="arbitrary candidate text token: candidate-secret",
        subject="config:managed-marketplace",
        authority="metadata-schema",
        authority_version="1",
        remediation="repeat candidate-secret at /private",
        details={"state": "rejected"},
    )
    rendered = json.dumps(finding_to_dict(finding), sort_keys=True)
    assert "candidate-secret" not in rendered
    assert "arbitrary candidate text" not in rendered
    assert "Package metadata is invalid." in rendered

    for invalid in (
        Finding(
            code="candidate.supplied",
            severity="error",
            message="safe",
            subject="config:managed-marketplace",
            authority="metadata-schema",
            authority_version="1",
            remediation="safe",
            details={},
        ),
        Finding(
            code="metadata.invalid",
            severity="fatal",
            message="safe",
            subject="config:managed-marketplace",
            authority="metadata-schema",
            authority_version="1",
            remediation="safe",
            details={},
        ),
        Finding(
            code="metadata.invalid",
            severity="error",
            message="safe",
            subject="config:managed-marketplace",
            authority="metadata-schema",
            authority_version="1",
            remediation="safe",
            details={"state": "candidate-secret"},
        ),
        Finding(
            code="metadata.invalid",
            severity="error",
            message="x" * 4_097,
            subject="config:managed-marketplace",
            authority="metadata-schema",
            authority_version="1",
            remediation="safe",
            details={},
        ),
    ):
        with pytest.raises(ForgeError, match="Diagnostic") as caught:
            finding_to_dict(invalid)
        assert caught.value.code == "diagnostic.value_rejected"
        assert caught.value.exit_category == 10

    with pytest.raises(ForgeError) as caught:
        redact_text("x" * 4_097)
    assert caught.value.code == "diagnostic.value_rejected"


def test_install_identity_is_candidate_deterministic_across_homes(
    tmp_path: Path,
) -> None:
    first_home = tmp_path / "one"
    second_home = tmp_path / "two"
    first_home.mkdir()
    second_home.mkdir()
    assert _identity() == _identity()
    assert str(first_home) not in repr(_identity())
    assert str(second_home) not in repr(_identity())


def test_install_version_uses_fixed_32_hex_component() -> None:
    from zagrosi_forge.install.version import derive_install_version

    digest = "0123456789abcdef" * 4
    assert derive_install_version("0.2.0", digest) == (
        "0.2.0+codex.local-0123456789abcdef0123456789abcdef"
    )


def test_install_identity_enforces_derived_version_relation() -> None:
    from zagrosi_forge.install.contracts import InstallIdentity

    with pytest.raises(ValueError, match="install_version"):
        InstallIdentity(
            marketplace_id="zagrosi",
            plugin_id="zagrosi-forge",
            base_version="0.2.0",
            install_version="../../not-derived",
            base_payload_digest="a" * 64,
            rendered_payload_digest="b" * 64,
            policy_digest="c" * 64,
            transformation_profile="plugin-v1",
            contract_versions=("identity-v1",),
        )


def test_same_path_different_full_digest_is_corruption() -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.version import require_digest_match

    with pytest.raises(ForgeError) as caught:
        require_digest_match("a" * 64, "a" * 63 + "b")
    assert caught.value.code == "identity.digest_collision"
    assert caught.value.exit_category == 12


def test_runner_provenance_authorizes_only_declared_operations() -> None:
    from zagrosi_forge.install.contracts import (
        ForgeError,
        RunnerOperation,
        RunnerProvenance,
        RunnerState,
        require_runner_authority,
    )

    unverified = RunnerProvenance(
        state=RunnerState.UNVERIFIED_SELF_ROOT,
        origin="source-checkout",
        artifact_digest="a" * 64,
        runner_version="0.2.0",
        verification_authority="none",
        policy_digest="b" * 64,
    )
    require_runner_authority(unverified, RunnerOperation.DIAGNOSTIC)
    require_runner_authority(unverified, RunnerOperation.PLAN)
    for operation in (
        RunnerOperation.MUTATE,
        RunnerOperation.RECOVER,
        RunnerOperation.CLAIM_CANDIDATE_VALID,
        RunnerOperation.CLAIM_RELEASE_VALID,
    ):
        with pytest.raises(ForgeError) as caught:
            require_runner_authority(unverified, operation)
        assert caught.value.code == "runner.untrusted"
        assert caught.value.exit_category == 15

    verified = RunnerProvenance(
        state=RunnerState.VERIFIED_INSTALLED_DISTRIBUTION,
        origin="installed-wheel",
        artifact_digest="c" * 64,
        runner_version="0.2.0",
        verification_authority="wheel-sha256",
        policy_digest="d" * 64,
    )
    for operation in RunnerOperation:
        require_runner_authority(verified, operation)


def test_runner_authority_is_closed_and_default_deny() -> None:
    from zagrosi_forge.install.contracts import (
        ForgeError,
        RunnerOperation,
        RunnerProvenance,
        RunnerState,
        require_runner_authority,
    )

    with pytest.raises(ForgeError) as caught:
        RunnerProvenance(
            state=cast(RunnerState, "unverified_self_root"),
            origin="source-checkout",
            artifact_digest="a" * 64,
            runner_version="0.2.0",
            verification_authority="none",
            policy_digest="b" * 64,
        )
    assert caught.value.code == "runner.untrusted"

    verified = RunnerProvenance(
        state=RunnerState.VERIFIED_INSTALLED_DISTRIBUTION,
        origin="installed-wheel",
        artifact_digest="c" * 64,
        runner_version="0.2.0",
        verification_authority="wheel-sha256",
        policy_digest="d" * 64,
    )
    with pytest.raises(ForgeError) as caught:
        require_runner_authority(
            verified, cast(RunnerOperation, "candidate-supplied-operation")
        )
    assert caught.value.code == "runner.untrusted"


def test_forge_error_contract_is_read_only_but_traceback_compatible() -> None:
    from zagrosi_forge.install.contracts import ForgeError

    @contextmanager
    def passthrough_context():
        yield

    with pytest.raises(ForgeError) as caught:
        with passthrough_context():
            raise ForgeError("record.invalid", 10, "Safe failure.")
    assert caught.value.__traceback__ is not None
    with pytest.raises(AttributeError):
        caught.value.code = "changed"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        del caught.value.code


def test_active_install_relation_requires_every_identity_member() -> None:
    from zagrosi_forge.install.contracts import ActiveInstallRelation

    values = {
        "effective_marketplace_id": "zagrosi",
        "plugin_id": "zagrosi-forge",
        "managed_config_projection": (("source_type", "local"),),
        "source_generation": "source:generation-a",
        "cache_generation": "cache:generation-a",
        "base_version": "0.2.0",
        "install_version": "0.2.0+codex.local-" + "a" * 32,
        "base_payload_digest": "a" * 64,
        "rendered_payload_digest": "b" * 64,
        "committed_receipt_ref": "receipt:" + "c" * 64,
    }
    ActiveInstallRelation(**values)
    for field in values:
        partial = dict(values)
        partial[field] = "" if isinstance(partial[field], str) else ()
        with pytest.raises(ValueError, match=field):
            ActiveInstallRelation(**partial)


def test_unknown_persistent_schema_major_preserves_and_stops() -> None:
    from zagrosi_forge.install.contracts import ForgeError, decode_persistent_record

    raw = bytearray(
        b'{"minimum_reader_version":"0.2.0","record_digest":"'
        + b"0" * 64
        + b'","schema_digest":"'
        + b"1" * 64
        + b'","schema_version":"2.0","writer_version":"0.2.0"}\n'
    )
    before = bytes(raw)
    effect_log: list[str] = []
    with pytest.raises(ForgeError) as caught:
        decode_persistent_record(bytes(raw), supported_major=1)
    assert caught.value.code == "record.reader_unsupported"
    assert caught.value.exit_category == 10
    assert bytes(raw) == before
    assert effect_log == []


def test_minimum_reader_above_runtime_preserves_and_stops() -> None:
    from zagrosi_forge.install.contracts import (
        ForgeError,
        canonical_json_bytes,
        decode_persistent_record,
    )

    record = {
        "minimum_reader_version": "999.0.0",
        "schema_digest": "1" * 64,
        "schema_version": "1.0",
        "writer_version": "0.2.0",
    }
    record["record_digest"] = hashlib.sha256(canonical_json_bytes(record)).hexdigest()
    raw = canonical_json_bytes(record, final_newline=True)
    before = bytes(raw)
    with pytest.raises(ForgeError) as caught:
        decode_persistent_record(raw)
    assert caught.value.code == "record.reader_unsupported"
    assert caught.value.exit_category == 10
    assert raw == before


def test_known_persistent_record_rejects_noncanonical_bytes() -> None:
    from zagrosi_forge.install.contracts import (
        ForgeError,
        canonical_json_bytes,
        decode_persistent_record,
    )

    record = {
        "minimum_reader_version": "0.2.0",
        "schema_digest": "1" * 64,
        "schema_version": "1.0",
        "writer_version": "0.2.0",
    }
    record["record_digest"] = hashlib.sha256(canonical_json_bytes(record)).hexdigest()
    raw = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
    with pytest.raises(ForgeError) as caught:
        decode_persistent_record(raw)
    assert caught.value.code == "record.noncanonical"
    assert caught.value.exit_category == 10


def test_canonical_json_bounds_members_depth_bytes_and_dataclass_details() -> None:
    from zagrosi_forge.install.contracts import (
        Finding,
        ForgeError,
        canonical_json_bytes,
    )

    finding = Finding(
        code="metadata.invalid",
        severity="error",
        message="safe",
        subject="config:managed-marketplace",
        authority="metadata-schema",
        authority_version="1",
        remediation="safe",
        details={"count": 1},
    )
    assert b'"details":{"count":1}' in canonical_json_bytes(finding)

    too_many = {str(index): index for index in range(513)}
    too_deep: object = "leaf"
    for _ in range(33):
        too_deep = [too_deep]
    for rejected in (too_many, too_deep, "x" * (256 * 1024)):
        with pytest.raises(ForgeError) as caught:
            canonical_json_bytes(rejected)
        assert caught.value.code == "diagnostic.value_rejected"


def test_persistent_decode_bounds_and_recursively_freezes() -> None:
    from zagrosi_forge.install.contracts import (
        ForgeError,
        canonical_json_bytes,
        decode_persistent_record,
    )

    record: dict[str, object] = {
        "minimum_reader_version": "0.2.0",
        "payload": {"items": ["one"]},
        "schema_digest": "1" * 64,
        "schema_version": "1.0",
        "writer_version": "0.2.0",
    }
    record["record_digest"] = hashlib.sha256(canonical_json_bytes(record)).hexdigest()
    decoded = decode_persistent_record(canonical_json_bytes(record, final_newline=True))
    payload = cast(dict[str, object], decoded["payload"])
    with pytest.raises(TypeError):
        payload["other"] = "value"
    items = cast(list[str], payload["items"])
    with pytest.raises(TypeError):
        items[0] = "two"

    oversized_members: dict[str, object] = {
        "minimum_reader_version": "0.2.0",
        "payload": {str(index): index for index in range(507)},
        "schema_digest": "1" * 64,
        "schema_version": "1.0",
        "writer_version": "0.2.0",
    }
    digest_input = json.dumps(
        oversized_members,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    oversized_members["record_digest"] = hashlib.sha256(digest_input).hexdigest()
    raw = (
        json.dumps(
            oversized_members,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()
    with pytest.raises(ForgeError) as caught:
        decode_persistent_record(raw)
    assert caught.value.code == "record.limit_exceeded"

    recursively_hostile = ("[" * 10_000 + "0" + "]" * 10_000).encode()
    with pytest.raises(ForgeError) as caught:
        decode_persistent_record(recursively_hostile)
    assert caught.value.code == "record.limit_exceeded"
