"""Versioned records and explicit protocol upgrades preserve shared ownership."""
from copy import deepcopy
import json

import pytest

from forge_test_helpers import load_zagrosi_module
from test_team_collaboration import git, join_pair, repositories, team
from test_team_git import client
from test_team_state import BOARD, FIRST, board, session


def legacy_pair(team, repositories):
    first, second, _ = repositories
    client(team.forge, first).write(board(), None, lambda receipt: None)
    for root in (first, second):
        local = team.forge.team_config.LocalTeam(team.forge.team_git.Repository.discover(root))
        local.write_marker(BOARD)
        team(root, 'join', '--name', root.name)
    return first, second


def local_state(team, root):
    local = team.forge.team_config.LocalTeam(team.forge.team_git.Repository.discover(root))
    return local, local.load()


def remote_head(repositories):
    return git(repositories[2], 'rev-parse', 'refs/heads/forge/team')


def unknown_push(*args):
    return {'returncode': 124, 'stdout': '', 'stderr': '', 'timed_out': True}


@pytest.mark.parametrize('version', [1, 2])
def test_board_round_trip_keeps_version_and_independent_values(version):
    state = load_zagrosi_module().team_state
    row = session()
    if version == 2:
        row['dependencies'] = {'paths': ['lib/api.py'], 'complete': False}
    original = {**board(**{FIRST: row}), 'version': version}
    before = deepcopy(original)
    result = state.validate_board(original)
    assert result == original == before
    again = state.validate_board(json.loads(json.dumps(result)))
    assert again == result
    again['sessions'][FIRST]['paths'].append('tests')
    if version == 2:
        again['sessions'][FIRST]['dependencies']['paths'].append('lib/other.py')
    assert result == original == before


def test_v1_cannot_smuggle_dependency_metadata():
    state = load_zagrosi_module().team_state
    value = board(**{FIRST: session(dependencies={'paths': ['lib/api.py'], 'complete': True})})
    with pytest.raises(state.TeamError) as failed:
        state.validate_board(value)
    assert failed.value.code == 'team-invalid-board'


@pytest.mark.parametrize('metadata', [
    None, {}, {'paths': ['lib/api.py']},
    {'paths': ['lib/api.py'], 'complete': 1},
    {'paths': ['lib/api.py'], 'complete': True, 'extra': 'ignored'},
    {'paths': [], 'complete': False},
    {'paths': ('lib/api.py',), 'complete': True},
    {'paths': ['../private'], 'complete': True},
])
def test_v2_dependency_metadata_is_strict(metadata):
    state = load_zagrosi_module().team_state
    value = {**board(**{FIRST: session(dependencies=metadata)}), 'version': 2}
    before = deepcopy(value)
    with pytest.raises(state.TeamError) as failed:
        state.validate_board(value)
    assert failed.value.code in {'team-invalid-board', 'team-invalid-path'}
    assert value == before


def test_dependency_paths_normalize_without_becoming_write_reservations():
    state = load_zagrosi_module().team_state
    row = session(dependencies={'paths': ['lib\\api.py', './lib/api.py', 'tests//api.py'], 'complete': False})
    result = state.validate_board({**board(**{FIRST: row}), 'version': 2})
    assert result['sessions'][FIRST]['dependencies'] == {
        'paths': ['lib/api.py', 'tests/api.py'], 'complete': False}
    assert result['sessions'][FIRST]['paths'] == ['src/auth.py']
    paths = [f'lib/file-{index:03}.py' for index in range(256)]
    row['dependencies']['paths'] = paths
    assert state.validate_board({**board(**{FIRST: row}), 'version': 2})['sessions'][FIRST]['dependencies']['paths'] == paths
    row['dependencies']['paths'] = [*paths, 'lib/extra.py']
    with pytest.raises(state.TeamError):
        state.validate_board({**board(**{FIRST: row}), 'version': 2})


def test_dependency_aliases_count_toward_existing_path_limit(tmp_path):
    state = load_zagrosi_module().team_state
    (tmp_path / 'target').mkdir()
    try:
        (tmp_path / 'alias').symlink_to('target', target_is_directory=True)
    except OSError:
        pytest.skip('Directory symlinks unavailable')
    empty = {**board(), 'version': 2}
    row = session(dependencies={'paths': ['alias'], 'complete': True})
    result = state.with_session(empty, FIRST, row, root=tmp_path)
    assert result['sessions'][FIRST]['dependencies']['paths'] == ['alias', 'target']
    row['dependencies']['paths'] = ['alias', *[f'file-{index:03}' for index in range(255)]]
    with pytest.raises(state.TeamError) as failed:
        state.with_session(empty, FIRST, row, root=tmp_path)
    assert failed.value.code == 'team-invalid-path'
    assert empty == {**board(), 'version': 2}


