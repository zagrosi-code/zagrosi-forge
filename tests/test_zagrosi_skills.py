from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zagrosi_skills.py"
IMPLEMENTATION_SOURCE_RELATIVE_PATHS = {
    "tool": Path("scripts/zagrosi_skills.py"),
    "skill": Path("skills/zagrosi-implement/SKILL.md"),
    "test": Path("tests/test_zagrosi_skills.py"),
}


def run_cmd(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(result.stdout)


def run_raw(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


def run_script_raw(
    script: Path,
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=cwd or script.parents[1],
        env=env,
        text=True,
        capture_output=True,
    )


def run_text(*args: str, cwd: Path | None = None) -> str:
    result = run_raw(*args, cwd=cwd)
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout


def load_zagrosi_module(script: Path = SCRIPT):
    import importlib.util

    module_name = f"zagrosi_skills_under_test_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(module_name, script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_required_plan_artifacts(planning_dir: Path) -> None:
    def write_missing(relative: str, content: str) -> None:
        path = planning_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(content)

    write_missing(
        "codex-research.md",
        "# Research\n\nVerified current state with `rg` and `uv run pytest`. Existing files include `scripts/zagrosi_skills.py`, "
        "`tests/test_zagrosi_skills.py`, and `skills/zagrosi-implement/SKILL.md`.\n",
    )
    write_missing(
        "codex-evidence.md",
        "# Codebase Evidence\n\nRuntime: `pyproject.toml`. Source files: `scripts/zagrosi_skills.py`. "
        "Tests: `tests/test_zagrosi_skills.py`. Commands: `uv run pytest`.\n",
    )
    write_missing(
        "codex-interview.md",
        "interview_mode: skipped_with_reason\n"
        "skip_reason: Test fixture has complete approved requirements and no product ambiguity.\n",
    )
    write_missing(
        "codex-spec.md",
        "# Spec\n\nREQ-001: Implement the planned Forge behavior with tests, traceability, and documentation.\n",
    )
    write_missing(
        "codex-plan.md",
        "# Plan\n\nREQ-001 updates `scripts/zagrosi_skills.py` and verifies with `tests/test_zagrosi_skills.py`. "
        "Architecture keeps workflow policy in Forge helpers. Rollback is reverting the helper change.\n",
    )
    write_missing(
        "codex-integration-notes.md",
        "# Review Integration\n\nAccepted review: enforce process completeness before implementation and keep traceability explicit.\n",
    )
    write_missing(
        "codex-plan-tdd.md",
        "# TDD Plan\n\nREQ-001: `test_implement_setup_and_record` verifies implementation recording with `uv run pytest`.\n",
    )
    write_missing(
        "decisions.md",
        "# Decision Log\n\n"
        "| ID | Date | Decision | Alternatives | Rationale | Impact |\n"
        "|----|------|----------|--------------|-----------|--------|\n"
        "| DEC-001 | Test | Enforce Forge process artifacts. | Rely on operator memory. | Durable records are required. | Implementation waits for planning artifacts. |\n",
    )
    write_missing(
        "risk-register.md",
        "# Risk Register\n\n"
        "| ID | Risk | Severity | Likelihood | Mitigation | Section | Verification |\n"
        "|----|------|----------|------------|------------|---------|--------------|\n"
        "| RISK-001 | Process artifacts are missing. | High | Medium | Gate implementation setup. | section-01-foundation | `uv run pytest`. |\n",
    )
    write_missing(
        "traceability.md",
        "# Traceability Matrix\n\n"
        "| Requirement | Plan Coverage | Section Coverage | Test Coverage | Status |\n"
        "|-------------|---------------|------------------|---------------|--------|\n"
        "| REQ-001 | `codex-plan.md` | `section-01-foundation.md` | `test_implement_setup_and_record` | Planned |\n",
    )
    write_missing("quality-gates.md", "# Quality Gates\n\nRun `uv run pytest`, `lint-plan-artifacts`, and `traceability`.\n")
    write_missing("reviews/process.md", "# Process Review\n\nNo blocking findings. The plan names files, tests, risks, and verification.\n")


def write_single_section_fixture(planning_dir: Path, section: str = "section-01-foundation") -> Path:
    sections = planning_dir / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        f"{section}\n"
        "END_MANIFEST -->\n"
    )
    (sections / f"{section}.md").write_text(
        "# Section\n\n"
        "REQ-001 changes `scripts/zagrosi_skills.py` and `tests/test_zagrosi_skills.py`.\n"
        "Tests first, expected failure, implementation, acceptance, rollback, and verification.\n"
    )
    write_required_plan_artifacts(planning_dir)
    review_dir = planning_dir / "implementation" / "code_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / f"{section}-diff.md").write_text("# Diff\n\nChanged helper and tests.\n")
    (review_dir / f"{section}-review.md").write_text("# Review\n\nNo blocking findings.\n")
    return sections


def write_non_topological_section_fixture(planning_dir: Path) -> Path:
    sections = planning_dir / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-foundation\n"
        "section-02-api\n"
        "section-03-storage\n"
        "END_MANIFEST -->\n\n"
        "# Sections\n\n"
        "## Dependency Graph\n\n"
        "| Section | Depends on |\n"
        "|---|---|\n"
        "| section-01-foundation | section-03-storage |\n"
        "| section-02-api | section-01-foundation |\n"
        "| section-03-storage | none |\n"
    )
    for section in ("section-01-foundation", "section-02-api", "section-03-storage"):
        (sections / f"{section}.md").write_text(
            f"# {section}\n\nTests first, expected failure, implementation, acceptance, rollback, and verification.\n"
        )
    write_required_plan_artifacts(planning_dir)
    return sections


def planning_tree_snapshot(planning_dir: Path) -> list[tuple[str, int, bytes | None]]:
    snapshot: list[tuple[str, int, bytes | None]] = []
    for path in sorted([planning_dir, *planning_dir.rglob("*")], key=lambda item: str(item.relative_to(planning_dir))):
        relative = "." if path == planning_dir else path.relative_to(planning_dir).as_posix()
        file_stat = path.lstat()
        snapshot.append((relative, file_stat.st_mode, path.read_bytes() if path.is_file() else None))
    return snapshot


def tree_bytes_metadata_snapshot(root: Path) -> list[tuple[str, int, int, int, int, int, int, bytes | None]]:
    snapshot: list[tuple[str, int, int, int, int, int, int, bytes | None]] = []
    for path in sorted([root, *root.rglob("*")], key=lambda item: str(item.relative_to(root))):
        relative = "." if path == root else path.relative_to(root).as_posix()
        observed = path.lstat()
        snapshot.append(
            (
                relative,
                observed.st_dev,
                observed.st_ino,
                observed.st_mode,
                observed.st_uid,
                observed.st_gid,
                observed.st_nlink,
                path.read_bytes() if path.is_file() else None,
            )
        )
    return snapshot


