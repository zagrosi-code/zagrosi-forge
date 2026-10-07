"""Remote coordination admits one writer without disturbing application work."""
import os
import shlex
import subprocess
from types import SimpleNamespace

import pytest

from forge_test_helpers import load_zagrosi_module


def git(path, *args, input=None, check=True):
    env = {**os.environ, "GIT_AUTHOR_NAME": "Engineer", "GIT_AUTHOR_EMAIL": "test@example.invalid",
           "GIT_COMMITTER_NAME": "Engineer", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
    return subprocess.run(["git", "-C", str(path), *args], input=input, text=True,
                          capture_output=True, check=check, env=env, timeout=15)


@pytest.fixture(params=["sha1", "sha256"])
def repositories(tmp_path, request):
    remote = tmp_path / "remote.git"
    result = git(tmp_path, "init", "--bare", f"--object-format={request.param}", str(remote), check=False)
    if result.returncode:
        pytest.skip(f"Git does not support {request.param}")
    git(remote, "config", "receive.denyNonFastForwards", "true")
    roots = []
    for name in ("alice", "bob"):
        root = tmp_path / name
        git(tmp_path, "init", f"--object-format={request.param}", str(root))
        git(root, "config", "user.name", "Engineer")
        git(root, "config", "user.email", "test@example.invalid")
        git(root, "remote", "add", "origin", str(remote))
        (root / "code.txt").write_text("original\n")
        git(root, "add", "code.txt")
        git(root, "commit", "-m", "Initial")
        roots.append(root)
    return remote, roots


def client(forge, path):
    return forge.team_git.GitBoard(forge.team_git.Repository.discover(path))


def board():
    return {"version": 1, "board_id": "a" * 32, "sessions": {}}


def test_cas_creation_and_stale_update_preserve_remote(repositories):
    _, (alice, bob) = repositories
    forge = load_zagrosi_module()
    first, second = client(forge, alice), client(forge, bob)
    receipts = []
    created = first.write(board(), None, receipts.append)
    assert receipts[-1]["revision"] == created.revision
    assert second.read().revision == created.revision
    with pytest.raises(forge.team_state.TeamError, match="changed") as failed:
        second.write(board(), None, receipts.append)
    assert failed.value.code == "team-contention"
    updated = first.write(board(), created.revision, receipts.append, message="Heartbeat")
    with pytest.raises(forge.team_state.TeamError) as failed:
        second.write(board(), created.revision, receipts.append, message="Stale heartbeat")
    assert failed.value.code == "team-contention"
    assert second.read().revision == updated.revision


def test_metadata_preserves_dirty_index_head_fetch_and_refs(repositories):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    repo = forge.team_git.Repository.discover(alice)
    (alice / "code.txt").write_text("staged\n")
    git(alice, "add", "code.txt")
    (alice / "code.txt").write_text("unstaged\n")
    (alice / "private.txt").write_text("private\n")
    (repo.git_dir / "FETCH_HEAD").write_text("preserve fetch\n")
    original = {name: (repo.git_dir / name).read_bytes() for name in ("index", "HEAD", "FETCH_HEAD")}
    status = git(alice, "status", "--porcelain=v1").stdout
    git(alice, "config", "remote.origin.mirror", "true")
    git(alice, "config", "fetch.prune", "true")
    git(alice, "config", "fetch.pruneTags", "true")
    git(alice, "config", "push.followTags", "true")
    transport = client(forge, alice)
    result = transport.write(board(), None, lambda attempt: None)
    assert transport.read().revision == result.revision
    assert original == {name: (repo.git_dir / name).read_bytes() for name in original}
    assert git(alice, "status", "--porcelain=v1").stdout == status
    assert not git(alice, "for-each-ref", "refs/forge", "refs/remotes").stdout
    assert (alice / "code.txt").read_text() == "unstaged\n"
    assert (alice / "private.txt").read_text() == "private\n"


def test_transport_distinguishes_missing_offline_and_replaced_board(repositories):
    remote, (alice, _) = repositories
    forge = load_zagrosi_module()
    transport = client(forge, alice)
    assert transport.read(allow_missing=True).board is None
    with pytest.raises(forge.team_state.TeamError) as failed:
        transport.read()
    assert failed.value.code == "team-board-missing"
    transport.write(board(), None, lambda attempt: None)
    with pytest.raises(forge.team_state.TeamError) as failed:
        transport.read(board_id="b" * 32)
    assert failed.value.code == "team-board-replaced"
    remote.rename(remote.with_name("offline.git"))
    with pytest.raises(forge.team_state.TeamError) as failed:
        transport.read(allow_missing=True)
    assert failed.value.code == "team-unavailable"


def test_pin_rejects_destination_change_and_split_pushurls(repositories):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    pinned = client(forge, alice).pin()
    git(alice, "config", "remote.origin.pushurl", "https://secret:token@example.invalid/repo")
    with pytest.raises(forge.team_state.TeamError) as failed:
        forge.team_git.GitBoard(forge.team_git.Repository.discover(alice), pin=pinned).read()
    assert "secret" not in str(failed.value) and "token" not in str(failed.value)
    assert failed.value.code == "team-destination-changed"


@pytest.mark.parametrize("push_mode", ["url", "pushInsteadOf", "explicit-pushurl"])
def test_publication_preserves_single_raw_url_rewrite(repositories, push_mode):
    remote, (alice, _) = repositories
    forge = load_zagrosi_module()
    wrong = remote.parent / "wrong.git"
    git(alice, "remote", "set-url", "origin", "forge:board")
    git(alice, "config", f"url.{remote}.insteadOf", "forge:board")
    git(alice, "config", f"url.{wrong}.insteadOf", str(remote))
    if push_mode == "pushInsteadOf":
        git(alice, "config", f"url.{remote}.pushInsteadOf", "forge:board")
    elif push_mode == "explicit-pushurl":
        git(alice, "config", "remote.origin.pushurl", "forge:publish")
        git(alice, "config", "--add", f"url.{remote}.insteadOf", "forge:publish")
        git(alice, "config", f"url.{wrong}.pushInsteadOf", "forge:board")
    assert git(alice, "remote", "get-url", "origin").stdout.strip() == str(remote)
    assert git(alice, "remote", "get-url", "--push", "origin").stdout.strip() == str(remote)
    transport = client(forge, alice)
    created = transport.write(board(), None, lambda attempt: None)
    assert transport.read().revision == created.revision
    assert git(remote, "rev-parse", "refs/heads/forge/team").stdout.strip() == created.revision
    assert git(alice, "config", "remote.origin.url").stdout.strip() == "forge:board"
    if push_mode == "explicit-pushurl":
        assert git(alice, "config", "remote.origin.pushurl").stdout.strip() == "forge:publish"
    assert not git(alice, "for-each-ref", "refs/remotes").stdout
    assert not wrong.exists()


def test_publication_preserves_multiline_receivepack(repositories):
    remote, (alice, _) = repositories
    if os.name == "nt":
        pytest.skip("POSIX shell receivepack fixture")
    forge = load_zagrosi_module()
    invoked = alice.parent / "receivepack-invoked"
    receivepack = f"printf '%s\\n' complete > {shlex.quote(str(invoked))}\nexec git-receive-pack"
    git(alice, "config", "remote.origin.receivepack", receivepack)
    transport = client(forge, alice)
    created = transport.write(board(), None, lambda attempt: None)
    assert invoked.read_text() == "complete\n"
    assert git(remote, "rev-parse", "refs/heads/forge/team").stdout.strip() == created.revision
    assert transport.read().revision == created.revision
    assert git(alice, "config", "--null", "--get-all", "remote.origin.receivepack").stdout == receivepack + "\0"


def test_unknown_push_success_reconciles_attempt_ancestry(repositories, monkeypatch):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    transport = client(forge, alice)
    execute = forge.team_git.execute
    def lost_response(argv, *args, **kwargs):
        result = execute(argv, *args, **kwargs)
        if "push" in argv and result["returncode"] == 0:
            return {**result, "returncode": 124, "timed_out": True}
        return result
    monkeypatch.setattr(forge.team_git, "execute", lost_response)
    attempts = []
    result = transport.write(board(), None, attempts.append)
    assert result.revision == attempts[0]["revision"]
    assert transport.read().revision == result.revision


def test_pending_receipt_is_durable_before_push(repositories):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    transport = client(forge, alice)
    def cannot_persist(attempt):
        raise OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        transport.write(board(), None, cannot_persist)
    assert transport.read(allow_missing=True).revision is None


def test_retry_reuses_unknown_operation_and_recovers_after_new_commits(repositories, monkeypatch):
    _, (alice, bob) = repositories
    forge = load_zagrosi_module()
    transport = client(forge, alice)
    execute = forge.team_git.execute
    def lost_request(argv, *args, **kwargs):
        if "push" in argv:
            return {"returncode": 124, "stdout": "", "stderr": "secret", "timed_out": True}
        return execute(argv, *args, **kwargs)
    monkeypatch.setattr(forge.team_git, "execute", lost_request)
    attempts = []
    with pytest.raises(forge.team_state.TeamError) as failed:
        transport.write(board(), None, attempts.append)
    assert failed.value.code == "team-write-unknown"
    assert transport.reconcile(attempts[0]) is None
    monkeypatch.setattr(forge.team_git, "execute", execute)
    retried = transport.retry(attempts[0])
    assert retried.revision == attempts[0]["revision"]
    peer = client(forge, bob)
    observed = peer.read()
    latest = peer.write(board(), observed.revision, lambda attempt: None)
    assert transport.reconcile(attempts[0]).revision == latest.revision
    assert transport.retry(attempts[0]).revision == latest.revision


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
def test_pending_commit_survives_gc_and_retries_exact_revision(repositories, monkeypatch, existing):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    transport = client(forge, alice)
    expected = transport.write(board(), None, lambda attempt: None).revision if existing else None
    unrelated = "refs/forge/team-pending/unrelated"
    head = git(alice, "rev-parse", "HEAD").stdout.strip()
    git(alice, "update-ref", unrelated, head)
    execute = forge.team_git.execute
    def lost_request(argv, *args, **kwargs):
        if "push" in argv:
            return {"returncode": 124, "stdout": "", "stderr": "", "timed_out": True}
        return execute(argv, *args, **kwargs)
    monkeypatch.setattr(forge.team_git, "execute", lost_request)
    attempts = []
    with pytest.raises(forge.team_state.TeamError) as failed:
        transport.write(board(), expected, attempts.append)
    assert failed.value.code == "team-write-unknown"
    assert transport.reconcile(attempts[0]) is None
    git(alice, "gc", "--prune=now")
    retained = git(alice, "cat-file", "-t", attempts[0]["revision"], check=False)
    assert retained.returncode == 0 and retained.stdout.strip() == "commit", retained.stderr
    monkeypatch.setattr(forge.team_git, "execute", execute)
    assert transport.retry(attempts[0]).revision == attempts[0]["revision"]
    assert transport.read().revision == attempts[0]["revision"]
    assert git(alice, "for-each-ref", "--format=%(refname) %(objectname)",
               "refs/forge/team-pending").stdout.splitlines() == [f"{unrelated} {head}"]


def test_pending_commit_survives_gc_during_receipt_save_failure(repositories):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    transport = client(forge, alice)
    attempts = []
    def persisted_but_failed(attempt):
        attempts.append(attempt)
        git(alice, "gc", "--prune=now")
        raise OSError("receipt saved but directory sync failed")
    with pytest.raises(OSError, match="directory sync failed"):
        transport.write(board(), None, persisted_but_failed)
    retained = git(alice, "cat-file", "-t", attempts[0]["revision"], check=False)
    assert retained.returncode == 0 and retained.stdout.strip() == "commit", retained.stderr
    assert transport.read(allow_missing=True).revision is None
    assert transport.retry(attempts[0]).revision == attempts[0]["revision"]
    assert not git(alice, "for-each-ref", "refs/forge/team-pending").stdout


def test_contended_pending_commit_survives_failed_receipt_clear(repositories, monkeypatch):
    _, (alice, bob) = repositories
    forge = load_zagrosi_module()
    transport = client(forge, alice)
    execute = forge.team_git.execute
    def lost_request(argv, *args, **kwargs):
        if "push" in argv:
            return {"returncode": 124, "stdout": "", "stderr": "", "timed_out": True}
        return execute(argv, *args, **kwargs)
    monkeypatch.setattr(forge.team_git, "execute", lost_request)
    attempts = []
    with pytest.raises(forge.team_state.TeamError) as failed:
        transport.write(board(), None, attempts.append)
    assert failed.value.code == "team-write-unknown"
    monkeypatch.setattr(forge.team_git, "execute", execute)
    client(forge, bob).write(board(), None, lambda attempt: None)
    def cannot_clear(state):
        raise OSError("receipt clear failed")
    context = {"transport": transport, "checkout": {"pending": attempts[0]}, "state": {},
               "local": SimpleNamespace(save=cannot_clear)}
    with pytest.raises(OSError, match="receipt clear failed"):
        forge.team.retry(context)
    # Reload the receipt that remains durable after the failed clear.
    context["checkout"]["pending"] = attempts[0]
    context["local"].save = lambda state: None
    git(alice, "gc", "--prune=now")
    with pytest.raises(forge.team_state.TeamError) as failed:
        forge.team.retry(context)
    assert failed.value.code == "team-contention"
    assert context["checkout"]["pending"] is None
    assert not git(alice, "for-each-ref", "refs/forge/team-pending").stdout


@pytest.mark.parametrize("symbolic", [False, True], ids=["changed-target", "symbolic-target"])
def test_pending_cleanup_preserves_ref_changed_after_receipt(repositories, symbolic):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    transport = client(forge, alice)
    head = git(alice, "rev-parse", "HEAD").stdout.strip()
    branch = git(alice, "symbolic-ref", "HEAD").stdout.strip()
    refs = []
    def changed_reference(attempt):
        reference = f"refs/forge/team-pending/{attempt['revision']}"
        refs.append(reference)
        assert git(alice, "rev-parse", "--verify", reference).stdout.strip() == attempt["revision"]
        if symbolic:
            git(alice, "symbolic-ref", reference, branch)
        else:
            git(alice, "update-ref", reference, head, attempt["revision"])
    published = transport.write(board(), None, changed_reference)
    assert transport.read().revision == published.revision
    assert git(alice, "rev-parse", "--verify", refs[0]).stdout.strip() == head
    assert git(alice, "rev-parse", "--verify", branch).stdout.strip() == head
    if symbolic:
        assert git(alice, "symbolic-ref", refs[0]).stdout.strip() == branch


def test_unknown_attempt_is_fenced_when_another_creator_wins(repositories, monkeypatch):
    _, (alice, bob) = repositories
    forge = load_zagrosi_module()
    transport = client(forge, alice)
    execute = forge.team_git.execute
    def lost_request(argv, *args, **kwargs):
        if "push" in argv:
            return {"returncode": 124, "stdout": "", "stderr": "", "timed_out": True}
        return execute(argv, *args, **kwargs)
    monkeypatch.setattr(forge.team_git, "execute", lost_request)
    attempts = []
    with pytest.raises(forge.team_state.TeamError):
        transport.write(board(), None, attempts.append)
    monkeypatch.setattr(forge.team_git, "execute", execute)
    winner = client(forge, bob).write(board(), None, lambda attempt: None)
    with pytest.raises(forge.team_state.TeamError) as failed:
        transport.retry(attempts[0])
    assert failed.value.code == "team-contention"
    assert transport.read().revision == winner.revision


def test_connection_loss_without_timeout_retains_unknown_delivery(repositories, monkeypatch):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    transport = client(forge, alice)
    execute = forge.team_git.execute
    def disconnected(argv, *args, **kwargs):
        if "push" in argv:
            return {"returncode": 128, "stdout": "", "stderr": "connection reset", "timed_out": False}
        return execute(argv, *args, **kwargs)
    monkeypatch.setattr(forge.team_git, "execute", disconnected)
    attempts = []
    with pytest.raises(forge.team_state.TeamError) as failed:
        transport.write(board(), None, attempts.append)
    assert failed.value.code == "team-write-unknown"
    assert attempts and transport.reconcile(attempts[0]) is None


def test_user_push_policy_refusal_is_reported_without_raw_error(repositories):
    remote, (alice, _) = repositories
    if os.name == "nt":
        pytest.skip("POSIX executable hook fixture")
    hook = remote / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\necho secret-credential >&2\nexit 1\n")
    hook.chmod(0o755)
    forge = load_zagrosi_module()
    transport = client(forge, alice)
    with pytest.raises(forge.team_state.TeamError) as failed:
        transport.write(board(), None, lambda attempt: None)
    assert failed.value.code == "team-unavailable"
    assert "secret-credential" not in str(failed.value)
    assert transport.read(allow_missing=True).board is None


def test_persisted_pending_bytes_bound_accepted_required_state(repositories, monkeypatch):
    import json

    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    with forge.team.workspace(alice) as context:
        forge.team.onboard(context, 'init', 'origin', 'Alice')
    local = forge.team_config.LocalTeam(forge.team_git.Repository.discover(alice))
    before_push = []
    original = forge.team_git.GitBoard._push
    def mandatory_size(state):
        return len((json.dumps({**state, 'cache': None}, ensure_ascii=True, allow_nan=False,
                               sort_keys=True, separators=(',', ':')) + '\n').encode())
    def inspect_pending(transport, receipt):
        state = local.load()
        pending = state['checkouts'][local.key]['pending']
        assert pending['revision'] == receipt['revision']
        before_push.append((pending['action'], mandatory_size(state)))
        return original(transport, receipt)
    monkeypatch.setattr(forge.team_git.GitBoard, '_push', inspect_pending)
    with forge.team.workspace(alice) as context:
        forge.team._observe(context)
        row = None
        for action in ('start', 'update', 'recover', 'finish'):
            fields = ({'task': 'Byte-bound task', 'binding': 'a' * 64} if action == 'start' else
                      {'identity': row['id'], 'generation': row['generation']})
            if action == 'update':
                fields['binding'] = 'b' * 64
            if action == 'recover':
                fields.update(expect=context['snapshot'].revision, reason='Agreed recovery', binding='c' * 64)
            if action == 'finish':
                fields['note'] = 'Delivered'
            result = forge.team.mutate(context, action, **fields)
            saved = local.load()
            assert saved['checkouts'][local.key]['pending'] is None
            assert before_push[-1][0] == action
            assert mandatory_size(saved) <= before_push[-1][1]
            row = result.get('session')
    assert len(before_push) == 4
