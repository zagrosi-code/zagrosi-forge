"""Frozen Unit 12 privileged contract; never populated from user configuration."""

HANDOFF_REQUEST_SCHEMA = "unit12-privileged-darwin-apfs-gate-handoff-request-v1"


HANDOFF_RECEIPT_SCHEMA = "unit12-privileged-darwin-apfs-gate-handoff-receipt-v1"


HANDOFF_VERIFICATION_SCHEMA = "unit12-privileged-darwin-apfs-gate-handoff-verification-v1"


HANDOFF_RESULT_SCHEMA = "zagrosi-privileged-evidence-handoff-result-v1"


HANDOFF_ERROR_SCHEMA = "zagrosi-privileged-evidence-handoff-error-v1"


HANDOFF_PURPOSE = "unit12_privileged_darwin_apfs_gate_handoff"


HANDOFF_VERIFICATION_PURPOSE = "unit12_privileged_darwin_apfs_gate_handoff_verification"


HANDOFF_ERROR_PURPOSE = "zagrosi_privileged_evidence_handoff_error"


HANDOFF_REQUEST_FIELDS = {
    "schema",
    "purpose",
    "gate_id",
    "admission_state_sha256",
    "admission_pinner_sha256",
    "planning_tree_sha256",
    "detached_implementation_root_identity_digest",
    "implement_tool_sha256",
    "implement_skill_sha256",
    "implement_test_sha256",
    "self_digest",
}


HANDOFF_RECEIPT_FIELDS = {
    "schema",
    "purpose",
    "gate_id",
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
    "result_bytes",
    "result_mode",
    "result_uid",
    "result_gid",
    "result_nlink",
    "gate_command_sha256",
    "handoff_command_sha256",
    "protected_source_root_identity_digest",
    "source_commit",
    "source_tree_sha256",
    "implementation_source_sha256",
    "test_source_sha256",
    "result_finished_at",
    "verdict",
    "attestation_key_id",
    "self_digest",
    "signature_b64u",
}


HANDOFF_VERIFICATION_FIELDS = {
    "schema",
    "purpose",
    "gate_id",
    "handoff_request_final_wire_digest",
    "handoff_receipt_final_wire_digest",
    "admission_state_sha256",
    "admission_pinner_sha256",
    "planning_tree_sha256",
    "detached_implementation_root_identity_digest",
    "verdict",
}


HANDOFF_RESULT_FIELDS = {"schema", "section", "evidence_name", "evidence_path", "sha256", "size", "status"}


HANDOFF_ERROR_FIELDS = {"schema", "purpose", "section", "status", "closed_error_code"}


HANDOFF_CLOSED_ERROR_CODES = {
    "HANDOFF_PLATFORM_UNAVAILABLE": 3,
    "HANDOFF_FIXED_DEPENDENCY_UNAVAILABLE": 3,
    "HANDOFF_ROOT_UNAVAILABLE": 3,
    "HANDOFF_VERIFIER_UNAVAILABLE": 3,
    "HANDOFF_CALLER_REFUSED": 5,
    "HANDOFF_SECTION_NOT_READY": 5,
    "HANDOFF_AUTHORITY_INVALID": 5,
    "HANDOFF_ROOT_OUTPUT_INVALID": 5,
    "HANDOFF_VERIFIER_OUTPUT_INVALID": 5,
    "HANDOFF_EVIDENCE_CONFLICT": 5,
    "HANDOFF_INTERNAL_FAILURE": 5,
}


HANDOFF_REQUEST_CAP = 16 * 1024


HANDOFF_RECEIPT_CAP = 64 * 1024


HANDOFF_VERIFICATION_CAP = 4 * 1024


HANDOFF_STDERR_CAP = 64 * 1024


HANDOFF_ROOT = "/var/db/santander-unit12/dec075"


HANDOFF_HOST_PROVISIONING_RECEIPT = (
    "/usr/local/share/santander-unit12-prereqs/privileged-darwin-apfs-host-provisioning-receipt-v1.json"
)


HANDOFF_PREREQUISITE_RECEIPT = "/usr/local/share/santander-unit12-prereqs/prerequisite-receipt-v1.json"


HANDOFF_PYTHON = "/usr/local/libexec/santander-unit12-prereqs/python-3.12.13/bin/python3.12"


HANDOFF_SUDO = "/usr/bin/sudo"


HANDOFF_STAT = "/usr/bin/stat"


HANDOFF_ENV = {"LC_ALL": "C", "LANG": "C", "TZ": "UTC"}


HANDOFF_GIT = "/usr/local/libexec/santander-unit12-prereqs/git-2.50.1-apple-155"


HANDOFF_GIT_ENV = {
    "LC_ALL": "C",
    "LANG": "C",
    "TZ": "UTC",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}


HANDOFF_GIT_STATUS_ARGS = ("status", "--porcelain=v1", "-z", "--untracked-files=all")