def assert_canonical_json_file(path: Path) -> dict:
    raw = path.read_bytes()
    payload = json.loads(raw)
    assert raw == (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    file_stat = path.stat()
    assert file_stat.st_nlink == 1
    assert file_stat.st_mode & 0o777 == 0o600
    return payload


def planning_section_digest_for_pinner(planning_dir: Path) -> str:
    index_text = (planning_dir / "sections" / "index.md").read_text()
    match = re.search(r"<!--\s*SECTION_MANIFEST\s*\n(.*?)\nEND_MANIFEST\s*-->", index_text, re.S)
    assert match
    sections = [line.strip() for line in match.group(1).splitlines() if line.strip() and not line.lstrip().startswith("#")]
    digest = hashlib.sha256()
    for section in sections:
        relative = f"sections/{section}.md"
        path_bytes = relative.encode("utf-8")
        body = (planning_dir / relative).read_bytes()
        digest.update(len(path_bytes).to_bytes(4, "big"))
        digest.update(path_bytes)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return "sha256:" + digest.hexdigest()


def admission_state_for_pinner(planning_dir: Path) -> dict:
    r_sha256 = "sha256:" + "11" * 32
    p_sha256 = "sha256:" + "22" * 32
    d_sha256 = planning_section_digest_for_pinner(planning_dir)
    a_digest = hashlib.sha256(b"dec075-a-v1\0")
    for value in (r_sha256, p_sha256, d_sha256):
        a_digest.update(bytes.fromhex(value.removeprefix("sha256:")))
    return {
        "schema": "dec075-admission-state-v1",
        "r_sha256": r_sha256,
        "p_sha256": p_sha256,
        "d_sha256": d_sha256,
        "a_sha256": "sha256:" + a_digest.hexdigest(),
    }


def admission_pinner_payload(planning_dir: Path, verdict: str = "PASS") -> dict:
    state = admission_state_for_pinner(planning_dir)
    return {
        "schema": "dec075-final-pinner-receipt-v1",
        "start": state,
        "end": dict(state),
        "o_sha256": "sha256:" + "33" * 32,
        "verdict": verdict,
    }


def write_test_admission_pinner(
    path: Path,
    authority: str = "PASS",
    *,
    planning_dir: Path | None = None,
    payload: dict | None = None,
) -> Path:
    planning = planning_dir or path.parent / "planning"
    payload = payload or admission_pinner_payload(planning, verdict=authority)
    path.write_bytes((json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
    path.chmod(0o600)
    return path


def replace_file(path: Path, raw: bytes, *, mode: int | None = None) -> None:
    replacement = path.with_name(f".{path.name}.replacement")
    replacement.write_bytes(raw)
    replacement.chmod(mode if mode is not None else path.stat().st_mode & 0o777)
    os.replace(replacement, path)


def file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json_bytes_for_test(payload: dict) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def pinner_state_record_for_test(pinner: dict, raw: bytes) -> dict:
    file_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    section = pinner["section"]
    return {
        "completed_at": pinner["completed_at"],
        "commit": pinner["commit"],
        "commit_status": pinner["commit_status"],
        "notes": pinner["notes"],
        "files_changed": pinner["files_changed"],
        "test_files": pinner["test_files"],
        "review_artifacts": pinner["review_artifacts"],
        "evidence_rows": pinner["evidence_rows"],
        "verification": pinner["verification"],
        "pinner_path": f"pinners/{section}-{file_digest.removeprefix('sha256:')}.json",
        "pinner_file_sha256": file_digest,
    }


def domain_sha256_for_test(domain: bytes, raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(domain + raw).hexdigest()


def b64u_for_test(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def protected_source_observation_for_test(module):
    filler = "sha256:" + "ab" * 32
    return module.ProtectedSourceObservation(
        protected_source_root_identity_digest=filler,
        source_commit="0123456789abcdef0123456789abcdef01234567",
        source_tree_sha256=filler,
        implementation_source_sha256=filler,
        test_source_sha256=filler,
    )


def handoff_receipt_for_test(
    module,
    config: dict,
    contract: dict,
    request_raw: bytes,
    source_observation=None,
) -> bytes:
    request_final_wire_digest = domain_sha256_for_test(
        b"unit12-privileged-darwin-apfs-gate-handoff-request-v1-final-wire\0",
        request_raw[:-1],
    )
    filler = "sha256:" + "ab" * 32
    source_observation = source_observation or protected_source_observation_for_test(module)
    receipt_without_self_and_signature = {
        "schema": module.HANDOFF_RECEIPT_SCHEMA,
        "purpose": module.HANDOFF_PURPOSE,
        "gate_id": contract["gate_id"],
        "handoff_request_final_wire_digest": request_final_wire_digest,
        "admission_state_sha256": config["admission_state_sha256"],
        "admission_pinner_sha256": config["admission_pinner_sha256"],
        "planning_tree_sha256": config["planning_tree_sha256"],
        "detached_implementation_root_identity_digest": config[
            "detached_implementation_root_identity_digest"
        ],
        "privileged_evidence_root_identity_digest": filler,
        "implement_tool_sha256": config["implement_tool_sha256"],
        "implement_skill_sha256": config["implement_skill_sha256"],
        "implement_test_sha256": config["implement_test_sha256"],
        "host_provisioning_receipt_final_wire_digest": filler,
        "host_input_final_wire_digest": filler,
        "result_final_wire_digest": filler,
        "result_sha256": filler,
        "result_bytes": 123,
        "result_mode": 0o600,
        "result_uid": 0,
        "result_gid": 0,
        "result_nlink": 1,
        "gate_command_sha256": contract["gate_command_sha256"],
        "handoff_command_sha256": contract["command_sha256"],
        "protected_source_root_identity_digest": source_observation.protected_source_root_identity_digest,
        "source_commit": source_observation.source_commit,
        "source_tree_sha256": source_observation.source_tree_sha256,
        "implementation_source_sha256": source_observation.implementation_source_sha256,
        "test_source_sha256": source_observation.test_source_sha256,
        "result_finished_at": "2026-08-21T12:00:00Z",
        "verdict": "PASS",
        "attestation_key_id": "unit12-test-attestation-key",
    }
    receipt = {
        **receipt_without_self_and_signature,
        "self_digest": domain_sha256_for_test(
            b"unit12-privileged-darwin-apfs-gate-handoff-receipt-v1-self\0",
            canonical_json_bytes_for_test(receipt_without_self_and_signature)[:-1],
        ),
        "signature_b64u": b64u_for_test(bytes(range(64))),
    }
    assert set(receipt) == module.HANDOFF_RECEIPT_FIELDS
    return canonical_json_bytes_for_test(receipt)


def handoff_verification_for_test(
    module,
    config: dict,
    contract: dict,
    request_raw: bytes,
    receipt_raw: bytes,
) -> bytes:
    return canonical_json_bytes_for_test(
        {
            "schema": module.HANDOFF_VERIFICATION_SCHEMA,
            "purpose": module.HANDOFF_VERIFICATION_PURPOSE,
            "gate_id": contract["gate_id"],
            "handoff_request_final_wire_digest": domain_sha256_for_test(
                b"unit12-privileged-darwin-apfs-gate-handoff-request-v1-final-wire\0",
                request_raw[:-1],
            ),
            "handoff_receipt_final_wire_digest": domain_sha256_for_test(
                b"unit12-privileged-darwin-apfs-gate-handoff-receipt-v1-final-wire\0",
                receipt_raw[:-1],
            ),
            "admission_state_sha256": config["admission_state_sha256"],
            "admission_pinner_sha256": config["admission_pinner_sha256"],
            "planning_tree_sha256": config["planning_tree_sha256"],
            "detached_implementation_root_identity_digest": config[
                "detached_implementation_root_identity_digest"
            ],
            "verdict": "PASS",
        }
    )


def test_handoff_canonical_json_uses_nfc_cj0_for_digests_and_lf_only_for_wire() -> None:
    module = load_zagrosi_module()
    payload = {"b": "e\u0301", "a": 1}
    expected_body = b'{"a":1,"b":"\xc3\xa9"}'

    assert module.handoff_canonical_json_body(payload) == expected_body
    assert module.handoff_canonical_json_bytes(payload) == expected_body + b"\n"
    assert module.domain_sha256(
        b"unit12-privileged-darwin-apfs-gate-handoff-request-v1-self\0",
        module.handoff_canonical_json_body(payload),
    ) == "sha256:1e3c3029b017c7ce4e9a64b2d5bed91dfa261e203b918776080420ff73f4e778"
    assert module.domain_sha256(
        b"unit12-privileged-darwin-apfs-gate-handoff-request-v1-final-wire\0",
        module.handoff_canonical_json_body(payload),
    ) == "sha256:ace49ee7e45ee0b2fe30ad19c96159384e5bc981aa809b9405fc9528fa766d4d"
    with pytest.raises(module.DetachedImplementationError, match="canonical JSON"):
        module.parse_canonical_object_bytes(
            b'{"a":1,"b":"e\xcc\x81"}\n',
            cap=4096,
            label="NFD mutant",
        )


def test_detached_root_identity_digest_vector_uses_cj0_without_lf() -> None:
    identity = {"device": 1, "gid": 20, "inode": 2, "link_count": 5, "mode": 448, "uid": 501}
    body = b'{"device":1,"gid":20,"inode":2,"link_count":5,"mode":448,"uid":501}'
    domain = b"zagrosi-detached-implementation-root-identity-v1\0"

    assert domain_sha256_for_test(domain, body) == (
        "sha256:f695b6afe7f1c9d246cb36e9627670d5a0940f2da9367aefddcaf13e7eee3477"
    )
    assert domain_sha256_for_test(domain, body + b"\n") == (
        "sha256:b8869a2672c63c8cb426438545381422b308da480da457ce0d5a37f9e9d55545"
    )
    assert domain_sha256_for_test(domain, body) != domain_sha256_for_test(domain, body + b"\n")
    assert json.dumps(identity, sort_keys=True, separators=(",", ":")).encode() == body


def test_target_root_identity_digest_vector_uses_exact_domain_and_cj0_without_lf() -> None:
    identity = {"device": 1, "gid": 20, "inode": 2, "link_count": 5, "mode": 448, "uid": 501}
    body = b'{"device":1,"gid":20,"inode":2,"link_count":5,"mode":448,"uid":501}'
    domain = b"zagrosi-detached-target-root-identity-v1\0"

    assert domain_sha256_for_test(domain, body) == (
        "sha256:659dd7b561a487f0f94dc7bd72565f376b12bc8c96010550abc8ddc64c94401b"
    )
    assert domain_sha256_for_test(domain, body + b"\n") == (
        "sha256:fca3d61843d599c4b8330b6c685e2a6f74a320181008ca97ed6b71229955e6a5"
    )
    assert domain_sha256_for_test(domain, body) != domain_sha256_for_test(domain, body + b"\n")
    assert json.dumps(identity, sort_keys=True, separators=(",", ":")).encode() == body


def test_linux_process_group_probe_treats_zombie_only_group_as_inactive(
    tmp_path: Path,
) -> None:
    module = load_zagrosi_module()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()

    def write_stat(pid: int, state: str, process_group: int) -> None:
        process_dir = proc_root / str(pid)
        process_dir.mkdir(exist_ok=True)
        (process_dir / "stat").write_text(
            f"{pid} (child {pid}) {state} 1 {process_group} {process_group} 0\n"
        )
        task_dir = process_dir / "task" / str(pid)
        task_dir.mkdir(parents=True)
        (task_dir / "stat").write_text(
            f"{pid} (child {pid}) {state} 1 {process_group} {process_group} 0\n"
        )

    assert module._linux_process_group_has_live_members(4321, proc_root) is None

    write_stat(100, "S", 9999)

    assert module._linux_process_group_has_live_members(4321, proc_root) is None

    write_stat(101, "Z", 4321)
    write_stat(102, "Z", 4321)

    assert module._linux_process_group_has_live_members(4321, proc_root) is False

    malformed_dir = proc_root / "104"
    malformed_dir.mkdir()
    (malformed_dir / "stat").write_bytes(b"malformed\n")

    assert module._linux_process_group_has_live_members(4321, proc_root) is None

    write_stat(103, "S", 4321)

    assert module._linux_process_group_has_live_members(4321, proc_root) is True


def test_linux_process_group_probe_detects_live_worker_behind_zombie_leader(
    tmp_path: Path,
) -> None:
    module = load_zagrosi_module()
    proc_root = tmp_path / "proc"
    process_dir = proc_root / "101"
    task_dir = process_dir / "task"
    (task_dir / "101").mkdir(parents=True)
    (task_dir / "201").mkdir()
    (process_dir / "stat").write_text("101 (leader) Z 1 4321 4321 0\n")
    (task_dir / "101" / "stat").write_text("101 (leader) Z 1 4321 4321 0\n")
    (task_dir / "201" / "stat").write_text("201 (worker) S 1 4321 4321 0\n")

    assert module._linux_process_group_has_live_members(4321, proc_root) is True


def test_procfs_mount_visibility_gate_rejects_hidden_or_unknown_process_views() -> None:
    module = load_zagrosi_module()

    assert module._procfs_mount_hides_processes(
        b"proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0\n"
    ) is False
    assert module._procfs_mount_hides_processes(
        b"proc /proc proc rw,nosuid,hidepid=2 0 0\n"
    ) is True
    assert module._procfs_mount_hides_processes(b"tmpfs /tmp tmpfs rw 0 0\n") is None


def test_run_bounded_child_accepts_zombie_only_process_group_after_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_zagrosi_module()
    real_killpg = module.os.killpg
    zero_probes: list[int] = []

    def zombie_only_group(process_group: int, sig: int) -> None:
        if sig == 0:
            zero_probes.append(process_group)
            return
        real_killpg(process_group, sig)

    monkeypatch.setattr(module.os, "killpg", zombie_only_group)
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(
        module,
        "_linux_process_group_has_live_members",
        lambda process_group: False,
    )
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        return_code, stdout, stderr = module.run_bounded_child(
            [sys.executable, "-I", "-B", "-c", "pass"],
            b"",
            cwd_fd=cwd_fd,
            timeout_seconds=2.0,
            stdout_cap=4096,
            stderr_cap=4096,
        )
    finally:
        os.close(cwd_fd)

    assert return_code == 0
    assert stdout == b""
    assert stderr == b""
    assert zero_probes


@pytest.mark.parametrize(
    ("child_shape", "expected_code"),
    (
        ("stubborn_leader", "handoff-child-timeout"),
        ("inherited_pipe_descendant", "handoff-child-timeout"),
        ("closed_pipe_descendant_after_success", "handoff-child-residual-process-group"),
    ),
)
def test_run_bounded_child_terminates_and_reaps_the_complete_process_group(
    tmp_path: Path,
    child_shape: str,
    expected_code: str,
) -> None:
    module = load_zagrosi_module()
    pid_path = tmp_path / f"{child_shape}.pid"
    if child_shape == "stubborn_leader":
        source = (
            "import os,signal,time\n"
            f"open({str(pid_path)!r},'w').write(str(os.getpid()))\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "while True: time.sleep(1)\n"
        )
    else:
        close_pipes = " os.close(1); os.close(2)\n" if child_shape == "closed_pipe_descendant_after_success" else ""
        source = (
            "import os,signal,time\n"
            "child=os.fork()\n"
            "if child==0:\n"
            f"{close_pipes}"
            " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            " while True: time.sleep(1)\n"
            f"open({str(pid_path)!r},'w').write(str(child))\n"
            "os._exit(0)\n"
        )
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    started = time.monotonic()
    try:
        with pytest.raises(module.DetachedImplementationError) as caught:
            module.run_bounded_child(
                [sys.executable, "-I", "-B", "-c", source],
                b"",
                cwd_fd=cwd_fd,
                timeout_seconds=0.25,
                stdout_cap=4096,
                stderr_cap=4096,
            )
    finally:
        os.close(cwd_fd)
    assert caught.value.code == expected_code
    assert time.monotonic() - started < 5.0
    pid = int(pid_path.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        assert sys.platform.startswith("linux")
        try:
            raw_stat = (Path("/proc") / str(pid) / "stat").read_bytes()
        except FileNotFoundError:
            pass
        else:
            closing_parenthesis = raw_stat.rfind(b")")
            fields = raw_stat[closing_parenthesis + 1 :].split()
            assert fields and fields[0] in {b"Z", b"X", b"x"}


@pytest.mark.parametrize("stream_fd", (1, 2))
def test_run_bounded_child_rejects_complete_output_cap_mutants(
    tmp_path: Path,
    stream_fd: int,
) -> None:
    module = load_zagrosi_module()
    source = f"import os,time; os.write({stream_fd}, b'x' * 65); time.sleep(10)"
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(module.DetachedImplementationError) as caught:
            module.run_bounded_child(
                [sys.executable, "-I", "-B", "-c", source],
                b"",
                cwd_fd=cwd_fd,
                timeout_seconds=2.0,
                stdout_cap=64,
                stderr_cap=64,
            )
    finally:
        os.close(cwd_fd)
    assert caught.value.code == "handoff-child-output-cap"


@pytest.mark.skipif(sys.platform != "darwin", reason="requires Apple Git process behaviour")
def test_run_bounded_child_real_apple_git_dirty_probe_preserves_semantic_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_zagrosi_module()
    apple_git = Path("/usr/bin/git")
    if not apple_git.is_file():
        pytest.skip("Apple Git is unavailable")
    version = subprocess.run(
        [str(apple_git), "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=module.HANDOFF_GIT_ENV,
    ).stdout.strip()
    if version != "git version 2.50.1 (Apple Git-155)":
        pytest.skip(f"requires Apple Git 2.50.1 (Apple Git-155), found {version}")

    repository = tmp_path / "dirty-repository"
    repository.mkdir()
    subprocess.run(
        [str(apple_git), "init", "--quiet"],
        cwd=repository,
        check=True,
        capture_output=True,
        env=module.HANDOFF_GIT_ENV,
    )
    (repository / "dirty-untracked.txt").write_text("dirty\n")
    monkeypatch.setattr(module, "HANDOFF_GIT", str(apple_git))
    cwd_fd = os.open(repository, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(module.DetachedImplementationError) as caught:
            module.run_bounded_child(
                [module.HANDOFF_GIT, *module.HANDOFF_GIT_STATUS_ARGS],
                b"",
                cwd_fd=cwd_fd,
                timeout_seconds=10.0,
                stdout_cap=1,
                stderr_cap=65536,
                child_env=module.HANDOFF_GIT_ENV,
            )
    finally:
        os.close(cwd_fd)

    assert caught.value.code == "handoff-source-dirty"


@pytest.mark.parametrize("section_token", ("S26", "S28"))
def test_protected_source_observation_uses_exact_git_and_raw_source_contract(
    tmp_path: Path,
    monkeypatch,
    section_token: str,
) -> None:
    module = load_zagrosi_module()
    contract = module.HANDOFF_SECTION_CONTRACTS[section_token]
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    source_bytes: dict[str, bytes] = {}
    for index, relative in enumerate(contract["implementation_sources"], start=1):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = f"source-{index}-{relative}\n".encode("ascii")
        path.write_bytes(raw)
        source_bytes[relative] = raw
    test_path = target / contract["test"]
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_raw = b"independent-test-source\n"
    test_path.write_bytes(test_raw)
    commit = b"0123456789abcdef0123456789abcdef01234567\n"
    tree = b"100644 blob 0123456789012345678901234567890123456789\tfile\0"
    calls: list[tuple[list[str], float, int, int, dict[str, str]]] = []

    def fake_child(argv, input_bytes, *, cwd_fd, timeout_seconds, stdout_cap, stderr_cap, child_env):
        assert input_bytes == b""
        assert (os.fstat(cwd_fd).st_dev, os.fstat(cwd_fd).st_ino) == (
            target.stat().st_dev,
            target.stat().st_ino,
        )
        calls.append((argv, timeout_seconds, stdout_cap, stderr_cap, child_env))
        if argv[1] == "status":
            return 0, b"", b""
        if argv[1] == "rev-parse":
            return 0, commit, b""
        assert argv[1] == "ls-tree"
        return 0, tree, b""

    monkeypatch.setattr(module, "run_bounded_child", fake_child)
    target_fd = os.open(target, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        observed = module.derive_protected_source_observation(target_fd, contract)
        root_stat = os.fstat(target_fd)
    finally:
        os.close(target_fd)

    identity = {
        "device": root_stat.st_dev,
        "gid": root_stat.st_gid,
        "inode": root_stat.st_ino,
        "link_count": root_stat.st_nlink,
        "mode": root_stat.st_mode & 0o777,
        "uid": root_stat.st_uid,
    }
    implementation_digest = hashlib.sha256(contract["implementation_source_domain"])
    for relative in sorted(source_bytes, key=lambda value: value.encode("ascii")):
        relative_raw = relative.encode("ascii")
        implementation_digest.update(len(relative_raw).to_bytes(4, "big"))
        implementation_digest.update(relative_raw)
        implementation_digest.update(hashlib.sha256(source_bytes[relative]).digest())
    expected_tree = hashlib.sha256(
        b"unit12-protected-source-tree-v1\0" + len(tree).to_bytes(8, "big") + tree
    ).hexdigest()
    assert observed == module.ProtectedSourceObservation(
        protected_source_root_identity_digest=domain_sha256_for_test(
            b"unit12-protected-source-root-identity-v1\0",
            canonical_json_bytes_for_test(identity)[:-1],
        ),
        source_commit=commit[:-1].decode("ascii"),
        source_tree_sha256="sha256:" + expected_tree,
        implementation_source_sha256="sha256:" + implementation_digest.hexdigest(),
        test_source_sha256="sha256:" + hashlib.sha256(test_raw).hexdigest(),
    )
    assert calls == [
        (
            [module.HANDOFF_GIT, "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            10.0,
            1,
            64 * 1024,
            module.HANDOFF_GIT_ENV,
        ),
        (
            [module.HANDOFF_GIT, "rev-parse", "--verify", "HEAD^{commit}"],
            10.0,
            41,
            64 * 1024,
            module.HANDOFF_GIT_ENV,
        ),
        (
            [module.HANDOFF_GIT, "ls-tree", "-r", "-z", "--full-tree", "HEAD"],
            30.0,
            16_777_216,
            64 * 1024,
            module.HANDOFF_GIT_ENV,
        ),
    ]
    assert module.HANDOFF_GIT_ENV == {
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }


@pytest.mark.parametrize(
    ("probe_frames", "expected_code"),
    (
        ((b"untracked\0",), "handoff-source-dirty"),
        ((b"", b"ABCDEF0123456789ABCDEF0123456789ABCDEF01\n"), "handoff-source-revision-invalid"),
        ((b"", b"0123456789abcdef0123456789abcdef01234567extra\n"), "handoff-source-revision-invalid"),
    ),
)
def test_protected_source_observation_rejects_dirty_and_noncanonical_revision(
    tmp_path: Path,
    monkeypatch,
    probe_frames: tuple[bytes, ...],
    expected_code: str,
) -> None:
    module = load_zagrosi_module()
    frames = iter(probe_frames)
    monkeypatch.setattr(module, "run_protected_source_probe", lambda *args, **kwargs: next(frames))
    target_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(module.DetachedImplementationError) as caught:
            module.derive_protected_source_observation(
                target_fd,
                module.HANDOFF_SECTION_CONTRACTS["S26"],
            )
    finally:
        os.close(target_fd)
    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("child_code", "expected_code"),
    (
        ("handoff-child-timeout", "handoff-source-probe-unavailable"),
        ("handoff-child-termination-unproven", "handoff-source-probe-unavailable"),
        ("handoff-child-residual-process-group", "handoff-source-probe-unavailable"),
        ("handoff-child-output-cap", "handoff-source-probe-unavailable"),
        ("handoff-source-dirty", "handoff-source-dirty"),
    ),
)
def test_protected_source_probe_distinguishes_unavailability_from_semantic_failure(
    tmp_path: Path,
    monkeypatch,
    child_code: str,
    expected_code: str,
) -> None:
    module = load_zagrosi_module()
    monkeypatch.setattr(
        module,
        "run_bounded_child",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            module.DetachedImplementationError(child_code, "value-bearing private detail")
        ),
    )
    target_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(module.DetachedImplementationError) as caught:
            module.run_protected_source_probe(
                target_fd,
                [module.HANDOFF_GIT, "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                timeout_seconds=10.0,
                stdout_cap=1,
            )
    finally:
        os.close(target_fd)
    assert caught.value.code == expected_code


@pytest.mark.parametrize("section_token", ("S26", "S28"))
def test_fixed_runner_matches_only_current_implementation_source_bytes(
    tmp_path: Path,
    section_token: str,
) -> None:
    module = load_zagrosi_module()
    contract = module.HANDOFF_SECTION_CONTRACTS[section_token]
    target = tmp_path / "target"
    source = target / contract["runner_source"]
    source.parent.mkdir(parents=True)
    admitted = b"root-runner-complete-bytes-v1\n"
    source.write_bytes(admitted)
    target_fd = os.open(target, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        module.require_gate_runner_matches_source(contract, target_fd, admitted)
        same_length_mutant = admitted[:-2] + bytes([admitted[-2] ^ 1]) + admitted[-1:]
        with pytest.raises(module.DetachedImplementationError) as stale:
            module.require_gate_runner_matches_source(contract, target_fd, same_length_mutant)
        assert stale.value.code == "handoff-runner-source-drift"
        hardlink = source.with_name(source.name + ".hardlink")
        os.link(source, hardlink)
        with pytest.raises(module.DetachedImplementationError, match="single-link"):
            module.require_gate_runner_matches_source(contract, target_fd, admitted)
    finally:
        os.close(target_fd)


def test_handoff_commands_use_only_fixed_root_runner_and_exact_frozen_hashes() -> None:
    module = load_zagrosi_module()
    expected = {
        "S26": (
            "/usr/local/libexec/santander-unit12-gates/s26-privileged-darwin-apfs-gate-runner-v1.py",
            "sha256:2adb5c10c313b7bb758d539ffaf6313439cf562f7f0dad99ac3ed8ff705adc1d",
            "sha256:3cc96ce9be563930630c151e0b854f140e3e2e2421c49b888d1751946f8d672b",
            "sha256:c415905a53f5ad02d97336b65d21a6a31a469c21f2ae5745ddef74ed9de1c862",
        ),
        "S28": (
            "/usr/local/libexec/santander-unit12-gates/s28-privileged-darwin-apfs-gate-runner-v1.py",
            "sha256:43db5942eeb65b2d69e306aa7086f04c346ce1fd453fa930c39d9e713de824c1",
            "sha256:783c80921f7c9bb431fec5c0f1d77a538aa5761149987804a841800ece2f2c3c",
            "sha256:3d4ac7e265610fca5106c8dedcf89564f7ee424376a60707db6590131d273e57",
        ),
    }
    for token, (runner, gate_hash, root_hash, verifier_hash) in expected.items():
        contract = module.HANDOFF_SECTION_CONTRACTS[token]
        root_argv = module.handoff_root_argv(contract)
        verifier_argv = module.handoff_verifier_argv(contract)
        assert root_argv[6] == runner
        assert verifier_argv[3] == runner
        assert contract["test"] not in root_argv + verifier_argv
        assert contract["gate_command_sha256"] == gate_hash
        assert contract["command_sha256"] == root_hash
        assert contract["verifier_command_sha256"] == verifier_hash
        module.verify_handoff_command_identities(contract)
        mutable_repo_mutant = dict(contract)
        mutable_repo_mutant["runner"] = contract["test"]
        mutable_repo_mutant["command_sha256"] = module.framed_command_sha256(
            b"unit12-privileged-gate-handoff-command-v1\0",
            module.handoff_root_argv(mutable_repo_mutant),
        )
        mutable_repo_mutant["verifier_command_sha256"] = module.framed_command_sha256(
            b"unit12-privileged-gate-handoff-verifier-command-v1\0",
            module.handoff_verifier_argv(mutable_repo_mutant),
        )
        with pytest.raises(module.DetachedImplementationError, match="runner selection"):
            module.verify_handoff_command_identities(mutable_repo_mutant)


@pytest.mark.parametrize("section_token", ("S26", "S28"))
def test_handoff_receipt_must_echo_selected_exact_gate_command_digest(section_token: str) -> None:
    module = load_zagrosi_module()
    contract = module.HANDOFF_SECTION_CONTRACTS[section_token]
    digest = "sha256:" + "42" * 32
    config = {
        "admission_state_sha256": digest,
        "admission_pinner_sha256": digest,
        "planning_tree_sha256": digest,
        "detached_implementation_root_identity_digest": digest,
        "implement_tool_sha256": digest,
        "implement_skill_sha256": digest,
        "implement_test_sha256": digest,
    }
    _, request_raw, request_final = module.build_handoff_request(config, contract)
    receipt = json.loads(handoff_receipt_for_test(module, config, contract, request_raw))
    receipt["gate_command_sha256"] = "sha256:" + "24" * 32
    with pytest.raises(module.DetachedImplementationError) as caught:
        module.parse_handoff_receipt(
            canonical_json_bytes_for_test(receipt),
            config,
            contract,
            request_final,
        )
    assert caught.value.code == "handoff-receipt-drift"


@pytest.mark.parametrize(
    ("mode", "link_count", "uid", "gid", "accepted"),
    (
        (0o555, 1, 0, 0, True),
        (0o755, 1, 0, 0, False),
        (0o555, 2, 0, 0, False),
        (0o555, 1, 501, 0, False),
        (0o555, 1, 0, 20, False),
    ),
)
def test_fixed_gate_runner_metadata_is_exact_and_no_follow(
    tmp_path: Path,
    monkeypatch,
    mode: int,
    link_count: int,
    uid: int,
    gid: int,
    accepted: bool,
) -> None:
    module = load_zagrosi_module()
    contract = module.HANDOFF_SECTION_CONTRACTS["S26"]
    runner_parent = tmp_path / "root-owned-runner-parent"
    runner_parent.mkdir()
    runner = runner_parent / Path(contract["runner"]).name
    raw = b"fixed-runner-source\n"
    runner.write_bytes(raw)
    runner.chmod(0o555)
    real_fstat = module.os.fstat

    def fake_parent(path):
        return os.open(runner_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))

    def fake_fstat(file_fd):
        observed = real_fstat(file_fd)
        if observed.st_ino != runner.stat().st_ino:
            return observed
        return SimpleNamespace(
            st_dev=observed.st_dev,
            st_ino=observed.st_ino,
            st_mode=module.stat.S_IFREG | mode,
            st_nlink=link_count,
            st_uid=uid,
            st_gid=gid,
            st_size=observed.st_size,
            st_mtime_ns=observed.st_mtime_ns,
            st_ctime_ns=observed.st_ctime_ns,
        )

    monkeypatch.setattr(module, "open_root_owned_nonwritable_directory_chain", fake_parent)
    monkeypatch.setattr(module.os, "fstat", fake_fstat)
    if accepted:
        assert module.read_fixed_gate_runner(contract) == raw
    else:
        with pytest.raises(module.DetachedImplementationError) as caught:
            module.read_fixed_gate_runner(contract)
        assert caught.value.code == "unsafe-handoff-dependency"

    runner.unlink()
    runner.symlink_to(tmp_path / "mutable-repository-runner.py")
    with pytest.raises(module.DetachedImplementationError) as symbolic:
        module.read_fixed_gate_runner(contract)
    assert symbolic.value.code == "unsafe-handoff-dependency"


@pytest.mark.parametrize(
    ("link_count", "mode", "uid", "gid", "accepted"),
    (
        (2, 0o555, 0, 0, True),
        (1, 0o555, 0, 0, True),
        (True, 0o555, 0, 0, False),
        (0, 0o555, 0, 0, False),
        (2, 0o666, 0, 0, False),
        (2, 0o555, 501, 0, False),
        (2, 0o555, 0, 20, False),
    ),
)
def test_fixed_stat_dependency_accepts_supported_link_count_only_with_safe_metadata(
    tmp_path: Path,
    monkeypatch,
    link_count: int,
    mode: int,
    uid: int,
    gid: int,
    accepted: bool,
) -> None:
    module = load_zagrosi_module()
    dependency_parent = tmp_path / "fixed-bin"
    dependency_parent.mkdir()
    executable = dependency_parent / "stat"
    executable.write_bytes(b"fixed stat executable")
    executable.chmod(0o555)
    real_fstat = module.os.fstat

    monkeypatch.setattr(
        module,
        "open_root_owned_nonwritable_directory_chain",
        lambda path: os.open(dependency_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)),
    )

    def fake_fstat(file_fd):
        observed = real_fstat(file_fd)
        return SimpleNamespace(
            st_mode=module.stat.S_IFREG | mode,
            st_nlink=link_count,
            st_uid=uid,
            st_gid=gid,
        )

    monkeypatch.setattr(module.os, "fstat", fake_fstat)
    if accepted:
        module.require_fixed_handoff_executable("/usr/bin/stat", allow_multiple_links=True)
    else:
        with pytest.raises(module.DetachedImplementationError) as caught:
            module.require_fixed_handoff_executable("/usr/bin/stat", allow_multiple_links=True)
        assert caught.value.code == "unsafe-handoff-dependency"


def test_fixed_executable_dependency_rejects_missing_and_symbolic_link(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_zagrosi_module()
    dependency_parent = tmp_path / "fixed-bin"
    dependency_parent.mkdir()
    monkeypatch.setattr(
        module,
        "open_root_owned_nonwritable_directory_chain",
        lambda path: os.open(dependency_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)),
    )
    with pytest.raises(module.DetachedImplementationError) as missing:
        module.require_fixed_handoff_executable("/usr/bin/stat", allow_multiple_links=True)
    assert missing.value.code == "missing-handoff-dependency"

    (dependency_parent / "real-stat").write_bytes(b"replacement")
    (dependency_parent / "stat").symlink_to(dependency_parent / "real-stat")
    with pytest.raises(module.DetachedImplementationError) as symbolic:
        module.require_fixed_handoff_executable("/usr/bin/stat", allow_multiple_links=True)
    assert symbolic.value.code == "unsafe-handoff-dependency"


def test_handoff_platform_rejects_fixed_stat_path_mutation_before_spawn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_zagrosi_module()
    monkeypatch.setattr(module.os, "geteuid", lambda: 501)
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(module, "HANDOFF_STAT", "/tmp/mutable-stat")
    child_calls = 0

    def forbidden_child(*args, **kwargs):
        nonlocal child_calls
        child_calls += 1
        raise AssertionError("mutated stat path must fail before spawn")

    monkeypatch.setattr(module, "run_bounded_child", forbidden_child)
    root_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(module.DetachedImplementationError) as caught:
            module.require_handoff_platform(root_fd)
    finally:
        os.close(root_fd)
    assert caught.value.code == "handoff-command-drift"
    assert child_calls == 0


@pytest.mark.parametrize(
    ("return_code", "stdout", "stderr", "accepted"),
    (
        (0, b"apfs\n", b"", True),
        (1, b"apfs\n", b"", False),
        (0, b"apfs", b"", False),
        (0, b"apfs\nextra", b"", False),
        (0, b"apfs\n", b"closed\n", False),
        (0, b"a" * 65, b"", False),
    ),
)
def test_handoff_platform_apfs_probe_is_exact_and_closed(
    tmp_path: Path,
    monkeypatch,
    return_code: int,
    stdout: bytes,
    stderr: bytes,
    accepted: bool,
) -> None:
    module = load_zagrosi_module()
    monkeypatch.setattr(module.os, "geteuid", lambda: 501)
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64")
    executable_checks: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        module,
        "require_fixed_handoff_executable",
        lambda path, *, allow_multiple_links=False: executable_checks.append((path, allow_multiple_links)),
    )

    def fake_child(argv, input_bytes, **kwargs):
        assert argv == ["/usr/bin/stat", "-f", "%T", "."]
        assert input_bytes == b""
        assert kwargs == {
            "cwd_fd": root_fd,
            "timeout_seconds": 5.0,
            "stdout_cap": 64,
            "stderr_cap": 64,
        }
        assert module.HANDOFF_ENV == {"LC_ALL": "C", "LANG": "C", "TZ": "UTC"}
        return return_code, stdout, stderr

    monkeypatch.setattr(module, "run_bounded_child", fake_child)
    root_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        if accepted:
            module.require_handoff_platform(root_fd)
        else:
            with pytest.raises(module.DetachedImplementationError) as caught:
                module.require_handoff_platform(root_fd)
            assert caught.value.code == "unsupported-handoff-platform"
    finally:
        os.close(root_fd)
    assert executable_checks == [("/usr/bin/stat", True)]


def detached_root_identity_digest_for_test(path: Path) -> str:
    observed = path.stat()
    identity = {
        "device": observed.st_dev,
        "gid": observed.st_gid,
        "inode": observed.st_ino,
        "link_count": observed.st_nlink,
        "mode": observed.st_mode & 0o777,
        "uid": observed.st_uid,
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(
        b"zagrosi-detached-implementation-root-identity-v1\0" + raw
    ).hexdigest()


def target_root_identity_digest_for_test(path: Path) -> str:
    observed = path.stat()
    identity = {
        "device": observed.st_dev,
        "gid": observed.st_gid,
        "inode": observed.st_ino,
        "link_count": observed.st_nlink,
        "mode": observed.st_mode & 0o777,
        "uid": observed.st_uid,
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(
        b"zagrosi-detached-target-root-identity-v1\0" + raw
    ).hexdigest()


def implementation_source_args(plugin_root: Path = ROOT, **overrides: str) -> tuple[str, ...]:
    values: list[str] = []
    for source, relative in IMPLEMENTATION_SOURCE_RELATIVE_PATHS.items():
        values.extend(
            [
                f"--expected-implement-{source}-sha256",
                overrides.get(source, file_sha256(plugin_root / relative)),
            ]
        )
    return tuple(values)


def copy_implementation_plugin(destination: Path) -> Path:
    plugin_root = destination / "plugin"
    for relative in IMPLEMENTATION_SOURCE_RELATIVE_PATHS.values():
        target = plugin_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return plugin_root


def instrument_record_crashpoints(plugin_root: Path) -> Path:
    script = plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"]
    text = script.read_text()
    insertion = '''\n\ndef _test_record_crashpoint(name: str) -> None:\n    if name == "state-cas-fsync" and os.environ.get("ZAGROSI_TEST_FORCE_ROLLBACK") == "1":\n        Path(os.environ["ZAGROSI_TEST_FORCE_ROLLBACK_PATH"]).write_bytes(b'{"schema":"test-record-gate-v1","verdict":"DRIFT"}\\n')\n    requested = os.environ.get("ZAGROSI_TEST_RECORD_CRASHPOINT")\n    if requested == name:\n        os.kill(os.getpid(), signal.SIGKILL)\n    pausepoint = os.environ.get("ZAGROSI_TEST_RECORD_PAUSEPOINT")\n    if pausepoint == name:\n        ready = Path(os.environ["ZAGROSI_TEST_RECORD_READY"])\n        release = Path(os.environ["ZAGROSI_TEST_RECORD_RELEASE"])\n        ready.write_text("ready\\n")\n        while not release.exists():\n            time.sleep(0.01)\n\ndef _test_precreate_adopted_pinner(root_fd: int, transaction_fd: int, pinner_path: str) -> None:\n    mode = os.environ.get("ZAGROSI_TEST_RECORD_ADOPT_PREEXISTING")\n    if mode not in {"1", "wrong"}:\n        return\n    staged, raw = load_canonical_json_at(transaction_fd, "pinner.json")\n    published = staged if mode == "1" else {**staged, "notes": "wrong preexisting bytes"}\n    write_canonical_json_at(root_fd, pinner_path, published, immutable=True)\n    reopened = read_single_link_regular_at(root_fd, pinner_path, cap=DETACHED_JSON_CAP, require_mode=0o600)\n    if mode == "1" and reopened != raw:\n        raise AssertionError("adopted test pinner bytes changed")\n'''
    anchor = '\n\ndef open_directory_chain_no_follow(path: Path, *, create: bool = False) -> int:\n'
    assert text.count(anchor) == 1
    text = text.replace(anchor, insertion + anchor)
    replacements = {
        '        _write_all(file_fd, raw)\n'
        '        os.fsync(file_fd)\n': (
            '        partial_crashpoint = {\n'
            '            "pinner.tmp": "pinner-tmp-partial",\n'
            '            "transaction.write.tmp": "journal-write-temp-partial",\n'
            '        }.get(name)\n'
            '        if (\n'
            '            partial_crashpoint is not None\n'
            '            and partial_crashpoint == os.environ.get("ZAGROSI_TEST_RECORD_CRASHPOINT")\n'
            '        ):\n'
            '            _write_all(file_fd, raw[: max(1, len(raw) // 2)])\n'
            '            os.fsync(file_fd)\n'
            '            _test_record_crashpoint(partial_crashpoint)\n'
            '        _write_all(file_fd, raw)\n'
            '        os.fsync(file_fd)\n'
        ),
        '    os.replace(\n'
        '        "transaction.json",\n'
        '        "rollback.json",\n'
        '        src_dir_fd=transaction_fd,\n'
        '        dst_dir_fd=transaction_fd,\n'
        '    )\n'
        '    os.fsync(transaction_fd)\n': (
            '    os.replace(\n'
            '        "transaction.json",\n'
            '        "rollback.json",\n'
            '        src_dir_fd=transaction_fd,\n'
            '        dst_dir_fd=transaction_fd,\n'
            '    )\n'
            '    _test_record_crashpoint("rollback-rename-before-dir-fsync")\n'
            '    os.fsync(transaction_fd)\n'
            '    _test_record_crashpoint("rollback-rename-fsync")\n'
        ),
        '        os.unlink(parts[1], dir_fd=pinners_fd)\n'
        '        os.fsync(pinners_fd)\n': (
            '        os.unlink(parts[1], dir_fd=pinners_fd)\n'
            '        os.fsync(pinners_fd)\n'
            '        _test_record_crashpoint("rollback-final-delete-fsync")\n'
        ),
        '        write_new_fixed_file_at(transaction_fd, "state.json", base_raw)\n': (
            '        write_new_fixed_file_at(transaction_fd, "state.json", base_raw)\n'
            '        _test_record_crashpoint("rollback-state-temp-fsync")\n'
        ),
        '    if section_record_entry_stat(transaction_fd, "state.json") is None:\n'
        '        write_new_fixed_file_at(transaction_fd, "state.json", replacement_raw)\n': (
            '    if section_record_entry_stat(transaction_fd, "state.json") is None:\n'
            '        write_new_fixed_file_at(transaction_fd, "state.json", replacement_raw)\n'
            '        _test_record_crashpoint("forward-state-temp-fsync")\n'
        ),
        '    os.replace("state.json", "zagrosi_implement_state.json", src_dir_fd=transaction_fd, dst_dir_fd=root_fd)\n'
        '    os.fsync(root_fd)\n': (
            '    os.replace("state.json", "zagrosi_implement_state.json", src_dir_fd=transaction_fd, dst_dir_fd=root_fd)\n'
            '    _test_record_crashpoint("forward-state-replace-before-root-fsync")\n'
            '    os.fsync(root_fd)\n'
        ),
        '    os.replace(\n'
        '        "state.json",\n'
        '        "zagrosi_implement_state.json",\n'
        '        src_dir_fd=transaction_fd,\n'
        '        dst_dir_fd=root_fd,\n'
        '    )\n'
        '    os.fsync(root_fd)\n': (
            '    os.replace(\n'
            '        "state.json",\n'
            '        "zagrosi_implement_state.json",\n'
            '        src_dir_fd=transaction_fd,\n'
            '        dst_dir_fd=root_fd,\n'
            '    )\n'
            '    _test_record_crashpoint("rollback-state-replace-before-root-fsync")\n'
            '    os.fsync(root_fd)\n'
            '    _test_record_crashpoint("rollback-state-replace-fsync")\n'
        ),
        '    os.unlink("rollback.json", dir_fd=transaction_fd)\n'
        '    os.fsync(transaction_fd)\n': (
            '    os.unlink("rollback.json", dir_fd=transaction_fd)\n'
            '    _test_record_crashpoint("rollback-unlink-before-dir-fsync")\n'
            '    os.fsync(transaction_fd)\n'
            '    _test_record_crashpoint("rollback-unlink-fsync")\n'
        ),
        '    write_new_fixed_file_at(transaction_fd, "transaction.write.tmp", raw)\n'
        '    rename_fixed_file_no_replace_at(\n'
        '        transaction_fd,\n'
        '        "transaction.write.tmp",\n'
        '        "transaction.tmp",\n'
        '    )\n'
        '    os.fsync(transaction_fd)\n': (
            '    write_new_fixed_file_at(transaction_fd, "transaction.write.tmp", raw)\n'
            '    _test_record_crashpoint("journal-write-temp-fsync")\n'
            '    rename_fixed_file_no_replace_at(\n'
            '        transaction_fd,\n'
            '        "transaction.write.tmp",\n'
            '        "transaction.tmp",\n'
            '    )\n'
            '    _test_record_crashpoint("journal-temp-rename-before-dir-fsync")\n'
            '    os.fsync(transaction_fd)\n'
            '    _test_record_crashpoint("journal-temp-rename-fsync")\n'
        ),
        '    rename_fixed_file_no_replace_at(\n'
        '        transaction_fd,\n'
        '        "transaction.tmp",\n'
        '        "transaction.json",\n'
        '    )\n'
        '    os.fsync(transaction_fd)\n': (
            '    rename_fixed_file_no_replace_at(\n'
            '        transaction_fd,\n'
            '        "transaction.tmp",\n'
            '        "transaction.json",\n'
            '    )\n'
            '    _test_record_crashpoint("journal-rename-before-dir-fsync")\n'
            '    os.fsync(transaction_fd)\n'
            '    _test_record_crashpoint("journal-rename-fsync")\n'
        ),
        '    write_new_fixed_file_at(transaction_fd, "pinner.tmp", pinner_raw)\n'
        '    rename_fixed_file_no_replace_at(transaction_fd, "pinner.tmp", "pinner.json")\n'
        '    os.fsync(transaction_fd)\n': (
            '    write_new_fixed_file_at(transaction_fd, "pinner.tmp", pinner_raw)\n'
            '    _test_record_crashpoint("pinner-tmp-write-fsync")\n'
            '    rename_fixed_file_no_replace_at(transaction_fd, "pinner.tmp", "pinner.json")\n'
            '    _test_record_crashpoint("pinner-rename-before-dir-fsync")\n'
            '    os.fsync(transaction_fd)\n'
            '    _test_record_crashpoint("pinner-rename-dir-fsync")\n'
        ),
        '            install_staged_section_pinner(root_fd, transaction_fd, pinner_path, pinner_raw)\n': (
        '            _test_precreate_adopted_pinner(root_fd, transaction_fd, pinner_path)\n'
        '            install_staged_section_pinner(root_fd, transaction_fd, pinner_path, pinner_raw)\n'
        '            _test_record_crashpoint("final-link-fsync")\n'
        ),
        '            created = True\n'
        '            os.fsync(pinners_fd)\n': (
            '            created = True\n'
            '            _test_record_crashpoint("final-link-before-dir-fsync")\n'
            '            os.fsync(pinners_fd)\n'
        ),
        '            replace_state_from_transaction(root_fd, transaction_fd, base_state_raw, candidate_state)\n': (
            '            replace_state_from_transaction(root_fd, transaction_fd, base_state_raw, candidate_state)\n'
            '            _test_record_crashpoint("state-cas-fsync")\n'
        ),
        '                candidate_state_raw,\n                require_lock_authority,\n            )\n            require_lock_authority()\n': (
            '                candidate_state_raw,\n                require_lock_authority,\n            )\n'
            '            _test_record_crashpoint("post-state-validation")\n'
            '            require_lock_authority()\n'
        ),
        '        os.fsync(transaction_fd)\n    except OSError:\n        if not journal_removed:\n': (
            '        os.fsync(transaction_fd)\n'
            '        _test_record_crashpoint("journal-unlink-fsync")\n'
            '    except OSError:\n        if not journal_removed:\n'
        ),
        '        os.unlink("transaction.json", dir_fd=transaction_fd)\n'
        '        journal_removed = True\n'
        '        os.fsync(transaction_fd)\n': (
            '        os.unlink("transaction.json", dir_fd=transaction_fd)\n'
            '        journal_removed = True\n'
            '        _test_record_crashpoint("journal-unlink-before-dir-fsync")\n'
            '        os.fsync(transaction_fd)\n'
        ),
        '            unlink_fixed_file_at(transaction_fd, name, missing_ok=True)\n': (
            '            unlink_fixed_file_at(transaction_fd, name, missing_ok=True)\n'
            '            if name == "pinner.json":\n'
            '                _test_record_crashpoint("stage-cleanup-fsync")\n'
        ),
        '        os.rmdir(Path(SECTION_RECORD_TRANSACTION_DIR).name, dir_fd=pinners_fd)\n        os.fsync(pinners_fd)\n': (
            '        os.rmdir(Path(SECTION_RECORD_TRANSACTION_DIR).name, dir_fd=pinners_fd)\n'
            '        _test_record_crashpoint("transaction-rmdir-before-parent-fsync")\n'
            '        os.fsync(pinners_fd)\n'
        ),
    }
    for needle, replacement in replacements.items():
        assert text.count(needle) == 1, needle
        text = text.replace(needle, replacement)
    replace_file(script, text.encode(), mode=script.stat().st_mode & 0o777)
    return script


def instrument_root_lifecycle_points(plugin_root: Path) -> Path:
    script = plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"]
    text = script.read_text()
    insertion = '''\n\ndef _test_root_lifecycle_point(name: str) -> None:\n    if os.environ.get("ZAGROSI_TEST_ROOT_CRASHPOINT") == name:\n        os.kill(os.getpid(), signal.SIGKILL)\n    if os.environ.get("ZAGROSI_TEST_ROOT_PAUSEPOINT") == name:\n        ready = Path(os.environ["ZAGROSI_TEST_ROOT_READY"])\n        release = Path(os.environ["ZAGROSI_TEST_ROOT_RELEASE"])\n        ready.write_text("ready\\n")\n        while not release.exists():\n            time.sleep(0.01)\n'''
    anchor = '\n\ndef open_directory_chain_no_follow(path: Path, *, create: bool = False) -> int:\n'
    assert text.count(anchor) == 1
    text = text.replace(anchor, insertion + anchor)
    replacements = {
        '                os.fsync(temporary_fd)\n                os.close(temporary_fd)\n': (
            '                os.fsync(temporary_fd)\n'
            '                _test_root_lifecycle_point(f"canonical-temp-fsync:{relative}")\n'
            '                os.close(temporary_fd)\n'
        ),
        '        _write_all(file_fd, pending_raw)\n        os.fsync(file_fd)\n        os.close(file_fd)\n': (
            '        _write_all(file_fd, pending_raw)\n'
            '        os.fsync(file_fd)\n'
            '        _test_root_lifecycle_point(f"setup-slot-temp-fsync:{relative}")\n'
            '        os.close(file_fd)\n'
        ),
        '        require_global_authority = lock_context.enter_context(detached_global_lock(lock_deadline))\n'
        '        require_global_authority()\n'
        '        requested_root = absolute_path_no_follow(args.implementation_root)\n': (
            '        require_global_authority = lock_context.enter_context(detached_global_lock(lock_deadline))\n'
            '        require_global_authority()\n'
            '        _test_root_lifecycle_point("setup-global-acquired")\n'
            '        requested_root = absolute_path_no_follow(args.implementation_root)\n'
        ),
    }
    for needle, replacement in replacements.items():
        assert text.count(needle) == 1, needle
        text = text.replace(needle, replacement)
    replace_file(script, text.encode(), mode=script.stat().st_mode & 0o777)
    return script


def make_detached_record_fixture(
    base: Path,
    *,
    section: str = "section-01-foundation",
    plugin_root: Path = ROOT,
    single_manifest: bool = False,
    manifest_sections: list[str] | None = None,
) -> SimpleNamespace:
    planning = base / "planning"
    section_number = int(section.removeprefix("section-").split("-", 1)[0])
    if manifest_sections is not None:
        sections = planning / "sections"
        sections.mkdir(parents=True, exist_ok=True)
        (sections / "index.md").write_text(
            "<!-- PROJECT_CONFIG\n"
            "runtime: python-uv\n"
            "test_command: uv run pytest\n"
            "END_PROJECT_CONFIG -->\n\n"
            "<!-- SECTION_MANIFEST\n"
            + "\n".join(manifest_sections)
            + "\nEND_MANIFEST -->\n"
        )
        for manifest_section in manifest_sections:
            (sections / f"{manifest_section}.md").write_text(
                f"# {manifest_section}\n\n"
                "REQ-001 changes `scripts/zagrosi_skills.py` and `tests/test_zagrosi_skills.py`.\n"
                "Tests first, expected failure, implementation, acceptance, rollback, and verification.\n"
            )
        write_required_plan_artifacts(planning)
    elif section_number == 1 or single_manifest:
        sections = write_single_section_fixture(planning, section)
    else:
        sections = planning / "sections"
        sections.mkdir(parents=True, exist_ok=True)
        manifest = ["section-01-foundation"] + [
            f"section-{number:02d}-placeholder" for number in range(2, section_number)
        ] + [section]
        (sections / "index.md").write_text(
            "<!-- PROJECT_CONFIG\n"
            "runtime: python-uv\n"
            "test_command: uv run pytest\n"
            "END_PROJECT_CONFIG -->\n\n"
            "<!-- SECTION_MANIFEST\n"
            + "\n".join(manifest)
            + "\nEND_MANIFEST -->\n"
        )
        for manifest_section in manifest:
            (sections / f"{manifest_section}.md").write_text(
                f"# {manifest_section}\n\n"
                "REQ-001 changes `scripts/zagrosi_skills.py` and `tests/test_zagrosi_skills.py`.\n"
                "Tests first, expected failure, implementation, acceptance, rollback, and verification.\n"
            )
        write_required_plan_artifacts(planning)
    target = base / "target"
    target.mkdir()
    (target / "tests" / "cutover").mkdir(parents=True)
    (target / "scripts" / "cutover").mkdir(parents=True)
    implementation_root = base / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        base / "admission-pinner.json",
        planning_dir=planning,
    )
    script = plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"]
    setup = run_script_raw(
        script,
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(plugin_root),
        "--flight",
        "off",
    )
    assert setup.returncode == 0, setup.stderr + setup.stdout
    review_dir = implementation_root / "code_review"
    (review_dir / f"{section}-review.md").write_text("# Review\n\nNo blockers.\n")
    (review_dir / f"{section}-decisions.md").write_text("# Decisions\n\nAccepted.\n")
    evidence_path = implementation_root / "evidence" / "record-gate.json"
    evidence_path.write_bytes(b'{"schema":"test-record-gate-v1","verdict":"PASS"}\n')
    evidence_path.chmod(0o600)
    return SimpleNamespace(
        planning=planning,
        sections=sections,
        target=target,
        implementation_root=implementation_root,
        admission_pinner=admission_pinner,
        script=script,
        section=section,
        evidence_path=evidence_path,
    )


def detached_record_arguments(fixture: SimpleNamespace, *extra: str) -> list[str]:
    return [
        "implement-record-section",
        "--sections-dir",
        str(fixture.sections),
        "--implementation-root",
        str(fixture.implementation_root),
        "--section",
        fixture.section,
        "--commit",
        "abc123",
        "--review-artifact",
        f"code_review/{fixture.section}-review.md",
        "--review-artifact",
        f"code_review/{fixture.section}-decisions.md",
        "--verification",
        "uv run pytest tests/test_section.py",
        *extra,
        "--flight",
        "off",
    ]


def assert_no_detached_section_record(fixture: SimpleNamespace) -> None:
    assert list((fixture.implementation_root / "pinners").glob("*.json")) == []
    state = assert_canonical_json_file(fixture.implementation_root / "zagrosi_implement_state.json")
    assert state["completed_sections"] == {}


def test_project_setup_and_create_dirs(tmp_path: Path) -> None:
    req = tmp_path / "requirements.md"
    req.write_text("# Build a SaaS app\n\nAuth, billing, dashboard.\n")

    setup = run_cmd("project-setup", "--file", str(req))
    assert setup["success"] is True
    assert setup["resume_step"] == 1
    assert setup["preflight"]["phase"] == "project"
    assert setup["preflight"]["stage"] == "preflight"

    (tmp_path / "project-manifest.md").write_text(
        "<!-- SPLIT_MANIFEST\n"
        "01-auth\n"
        "02-billing\n"
        "END_MANIFEST -->\n\n"
        "# Project Manifest\n"
    )
    created = run_cmd("project-create-dirs", "--planning-dir", str(tmp_path))
    assert created["splits"] == ["01-auth", "02-billing"]
    assert created["postflight"]["phase"] == "project"
    assert (tmp_path / "01-auth").is_dir()
    assert (tmp_path / "02-billing").is_dir()
    assert str(tmp_path / "01-auth" / "spec.md") in created["missing_specs"]


def test_project_setup_from_chat_brief_materializes_requirements(tmp_path: Path) -> None:
    brief = "Improve Zagrosi Forge so project decomposition can start from a chat idea and interview."

    setup = run_cmd("project-setup", "--brief", brief, "--planning-dir", str(tmp_path))

    generated = tmp_path / "requirements.md"
    assert setup["success"] is True
    assert setup["input_mode"] == "chat"
    assert setup["initial_file"] == str(generated)
    assert setup["generated_requirements_file"] == str(generated)
    assert setup["resume_step"] == 1
    assert setup["preflight"]["input_mode"] == "chat"
    assert setup["preflight"]["gates"][0]["name"] == "chat-brief"
    assert generated.exists()
    assert brief in generated.read_text()

    resumed = run_cmd("project-setup", "--brief", brief, "--planning-dir", str(tmp_path))
    assert resumed["mode"] == "resume"
    assert resumed["initial_file"] == str(generated)
    assert resumed["generated_requirements_file"] is None
    assert not (tmp_path / "requirements-2.md").exists()

    preflight = run_cmd("preflight", "--phase", "project", "--brief", brief, "--planning-dir", str(tmp_path))
    assert preflight["success"] is True
    assert preflight["input_mode"] == "chat"


def test_plan_setup_sections_and_prompts(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Auth\n\nAdd OAuth login.\n")

    setup = run_cmd("plan-setup", "--file", str(spec), "--plugin-root", str(ROOT))
    assert setup["success"] is True
    assert setup["resume_step"] == 6
    assert setup["preflight"]["phase"] == "plan"
    assert (tmp_path / "zagrosi_plan_config.json").exists()
    assert (tmp_path / "decisions.md").exists()
    assert (tmp_path / "risk-register.md").exists()

    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-foundation\n"
        "section-02-oauth\n"
        "END_MANIFEST -->\n\n"
        "# Sections\n"
    )

    checked = run_cmd("plan-check-sections", "--planning-dir", str(tmp_path))
    assert checked["state"] == "has_index"
    assert checked["missing"] == ["section-01-foundation", "section-02-oauth"]

    prompts = run_cmd(
        "plan-generate-section-prompts",
        "--planning-dir",
        str(tmp_path),
        "--batch-size",
        "1",
    )
    assert len(prompts["prompt_files"]) == 1
    assert Path(prompts["prompt_files"][0]).exists()


def test_parallel_plan_parses_documented_dependency_graph_prose(tmp_path: Path) -> None:
    sections = tmp_path / "sections"
    sections.mkdir(parents=True)
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-foundation\n"
        "section-02-api\n"
        "section-03-ui\n"
        "END_MANIFEST -->\n\n"
        "# Sections\n\n"
        "## Dependency Graph\n\n"
        "- section-02-api depends on section-01-foundation.\n"
        "- `section-03-ui` depends on `section-02-api`.\n"
    )

    parallel = run_cmd("parallel-plan", "--planning-dir", str(tmp_path))

    assert parallel["layers"] == [
        ["section-01-foundation"],
        ["section-02-api"],
        ["section-03-ui"],
    ]


def test_parallel_plan_reports_unknown_dependency_tokens(tmp_path: Path) -> None:
    sections = tmp_path / "sections"
    sections.mkdir(parents=True)
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-foundation\n"
        "section-02-api\n"
        "END_MANIFEST -->\n\n"
        "# Sections\n\n"
        "## Dependency Graph\n\n"
        "- section-02-api depends on section-99-missing.\n"
    )

    result = run_raw("parallel-plan", "--planning-dir", str(tmp_path))

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["unknown_dependencies"] == {"section-02-api": ["section-99-missing"]}
    assert payload["blocked_or_cyclic"] == ["section-02-api"]


def test_status_reports_plan_artifact_sequence(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Improve Forge\n\nMake operator workflows clearer.\n")
    run_cmd("plan-setup", "--file", str(spec), "--plugin-root", str(ROOT), "--flight", "off")

    status = run_cmd("status", "--path", str(tmp_path))
    assert "codex-research.md" in status["next_action"]

    (tmp_path / "codex-research.md").write_text("# Research\n\nCurrent state verified with `rg` and `uv run pytest`.\n")
    status = run_cmd("status", "--path", str(tmp_path))
    assert "codex-interview.md" in status["next_action"]

    (tmp_path / "codex-interview.md").write_text(
        "interview_mode: skipped_with_reason\n"
        "skip_reason: Fixture has enough detail to proceed.\n"
    )
    status = run_cmd("status", "--path", str(tmp_path))
    assert "codex-spec.md" in status["next_action"]

    (tmp_path / "codex-spec.md").write_text("# Spec\n\nREQ-001: Improve status.\n")
    status = run_cmd("status", "--path", str(tmp_path))
    assert "codex-plan.md" in status["next_action"]

    (tmp_path / "codex-plan.md").write_text("")
    status = run_cmd("status", "--path", str(tmp_path))
    assert "codex-plan.md" in status["next_action"]

    (tmp_path / "codex-plan.md").write_text("# Plan\n\nREQ-001 implementation plan.\n")
    status = run_cmd("status", "--path", str(tmp_path))
    assert "review" in status["next_action"].lower()

    (tmp_path / "codex-integration-notes.md").write_text("# Review Integration\n\nAccepted review items.\n")
    status = run_cmd("status", "--path", str(tmp_path))
    assert "codex-plan-tdd.md" in status["next_action"]

    (tmp_path / "codex-plan-tdd.md").write_text("# TDD\n\n`test_status_reports_plan_artifact_sequence` fails first.\n")
    status = run_cmd("status", "--path", str(tmp_path))
    assert "sections/index.md" in status["next_action"]

    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-status\n"
        "END_MANIFEST -->\n\n"
        "# Sections\n"
    )
    status = run_cmd("status", "--path", str(tmp_path))
    assert "section files" in status["next_action"]


def test_status_exposes_plan_artifact_state(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Improve Forge\n\nExpose plan artifact state.\n")
    run_cmd("plan-setup", "--file", str(spec), "--plugin-root", str(ROOT), "--flight", "off")
    (tmp_path / "codex-research.md").write_text("# Research\n\nVerified current state.\n")
    (tmp_path / "codex-plan.md").write_text("   ")

    status = run_cmd("status", "--path", str(tmp_path))

    assert status["files"]["zagrosi_plan_config"] == str(tmp_path / "zagrosi_plan_config.json")
    assert status["plan_artifacts"]["research"] == str(tmp_path / "codex-research.md")
    assert status["plan_artifacts"]["interview"] is None
    assert status["plan_artifacts"]["plan"] is None
    assert status["plan_artifacts"]["section_index"] is None
    assert status["section_progress"]["state"] == "no_index"


def test_commands_catalog_outputs_grouped_json_and_pretty_text() -> None:
    catalog = run_cmd("commands")

    required = {"project-setup", "plan-setup", "implement-setup", "status", "codebase-evidence", "eval-suite", "release-check"}
    by_name = {entry["name"]: entry for entry in catalog["commands"]}
    assert required <= set(by_name)
    for name in required:
        entry = by_name[name]
        assert entry["phase"]
        assert entry["summary"]
        assert isinstance(entry["aliases"], list)
        assert entry["examples"]

    plan_catalog = run_cmd("commands", "--phase", "plan")
    assert plan_catalog["commands"]
    assert {entry["phase"] for entry in plan_catalog["commands"]} <= {"plan", "all", "quality", "utility"}

    pretty = run_text("commands", "--pretty")
    assert "PLAN" in pretty.upper()
    assert "status" in pretty
    assert "codebase-evidence" in pretty


def test_command_catalog_matches_parser_aliases() -> None:
    catalog = run_cmd("commands")
    entries = catalog["commands"]
    names = {entry["name"] for entry in entries}
    aliases = {alias for entry in entries for alias in entry["aliases"]}

    assert {"project-setup", "plan-setup", "implement-setup", "status", "doctor", "eval-suite", "release-check"} <= names
    assert {
        "project",
        "plan",
        "implement",
        "install",
        "deep-project-setup",
        "deep-plan-setup",
        "deep-implement-setup",
    } <= aliases

    help_text = run_text("--help")
    assert "Inspect workflow state" in help_text
    assert "Show grouped command catalog" in help_text


def test_codebase_evidence_includes_forge_surface_without_cache_noise(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    planning.mkdir()

    evidence = run_cmd("codebase-evidence", "--target-dir", str(ROOT), "--planning-dir", str(planning), "--write")

    assert "scripts/zagrosi_skills.py" in evidence["source_files"]
    assert "skills/zagrosi-plan/SKILL.md" in evidence["skill_files"]
    assert ".codex-plugin/plugin.json" in evidence["plugin_metadata"]
    assert ".github/workflows/validate.yml" in evidence["ci_files"]
    assert "examples/evals/suite.json" in evidence["example_files"]

    grouped_paths = [
        path
        for key in ("runtime_files", "test_files", "source_files", "skill_files", "plugin_metadata", "ci_files", "example_files")
        for path in evidence[key]
    ]
    assert not any(".git/" in path or ".venv/" in path or "__pycache__/" in path for path in grouped_paths)
    assert not any(".codex/plugins/cache" in path for path in grouped_paths)

    written = Path(evidence["output"]).read_text()
    assert "Forge Source Files" in written
    assert "Skills" in written
    assert "Assumptions / Open Questions" in written


def test_lint_evidence_accepts_expanded_codebase_evidence(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    (target / "scripts").mkdir()
    (target / "scripts" / "tool.py").write_text("print('fixture')\n")
    (target / "tests").mkdir()
    (target / "tests" / "test_tool.py").write_text("def test_tool():\n    assert True\n")

    planning = tmp_path / "planning"
    planning.mkdir()
    run_cmd("codebase-evidence", "--target-dir", str(target), "--planning-dir", str(planning), "--write")
    (planning / "codex-plan.md").write_text(
        "# Plan\n\n"
        "REQ-006: Current state verified with `uv run pytest`; see `codex-evidence.md` for existing files.\n"
        "Assumption: no open question blocks evidence linting.\n"
    )

    evidence = run_cmd("lint-evidence", "--planning-dir", str(planning), "--strict")

    assert evidence["success"] is True
    assert evidence["file_count"] >= 3
    assert "codex-evidence.md" in evidence["artifacts"]


def test_implement_setup_and_record(tmp_path: Path) -> None:
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-foundation\n"
        "END_MANIFEST -->\n"
    )
    (sections / "section-01-foundation.md").write_text("# Section\n\nTests first.\n")
    write_required_plan_artifacts(tmp_path)

    setup = run_cmd(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(tmp_path),
    )
    assert setup["success"] is True
    assert setup["next_section"] == "section-01-foundation"
    assert setup["preflight"]["phase"] == "implement"
    assert (tmp_path / "implementation" / "zagrosi_implement_config.json").exists()

    record = run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-01-foundation",
        "--commit",
        "abc123",
    )
    assert record["success"] is True
    assert record["postflight"]["phase"] == "implement"
    state = json.loads((tmp_path / "implementation" / "zagrosi_implement_state.json").read_text())
    assert state["completed_sections"]["section-01-foundation"]["commit"] == "abc123"

    impl_gate = run_cmd("lint-implementation-state", "--sections-dir", str(sections))
    assert impl_gate["success"] is True
    assert "section-01-foundation" in impl_gate["completed_sections"]


def test_implement_record_section_refreshes_traceability_matrix(tmp_path: Path) -> None:
    sections = tmp_path / "sections"
    sections.mkdir()
    (tmp_path / "codex-spec.md").write_text("# Spec\n\nREQ-001: Implement status.\nREQ-002: Document status.\n")
    (tmp_path / "codex-plan.md").write_text("# Plan\n\nREQ-001 in `scripts/tool.py`.\nREQ-002 in `README.md`.\n")
    (tmp_path / "codex-plan-tdd.md").write_text(
        "# TDD\n\nREQ-001: `test_status_flow`.\nREQ-002: `test_readme_status_docs`.\n"
    )
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-status\n"
        "section-02-docs\n"
        "END_MANIFEST -->\n"
    )
    (sections / "section-01-status.md").write_text("# Section\n\nREQ-001 with `test_status_flow`.\n")
    (sections / "section-02-docs.md").write_text("# Section\n\nREQ-002 with `test_readme_status_docs`.\n")
    write_required_plan_artifacts(tmp_path)
    (tmp_path / "traceability.md").write_text(
        "# Traceability Matrix\n\n"
        "| Requirement | Plan Coverage | Section Coverage | Test Coverage | Status |\n"
        "|-------------|---------------|------------------|---------------|--------|\n"
        "| REQ-001 | `codex-plan.md` | `section-01-status.md` | `test_status_flow` | Planned |\n"
        "| REQ-002 | `codex-plan.md` | `section-02-docs.md` | `test_readme_status_docs` | Planned |\n"
    )

    recorded = run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-01-status",
        "--commit",
        "abc123",
        "--flight",
        "off",
    )

    matrix = (tmp_path / "traceability.md").read_text()
    assert recorded["traceability_matrix"] == str(tmp_path / "traceability.md")
    assert "| Requirement | Plan Coverage | Section Coverage | Test Coverage | Implementation Evidence | Status |" in matrix
    assert (
        "| REQ-001 | `codex-plan.md` | `section-01-status.md` | `test_status_flow` | commit `abc123` | Implemented |"
        in matrix
    )
    assert (
        "| REQ-002 | `codex-plan.md` | `section-02-docs.md` | `test_readme_status_docs` | - | Planned |"
        in matrix
    )


def test_implement_record_section_stores_evidence_and_refreshes_traceability(tmp_path: Path) -> None:
    sections = write_single_section_fixture(tmp_path)
    (tmp_path / "implementation" / "code_review" / "section-01-foundation-decisions.md").write_text(
        "# Decisions\n\nAccepted implementation evidence and traceability updates.\n"
    )

    recorded = run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-01-foundation",
        "--commit",
        "abc123",
        "--file",
        "scripts/zagrosi_skills.py",
        "--file",
        "scripts/zagrosi_skills.py",
        "--test-file",
        "tests/test_zagrosi_skills.py",
        "--review-artifact",
        "implementation/code_review/section-01-foundation-review.md",
        "--review-artifact",
        "implementation/code_review/section-01-foundation-decisions.md",
        "--verification",
        "uv run pytest",
        "--flight",
        "off",
    )

    assert recorded["success"] is True
    record = recorded["record"]
    assert record["files_changed"] == ["scripts/zagrosi_skills.py"]
    assert record["test_files"] == ["tests/test_zagrosi_skills.py"]
    assert record["review_artifacts"] == [
        "implementation/code_review/section-01-foundation-review.md",
        "implementation/code_review/section-01-foundation-decisions.md",
    ]
    assert record["verification"] == ["uv run pytest"]
    assert record["commit_status"] == "recorded"

    state = json.loads((tmp_path / "implementation" / "zagrosi_implement_state.json").read_text())
    persisted = state["completed_sections"]["section-01-foundation"]
    assert persisted["files_changed"] == ["scripts/zagrosi_skills.py"]
    assert persisted["test_files"] == ["tests/test_zagrosi_skills.py"]

    matrix = (tmp_path / "traceability.md").read_text()
    assert "Implementation Evidence" in matrix
    assert "abc123" in matrix
    assert "scripts/zagrosi_skills.py" in matrix
    assert "tests/test_zagrosi_skills.py" in matrix


def test_traceability_handles_legacy_implementation_records(tmp_path: Path) -> None:
    write_single_section_fixture(tmp_path)
    state_path = tmp_path / "implementation" / "zagrosi_implement_state.json"
    state_path.write_text(
        json.dumps(
            {
                "completed_sections": {
                    "section-01-foundation": {
                        "completed_at": "2026-05-18T00:00:00+00:00",
                        "commit": "abc123",
                        "notes": "legacy record",
                    }
                }
            }
        )
    )

    trace = run_cmd("traceability", "--planning-dir", str(tmp_path), "--strict")
    assert trace["success"] is True
    assert trace["implementation_evidence"]["section-01-foundation"]["commit"] == "abc123"


def test_implementation_state_requires_review_decisions(tmp_path: Path) -> None:
    sections = write_single_section_fixture(tmp_path)
    run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-01-foundation",
        "--commit",
        "abc123",
        "--file",
        "scripts/zagrosi_skills.py",
        "--test-file",
        "tests/test_zagrosi_skills.py",
        "--review-artifact",
        "implementation/code_review/section-01-foundation-review.md",
        "--verification",
        "uv run pytest",
        "--flight",
        "off",
    )
    (tmp_path / "implementation" / "usage.md").write_text("# Usage\n\nRun `uv run pytest`.\n")

    missing = run_raw("lint-implementation-state", "--sections-dir", str(sections), "--strict")
    assert missing.returncode != 0
    payload = json.loads(missing.stdout)
    assert "missing-review-decisions" in {item["code"] for item in payload["findings"]}

    (tmp_path / "implementation" / "code_review" / "section-01-foundation-decisions.md").write_text(
        "# Decisions\n\nAccepted all review items.\n"
    )
    passed = run_cmd("lint-implementation-state", "--sections-dir", str(sections), "--strict")
    assert passed["success"] is True


def test_implement_setup_blocks_incomplete_forge_process_even_with_flight_off(tmp_path: Path) -> None:
    sections = tmp_path / "sections"
    sections.mkdir()
    (tmp_path / "spec.md").write_text("# Fix Forge\n\nREQ-001: Fix workflow shortcuts.\n")
    (tmp_path / "decisions.md").write_text(
        "# Decision Log\n\n"
        "| ID | Date | Decision | Alternatives | Rationale | Impact |\n"
        "|----|------|----------|--------------|-----------|--------|\n"
        "| DEC-001 | TBD | TBD | TBD | TBD | TBD |\n"
    )
    (tmp_path / "risk-register.md").write_text(
        "# Risk Register\n\n"
        "| ID | Risk | Severity | Likelihood | Mitigation | Section | Verification |\n"
        "|----|------|----------|------------|------------|---------|--------------|\n"
        "| RISK-001 | TBD | TBD | TBD | TBD | TBD | TBD |\n"
    )
    (tmp_path / "traceability.md").write_text(
        "# Traceability Matrix\n\n"
        "| Requirement | Plan Coverage | Section Coverage | Test Coverage | Status |\n"
        "|-------------|---------------|------------------|---------------|--------|\n"
        "| REQ-001 | TBD | TBD | TBD | TBD |\n"
    )
    (tmp_path / "quality-gates.md").write_text("# Quality Gates\n\n- `lint-plan`\n")
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-shortcut\n"
        "END_MANIFEST -->\n"
    )
    (sections / "section-01-shortcut.md").write_text("# Section\n\nTests first in `tests/test_zagrosi_skills.py`.\n")

    result = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(tmp_path),
        "--flight",
        "off",
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert payload["gate"] == "plan-artifacts"
    codes = {item["code"] for item in payload["findings"]}
    assert "missing-research" in codes
    assert "missing-plan" in codes
    assert "placeholder-decisions" in codes


def test_detached_implementation_mode_uses_dependency_ready_order_and_preserves_planning_bytes(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    expected_planning = planning_tree_snapshot(planning)

    setup = run_cmd(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )

    assert setup["mode"] == "detached-frozen"
    assert setup["next_section"] == "section-03-storage"
    assert setup["ready_sections"] == ["section-03-storage"]
    assert setup["implementation_root"] == str(implementation_root)
    assert planning_tree_snapshot(planning) == expected_planning


    assert not (planning / "implementation").exists()
    config = assert_canonical_json_file(implementation_root / "zagrosi_implement_config.json")
    assert config["schema"] == "zagrosi-detached-implementation-config-v2"
    assert config["planning_tree_sha256"] == setup["planning_tree_sha256"]
    assert config["admission_state_sha256"] == admission_pinner_payload(planning)["start"]["a_sha256"]
    assert config["detached_implementation_root_identity_digest"] == detached_root_identity_digest_for_test(
        implementation_root
    )
    assert config["target_root_identity_digest"] == target_root_identity_digest_for_test(target)
    for source, relative in IMPLEMENTATION_SOURCE_RELATIVE_PATHS.items():
        source_path = ROOT / relative
        assert config[f"implement_{source}_path"] == str(source_path)
        assert config[f"implement_{source}_sha256"] == file_sha256(source_path)
        assert config[f"implement_{source}_size"] == source_path.stat().st_size
    assert_canonical_json_file(implementation_root / "zagrosi_implement_state.json")

    progress = run_cmd(
        "implement-progress",
        "--planning-dir",
        str(planning),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-03-storage",
        "--stage",
        "red",
        "--result",
        "focused test failed as expected",
    )
    assert progress["mode"] == "detached-frozen"
    assert progress["planning_tree_sha256"] == setup["planning_tree_sha256"]
    assert planning_tree_snapshot(planning) == expected_planning
    assert_canonical_json_file(implementation_root / "forge-progress.json")

    review_dir = implementation_root / "code_review"
    (review_dir / "section-03-storage-review.md").write_text("# Review\n\nNo blockers.\n")
    (review_dir / "section-03-storage-decisions.md").write_text("# Decisions\n\nAccepted.\n")
    evidence_path = implementation_root / "evidence" / "storage-gate.json"
    evidence_path.write_bytes(b'{"schema":"test-storage-gate-v1","verdict":"PASS"}\n')
    evidence_path.chmod(0o600)
    record = run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-03-storage",
        "--commit",
        "abc123",
        "--review-artifact",
        "code_review/section-03-storage-review.md",
        "--review-artifact",
        "code_review/section-03-storage-decisions.md",
        "--evidence-row",
        "storage_gate=evidence/storage-gate.json",
        "--verification",
        "uv run pytest tests/test_storage.py",
        "--flight",
        "off",
    )
    assert record["mode"] == "detached-frozen"
    assert record["traceability_matrix"] is None
    assert record["planning_tree_sha256"] == setup["planning_tree_sha256"]
    assert planning_tree_snapshot(planning) == expected_planning
    pinner_path = Path(record["pinner_path"])
    pinner = assert_canonical_json_file(pinner_path)
    assert pinner["schema"] == "zagrosi-implementation-section-pinner-v2"
    assert pinner["section"] == "section-03-storage"
    assert pinner["predecessor_pinners"] == []
    assert "receipt_sha256" not in pinner
    assert pinner["admission_pinner_sha256"] == setup["admission_pinner_sha256"]
    assert pinner["admission_state_sha256"] == config["admission_state_sha256"]
    assert pinner["detached_implementation_root_identity_digest"] == config[
        "detached_implementation_root_identity_digest"
    ]
    assert pinner["target_root_identity_digest"] == config["target_root_identity_digest"]
    assert pinner["implement_tool_sha256"] == config["implement_tool_sha256"]
    assert pinner["implement_skill_sha256"] == config["implement_skill_sha256"]
    assert pinner["implement_test_sha256"] == config["implement_test_sha256"]
    assert pinner["evidence_rows"] == [
        {
            "name": "storage_gate",
            "path": "evidence/storage-gate.json",
            "sha256": "sha256:" + hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            "size": len(evidence_path.read_bytes()),
        }
    ]
    state = assert_canonical_json_file(implementation_root / "zagrosi_implement_state.json")
    assert state["admission_state_sha256"] == config["admission_state_sha256"]
    assert state["detached_implementation_root_identity_digest"] == config[
        "detached_implementation_root_identity_digest"
    ]
    assert state["target_root_identity_digest"] == config["target_root_identity_digest"]
    assert state["completed_sections"]["section-03-storage"]["pinner_file_sha256"] == record["pinner_file_sha256"]

    next_ready = run_cmd(
        "next-section",
        "--planning-dir",
        str(planning),
        "--implementation-root",
        str(implementation_root),
    )
    assert next_ready["mode"] == "detached-frozen"
    assert next_ready["next_section"] == "section-01-foundation"
    assert next_ready["ready_sections"] == ["section-01-foundation"]
    assert next_ready["planning_tree_sha256"] == setup["planning_tree_sha256"]
    assert planning_tree_snapshot(planning) == expected_planning

    (review_dir / "section-01-foundation-review.md").write_text("# Review\n\nNo blockers.\n")
    (review_dir / "section-01-foundation-decisions.md").write_text("# Decisions\n\nAccepted.\n")
    dependent = run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-01-foundation",
        "--commit",
        "def456",
        "--review-artifact",
        "code_review/section-01-foundation-review.md",
        "--review-artifact",
        "code_review/section-01-foundation-decisions.md",
        "--verification",
        "uv run pytest tests/test_foundation.py",
        "--flight",
        "off",
    )
    dependent_pinner = assert_canonical_json_file(Path(dependent["pinner_path"]))
    predecessor_state = state["completed_sections"]["section-03-storage"]
    assert dependent_pinner["predecessor_pinners"] == [
        {
            "section": "section-03-storage",
            "pinner_path": predecessor_state["pinner_path"],
            "pinner_file_sha256": predecessor_state["pinner_file_sha256"],
        }
    ]
    assert dependent["next_section"] == "section-02-api"
    assert planning_tree_snapshot(planning) == expected_planning


def test_identical_same_second_leaf_rerecord_refuses_before_transaction_publication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "same-second-noop")
    fixture = make_detached_record_fixture(tmp_path / "fixture-same-second-noop", plugin_root=plugin_root)
    module = load_zagrosi_module(fixture.script)
    monkeypatch.setattr(module, "now_iso", lambda: "2026-08-22T12:00:00Z")
    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(
        module,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(detached_record_arguments(fixture))
    assert parsed.func(parsed) == 0
    assert captured[-1][0]["transaction_status"] == "committed-clean"
    before = tree_bytes_metadata_snapshot(fixture.implementation_root)
    publication_called = False

    def refuse_publication(*args, **kwargs):
        nonlocal publication_called
        publication_called = True
        raise AssertionError("no-op rerecord reached staged pinner publication")

    monkeypatch.setattr(module, "publish_section_record_staged_pinner", refuse_publication)
    captured.clear()
    assert parsed.func(parsed) == 1
    assert captured[-1][0]["error_code"] == "section-record-state-conflict"
    assert publication_called is False
    assert tree_bytes_metadata_snapshot(fixture.implementation_root) == before
    assert not (
        fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    ).exists()


@pytest.mark.parametrize(
    ("crashpoint", "recovers_completed"),
    (
        ("pinner-tmp-partial", False),
        ("pinner-tmp-write-fsync", False),
        ("pinner-rename-before-dir-fsync", False),
        ("pinner-rename-dir-fsync", False),
        ("journal-write-temp-partial", False),
        ("journal-write-temp-fsync", False),
        ("journal-temp-rename-before-dir-fsync", False),
        ("journal-temp-rename-fsync", False),
        ("journal-rename-before-dir-fsync", True),
        ("journal-rename-fsync", True),
        ("final-link-before-dir-fsync", True),
        ("final-link-fsync", True),
        ("forward-state-temp-fsync", True),
        ("forward-state-replace-before-root-fsync", True),
        ("state-cas-fsync", True),
        ("post-state-validation", True),
        ("journal-unlink-fsync", True),
        ("journal-unlink-before-dir-fsync", True),
        ("stage-cleanup-fsync", True),
        ("transaction-rmdir-before-parent-fsync", True),
    ),
)
def test_section_record_real_sigkill_recovers_every_durable_edge(
    tmp_path: Path,
    crashpoint: str,
    recovers_completed: bool,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / crashpoint)
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-{crashpoint}",
        plugin_root=plugin_root,
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = crashpoint
    crashed = run_script_raw(
        script,
        *detached_record_arguments(fixture),
        env=environment,
    )
    assert crashed.returncode == -signal.SIGKILL

    final_pinners_before_recovery = list(
        (fixture.implementation_root / "pinners").glob("section-01-foundation-*.json")
    )
    for pinner_path in final_pinners_before_recovery:
        raw = pinner_path.read_bytes()
        assert canonical_json_bytes_for_test(json.loads(raw)) == raw

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    payload = json.loads(recovered.stdout)
    state = assert_canonical_json_file(fixture.implementation_root / "zagrosi_implement_state.json")
    assert (fixture.section in state["completed_sections"]) is recovers_completed
    assert (payload["next_section"] is None) is recovers_completed
    assert not (
        fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    ).exists()
    assert set(path.name for path in fixture.implementation_root.iterdir()) == {
        "code_review",
        "evidence",
        "pinners",
        "zagrosi_implement_config.json",
        "zagrosi_implement_state.json",
        "forge-progress.json",
    }
    marker = fixture.implementation_root / "pinners" / ".record-section.lock"
    assert marker.is_file()
    assert marker.stat().st_mode & 0o777 == 0o600
    final_pinners = list(
        (fixture.implementation_root / "pinners").glob("section-01-foundation-*.json")
    )
    assert len(final_pinners) == int(recovers_completed)
    if final_pinners:
        assert final_pinners[0].stat().st_nlink == 1


@pytest.mark.parametrize("mutation", ("noncanonical", "candidate-missing-final", "unknown-section"))
def test_no_journal_published_stage_requires_canonical_known_current_state_join(
    tmp_path: Path,
    mutation: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"no-journal-stage-{mutation}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-no-journal-stage-{mutation}",
        plugin_root=plugin_root,
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "pinner-rename-dir-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    staged_path = transaction_dir / "pinner.json"
    if mutation == "noncanonical":
        replace_file(staged_path, b'{"partial":', mode=0o600)
    else:
        staged_raw = staged_path.read_bytes()
        staged = json.loads(staged_raw)
        if mutation == "unknown-section":
            staged["section"] = "section-99-unknown"
            replace_file(staged_path, canonical_json_bytes_for_test(staged), mode=0o600)
        else:
            state_path = fixture.implementation_root / "zagrosi_implement_state.json"
            state = assert_canonical_json_file(state_path)
            state["completed_sections"][fixture.section] = pinner_state_record_for_test(
                staged,
                staged_raw,
            )
            replace_file(state_path, canonical_json_bytes_for_test(state), mode=0o600)
    before = planning_tree_snapshot(fixture.implementation_root)

    result = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] in {
        "section-record-recovery-required",
        "invalid-section-record-transaction",
        "invalid-section-pinner",
    }
    assert planning_tree_snapshot(fixture.implementation_root) == before


@pytest.mark.parametrize("committed_candidate", (False, True))
def test_no_journal_exact_distinct_final_is_preserved_and_only_candidate_is_committed(
    tmp_path: Path,
    committed_candidate: bool,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"no-journal-adopted-{committed_candidate}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-no-journal-adopted-{committed_candidate}",
        plugin_root=plugin_root,
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "pinner-rename-dir-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    staged_path = transaction_dir / "pinner.json"
    staged_raw = staged_path.read_bytes()
    staged = json.loads(staged_raw)
    state_record = pinner_state_record_for_test(staged, staged_raw)
    final_path = fixture.implementation_root / state_record["pinner_path"]
    final_path.write_bytes(staged_raw)
    final_path.chmod(0o600)
    assert os.stat(staged_path).st_ino != os.stat(final_path).st_ino
    if committed_candidate:
        state_path = fixture.implementation_root / "zagrosi_implement_state.json"
        state = assert_canonical_json_file(state_path)
        state["completed_sections"][fixture.section] = state_record
        replace_file(state_path, canonical_json_bytes_for_test(state), mode=0o600)

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    state = assert_canonical_json_file(fixture.implementation_root / "zagrosi_implement_state.json")
    assert (fixture.section in state["completed_sections"]) is committed_candidate
    assert final_path.is_file()
    assert final_path.stat().st_nlink == 1
    assert not transaction_dir.exists()


def test_no_journal_base_refuses_wrong_distinct_orphan_without_mutation(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "no-journal-wrong-orphan")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-no-journal-wrong-orphan", plugin_root=plugin_root)
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "pinner-rename-dir-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    staged_path = transaction_dir / "pinner.json"
    staged_raw = staged_path.read_bytes()
    staged = json.loads(staged_raw)
    state_record = pinner_state_record_for_test(staged, staged_raw)
    final_path = fixture.implementation_root / state_record["pinner_path"]
    final_path.write_bytes(canonical_json_bytes_for_test({**staged, "notes": "wrong orphan"}))
    final_path.chmod(0o600)
    before = planning_tree_snapshot(fixture.implementation_root)

    result = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] in {
        "section-record-recovery-required",
        "invalid-detached-json",
    }
    assert planning_tree_snapshot(fixture.implementation_root) == before


@pytest.mark.parametrize("mutation", ("noncanonical", "candidate"))
def test_published_transaction_temp_requires_exact_canonical_base_projection(
    tmp_path: Path,
    mutation: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"transaction-temp-{mutation}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-transaction-temp-{mutation}",
        plugin_root=plugin_root,
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "journal-temp-rename-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction_temp = transaction_dir / "transaction.tmp"
    if mutation == "noncanonical":
        replace_file(transaction_temp, b'{"partial":', mode=0o600)
    else:
        transaction = assert_canonical_json_file(transaction_temp)
        state_path = fixture.implementation_root / "zagrosi_implement_state.json"
        state = assert_canonical_json_file(state_path)
        state["completed_sections"][transaction["section"]] = transaction["state_record"]
        replace_file(state_path, canonical_json_bytes_for_test(state), mode=0o600)
    before = planning_tree_snapshot(fixture.implementation_root)

    result = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] in {
        "section-record-recovery-required",
        "invalid-detached-json",
    }
    assert planning_tree_snapshot(fixture.implementation_root) == before


@pytest.mark.parametrize("mutation", ("candidate", "mixed-stage"))
def test_journal_write_temp_refuses_candidate_or_mixed_exact_stage(
    tmp_path: Path,
    mutation: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"journal-write-{mutation}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-journal-write-{mutation}",
        plugin_root=plugin_root,
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "journal-write-temp-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.write.tmp")
    if mutation == "candidate":
        state_path = fixture.implementation_root / "zagrosi_implement_state.json"
        state = assert_canonical_json_file(state_path)
        state["completed_sections"][transaction["section"]] = transaction["state_record"]
        replace_file(state_path, canonical_json_bytes_for_test(state), mode=0o600)
    else:
        staged_path = transaction_dir / "pinner.json"
        staged = assert_canonical_json_file(staged_path)
        replace_file(
            staged_path,
            canonical_json_bytes_for_test({**staged, "notes": "different exact stage"}),
            mode=0o600,
        )
    before = planning_tree_snapshot(fixture.implementation_root)

    result = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == "section-record-recovery-required"
    assert planning_tree_snapshot(fixture.implementation_root) == before


@pytest.mark.parametrize("drift_kind", ("review", "evidence"))
def test_post_commit_cleanup_ignores_mutable_review_and_evidence_drift(
    tmp_path: Path,
    drift_kind: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"post-commit-{drift_kind}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / f"fixture-post-commit-{drift_kind}", plugin_root=plugin_root)
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "journal-unlink-fsync"
    crashed = run_script_raw(
        script,
        *detached_record_arguments(
            fixture,
            "--evidence-row",
            "record_gate=evidence/record-gate.json",
        ),
        env=environment,
    )
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    assert transaction_dir.is_dir()
    assert not (transaction_dir / "transaction.json").exists()

    if drift_kind == "review":
        (fixture.implementation_root / "code_review" / f"{fixture.section}-review.md").write_text(
            "# Review\n\nChanged after commit.\n"
        )
    else:
        (fixture.implementation_root / "evidence" / "record-gate.json").write_bytes(
            b'{"schema":"test-record-gate-v1","verdict":"CHANGED"}\n'
        )

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    assert json.loads(recovered.stdout)["completed_sections"] == [fixture.section]
    assert not transaction_dir.exists()


@pytest.mark.parametrize("adopted_final", (False, True))
def test_candidate_recovery_failure_rolls_back_state_and_only_invocation_created_final(
    tmp_path: Path,
    adopted_final: bool,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"candidate-rollback-{adopted_final}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-candidate-rollback-{adopted_final}",
        plugin_root=plugin_root,
    )
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "state-cas-fsync"
    if adopted_final:
        environment["ZAGROSI_TEST_RECORD_ADOPT_PREEXISTING"] = "1"
    crashed = run_script_raw(
        script,
        *detached_record_arguments(
            fixture,
            "--evidence-row",
            "record_gate=evidence/record-gate.json",
        ),
        env=environment,
    )
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
    final_path = fixture.implementation_root / transaction["pinner_path"]
    assert final_path.is_file()
    (fixture.implementation_root / "evidence" / "record-gate.json").write_bytes(
        b'{"schema":"test-record-gate-v1","verdict":"DRIFT"}\n'
    )

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert recovered.returncode == 1
    assert json.loads(recovered.stdout)["error_code"] == "section-record-recovery-required"
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == base_state_raw
    assert final_path.exists() is adopted_final
    assert not transaction_dir.exists()


def test_forward_candidate_with_missing_final_durably_rolls_back_without_repromotion(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "candidate-missing-final")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-candidate-missing-final", plugin_root=plugin_root)
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "state-cas-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
    final_path = fixture.implementation_root / transaction["pinner_path"]
    final_path.unlink()
    assert (transaction_dir / "pinner.json").stat().st_nlink == 1

    rolled_back = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert rolled_back.returncode == 1
    assert json.loads(rolled_back.stdout)["error_code"] == "section-record-recovery-required"
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == base_state_raw
    assert not final_path.exists()
    assert not transaction_dir.exists()

    retried = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert retried.returncode == 0, retried.stderr + retried.stdout
    assert json.loads(retried.stdout)["next_section"] == fixture.section


def test_forward_candidate_missing_final_rollback_survives_real_sigkill_replay(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "candidate-missing-final-sigkill")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / "fixture-candidate-missing-final-sigkill",
        plugin_root=plugin_root,
    )
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    initial_environment = dict(os.environ)
    initial_environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "state-cas-fsync"
    crashed = run_script_raw(script, *detached_record_arguments(fixture), env=initial_environment)
    assert crashed.returncode == -signal.SIGKILL
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
    final_path = fixture.implementation_root / transaction["pinner_path"]
    final_path.unlink()

    rollback_environment = dict(os.environ)
    rollback_environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "rollback-rename-fsync"
    rollback_crashed = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
        env=rollback_environment,
    )
    assert rollback_crashed.returncode == -signal.SIGKILL
    assert (transaction_dir / "rollback.json").is_file()
    assert not (transaction_dir / "transaction.json").exists()

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == base_state_raw
    assert json.loads(recovered.stdout)["next_section"] == fixture.section
    assert not transaction_dir.exists()
    assert not final_path.exists()