def test_dependency_metadata_counts_toward_board_byte_limit():
    state = load_zagrosi_module().team_state
    rows = {f'{index + 1:032x}': session(paths=[], dependencies={
        'paths': [f'lib/{item:03}/' + 'x' * 990 for item in range(100)], 'complete': True})
        for index in range(12)}
    with pytest.raises(state.TeamError) as failed:
        state.validate_board({**board(**rows), 'version': 2})
    assert failed.value.code == 'team-invalid-board'


@pytest.mark.parametrize('version', [1, 2])
def test_public_mutations_and_cache_reopen_keep_observed_version(team, repositories, version):
    first, _ = legacy_pair(team, repositories) if version == 1 else join_pair(team, repositories)
    started = team(first, 'start', '--task', 'Ordinary work', '--path', 'src/a.py')
    updated = team(first, 'update', '--session', started['session']['id'], '--note', 'Still working')
    assert started['protocol_version'] == updated['protocol_version'] == version
    assert client(team.forge, first).read().board['version'] == version
    local, saved = local_state(team, first)
    reopened = team.forge.team_config.LocalTeam(team.forge.team_git.Repository.discover(first)).load()
    assert reopened == saved and saved['version'] == 1
    assert saved['cache']['board']['version'] == version
    assert json.loads((first / '.forge/team.json').read_text())['version'] == 1
    assert team(first, 'status', '--offline')['protocol_version'] == version
    with local.locked():
        saved['cache'] = None
        local.save(saved)
    uncached = team(first, 'status', '--offline')
    assert uncached['protocol_version'] is None and uncached['clearance'] is False
    finished = team(first, 'finish', '--session', started['session']['id'], '--note', 'Complete')
    assert finished['protocol_version'] == version
    assert client(team.forge, first).read().board['version'] == version


@pytest.mark.parametrize('action', ['start', 'update'])
def test_explicit_upgrade_is_one_atomic_actor_operation(team, repositories, action):
    first, second = legacy_pair(team, repositories)
    peer = team(second, 'start', '--task', 'Peer work', '--path', 'src/peer.py')['session']
    actor = (team(first, 'start', '--task', 'Actor work', '--path', 'src/actor.py')['session']
             if action == 'update' else None)
    before = client(team.forge, first).read()
    local, saved = local_state(team, first)
    marker = (first / '.forge/team.json').read_bytes()
    args = (['--session', actor['id'], '--note', 'Upgraded'] if actor else
            ['--task', 'Actor work', '--path', 'src/actor.py'])
    result = team(first, action, *args, '--upgrade-protocol', '--expect', before.revision)
    after = client(team.forge, first).read()
    assert result['published'] and result['protocol_version'] == 2
    assert after.board['version'] == 2 and after.board['board_id'] == before.board['board_id']
    assert after.board['sessions'][peer['id']] == before.board['sessions'][peer['id']]
    assert len(after.board['sessions']) == 2
    assert git(repositories[2], 'rev-list', '--count', f'{before.revision}..{after.revision}') == '1'
    assert git(repositories[2], 'rev-parse', f'{after.revision}^') == before.revision
    if actor:
        assert (result['session']['id'], result['session']['generation']) == (actor['id'], actor['generation'])
    current = local.load()
    assert current['version'] == saved['version'] == 1
    assert current['participant_id'] == saved['participant_id']
    assert current['checkouts'][local.key]['checkout_id'] == saved['checkouts'][local.key]['checkout_id']
    assert current['checkouts'][local.key]['pending'] is None
    assert (first / '.forge/team.json').read_bytes() == marker


@pytest.mark.parametrize('case', ['missing-expect', 'unpaired-expect', 'already-v2'])
def test_invalid_upgrade_consent_never_publishes(team, repositories, case):
    first, _ = join_pair(team, repositories) if case == 'already-v2' else legacy_pair(team, repositories)
    before = remote_head(repositories)
    flags = (['--upgrade-protocol'] if case == 'missing-expect' else
             ['--expect', before] if case == 'unpaired-expect' else
             ['--upgrade-protocol', '--expect', before])
    result = team(first, 'start', '--task', 'Invalid consent', *flags, ok=False)
    assert result['error_code'] == 'team-protocol-upgrade'
    assert remote_head(repositories) == before
    assert not git(first, 'for-each-ref', 'refs/forge/team-pending')


