"""Forge detached contract."""

from __future__ import annotations

from pathlib import Path
import os

DETACHED_CONFIG_SCHEMA = "zagrosi-detached-implementation-config-v2"


DETACHED_STATE_SCHEMA = "zagrosi-detached-implementation-state-v2"


DETACHED_PROGRESS_SCHEMA = "zagrosi-detached-implementation-progress-v1"


DETACHED_SETUP_PREFIX_SCHEMA = "zagrosi-detached-implementation-setup-prefix-v2"


SECTION_PINNER_SCHEMA = "zagrosi-implementation-section-pinner-v2"


SECTION_RECORD_TRANSACTION_SCHEMA = "zagrosi-section-record-transaction-v1"


SECTION_RECORD_LOCK_PATH = "pinners/.record-section.lock"


SECTION_RECORD_TRANSACTION_DIR = "pinners/.record-section-transaction-v1"


SECTION_RECORD_TRANSACTION_PATH = f"{SECTION_RECORD_TRANSACTION_DIR}/transaction.json"


SECTION_RECORD_ROLLBACK_PATH = f"{SECTION_RECORD_TRANSACTION_DIR}/rollback.json"


SECTION_RECORD_STAGED_PINNER_TMP_PATH = f"{SECTION_RECORD_TRANSACTION_DIR}/pinner.tmp"


SECTION_RECORD_STAGED_PINNER_PATH = f"{SECTION_RECORD_TRANSACTION_DIR}/pinner.json"


DETACHED_GLOBAL_LOCK_PATH = Path(os.sep)


DETACHED_LOCK_TIMEOUT_SECONDS = 5.0


FINAL_ADMISSION_PINNER_SCHEMA = "dec075-final-pinner-receipt-v1"


ADMISSION_STATE_SCHEMA = "dec075-admission-state-v1"


DETACHED_JSON_CAP = 4 * 1024 * 1024


DETACHED_REVIEW_CAP = 8 * 1024 * 1024


IMPLEMENTATION_SOURCE_CAP = 16 * 1024 * 1024


FROZEN_PLANNING_FILE_CAP = 64 * 1024 * 1024


FROZEN_PLANNING_TREE_CAP = 512 * 1024 * 1024


FINAL_ADMISSION_PINNER_FIELDS = {"schema", "start", "end", "o_sha256", "verdict"}


ADMISSION_STATE_FIELDS = {"schema", "r_sha256", "p_sha256", "d_sha256", "a_sha256"}


DETACHED_TOP_LEVEL_DIRECTORIES = {"code_review", "evidence", "pinners"}


DETACHED_TOP_LEVEL_FILES = {
    "zagrosi_implement_config.json",
    "zagrosi_implement_state.json",
    "forge-progress.json",
}


DETACHED_TOP_LEVEL_ALLOWED = DETACHED_TOP_LEVEL_DIRECTORIES | DETACHED_TOP_LEVEL_FILES


DETACHED_ROOT_RECOVERABLE_TEMPS = {
    f".{name}.tmp" for name in DETACHED_TOP_LEVEL_FILES
} | {
    f".{name}.setup.tmp" for name in DETACHED_TOP_LEVEL_FILES
}


DETACHED_SETUP_PREFIX_FIELDS = {
    "schema",
    "slot",
    "planning_dir",
    "sections_dir",
    "target_dir",
    "target_root_identity_digest",
    "implementation_root",
    "planning_tree_sha256",
    "planning_file_count",
    "planning_total_bytes",
    "admission_pinner_path",
    "admission_pinner_sha256",
    "admission_pinner_size",
    "admission_state_sha256",
    "implement_tool_sha256",
    "implement_skill_sha256",
    "implement_test_sha256",
    "self_digest",
}


SECTION_RECORD_TRANSACTION_FIELDS = {
    "schema",
    "section",
    "base_state_sha256",
    "candidate_state_sha256",
    "prior_state_record",
    "state_record",
    "pinner_path",
    "pinner_file_sha256",
}


REQUIRED_PRIVILEGED_SECTION_EVIDENCE = {
    "section-26-publication-wire-and-decision-store": (
        "s26_privileged_darwin_apfs_gate",
        "evidence/s26-privileged-darwin-apfs-gate-handoff-receipt-v1.json",
    ),
    "section-28-scoped-native-and-external-composition": (
        "s28_privileged_darwin_apfs_gate",
        "evidence/s28-privileged-darwin-apfs-gate-handoff-receipt-v1.json",
    ),
}


def __getattr__(name: str):
    """Keep legacy contract imports lazy while isolating privileged project policy."""
    if name.startswith("HANDOFF_"):
        from . import unit12_adapter

        return getattr(unit12_adapter, name)
    raise AttributeError(name)


IMPLEMENTATION_SOURCE_NAMES = ("tool", "skill", "test")


DETACHED_CONTRACT_RELATIVE_PATH = Path("references/detached-frozen.md")


DETACHED_CONTRACT_SHA256 = "sha256:f1e117907da4357d496c17adc4bde647b8cb1ab96144b3f8e6347f1009dada46"


DETACHED_CONFIG_FIELDS = {
    "schema",
    "mode",
    "planning_dir",
    "sections_dir",
    "target_dir",
    "target_root_identity_digest",
    "implementation_root",
    "state_path",
    "progress_path",
    "reviews_dir",
    "evidence_dir",
    "pinners_dir",
    "planning_tree_sha256",
    "planning_file_count",
    "planning_total_bytes",
    "admission_pinner_path",
    "admission_pinner_sha256",
    "admission_pinner_size",
    "admission_state_sha256",
    "detached_implementation_root_identity_digest",
    "implement_tool_path",
    "implement_tool_sha256",
    "implement_tool_size",
    "implement_skill_path",
    "implement_skill_sha256",
    "implement_skill_size",
    "implement_test_path",
    "implement_test_sha256",
    "implement_test_size",
    "runtime",
    "test_command",
}


DETACHED_STATE_FIELDS = {
    "schema",
    "mode",
    "planning_tree_sha256",
    "admission_pinner_sha256",
    "admission_state_sha256",
    "detached_implementation_root_identity_digest",
    "target_root_identity_digest",
    "created_at",
    "completed_sections",
}


DETACHED_PROGRESS_FIELDS = {
    "schema",
    "mode",
    "planning_tree_sha256",
    "admission_pinner_sha256",
    "created_at",
    "events",
}


SECTION_PINNER_FIELDS = {
    "schema",
    "section",
    "planning_tree_sha256",
    "admission_pinner_sha256",
    "admission_state_sha256",
    "detached_implementation_root_identity_digest",
    "target_root_identity_digest",
    "implement_tool_sha256",
    "implement_skill_sha256",
    "implement_test_sha256",
    "completed_at",
    "commit",
    "commit_status",
    "notes",
    "files_changed",
    "test_files",
    "review_artifacts",
    "evidence_rows",
    "verification",
    "predecessor_pinners",
}