def test_rollback_restores_base_before_refusing_drifted_predecessor_closure(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "rollback-predecessor-drift")
    script = instrument_record_crashpoints(plugin_root)
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    setup = run_script_raw(
        script,
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(plugin_root),
        "--flight",
        "off",
    )
    assert setup.returncode == 0, setup.stderr + setup.stdout

    def record_args(section: str, commit: str) -> list[str]:
        review_dir = implementation_root / "code_review"
        (review_dir / f"{section}-review.md").write_text("# Review\n\nNo blockers.\n")
        (review_dir / f"{section}-decisions.md").write_text("# Decisions\n\nAccepted.\n")
        return [
            "implement-record-section",
            "--sections-dir",
            str(sections),
            "--implementation-root",
            str(implementation_root),
            "--section",
            section,
            "--commit",
            commit,
            "--review-artifact",
            f"code_review/{section}-review.md",
            "--review-artifact",
            f"code_review/{section}-decisions.md",
            "--verification",
            f"uv run pytest tests/test_{section}.py",
            "--flight",
            "off",
        ]

    storage = run_script_raw(script, *record_args("section-03-storage", "storage-1"))
    assert storage.returncode == 0, storage.stderr + storage.stdout
    state_path = implementation_root / "zagrosi_implement_state.json"
    base_state_raw = state_path.read_bytes()
    base_state = json.loads(base_state_raw)
    storage_path = implementation_root / base_state["completed_sections"]["section-03-storage"]["pinner_path"]

    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "state-cas-fsync"
    crashed = run_script_raw(
        script,
        *record_args("section-01-foundation", "foundation-1"),
        env=environment,
    )
    assert crashed.returncode == -signal.SIGKILL
    drifted_storage = assert_canonical_json_file(storage_path)
    replace_file(
        storage_path,
        canonical_json_bytes_for_test({**drifted_storage, "notes": "predecessor drift"}),
        mode=0o600,
    )

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(planning),
        "--implementation-root",
        str(implementation_root),
    )
    assert recovered.returncode == 1
    assert json.loads(recovered.stdout)["error_code"] == "section-record-recovery-required"
    assert state_path.read_bytes() == base_state_raw
    transaction_dir = implementation_root / "pinners" / ".record-section-transaction-v1"
    assert (transaction_dir / "rollback.json").is_file()
    assert not (transaction_dir / "transaction.json").exists()


