"""Clone-local opt-in and checkout identity preserve user-owned configuration."""
from copy import deepcopy
import json

import pytest

from forge_test_helpers import load_zagrosi_module
from test_team_git import board, git, repositories


def test_marker_is_explicit_strict_and_never_replaces_existing_content(repositories):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    local = forge.team_config.LocalTeam(forge.team_git.Repository.discover(alice))
    assert local.marker() is None
    local.write_marker("a" * 32)
    marker = local.marker()
    assert marker == {"version": 1, "board_id": "a" * 32, "ref": "refs/heads/forge/team"}
    local.write_marker("a" * 32)
    with pytest.raises(forge.team_state.TeamError):
        local.write_marker("b" * 32)
    path = alice / ".forge/team.json"
    before = path.read_bytes()
    assert local.marker_digest(marker) == local.marker_digest(json.loads(before))
    path.write_text('{"version":1,"version":2}')
    with pytest.raises(forge.team_state.TeamError):
        local.marker()


def test_state_survives_reopen_and_worktrees_have_separate_identity(repositories):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    local = forge.team_config.LocalTeam(forge.team_git.Repository.discover(alice))
    with local.locked():
        state = local.load()
        checkout = local.checkout(state)
        receipt = {"revision": "f" * 40, "expected": None, "board_id": "a" * 32,
                   "action": "start", "session_id": "b" * 32, "generation": "c" * 32, "binding": None}
        checkout["pending"] = receipt
        state["pending_init"] = {"board_id": "a" * 32, "name": "Alice",
                                 "pin": {"remote": "origin", "endpoint_digest": "e" * 64}}
        local.save(state)
    with local.locked():
        reopened = local.load()
        assert reopened == state
    worktree = alice.parent / "linked"
    git(alice, "worktree", "add", "-b", "linked", str(worktree))
    linked = forge.team_config.LocalTeam(forge.team_git.Repository.discover(worktree))
    with linked.locked():
        other = linked.load()
        assert other["participant_id"] == state["participant_id"]
        assert linked.checkout(other)["checkout_id"] != checkout["checkout_id"]
        linked.save(other)
    with local.locked():
        assert local.checkout(local.load())["pending"] == receipt


def test_marker_symlink_cannot_redirect_discovery_or_creation(repositories, tmp_path):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    local = forge.team_config.LocalTeam(forge.team_git.Repository.discover(alice))
    (alice / ".forge").mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("private")
    try:
        (alice / ".forge/team.json").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(forge.team_state.TeamError):
        local.marker()
    with pytest.raises(forge.team_state.TeamError):
        local.write_marker("a" * 32)
    assert outside.read_text() == "private"


def test_marker_rejects_boolean_schema_version(repositories):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    local = forge.team_config.LocalTeam(forge.team_git.Repository.discover(alice))
    local.write_marker("a" * 32)
    path = alice / ".forge/team.json"
    marker = json.loads(path.read_text())
    marker["version"] = True
    path.write_text(json.dumps(marker))
    before = path.read_bytes()
    with pytest.raises(forge.team_state.TeamError) as failed:
        local.marker()
    assert failed.value.code == "team-marker"
    assert path.read_bytes() == before


@pytest.mark.parametrize("malformed", ["boolean-version", "huge-timestamp", "list-action", "object-action"])
def test_malformed_local_state_is_rejected_without_replacing_evidence(repositories, malformed):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    local = forge.team_config.LocalTeam(forge.team_git.Repository.discover(alice))
    with local.locked():
        state = local.load()
        checkout = local.checkout(state)
        local.save(state)
        valid_bytes = local.path.read_bytes()
        if malformed == "boolean-version":
            state["version"] = True
        elif malformed == "huge-timestamp":
            state["cache"] = {"revision": "f" * 40, "board": board(), "observed_at": 10 ** 400}
        else:
            checkout["pending"] = {"revision": "f" * 40, "expected": None, "board_id": "a" * 32,
                                   "action": [] if malformed == "list-action" else {},
                                   "session_id": "b" * 32, "generation": "c" * 32, "binding": None}
        with pytest.raises(forge.team_state.TeamError) as failed:
            local.save(state)
        assert failed.value.code == "team-local-state"
        assert local.path.read_bytes() == valid_bytes
        local.path.write_text(json.dumps(state))
        invalid_bytes = local.path.read_bytes()
        with pytest.raises(forge.team_state.TeamError) as failed:
            local.load()
        assert failed.value.code == "team-local-state"
        assert local.path.read_bytes() == invalid_bytes


def test_full_checkout_registry_preserves_pending_recovery_before_admission(repositories):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    local = forge.team_config.LocalTeam(forge.team_git.Repository.discover(alice))
    with local.locked():
        state = local.load()
        for index in range(128):
            state['checkouts'][f'{index + 1:064x}'] = {
                'checkout_id': f'{index + 1:032x}', 'sessions': {}, 'bindings': {},
                'pending': {'revision': 'f' * 40, 'expected': None, 'board_id': 'a' * 32,
                            'action': 'start', 'session_id': f'{index + 1:032x}',
                            'generation': 'c' * 32, 'binding': None}}
        local.save(state)
        before, persisted = deepcopy(state), local.path.read_bytes()
        with pytest.raises(forge.team_state.TeamError) as failed:
            local.checkout(state)
        assert failed.value.code == 'team-local-capacity'
        assert state == before
        assert local.path.read_bytes() == persisted


def test_mandatory_byte_overflow_preserves_durable_state_and_required_records(repositories):
    _, (alice, _) = repositories
    forge = load_zagrosi_module()
    local = forge.team_config.LocalTeam(forge.team_git.Repository.discover(alice))
    with local.locked():
        state = local.load()
        local.save(state)
        persisted = local.path.read_bytes()
        for index in range(128):
            identity = f'{index + 1:032x}'
            state['checkouts'][f'{index + 1:064x}'] = {
                'checkout_id': identity, 'sessions': {identity: 'a' * 32}, 'pending': None,
                'bindings': {f'{bound + 1:064x}': {'session_id': identity, 'generation': 'a' * 32}
                             for bound in range(512)}}
        before = deepcopy(state)
        assert forge.team_config._state_valid(state)
        with pytest.raises(forge.team_state.TeamError) as failed:
            local.save(state)
        assert failed.value.code == 'team-local-state'
        assert local.path.read_bytes() == persisted and state == before
