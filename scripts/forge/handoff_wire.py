"""Forge handoff wire."""

from __future__ import annotations

from typing import Any
import base64
import binascii
import hashlib
import json
import re
import unicodedata

from . import detached_contract as _detached_contract
from . import models as _models

def canonical_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def _nfc_json_value(value: Any) -> Any:
    if type(value) is str:
        return unicodedata.normalize("NFC", value)
    if type(value) is list:
        return [_nfc_json_value(item) for item in value]
    if type(value) is dict:
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise _models.DetachedImplementationError(
                    "invalid-handoff-envelope",
                    "Handoff canonical JSON object keys must be strings.",
                )
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise _models.DetachedImplementationError(
                    "invalid-handoff-envelope",
                    "Handoff canonical JSON contains colliding NFC keys.",
                )
            normalized[normalized_key] = _nfc_json_value(item)
        return normalized
    return value


def handoff_canonical_json_body(payload: Any) -> bytes:
    return json.dumps(
        _nfc_json_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def handoff_canonical_json_bytes(payload: Any) -> bytes:
    return handoff_canonical_json_body(payload) + b"\n"


def reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant is forbidden: {value}")


def sha256_digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def domain_sha256(domain: bytes, raw: bytes) -> str:
    digest = hashlib.sha256(domain)
    digest.update(raw)
    return "sha256:" + digest.hexdigest()


def parse_canonical_object_bytes(raw: bytes, *, cap: int, label: str) -> dict[str, Any]:
    if len(raw) > cap:
        raise _models.DetachedImplementationError(
            "invalid-handoff-envelope",
            f"{label} exceeds its {cap}-byte cap.",
            size=len(raw),
        )
    try:
        payload = json.loads(raw.decode("utf-8"), parse_constant=reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _models.DetachedImplementationError(
            "invalid-handoff-envelope",
            f"{label} is not strict UTF-8 JSON.",
        ) from exc
    if type(payload) is not dict or handoff_canonical_json_bytes(payload) != raw:
        raise _models.DetachedImplementationError(
            "invalid-handoff-envelope",
            f"{label} must be one compact sorted-key canonical JSON object with one terminal LF.",
        )
    return payload


def strict_b64u_decode(value: Any, *, expected_bytes: int | None = None) -> bytes:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise _models.DetachedImplementationError("invalid-handoff-envelope", "Handoff base64url value is not canonical.")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))
    except (ValueError, binascii.Error) as exc:
        raise _models.DetachedImplementationError("invalid-handoff-envelope", "Handoff base64url value is invalid.") from exc
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise _models.DetachedImplementationError("invalid-handoff-envelope", "Handoff base64url value is not canonical.")
    if expected_bytes is not None and len(decoded) != expected_bytes:
        raise _models.DetachedImplementationError(
            "invalid-handoff-envelope",
            f"Handoff base64url value must decode to exactly {expected_bytes} bytes.",
        )
    return decoded


def framed_command_sha256(domain: bytes, argv: list[str]) -> str:
    digest = hashlib.sha256(domain)
    digest.update(len(argv).to_bytes(4, "big"))
    for argument in argv:
        raw = argument.encode("utf-8", errors="strict")
        digest.update(len(raw).to_bytes(4, "big"))
        digest.update(raw)
    return "sha256:" + digest.hexdigest()


def handoff_root_argv(contract: dict[str, Any]) -> list[str]:
    return [
        _detached_contract.HANDOFF_SUDO,
        "-n",
        "--",
        _detached_contract.HANDOFF_PYTHON,
        "-I",
        "-B",
        contract["runner"],
        "--privileged-darwin-apfs-handoff-root",
        "--host-provisioning-receipt",
        _detached_contract.HANDOFF_HOST_PROVISIONING_RECEIPT,
        "--host-input",
        contract["host_input"],
        "--result",
        contract["result"],
        "--request-fd",
        "0",
        "--receipt-fd",
        "1",
    ]


def handoff_verifier_argv(contract: dict[str, Any]) -> list[str]:
    return [
        _detached_contract.HANDOFF_PYTHON,
        "-I",
        "-B",
        contract["runner"],
        "--verify-privileged-darwin-apfs-handoff",
        "--host-provisioning-receipt",
        _detached_contract.HANDOFF_HOST_PROVISIONING_RECEIPT,
        "--framed-input-fd",
        "0",
    ]