def test_record_adopts_exact_preexisting_pinner_and_preserves_single_link_final(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "adopted-success")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-adopted-success", plugin_root=plugin_root)
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_ADOPT_PREEXISTING"] = "1"
    result = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    final_path = Path(payload["pinner_path"])
    assert final_path.is_file()
    assert final_path.stat().st_nlink == 1
    assert not (
        fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    ).exists()


def test_record_conflicting_preexisting_pinner_is_retained_and_never_adopted(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "adopted-conflict")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-adopted-conflict", plugin_root=plugin_root)
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_ADOPT_PREEXISTING"] = "wrong"
    result = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == "section-record-recovery-required"
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == base_state_raw
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
    final_path = fixture.implementation_root / transaction["pinner_path"]
    assert final_path.is_file()
    assert final_path.stat().st_nlink == 1
    assert final_path.read_bytes() != (transaction_dir / "pinner.json").read_bytes()


@pytest.mark.parametrize("failure", ("state-rollback", "created-final-unlink"))
def test_candidate_recovery_rollback_failure_retains_transaction_for_retry(
    tmp_path: Path,
    monkeypatch,
    failure: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"candidate-rollback-failure-{failure}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-candidate-rollback-failure-{failure}",
        plugin_root=plugin_root,
    )
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "state-cas-fsync"
    crashed = run_script_raw(
        script,
        *detached_record_arguments(
            fixture,
            "--evidence-row",
            "record_gate=evidence/record-gate.json",
        ),
        env=environment,
    )
    assert crashed.returncode == -signal.SIGKILL
    candidate_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
    final_path = fixture.implementation_root / transaction["pinner_path"]
    (fixture.implementation_root / "evidence" / "record-gate.json").write_bytes(
        b'{"schema":"test-record-gate-v1","verdict":"DRIFT"}\n'
    )

    module = load_zagrosi_module(script)
    if failure == "state-rollback":
        monkeypatch.setattr(
            module,
            "replace_state_from_rollback",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected rollback write failure")),
        )
    else:
        monkeypatch.setattr(
            module,
            "unlink_invocation_created_section_pinner",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected pinner unlink failure")),
        )
    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(
        module,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(
        [
            "next-section",
            "--planning-dir",
            str(fixture.planning),
            "--implementation-root",
            str(fixture.implementation_root),
        ]
    )
    assert parsed.func(parsed) == 1
    assert captured[-1][0]["error_code"] == "section-record-recovery-required"
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == candidate_state_raw
    assert (transaction_dir / "rollback.json").is_file()
    assert final_path.exists() is (failure == "created-final-unlink")


@pytest.mark.parametrize(
    "crashpoint",
    (
        "rollback-rename-before-dir-fsync",
        "rollback-rename-fsync",
        "rollback-final-delete-fsync",
        "rollback-state-temp-fsync",
        "rollback-state-replace-before-root-fsync",
        "rollback-state-replace-fsync",
        "rollback-unlink-before-dir-fsync",
        "rollback-unlink-fsync",
        "stage-cleanup-fsync",
    ),
)
def test_section_record_real_sigkill_resumes_durable_rollback_edges(
    tmp_path: Path,
    crashpoint: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / crashpoint)
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / f"fixture-{crashpoint}", plugin_root=plugin_root)
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    environment = dict(os.environ)
    environment.update(
        {
            "ZAGROSI_TEST_RECORD_CRASHPOINT": crashpoint,
            "ZAGROSI_TEST_FORCE_ROLLBACK": "1",
            "ZAGROSI_TEST_FORCE_ROLLBACK_PATH": str(fixture.evidence_path),
        }
    )
    crashed = run_script_raw(
        script,
        *detached_record_arguments(
            fixture,
            "--evidence-row",
            "record_gate=evidence/record-gate.json",
        ),
        env=environment,
    )
    assert crashed.returncode == -signal.SIGKILL

    recovered = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    assert json.loads(recovered.stdout)["next_section"] == fixture.section
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == base_state_raw
    assert not (
        fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    ).exists()
    assert list((fixture.implementation_root / "pinners").glob(f"{fixture.section}-*.json")) == []