def test_stale_expected_revision_refuses_before_publication(team, repositories, monkeypatch):
    first, second = legacy_pair(team, repositories)
    expected = team(first, 'status')['revision']
    team(second, 'start', '--task', 'New peer task')
    before = remote_head(repositories)
    def forbidden_push(*args):
        pytest.fail('Stale consent attempted publication')
    monkeypatch.setattr(team.forge.team_git.GitBoard, '_push', forbidden_push)
    result = team(first, 'start', '--task', 'Stale consent', '--upgrade-protocol', '--expect', expected, ok=False)
    assert result['error_code'] == 'team-upgrade-changed'
    assert remote_head(repositories) == before
    assert not git(first, 'for-each-ref', 'refs/forge/team-pending')


@pytest.mark.parametrize('case', ['invalid-scope', 'full-board'])
def test_invalid_actor_operation_cannot_upgrade_board(team, repositories, monkeypatch, case):
    first, _ = legacy_pair(team, repositories)
    if case == 'full-board':
        transport = client(team.forge, first)
        current = transport.read()
        filled = deepcopy(current.board)
        filled['sessions'] = {f'{index + 1:032x}': session(paths=[]) for index in range(128)}
        transport.write(filled, current.revision, lambda receipt: None)
    before = remote_head(repositories)
    def forbidden_push(*args):
        pytest.fail('Invalid actor operation attempted publication')
    monkeypatch.setattr(team.forge.team_git.GitBoard, '_push', forbidden_push)
    paths = ['--path', '../outside'] if case == 'invalid-scope' else []
    result = team(first, 'start', '--task', 'Invalid upgrade', *paths,
                  '--upgrade-protocol', '--expect', before, ok=False)
    assert result['error_code'] == ('team-invalid-path' if case == 'invalid-scope' else 'team-invalid-board')
    assert remote_head(repositories) == before
    assert client(team.forge, first).read().board['version'] == 1
    assert not git(first, 'for-each-ref', 'refs/forge/team-pending')


@pytest.mark.parametrize('upgrade', [False, True], ids=['ordinary-retries', 'upgrade-stops'])
def test_actual_cas_contention_retries_only_ordinary_mutations(team, repositories, monkeypatch, upgrade):
    first, second = legacy_pair(team, repositories)
    peer = team(second, 'start', '--task', 'Peer work', '--path', 'src/peer.py')['session']
    expected = remote_head(repositories)
    original_push = team.forge.team_git.GitBoard._push
    actor_pushes, peer_heads = [], []
    def contend(transport, receipt):
        if transport.repo.root == first.resolve():
            actor_pushes.append(receipt.copy())
            if len(actor_pushes) == 1:
                other = client(team.forge, second)
                current = other.read()
                changed = deepcopy(current.board)
                changed['sessions'][peer['id']]['note'] = 'Concurrent peer update'
                peer_heads.append(other.write(changed, current.revision, lambda receipt: None).revision)
        return original_push(transport, receipt)
    monkeypatch.setattr(team.forge.team_git.GitBoard, '_push', contend)
    flags = ['--upgrade-protocol', '--expect', expected] if upgrade else []
    result = team(first, 'start', '--task', 'Actor work', '--path', 'src/actor.py', *flags, ok=not upgrade)
    current = client(team.forge, first).read()
    assert current.board['sessions'][peer['id']]['note'] == 'Concurrent peer update'
    assert current.board['version'] == 1
    assert len(actor_pushes) == (1 if upgrade else 2)
    if upgrade:
        assert result['error_code'] == 'team-contention'
        assert current.revision == peer_heads[0] and len(current.board['sessions']) == 1
    else:
        assert result['published'] and len(current.board['sessions']) == 2
        assert actor_pushes[1]['expected'] == peer_heads[0]
    local, saved = local_state(team, first)
    assert saved['checkouts'][local.key]['pending'] is None


def test_unknown_upgrade_retries_exact_commit_and_fences_new_work(team, repositories, monkeypatch):
    first, _ = legacy_pair(team, repositories)
    expected = remote_head(repositories)
    with monkeypatch.context() as patch:
        patch.setattr(team.forge.team_git.GitBoard, '_push', unknown_push)
        failed = team(first, 'start', '--task', 'Upgrade', '--upgrade-protocol', '--expect', expected, ok=False)
    assert failed['error_code'] == 'team-write-unknown'
    local, saved = local_state(team, first)
    pending = saved['checkouts'][local.key]['pending']
    assert set(pending) == {'action', 'session_id', 'generation', 'binding', 'revision', 'expected', 'board_id'}
    frozen = git(first, 'show', f"{pending['revision']}:board.json")
    assert json.loads(frozen)['version'] == 2 and remote_head(repositories) == expected
    blocked = team(first, 'start', '--task', 'Must wait', '--upgrade-protocol', '--expect', expected, ok=False)
    assert blocked['error_code'] == 'team-pending-write'
    assert local.load()['checkouts'][local.key]['pending'] == pending
    recovered = team(first, 'retry')
    assert recovered['published'] and recovered['protocol_version'] == 2
    assert remote_head(repositories) == pending['revision']
    assert git(first, 'show', f"{pending['revision']}:board.json") == frozen
    assert recovered['session']['id'] == pending['session_id']
    assert local.load()['checkouts'][local.key]['pending'] is None