def verify_handoff_command_identities(contract: dict[str, Any]) -> None:
    frozen_runner = _detached_contract.HANDOFF_FROZEN_RUNNER_CONTRACTS.get(contract.get("gate_id"))
    if frozen_runner is None or any(contract.get(field) != value for field, value in frozen_runner.items()):
        raise _models.DetachedImplementationError(
            "handoff-command-drift",
            "Fixed privileged gate runner selection does not match its frozen contract.",
        )
    root_argv = handoff_root_argv(contract)
    verifier_argv = handoff_verifier_argv(contract)
    if len(root_argv) != 18 or framed_command_sha256(
        b"unit12-privileged-gate-handoff-command-v1\0", root_argv
    ) != contract["command_sha256"]:
        raise _models.DetachedImplementationError(
            "handoff-command-drift",
            "Fixed privileged handoff command identity does not match its frozen contract.",
        )
    if len(verifier_argv) != 9 or framed_command_sha256(
        b"unit12-privileged-gate-handoff-verifier-command-v1\0", verifier_argv
    ) != contract["verifier_command_sha256"]:
        raise _models.DetachedImplementationError(
            "handoff-command-drift",
            "Fixed unprivileged handoff verifier command identity does not match its frozen contract.",
        )


def build_handoff_request(config: dict[str, Any], contract: dict[str, Any]) -> tuple[dict[str, Any], bytes, str]:
    request_without_self = {
        "schema": _detached_contract.HANDOFF_REQUEST_SCHEMA,
        "purpose": _detached_contract.HANDOFF_PURPOSE,
        "gate_id": contract["gate_id"],
        "admission_state_sha256": config["admission_state_sha256"],
        "admission_pinner_sha256": config["admission_pinner_sha256"],
        "planning_tree_sha256": config["planning_tree_sha256"],
        "detached_implementation_root_identity_digest": config[
            "detached_implementation_root_identity_digest"
        ],
        "implement_tool_sha256": config["implement_tool_sha256"],
        "implement_skill_sha256": config["implement_skill_sha256"],
        "implement_test_sha256": config["implement_test_sha256"],
    }
    request = {
        **request_without_self,
        "self_digest": domain_sha256(
            b"unit12-privileged-darwin-apfs-gate-handoff-request-v1-self\0",
            handoff_canonical_json_body(request_without_self),
        ),
    }
    if set(request) != _detached_contract.HANDOFF_REQUEST_FIELDS:
        raise _models.DetachedImplementationError("invalid-handoff-request", "Handoff request fields are not exact.")
    raw = handoff_canonical_json_bytes(request)
    if len(raw) > _detached_contract.HANDOFF_REQUEST_CAP:
        raise _models.DetachedImplementationError("invalid-handoff-request", "Handoff request exceeds its fixed cap.")
    final_wire_digest = domain_sha256(
        b"unit12-privileged-darwin-apfs-gate-handoff-request-v1-final-wire\0",
        handoff_canonical_json_body(request),
    )
    return request, raw, final_wire_digest