HANDOFF_SECTION_CONTRACTS = {
    "S26": {
        "section": "section-26-publication-wire-and-decision-store",
        "gate_id": "s26_publication_store",
        "evidence_name": "s26_privileged_darwin_apfs_gate",
        "evidence_path": "evidence/s26-privileged-darwin-apfs-gate-handoff-receipt-v1.json",
        "host_input": f"{HANDOFF_ROOT}/s26-privileged-darwin-apfs-host-input-v1.json",
        "result": f"{HANDOFF_ROOT}/evidence/s26-privileged-darwin-apfs-gate-result-v1.json",
        "runner": "/usr/local/libexec/santander-unit12-gates/s26-privileged-darwin-apfs-gate-runner-v1.py",
        "runner_source": "scripts/cutover/_runtime_evidence_revocation_publication_store.py",
        "implementation_sources": (
            "scripts/cutover/_runtime_evidence_revocation_publication_store.py",
            "scripts/cutover/_runtime_evidence_revocation_publication_wire.py",
        ),
        "implementation_source_domain": b"unit12-s26-privileged-gate-implementation-source-set-v1\0",
        "test": "tests/cutover/test_runtime_evidence_revocation_publication_store.py",
        "gate_command_sha256": "sha256:2adb5c10c313b7bb758d539ffaf6313439cf562f7f0dad99ac3ed8ff705adc1d",
        "command_sha256": "sha256:3cc96ce9be563930630c151e0b854f140e3e2e2421c49b888d1751946f8d672b",
        "verifier_command_sha256": "sha256:c415905a53f5ad02d97336b65d21a6a31a469c21f2ae5745ddef74ed9de1c862",
    },
    "S28": {
        "section": "section-28-scoped-native-and-external-composition",
        "gate_id": "s28_publication_transport",
        "evidence_name": "s28_privileged_darwin_apfs_gate",
        "evidence_path": "evidence/s28-privileged-darwin-apfs-gate-handoff-receipt-v1.json",
        "host_input": f"{HANDOFF_ROOT}/s28-privileged-darwin-apfs-host-input-v1.json",
        "result": f"{HANDOFF_ROOT}/evidence/s28-privileged-darwin-apfs-gate-result-v1.json",
        "runner": "/usr/local/libexec/santander-unit12-gates/s28-privileged-darwin-apfs-gate-runner-v1.py",
        "runner_source": "scripts/cutover/_runtime_evidence_revocation_publication_transport.py",
        "implementation_sources": (
            "scripts/cutover/_runtime_evidence_revocation_github_native.py",
            "scripts/cutover/_runtime_evidence_revocation_publication_transport.py",
            "scripts/cutover/runtime_evidence_revocation_toolchain.py",
            "scripts/cutover/runtime_evidence_revocation_toolchain_native.py",
        ),
        "implementation_source_domain": b"unit12-s28-privileged-gate-implementation-source-set-v1\0",
        "test": "tests/cutover/test_runtime_evidence_revocation_publication_transport.py",
        "gate_command_sha256": "sha256:43db5942eeb65b2d69e306aa7086f04c346ce1fd453fa930c39d9e713de824c1",
        "command_sha256": "sha256:783c80921f7c9bb431fec5c0f1d77a538aa5761149987804a841800ece2f2c3c",
        "verifier_command_sha256": "sha256:3d4ac7e265610fca5106c8dedcf89564f7ee424376a60707db6590131d273e57",
    },
}


HANDOFF_FROZEN_RUNNER_CONTRACTS = {
    "s26_publication_store": {
        "runner": "/usr/local/libexec/santander-unit12-gates/s26-privileged-darwin-apfs-gate-runner-v1.py",
        "runner_source": "scripts/cutover/_runtime_evidence_revocation_publication_store.py",
        "gate_command_sha256": "sha256:2adb5c10c313b7bb758d539ffaf6313439cf562f7f0dad99ac3ed8ff705adc1d",
        "command_sha256": "sha256:3cc96ce9be563930630c151e0b854f140e3e2e2421c49b888d1751946f8d672b",
        "verifier_command_sha256": "sha256:c415905a53f5ad02d97336b65d21a6a31a469c21f2ae5745ddef74ed9de1c862",
    },
    "s28_publication_transport": {
        "runner": "/usr/local/libexec/santander-unit12-gates/s28-privileged-darwin-apfs-gate-runner-v1.py",
        "runner_source": "scripts/cutover/_runtime_evidence_revocation_publication_transport.py",
        "gate_command_sha256": "sha256:43db5942eeb65b2d69e306aa7086f04c346ce1fd453fa930c39d9e713de824c1",
        "command_sha256": "sha256:783c80921f7c9bb431fec5c0f1d77a538aa5761149987804a841800ece2f2c3c",
        "verifier_command_sha256": "sha256:3d4ac7e265610fca5106c8dedcf89564f7ee424376a60707db6590131d273e57",
    },
}


HANDOFF_CONTRACT_BY_SECTION = {
    contract["section"]: contract for contract in HANDOFF_SECTION_CONTRACTS.values()
}


def require_fixed_commands(actual: dict[str, str]) -> None:
    """Verify the host's selected commands against this source-bound adapter."""
    from .models import DetachedImplementationError

    expected = {
        "HANDOFF_SUDO": "/usr/bin/sudo",
        "HANDOFF_STAT": "/usr/bin/stat",
        "HANDOFF_PYTHON": "/usr/local/libexec/santander-unit12-prereqs/python-3.12.13/bin/python3.12",
        "HANDOFF_GIT": "/usr/local/libexec/santander-unit12-prereqs/git-2.50.1-apple-155",
    }
    if any(value != expected.get(name) for name, value in actual.items()):
        raise DetachedImplementationError(
            "handoff-command-drift",
            "A fixed privileged handoff executable path does not match its frozen literal.",
        )