@pytest.mark.parametrize("root_state", ("base", "candidate"))
def test_no_journal_state_temp_is_retained_as_unreachable_for_base_and_candidate(
    tmp_path: Path,
    root_state: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / f"no-journal-state-{root_state}")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-no-journal-state-{root_state}",
        plugin_root=plugin_root,
    )
    base_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    if root_state == "candidate":
        environment = dict(os.environ)
        environment["ZAGROSI_TEST_RECORD_CRASHPOINT"] = "state-cas-fsync"
        crashed = run_script_raw(script, *detached_record_arguments(fixture), env=environment)
        assert crashed.returncode == -signal.SIGKILL
        transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
        final_path = fixture.implementation_root / transaction["pinner_path"]
        (transaction_dir / "transaction.json").unlink()
        (transaction_dir / "pinner.json").unlink()
        final_path.unlink()
    else:
        transaction_dir.mkdir(mode=0o700)
    state_temp = transaction_dir / "state.json"
    state_temp.write_bytes(base_state_raw)
    state_temp.chmod(0o600)
    before = planning_tree_snapshot(fixture.implementation_root)

    result = run_script_raw(
        script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == "section-record-recovery-required"
    assert planning_tree_snapshot(fixture.implementation_root) == before


@pytest.mark.parametrize(
    "crashpoint",
    (
        "setup-slot-temp-fsync:zagrosi_implement_config.json",
        "canonical-temp-fsync:zagrosi_implement_config.json",
    ),
)
def test_detached_setup_real_sigkill_recovers_fixed_root_temps(
    tmp_path: Path,
    crashpoint: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / crashpoint.split(":", 1)[0])
    script = instrument_root_lifecycle_points(plugin_root)
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    arguments = (
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(plugin_root),
        "--flight",
        "off",
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_ROOT_CRASHPOINT"] = crashpoint
    crashed = run_script_raw(script, *arguments, env=environment)
    assert crashed.returncode == -signal.SIGKILL
    assert any(path.name.endswith(".tmp") for path in implementation_root.iterdir())

    recovered = run_script_raw(script, *arguments)
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    assert {path.name for path in implementation_root.iterdir()} == {
        "code_review",
        "evidence",
        "pinners",
        "zagrosi_implement_config.json",
        "zagrosi_implement_state.json",
        "forge-progress.json",
    }
    assert not any(path.name.endswith(".tmp") for path in implementation_root.iterdir())


def test_detached_progress_real_sigkill_recovers_without_stale_lock_or_duplicate_event(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "progress-temp")
    script = instrument_root_lifecycle_points(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-progress-temp", plugin_root=plugin_root)
    arguments = (
        "implement-progress",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
        "--section",
        fixture.section,
        "--stage",
        "red",
        "--result",
        "expected failure",
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_ROOT_CRASHPOINT"] = "canonical-temp-fsync:forge-progress.json"
    crashed = run_script_raw(script, *arguments, env=environment)
    assert crashed.returncode == -signal.SIGKILL
    assert (fixture.implementation_root / ".forge-progress.json.tmp").is_file()
    assert not (fixture.implementation_root / ".forge-progress.json.lock").exists()

    recovered = run_script_raw(script, *arguments)
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    payload = json.loads(recovered.stdout)
    assert payload["event_count"] == 1
    assert not (fixture.implementation_root / ".forge-progress.json.tmp").exists()
    assert not (fixture.implementation_root / ".forge-progress.json.lock").exists()


def test_global_anchor_serializes_concurrent_setup_processes(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "setup-global-contention")
    script = instrument_root_lifecycle_points(plugin_root)
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    arguments = [
        sys.executable,
        str(script),
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(plugin_root),
        "--flight",
        "off",
    ]
    ready = tmp_path / "setup-global-ready"
    release = tmp_path / "setup-global-release"
    environment = dict(os.environ)
    environment.update(
        {
            "ZAGROSI_TEST_ROOT_PAUSEPOINT": "setup-global-acquired",
            "ZAGROSI_TEST_ROOT_READY": str(ready),
            "ZAGROSI_TEST_ROOT_RELEASE": str(release),
        }
    )
    first = subprocess.Popen(
        arguments,
        cwd=plugin_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5.0
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    second = subprocess.Popen(
        arguments,
        cwd=plugin_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.2)
    assert second.poll() is None
    release.write_text("release\n")
    first_stdout, first_stderr = first.communicate(timeout=10)
    second_stdout, second_stderr = second.communicate(timeout=10)
    assert first.returncode == 0, first_stderr + first_stdout
    assert second.returncode == 0, second_stderr + second_stdout
    assert json.loads(first_stdout)["planning_tree_sha256"] == json.loads(second_stdout)["planning_tree_sha256"]


def test_u_root_flock_hides_candidate_window_from_concurrent_reader(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "concurrent-candidate")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-concurrent", plugin_root=plugin_root)
    ready = tmp_path / "candidate-ready"
    release = tmp_path / "candidate-release"
    environment = dict(os.environ)
    environment.update(
        {
            "ZAGROSI_TEST_RECORD_PAUSEPOINT": "state-cas-fsync",
            "ZAGROSI_TEST_RECORD_READY": str(ready),
            "ZAGROSI_TEST_RECORD_RELEASE": str(release),
        }
    )
    writer = subprocess.Popen(
        [sys.executable, str(script), *detached_record_arguments(fixture)],
        cwd=plugin_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5.0
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    reader = subprocess.Popen(
        [
            sys.executable,
            str(script),
            "next-section",
            "--planning-dir",
            str(fixture.planning),
            "--implementation-root",
            str(fixture.implementation_root),
        ],
        cwd=plugin_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.2)
    assert reader.poll() is None
    release.write_text("release\n")
    writer_stdout, writer_stderr = writer.communicate(timeout=10)
    reader_stdout, reader_stderr = reader.communicate(timeout=10)
    assert writer.returncode == 0, writer_stderr + writer_stdout
    assert reader.returncode == 0, reader_stderr + reader_stdout
    assert json.loads(reader_stdout)["completed_sections"] == [fixture.section]


def test_late_commit_reopens_staged_and_final_pinner_before_journal_removal(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "late-pinner-reopen")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-late-pinner-reopen", plugin_root=plugin_root)
    ready = tmp_path / "late-pinner-ready"
    release = tmp_path / "late-pinner-release"
    environment = dict(os.environ)
    environment.update(
        {
            "ZAGROSI_TEST_RECORD_PAUSEPOINT": "post-state-validation",
            "ZAGROSI_TEST_RECORD_READY": str(ready),
            "ZAGROSI_TEST_RECORD_RELEASE": str(release),
        }
    )
    writer = subprocess.Popen(
        [sys.executable, str(script), *detached_record_arguments(fixture)],
        cwd=plugin_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5.0
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
    final_path = fixture.implementation_root / transaction["pinner_path"]
    assert final_path.stat().st_ino == (transaction_dir / "pinner.json").stat().st_ino
    final_path.write_bytes(b'{"schema":"mutated-after-validation"}\n')
    release.write_text("release\n")

    writer_stdout, writer_stderr = writer.communicate(timeout=10)
    assert writer.returncode == 1, writer_stderr + writer_stdout
    assert json.loads(writer_stdout)["error_code"] == "section-record-recovery-required"
    assert (transaction_dir / "transaction.json").is_file()
    state = assert_canonical_json_file(fixture.implementation_root / "zagrosi_implement_state.json")
    assert fixture.section in state["completed_sections"]


def test_global_anchor_blocks_replacement_u_contender_until_original_holder_closes(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "global-u-replacement")
    instrument_record_crashpoints(plugin_root)
    script = instrument_root_lifecycle_points(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-global-u-replacement", plugin_root=plugin_root)
    ready = tmp_path / "replacement-ready"
    release = tmp_path / "replacement-release"
    environment = dict(os.environ)
    environment.update(
        {
            "ZAGROSI_TEST_RECORD_PAUSEPOINT": "state-cas-fsync",
            "ZAGROSI_TEST_RECORD_READY": str(ready),
            "ZAGROSI_TEST_RECORD_RELEASE": str(release),
        }
    )
    writer = subprocess.Popen(
        [sys.executable, str(script), *detached_record_arguments(fixture)],
        cwd=plugin_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5.0
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()

    displaced = tmp_path / "displaced-implementation-root"
    replacement = fixture.implementation_root
    replacement.rename(displaced)
    replacement.mkdir(mode=0o700)
    reader = subprocess.Popen(
        [
            sys.executable,
            str(script),
            "next-section",
            "--planning-dir",
            str(fixture.planning),
            "--implementation-root",
            str(replacement),
        ],
        cwd=plugin_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.2)
    assert reader.poll() is None
    replacement.rmdir()
    displaced.rename(replacement)
    release.write_text("release\n")

    writer_stdout, writer_stderr = writer.communicate(timeout=10)
    reader_stdout, reader_stderr = reader.communicate(timeout=10)
    assert writer.returncode == 0, writer_stderr + writer_stdout
    assert reader.returncode == 0, reader_stderr + reader_stdout
    assert json.loads(reader_stdout)["completed_sections"] == [fixture.section]


def test_global_and_u_lock_fds_are_noninheritable_closed_and_reverse_released(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_zagrosi_module()
    root = tmp_path / "detached-root"
    (root / "pinners").mkdir(parents=True, mode=0o700)
    marker = root / "pinners" / ".record-section.lock"
    marker.write_bytes(b"")
    marker.chmod(0o600)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    real_flock = module.fcntl.flock
    operations: list[tuple[int, int]] = []

    def tracked_flock(file_fd: int, operation: int) -> None:
        operations.append((file_fd, operation))
        real_flock(file_fd, operation)

    monkeypatch.setattr(module.fcntl, "flock", tracked_flock)
    locked_fds: list[int] = []
    try:
        with ExitStack() as stack:
            stack.enter_context(module.detached_global_lock(time.monotonic() + 2.0))
            stack.enter_context(module.section_record_lock(root_fd, root))
            locked_fds = [file_fd for file_fd, operation in operations if operation & module.fcntl.LOCK_EX]
            assert len(locked_fds) == 2
            assert all(os.get_inheritable(file_fd) is False for file_fd in locked_fds)
        unlock_fds = [file_fd for file_fd, operation in operations if operation == module.fcntl.LOCK_UN]
        assert unlock_fds == list(reversed(locked_fds))
        for file_fd in locked_fds:
            with pytest.raises(OSError):
                os.fstat(file_fd)
    finally:
        os.close(root_fd)


def test_global_anchor_unsupported_flock_fails_closed_and_closes_fd(monkeypatch) -> None:
    module = load_zagrosi_module()
    real_open = module.os.open
    opened: list[int] = []

    def tracked_open(path, flags, *args, **kwargs):
        file_fd = real_open(path, flags, *args, **kwargs)
        if os.fspath(path) == os.sep:
            opened.append(file_fd)
        return file_fd

    def unsupported_flock(file_fd: int, operation: int) -> None:
        raise OSError(errno.EOPNOTSUPP, "unsupported test flock")

    monkeypatch.setattr(module.os, "open", tracked_open)
    monkeypatch.setattr(module.fcntl, "flock", unsupported_flock)
    with pytest.raises(module.DetachedImplementationError) as caught:
        with module.detached_global_lock(time.monotonic() + 1.0):
            raise AssertionError("unreachable")
    assert caught.value.code == "detached-global-lock-unsupported"
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_pinners_directory_replacement_during_candidate_rolls_back_and_retains_transaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "pinners-replacement")
    fixture = make_detached_record_fixture(tmp_path / "fixture-pinners-replacement", plugin_root=plugin_root)
    module = load_zagrosi_module(fixture.script)
    original_replace_state = module.replace_state_from_transaction
    initial_state = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    displaced_pinners = tmp_path / "displaced-pinners"
    replacement_pinners = fixture.implementation_root / "pinners"
    replaced = False

    def replace_state_then_swap_pinners(root_fd, transaction_fd, expected_raw, replacement):
        nonlocal replaced
        replacement_raw = original_replace_state(root_fd, transaction_fd, expected_raw, replacement)
        if not replaced and replacement.get("completed_sections", {}).get(fixture.section) is not None:
            replaced = True
            replacement_pinners.rename(displaced_pinners)
            replacement_pinners.mkdir(mode=0o700)
            marker = replacement_pinners / ".record-section.lock"
            marker.write_bytes(b"")
            marker.chmod(0o600)
        return replacement_raw

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module, "replace_state_from_transaction", replace_state_then_swap_pinners)
    monkeypatch.setattr(
        module,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(detached_record_arguments(fixture))
    assert parsed.func(parsed) == 1
    assert replaced is True
    assert captured[-1][0]["error_code"] == "section-record-recovery-required"
    retained_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    assert retained_state_raw != initial_state
    assert fixture.section in json.loads(retained_state_raw)["completed_sections"]
    assert (displaced_pinners / ".record-section-transaction-v1" / "transaction.json").is_file()

    shutil.rmtree(replacement_pinners)
    displaced_pinners.rename(replacement_pinners)


def test_detached_setup_requires_exact_final_pinner_semantics_and_trusted_hash(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    valid = admission_pinner_payload(planning)

    def copied() -> dict:
        return json.loads(json.dumps(valid))

    fake_schema = copied()
    fake_schema["schema"] = "attacker-pinner-v1"
    fake_fields = copied()
    fake_fields["authority"] = "PASS"
    fake_pass = copied()
    fake_pass["verdict"] = "FAIL"
    mismatched_end = copied()
    mismatched_end["end"]["r_sha256"] = "sha256:" + "44" * 32
    wrong_a = copied()
    wrong_a["start"]["a_sha256"] = wrong_a["end"]["a_sha256"] = "sha256:" + "55" * 32
    wrong_d = copied()
    wrong_d_value = "sha256:" + "66" * 32
    for endpoint in (wrong_d["start"], wrong_d["end"]):
        endpoint["d_sha256"] = wrong_d_value
        digest = hashlib.sha256(b"dec075-a-v1\0")
        for field in ("r_sha256", "p_sha256", "d_sha256"):
            digest.update(bytes.fromhex(endpoint[field].removeprefix("sha256:")))
        endpoint["a_sha256"] = "sha256:" + digest.hexdigest()
    wrong_o = copied()
    wrong_o["o_sha256"] = "not-a-digest"

    cases = {
        "fake-schema": (fake_schema, None, "invalid-admission-pinner"),
        "fake-fields": (fake_fields, None, "invalid-admission-pinner"),
        "fake-pass": (fake_pass, None, "invalid-admission-pinner"),
        "mismatched-end": (mismatched_end, None, "invalid-admission-pinner"),
        "wrong-a": (wrong_a, None, "invalid-admission-pinner"),
        "wrong-d": (wrong_d, None, "invalid-admission-pinner"),
        "wrong-o": (wrong_o, None, "invalid-admission-pinner"),
        "wrong-trust-anchor": (copied(), "sha256:" + "0" * 64, "admission-pinner-drift"),
    }
    expected_planning = planning_tree_snapshot(planning)
    for label, (payload, expected_hash, error_code) in cases.items():
        pinner = write_test_admission_pinner(
            tmp_path / f"{label}-pinner.json",
            planning_dir=planning,
            payload=payload,
        )
        implementation_root = tmp_path / f"{label}-implementation"
        result = run_raw(
            "implement-setup",
            "--sections-dir",
            str(sections),
            "--target-dir",
            str(target),
            "--implementation-root",
            str(implementation_root),
            "--admission-pinner",
            str(pinner),
            "--expected-admission-pinner-sha256",
            expected_hash or file_sha256(pinner),
            *implementation_source_args(),
            "--flight",
            "off",
        )
        assert result.returncode != 0, label
        assert json.loads(result.stdout)["error_code"] == error_code
        assert not implementation_root.exists(), label
        assert planning_tree_snapshot(planning) == expected_planning


@pytest.mark.parametrize(
    "crash_point",
    (
        "slot_write:zagrosi_implement_config.json",
        "slot_write:zagrosi_implement_state.json",
        "slot_write:forge-progress.json",
        "slot_fsync:zagrosi_implement_config.json",
        "slot_fsync:zagrosi_implement_state.json",
        "slot_fsync:forge-progress.json",
        "final:zagrosi_implement_config.json",
        "final:zagrosi_implement_state.json",
        "final:forge-progress.json",
    ),
)
def test_detached_setup_recovers_only_its_exact_authenticated_pending_prefix(
    tmp_path: Path,
    monkeypatch,
    crash_point: str,
) -> None:
    phase, crash_after_relative = crash_point.split(":", 1)
    plugin_root = copy_implementation_plugin(tmp_path / f"{phase}-{Path(crash_after_relative).stem}")
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    module = load_zagrosi_module(plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"])
    parsed = module.build_parser().parse_args(
        [
            "implement-setup",
            "--sections-dir",
            str(sections),
            "--target-dir",
            str(target),
            "--implementation-root",
            str(implementation_root),
            "--admission-pinner",
            str(admission_pinner),
            "--expected-admission-pinner-sha256",
            file_sha256(admission_pinner),
            *implementation_source_args(plugin_root),
            "--flight",
            "off",
        ]
    )
    original_write = module.write_canonical_json_at
    original_slot = module.ensure_detached_root_file_slot
    original_write_all = module._write_all
    crashed = False

    def crash_after_exact_write(root_fd, relative, payload, **kwargs):
        nonlocal crashed
        result = original_write(root_fd, relative, payload, **kwargs)
        if (
            phase == "final"
            and relative == crash_after_relative
            and payload.get("schema") != module.DETACHED_SETUP_PREFIX_SCHEMA
        ):
            crashed = True
            raise OSError("injected closed setup crash")
        return result

    def crash_after_exact_slot(root_fd, relative, payload):
        nonlocal crashed
        result = original_slot(root_fd, relative, payload)
        if phase == "slot_fsync" and relative == crash_after_relative:
            crashed = True
            raise OSError("injected closed setup slot crash")
        return result

    def crash_after_slot_write(file_fd, raw):
        nonlocal crashed
        original_write_all(file_fd, raw)
        if phase != "slot_write" or crashed:
            return
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return
        expected_slot = {
            "zagrosi_implement_config.json": "config",
            "zagrosi_implement_state.json": "state",
            "forge-progress.json": "progress",
        }[crash_after_relative]
        if payload.get("schema") == module.DETACHED_SETUP_PREFIX_SCHEMA and payload.get("slot") == expected_slot:
            crashed = True
            raise OSError("injected closed setup write-before-fsync crash")

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module, "write_canonical_json_at", crash_after_exact_write)
    monkeypatch.setattr(module, "ensure_detached_root_file_slot", crash_after_exact_slot)
    monkeypatch.setattr(module, "_write_all", crash_after_slot_write)
    monkeypatch.setattr(
        module,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    assert parsed.func(parsed) == 1
    assert crashed is True
    assert captured[-1][0]["error_code"] == "detached-io-failure"

    monkeypatch.setattr(module, "write_canonical_json_at", original_write)
    monkeypatch.setattr(module, "ensure_detached_root_file_slot", original_slot)
    monkeypatch.setattr(module, "_write_all", original_write_all)
    assert parsed.func(parsed) == 0
    assert captured[-1][0]["success"] is True
    assert_canonical_json_file(implementation_root / "zagrosi_implement_config.json")
    assert_canonical_json_file(implementation_root / "zagrosi_implement_state.json")
    assert_canonical_json_file(implementation_root / "forge-progress.json")
    assert {path.name for path in implementation_root.iterdir()} == {
        "zagrosi_implement_config.json",
        "zagrosi_implement_state.json",
        "forge-progress.json",
        "code_review",
        "evidence",
        "pinners",
    }


def test_detached_setup_replay_is_exact_and_rejects_changed_authorities_without_mutation(tmp_path: Path) -> None:
    fixture = make_detached_record_fixture(tmp_path)
    before = planning_tree_snapshot(fixture.implementation_root)

    def setup_again(target: Path, source_args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return run_script_raw(
            fixture.script,
            "implement-setup",
            "--sections-dir",
            str(fixture.sections),
            "--target-dir",
            str(target),
            "--implementation-root",
            str(fixture.implementation_root),
            "--admission-pinner",
            str(fixture.admission_pinner),
            "--expected-admission-pinner-sha256",
            file_sha256(fixture.admission_pinner),
            *source_args,
            "--flight",
            "off",
        )

    replay = setup_again(fixture.target, implementation_source_args())
    assert replay.returncode == 0, replay.stderr + replay.stdout
    assert planning_tree_snapshot(fixture.implementation_root) == before

    different_target = tmp_path / "different-target"
    different_target.mkdir()
    changed_target = setup_again(different_target, implementation_source_args())
    assert changed_target.returncode != 0
    assert json.loads(changed_target.stdout)["error_code"] == "detached-config-conflict"
    assert planning_tree_snapshot(fixture.implementation_root) == before

    changed_source = setup_again(
        fixture.target,
        implementation_source_args(tool="sha256:" + "00" * 32),
    )
    assert changed_source.returncode != 0
    assert json.loads(changed_source.stdout)["error_code"] == "implement-source-drift"
    assert planning_tree_snapshot(fixture.implementation_root) == before


def test_detached_setup_rejects_arbitrary_prefix_and_unknown_top_level_before_writes(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )

    def setup(root: Path) -> subprocess.CompletedProcess[str]:
        return run_raw(
            "implement-setup",
            "--sections-dir",
            str(sections),
            "--target-dir",
            str(target),
            "--implementation-root",
            str(root),
            "--admission-pinner",
            str(admission_pinner),
            "--expected-admission-pinner-sha256",
            file_sha256(admission_pinner),
            *implementation_source_args(),
            "--flight",
            "off",
        )

    planted = tmp_path / "planted-prefix"
    planted.mkdir(mode=0o700)
    (planted / "zagrosi_implement_config.json").write_bytes(b"{}\n")
    (planted / "zagrosi_implement_config.json").chmod(0o600)
    (planted / ".forge-progress.json.tmp").write_bytes(b'{"attacker":true}\n')
    (planted / ".forge-progress.json.tmp").chmod(0o600)
    planted_before = planning_tree_snapshot(planted)
    refused_prefix = setup(planted)
    assert refused_prefix.returncode != 0
    assert json.loads(refused_prefix.stdout)["error_code"] == "detached-setup-prefix-conflict"
    assert planning_tree_snapshot(planted) == planted_before

    unknown = tmp_path / "unknown-sibling"
    unknown.mkdir(mode=0o700)
    (unknown / "caller-data.txt").write_text("must remain untouched\n")
    unknown_before = planning_tree_snapshot(unknown)
    refused_unknown = setup(unknown)
    assert refused_unknown.returncode != 0
    assert json.loads(refused_unknown.stdout)["error_code"] == "unsafe-detached-root-inventory"
    assert planning_tree_snapshot(unknown) == unknown_before
    assert not (unknown / "zagrosi_implement_state.json").exists()
    assert not (unknown / "evidence").exists()
    assert not (unknown / "pinners").exists()


@pytest.mark.parametrize("directory", ("code_review", "evidence"))
def test_detached_setup_rejects_preplanted_nested_artifacts_without_mutation(
    tmp_path: Path,
    directory: str,
) -> None:
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    implementation_root = tmp_path / "detached-implementation"
    planted_dir = implementation_root / directory
    planted_dir.mkdir(parents=True, mode=0o700)
    implementation_root.chmod(0o700)
    (planted_dir / "caller-artifact.txt").write_text("retain exactly\n")
    before = planning_tree_snapshot(implementation_root)

    result = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == "detached-setup-prefix-conflict"
    assert planning_tree_snapshot(implementation_root) == before
    assert not (implementation_root / "pinners").exists()


@pytest.mark.parametrize("invalid_shape", ("missing-marker", "config-plus-progress"))
def test_detached_setup_rejects_impossible_authenticated_prefix_shapes_without_mutation(
    tmp_path: Path,
    monkeypatch,
    invalid_shape: str,
) -> None:
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    module = load_zagrosi_module()
    parsed = module.build_parser().parse_args(
        [
            "implement-setup",
            "--sections-dir",
            str(sections),
            "--target-dir",
            str(target),
            "--implementation-root",
            str(implementation_root),
            "--admission-pinner",
            str(admission_pinner),
            "--expected-admission-pinner-sha256",
            file_sha256(admission_pinner),
            *implementation_source_args(),
            "--flight",
            "off",
        ]
    )
    original_slot = module.ensure_detached_root_file_slot

    def leave_invalid_prefix(root_fd, relative, payload):
        if invalid_shape == "config-plus-progress" and relative == "zagrosi_implement_state.json":
            return False, payload, module.canonical_json_bytes(payload)
        result = original_slot(root_fd, relative, payload)
        if (
            invalid_shape == "missing-marker"
            and relative == "zagrosi_implement_config.json"
        ) or (
            invalid_shape == "config-plus-progress"
            and relative == "forge-progress.json"
        ):
            raise OSError("injected setup prefix stop")
        return result

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module, "ensure_detached_root_file_slot", leave_invalid_prefix)
    monkeypatch.setattr(
        module,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    assert parsed.func(parsed) == 1
    monkeypatch.setattr(module, "ensure_detached_root_file_slot", original_slot)
    if invalid_shape == "missing-marker":
        (implementation_root / "pinners" / ".record-section.lock").unlink()
    before = planning_tree_snapshot(implementation_root)

    assert parsed.func(parsed) == 1
    assert captured[-1][0]["error_code"] in {
        "unsafe-section-record-lock",
        "detached-setup-prefix-conflict",
    }
    assert planning_tree_snapshot(implementation_root) == before


@pytest.mark.parametrize("relationship", ("root_inside_target", "target_inside_root"))
def test_detached_setup_rejects_target_root_ancestry_before_any_create(
    tmp_path: Path,
    relationship: str,
) -> None:
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    if relationship == "root_inside_target":
        target = tmp_path / "target"
        target.mkdir()
        implementation_root = target / "detached-state"
    else:
        implementation_root = tmp_path / "detached-state"
        implementation_root.mkdir(mode=0o700)
        target = implementation_root / "worktree"
        target.mkdir()
    before = planning_tree_snapshot(target if relationship == "root_inside_target" else implementation_root)

    result = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )

    assert result.returncode != 0
    assert json.loads(result.stdout)["error_code"] == "detached-root-target-overlap"
    assert planning_tree_snapshot(target if relationship == "root_inside_target" else implementation_root) == before
    if relationship == "root_inside_target":
        assert not implementation_root.exists()


@pytest.mark.parametrize(
    "relationship",
    ("same_root", "target_inside_planning", "planning_inside_target"),
)
def test_detached_setup_rejects_planning_target_ancestry_before_u_creation(
    tmp_path: Path,
    relationship: str,
) -> None:
    if relationship in {"same_root", "target_inside_planning"}:
        planning = tmp_path / "planning"
        sections = write_single_section_fixture(planning)
        if relationship == "same_root":
            target = planning
        else:
            target = planning / "target"
            target.mkdir()
        observed_root = planning
    else:
        target = tmp_path / "target"
        target.mkdir()
        planning = target / "planning"
        sections = write_single_section_fixture(planning)
        observed_root = target
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    before = planning_tree_snapshot(observed_root)

    result = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == "planning-target-overlap"
    assert not implementation_root.exists()
    assert planning_tree_snapshot(observed_root) == before


def test_detached_record_derives_and_reverifies_exact_privileged_evidence_name_and_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contracts = {
        "section-26-publication-wire-and-decision-store": (
            "s26_privileged_darwin_apfs_gate",
            "evidence/s26-privileged-darwin-apfs-gate-handoff-receipt-v1.json",
        ),
        "section-28-scoped-native-and-external-composition": (
            "s28_privileged_darwin_apfs_gate",
            "evidence/s28-privileged-darwin-apfs-gate-handoff-receipt-v1.json",
        ),
    }
    for section, (name, relative) in contracts.items():
        fixture = make_detached_record_fixture(tmp_path / section, section=section)
        required = fixture.implementation_root / relative
        required.parent.mkdir(parents=True, exist_ok=True)
        required.write_bytes(
            b'{"schema":"unit12-privileged-darwin-apfs-gate-handoff-receipt-v1"}\n'
        )
        required.chmod(0o600)
        alternate = fixture.implementation_root / "evidence" / "alternate-result.json"
        alternate.write_bytes(required.read_bytes())
        alternate.chmod(0o600)

        module = load_zagrosi_module(fixture.script)
        verified: list[str] = []

        def verify_stored(*args):
            verified.append(args[-1])
            raw = required.read_bytes()
            return json.loads(raw), raw

        captured: list[tuple[dict, int]] = []
        monkeypatch.setattr(module, "verify_stored_privileged_handoff", verify_stored)
        monkeypatch.setattr(
            module,
            "print_json",
            lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
        )
        args = module.build_parser().parse_args(detached_record_arguments(fixture))
        assert args.func(args) == 0
        assert verified == [section, section, section, section, section, section]
        pinner = assert_canonical_json_file(Path(captured[-1][0]["pinner_path"]))
        assert pinner["evidence_rows"] == [
            {
                "name": name,
                "path": relative,
                "sha256": file_sha256(required),
                "size": required.stat().st_size,
            }
        ]

        wrong_name = run_script_raw(
            fixture.script,
            *detached_record_arguments(fixture, "--evidence-row", f"wrong_name={relative}"),
        )
        assert wrong_name.returncode != 0
        assert json.loads(wrong_name.stdout)["error_code"] == "reserved-evidence-row"

        wrong_path = run_script_raw(
            fixture.script,
            *detached_record_arguments(fixture, "--evidence-row", f"{name}=evidence/alternate-result.json"),
        )
        assert wrong_path.returncode != 0
        assert json.loads(wrong_path.stdout)["error_code"] == "reserved-evidence-row"

        caller_exact = run_script_raw(
            fixture.script,
            *detached_record_arguments(fixture, "--evidence-row", f"{name}={relative}"),
        )
        assert caller_exact.returncode != 0
        assert json.loads(caller_exact.stdout)["error_code"] == "reserved-evidence-row"


def test_detached_record_rejects_copied_raw_privileged_result(tmp_path: Path) -> None:
    fixture = make_detached_record_fixture(tmp_path)
    raw_result = fixture.implementation_root / "evidence" / "copied-root-result.json"
    raw_result.write_bytes(
        b'{"schema":"unit12-privileged-darwin-apfs-gate-result-v1","verdict":"PASS"}\n'
    )
    raw_result.chmod(0o600)

    rejected = run_script_raw(
        fixture.script,
        *detached_record_arguments(
            fixture,
            "--evidence-row",
            "copied_root_result=evidence/copied-root-result.json",
        ),
    )

    assert rejected.returncode != 0
    assert json.loads(rejected.stdout)["error_code"] == "raw-privileged-evidence-forbidden"
    assert_no_detached_section_record(fixture)


@pytest.mark.parametrize(
    "selector_args",
    (
        (),
        ("--section", "section-26-publication-wire-and-decision-store"),
        ("--section", "26"),
        ("--section", "s26"),
        ("--section", "S26", "S28"),
        ("--section", "S26", "--section", "S28"),
    ),
)
def test_implement_evidence_handoff_selector_errors_are_silent_exit_two(
    tmp_path: Path,
    selector_args: tuple[str, ...],
) -> None:
    result = run_raw(
        "implement-evidence-handoff",
        "--implementation-root",
        str(tmp_path / "private-detached-root"),
        *selector_args,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("section_token", "section"),
    (
        ("S26", "section-26-publication-wire-and-decision-store"),
        ("S28", "section-28-scoped-native-and-external-composition"),
    ),
)
def test_implement_evidence_handoff_uses_exact_transport_and_create_once_replay(
    tmp_path: Path,
    monkeypatch,
    section_token: str,
    section: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / section)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-{section}",
        section=section,
        plugin_root=plugin_root,
    )
    module = load_zagrosi_module(fixture.script)
    contract = module.HANDOFF_SECTION_CONTRACTS[section_token]
    source_observation = protected_source_observation_for_test(module)
    test_path = fixture.target / contract["test"]
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text("# fixed privileged handoff verifier fixture\n")
    config = assert_canonical_json_file(fixture.implementation_root / "zagrosi_implement_config.json")
    calls: list[tuple[list[str], bytes, float, int, int]] = []

    def fake_bounded_child(
        argv: list[str],
        input_bytes: bytes,
        *,
        cwd_fd: int,
        timeout_seconds: float,
        stdout_cap: int,
        stderr_cap: int,
    ) -> tuple[int, bytes, bytes]:
        calls.append((argv, input_bytes, timeout_seconds, stdout_cap, stderr_cap))
        assert (os.fstat(cwd_fd).st_dev, os.fstat(cwd_fd).st_ino) == (
            fixture.target.stat().st_dev,
            fixture.target.stat().st_ino,
        )
        if argv == module.handoff_root_argv(contract):
            request = json.loads(input_bytes)
            assert set(request) == module.HANDOFF_REQUEST_FIELDS
            assert input_bytes == canonical_json_bytes_for_test(request)
            assert len(input_bytes) <= 16 * 1024
            request_without_self = {key: value for key, value in request.items() if key != "self_digest"}
            assert request["self_digest"] == domain_sha256_for_test(
                b"unit12-privileged-darwin-apfs-gate-handoff-request-v1-self\0",
                canonical_json_bytes_for_test(request_without_self)[:-1],
            )
            request_final_wire_digest = domain_sha256_for_test(
                b"unit12-privileged-darwin-apfs-gate-handoff-request-v1-final-wire\0",
                input_bytes[:-1],
            )
            assert request_final_wire_digest != "sha256:" + hashlib.sha256(input_bytes).hexdigest()
            receipt_raw = handoff_receipt_for_test(
                module,
                config,
                contract,
                input_bytes,
                source_observation,
            )
            receipt = json.loads(receipt_raw)
            assert receipt["handoff_request_final_wire_digest"] == request_final_wire_digest
            receipt_without_self_and_signature = {
                key: value for key, value in receipt.items() if key not in {"self_digest", "signature_b64u"}
            }
            assert receipt["self_digest"] == domain_sha256_for_test(
                b"unit12-privileged-darwin-apfs-gate-handoff-receipt-v1-self\0",
                canonical_json_bytes_for_test(receipt_without_self_and_signature)[:-1],
            )
            receipt_final_wire_digest = domain_sha256_for_test(
                b"unit12-privileged-darwin-apfs-gate-handoff-receipt-v1-final-wire\0",
                receipt_raw[:-1],
            )
            assert receipt_final_wire_digest != "sha256:" + hashlib.sha256(receipt_raw).hexdigest()
            return 0, receipt_raw, b""
        assert argv == module.handoff_verifier_argv(contract)
        request_size = int.from_bytes(input_bytes[:4], "big")
        request_raw = input_bytes[4 : 4 + request_size]
        receipt_offset = 4 + request_size
        receipt_size = int.from_bytes(input_bytes[receipt_offset : receipt_offset + 4], "big")
        receipt_raw = input_bytes[receipt_offset + 4 : receipt_offset + 4 + receipt_size]
        assert receipt_offset + 4 + receipt_size == len(input_bytes)
        return 0, handoff_verification_for_test(module, config, contract, request_raw, receipt_raw), b""

    emitted: list[tuple[dict, int]] = []
    monkeypatch.setattr(module, "require_handoff_platform", lambda root_fd: None)
    monkeypatch.setattr(module, "require_fixed_handoff_dependencies", lambda *args, **kwargs: None)

    monkeypatch.setattr(
        module,
        "derive_protected_source_observation",
        lambda target_fd, selected_contract: source_observation,
    )
    monkeypatch.setattr(module, "run_bounded_child", fake_bounded_child)
    monkeypatch.setattr(
        module,
        "emit_canonical_json",
        lambda payload, exit_code=0: emitted.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(
        [
            "implement-evidence-handoff",
            "--implementation-root",
            str(fixture.implementation_root),
            "--section",
            section_token,
        ]
    )

    assert parsed.func(parsed) == 0
    assert emitted[-1][1] == 0
    assert emitted[-1][0] == {
        "schema": "zagrosi-privileged-evidence-handoff-result-v1",
        "section": section_token,
        "evidence_name": contract["evidence_name"],
        "evidence_path": contract["evidence_path"],
        "sha256": file_sha256(fixture.implementation_root / contract["evidence_path"]),
        "size": (fixture.implementation_root / contract["evidence_path"]).stat().st_size,
        "status": "created",
    }
    assert parsed.func(parsed) == 0
    assert emitted[-1][0]["status"] == "reopened"
    assert [call[0] for call in calls] == [
        module.handoff_root_argv(contract),
        module.handoff_verifier_argv(contract),
        module.handoff_root_argv(contract),
        module.handoff_verifier_argv(contract),
    ]
    assert calls[0][2:] == (30.0, 64 * 1024, 64 * 1024)
    assert calls[1][2:] == (10.0, 4 * 1024, 64 * 1024)
    module.verify_handoff_command_identities(contract)
    assert_canonical_json_file(fixture.implementation_root / contract["evidence_path"])


@pytest.mark.parametrize(
    ("failure", "expected_exit", "closed_error_code"),
    (
        ("root_stderr", 5, "HANDOFF_ROOT_OUTPUT_INVALID"),
        ("root_partial", 5, "HANDOFF_ROOT_OUTPUT_INVALID"),
        ("root_extra_stdout", 5, "HANDOFF_ROOT_OUTPUT_INVALID"),
        ("root_timeout", 3, "HANDOFF_ROOT_UNAVAILABLE"),
        ("verifier_stderr", 5, "HANDOFF_VERIFIER_OUTPUT_INVALID"),
        ("verifier_partial", 5, "HANDOFF_VERIFIER_OUTPUT_INVALID"),
    ),
)
def test_implement_evidence_handoff_transport_failures_leave_no_user_receipt(
    tmp_path: Path,
    monkeypatch,
    failure: str,
    expected_exit: int,
    closed_error_code: str,
) -> None:
    section_token = "S26"
    section = "section-26-publication-wire-and-decision-store"
    plugin_root = copy_implementation_plugin(tmp_path / failure)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-{failure}",
        section=section,
        plugin_root=plugin_root,
    )
    module = load_zagrosi_module(fixture.script)
    contract = module.HANDOFF_SECTION_CONTRACTS[section_token]
    source_observation = protected_source_observation_for_test(module)
    test_path = fixture.target / contract["test"]
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text("# fixed privileged handoff verifier fixture\n")
    config = assert_canonical_json_file(fixture.implementation_root / "zagrosi_implement_config.json")

    def fake_bounded_child(argv: list[str], input_bytes: bytes, **kwargs) -> tuple[int, bytes, bytes]:
        if argv == module.handoff_root_argv(contract):
            receipt = handoff_receipt_for_test(
                module,
                config,
                contract,
                input_bytes,
                source_observation,
            )
            if failure == "root_stderr":
                return 0, receipt, b"closed-error\n"
            if failure == "root_partial":
                return 0, receipt[:-1], b""
            if failure == "root_extra_stdout":
                return 0, receipt + b"{}\n", b""
            if failure == "root_timeout":
                raise module.DetachedImplementationError(
                    "handoff-child-timeout",
                    "Privileged handoff child exceeded its fixed deadline.",
                )
            return 0, receipt, b""
        request_size = int.from_bytes(input_bytes[:4], "big")
        request_raw = input_bytes[4 : 4 + request_size]
        receipt_offset = 4 + request_size
        receipt_size = int.from_bytes(input_bytes[receipt_offset : receipt_offset + 4], "big")
        receipt_raw = input_bytes[receipt_offset + 4 : receipt_offset + 4 + receipt_size]
        verification = handoff_verification_for_test(module, config, contract, request_raw, receipt_raw)
        if failure == "verifier_stderr":
            return 0, verification, b"closed-error\n"
        if failure == "verifier_partial":
            return 0, verification[:-1], b""
        return 0, verification, b""

    emitted: list[tuple[dict, int]] = []
    monkeypatch.setattr(module, "require_handoff_platform", lambda root_fd: None)
    monkeypatch.setattr(module, "require_fixed_handoff_dependencies", lambda *args, **kwargs: None)

    monkeypatch.setattr(
        module,
        "derive_protected_source_observation",
        lambda target_fd, selected_contract: source_observation,
    )
    monkeypatch.setattr(module, "run_bounded_child", fake_bounded_child)
    monkeypatch.setattr(
        module,
        "emit_canonical_json",
        lambda payload, exit_code=0: emitted.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(
        [
            "implement-evidence-handoff",
            "--implementation-root",
            str(fixture.implementation_root),
            "--section",
            section_token,
        ]
    )

    assert parsed.func(parsed) == expected_exit
    assert emitted[-1][1] == expected_exit
    assert emitted[-1][0] == {
        "schema": "zagrosi-privileged-evidence-handoff-error-v1",
        "purpose": "zagrosi_privileged_evidence_handoff_error",
        "section": section_token,
        "status": "failed",
        "closed_error_code": closed_error_code,
    }
    assert not (fixture.implementation_root / contract["evidence_path"]).exists()


@pytest.mark.parametrize(
    ("failure", "expected_exit", "closed_error_code"),
    (
        ("config", 5, "HANDOFF_AUTHORITY_INVALID"),
        ("source", 5, "HANDOFF_AUTHORITY_INVALID"),
        ("caller", 5, "HANDOFF_CALLER_REFUSED"),
        ("platform", 3, "HANDOFF_PLATFORM_UNAVAILABLE"),
        ("fixed_dependency", 3, "HANDOFF_FIXED_DEPENDENCY_UNAVAILABLE"),
        ("git_output_cap", 3, "HANDOFF_FIXED_DEPENDENCY_UNAVAILABLE"),
        ("internal", 5, "HANDOFF_INTERNAL_FAILURE"),
    ),
)
def test_implement_evidence_handoff_public_failures_are_exact_bounded_and_private(
    tmp_path: Path,
    monkeypatch,
    failure: str,
    expected_exit: int,
    closed_error_code: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / failure)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-{failure}",
        section="section-26-publication-wire-and-decision-store",
        plugin_root=plugin_root,
    )
    module = load_zagrosi_module(fixture.script)
    if failure == "config":
        config_path = fixture.implementation_root / "zagrosi_implement_config.json"
        config = json.loads(config_path.read_bytes())
        config["private_path_mutant"] = str(fixture.planning)
        replace_file(config_path, canonical_json_bytes_for_test(config), mode=0o600)
    elif failure == "source":
        replace_file(
            fixture.script,
            fixture.script.read_bytes() + b"\n# private source mutant\n",
            mode=fixture.script.stat().st_mode & 0o777,
        )
    elif failure == "caller":
        monkeypatch.setattr(
            module,
            "require_handoff_platform",
            lambda root_fd: (_ for _ in ()).throw(
                module.DetachedImplementationError("unsafe-handoff-caller", str(fixture.implementation_root))
            ),
        )
    elif failure == "platform":
        monkeypatch.setattr(
            module,
            "require_handoff_platform",
            lambda root_fd: (_ for _ in ()).throw(
                module.DetachedImplementationError("unsupported-handoff-platform", str(fixture.target))
            ),
        )
    elif failure == "fixed_dependency":
        monkeypatch.setattr(module, "require_handoff_platform", lambda root_fd: None)
        monkeypatch.setattr(
            module,
            "require_fixed_handoff_dependencies",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                module.DetachedImplementationError("missing-handoff-dependency", str(fixture.admission_pinner))
            ),
        )
    elif failure == "git_output_cap":
        monkeypatch.setattr(module, "require_handoff_platform", lambda root_fd: None)
        monkeypatch.setattr(module, "require_fixed_handoff_dependencies", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            module,
            "run_bounded_child",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                module.DetachedImplementationError(
                    "handoff-child-output-cap",
                    str(fixture.target / "private-source-cap-detail"),
                )
            ),
        )
    else:
        monkeypatch.setattr(module, "require_handoff_platform", lambda root_fd: None)
        monkeypatch.setattr(
            module,
            "verify_handoff_command_identities",
            lambda contract: (_ for _ in ()).throw(RuntimeError(str(fixture.planning))),
        )
    emitted: list[tuple[dict, int]] = []
    monkeypatch.setattr(
        module,
        "emit_canonical_json",
        lambda payload, exit_code=0: emitted.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(
        [
            "implement-evidence-handoff",
            "--implementation-root",
            str(fixture.implementation_root),
            "--section",
            "S26",
        ]
    )

    assert parsed.func(parsed) == expected_exit
    assert emitted == [
        (
            {
                "schema": "zagrosi-privileged-evidence-handoff-error-v1",
                "purpose": "zagrosi_privileged_evidence_handoff_error",
                "section": "S26",
                "status": "failed",
                "closed_error_code": closed_error_code,
            },
            expected_exit,
        )
    ]
    wire = module.handoff_canonical_json_bytes(emitted[0][0])
    assert len(wire) <= 4096
    assert str(fixture.planning).encode() not in wire
    assert str(fixture.implementation_root).encode() not in wire
    assert str(fixture.target).encode() not in wire
    assert str(fixture.admission_pinner).encode() not in wire


def test_implement_evidence_handoff_real_dirty_status_maps_to_authority_invalid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "plugin-copy")
    fixture = make_detached_record_fixture(
        tmp_path / "fixture",
        section="section-26-publication-wire-and-decision-store",
        plugin_root=plugin_root,
    )
    module = load_zagrosi_module(fixture.script)
    fake_git = tmp_path / "git-dirty"
    fake_git.write_text("#!/bin/sh\nprintf '?? dirty\\000'\nsleep 30\n")
    fake_git.chmod(0o755)

    monkeypatch.setattr(module, "HANDOFF_GIT", str(fake_git))
    monkeypatch.setattr(module, "require_handoff_platform", lambda root_fd: None)
    monkeypatch.setattr(module, "require_fixed_handoff_dependencies", lambda *args, **kwargs: None)
    emitted: list[tuple[dict, int]] = []
    monkeypatch.setattr(
        module,
        "emit_canonical_json",
        lambda payload, exit_code=0: emitted.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(
        [
            "implement-evidence-handoff",
            "--implementation-root",
            str(fixture.implementation_root),
            "--section",
            "S26",
        ]
    )

    started = time.monotonic()
    assert parsed.func(parsed) == 5
    assert time.monotonic() - started < 5.0
    assert emitted == [
        (
            {
                "schema": "zagrosi-privileged-evidence-handoff-error-v1",
                "purpose": "zagrosi_privileged_evidence_handoff_error",
                "section": "S26",
                "status": "failed",
                "closed_error_code": "HANDOFF_AUTHORITY_INVALID",
            },
            5,
        )
    ]
    assert not (
        fixture.implementation_root
        / module.HANDOFF_SECTION_CONTRACTS["S26"]["evidence_path"]
    ).exists()


def test_implement_evidence_handoff_immediate_dirty_status_reads_one_byte_and_preserves_authority_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "plugin-copy")
    fixture = make_detached_record_fixture(
        tmp_path / "fixture",
        section="section-26-publication-wire-and-decision-store",
        plugin_root=plugin_root,
    )
    module = load_zagrosi_module(fixture.script)
    fake_git = tmp_path / "git-dirty-immediate"
    fake_git.write_text("#!/bin/sh\nprintf '?? dirty\\000'\n")
    fake_git.chmod(0o755)

    real_poll = module.subprocess.Popen.poll
    real_read = module.os.read
    real_killpg = module.os.killpg
    read_calls: list[tuple[int, bytes]] = []
    signal_attempts: list[int] = []
    observed_processes: list[subprocess.Popen[bytes]] = []
    dirty_byte_seen = False
    transient_poll_remaining = 2
    transient_zero_probe_remaining = 2
    transient_poll_count = 0
    transient_zero_probe_count = 0
    eventual_absence_proofs = 0

    def transient_poll(process: subprocess.Popen[bytes]):
        nonlocal transient_poll_remaining, transient_poll_count
        if process not in observed_processes:
            observed_processes.append(process)
        if dirty_byte_seen and transient_poll_remaining:
            transient_poll_remaining -= 1
            transient_poll_count += 1
            return None
        return real_poll(process)

    def recording_read(fd: int, requested: int) -> bytes:
        nonlocal dirty_byte_seen
        chunk = real_read(fd, requested)
        if chunk:
            read_calls.append((requested, chunk))
        if chunk == b"?":
            dirty_byte_seen = True
        return chunk

    def transient_apple_killpg(process_group: int, sig: int) -> None:
        nonlocal transient_zero_probe_remaining, transient_zero_probe_count, eventual_absence_proofs
        if dirty_byte_seen and sig == 0 and transient_zero_probe_remaining:
            transient_zero_probe_remaining -= 1
            transient_zero_probe_count += 1
            raise PermissionError("transient Apple process-group probe race")
        if dirty_byte_seen and sig == signal.SIGTERM:
            signal_attempts.append(sig)
            raise PermissionError("transient Apple process-group signal race")
        try:
            real_killpg(process_group, sig)
        except ProcessLookupError:
            if dirty_byte_seen and sig == 0:
                eventual_absence_proofs += 1
            raise

    monkeypatch.setattr(module, "HANDOFF_GIT", str(fake_git))
    monkeypatch.setattr(module.subprocess.Popen, "poll", transient_poll)
    monkeypatch.setattr(module.os, "read", recording_read)
    monkeypatch.setattr(module.os, "killpg", transient_apple_killpg)
    monkeypatch.setattr(module, "require_handoff_platform", lambda root_fd: None)
    monkeypatch.setattr(module, "require_fixed_handoff_dependencies", lambda *args, **kwargs: None)
    emitted: list[tuple[dict, int]] = []
    monkeypatch.setattr(
        module,
        "emit_canonical_json",
        lambda payload, exit_code=0: emitted.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(
        [
            "implement-evidence-handoff",
            "--implementation-root",
            str(fixture.implementation_root),
            "--section",
            "S26",
        ]
    )

    assert parsed.func(parsed) == 5
    assert [(requested, chunk) for requested, chunk in read_calls if chunk == b"?"] == [(1, b"?")]
    assert transient_poll_count == 2
    assert transient_zero_probe_count == 2
    assert signal_attempts == [signal.SIGTERM]
    assert eventual_absence_proofs >= 1
    assert observed_processes and observed_processes[0].returncode is not None
    assert emitted == [
        (
            {
                "schema": "zagrosi-privileged-evidence-handoff-error-v1",
                "purpose": "zagrosi_privileged_evidence_handoff_error",
                "section": "S26",
                "status": "failed",
                "closed_error_code": "HANDOFF_AUTHORITY_INVALID",
            },
            5,
        )
    ]
    assert not (
        fixture.implementation_root
        / module.HANDOFF_SECTION_CONTRACTS["S26"]["evidence_path"]
    ).exists()


def test_implement_evidence_handoff_failure_writes_only_safe_canonical_stdout(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "plugin-copy")
    fixture = make_detached_record_fixture(
        tmp_path / "fixture",
        section="section-26-publication-wire-and-decision-store",
        plugin_root=plugin_root,
    )
    config_path = fixture.implementation_root / "zagrosi_implement_config.json"
    config = json.loads(config_path.read_bytes())
    config["private_path_mutant"] = str(fixture.planning)
    replace_file(config_path, canonical_json_bytes_for_test(config), mode=0o600)

    result = run_script_raw(
        fixture.script,
        "implement-evidence-handoff",
        "--implementation-root",
        str(fixture.implementation_root),
        "--section",
        "S26",
    )

    assert result.returncode == 5
    assert result.stderr == ""
    assert result.stdout.encode() == canonical_json_bytes_for_test(json.loads(result.stdout))
    assert json.loads(result.stdout) == {
        "schema": "zagrosi-privileged-evidence-handoff-error-v1",
        "purpose": "zagrosi_privileged_evidence_handoff_error",
        "section": "S26",
        "status": "failed",
        "closed_error_code": "HANDOFF_AUTHORITY_INVALID",
    }
    assert len(result.stdout.encode()) <= 4096
    assert str(fixture.planning) not in result.stdout
    assert str(fixture.implementation_root) not in result.stdout


@pytest.mark.parametrize("drift_timing", ("post_verifier", "post_write"))
def test_implement_evidence_handoff_removes_new_receipt_on_late_authority_drift(
    tmp_path: Path,
    monkeypatch,
    drift_timing: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / drift_timing)
    fixture = make_detached_record_fixture(
        tmp_path / f"fixture-{drift_timing}",
        section="section-26-publication-wire-and-decision-store",
        plugin_root=plugin_root,
    )
    module = load_zagrosi_module(fixture.script)
    contract = module.HANDOFF_SECTION_CONTRACTS["S26"]
    source_observation = protected_source_observation_for_test(module)
    test_path = fixture.target / contract["test"]
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text("# fixed privileged handoff verifier fixture\n")
    config = assert_canonical_json_file(fixture.implementation_root / "zagrosi_implement_config.json")
    mutated = False

    def fake_bounded_child(argv: list[str], input_bytes: bytes, **kwargs) -> tuple[int, bytes, bytes]:
        nonlocal mutated
        if argv == module.handoff_root_argv(contract):
            return 0, handoff_receipt_for_test(
                module,
                config,
                contract,
                input_bytes,
                source_observation,
            ), b""
        request_size = int.from_bytes(input_bytes[:4], "big")
        request_raw = input_bytes[4 : 4 + request_size]
        receipt_offset = 4 + request_size
        receipt_size = int.from_bytes(input_bytes[receipt_offset : receipt_offset + 4], "big")
        receipt_raw = input_bytes[receipt_offset + 4 : receipt_offset + 4 + receipt_size]
        verification = handoff_verification_for_test(module, config, contract, request_raw, receipt_raw)
        if drift_timing == "post_verifier":
            plan = fixture.planning / "codex-plan.md"
            replace_file(plan, plan.read_bytes() + b"\npost-verifier planning drift\n")
            mutated = True
        return 0, verification, b""

    original_write = module.write_canonical_json_at

    def write_then_drift(root_fd, relative, payload, **kwargs):
        nonlocal mutated
        result = original_write(root_fd, relative, payload, **kwargs)
        if drift_timing == "post_write" and relative == contract["evidence_path"]:
            replace_file(
                test_path,
                test_path.read_bytes() + b"# post-write source drift\n",
                mode=test_path.stat().st_mode & 0o777,
            )
            mutated = True
        return result

    emitted: list[tuple[dict, int]] = []
    monkeypatch.setattr(module, "require_handoff_platform", lambda root_fd: None)
    monkeypatch.setattr(module, "require_fixed_handoff_dependencies", lambda *args, **kwargs: None)

    def derive_observation(target_fd, selected_contract):
        if drift_timing == "post_write" and mutated:
            return module.ProtectedSourceObservation(
                protected_source_root_identity_digest=source_observation.protected_source_root_identity_digest,
                source_commit=source_observation.source_commit,
                source_tree_sha256=source_observation.source_tree_sha256,
                implementation_source_sha256=source_observation.implementation_source_sha256,
                test_source_sha256="sha256:" + "cd" * 32,
            )
        return source_observation

    monkeypatch.setattr(
        module,
        "derive_protected_source_observation",
        derive_observation,
    )
    monkeypatch.setattr(module, "run_bounded_child", fake_bounded_child)
    monkeypatch.setattr(module, "write_canonical_json_at", write_then_drift)
    monkeypatch.setattr(
        module,
        "emit_canonical_json",
        lambda payload, exit_code=0: emitted.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(
        [
            "implement-evidence-handoff",
            "--implementation-root",
            str(fixture.implementation_root),
            "--section",
            "S26",
        ]
    )

    assert parsed.func(parsed) == 5
    assert mutated is True
    assert emitted[-1][0] == {
        "schema": "zagrosi-privileged-evidence-handoff-error-v1",
        "purpose": "zagrosi_privileged_evidence_handoff_error",
        "section": "S26",
        "status": "failed",
        "closed_error_code": "HANDOFF_AUTHORITY_INVALID",
    }
    assert not (fixture.implementation_root / contract["evidence_path"]).exists()


@pytest.mark.parametrize(
    ("requested_token", "completed", "expected_code"),
    (
        (
            "S28",
            {"section-27-placeholder"},
            "incomplete-handoff-predecessors",
        ),
        (
            "S26",
            {"section-01-foundation"},
            "incomplete-handoff-predecessors",
        ),
        (
            "S26",
            {"section-01-foundation", "section-27-placeholder"},
            "handoff-section-not-ready",
        ),
    ),
)
def test_implement_evidence_handoff_requires_current_readiness_before_any_child(
    tmp_path: Path,
    monkeypatch,
    requested_token: str,
    completed: set[str],
    expected_code: str,
) -> None:
    fixture = make_detached_record_fixture(
        tmp_path,
        section="section-28-scoped-native-and-external-composition",
        manifest_sections=(
            ["section-01-foundation"]
            + [f"section-{number:02d}-placeholder" for number in range(2, 26)]
            + [
                "section-26-publication-wire-and-decision-store",
                "section-27-placeholder",
                "section-28-scoped-native-and-external-composition",
            ]
        ),
    )
    module = load_zagrosi_module(fixture.script)
    progress = module.check_section_progress(fixture.planning)
    s26 = "section-26-publication-wire-and-decision-store"
    s28 = "section-28-scoped-native-and-external-composition"
    s01 = "section-01-foundation"
    s27 = "section-27-placeholder"
    dependencies = {candidate: [s28] for candidate in progress["sections"]}
    dependencies[s01] = []
    dependencies[s27] = []
    dependencies[s26] = [s01, s27]
    dependencies[s28] = [s26, s27]
    child_calls = 0

    def forbidden_child(*args, **kwargs):
        nonlocal child_calls
        child_calls += 1
        raise AssertionError("readiness failure must occur before any child process")

    emitted: list[tuple[dict, int]] = []
    monkeypatch.setattr(module, "dependency_graph", lambda planning_dir, current: dependencies)
    monkeypatch.setattr(
        module,
        "detached_completed_records",
        lambda root_fd, config, current: {section: {} for section in completed},
    )
    if expected_code == "handoff-section-not-ready":
        monkeypatch.setattr(module, "ready_sections", lambda current, graph, done: [s28])
    monkeypatch.setattr(module, "run_bounded_child", forbidden_child)
    monkeypatch.setattr(
        module,
        "emit_canonical_json",
        lambda payload, exit_code=0: emitted.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(
        [
            "implement-evidence-handoff",
            "--implementation-root",
            str(fixture.implementation_root),
            "--section",
            requested_token,
        ]
    )

    assert parsed.func(parsed) == 5
    assert emitted[-1][0] == {
        "schema": "zagrosi-privileged-evidence-handoff-error-v1",
        "purpose": "zagrosi_privileged_evidence_handoff_error",
        "section": requested_token,
        "status": "failed",
        "closed_error_code": "HANDOFF_SECTION_NOT_READY",
    }
    assert child_calls == 0
    contract = module.HANDOFF_SECTION_CONTRACTS[requested_token]
    assert not (fixture.implementation_root / contract["evidence_path"]).exists()


def test_detached_packet_and_skeleton_writers_require_external_output(tmp_path: Path) -> None:
    fixture = make_detached_record_fixture(tmp_path)
    expected_planning = planning_tree_snapshot(fixture.planning)
    commands = (
        (
            "implementation-packet",
            "--section",
            fixture.section,
            "packets",
            f"{fixture.section}-packet.md",
        ),
        ("tdd-skeletons", "--framework", "pytest", "tdd-skeletons", "test_skeleton.py"),
    )
    for command, option, value, directory, filename in commands:
        missing = run_raw(
            command,
            "--planning-dir",
            str(fixture.planning),
            option,
            value,
            "--implementation-root",
            str(fixture.implementation_root),
        )
        assert missing.returncode != 0
        assert json.loads(missing.stdout)["error_code"] == "missing-detached-output-dir"

        planning_output = fixture.planning / ".forge" / directory
        refused = run_raw(
            command,
            "--planning-dir",
            str(fixture.planning),
            option,
            value,
            "--implementation-root",
            str(fixture.implementation_root),
            "--output-dir",
            str(planning_output),
        )
        assert refused.returncode != 0
        assert json.loads(refused.stdout)["error_code"] == "external-artifact-outside-root"
        assert not planning_output.exists()

        external_output = fixture.implementation_root / "code_review" / "generated" / directory
        accepted = run_raw(
            command,
            "--planning-dir",
            str(fixture.planning),
            option,
            value,
            "--implementation-root",
            str(fixture.implementation_root),
            "--output-dir",
            str(external_output),
        )
        assert accepted.returncode == 0, accepted.stderr + accepted.stdout
        assert Path(json.loads(accepted.stdout)["output"]) == external_output / filename
        assert planning_tree_snapshot(fixture.planning) == expected_planning


@pytest.mark.parametrize(
    ("drift_kind", "expected_code"),
    (
        ("planning", "planning-tree-changed"),
        ("tool", "implement-source-drift"),
        ("admission", "admission-pinner-drift"),
        ("evidence", "detached-evidence-drift"),
        ("root_identity", "unsafe-detached-root-identity"),
    ),
)
def test_detached_record_rechecks_every_late_input_before_pinner(
    tmp_path: Path,
    monkeypatch,
    drift_kind: str,
    expected_code: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / drift_kind)
    fixture = make_detached_record_fixture(tmp_path / f"fixture-{drift_kind}", plugin_root=plugin_root)
    module = load_zagrosi_module(fixture.script)
    original_evidence_rows = module.detached_evidence_rows
    evidence_calls = 0
    initial_state = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    initial_pinners = list((fixture.implementation_root / "pinners").glob("*.json"))

    def replace_late_input() -> None:
        if drift_kind == "planning":
            plan = fixture.planning / "codex-plan.md"
            replace_file(plan, plan.read_bytes() + b"\nlate planning replacement\n")
        elif drift_kind == "tool":
            replace_file(
                fixture.script,
                fixture.script.read_bytes() + b"\n# late tool replacement\n",
                mode=fixture.script.stat().st_mode & 0o777,
            )
        elif drift_kind == "admission":
            payload = admission_pinner_payload(fixture.planning)
            payload["o_sha256"] = "sha256:" + "44" * 32
            replace_file(
                fixture.admission_pinner,
                (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
                mode=0o600,
            )
        elif drift_kind == "root_identity":
            fixture.implementation_root.chmod(0o755)
        else:
            replace_file(
                fixture.evidence_path,
                b'{"schema":"late-replacement-evidence-v1","verdict":"PASS"}\n',
                mode=0o600,
            )

    def mutate_before_final_evidence_reopen(*args, **kwargs):
        nonlocal evidence_calls
        evidence_calls += 1
        if evidence_calls == 2:
            replace_late_input()
        return original_evidence_rows(*args, **kwargs)

    captured: list[tuple[dict, int]] = []

    def capture_json(payload: dict, exit_code: int = 0) -> int:
        captured.append((payload, exit_code))
        return exit_code

    monkeypatch.setattr(module, "detached_evidence_rows", mutate_before_final_evidence_reopen)
    monkeypatch.setattr(module, "print_json", capture_json)
    args = module.build_parser().parse_args(
        detached_record_arguments(fixture, "--evidence-row", "record_gate=evidence/record-gate.json")
    )
    result = args.func(args)

    assert result == 1
    assert captured[-1][1] == 1
    assert captured[-1][0]["error_code"] == expected_code
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == initial_state
    assert list((fixture.implementation_root / "pinners").glob("*.json")) == initial_pinners


@pytest.mark.parametrize(
    ("drift_kind", "expected_code"),
    (
        ("planning", "section-record-recovery-required"),
        ("tool", "section-record-recovery-required"),
        ("admission", "section-record-recovery-required"),
        ("evidence", "detached-evidence-drift"),
    ),
)
def test_detached_record_rolls_back_new_pinner_on_post_pinner_authority_drift(
    tmp_path: Path,
    monkeypatch,
    drift_kind: str,
    expected_code: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / drift_kind)
    fixture = make_detached_record_fixture(tmp_path / f"fixture-{drift_kind}", plugin_root=plugin_root)
    module = load_zagrosi_module(fixture.script)
    original_install = module.install_staged_section_pinner
    initial_state = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    initial_pinners = list((fixture.implementation_root / "pinners").glob("*.json"))
    mutated = False

    def replace_after_pinner_install(root_fd, transaction_fd, pinner_path, pinner_raw):
        nonlocal mutated
        result = original_install(root_fd, transaction_fd, pinner_path, pinner_raw)
        if not mutated:
            mutated = True
            if drift_kind == "planning":
                plan = fixture.planning / "codex-plan.md"
                replace_file(plan, plan.read_bytes() + b"\npost-pinner planning drift\n")
            elif drift_kind == "tool":
                replace_file(
                    fixture.script,
                    fixture.script.read_bytes() + b"\n# post-pinner tool drift\n",
                    mode=fixture.script.stat().st_mode & 0o777,
                )
            elif drift_kind == "admission":
                payload = admission_pinner_payload(fixture.planning)
                payload["o_sha256"] = "sha256:" + "77" * 32
                replace_file(
                    fixture.admission_pinner,
                    canonical_json_bytes_for_test(payload),
                    mode=0o600,
                )
            else:
                replace_file(
                    fixture.evidence_path,
                    b'{"schema":"post-pinner-mutant-v1","verdict":"PASS"}\n',
                    mode=0o600,
                )
        return result

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module, "install_staged_section_pinner", replace_after_pinner_install)
    monkeypatch.setattr(
        module,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(
        detached_record_arguments(fixture, "--evidence-row", "record_gate=evidence/record-gate.json")
    )

    assert parsed.func(parsed) == 1
    assert mutated is True
    assert captured[-1][0]["error_code"] == expected_code
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == initial_state
    assert list((fixture.implementation_root / "pinners").glob("*.json")) == initial_pinners


def test_detached_record_rolls_back_state_and_pinner_on_post_state_evidence_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "plugin-copy")
    fixture = make_detached_record_fixture(tmp_path / "fixture", plugin_root=plugin_root)
    module = load_zagrosi_module(fixture.script)
    original_replace_state = module.replace_state_from_transaction
    initial_state = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    initial_pinners = list((fixture.implementation_root / "pinners").glob("*.json"))
    mutated = False

    def promote_then_replace_evidence(root_fd, transaction_fd, expected_raw, replacement):
        nonlocal mutated
        replacement_raw = original_replace_state(root_fd, transaction_fd, expected_raw, replacement)
        if not mutated and replacement.get("completed_sections", {}).get(fixture.section) is not None:
            mutated = True
            replace_file(
                fixture.evidence_path,
                b'{"schema":"post-state-mutant-v1","verdict":"PASS"}\n',
                mode=0o600,
            )
        return replacement_raw

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module, "replace_state_from_transaction", promote_then_replace_evidence)
    monkeypatch.setattr(
        module,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(
        detached_record_arguments(fixture, "--evidence-row", "record_gate=evidence/record-gate.json")
    )

    assert parsed.func(parsed) == 1
    assert captured[-1][0]["error_code"] == "detached-evidence-drift"
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == initial_state
    assert list((fixture.implementation_root / "pinners").glob("*.json")) == initial_pinners


def test_detached_identity_checks_reject_case_aliases_before_any_create(tmp_path: Path) -> None:
    planning = tmp_path / "Planning"
    sections = write_single_section_fixture(planning)
    alias = tmp_path / "pLANNING"
    try:
        case_alias_available = alias.exists() and os.path.samefile(planning, alias)
    except OSError:
        case_alias_available = False
    if not case_alias_available:
        pytest.skip("case-insensitive filesystem alias is unavailable")

    target = tmp_path / "target"
    target.mkdir()
    external_pinner = write_test_admission_pinner(
        tmp_path / "external-admission-pinner.json",
        planning_dir=planning,
    )
    aliased_root = alias / "detached-implementation"
    root_overlap = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(aliased_root),
        "--admission-pinner",
        str(external_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(external_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )
    assert root_overlap.returncode != 0
    assert json.loads(root_overlap.stdout)["error_code"] == "detached-root-overlap"
    assert not aliased_root.exists()

    internal_pinner = write_test_admission_pinner(
        planning / "internal-admission-pinner.json",
        planning_dir=planning,
    )
    external_root = tmp_path / "external-implementation"
    admission_overlap = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(external_root),
        "--admission-pinner",
        str(alias / internal_pinner.name),
        "--expected-admission-pinner-sha256",
        file_sha256(internal_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )
    assert admission_overlap.returncode != 0
    assert json.loads(admission_overlap.stdout)["error_code"] == "admission-pinner-overlap"
    assert not external_root.exists()

    planning_target_root = tmp_path / "planning-target-implementation"
    planning_target_overlap = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(alias),
        "--implementation-root",
        str(planning_target_root),
        "--admission-pinner",
        str(external_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(external_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )
    assert planning_target_overlap.returncode != 0
    assert json.loads(planning_target_overlap.stdout)["error_code"] == "planning-target-overlap"
    assert not planning_target_root.exists()

    protected_target = tmp_path / "ProtectedTarget"
    protected_target.mkdir()
    target_alias = tmp_path / "pROTECTEDtARGET"
    assert target_alias.exists() and os.path.samefile(protected_target, target_alias)
    target_overlap = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(protected_target),
        "--implementation-root",
        str(target_alias),
        "--admission-pinner",
        str(external_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(external_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )
    assert target_overlap.returncode != 0
    assert json.loads(target_overlap.stdout)["error_code"] == "detached-root-target-overlap"
    assert list(protected_target.iterdir()) == []


def test_detached_rerecord_rejects_completed_transitive_dependants_and_current_pointer_drift(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json", planning_dir=planning)
    run_cmd(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )

    def record(section: str, commit: str) -> dict:
        review_dir = implementation_root / "code_review"
        (review_dir / f"{section}-review.md").write_text("# Review\n\nNo blockers.\n")
        (review_dir / f"{section}-decisions.md").write_text("# Decisions\n\nAccepted.\n")
        return run_cmd(
            "implement-record-section",
            "--sections-dir",
            str(sections),
            "--implementation-root",
            str(implementation_root),
            "--section",
            section,
            "--commit",
            commit,
            "--review-artifact",
            f"code_review/{section}-review.md",
            "--review-artifact",
            f"code_review/{section}-decisions.md",
            "--verification",
            f"uv run pytest tests/test_{section}.py",
            "--flight",
            "off",
        )

    record("section-03-storage", "storage-1")
    record("section-01-foundation", "foundation-1")
    record("section-02-api", "api-1")
    state_path = implementation_root / "zagrosi_implement_state.json"
    state_before = state_path.read_bytes()
    pinners_before = {
        path.name: path.read_bytes() for path in sorted((implementation_root / "pinners").glob("*.json"))
    }

    rerecord = run_raw(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-03-storage",
        "--commit",
        "storage-2",
        "--notes",
        "must not replace a closed predecessor",
        "--review-artifact",
        "code_review/section-03-storage-review.md",
        "--review-artifact",
        "code_review/section-03-storage-decisions.md",
        "--verification",
        "uv run pytest tests/test_storage.py",
        "--flight",
        "off",
    )
    assert rerecord.returncode != 0
    rerecord_payload = json.loads(rerecord.stdout)
    assert rerecord_payload["error_code"] == "completed-dependent-pinner-conflict"
    assert rerecord_payload["completed_dependants"] == ["section-01-foundation", "section-02-api"]
    assert state_path.read_bytes() == state_before
    assert {
        path.name: path.read_bytes() for path in sorted((implementation_root / "pinners").glob("*.json"))
    } == pinners_before

    state = json.loads(state_before)
    storage_record = state["completed_sections"]["section-03-storage"]
    old_pinner_path = implementation_root / storage_record["pinner_path"]
    replacement_pinner = json.loads(old_pinner_path.read_bytes())
    replacement_pinner["notes"] = "authenticated replacement"
    replacement_raw = (json.dumps(replacement_pinner, sort_keys=True, separators=(",", ":")) + "\n").encode()
    replacement_sha256 = "sha256:" + hashlib.sha256(replacement_raw).hexdigest()
    replacement_relative = f"pinners/section-03-storage-{replacement_sha256.removeprefix('sha256:')}.json"
    replacement_path = implementation_root / replacement_relative
    replacement_path.write_bytes(replacement_raw)
    replacement_path.chmod(0o600)
    storage_record["notes"] = replacement_pinner["notes"]
    storage_record["pinner_path"] = replacement_relative
    storage_record["pinner_file_sha256"] = replacement_sha256
    replace_file(
        state_path,
        (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        mode=0o600,
    )

    stale_dependent = run_raw(
        "next-section",
        "--planning-dir",
        str(planning),
        "--implementation-root",
        str(implementation_root),
    )
    assert stale_dependent.returncode != 0
    stale_payload = json.loads(stale_dependent.stdout)
    assert stale_payload["error_code"] == "predecessor-pinner-current-state-mismatch"
    assert stale_payload["section"] == "section-01-foundation"
    assert stale_payload["predecessor_section"] == "section-03-storage"


def test_detached_record_rolls_back_new_pinner_on_post_pinner_predecessor_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    run_cmd(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )
    review_dir = implementation_root / "code_review"
    for section in ("section-03-storage", "section-01-foundation"):
        (review_dir / f"{section}-review.md").write_text("# Review\n\nNo blockers.\n")
        (review_dir / f"{section}-decisions.md").write_text("# Decisions\n\nAccepted.\n")
    run_cmd(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-03-storage",
        "--commit",
        "storage-parent",
        "--review-artifact",
        "code_review/section-03-storage-review.md",
        "--review-artifact",
        "code_review/section-03-storage-decisions.md",
        "--verification",
        "uv run pytest tests/test_storage.py",
        "--flight",
        "off",
    )
    state_path = implementation_root / "zagrosi_implement_state.json"
    initial_state = state_path.read_bytes()
    state = json.loads(initial_state)
    parent_path = implementation_root / state["completed_sections"]["section-03-storage"]["pinner_path"]
    original_parent = parent_path.read_bytes()
    parent_mutant = json.loads(original_parent)
    parent_mutant["notes"] = "post-child predecessor replacement"
    parent_mutant_raw = canonical_json_bytes_for_test(parent_mutant)
    initial_pinner_names = {path.name for path in (implementation_root / "pinners").glob("*.json")}

    module = load_zagrosi_module()
    original_install = module.install_staged_section_pinner
    mutated = False

    def replace_parent_after_child_pinner(root_fd, transaction_fd, pinner_path, pinner_raw):
        nonlocal mutated
        result = original_install(root_fd, transaction_fd, pinner_path, pinner_raw)
        if not mutated and pinner_path.startswith("pinners/section-01-foundation-"):
            mutated = True
            replace_file(parent_path, parent_mutant_raw, mode=0o600)
        return result

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module, "install_staged_section_pinner", replace_parent_after_child_pinner)
    monkeypatch.setattr(
        module,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(
        [
            "implement-record-section",
            "--sections-dir",
            str(sections),
            "--implementation-root",
            str(implementation_root),
            "--section",
            "section-01-foundation",
            "--commit",
            "foundation-child",
            "--review-artifact",
            "code_review/section-01-foundation-review.md",
            "--review-artifact",
            "code_review/section-01-foundation-decisions.md",
            "--verification",
            "uv run pytest tests/test_foundation.py",
            "--flight",
            "off",
        ]
    )

    assert parsed.func(parsed) == 1
    assert mutated is True
    assert captured[-1][0]["error_code"] == "section-record-recovery-required"
    assert state_path.read_bytes() == initial_state
    assert {path.name for path in (implementation_root / "pinners").glob("*.json")} == initial_pinner_names


def test_detached_record_rejects_unknown_section_and_uncompleted_predecessors(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    run_cmd(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )

    blocked = run_raw(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-01-foundation",
        "--commit",
        "abc123",
        "--flight",
        "off",
    )
    assert blocked.returncode != 0
    blocked_payload = json.loads(blocked.stdout)
    assert blocked_payload["error_code"] == "incomplete-predecessors"
    assert blocked_payload["incomplete_predecessors"] == ["section-03-storage"]

    unknown = run_raw(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--implementation-root",
        str(implementation_root),
        "--section",
        "section-99-missing",
        "--commit",
        "abc123",
        "--flight",
        "off",
    )
    assert unknown.returncode != 0
    unknown_payload = json.loads(unknown.stdout)
    assert unknown_payload["error_code"] == "unknown-section"
    assert unknown_payload["section"] == "section-99-missing"


def test_detached_implementation_root_rejects_symlink_components(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(real_root, target_is_directory=True)

    result = run_raw(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(linked_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "unsafe-detached-path"
    assert "symbolic link" in payload["error"]


def test_detached_next_section_rejects_planning_or_admission_drift(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    run_cmd(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(),
        "--flight",
        "off",
    )

    (planning / "unexpected.md").write_text("changed membership\n")
    planning_drift = run_raw(
        "next-section",
        "--planning-dir",
        str(planning),
        "--implementation-root",
        str(implementation_root),
    )
    assert planning_drift.returncode != 0
    assert json.loads(planning_drift.stdout)["error_code"] == "planning-tree-drift"
    (planning / "unexpected.md").unlink()

    write_test_admission_pinner(admission_pinner, authority="REPLACED")
    admission_drift = run_raw(
        "next-section",
        "--planning-dir",
        str(planning),
        "--implementation-root",
        str(implementation_root),
    )
    assert admission_drift.returncode != 0
    assert json.loads(admission_drift.stdout)["error_code"] == "admission-pinner-drift"


def test_detached_next_section_rejects_target_root_replacement_without_u_mutation(tmp_path: Path) -> None:
    fixture = make_detached_record_fixture(tmp_path)
    before = planning_tree_snapshot(fixture.implementation_root)
    displaced_target = tmp_path / "displaced-target"
    fixture.target.rename(displaced_target)
    fixture.target.mkdir()
    try:
        result = run_script_raw(
            fixture.script,
            "next-section",
            "--planning-dir",
            str(fixture.planning),
            "--implementation-root",
            str(fixture.implementation_root),
        )
        assert result.returncode == 1
        assert json.loads(result.stdout)["error_code"] == "target-root-identity-drift"
        assert planning_tree_snapshot(fixture.implementation_root) == before
    finally:
        fixture.target.rmdir()
        displaced_target.rename(fixture.target)


@pytest.mark.parametrize("drift_kind", ("planning", "admission", "source"))
def test_authenticated_root_temp_is_retained_when_external_authority_drift_refuses_context(
    tmp_path: Path,
    drift_kind: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / drift_kind)
    fixture = make_detached_record_fixture(tmp_path / f"fixture-{drift_kind}", plugin_root=plugin_root)
    root_temp = fixture.implementation_root / ".forge-progress.json.tmp"
    root_temp.write_bytes(b'{"uncommitted":"retain"}\n')
    root_temp.chmod(0o600)
    if drift_kind == "planning":
        (fixture.planning / "post-setup-drift.md").write_text("drift\n")
        expected_error = "planning-tree-drift"
    elif drift_kind == "admission":
        write_test_admission_pinner(fixture.admission_pinner, authority="REPLACED")
        expected_error = "admission-pinner-drift"
    else:
        skill_path = plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["skill"]
        skill_path.write_bytes(skill_path.read_bytes() + b"\nsource drift\n")
        expected_error = "implement-source-drift"
    before = planning_tree_snapshot(fixture.implementation_root)

    result = run_script_raw(
        fixture.script,
        "next-section",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == expected_error
    assert planning_tree_snapshot(fixture.implementation_root) == before
    assert root_temp.is_file()


def test_detached_next_section_reopens_exact_config_after_mid_command_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = make_detached_record_fixture(tmp_path)
    module = load_zagrosi_module(fixture.script)
    original_completed = module.detached_completed_records
    state_before = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    replaced = False

    def completed_then_replace_config(root_fd, config, progress, **kwargs):
        nonlocal replaced
        completed = original_completed(root_fd, config, progress, **kwargs)
        if not replaced:
            replaced = True
            changed = dict(config)
            changed["runtime"] = "mid-command-replacement"
            replace_file(
                fixture.implementation_root / "zagrosi_implement_config.json",
                canonical_json_bytes_for_test(changed),
                mode=0o600,
            )
        return completed

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module, "detached_completed_records", completed_then_replace_config)
    monkeypatch.setattr(
        module,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.build_parser().parse_args(
        [
            "next-section",
            "--planning-dir",
            str(fixture.planning),
            "--implementation-root",
            str(fixture.implementation_root),
        ]
    )
    assert parsed.func(parsed) == 1
    assert replaced is True
    assert captured[-1][0]["error_code"] == "detached-config-drift"
    assert (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes() == state_before


def test_detached_setup_requires_all_implementation_source_hashes_and_rejects_wrong_hash(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    expected_planning = planning_tree_snapshot(planning)
    base_args = (
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(tmp_path / "detached-implementation"),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        "--flight",
        "off",
    )

    all_source_args = implementation_source_args()
    for index, source in enumerate(IMPLEMENTATION_SOURCE_RELATIVE_PATHS):
        supplied_args = all_source_args[: index * 2] + all_source_args[index * 2 + 2 :]
        missing = run_raw(*base_args[:-2], *supplied_args, *base_args[-2:])
        assert missing.returncode != 0
        missing_payload = json.loads(missing.stdout)
        assert missing_payload["error_code"] == "missing-implement-source-hash"
        assert missing_payload["implement_source"] == source
        assert missing_payload["required_argument"] == f"--expected-implement-{source}-sha256"

    for source in IMPLEMENTATION_SOURCE_RELATIVE_PATHS:
        wrong = run_raw(
            *base_args[:-2],
            *implementation_source_args(**{source: "sha256:" + "0" * 64}),
            *base_args[-2:],
        )
        assert wrong.returncode != 0
        wrong_payload = json.loads(wrong.stdout)
        assert wrong_payload["error_code"] == "implement-source-drift"
        assert wrong_payload["implement_source"] == source
        assert wrong_payload["expected_implement_source_sha256"] == "sha256:" + "0" * 64
    assert planning_tree_snapshot(planning) == expected_planning


def test_detached_setup_rejects_missing_symlinked_and_hardlinked_implementation_sources(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")

    def setup_with(plugin_root: Path, implementation_root: Path, source_args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return run_script_raw(
            plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"],
            "implement-setup",
            "--sections-dir",
            str(sections),
            "--target-dir",
            str(target),
            "--implementation-root",
            str(implementation_root),
            "--admission-pinner",
            str(admission_pinner),
            "--expected-admission-pinner-sha256",
            file_sha256(admission_pinner),
            *source_args,
            "--flight",
            "off",
        )

    missing_root = copy_implementation_plugin(tmp_path / "missing-source")
    missing_args = implementation_source_args(missing_root)
    (missing_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["test"]).unlink()
    missing = setup_with(missing_root, tmp_path / "missing-state", missing_args)
    assert missing.returncode != 0
    missing_payload = json.loads(missing.stdout)
    assert missing_payload["error_code"] == "unsafe-implement-source"
    assert missing_payload["implement_source"] == "test"

    symlink_root = copy_implementation_plugin(tmp_path / "symlink-source")
    symlink_args = implementation_source_args(symlink_root)
    skill_path = symlink_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["skill"]
    replacement = symlink_root / "replacement-skill.md"
    replacement.write_bytes(skill_path.read_bytes())
    skill_path.unlink()
    skill_path.symlink_to(replacement)
    symlinked = setup_with(symlink_root, tmp_path / "symlink-state", symlink_args)
    assert symlinked.returncode != 0
    symlink_payload = json.loads(symlinked.stdout)
    assert symlink_payload["error_code"] == "unsafe-implement-source"
    assert symlink_payload["implement_source"] == "skill"

    hardlink_root = copy_implementation_plugin(tmp_path / "hardlink-source")
    hardlink_args = implementation_source_args(hardlink_root)
    tool_path = hardlink_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"]
    os.link(tool_path, hardlink_root / "tool-alias.py")
    hardlinked = setup_with(hardlink_root, tmp_path / "hardlink-state", hardlink_args)
    assert hardlinked.returncode != 0
    hardlink_payload = json.loads(hardlinked.stdout)
    assert hardlink_payload["error_code"] == "unsafe-implement-source"
    assert hardlink_payload["implement_source"] == "tool"


def test_every_detached_command_rejects_implementation_source_changed_after_setup(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "copied-plugin")
    script = plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"]
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    expected_planning = planning_tree_snapshot(planning)
    setup = run_script_raw(
        script,
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(plugin_root),
        "--flight",
        "off",
    )
    assert setup.returncode == 0, setup.stderr + setup.stdout
    config = assert_canonical_json_file(implementation_root / "zagrosi_implement_config.json")
    assert config["implement_tool_path"] == str(script)
    assert config["implement_skill_path"] == str(plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["skill"])
    assert config["implement_test_path"] == str(plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["test"])

    test_source = plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["test"]
    test_source.write_bytes(test_source.read_bytes() + b"\n# changed after detached setup\n")
    commands = (
        ("next-section", "--planning-dir", str(planning), "--implementation-root", str(implementation_root)),
        (
            "implement-progress",
            "--planning-dir",
            str(planning),
            "--implementation-root",
            str(implementation_root),
            "--section",
            "section-03-storage",
            "--stage",
            "red",
        ),
        (
            "implement-record-section",
            "--sections-dir",
            str(sections),
            "--implementation-root",
            str(implementation_root),
            "--section",
            "section-03-storage",
            "--flight",
            "off",
        ),
    )
    for command in commands:
        result = run_script_raw(script, *command)
        assert result.returncode != 0
        payload = json.loads(result.stdout)
        assert payload["error_code"] == "implement-source-drift"
        assert payload["implement_source"] == "test"
    assert planning_tree_snapshot(planning) == expected_planning


def test_detached_next_section_rejects_mid_command_tool_source_drift(tmp_path: Path, monkeypatch) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "copied-plugin")
    script = plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"]
    planning = tmp_path / "planning"
    sections = write_non_topological_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(tmp_path / "admission-pinner.json")
    expected_planning = planning_tree_snapshot(planning)
    setup = run_script_raw(
        script,
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(plugin_root),
        "--flight",
        "off",
    )
    assert setup.returncode == 0, setup.stderr + setup.stdout

    module = load_zagrosi_module(script)
    original_completed_records = module.detached_completed_records
    changed = False

    def change_tool_after_initial_verification(*args, **kwargs):
        nonlocal changed
        result = original_completed_records(*args, **kwargs)
        if not changed:
            script.write_bytes(script.read_bytes() + b"\n# changed during detached command\n")
            changed = True
        return result

    captured: list[tuple[dict, int]] = []

    def capture_json(payload: dict, exit_code: int = 0) -> int:
        captured.append((payload, exit_code))
        return exit_code

    monkeypatch.setattr(module, "detached_completed_records", change_tool_after_initial_verification)
    monkeypatch.setattr(module, "print_json", capture_json)
    result = module.detached_next_section(
        SimpleNamespace(planning_dir=str(planning), implementation_root=str(implementation_root))
    )

    assert result == 1
    payload, exit_code = captured[-1]
    assert exit_code == 1
    assert payload["error_code"] == "implement-source-drift"
    assert payload["implement_source"] == "tool"
    assert planning_tree_snapshot(planning) == expected_planning


def test_legacy_implementation_uses_dependency_ready_order_and_rejects_early_record(tmp_path: Path) -> None:
    sections = write_non_topological_section_fixture(tmp_path)
    setup = run_cmd(
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(tmp_path),
        "--flight",
        "off",
    )
    assert setup["next_section"] == "section-03-storage"
    assert setup["ready_sections"] == ["section-03-storage"]

    blocked = run_raw(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-01-foundation",
        "--commit",
        "abc123",
        "--flight",
        "off",
    )
    assert blocked.returncode != 0
    assert json.loads(blocked.stdout)["incomplete_predecessors"] == ["section-03-storage"]

    unknown = run_raw(
        "implement-record-section",
        "--sections-dir",
        str(sections),
        "--section",
        "section-99-missing",
        "--commit",
        "abc123",
        "--flight",
        "off",
    )
    assert unknown.returncode != 0
    assert json.loads(unknown.stdout)["error_code"] == "unknown-section"


def test_patch_scope_preserves_long_file_extensions(tmp_path: Path) -> None:
    section_file = tmp_path / "section-01-snapshots.md"
    section_file.write_text(
        "# Section\n\n"
        "Update `examples/evals/suite.json`, `src/ui/Widget.jsx`, `src/ui/App.tsx`, and `config/settings.yaml`.\n"
    )
    diff_file = tmp_path / "scope.diff"
    diff_file.write_text(
        "diff --git a/examples/evals/suite.json b/examples/evals/suite.json\n"
        "+++ b/examples/evals/suite.json\n"
        "diff --git a/src/ui/Widget.jsx b/src/ui/Widget.jsx\n"
        "+++ b/src/ui/Widget.jsx\n"
        "diff --git a/src/ui/App.tsx b/src/ui/App.tsx\n"
        "+++ b/src/ui/App.tsx\n"
        "diff --git a/config/settings.yaml b/config/settings.yaml\n"
        "+++ b/config/settings.yaml\n"
    )

    scope = run_cmd("patch-scope", "--section-file", str(section_file), "--diff-file", str(diff_file), "--strict")

    assert scope["success"] is True
    assert scope["declared_files"] == [
        "config/settings.yaml",
        "examples/evals/suite.json",
        "src/ui/App.tsx",
        "src/ui/Widget.jsx",
    ]
    assert scope["out_of_scope"] == []


def test_patch_scope_accepts_declared_frontend_assets(tmp_path: Path) -> None:
    section_file = tmp_path / "section-01-ui.md"
    section_file.write_text("# Section\n\nUpdate `index.html`, `src/App.css`, and `public/logo.svg`.\n")
    diff_file = tmp_path / "scope.diff"
    diff_file.write_text(
        "diff --git a/index.html b/index.html\n"
        "+++ b/index.html\n"
        "diff --git a/src/App.css b/src/App.css\n"
        "+++ b/src/App.css\n"
        "diff --git a/public/logo.svg b/public/logo.svg\n"
        "+++ b/public/logo.svg\n"
    )

    scope = run_cmd("patch-scope", "--section-file", str(section_file), "--diff-file", str(diff_file), "--strict")

    assert scope["success"] is True
    assert scope["declared_files"] == ["index.html", "public/logo.svg", "src/App.css"]
    assert scope["out_of_scope"] == []


def test_patch_scope_reports_untracked_files_by_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "src/auth").mkdir(parents=True)
    (repo / "src/auth/extra.py").write_text("SECRET = 'new file'\n")

    section_file = tmp_path / "section-01-auth.md"
    section_file.write_text("# Section\n\nModify `src/auth/oauth.py`.\n")

    scope = run_raw("patch-scope", "--section-file", str(section_file), "--repo", str(repo))

    assert scope.returncode != 0
    payload = json.loads(scope.stdout)
    assert "src/auth/extra.py" in payload["changed_files"]
    assert payload["out_of_scope"] == ["src/auth/extra.py"]
    assert any(item["code"] == "out-of-scope-file" for item in payload["findings"])


def test_patch_scope_staged_excludes_unstaged_worktree_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "src/auth").mkdir(parents=True)
    extra = repo / "src/auth/extra.py"
    extra.write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "src/auth/extra.py"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    extra.write_text("VALUE = 2\n")

    section_file = tmp_path / "section-01-auth.md"
    section_file.write_text("# Section\n\nModify `src/auth/oauth.py`.\n")

    scope = run_cmd("patch-scope", "--section-file", str(section_file), "--repo", str(repo), "--staged")

    assert scope["changed_files"] == []
    assert scope["out_of_scope"] == []


def test_implementation_drift_staged_excludes_unstaged_worktree_changes(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    write_single_section_fixture(planning, "section-01-auth")
    (planning / "sections" / "section-01-auth.md").write_text("# Section\n\nImplement `src/auth/oauth.py`.\n")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "src/auth").mkdir(parents=True)
    planned = repo / "src/auth/oauth.py"
    unrelated = repo / "src/auth/local.py"
    planned.write_text("VALUE = 1\n")
    unrelated.write_text("LOCAL = 1\n")
    subprocess.run(["git", "add", "src/auth/oauth.py", "src/auth/local.py"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    planned.write_text("VALUE = 2\n")
    subprocess.run(["git", "add", "src/auth/oauth.py"], cwd=repo, check=True, capture_output=True, text=True)
    unrelated.write_text("LOCAL = 2\n")

    drift = run_cmd("implementation-drift", "--planning-dir", str(planning), "--repo", str(repo), "--staged")

    assert drift["changed_files"] == ["src/auth/oauth.py"]
    assert drift["out_of_scope"] == []


def write_implementation_drift_fixture(
    planning: Path,
    manifest: tuple[str, ...],
    section_bodies: dict[str, str],
) -> None:
    sections = planning / "sections"
    sections.mkdir(parents=True)
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        + "".join(f"{section}\n" for section in manifest)
        + "END_MANIFEST -->\n"
    )
    for section, body in section_bodies.items():
        (sections / f"{section}.md").write_text(body)


def test_implementation_drift_uses_latest_exact_owner_and_ignores_prose_paths(
    tmp_path: Path,
) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-tooling", "section-02-config-correction"),
        {
            "section-01-tooling": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "tests/test_tooling.py\n"
                "```\n"
            ),
            "section-02-config-correction": (
                "# Section 02\n\n"
                "The correction is verified separately in `tests/test_release.py`.\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "```\n\n"
                "## Verification\n\n"
                "Run `tests/test_release.py` after updating the package.\n"
            ),
        },
    )
    diff_file = tmp_path / "config.diff"
    diff_file.write_text(
        "diff --git a/apps/web/package.json b/apps/web/package.json\n"
        "+++ b/apps/web/package.json\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert drift["success"] is True
    assert drift["active_sections"] == ["section-02-config-correction"]
    assert drift["planned_files"] == ["apps/web/package.json", "tests/test_tooling.py"]
    assert drift["planned_tests"] == []
    assert drift["changed_tests"] == []
    assert "planned-tests-not-changed" not in {
        item["code"] for item in drift["findings"]
    }


def test_implementation_drift_latest_owner_still_requires_its_test(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-tooling", "section-02-config-correction"),
        {
            "section-01-tooling": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "tests/test_tooling.py\n"
                "```\n"
            ),
            "section-02-config-correction": (
                "# Section 02\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "tests/test_config_correction.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "config.diff"
    diff_file.write_text(
        "diff --git a/apps/web/package.json b/apps/web/package.json\n"
        "+++ b/apps/web/package.json\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
    )

    assert drift["active_sections"] == ["section-02-config-correction"]
    assert drift["planned_tests"] == ["tests/test_config_correction.py"]
    assert drift["changed_tests"] == []
    assert drift["missing_planned_tests"] == ["tests/test_config_correction.py"]
    assert "planned-tests-not-changed" in {
        item["code"] for item in drift["findings"]
    }


def test_implementation_drift_ignores_test_superseded_by_later_owner(
    tmp_path: Path,
) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-api", "section-02-test-correction"),
        {
            "section-01-api": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/api.py\n"
                "tests/test_shared.py\n"
                "```\n"
            ),
            "section-02-test-correction": (
                "# Section 02\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "tests/test_shared.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "api.diff"
    diff_file.write_text(
        "diff --git a/src/api.py b/src/api.py\n"
        "+++ b/src/api.py\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert drift["active_sections"] == ["section-01-api"]
    assert drift["planned_tests"] == []
    assert drift["changed_tests"] == []
    assert drift["sections_missing_changed_tests"] == []


def test_implementation_drift_ignores_unmanifested_section_files(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-tooling", "section-02-config-correction"),
        {
            "section-01-tooling": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "tests/test_tooling.py\n"
                "```\n"
            ),
            "section-02-config-correction": (
                "# Section 02\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "```\n"
            ),
            "section-99-stray": (
                "# Stray section\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "tests/test_stray.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "config.diff"
    diff_file.write_text(
        "diff --git a/apps/web/package.json b/apps/web/package.json\n"
        "+++ b/apps/web/package.json\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert drift["active_sections"] == ["section-02-config-correction"]
    assert drift["planned_files"] == ["apps/web/package.json", "tests/test_tooling.py"]
    assert drift["planned_tests"] == []
    assert "tests/test_stray.py" not in drift["planned_files"]


def test_implementation_drift_rejects_incomplete_manifest_without_traceback(
    tmp_path: Path,
) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-app", "section-02-missing"),
        {
            "section-01-app": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/app.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "app.diff"
    diff_file.write_text(
        "diff --git a/src/app.py b/src/app.py\n"
        "+++ b/src/app.py\n"
    )

    result = run_raw(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["section_progress"]["state"] == "partial"
    assert payload["section_progress"]["missing"] == ["section-02-missing"]
    assert "invalid-sections" in {item["code"] for item in payload["findings"]}


def test_implementation_drift_does_not_count_out_of_scope_test_as_changed_test(
    tmp_path: Path,
) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-app",),
        {
            "section-01-app": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/app.py\n"
                "tests/test_app.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "app.diff"
    diff_file.write_text(
        "diff --git a/src/app.py b/src/app.py\n"
        "+++ b/src/app.py\n"
        "diff --git a/tests/unplanned.py b/tests/unplanned.py\n"
        "+++ b/tests/unplanned.py\n"
    )

    result = run_raw(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["active_sections"] == ["section-01-app"]
    assert payload["planned_tests"] == ["tests/test_app.py"]
    assert payload["changed_tests"] == []
    assert payload["missing_planned_tests"] == ["tests/test_app.py"]
    assert payload["out_of_scope"] == ["tests/unplanned.py"]
    codes = {item["code"] for item in payload["findings"]}
    assert {"implementation-drift-file", "planned-tests-not-changed"} <= codes


def test_implementation_drift_requires_changed_test_for_each_active_section(
    tmp_path: Path,
) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-api", "section-02-worker"),
        {
            "section-01-api": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/api.py\n"
                "tests/test_api.py\n"
                "```\n"
            ),
            "section-02-worker": (
                "# Section 02\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/worker.py\n"
                "tests/test_worker.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "multi-section.diff"
    diff_file.write_text(
        "diff --git a/tests/test_api.py b/tests/test_api.py\n"
        "+++ b/tests/test_api.py\n"
        "diff --git a/src/worker.py b/src/worker.py\n"
        "+++ b/src/worker.py\n"
    )

    result = run_raw(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["active_sections"] == ["section-01-api", "section-02-worker"]
    assert payload["changed_tests"] == ["tests/test_api.py"]
    assert payload["missing_planned_tests"] == ["tests/test_worker.py"]
    assert payload["sections_missing_changed_tests"] == ["section-02-worker"]
    findings = [
        item for item in payload["findings"]
        if item["code"] == "planned-tests-not-changed"
    ]
    assert len(findings) == 1
    assert "section-02-worker" in findings[0]["message"]


def test_implementation_drift_preserves_legacy_prose_path_fallback(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-legacy",),
        {
            "section-01-legacy": (
                "# Legacy section\n\n"
                "Implement `src/legacy.py` and verify it in `tests/test_legacy.py`.\n"
            ),
        },
    )
    diff_file = tmp_path / "legacy.diff"
    diff_file.write_text(
        "diff --git a/src/legacy.py b/src/legacy.py\n"
        "+++ b/src/legacy.py\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
    )

    assert drift["active_sections"] == ["section-01-legacy"]
    assert drift["planned_files"] == ["src/legacy.py", "tests/test_legacy.py"]
    assert drift["planned_tests"] == ["tests/test_legacy.py"]


def test_implementation_drift_counts_storybook_story_as_changed_test(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-shell",),
        {
            "section-01-shell": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/ui/foundation.stories.tsx\n"
                "tests/shell.spec.ts\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "story.diff"
    diff_file.write_text(
        "diff --git a/src/ui/foundation.stories.tsx b/src/ui/foundation.stories.tsx\n"
        "+++ b/src/ui/foundation.stories.tsx\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert drift["active_sections"] == ["section-01-shell"]
    assert drift["changed_tests"] == ["src/ui/foundation.stories.tsx"]
    assert drift["planned_tests"] == [
        "src/ui/foundation.stories.tsx",
        "tests/shell.spec.ts",
    ]


def test_implementation_drift_does_not_treat_substring_matches_as_tests(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-source",),
        {
            "section-01-source": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/latest.py\n"
                "src/contest.py\n"
                "src/specification.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "source.diff"
    diff_file.write_text(
        "diff --git a/src/latest.py b/src/latest.py\n"
        "+++ b/src/latest.py\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert drift["active_sections"] == ["section-01-source"]
    assert drift["planned_tests"] == []
    assert drift["changed_tests"] == []


def test_test_path_classifier_accepts_story_modules_and_rejects_story_prose() -> None:
    module = load_zagrosi_module()

    for suffix in ("js", "jsx", "mjs", "ts", "tsx"):
        assert module.is_test_path(f"src/ui/foundation.stories.{suffix}") is True
    for path in (
        "tests/test_auth.py",
        "src/auth.test.ts",
        "src/auth_spec.rb",
        "src/test/java/AuthTest.java",
        "src/spec/helpers.rb",
        "src/FooTest.java",
        "src/TestOAuth.java",
        "conftest.py",
    ):
        assert module.is_test_path(path) is True
    for path in (
        "docs/user-stories.md",
        "src/ui/user.stories.md",
        "src/ui/foundation.stories.tsx.backup",
        "src/ui/foundation.story.tsx",
        "src/latest.py",
        "src/contest.py",
        "src/special.py",
        "src/specification.py",
        "src/testing.py",
        "src/unittest.py",
    ):
        assert module.is_test_path(path) is False


def test_implement_progress_preserves_overlapping_writes(tmp_path: Path, monkeypatch) -> None:
    module = load_zagrosi_module()
    planning = tmp_path / "planning"
    planning.mkdir()
    start = threading.Barrier(2)
    original_write_json = module.write_json

    def slow_progress_write(path: Path, payload: dict) -> None:
        if path.name == "forge-progress.json":
            time.sleep(0.05)
        original_write_json(path, payload)

    monkeypatch.setattr(module, "write_json", slow_progress_write)

    def record(stage: str) -> int:
        start.wait(timeout=2)
        return module.implement_progress(
            SimpleNamespace(
                planning_dir=str(planning),
                section="section-01-progress",
                stage=stage,
                command=None,
                result=f"{stage} recorded",
                notes=None,
            )
        )

    threads = [threading.Thread(target=record, args=(stage,)) for stage in ("red", "green")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    assert not any(thread.is_alive() for thread in threads)

    state = json.loads((planning / "implementation" / "forge-progress.json").read_text())
    assert sorted(event["stage"] for event in state["events"]) == ["green", "red"]


def test_implement_postflight_defers_state_lint_until_sections_recorded(tmp_path: Path) -> None:
    planning = write_quality_plan_fixture(tmp_path / "planning")

    postflight = run_cmd(
        "postflight",
        "--phase",
        "implement",
        "--planning-dir",
        str(planning),
        "--sections-dir",
        str(planning / "sections"),
        "--target-dir",
        str(tmp_path),
        "--depth",
        "fast",
        "--flight",
        "strict",
    )

    assert postflight["success"] is True
    assert postflight["sections_recorded_complete"] is False
    assert postflight["remaining_sections"] == ["section-01-auth"]
    assert "lint-implementation-state" not in postflight["blocking_gates"]
    progress_gate = next(gate for gate in postflight["gates"] if gate["name"] == "implementation-progress")
    assert progress_gate["success"] is True
    assert progress_gate["payload"]["deferred_gate"] == "lint-implementation-state"


def write_quality_plan_fixture(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    spec = tmp_path / "codex-spec.md"
    spec.write_text(
        "<!-- FORGE_META\n"
        "{\n"
        '  "artifact_type": "normalized_spec",\n'
        '  "workflow": "zagrosi-plan",\n'
        '  "depth_mode": "standard",\n'
        '  "requirement_ids": ["REQ-001"]\n'
        "}\n"
        "END_FORGE_META -->\n\n"
        "# Spec\n\n"
        "## Reader Note\n"
        "This normalized spec is self-contained for a fresh implementer.\n\n"
        "## Current System Context\n"
        "REQ-001: Add an authenticated OAuth callback flow. The existing system has auth modules, session helpers, "
        "provider configuration, and pytest coverage. The implementation must preserve current password login and "
        "session behavior while introducing OAuth callback support.\n\n"
        "## Requirements\n"
        "- REQ-001: Valid OAuth callbacks create authenticated local sessions after state validation.\n"
        "- REQ-001: Invalid state, provider denial, and duplicate-account ambiguity fail without creating sessions.\n\n"
        "## Contracts And Constraints\n"
        "The contract spans `src/auth/oauth.py`, `src/auth/session.py`, `src/auth/config.py`, and "
        "`tests/auth/test_oauth.py`. Session creation remains owned by `src/auth/session.py`; OAuth handling delegates "
        "to that module. Provider secrets are never logged.\n\n"
        "## Testing And Risks\n"
        "Tests cover valid callback, invalid state, provider denial, duplicate email handling, and configuration errors. "
        "The main risks are account-link ambiguity, token leakage, and route-level duplication of session policy.\n\n"
        + "Additional context for the implementer: OAuth callback behavior must be deterministic, observable, "
        "security reviewed, and compatible with existing auth routes. " * 34
    )
    (tmp_path / "codex-interview.md").write_text(
        "user_interviewed: true\n\n"
        "# Planning Interview\n\n"
        "Q: Should OAuth callback work create sessions directly in the route handler?\n"
        "A: No. Session creation should remain in `src/auth/session.py` so existing cookie policy is preserved.\n\n"
        "Q: Which failure cases must be planned before implementation?\n"
        "A: Invalid state, provider denial, duplicate accounts, missing config, and token leakage must be covered.\n"
    )
    (tmp_path / "codex-plan.md").write_text(
        "<!-- FORGE_META\n"
        "{\n"
        '  "artifact_type": "implementation_plan",\n'
        '  "workflow": "zagrosi-plan",\n'
        '  "depth_mode": "standard",\n'
        '  "requirement_ids": ["REQ-001"]\n'
        "}\n"
        "END_FORGE_META -->\n\n"
        "# Plan\n\n"
        "## Reader Note\nThis plan is self-contained for a fresh implementer with no prior context.\n\n"
        "## Current State Evidence\nVerified existing auth ownership through current state review: `src/auth/oauth.py`, "
        "`src/auth/session.py`, `src/auth/config.py`, and `tests/auth/test_oauth.py` are the files in scope. "
        "A grep for auth callback routes should happen before implementation.\n\n"
        "## Goal and Non-Goals\nREQ-001 adds OAuth callback handling and excludes billing.\n\n"
        "## Architecture\nUse `src/auth/oauth.py`, `src/auth/session.py`, and `src/auth/config.py`.\n\n"
        "## Architecture Rationale\nThe rationale is to keep callback-specific provider behavior in OAuth code while preserving session policy in "
        "the existing session module. The rejected alternative is duplicating cookie/session creation inside the callback route.\n\n"
        "## Contracts\nThe callback contract accepts provider payload, state, and configuration, then returns a typed result shape for success, "
        "provider denial, invalid state, or ambiguous account linking.\n\n"
        "## File Tree\n```\nsrc/auth/oauth.py\nsrc/auth/session.py\nsrc/auth/config.py\ntests/auth/test_oauth.py\n```\n\n"
        "## Phase Plan\nBatch 1 writes tests and config validation. Batch 2 implements callback state validation. Batch 3 wires session creation "
        "and executes verification.\n\n"
        "## File Plan\nModify `src/auth/oauth.py` and create `tests/auth/test_oauth.py`.\n\n"
        "## Test Matrix\nUnit tests cover valid callback, invalid state, provider denial, duplicate email, config missing fields, and no token leakage. "
        "Write pytest cases first and run `uv run pytest tests/auth/test_oauth.py`.\n\n"
        "## Security and Privacy\nValidate callback state, protect tokens, and enforce auth permissions.\n\n"
        "## Risks and Edge Cases\nHandle provider denial, duplicate accounts, and invalid state failure.\n\n"
        "## Rollout\nShip behind configuration with backward compatibility and rollback by disabling provider config.\n\n"
        "## Review Integration\nReview integration confirms account-link ambiguity and token logging are the stop-line risks. Accepted review edits "
        "must keep provider secrets out of logs and require explicit user confirmation for ambiguous linking.\n\n"
        "## Acceptance\nREQ-001 is complete when tests pass and valid callbacks create sessions.\n\n"
        + "Detailed implementation context with current-state evidence, contracts, phase sequencing, test matrix, rollback, "
        "review integration, and implementation rationale. " * 80
    )
    (tmp_path / "codex-plan-tdd.md").write_text(
        "# TDD\n\n"
        "REQ-001: `tests/auth/test_oauth.py::test_valid_callback_creates_session` "
        "expects failure before implementation. Run `uv run pytest`.\n\n"
        "## Test Matrix\n"
        "- `test_valid_callback_creates_session`: expected failure until OAuth callback maps a provider identity to a local session.\n"
        "- `test_invalid_state_rejects_callback`: expected failure until state validation rejects tampered values.\n"
        "- `test_provider_denial_does_not_create_session`: expected failure until provider errors short-circuit before session creation.\n"
        "- `test_duplicate_email_requires_explicit_policy`: expected failure until ambiguous account linking stops safely.\n"
        "- `test_provider_config_missing_fields_fails_startup`: expected failure until config validation is centralized.\n\n"
        + "Fixture context: provider payloads, signed state values, invalid state values, duplicate local users, and log capture "
        "must be available to every test. " * 32
    )
    (tmp_path / "decisions.md").write_text(
        "# Decision Log\n\n"
        "| ID | Date | Decision | Alternatives | Rationale | Impact |\n"
        "|----|------|----------|--------------|-----------|--------|\n"
        "| DEC-001 | Test | Keep OAuth callback orchestration in auth service. | Put all callback logic in a route. | Service ownership keeps REQ-001 testable. | `src/auth/oauth.py` owns callback policy. |\n"
    )
    (tmp_path / "risk-register.md").write_text(
        "# Risk Register\n\n"
        "| ID | Risk | Severity | Likelihood | Mitigation | Section | Verification |\n"
        "|----|------|----------|------------|------------|---------|--------------|\n"
        "| RISK-001 | Invalid state creates a session. | High | Medium | Validate state before provider work. | section-01-auth | `test_invalid_state_rejects_callback`. |\n"
    )
    (tmp_path / "traceability.md").write_text(
        "# Traceability Matrix\n\n"
        "| Requirement | Plan Coverage | Section Coverage | Test Coverage | Status |\n"
        "|-------------|---------------|------------------|---------------|--------|\n"
        "| REQ-001 | `codex-plan.md`, `codex-plan-tdd.md` | `section-01-auth.md` | `tests/auth/test_oauth.py` | Covered |\n"
    )
    (tmp_path / "quality-gates.md").write_text("# Quality Gates\n\nREQ-001 covered by pytest and traceability.\n")
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        "section-01-auth\n"
        "END_MANIFEST -->\n\n"
        "# Sections\n\n"
        "## Project Notes\n"
        "Runtime is python-uv and tests run through pytest. This index is self-contained enough for an implementer to "
        "understand the build order, dependency boundary, and verification command. The auth section owns OAuth callback "
        "tests, provider configuration validation, session creation wiring, and token logging safety. No billing or dashboard "
        "work belongs in this section.\n\n"
        "## Dependency Graph\n"
        "| Section | Depends On | Blocks | Parallelizable |\n"
        "|---------|------------|--------|----------------|\n"
        "| section-01-auth | - | - | No |\n\n"
        "## Execution Order\n"
        "1. Batch 1: section-01-auth. Write tests first, implement the callback flow, run `uv run pytest`, then update "
        "implementation notes.\n\n"
        "## Section Summaries\n"
        "### section-01-auth\n"
        "Implements REQ-001 by validating OAuth callback state, handling provider denial, delegating session creation to "
        "`src/auth/session.py`, and verifying behavior in `tests/auth/test_oauth.py`. It can be implemented alone because "
        "it has no dependency on other sections.\n"
    )
    (sections / "section-01-auth.md").write_text(
        "# section-01-auth\n\n"
        "## Purpose\nImplement REQ-001 OAuth callback behavior.\n\n"
        "## Tests First\nCreate `tests/auth/test_oauth.py` with failing tests for valid callback, invalid state, and provider error.\n\n"
        "## Implementation\nModify `src/auth/oauth.py`, `src/auth/session.py`, and `src/auth/config.py` to validate state, handle provider errors, and create sessions.\n\n"
        "## Acceptance\nREQ-001 is complete when verification passes with `uv run pytest` and invalid callbacks do not create sessions.\n\n"
        "## Background Context\nThis section is self-contained and copies the OAuth ownership, security rationale, session contract, and route boundaries "
        "from the plan. It depends on no prior sections.\n\n"
        "## File Tree\n```\nsrc/auth/oauth.py\nsrc/auth/session.py\nsrc/auth/config.py\ntests/auth/test_oauth.py\n```\n\n"
        "## Risks\nInvalid state, provider denial, duplicate accounts, and token leakage are the main risks.\n\n"
        + "The section is self-contained and includes enough implementation context, expected failures, file paths, contracts, "
        "verification, risks, and acceptance details. " * 12
    )
    return tmp_path


def test_quality_gates_traceability_and_status(tmp_path: Path) -> None:
    planning = write_quality_plan_fixture(tmp_path)

    export_path = tmp_path / "findings.jsonl"
    plan = run_cmd(
        "lint-plan",
        "--planning-dir",
        str(planning),
        "--depth",
        "fast",
        "--profile",
        "enterprise",
        "--strict",
        "--export",
        str(export_path),
    )
    assert plan["success"] is True
    assert plan["score"] == 100
    assert export_path.exists()

    sections = run_cmd("lint-sections", "--planning-dir", str(planning), "--depth", "fast")
    assert sections["success"] is True
    assert sections["section_estimates"][0]["effort"] in {"small", "medium", "large"}

    trace = run_cmd("traceability", "--planning-dir", str(planning))
    assert trace["success"] is True
    assert trace["coverage"]["REQ-001"]["covered"] is True
    assert trace["orphans"] == {"sections": [], "tests": []}

    trace_csv = tmp_path / "trace.csv"
    exported = run_cmd("trace-export", "--planning-dir", str(planning), "--format", "csv", "--output", str(trace_csv))
    assert exported["success"] is True
    assert trace_csv.read_text().startswith("requirement,in_plan,in_tdd,sections,covered")

    next_ready = run_cmd("next-section", "--planning-dir", str(planning))
    assert next_ready["next_section"] == "section-01-auth"

    parallel = run_cmd("parallel-plan", "--planning-dir", str(planning))
    assert parallel["layers"] == [["section-01-auth"]]

    estimates = run_cmd("section-estimates", "--planning-dir", str(planning))
    assert estimates["estimates"][0]["section"] == "section-01-auth"

    prompts = run_cmd("agent-prompts", "--planning-dir", str(planning), "--type", "security-reviewer")
    assert Path(prompts["prompt_files"][0]).exists()

    budget = run_cmd("context-budget", "--planning-dir", str(planning), "--max-words", "12000")
    assert budget["success"] is True
    assert budget["total_words"] > 0

    evidence = run_cmd("lint-evidence", "--planning-dir", str(planning), "--min-files", "3")
    assert evidence["success"] is True
    assert evidence["file_count"] >= 3

    readiness = run_cmd("lint-implementation-readiness", "--planning-dir", str(planning))
    assert readiness["success"] is True
    assert readiness["sections"][0]["section"] == "section-01-auth"

    score = run_cmd("forge-score", "--planning-dir", str(planning), "--depth", "fast")
    assert score["forge_score"] >= 90
    assert score["components"]["traceability"] == 100

    first_history = run_cmd("forge-score", "--planning-dir", str(planning), "--depth", "fast", "--write-history")
    second_history = run_cmd("forge-score", "--planning-dir", str(planning), "--depth", "fast", "--write-history")
    assert Path(first_history["history_path"]).exists()
    assert second_history["trend_delta"] == 0

    ledger = run_cmd("assumption-ledger", "--planning-dir", str(planning), "--write")
    assert ledger["success"] is True
    assert (planning / "assumption-ledger.md").exists()

    packet = run_cmd("implementation-packet", "--planning-dir", str(planning), "--section", "section-01-auth")
    assert Path(packet["output"]).exists()
    assert "REQ-001" in packet["requirements"]

    brief = run_cmd("context-brief", "--planning-dir", str(planning), "--section", "section-01-auth")
    assert brief["success"] is True
    assert brief["word_count"] > 0

    skeletons = run_cmd("tdd-skeletons", "--planning-dir", str(planning), "--framework", "pytest")
    assert Path(skeletons["output"]).exists()
    assert "test_valid_callback_creates_session" in skeletons["tests"]

    progress = run_cmd(
        "implement-progress",
        "--planning-dir",
        str(planning),
        "--section",
        "section-01-auth",
        "--stage",
        "verified",
        "--result",
        "tests passed",
    )
    assert progress["event_count"] == 1

    section_file = planning / "sections" / "section-01-auth.md"
    commit = run_cmd("commit-message", "--section-file", str(section_file))
    assert commit["subject"] == "feat: implement 01 auth"

    diff_file = tmp_path / "scope.diff"
    diff_file.write_text("diff --git a/src/auth/oauth.py b/src/auth/oauth.py\n+++ b/src/auth/oauth.py\n")
    scope = run_cmd("patch-scope", "--section-file", str(section_file), "--diff-file", str(diff_file))
    assert scope["success"] is True
    assert scope["out_of_scope"] == []

    status = run_cmd("status", "--path", str(planning))
    assert status["success"] is True
    assert status["section_progress"]["state"] == "complete"

    diffed = run_cmd("plan-diff", "--before", str(planning / "codex-spec.md"), "--after", str(planning / "codex-plan.md"))
    assert diffed["success"] is True
    assert diffed["word_delta"] > 0


def test_assumption_ledger_uses_canonical_typed_line_tokens_and_replays_deterministically(
    tmp_path: Path,
) -> None:
    planning = tmp_path / "planning"
    planning.mkdir()
    source_rows = {
        1: "Assumption at the first boundary.",
        26: "Assumption at the final single-letter boundary.",
        27: "Assumption at the first double-letter boundary.",
        52: "Assumption at the final a-prefix boundary.",
        53: "Assumption at the first b-prefix boundary.",
        211: "Assumption exactly at the collision source line.",
    }
    source_lines = [""] * 211
    for line_no, text in source_rows.items():
        source_lines[line_no - 1] = text
    (planning / "codex-spec.md").write_text("\n".join(source_lines) + "\n", encoding="utf-8")

    first = run_cmd("assumption-ledger", "--planning-dir", str(planning), "--write")
    ledger_path = planning / "assumption-ledger.md"
    first_bytes = ledger_path.read_bytes()
    second = run_cmd("assumption-ledger", "--planning-dir", str(planning), "--write")
    second_bytes = ledger_path.read_bytes()

    assert first["rows"] == [
        {
            "type": "assumption",
            "artifact": "spec",
            "line": "La",
            "text": "Assumption at the first boundary.",
        },
        {
            "type": "assumption",
            "artifact": "spec",
            "line": "Lz",
            "text": "Assumption at the final single-letter boundary.",
        },
        {
            "type": "assumption",
            "artifact": "spec",
            "line": "Laa",
            "text": "Assumption at the first double-letter boundary.",
        },
        {
            "type": "assumption",
            "artifact": "spec",
            "line": "Laz",
            "text": "Assumption at the final a-prefix boundary.",
        },
        {
            "type": "assumption",
            "artifact": "spec",
            "line": "Lba",
            "text": "Assumption at the first b-prefix boundary.",
        },
        {
            "type": "assumption",
            "artifact": "spec",
            "line": "Lhc",
            "text": "Assumption exactly at the collision source line.",
        },
    ]
    assert all(re.fullmatch(r"L[a-z]+", row["line"]) for row in first["rows"])
    assert second == first
    assert second_bytes == first_bytes
    ledger = first_bytes.decode("utf-8")
    rows_json = json.dumps(first["rows"], sort_keys=True)
    assert "| assumption | spec | Lhc | Assumption exactly at the collision source line. |" in ledger
    assert "| 211 |" not in ledger
    assert "L211" not in ledger
    assert "Lxd3" not in ledger
    assert "L211" not in rows_json
    assert "Lxd3" not in rows_json
    assert re.search(r"(?<![0-9A-Za-z])211(?![0-9A-Za-z])", ledger) is None
    assert re.search(r"(?<![0-9A-Za-z])211(?![0-9A-Za-z])", rows_json) is None


def test_workflow_options_recommends_deep_and_interview_for_ambiguous_prompt() -> None:
    payload = run_cmd(
        "workflow-options",
        "--brief",
        "maybe use external review or auto PR, whatever you recommend",
    )

    assert payload["success"] is True
    assert payload["depth"]["recommended"] == "deep"
    assert payload["depth"]["requires_confirmation"] is True
    assert payload["interview"]["required"] is True
    assert payload["interview"]["use_structured_input_when_available"] is True
    assert payload["interview"]["fallback"] == "chat"
    assert payload["autonomy"]["auto_commit"] is False
    assert payload["autonomy"]["auto_pr"] is False
    assert payload["autonomy"]["ci_watch"] is False
    assert payload["autonomy"]["fix_watch_loop"] is False


def test_workflow_options_respects_explicit_depth() -> None:
    payload = run_cmd("workflow-options", "--brief", "small docs fix", "--depth", "fast")

    assert payload["success"] is True
    assert payload["depth"]["selected"] == "fast"
    assert payload["depth"]["recommended"] == "fast"
    assert payload["depth"]["requires_confirmation"] is False


def test_workflow_options_includes_recommended_interview_choices_with_rationale() -> None:
    payload = run_cmd(
        "workflow-options",
        "--brief",
        "maybe use external review, web research, auto PR, whatever you recommend",
    )

    option_sets = payload["interview"]["option_sets"]
    assert option_sets
    assert any(
        len([option for option in option_set["options"] if option["recommended"]]) == 1
        for option_set in option_sets
    )
    for option_set in option_sets:
        recommended = [option for option in option_set["options"] if option["recommended"]]
        assert len(recommended) <= 1
        if recommended:
            option = recommended[0]
            assert option["recommended_label"].endswith("(Recommended)")
            assert option["rationale"]


def test_capability_inventory_redacts_secrets_and_reports_tools(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[plugins."github@openai-curated"]\n'
        "enabled = true\n\n"
        '[plugins."zagrosi-forge@zagrosi"]\n'
        "enabled = true\n\n"
        "[mcp_servers.context7]\n"
        'url = "https://mcp.context7.com/mcp"\n\n'
        "[mcp_servers.context7.http_headers]\n"
        'CONTEXT7_API_KEY = "SECRET-DO-NOT-LEAK"\n'
    )

    payload = run_cmd("capability-inventory", "--plugin-root", str(ROOT), "--config", str(config))
    serialized = json.dumps(payload)

    assert payload["success"] is True
    assert "SECRET-DO-NOT-LEAK" not in serialized
    assert {"gh", "codex", "claude", "gemini"} <= set(payload["local_tools"])
    assert "github@openai-curated" in {item["id"] for item in payload["plugins"]["configured"]}
    assert "zagrosi-forge@zagrosi" in {item["id"] for item in payload["plugins"]["configured"]}
    assert "context7" in {item["name"] for item in payload["mcp_servers"]["configured"]}
    assert payload["recommendations"]


def test_capability_inventory_handles_missing_config(tmp_path: Path) -> None:
    payload = run_cmd("capability-inventory", "--plugin-root", str(ROOT), "--config", str(tmp_path / "missing.toml"))

    assert payload["success"] is True
    assert {"gh", "codex", "claude", "gemini"} <= set(payload["local_tools"])
    assert payload["warnings"]


def test_review_capabilities_reports_mandatory_codex_fallback(tmp_path: Path) -> None:
    (tmp_path / "zagrosi_plan_config.json").write_text(json.dumps({"review_mode": "external_llm"}))

    payload = run_cmd("review-capabilities", "--planning-dir", str(tmp_path))

    assert payload["success"] is True
    assert payload["configured_mode"] == "external_llm"
    assert payload["baseline"]["codex_review"]["available"] is True
    assert payload["baseline"]["codex_review"]["mandatory"] is True
    assert payload["external"]
    assert {item["execution"] for item in payload["external"].values()} <= {"opt_in", "not_configured"}


def test_review_capabilities_warns_on_skip_mode(tmp_path: Path) -> None:
    (tmp_path / "zagrosi_plan_config.json").write_text(json.dumps({"review_mode": "skip"}))

    payload = run_cmd("review-capabilities", "--planning-dir", str(tmp_path))

    assert payload["success"] is True
    assert payload["configured_mode"] == "skip"
    assert any("skip" in item.lower() and "review" in item.lower() for item in payload["recommendations"])


def test_lint_plan_thin_artifacts_recommend_questions_before_padding(tmp_path: Path) -> None:
    planning = write_quality_plan_fixture(tmp_path)
    review_file = planning / "reviews" / "architecture.md"
    review_file.parent.mkdir(exist_ok=True)
    review_file.write_text("# Architecture\n\nREQ-001: too short.\n")

    result = run_raw("lint-plan", "--planning-dir", str(planning), "--depth", "deep", "--strict")

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    thin_findings = [item for item in payload["findings"] if item["code"].endswith("too-thin")]
    assert thin_findings
    recommendation = " ".join(item.get("recommendation", "") for item in thin_findings).lower()
    assert "ask relevant questions" in recommendation or "targeted research" in recommendation or "missing decisions" in recommendation
    assert "add more words" not in recommendation


def test_planning_consistency_reports_missing_late_requirement(tmp_path: Path) -> None:
    (tmp_path / "codex-spec.md").write_text("# Spec\n\nREQ-001: Existing behavior.\nREQ-011: Late interview consistency.\n")
    (tmp_path / "codex-plan.md").write_text("# Plan\n\nREQ-001 only.\n")
    (tmp_path / "codex-plan-tdd.md").write_text("# TDD\n\nREQ-001 only.\n")

    result = run_raw("planning-consistency", "--planning-dir", str(tmp_path), "--strict")

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert any("REQ-011" in item["message"] for item in payload["findings"])
    recommendation = " ".join(item.get("recommendation", "") for item in payload["findings"]).lower()
    assert "review planning docs" in recommendation
    assert "ask the user" in recommendation


def test_new_commands_are_discoverable() -> None:
    commands = run_cmd("commands")
    names = {item["name"] for item in commands["commands"]}
    assert {
        "workflow-options",
        "capability-inventory",
        "review-capabilities",
        "planning-consistency",
        "update-check",
        "self-update",
    } <= names


def test_doctor_and_requirement_extraction(tmp_path: Path) -> None:
    doctor = run_cmd("doctor", "--plugin-root", str(ROOT))
    assert doctor["success"] is True
    assert doctor["marketplace"]["name"] == "zagrosi"
    assert doctor["marketplace"]["plugin"] == "zagrosi-forge@zagrosi"

    marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text())
    assert marketplace["plugins"][0]["name"] == "zagrosi-forge"
    assert marketplace["plugins"][0]["source"] == {"source": "local", "path": "./"}
    assert marketplace["plugins"][0]["policy"]["authentication"] == "ON_INSTALL"

    req_file = tmp_path / "brief.md"
    req_file.write_text("# Brief\n\n- must support OAuth login\n- should allow logout\n")
    extracted = run_cmd("extract-requirements", "--file", str(req_file), "--write")
    assert extracted["updated"] is True
    assert "REQ-001" in req_file.read_text()
    assert extracted["requirements"][1]["id"] == "REQ-002"


def test_interview_gate_blocks_missing_and_fake_interviews(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project-manifest.md").write_text(
        "<!-- SPLIT_MANIFEST\n"
        "01-auth\n"
        "END_MANIFEST -->\n\n"
        "# Project Manifest\n\n"
        "## Execution Order\nRun `01-auth` first.\n\n"
        "## Dependencies\n`01-auth` depends on no earlier split and blocks later work.\n\n"
        "## Parallelization\nNo parallel work is needed for this fixture.\n\n"
        "## Shared Concerns\nTesting and docs are shared concerns.\n\n"
        "## Commands\nUse `$zagrosi-plan` on `01-auth/spec.md`.\n"
    )
    split = project / "01-auth"
    split.mkdir()
    (split / "spec.md").write_text(
        "# Auth Spec\n\n"
        "## In Scope\nREQ-001: Implement auth.\n\n"
        "## Out Of Scope\nBilling is out of scope.\n\n"
        "## Acceptance Criteria\nDone when auth tests pass.\n\n"
        "## Testing\nRun pytest.\n\n"
        "## Open Questions\nUnknown provider details remain.\n"
    )

    missing = run_raw("lint-project-manifest", "--planning-dir", str(project), "--strict")
    assert missing.returncode != 0
    missing_codes = {item["code"] for item in json.loads(missing.stdout)["findings"]}
    assert "missing-interview" in missing_codes

    postflight = run_raw("postflight", "--phase", "project", "--planning-dir", str(project), "--flight", "strict")
    assert postflight.returncode != 0
    assert "lint-interview" in json.loads(postflight.stdout)["blocking_gates"]

    (project / "zagrosi_project_interview.md").write_text(
        "interview_mode: skipped_with_reason\n"
        "skip_reason: User explicitly asked to proceed from a complete written brief.\n"
    )
    skipped = run_cmd("lint-interview", "--phase", "project", "--planning-dir", str(project), "--strict")
    assert skipped["success"] is True

    (project / "zagrosi_project_interview.md").write_text(
        "user_interviewed: true\n\n"
        "# Project Interview\n\n"
        "Q: What guardrail is this validating?\n"
        "A: It validates that skipped or fake interviews are blocked without treating this answer as fake.\n"
    )
    real_project = run_cmd("lint-interview", "--phase", "project", "--planning-dir", str(project), "--strict")
    assert real_project["success"] is True

    planning = write_quality_plan_fixture(tmp_path / "planning")
    (planning / "codex-interview.md").write_text(
        "user_interviewed: true\n\n"
        "# Planning Interview\n\n"
        "Q: TBD\n"
        "A: TBD\n"
    )
    fake = run_raw("lint-interview", "--phase", "plan", "--planning-dir", str(planning), "--strict")
    assert fake.returncode != 0
    fake_codes = {item["code"] for item in json.loads(fake.stdout)["findings"]}
    assert "placeholder-interview" in fake_codes

    (planning / "codex-interview.md").write_text(
        "interview_mode: skipped_with_reason\n"
        "skip_reason: User supplied a complete approved spec and asked to skip questions for this fixture.\n"
    )
    skipped_plan = run_cmd("lint-interview", "--phase", "plan", "--planning-dir", str(planning), "--strict")
    assert skipped_plan["success"] is True


def test_install_codex_updates_config(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[plugins."other@example"]\nenabled = true\n')

    dry_run = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
        "--dry-run",
        "--no-verify-codex",
    )
    assert dry_run["success"] is True
    assert dry_run["changed"] is True
    assert "[marketplaces.zagrosi]" in dry_run["config_preview"]
    assert "[marketplaces.zagrosi]" not in config.read_text()
    assert dry_run["cache"]["changed"] is True
    assert not Path(dry_run["cache"]["path"]).exists()

    installed = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
        "--no-verify-codex",
    )
    assert installed["success"] is True
    assert installed["changed"] is True
    assert installed["config_changed"] is True
    assert installed["backup_path"]
    updated = config.read_text()
    assert "[marketplaces.zagrosi]" in updated
    assert f'source = "{ROOT}"' in updated
    assert '[plugins."zagrosi-forge@zagrosi"]' in updated
    assert "enabled = true" in updated
    assert Path(installed["backup_path"]).exists()
    cache_path = Path(installed["cache"]["path"])
    assert cache_path == tmp_path / "plugins" / "cache" / "zagrosi" / "zagrosi-forge" / "0.2.0"
    assert (cache_path / ".codex-plugin" / "plugin.json").exists()
    assert (cache_path / "skills" / "zagrosi-project" / "SKILL.md").exists()

    repeated = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
        "--no-verify-codex",
    )
    assert repeated["success"] is True
    assert repeated["changed"] is False
    assert repeated["cache"]["changed"] is False
    assert repeated["backup_path"] is None


def test_update_check_reports_cache_and_config_status(tmp_path: Path) -> None:
    config = tmp_path / "codex" / "config.toml"

    status = run_cmd("update-check", "--plugin-root", str(ROOT), "--config", str(config))

    assert status["success"] is True
    assert status["operation"] == "update-check"
    assert status["network_policy"] == "local-only"
    assert status["remote_checked"] is False
    assert status["cache"]["current"] is False
    assert status["cache"]["exists"] is False
    assert status["cache"]["changed"] is True
    assert Path(status["cache"]["path"]) == tmp_path / "codex" / "plugins" / "cache" / "zagrosi" / "zagrosi-forge" / "0.2.0"
    assert status["config"]["current"] is False
    assert status["restart_required"] is True
    assert any("self-update" in item for item in status["next_steps"])
    assert not config.exists()


def test_self_update_materializes_cache_and_update_check_passes(tmp_path: Path) -> None:
    config = tmp_path / "codex" / "config.toml"

    updated = run_cmd("self-update", "--plugin-root", str(ROOT), "--config", str(config), "--no-verify-codex")

    assert updated["success"] is True
    assert updated["operation"] == "self-update"
    assert updated["changed"] is True
    cache_path = Path(updated["cache"]["path"])
    assert cache_path == tmp_path / "codex" / "plugins" / "cache" / "zagrosi" / "zagrosi-forge" / "0.2.0"
    assert not (cache_path / "planning").exists()
    assert config.exists()

    status = run_cmd("update-check", "--plugin-root", str(ROOT), "--config", str(config))

    assert status["success"] is True
    assert status["cache"]["current"] is True
    assert status["cache"]["changed"] is False
    assert status["config"]["current"] is True
    assert status["restart_required"] is False
    assert any("already current" in item.lower() for item in status["next_steps"])


def test_install_codex_verifies_prompt_input_with_cached_plugin(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"debug\" ] && [ \"$2\" = \"prompt-input\" ]; then\n"
        "  printf '%s\\n' 'zagrosi-forge:zagrosi-project' 'zagrosi-forge:zagrosi-plan' 'zagrosi-forge:zagrosi-implement'\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    installed = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
        "--verify-codex",
        env=env,
    )

    assert installed["success"] is True
    assert installed["verification"]["status"] == "passed"
    assert installed["verification"]["missing"] == []


def test_strict_profile_blocks_medium_findings(tmp_path: Path) -> None:
    (tmp_path / "codex-plan.md").write_text(
        "<!-- FORGE_META\n"
        '{"artifact_type": "implementation_plan"}\n'
        "END_FORGE_META -->\n\n"
        "# Thin\n\nGoal, architecture, file `src/app.py`, tests, security, risk, rollout, rollback, acceptance.\n"
    )
    result = run_raw("lint-plan", "--planning-dir", str(tmp_path), "--strict")
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["strict"] is True
    assert payload["success"] is False


def test_review_board_governance_and_migration(tmp_path: Path) -> None:
    (tmp_path / "claude-plan.md").write_text("# Old Plan\n\nREQ-001 with tests and `src/app.py`.\n")

    migrated = run_cmd("migrate", "--planning-dir", str(tmp_path))
    assert migrated["success"] is True
    assert (tmp_path / "codex-plan.md").exists()
    assert (tmp_path / "decisions.md").exists()

    prompts = run_cmd("review-board-prompts", "--planning-dir", str(tmp_path))
    assert prompts["success"] is True
    assert len(prompts["prompt_files"]) == 6
    assert all(Path(path).exists() for path in prompts["prompt_files"])

    stubs_dir = tmp_path / "new"
    stubs = run_cmd("write-governance-stubs", "--planning-dir", str(stubs_dir), "--depth", "deep")
    assert stubs["success"] is True
    assert len(stubs["created"]) == 4


def test_eval_suite_and_new_invalid_fixtures() -> None:
    report = run_cmd("eval-suite", "--examples-dir", str(ROOT / "examples"))
    assert report["success"] is True
    assert {Path(row["planning_dir"]).name for row in report["rows"]} == {"01-authentication", "01-auth"}
    assert all(row["forge_score"] == 100 for row in report["rows"])

    fake = run_raw("lint-evidence", "--planning-dir", str(ROOT / "examples" / "invalid" / "fake-evidence"), "--strict")
    assert fake.returncode != 0
    fake_codes = {item["code"] for item in json.loads(fake.stdout)["findings"]}
    assert "missing-command-evidence" in fake_codes

    large = run_raw(
        "lint-implementation-readiness",
        "--planning-dir",
        str(ROOT / "examples" / "invalid" / "overlarge-section"),
        "--max-files",
        "4",
        "--strict",
    )
    assert large.returncode != 0
    large_codes = {item["code"] for item in json.loads(large.stdout)["findings"]}
    assert "too-many-owned-files" in large_codes

    governance = run_raw(
        "lint-artifact-schema",
        "--planning-dir",
        str(ROOT / "examples" / "invalid" / "bad-governance"),
        "--strict",
    )
    assert governance.returncode != 0
    governance_codes = {item["code"] for item in json.loads(governance.stdout)["findings"]}
    assert {"invalid-decisions-table", "invalid-risks-table", "invalid-traceability-table"} <= governance_codes


def test_eval_suite_uses_suite_json_rows_and_snapshot_check(tmp_path: Path) -> None:
    examples = tmp_path / "examples"
    evals = examples / "evals"
    evals.mkdir(parents=True)
    planning = write_quality_plan_fixture(examples / "benchmarks" / "alpha")
    suite = {
        "benchmarks": [
            {"name": "missing-bench", "planning_dir": "../missing"},
            {"name": "alpha-bench", "planning_dir": "../benchmarks/alpha", "depth": "standard"},
        ],
        "snapshots_dir": "golden",
    }
    suite_path = evals / "suite.json"
    suite_path.write_text(json.dumps(suite))

    missing = run_raw("eval-suite", "--examples-dir", str(examples))
    assert missing.returncode != 0
    missing_payload = json.loads(missing.stdout)
    assert missing_payload["success"] is False
    assert "missing-bench" in {item["name"] for item in missing_payload["suite_errors"]}

    suite["benchmarks"] = [{"name": "alpha-bench", "planning_dir": "../benchmarks/alpha", "depth": "standard"}]
    suite_path.write_text(json.dumps(suite))
    report = run_cmd("eval-suite", "--examples-dir", str(examples))
    assert [row["name"] for row in report["rows"]] == ["alpha-bench"]
    assert Path(report["rows"][0]["planning_dir"]) == planning

    updated = run_cmd("eval-suite", "--examples-dir", str(examples), "--update-snapshots")
    assert updated["snapshot_summary"]["updated"] == ["alpha-bench"]

    checked = run_cmd("eval-suite", "--examples-dir", str(examples), "--check-snapshots")
    assert checked["snapshot_summary"]["matched"] == ["alpha-bench"]

    snapshot = evals / "golden" / "alpha-bench-forge-score.json"
    snapshot.write_text(json.dumps({"planning_dir_name": "wrong", "forge_score": 1, "grade": "D", "components": {}}))
    drifted = run_raw("eval-suite", "--examples-dir", str(examples), "--check-snapshots")
    assert drifted.returncode != 0
    drift_payload = json.loads(drifted.stdout)
    assert "alpha-bench" in {item["name"] for item in drift_payload["snapshot_summary"]["drifted"]}


def test_eval_suite_fails_when_fixtures_are_absent_or_empty(tmp_path: Path) -> None:
    missing = run_raw("eval-suite", "--examples-dir", str(tmp_path / "missing"), "--check-snapshots")
    assert missing.returncode != 0
    missing_payload = json.loads(missing.stdout)
    assert missing_payload["success"] is False
    assert missing_payload["discovery_mode"] == "missing"
    assert missing_payload["suite_errors"][0]["error"] == "examples_dir does not exist"

    empty = tmp_path / "empty-examples"
    empty.mkdir()
    empty_result = run_raw("eval-suite", "--examples-dir", str(empty), "--check-snapshots")
    assert empty_result.returncode != 0
    empty_payload = json.loads(empty_result.stdout)
    assert empty_payload["success"] is False
    assert empty_payload["discovery_mode"] == "glob"
    assert empty_payload["suite_errors"][0]["error"] == "No benchmark planning fixtures found"

    evals = empty / "evals"
    evals.mkdir()
    (evals / "suite.json").write_text(json.dumps({"benchmarks": [], "snapshots_dir": "golden"}))
    suite_empty = run_raw("eval-suite", "--examples-dir", str(empty), "--check-snapshots")
    assert suite_empty.returncode != 0
    suite_empty_payload = json.loads(suite_empty.stdout)
    assert suite_empty_payload["discovery_mode"] == "suite"
    assert suite_empty_payload["suite_errors"][0]["error"] == "benchmarks list is empty"


def test_eval_suite_keeps_glob_fallback_without_suite_json(tmp_path: Path) -> None:
    examples = tmp_path / "examples"
    planning = write_quality_plan_fixture(examples / "gallery" / "alpha")

    report = run_cmd("eval-suite", "--examples-dir", str(examples))

    assert report["success"] is True
    assert [Path(row["planning_dir"]) for row in report["rows"]] == [planning]
    assert report["discovery_mode"] == "glob"


def test_advanced_operational_commands_and_snapshots(tmp_path: Path) -> None:
    planning = write_quality_plan_fixture(tmp_path / "planning")

    pre = run_cmd(
        "preflight",
        "--phase",
        "plan",
        "--file",
        str(planning / "codex-spec.md"),
        "--target-dir",
        str(ROOT),
        "--flight",
        "advisory",
    )
    assert pre["success"] is True
    assert {gate["name"] for gate in pre["gates"]} >= {"spec-file", "doctor", "codebase-evidence"}

    pretty = run_text(
        "preflight",
        "--phase",
        "plan",
        "--file",
        str(planning / "codex-spec.md"),
        "--target-dir",
        str(ROOT),
        "--pretty",
    )
    assert "ZAGROSI FORGE PREFLIGHT: PLAN" in pretty
    assert "[PASS] doctor" in pretty

    post = run_cmd(
        "postflight",
        "--phase",
        "plan",
        "--planning-dir",
        str(planning),
        "--depth",
        "fast",
        "--flight",
        "advisory",
    )
    assert post["success"] is True
    assert "forge-score" in {gate["name"] for gate in post["gates"]}

    pretty_score = run_text("forge-score", "--planning-dir", str(planning), "--depth", "fast", "--pretty")
    assert "ZAGROSI FORGE SCORE" in pretty_score
    assert "Components:" in pretty_score

    impl_pre = run_cmd(
        "preflight",
        "--phase",
        "implement",
        "--sections-dir",
        str(planning / "sections"),
        "--target-dir",
        str(tmp_path),
        "--flight",
        "advisory",
    )
    assert impl_pre["success"] is True
    assert "suggest-section-splits" in {gate["name"] for gate in impl_pre["gates"]}

    schema = run_cmd("lint-artifact-schema", "--planning-dir", str(planning), "--strict")
    assert schema["success"] is True
    assert schema["score"] == 100

    split = run_cmd(
        "suggest-section-splits",
        "--planning-dir",
        str(ROOT / "examples" / "invalid" / "overlarge-section"),
        "--max-files",
        "4",
    )
    assert split["suggestions"]
    assert split["suggestions"][0]["recommendation"] == "Split before implementation."

    ok_diff = tmp_path / "ok.diff"
    ok_diff.write_text(
        "diff --git a/src/auth/oauth.py b/src/auth/oauth.py\n"
        "+++ b/src/auth/oauth.py\n"
        "diff --git a/tests/auth/test_oauth.py b/tests/auth/test_oauth.py\n"
        "+++ b/tests/auth/test_oauth.py\n"
    )
    drift_ok = run_cmd("implementation-drift", "--planning-dir", str(planning), "--diff-file", str(ok_diff), "--strict")
    assert drift_ok["success"] is True
    assert drift_ok["out_of_scope"] == []

    bad_diff = tmp_path / "bad.diff"
    bad_diff.write_text(
        "diff --git a/src/auth/oauth.py b/src/auth/oauth.py\n"
        "+++ b/src/auth/oauth.py\n"
        "diff --git a/src/billing/plans.py b/src/billing/plans.py\n"
        "+++ b/src/billing/plans.py\n"
    )
    drift_bad = run_raw("implementation-drift", "--planning-dir", str(planning), "--diff-file", str(bad_diff), "--strict")
    assert drift_bad.returncode != 0
    assert "implementation-drift-file" in {item["code"] for item in json.loads(drift_bad.stdout)["findings"]}

    target = tmp_path / "target"
    target.mkdir()
    (target / "package.json").write_text('{"scripts":{"test":"vitest run","lint":"eslint .","build":"vite build"}}\n')
    tests_dir = target / "tests"
    tests_dir.mkdir()
    (tests_dir / "auth.test.ts").write_text("import { expect, test } from 'vitest';\n")
    evidence = run_cmd("codebase-evidence", "--target-dir", str(target), "--planning-dir", str(planning), "--write")
    assert "npm run test" in evidence["candidate_commands"]
    assert Path(evidence["output"]).exists()

    report_path = tmp_path / "report.html"
    report = run_cmd("report", "--planning-dir", str(planning), "--depth", "fast", "--output", str(report_path))
    assert report["success"] is True
    assert "Zagrosi Forge Report" in report_path.read_text()

    trial = run_cmd(
        "e2e-trial-record",
        "--planning-dir",
        str(planning),
        "--name",
        "fixture trial",
        "--output-dir",
        str(tmp_path / "trials"),
        "--implementation-success",
        "yes",
        "--time-to-plan-minutes",
        "42",
    )
    assert Path(trial["output"]).exists()
    assert trial["record"]["metrics"]["implementation_success"] == "yes"

    for planning_dir, snapshot in [
        (ROOT / "examples" / "saas" / "01-authentication", ROOT / "examples" / "evals" / "golden" / "saas-authentication-forge-score.json"),
        (ROOT / "examples" / "typescript-app" / "01-auth", ROOT / "examples" / "evals" / "golden" / "typescript-auth-preferences-forge-score.json"),
    ]:
        actual = run_cmd("forge-score", "--planning-dir", str(planning_dir), "--depth", "standard", "--strict")
        expected = json.loads(snapshot.read_text())
        assert {
            "planning_dir_name": Path(actual["planning_dir"]).name,
            "forge_score": actual["forge_score"],
            "grade": actual["grade"],
            "components": actual["components"],
        } == expected

    release = run_cmd("release-check", "--plugin-root", str(ROOT))
    assert release["success"] is True
    assert any(".agents/plugins/marketplace.json" in row["command"] for row in release["results"])
    assert any("eval-suite" in row["command"] and "--check-snapshots" in row["command"] for row in release["results"])


def test_release_check_skips_example_gates_when_examples_are_absent(tmp_path: Path) -> None:
    package = tmp_path / "bundle"
    for relative in [
        ".agents",
        ".codex-plugin",
        "assets",
        "scripts",
        "skills",
    ]:
        shutil.copytree(ROOT / relative, package / relative)
    for filename in [".codexignore", "LICENSE", "NOTICE.md", "README.md", "pyproject.toml"]:
        shutil.copy2(ROOT / filename, package / filename)

    release = run_cmd("release-check", "--plugin-root", str(package))

    assert release["success"] is True
    command_text = "\n".join(row["command"] for row in release["results"])
    assert "examples/evals/suite.json" not in command_text
    assert "lint-project-manifest" not in command_text
    assert "eval-suite" not in command_text
    assert ".agents/plugins/marketplace.json" in command_text


def test_lint_project_manifest_fixture() -> None:
    for example in ("saas", "typescript-app"):
        result = run_cmd("lint-project-manifest", "--planning-dir", str(ROOT / "examples" / example), "--strict")
        assert result["success"] is True
        assert result["score"] == 100


def test_lint_sections_rejects_shell_gates_for_non_predecessor_owned_paths(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = planning / "sections"
    sections.mkdir(parents=True)
    manifest = [
        "section-01-root",
        "section-02-direct",
        "section-03-current",
        "section-04-successor",
        "section-05-parallel",
    ]
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        + "\n".join(manifest)
        + "\nEND_MANIFEST -->\n\n"
        "# Sections\n\n"
        "## Dependency Graph\n\n"
        "| Section | Depends On | Blocks | Parallelizable |\n"
        "|---|---|---|---|\n"
        "| section-01-root | none | section-02-direct, section-05-parallel | Yes |\n"
        "| section-02-direct | section-01-root | section-03-current | No |\n"
        "| section-03-current | section-02-direct | section-04-successor | No |\n"
        "| section-04-successor | section-03-current | none | No |\n"
        "| section-05-parallel | section-01-root | none | Yes |\n\n"
        "## Execution Order\n\n"
        "Run the root, direct, current, and successor sections in dependency sequence. The parallel section may run "
        "concurrently after the root. "
        + "The dependency graph, execution order, blocking edge, and parallel boundary are explicit. " * 20
    )
    owned = {
        "section-01-root": ["tests/root.py"],
        "section-02-direct": ["tests/direct.py"],
        "section-03-current": ["tests/current.py"],
        "section-04-successor": [
            "tests/continued.py",
            "tests/quoted.py",
            "tests/successor.py",
        ],
        "section-05-parallel": ["tests/parallel.py"],
    }

    def section_text(section: str, gates: str = "") -> str:
        return (
            f"# {section}\n\n"
            "## Goal and dependencies\n\n"
            "Implement the section after its declared dependencies. Non-goals exclude every successor and parallel "
            "implementation boundary.\n\n"
            "## Exact path ownership\n\n"
            "This section owns exactly these paths:\n\n"
            "```text\n"
            + "\n".join(owned[section])
            + "\n```\n\n"
            "## Tests First\n\n"
            "Write the expected failure first, then implement the contract and run verification. The test matrix covers "
            "success, rejection, replay, rollback, security, privacy, and acceptance.\n\n"
            "## Implementation and acceptance\n\n"
            "The current state, architecture rationale, interface contract, file tree, phase plan, risks, rollout, and "
            "acceptance criteria are fixed for this implementation. "
            + "Tests first establish the expected failure before implementation, then verification proves the contract. " * 22
            + gates
        )

    allowed_and_near_miss_gates = (
        "\n\n## Shell gates and near misses\n\n"
        "```bash\n"
        "pytest tests/current.py\n"
        "pytest \"tests/direct.py\"\n"
        "pytest \\\n"
        "  tests/root.py::test_transitive\n"
        "pytest tests/unowned-baseline.py\n"
        "pytest prefix/tests/successor.py\n"
        "pytest tests/successor.py.suffix\n"
        "pytest tests/successor.pyextra\n"
        "```\n\n"
        "Prose-only example: pytest tests/successor.py must not be treated as a shell gate.\n\n"
        "```python\n"
        "run('pytest tests/successor.py')\n"
        "```\n\n"
        "```console\n"
        "pytest tests/successor.py\n"
        "```\n"
    )
    for section in manifest:
        gates = allowed_and_near_miss_gates if section == "section-03-current" else ""
        (sections / f"{section}.md").write_text(section_text(section, gates))
    write_required_plan_artifacts(planning)

    def lint_findings() -> tuple[subprocess.CompletedProcess[str], list[dict]]:
        result = run_raw("lint-sections", "--planning-dir", str(planning), "--depth", "fast")
        payload = json.loads(result.stdout)
        return result, payload["findings"]

    def gate_findings() -> tuple[subprocess.CompletedProcess[str], list[dict]]:
        result, findings = lint_findings()
        return result, [
            item
            for item in findings
            if item["code"] == "section-gate-non-predecessor-owned-path"
        ]

    _, passing_findings = gate_findings()
    assert passing_findings == []

    violating_gates = (
        "\n\n## Non-predecessor gates\n\n"
        "```bash\n"
        "pytest tests/successor.py::test_owned_node\n"
        "```\n\n"
        "```sh\n"
        "pytest 'tests/quoted.py'\n"
        "```\n\n"
        "```shell\n"
        "pytest \\\n"
        "  tests/continued.py\n"
        "```\n\n"
        "```zsh\n"
        "pytest tests/parallel.py\n"
        "```\n"
    )
    current_path = sections / "section-03-current.md"
    current_path.write_text(section_text("section-03-current", allowed_and_near_miss_gates + violating_gates))

    absent_result, absent_findings = gate_findings()
    expected_paths = {
        "tests/continued.py",
        "tests/parallel.py",
        "tests/quoted.py",
        "tests/successor.py",
    }
    assert absent_result.returncode == 1
    assert len(absent_findings) == len(expected_paths)
    assert {item["severity"] for item in absent_findings} == {"high"}
    for path in expected_paths:
        assert sum(path in item["message"] for item in absent_findings) == 1

    for relative_path in expected_paths:
        target = planning / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# pre-existing target\n")
    existing_result, existing_findings = gate_findings()
    assert existing_result.returncode == 1
    assert existing_findings == absent_findings

    unclosed_gate = (
        "\n\n## Unclosed CommonMark shell fence\n\n"
        "```bash\n"
        "pytest tests/successor.py::test_unclosed_fence\n"
    )
    current_path.write_text(section_text("section-03-current", unclosed_gate))
    unclosed_result, unclosed_findings = lint_findings()
    assert unclosed_result.returncode == 1
    assert [item["code"] for item in unclosed_findings].count("malformed-shell-gate") == 1
    unclosed_gate_findings = [
        item
        for item in unclosed_findings
        if item["code"] == "section-gate-non-predecessor-owned-path"
    ]
    assert len(unclosed_gate_findings) == 1
    assert unclosed_gate_findings[0]["severity"] == "high"
    assert "tests/successor.py" in unclosed_gate_findings[0]["message"]

    unmatched_quote_gates = (
        "\n\n## Malformed shell gates\n\n"
        "```bash\n"
        "pytest tests/successor.py \"unterminated\n"
        "```\n\n"
        "```zsh\n"
        "pytest \"tests/quoted.py\n"
        "```\n"
    )
    current_path.write_text(section_text("section-03-current", unmatched_quote_gates))
    unmatched_result, unmatched_findings = lint_findings()
    assert unmatched_result.returncode == 1
    unmatched_codes = [item["code"] for item in unmatched_findings]
    assert unmatched_codes.count("malformed-shell-gate") == 1
    recovered_gate_findings = [
        item
        for item in unmatched_findings
        if item["code"] == "section-gate-non-predecessor-owned-path"
    ]
    assert len(recovered_gate_findings) == 2
    assert {item["severity"] for item in recovered_gate_findings} == {"high"}
    for path in ("tests/quoted.py", "tests/successor.py"):
        assert sum(path in item["message"] for item in recovered_gate_findings) == 1

    valid_heredoc_gates = (
        "\n\n## Literal heredoc bodies\n\n"
        "```bash\n"
        "cat <<'FIRST' <<-\"SECOND\"\n"
        "'\n"
        "tests/successor.py\n"
        "FIRST\n"
        "\t\"\n"
        "\ttests/parallel.py\n"
        "\tSECOND\n"
        "cat <<EOF-DASH\n"
        "'\n"
        "tests/successor.py\n"
        "EOF-DASH\n"
        "cat <<-EOF-DASH\n"
        "\t\"\n"
        "\ttests/parallel.py\n"
        "\tEOF-DASH\n"
        "pytest tests/current.py\n"
        "```\n"
    )
    current_path.write_text(section_text("section-03-current", valid_heredoc_gates))
    valid_heredoc_result, valid_heredoc_findings = lint_findings()
    assert "malformed-shell-gate" not in {item["code"] for item in valid_heredoc_findings}
    assert not [
        item
        for item in valid_heredoc_findings
        if item["code"] == "section-gate-non-predecessor-owned-path"
    ]

    surrounding_gate_paths = (
        "\n\n## Executed paths around a heredoc\n\n"
        "```sh\n"
        "pytest tests/successor.py\n"
        "cat <<BODY\n"
        "tests/unowned-baseline.py\n"
        "BODY\n"
        "pytest tests/parallel.py\n"
        "```\n"
    )
    current_path.write_text(section_text("section-03-current", surrounding_gate_paths))
    surrounding_result, surrounding_findings = lint_findings()
    assert surrounding_result.returncode == 1
    assert "malformed-shell-gate" not in {item["code"] for item in surrounding_findings}
    surrounding_owned_findings = [
        item
        for item in surrounding_findings
        if item["code"] == "section-gate-non-predecessor-owned-path"
    ]
    assert len(surrounding_owned_findings) == 2
    for path in ("tests/parallel.py", "tests/successor.py"):
        assert sum(path in item["message"] for item in surrounding_owned_findings) == 1

    unterminated_heredoc = (
        "\n\n## Unterminated heredoc\n\n"
        "```zsh\n"
        "cat <<'NEVER_CLOSES'\n"
        "tests/successor.py\n"
        "```\n"
    )
    current_path.write_text(section_text("section-03-current", unterminated_heredoc))
    unterminated_result, unterminated_findings = lint_findings()
    assert unterminated_result.returncode == 1
    assert [item["code"] for item in unterminated_findings].count("malformed-shell-gate") == 1
    assert not [
        item
        for item in unterminated_findings
        if item["code"] == "section-gate-non-predecessor-owned-path"
    ]

    dynamic_heredoc = (
        "\n\n## Dynamic heredoc delimiter\n\n"
        "```shell\n"
        "cat <<$(delimiter)\n"
        "tests/successor.py\n"
        "```\n"
    )
    current_path.write_text(section_text("section-03-current", dynamic_heredoc))
    dynamic_result, dynamic_findings = lint_findings()
    assert dynamic_result.returncode == 1
    assert [item["code"] for item in dynamic_findings].count("malformed-shell-gate") == 1
    assert not [
        item
        for item in dynamic_findings
        if item["code"] == "section-gate-non-predecessor-owned-path"
    ]


def test_typescript_fixture_and_invalid_fixture_snapshots() -> None:
    planning = ROOT / "examples" / "typescript-app" / "01-auth"
    assert run_cmd("lint-plan", "--planning-dir", str(planning), "--depth", "standard", "--strict")["score"] == 100
    assert run_cmd("lint-sections", "--planning-dir", str(planning), "--depth", "standard", "--strict")["score"] == 100
    assert run_cmd("traceability", "--planning-dir", str(planning), "--strict")["score"] == 100
    assert run_cmd("forge-score", "--planning-dir", str(planning), "--depth", "standard", "--strict")["forge_score"] == 100

    saas_planning = ROOT / "examples" / "saas" / "01-authentication"
    assert run_cmd("lint-plan", "--planning-dir", str(saas_planning), "--depth", "standard", "--strict")["score"] == 100
    assert run_cmd("lint-sections", "--planning-dir", str(saas_planning), "--depth", "standard", "--strict")["score"] == 100
    assert run_cmd("traceability", "--planning-dir", str(saas_planning), "--strict")["score"] == 100
    assert run_cmd("forge-score", "--planning-dir", str(saas_planning), "--depth", "standard", "--strict")["forge_score"] == 100

    missing_index = run_raw("lint-sections", "--planning-dir", str(ROOT / "examples" / "invalid" / "missing-section-index"))
    assert missing_index.returncode != 0
    assert json.loads(missing_index.stdout)["findings"][0]["code"] == "missing-section-index"

    vague = run_raw("lint-sections", "--planning-dir", str(ROOT / "examples" / "invalid" / "vague-section"))
    assert vague.returncode != 0
    codes = {item["code"] for item in json.loads(vague.stdout)["findings"]}
    assert "vague-section-name" in codes


def test_readme_documents_operator_quality_commands() -> None:
    readme = (ROOT / "README.md").read_text().lower()

    for phrase in (
        "commands --pretty",
        "commands --phase plan",
        "plan-aware status",
        "plan_artifacts",
        "expanded codebase evidence",
        "source files",
        "eval-suite --examples-dir examples --check-snapshots",
        "update-snapshots",
        "release-check --plugin-root .",
        "update-check",
        "self-update",
        "does not poll git remotes automatically",
    ):
        assert phrase in readme


def test_validate_workflow_mentions_snapshot_eval() -> None:
    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text()
    assert "eval-suite --examples-dir examples --check-snapshots" in workflow


def test_skill_files_are_codex_native() -> None:
    banned = ["TaskList", "TaskUpdate", "AskUserQuestion", "CLAUDE_CODE_TASK_LIST_ID"]
    for skill in (ROOT / "skills").glob("*/SKILL.md"):
        content = skill.read_text()
        assert "[TODO:" not in content
        for token in banned:
            assert token not in content, f"{token} leaked into {skill}"

    plan_skill = (ROOT / "skills" / "zagrosi-plan" / "SKILL.md").read_text().lower()
    assert "source files" in plan_skill
    assert "plan artifact" in plan_skill
    assert "lint-plan-artifacts" in plan_skill
    assert "capability-inventory" in plan_skill
    assert "review-capabilities" in plan_skill
    assert "planning-consistency" in plan_skill
    assert "ask relevant questions" in plan_skill
    assert "targeted research" in plan_skill
    assert "(recommended)" in plan_skill

    implement_skill = (ROOT / "skills" / "zagrosi-implement" / "SKILL.md").read_text().lower()
    assert "consolidated commit" in implement_skill
    assert "section commits" in implement_skill
    assert "lint-plan-artifacts" in implement_skill
    assert "--review-artifact" in implement_skill
    assert "--verification" in implement_skill
    assert "review decisions" in implement_skill
    assert "refresh" in implement_skill
    assert "ci watch" in implement_skill

    project_skill = (ROOT / "skills" / "zagrosi-project" / "SKILL.md").read_text().lower()
    assert "workflow-options" in project_skill
    assert "capability-inventory" in project_skill
    assert "structured" in project_skill
    assert "chat" in project_skill
    assert "(recommended)" in project_skill
    assert "consistency review" in project_skill