def parse_handoff_receipt(
    raw: bytes,
    config: dict[str, Any],
    contract: dict[str, Any],
    request_final_wire_digest: str,
) -> tuple[dict[str, Any], str]:
    receipt = parse_canonical_object_bytes(raw, cap=_detached_contract.HANDOFF_RECEIPT_CAP, label="Handoff receipt")
    if set(receipt) != _detached_contract.HANDOFF_RECEIPT_FIELDS:
        raise _models.DetachedImplementationError(
            "invalid-handoff-receipt",
            "Handoff receipt fields do not match the frozen schema.",
            missing_fields=sorted(_detached_contract.HANDOFF_RECEIPT_FIELDS - set(receipt)),
            extra_fields=sorted(set(receipt) - _detached_contract.HANDOFF_RECEIPT_FIELDS),
        )
    expected_echoes = {
        "schema": _detached_contract.HANDOFF_RECEIPT_SCHEMA,
        "purpose": _detached_contract.HANDOFF_PURPOSE,
        "gate_id": contract["gate_id"],
        "handoff_request_final_wire_digest": request_final_wire_digest,
        "admission_state_sha256": config["admission_state_sha256"],
        "admission_pinner_sha256": config["admission_pinner_sha256"],
        "planning_tree_sha256": config["planning_tree_sha256"],
        "detached_implementation_root_identity_digest": config[
            "detached_implementation_root_identity_digest"
        ],
        "implement_tool_sha256": config["implement_tool_sha256"],
        "implement_skill_sha256": config["implement_skill_sha256"],
        "implement_test_sha256": config["implement_test_sha256"],
        "gate_command_sha256": contract["gate_command_sha256"],
        "handoff_command_sha256": contract["command_sha256"],
        "verdict": "PASS",
    }
    mismatches = {
        key: {"expected": value, "actual": receipt.get(key)}
        for key, value in expected_echoes.items()
        if type(receipt.get(key)) is not str or receipt.get(key) != value
    }
    if mismatches:
        raise _models.DetachedImplementationError(
            "handoff-receipt-drift",
            "Handoff receipt does not echo the current request and detached config exactly.",
            field_mismatches=mismatches,
        )
    integer_expectations = {
        "result_mode": 0o600,
        "result_uid": 0,
        "result_gid": 0,
        "result_nlink": 1,
    }
    if any(type(receipt.get(key)) is not int or receipt[key] != value for key, value in integer_expectations.items()):
        raise _models.DetachedImplementationError(
            "invalid-handoff-receipt",
            "Handoff receipt raw-result ownership metadata is invalid.",
        )
    if (
        type(receipt.get("result_bytes")) is not int
        or receipt["result_bytes"] <= 0
        or receipt["result_bytes"] > _detached_contract.HANDOFF_RECEIPT_CAP
    ):
        raise _models.DetachedImplementationError(
            "invalid-handoff-receipt",
            "Handoff receipt raw-result byte count is invalid.",
        )
    digest_fields = {
        "handoff_request_final_wire_digest",
        "admission_state_sha256",
        "admission_pinner_sha256",
        "planning_tree_sha256",
        "detached_implementation_root_identity_digest",
        "privileged_evidence_root_identity_digest",
        "implement_tool_sha256",
        "implement_skill_sha256",
        "implement_test_sha256",
        "host_provisioning_receipt_final_wire_digest",
        "host_input_final_wire_digest",
        "result_final_wire_digest",
        "result_sha256",
        "gate_command_sha256",
        "handoff_command_sha256",
        "protected_source_root_identity_digest",
        "source_tree_sha256",
        "implementation_source_sha256",
        "test_source_sha256",
        "self_digest",
    }
    if any(
        type(receipt.get(field)) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", receipt[field])
        for field in digest_fields
    ):
        raise _models.DetachedImplementationError(
            "invalid-handoff-receipt",
            "Handoff receipt contains an invalid lowercase SHA-256 field.",
        )
    for field in ("source_commit", "result_finished_at", "attestation_key_id"):
        if type(receipt.get(field)) is not str or not receipt[field] or len(receipt[field].encode("utf-8")) > 512:
            raise _models.DetachedImplementationError(
                "invalid-handoff-receipt",
                f"Handoff receipt field is invalid: {field}",
            )
    strict_b64u_decode(receipt["signature_b64u"], expected_bytes=64)
    receipt_without_self_and_signature = {
        key: value for key, value in receipt.items() if key not in {"self_digest", "signature_b64u"}
    }
    expected_self_digest = domain_sha256(
        b"unit12-privileged-darwin-apfs-gate-handoff-receipt-v1-self\0",
        handoff_canonical_json_body(receipt_without_self_and_signature),
    )
    if receipt["self_digest"] != expected_self_digest:
        raise _models.DetachedImplementationError(
            "invalid-handoff-receipt",
            "Handoff receipt self digest is invalid.",
        )
    final_wire_digest = domain_sha256(
        b"unit12-privileged-darwin-apfs-gate-handoff-receipt-v1-final-wire\0",
        handoff_canonical_json_body(receipt),
    )
    return receipt, final_wire_digest


def parse_handoff_verification(
    raw: bytes,
    config: dict[str, Any],
    contract: dict[str, Any],
    request_final_wire_digest: str,
    receipt_final_wire_digest: str,
) -> dict[str, Any]:
    verification = parse_canonical_object_bytes(
        raw,
        cap=_detached_contract.HANDOFF_VERIFICATION_CAP,
        label="Handoff verification",
    )
    if set(verification) != _detached_contract.HANDOFF_VERIFICATION_FIELDS:
        raise _models.DetachedImplementationError(
            "invalid-handoff-verification",
            "Handoff verification fields do not match the frozen schema.",
        )
    expected = {
        "schema": _detached_contract.HANDOFF_VERIFICATION_SCHEMA,
        "purpose": _detached_contract.HANDOFF_VERIFICATION_PURPOSE,
        "gate_id": contract["gate_id"],
        "handoff_request_final_wire_digest": request_final_wire_digest,
        "handoff_receipt_final_wire_digest": receipt_final_wire_digest,
        "admission_state_sha256": config["admission_state_sha256"],
        "admission_pinner_sha256": config["admission_pinner_sha256"],
        "planning_tree_sha256": config["planning_tree_sha256"],
        "detached_implementation_root_identity_digest": config[
            "detached_implementation_root_identity_digest"
        ],
        "verdict": "PASS",
    }
    if any(type(verification.get(key)) is not str or verification.get(key) != value for key, value in expected.items()):
        raise _models.DetachedImplementationError(
            "handoff-verification-drift",
            "Handoff verifier did not return the exact current PASS projection.",
        )
    return verification


def require_exact_fields(payload: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(payload)
    if actual != expected:
        raise _models.DetachedImplementationError(
            "invalid-detached-schema",
            f"{label} fields do not match the frozen schema.",
            missing_fields=sorted(expected - actual),
            extra_fields=sorted(actual - expected),
        )
