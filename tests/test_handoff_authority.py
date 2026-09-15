from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from detached_test_support import (
    canonical_json_bytes_for_test,
    domain_sha256_for_test,
    handoff_receipt_for_test,
)
from forge_test_helpers import (
    load_zagrosi_module,
)


@pytest.mark.parametrize("section_token", ("S26", "S28"))
def test_protected_source_observation_uses_exact_git_and_raw_source_contract(
    tmp_path: Path,
    monkeypatch,
    section_token: str,
) -> None:
    module = load_zagrosi_module()
    contract = module.detached_contract.HANDOFF_SECTION_CONTRACTS[section_token]
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

    monkeypatch.setattr(module.processes, "run_bounded_child", fake_child)
    target_fd = os.open(target, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        observed = module.handoff_host.derive_protected_source_observation(target_fd, contract)
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
    assert observed == module.handoff_host.ProtectedSourceObservation(
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
            [module.detached_contract.HANDOFF_GIT, "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            10.0,
            1,
            64 * 1024,
            module.detached_contract.HANDOFF_GIT_ENV,
        ),
        (
            [module.detached_contract.HANDOFF_GIT, "rev-parse", "--verify", "HEAD^{commit}"],
            10.0,
            41,
            64 * 1024,
            module.detached_contract.HANDOFF_GIT_ENV,
        ),
        (
            [module.detached_contract.HANDOFF_GIT, "ls-tree", "-r", "-z", "--full-tree", "HEAD"],
            30.0,
            16_777_216,
            64 * 1024,
            module.detached_contract.HANDOFF_GIT_ENV,
        ),
    ]
    assert module.detached_contract.HANDOFF_GIT_ENV == {
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
    monkeypatch.setattr(module.handoff_host, "run_protected_source_probe", lambda *args, **kwargs: next(frames))
    target_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(module.models.DetachedImplementationError) as caught:
            module.handoff_host.derive_protected_source_observation(
                target_fd,
                module.detached_contract.HANDOFF_SECTION_CONTRACTS["S26"],
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
        module.processes,
        "run_bounded_child",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            module.models.DetachedImplementationError(child_code, "value-bearing private detail")
        ),
    )
    target_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(module.models.DetachedImplementationError) as caught:
            module.handoff_host.run_protected_source_probe(
                target_fd,
                [module.detached_contract.HANDOFF_GIT, "status", "--porcelain=v1", "-z", "--untracked-files=all"],
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
    contract = module.detached_contract.HANDOFF_SECTION_CONTRACTS[section_token]
    target = tmp_path / "target"
    source = target / contract["runner_source"]
    source.parent.mkdir(parents=True)
    admitted = b"root-runner-complete-bytes-v1\n"
    source.write_bytes(admitted)
    target_fd = os.open(target, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        module.handoff_host.require_gate_runner_matches_source(contract, target_fd, admitted)
        same_length_mutant = admitted[:-2] + bytes([admitted[-2] ^ 1]) + admitted[-1:]
        with pytest.raises(module.models.DetachedImplementationError) as stale:
            module.handoff_host.require_gate_runner_matches_source(contract, target_fd, same_length_mutant)
        assert stale.value.code == "handoff-runner-source-drift"
        hardlink = source.with_name(source.name + ".hardlink")
        os.link(source, hardlink)
        with pytest.raises(module.models.DetachedImplementationError, match="single-link"):
            module.handoff_host.require_gate_runner_matches_source(contract, target_fd, admitted)
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
        contract = module.detached_contract.HANDOFF_SECTION_CONTRACTS[token]
        root_argv = module.handoff_wire.handoff_root_argv(contract)
        verifier_argv = module.handoff_wire.handoff_verifier_argv(contract)
        assert root_argv[6] == runner
        assert verifier_argv[3] == runner
        assert contract["test"] not in root_argv + verifier_argv
        assert contract["gate_command_sha256"] == gate_hash
        assert contract["command_sha256"] == root_hash
        assert contract["verifier_command_sha256"] == verifier_hash
        module.handoff_wire.verify_handoff_command_identities(contract)
        mutable_repo_mutant = dict(contract)
        mutable_repo_mutant["runner"] = contract["test"]
        mutable_repo_mutant["command_sha256"] = module.handoff_wire.framed_command_sha256(
            b"unit12-privileged-gate-handoff-command-v1\0",
            module.handoff_wire.handoff_root_argv(mutable_repo_mutant),
        )
        mutable_repo_mutant["verifier_command_sha256"] = module.handoff_wire.framed_command_sha256(
            b"unit12-privileged-gate-handoff-verifier-command-v1\0",
            module.handoff_wire.handoff_verifier_argv(mutable_repo_mutant),
        )
        with pytest.raises(module.models.DetachedImplementationError, match="runner selection"):
            module.handoff_wire.verify_handoff_command_identities(mutable_repo_mutant)


@pytest.mark.parametrize("section_token", ("S26", "S28"))
def test_handoff_receipt_must_echo_selected_exact_gate_command_digest(section_token: str) -> None:
    module = load_zagrosi_module()
    contract = module.detached_contract.HANDOFF_SECTION_CONTRACTS[section_token]
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
    _, request_raw, request_final = module.handoff_wire.build_handoff_request(config, contract)
    receipt = json.loads(handoff_receipt_for_test(module, config, contract, request_raw))
    receipt["gate_command_sha256"] = "sha256:" + "24" * 32
    with pytest.raises(module.models.DetachedImplementationError) as caught:
        module.handoff_wire.parse_handoff_receipt(
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
    contract = module.detached_contract.HANDOFF_SECTION_CONTRACTS["S26"]
    runner_parent = tmp_path / "root-owned-runner-parent"
    runner_parent.mkdir()
    runner = runner_parent / Path(contract["runner"]).name
    raw = b"fixed-runner-source\n"
    runner.write_bytes(raw)
    runner.chmod(0o555)
    real_fstat = os.fstat

    def fake_parent(path):
        return os.open(runner_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))

    def fake_fstat(file_fd):
        observed = real_fstat(file_fd)
        if observed.st_ino != runner.stat().st_ino:
            return observed
        return SimpleNamespace(
            st_dev=observed.st_dev,
            st_ino=observed.st_ino,
            st_mode=stat.S_IFREG | mode,
            st_nlink=link_count,
            st_uid=uid,
            st_gid=gid,
            st_size=observed.st_size,
            st_mtime_ns=observed.st_mtime_ns,
            st_ctime_ns=observed.st_ctime_ns,
        )

    monkeypatch.setattr(module.handoff_host, "open_root_owned_nonwritable_directory_chain", fake_parent)
    monkeypatch.setattr(os, "fstat", fake_fstat)
    if accepted:
        assert module.handoff_host.read_fixed_gate_runner(contract) == raw
    else:
        with pytest.raises(module.models.DetachedImplementationError) as caught:
            module.handoff_host.read_fixed_gate_runner(contract)
        assert caught.value.code == "unsafe-handoff-dependency"

    runner.unlink()
    runner.symlink_to(tmp_path / "mutable-repository-runner.py")
    with pytest.raises(module.models.DetachedImplementationError) as symbolic:
        module.handoff_host.read_fixed_gate_runner(contract)
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
    real_fstat = os.fstat

    monkeypatch.setattr(
        module.handoff_host,
        "open_root_owned_nonwritable_directory_chain",
        lambda path: os.open(dependency_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)),
    )

    def fake_fstat(file_fd):
        real_fstat(file_fd)
        return SimpleNamespace(
            st_mode=stat.S_IFREG | mode,
            st_nlink=link_count,
            st_uid=uid,
            st_gid=gid,
        )

    monkeypatch.setattr(os, "fstat", fake_fstat)
    if accepted:
        module.handoff_host.require_fixed_handoff_executable("/usr/bin/stat", allow_multiple_links=True)
    else:
        with pytest.raises(module.models.DetachedImplementationError) as caught:
            module.handoff_host.require_fixed_handoff_executable("/usr/bin/stat", allow_multiple_links=True)
        assert caught.value.code == "unsafe-handoff-dependency"


def test_fixed_executable_dependency_rejects_missing_and_symbolic_link(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_zagrosi_module()
    dependency_parent = tmp_path / "fixed-bin"
    dependency_parent.mkdir()
    monkeypatch.setattr(
        module.handoff_host,
        "open_root_owned_nonwritable_directory_chain",
        lambda path: os.open(dependency_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)),
    )
    with pytest.raises(module.models.DetachedImplementationError) as missing:
        module.handoff_host.require_fixed_handoff_executable("/usr/bin/stat", allow_multiple_links=True)
    assert missing.value.code == "missing-handoff-dependency"

    (dependency_parent / "real-stat").write_bytes(b"replacement")
    (dependency_parent / "stat").symlink_to(dependency_parent / "real-stat")
    with pytest.raises(module.models.DetachedImplementationError) as symbolic:
        module.handoff_host.require_fixed_handoff_executable("/usr/bin/stat", allow_multiple_links=True)
    assert symbolic.value.code == "unsafe-handoff-dependency"


def test_handoff_platform_rejects_fixed_stat_path_mutation_before_spawn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_zagrosi_module()
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    monkeypatch.setattr(module.handoff_host.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module.handoff_host.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(module.detached_contract, "HANDOFF_STAT", "/tmp/mutable-stat")
    child_calls = 0

    def forbidden_child(*args, **kwargs):
        nonlocal child_calls
        child_calls += 1
        raise AssertionError("mutated stat path must fail before spawn")

    monkeypatch.setattr(module.processes, "run_bounded_child", forbidden_child)
    root_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(module.models.DetachedImplementationError) as caught:
            module.handoff_host.require_handoff_platform(root_fd)
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
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    monkeypatch.setattr(module.handoff_host.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module.handoff_host.platform, "machine", lambda: "arm64")
    executable_checks: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        module.handoff_host,
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
        assert module.detached_contract.HANDOFF_ENV == {"LC_ALL": "C", "LANG": "C", "TZ": "UTC"}
        return return_code, stdout, stderr

    monkeypatch.setattr(module.processes, "run_bounded_child", fake_child)
    root_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        if accepted:
            module.handoff_host.require_handoff_platform(root_fd)
        else:
            with pytest.raises(module.models.DetachedImplementationError) as caught:
                module.handoff_host.require_handoff_platform(root_fd)
            assert caught.value.code == "unsupported-handoff-platform"
    finally:
        os.close(root_fd)
    assert executable_checks == [("/usr/bin/stat", True)]
