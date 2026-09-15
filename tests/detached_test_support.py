from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

from forge_test_helpers import (
    DETACHED_CONTRACT_RELATIVE_PATH,
    IMPLEMENTATION_SOURCE_RELATIVE_PATHS,
    ROOT,
    run_script_raw,
    write_required_plan_artifacts,
    write_single_section_fixture,
)


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
    return module.handoff_host.ProtectedSourceObservation(
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
        "schema": module.detached_contract.HANDOFF_RECEIPT_SCHEMA,
        "purpose": module.detached_contract.HANDOFF_PURPOSE,
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
    assert set(receipt) == module.detached_contract.HANDOFF_RECEIPT_FIELDS
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
            "schema": module.detached_contract.HANDOFF_VERIFICATION_SCHEMA,
            "purpose": module.detached_contract.HANDOFF_VERIFICATION_PURPOSE,
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
    for relative in (*IMPLEMENTATION_SOURCE_RELATIVE_PATHS.values(), DETACHED_CONTRACT_RELATIVE_PATH):
        target = plugin_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    shutil.copytree(ROOT / "scripts/forge", plugin_root / "scripts/forge", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for source in (ROOT / "tests").glob("*.py"):
        shutil.copy2(source, plugin_root / "tests" / source.name)
    return plugin_root


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