@pytest.mark.parametrize('published', [False, True], ids=['unapplied-v1-refused', 'applied-v1-reconciled'])
def test_pending_v1_operation_never_downgrades_peer_upgrade(team, repositories, monkeypatch, published):
    first, second = legacy_pair(team, repositories)
    with monkeypatch.context() as patch:
        if published:
            patch.setattr(team.forge.team, '_accept_pending', lambda *args:
                          (_ for _ in ()).throw(OSError('crash after publication')))
        else:
            patch.setattr(team.forge.team_git.GitBoard, '_push', unknown_push)
        failed = team(first, 'start', '--task', 'Old v1 work', '--path', 'src/old.py', ok=False)
    assert failed['error_code'] == ('team-local-error' if published else 'team-write-unknown')
    local, saved = local_state(team, first)
    pending = saved['checkouts'][local.key]['pending']
    assert json.loads(git(first, 'show', f"{pending['revision']}:board.json"))['version'] == 1
    expected = team(second, 'status')['revision']
    team(second, 'start', '--task', 'Upgrade peer', '--path', 'src/peer.py', '--upgrade-protocol', '--expect', expected)
    upgraded = remote_head(repositories)
    result = team(first, 'retry', ok=published)
    assert remote_head(repositories) == upgraded
    assert client(team.forge, first).read().board['version'] == 2
    if published:
        assert result['recovered_operation'] and result['protocol_version'] == 2
        assert (result['session']['id'], result['session']['generation']) == (pending['session_id'], pending['generation'])
    else:
        assert result['error_code'] == 'team-contention'
        assert pending['session_id'] not in client(team.forge, first).read().board['sessions']
    assert local.load()['checkouts'][local.key]['pending'] is None


def test_pending_v1_initialization_replays_frozen_commit_after_default_changes(team, repositories, monkeypatch):
    first, _, _ = repositories
    # Model a preexisting v1 receipt by changing only the creation fixture data.
    # Every persistence/Git/retry operation remains the real implementation.
    with monkeypatch.context() as patch:
        patch.setattr(team.forge.team, 'new_board', lambda identity: {'version': 1, 'board_id': identity, 'sessions': {}})
        patch.setattr(team.forge.team_git.GitBoard, '_push', unknown_push)
        failed = team(first, 'init', '--name', 'Alex', ok=False)
    assert failed['error_code'] == 'team-write-unknown'
    local, saved = local_state(team, first)
    pending = saved['pending_init']
    revision = pending['receipt']['revision']
    frozen = git(first, 'show', f'{revision}:board.json')
    assert json.loads(frozen)['version'] == 1
    assert team.forge.team_state.new_board(BOARD)['version'] == 2
    resumed = team(first, 'init', '--name', 'Alex')
    assert resumed['joined'] and resumed['protocol_version'] == 1
    assert remote_head(repositories) == revision
    assert git(first, 'show', f'{revision}:board.json') == frozen
    assert local.load()['pending_init'] is None


def test_unplanned_update_and_recovery_preserve_metadata_then_finish_removes_it(team, repositories):
    first, second = join_pair(team, repositories)
    row = team(first, 'start', '--task', 'Task', '--path', 'src/task.py')['session']
    transport = client(team.forge, first)
    current = transport.read()
    modified = deepcopy(current.board)
    metadata = {'paths': ['lib/api.py'], 'complete': False}
    modified['sessions'][row['id']]['dependencies'] = metadata
    transport.write(modified, current.revision, lambda receipt: None)
    updated = team(first, 'update', '--session', row['id'], '--state', 'handoff', '--note', 'Ready')['session']
    assert updated['dependencies'] == metadata and updated['generation'] == row['generation']
    expected = team(second, 'status')['revision']
    recovered = team(second, 'recover', '--session', row['id'], '--expect', expected, '--reason', 'Agreed handoff')['session']
    assert recovered['dependencies'] == metadata and recovered['generation'] != row['generation']
    team(second, 'finish', '--session', recovered['id'], '--note', 'Complete')
    current = transport.read().board
    assert current['version'] == 2 and row['id'] not in current['sessions']
