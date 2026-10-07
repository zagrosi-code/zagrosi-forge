"""Public team lifecycle exercised against independent real Git clones."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys

import pytest

from forge_test_helpers import SCRIPT, load_zagrosi_module
from test_compact_plan import make_plan


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def repositories(tmp_path):
    remote = tmp_path / 'shared.git'
    remote.mkdir()
    git(remote, 'init', '--bare')
    first = tmp_path / 'alice'
    first.mkdir()
    git(first, 'init')
    git(first, 'config', 'user.name', 'Test engineer')
    git(first, 'config', 'user.email', 'test@example.invalid')
    git(first, 'config', 'commit.gpgsign', 'false')
    git(first, 'config', 'core.autocrlf', 'false')
    (first / 'README.md').write_text('Example\n')
    git(first, 'add', 'README.md')
    git(first, 'commit', '-m', 'initial')
    git(first, 'remote', 'add', 'origin', str(remote))
    git(first, 'push', 'origin', 'HEAD')
    second = tmp_path / 'bob'
    git(tmp_path, 'clone', str(remote), str(second))
    git(second, 'config', 'commit.gpgsign', 'false')
    return first, second, remote


@pytest.fixture
def team(capsys):
    forge = load_zagrosi_module()
    generations = {}
    def call(root, action, *args, ok=True):
        if action in {"update", "check", "finish"} and "--generation" not in args:
            identity = args[args.index("--session") + 1]
            args = (*args, "--generation", generations[(root, identity)])
        code = forge.entrypoint.main(['team', action, '--target-dir', str(root), *map(str, args)])
        data = json.loads(capsys.readouterr().out)
        assert code == (0 if ok else 1), data
        assert data['success'] is ok, data
        if data.get('session'):
            row = data['session']
            generations[(root, row['id'])] = row['generation']
        return data
    return call


def join_pair(team, repositories):
    first, second, _ = repositories
    team(first, 'init', '--remote', 'origin', '--name', 'Alex')
    marker = second / '.forge/team.json'
    marker.parent.mkdir()
    marker.write_bytes((first / '.forge/team.json').read_bytes())
    pending = team(second, 'status', ok=False)
    assert pending['status'] == 'join_required'
    team(second, 'join', '--remote', 'origin', '--name', 'Alex')
    return first, second


def test_unconfigured_status_never_claims_remote_clearance(team, repositories):
    first, _, _ = repositories
    result = team(first, 'status')
    assert result['status'] == 'unconfigured'
    assert result['clearance'] is False
    assert not (first / '.forge').exists()


def test_two_engineers_see_intent_and_reserve_without_touching_source(team, repositories):
    first, second = join_pair(team, repositories)
    before = git(first, 'status', '--porcelain'), git(first, 'rev-parse', 'HEAD')
    planning = team(first, 'start', '--task', 'Research authentication', '--host', 'codex')
    row = planning['session']
    assert row['state'] == 'planning' and row['paths'] == []
    assert planning['clearance'] is False
    active = team(first, 'update', '--session', row['id'], '--generation', row['generation'], '--path', 'src/auth', '--state', 'working')
    assert active['clearance'] is True
    observed = team(second, 'status')
    assert observed['status'] == 'fresh'
    assert observed['sessions'][0]['task'] == 'Research authentication'
    assert observed['sessions'][0]['host'] == 'codex'
    assert not observed['clearance']
    blocked = team(second, 'start', '--task', 'Other auth', '--host', 'claude', '--path', 'src/auth/login.py', ok=False)
    assert blocked['error_code'] == 'team-conflict'
    team(second, 'start', '--task', 'Improve docs', '--host', 'claude', '--path', 'docs')
    assert len(team(first, 'status')['sessions']) == 2
    assert before == (git(first, 'status', '--porcelain'), git(first, 'rev-parse', 'HEAD'))


def test_expansion_failure_preserves_claim_and_shared_checkout_serializes(team, repositories):
    first, second = join_pair(team, repositories)
    a = team(first, 'start', '--task', 'A', '--path', 'src/a.py')['session']
    team(second, 'start', '--task', 'B', '--path', 'src/b.py')
    result = team(first, 'update', '--session', a['id'], '--generation', a['generation'], '--path', 'src', ok=False)
    assert result['error_code'] == 'team-conflict'
    row = next(row for row in team(first, 'status')['sessions'] if row['id'] == a['id'])
    assert row['paths'] == ['src/a.py']
    other = team(first, 'start', '--task', 'Second writer', '--path', 'docs', ok=False)
    assert other['error_code'] == 'team-conflict'


def test_deliberate_handoff_fences_old_generation_and_finish_has_history(team, repositories):
    first, second = join_pair(team, repositories)
    original = team(first, 'start', '--task', 'Authentication', '--path', 'src/auth')['session']
    team(first, 'update', '--session', original['id'], '--state', 'handoff', '--note', 'Branch ready; integration remains unverified.')
    revision = team(second, 'status')['revision']
    taken = team(second, 'recover', '--session', original['id'], '--expect', revision,
                 '--reason', 'Agreed handoff', '--host', 'claude')['session']
    assert taken['generation'] != original['generation']
    assert taken['host'] == 'claude'
    lost = team(first, 'check', '--session', original['id'], '--generation', original['generation'], ok=False)
    assert lost['clearance'] is False
    team(first, 'finish', '--session', original['id'], '--note', 'wrong owner', ok=False)
    team(second, 'finish', '--session', taken['id'], '--generation', taken['generation'], '--note', 'Delivered in reviewed PR')
    assert team(first, 'status')['sessions'] == []
    history = git(repositories[2], 'log', '--format=%B', 'refs/heads/forge/team')
    assert 'Delivered in reviewed PR' in history


def test_offline_and_missing_board_never_authorize_edits(team, repositories):
    first, _ = join_pair(team, repositories)
    row = team(first, 'start', '--task', 'A', '--path', 'src/a.py')['session']
    cached = team(first, 'status', '--offline')
    assert cached['status'] == 'offline' and cached['clearance'] is False
    git(repositories[2], 'update-ref', '-d', 'refs/heads/forge/team')
    missing = team(first, 'check', '--session', row['id'], ok=False)
    assert missing['clearance'] is False
    assert missing['error_code'] == 'team-board-missing'


@pytest.mark.parametrize('overlap', [True, False])
def test_simultaneous_claims_preserve_admission_and_disjoint_updates(team, repositories, overlap):
    first, second = join_pair(team, repositories)
    def claim(root):
        result = subprocess.run([sys.executable, str(SCRIPT), 'team', 'start', '--target-dir', str(root),
                                 '--task', root.name, '--path', 'src/shared.py' if overlap else f'src/{root.name}.py'],
                                capture_output=True, text=True, timeout=60)
        return result.returncode, json.loads(result.stdout)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, [first, second]))
    assert sorted(code for code, _ in results) == ([0, 1] if overlap else [0, 0]), results
    if overlap:
        assert next(data for code, data in results if code)['error_code'] == 'team-conflict'
    assert len(team(first, 'status')['sessions']) == (1 if overlap else 2)


def test_linked_worktrees_share_awareness_with_independent_writer_scopes(team, repositories):
    first, _ = join_pair(team, repositories)
    other = first.parent / 'worktree'
    git(first, 'worktree', 'add', '-b', 'second-task', str(other))
    (other / '.forge').mkdir()
    (other / '.forge/team.json').write_bytes((first / '.forge/team.json').read_bytes())
    team(first, 'start', '--task', 'First', '--path', 'src/a.py')
    team(other, 'start', '--task', 'Second', '--path', 'src/b.py')
    rows = team(first, 'status')['sessions']
    assert {row['task'] for row in rows} == {'First', 'Second'}
    assert len({row['branch'] for row in rows}) == 2


@pytest.mark.parametrize('surface', ['hint', 'status', 'setup'])
def test_non_utf8_locale_cannot_hide_linked_checkout_participation(repositories, capsys, monkeypatch, surface):
    first, _, _ = repositories
    forge = load_zagrosi_module()
    renamed = first.with_name('r\u00e9po')
    first.rename(renamed)
    first = renamed
    assert forge.entrypoint.main(['team', 'init', '--target-dir', str(first), '--name', 'Alex']) == 0
    capsys.readouterr()
    linked = first.parent / 'linked'
    git(first, 'worktree', 'add', '-b', 'encoding-check', str(linked))
    administrative_path = (linked / '.git').read_bytes()
    assert administrative_path.decode('utf-8').startswith('gitdir: ')
    assert any(byte >= 128 for byte in administrative_path)
    assert not (linked / '.forge/team.json').exists()
    planning = make_plan(first.parent / 'private-plan')
    if surface == 'setup':
        with forge.storage.file_lock(planning / 'implementation' / '.mutable-state'):
            pass  # The existing workflow creates its administrative lock before dispatch.
    before = {path.relative_to(planning): path.read_bytes() for path in planning.rglob('*') if path.is_file()}
    original_read_text = Path.read_text
    def locale_read_text(path, encoding=None, *args, **kwargs):
        return original_read_text(path, encoding or 'cp1252', *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', locale_read_text)
    if surface == 'hint':
        assert forge.team._team_hint(linked)
        return
    arguments = (['team', 'status'] if surface == 'status' else
                 ['implement-setup', '--sections-dir', str(planning / 'sections'), '--flight', 'off'])
    code = forge.entrypoint.main([*arguments, '--target-dir', str(linked)])
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result['error_code'] == 'team-config-changed', result
    assert before == {path.relative_to(planning): path.read_bytes() for path in planning.rglob('*') if path.is_file()}


def test_check_revalidates_all_claimed_aliases_without_explicit_scope(team, repositories):
    first, _ = join_pair(team, repositories)
    (first / 'src').mkdir()
    (first / 'other').mkdir()
    alias = first / 'alias'
    try:
        alias.symlink_to('src', target_is_directory=True)
    except OSError:
        pytest.skip('Directory symlinks unavailable')
    row = team(first, 'start', '--task', 'Alias cleanup', '--path', 'alias')['session']
    alias.unlink()
    alias.symlink_to('other', target_is_directory=True)
    result = team(first, 'check', '--session', row['id'], ok=False)
    assert result['error_code'] == 'team-scope-changed'
    alias.unlink()
    alias.symlink_to('..', target_is_directory=True)
    result = team(first, 'check', '--session', row['id'], ok=False)
    assert result['error_code'] == 'team-invalid-path'


def test_note_update_cannot_implicitly_expand_retargeted_alias(team, repositories):
    first, _ = join_pair(team, repositories)
    (first / 'src').mkdir()
    (first / 'other').mkdir()
    alias = first / 'alias'
    try:
        alias.symlink_to('src', target_is_directory=True)
    except OSError:
        pytest.skip('Directory symlinks unavailable')
    row = team(first, 'start', '--task', 'Alias cleanup', '--path', 'alias')['session']
    revision = team(first, 'status')['revision']
    alias.unlink()
    alias.symlink_to('other', target_is_directory=True)
    refused = team(first, 'update', '--session', row['id'], '--note', 'Still working', ok=False)
    assert refused['error_code'] == 'team-scope-changed'
    assert team(first, 'status')['revision'] == revision
    updated = team(first, 'update', '--session', row['id'], '--path', 'alias')['session']
    assert updated['paths'] == ['alias', 'other']


@pytest.mark.parametrize('name', ['bad\x7f', 'bad\u202e', 'bad\u2028'])
def test_invalid_display_name_never_publishes_board(team, repositories, name):
    first, _, remote = repositories
    rejected = team(first, 'init', '--name', name, ok=False)
    assert rejected['error_code'] == 'team-name'
    assert not git(remote, 'for-each-ref', 'refs/heads/forge/team')
    assert not (first / '.forge/team.json').exists()


def ignore_discovery_marker(root, tmp_path, monkeypatch, source):
    pattern = '.forge/team.json\n'
    if source == 'global':
        ignored = tmp_path / 'global-ignore'
        configuration = tmp_path / 'global-gitconfig'
        git(root, 'config', '--file', str(configuration), 'core.excludesFile', str(ignored))
        monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(configuration))
    elif source == 'info':
        ignored = root / '.git/info/exclude'
    elif source == 'nested':
        ignored = root / '.forge/.gitignore'
        ignored.parent.mkdir(exist_ok=True)
        pattern = 'team.json\n'
    else:
        ignored = root / '.gitignore'
        if source == 'parent':
            pattern = '.forge/\n!.forge/team.json\n'
    ignored.write_text(pattern, encoding='utf-8')
    return ignored


def discovery_files(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob('*')
            if path.is_file() and path.relative_to(root).parts[0] != '.git'}


@pytest.mark.parametrize('source', ['user', 'parent', 'info', 'global', 'nested'])
def test_ignored_discovery_marker_blocks_init_before_publication(repositories, capsys, tmp_path, monkeypatch, source):
    first, _, remote = repositories
    forge = load_zagrosi_module()
    ignored = ignore_discovery_marker(first, tmp_path, monkeypatch, source)
    preserved = {path: path.read_bytes() for path in (ignored, first / '.git/config', first / '.git/index')}
    if (configuration := tmp_path / 'global-gitconfig').exists():
        preserved[configuration] = configuration.read_bytes()
    before = discovery_files(first)
    code = forge.entrypoint.main(['team', 'init', '--target-dir', str(first), '--name', 'Alex'])
    result = json.loads(capsys.readouterr().out)
    assert not git(remote, 'for-each-ref', 'refs/heads/forge/team'), result
    assert code == 1 and result['error_code'] == 'team-marker-ignored', result
    assert '.forge/team.json' in result['error'] and 'ignore' in result['error']
    assert discovery_files(first) == before
    assert preserved == {path: path.read_bytes() for path in preserved}
    local = forge.team_config.LocalTeam(forge.team_git.Repository.discover(first))
    assert local.load()['pending_init'] is None
    assert not git(first, 'for-each-ref', 'refs/forge/team-pending')


@pytest.mark.parametrize('source', ['parent', 'info', 'global'])
def test_committed_discovery_marker_remains_usable_when_ignore_rules_match(team, repositories, tmp_path, monkeypatch, source):
    first, _, _ = repositories
    team(first, 'init', '--name', 'Alex')
    git(first, 'add', '.forge/team.json')
    git(first, 'commit', '-m', 'Share team discovery')
    ignored = ignore_discovery_marker(first, tmp_path, monkeypatch, source)
    preserved = {path: path.read_bytes() for path in (ignored, first / '.git/config', first / '.git/index')}
    if (configuration := tmp_path / 'global-gitconfig').exists():
        preserved[configuration] = configuration.read_bytes()
    assert 'warning_code' not in team(first, 'status')
    assert team(first, 'join', '--name', 'Alex')['joined']
    assert preserved == {path: path.read_bytes() for path in preserved}


@pytest.mark.parametrize('action', ['status', 'join'])
def test_ignored_marker_reports_discovery_problem_before_join(team, repositories, tmp_path, monkeypatch, action):
    first, second, remote = repositories
    team(first, 'init', '--name', 'Alex')
    (second / '.forge').mkdir()
    (second / '.forge/team.json').write_bytes((first / '.forge/team.json').read_bytes())
    ignore_discovery_marker(second, tmp_path, monkeypatch, 'parent')
    before = discovery_files(second)
    revision = git(remote, 'rev-parse', 'refs/heads/forge/team')
    arguments = ['--name', 'Blair'] if action == 'join' else []
    result = team(second, action, *arguments, ok=False)
    assert result['error_code'] == 'team-marker-ignored', result
    assert discovery_files(second) == before
    assert git(remote, 'rev-parse', 'refs/heads/forge/team') == revision


def test_ignored_existing_marker_warns_without_stranding_finish_or_leave(team, repositories, tmp_path, monkeypatch):
    first, _, _ = repositories
    team(first, 'init', '--name', 'Alex')
    owned = team(first, 'start', '--task', 'Current owner', '--path', 'src')['session']
    ignored = ignore_discovery_marker(first, tmp_path, monkeypatch, 'info')
    preserved = {path: path.read_bytes() for path in
                 (ignored, first / '.forge/team.json', first / '.git/config', first / '.git/index')}
    status = team(first, 'status')
    assert status['warning_code'] == 'team-marker-ignored'
    assert '.forge/team.json' in status['next_action']
    joined = team(first, 'join', '--name', 'Alex', ok=False)
    assert joined['error_code'] == 'team-marker-ignored'
    finished = team(first, 'finish', '--session', owned['id'], '--note', 'Work complete')
    assert finished['published'] and finished['warning_code'] == 'team-marker-ignored'
    assert team(first, 'leave')['status'] == 'left'
    assert preserved == {path: path.read_bytes() for path in preserved}


@pytest.mark.parametrize('published', [False, True])
def test_ignored_marker_preserves_pending_init_until_user_repairs_ignore(repositories, capsys, tmp_path, monkeypatch, published):
    first, _, remote = repositories
    forge = load_zagrosi_module()
    original_marker = forge.team_config.LocalTeam.write_marker
    original_push = forge.team_git.GitBoard._push
    if published:
        monkeypatch.setattr(forge.team_config.LocalTeam, 'write_marker',
                            lambda *args: (_ for _ in ()).throw(OSError('injected')))
    else:
        monkeypatch.setattr(forge.team_git.GitBoard, '_push', lambda *args:
                            {'returncode': 124, 'stdout': '', 'stderr': '', 'timed_out': True})
    arguments = ['team', 'init', '--target-dir', str(first), '--name', 'Alex']
    assert forge.entrypoint.main(arguments) == 1
    capsys.readouterr()
    monkeypatch.setattr(forge.team_config.LocalTeam, 'write_marker', original_marker)
    monkeypatch.setattr(forge.team_git.GitBoard, '_push', original_push)
    remote_before = git(remote, 'for-each-ref', '--format=%(objectname)', 'refs/heads/forge/team')
    local = forge.team_config.LocalTeam(forge.team_git.Repository.discover(first))
    pending = local.path.read_bytes()
    attempted = json.loads(pending)['pending_init']['receipt']['revision']
    assert remote_before == (attempted if published else '')
    retained = git(first, 'for-each-ref', 'refs/forge/team-pending')
    ignored = ignore_discovery_marker(first, tmp_path, monkeypatch, 'parent')
    code = forge.entrypoint.main(arguments)
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result['error_code'] == 'team-marker-ignored', result
    assert local.path.read_bytes() == pending
    assert git(remote, 'for-each-ref', '--format=%(objectname)', 'refs/heads/forge/team') == remote_before
    assert git(first, 'for-each-ref', 'refs/forge/team-pending') == retained
    assert not (first / '.forge/team.json').exists()
    ignored.unlink()
    assert forge.entrypoint.main(arguments) == 0
    assert json.loads(capsys.readouterr().out)['joined']
    assert git(remote, 'rev-parse', 'refs/heads/forge/team') == attempted
    assert local.load()['pending_init'] is None


def test_ignored_marker_does_not_strand_pending_publication_retry(repositories, capsys, tmp_path, monkeypatch):
    first, _, _ = repositories
    forge = load_zagrosi_module()
    def call(action, *args):
        code = forge.entrypoint.main(['team', action, '--target-dir', str(first), *args])
        return code, json.loads(capsys.readouterr().out)
    assert call('init', '--name', 'Alex')[0] == 0
    original = forge.team_git.GitBoard._push
    monkeypatch.setattr(forge.team_git.GitBoard, '_push', lambda *args:
                        {'returncode': 124, 'stdout': '', 'stderr': '', 'timed_out': True})
    code, pending = call('start', '--task', 'Delayed task', '--path', 'src')
    assert code == 1 and pending['error_code'] == 'team-write-unknown'
    ignored = ignore_discovery_marker(first, tmp_path, monkeypatch, 'info')
    ignore_bytes = ignored.read_bytes()
    monkeypatch.setattr(forge.team_git.GitBoard, '_push', original)
    code, recovered = call('retry')
    assert code == 0 and recovered['published'], recovered
    assert recovered['warning_code'] == 'team-marker-ignored'
    assert ignored.read_bytes() == ignore_bytes


def test_interrupted_setup_resumes_the_created_board(repositories, capsys, monkeypatch):
    first, _, remote = repositories
    forge = load_zagrosi_module()
    original = forge.team_config.LocalTeam.write_marker
    monkeypatch.setattr(forge.team_config.LocalTeam, 'write_marker', lambda *args: (_ for _ in ()).throw(OSError('injected')))
    args = ['team', 'init', '--target-dir', str(first), '--name', 'Alex']
    assert forge.entrypoint.main(args) == 1
    capsys.readouterr()
    created = git(remote, 'rev-parse', 'refs/heads/forge/team')
    monkeypatch.setattr(forge.team_config.LocalTeam, 'write_marker', original)
    assert forge.entrypoint.main(args) == 0
    assert json.loads(capsys.readouterr().out)['joined']
    assert git(remote, 'rev-parse', 'refs/heads/forge/team') == created


def test_crash_after_publication_retries_the_same_task(repositories, capsys, monkeypatch):
    first, _, _ = repositories
    forge = load_zagrosi_module()
    assert forge.entrypoint.main(['team', 'init', '--target-dir', str(first), '--name', 'Alex']) == 0
    capsys.readouterr()
    original = forge.team._accept_pending
    monkeypatch.setattr(forge.team, '_accept_pending', lambda *args: (_ for _ in ()).throw(OSError('injected')))
    args = ['team', 'start', '--target-dir', str(first), '--task', 'Research']
    assert forge.entrypoint.main(args) == 1
    capsys.readouterr()
    monkeypatch.setattr(forge.team, '_accept_pending', original)
    assert forge.entrypoint.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['recovered_operation'] and len(result['sessions']) == 1
    assert result['session']['task'] == 'Research'


def test_shared_cache_retention_is_bounded_across_worktrees(team, repositories):
    first, _ = join_pair(team, repositories)
    row = team(first, 'start', '--task', 'A', '--path', 'src')['session']
    forge = load_zagrosi_module()
    with forge.team.workspace(first) as context:
        observed = forge.team._observe(context)['snapshot']
        template = {key: value for key, value in row.items() if key != 'id'}
        sessions = {f'{i + 1:032x}': {**template, 'checkout_id': f'{i + 100:032x}',
                    'paths': [f'scope{i}/{j:03d}' + 'a' * 840 for j in range(256)]} for i in range(4)}
        board = forge.team_git.Snapshot(observed.revision, forge.team_state.validate_board(
            {**observed.board, 'sessions': sessions}))
        for index in range(16):
            clone = {**context['checkout'], 'checkout_id': f'{index + 100:032x}'}
            context['state']['checkouts'][f'{index:064x}'] = clone
            moved = {**context, 'checkout': clone}
            forge.team._cache(moved, board)
        saved = context['local'].load()
        assert context['local'].path.stat().st_size < 2 * 1024 * 1024
        assert len(saved['cache']['board']['sessions']) == 4
    offline = team(first, 'status', '--offline')
    assert len(offline['sessions']) == 4 and not offline['clearance']


@pytest.mark.parametrize('other_worktree', [False, True])
@pytest.mark.parametrize('abandon', [False, True])
def test_leave_preserves_unknown_publications_from_every_checkout(repositories, capsys, monkeypatch, other_worktree, abandon):
    first, _, _ = repositories
    forge = load_zagrosi_module()
    def call(root, action, *args):
        code = forge.entrypoint.main(['team', action, '--target-dir', str(root), *args])
        return code, json.loads(capsys.readouterr().out)
    assert call(first, 'init', '--name', 'Alex')[0] == 0
    caller = first
    if other_worktree:
        caller = first.parent / 'worktree'
        git(first, 'worktree', 'add', '-b', 'other', str(caller))
        (caller / '.forge').mkdir()
        (caller / '.forge/team.json').write_bytes((first / '.forge/team.json').read_bytes())
    original = forge.team_git.GitBoard._push
    delayed = []
    def unknown(transport, receipt):
        delayed.append((transport, receipt.copy()))
        return {'returncode': 1, 'stdout': '', 'stderr': 'connection interrupted'}
    monkeypatch.setattr(forge.team_git.GitBoard, '_push', unknown)
    code, result = call(first, 'start', '--task', 'Delayed task', '--path', 'src')
    assert code == 1 and result['error_code'] == 'team-write-unknown'
    local = forge.team_config.LocalTeam(forge.team_git.Repository.discover(first))
    pending = local.load()['checkouts'][local.key]['pending']
    code, result = call(caller, 'leave')
    assert code == 1 and result['error_code'] == 'team-pending-write'
    assert local.load()['connection'] is not None
    assert local.load()['checkouts'][local.key]['pending'] == pending
    if abandon:
        assert call(caller, 'leave', '--abandon')[0] == 0
        assert local.load()['connection'] is None
        assert not git(first, 'for-each-ref', 'refs/forge/team-pending')
        return
    monkeypatch.setattr(forge.team_git.GitBoard, '_push', original)
    assert original(*delayed[0])['returncode'] == 0
    code, result = call(first, 'retry')
    assert code == 0 and result['session']['id'] == pending['session_id']
    assert result['clearance']


def test_service_never_substitutes_latest_generation_for_stale_caller(repositories, capsys):
    first, _, _ = repositories
    forge = load_zagrosi_module()
    def call(action, *args):
        assert forge.entrypoint.main(['team', action, '--target-dir', str(first), *args]) == 0
        return json.loads(capsys.readouterr().out)
    call('init', '--name', 'Alex')
    original = call('start', '--task', 'Owned', '--path', 'src')['session']
    recovered = call('recover', '--session', original['id'], '--expect', call('status')['revision'],
                     '--reason', 'Agreed same-checkout handoff')['session']
    assert recovered['generation'] != original['generation']
    with forge.team.workspace(first) as context:
        forge.team._observe(context)
        before = context['snapshot'].revision
        for generation in (None, original['generation']):
            with pytest.raises(forge.team_state.TeamError):
                forge.team.check(context, original['id'], generation=generation)
            with pytest.raises(forge.team_state.TeamError):
                forge.team.mutate(context, 'finish', identity=original['id'], generation=generation, note='Stale caller')
        assert context['transport'].read().revision == before
        assert forge.team.check(context, original['id'], generation=recovered['generation'])['clearance']
