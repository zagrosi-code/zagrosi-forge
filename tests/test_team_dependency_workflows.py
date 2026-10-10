"""Declared inputs stay current across team workflow boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from test_compact_plan import SECTION, make_plan
from test_compatibility_checks import block, contract
from test_team_git import client
from test_team_workflows import call, configure, files, forge, record, repositories, setup, team, verify
from test_workflow_admission import two_sections


SECOND = 'section-02-consumer'
INPUTS = {'source_paths': ['lib/api.py'], 'check_paths': ['checks/api.py']}
NOT_REQUIRED = {'version': 1, 'mode': 'not_required',
                'reason': 'Documentation-only section has no executable behavior to preserve.'}


def declare(planning, section=SECTION, value=None):
    path = planning / 'sections' / f'{section}.md'
    body = path.read_text().split('\n## Compatibility\n', 1)[0]
    path.write_text(body + (block(value) if value is not None else ''))


def inputs(target, declaration):
    for name in declaration['source_paths'] + declaration['check_paths']:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('pass\n')


def join_second(forge, capsys, repositories):
    first, second, _ = repositories
    configure(forge, capsys, first)
    (second / '.forge').mkdir()
    (second / '.forge/team.json').write_bytes((first / '.forge/team.json').read_bytes())
    team(forge, capsys, second, 'join', '--name', 'Blair')
    return first, second


def start(forge, capsys, target, planning, section=SECTION):
    selection = ['--section', section] if section else []
    return team(forge, capsys, target, 'start', '--task', 'Declared-input work',
                '--planning-dir', planning, *selection)


def check_args(target, row, planning=None, section=None):
    args = ['team', 'check', '--target-dir', target, '--session', row['id'], '--generation', row['generation']]
    if planning is not None:
        args += ['--planning-dir', planning]
    if section is not None:
        args += ['--section', section]
    return args


def assert_warning(result, peer, direction):
    awareness = result['dependency_awareness']
    assert awareness['status'] == 'partial'  # The plain writer has unknown declared inputs.
    assert awareness['reason'] is None and awareness['unknown_sessions'] == 1
    assert awareness['omitted_warnings'] == 0
    assert awareness['alias_issues'] == {'count': 0, 'first': None}
    assert awareness['warnings'] == [{
        'session_id': peer['id'], 'name': peer['name'], 'stale': False,
        'direction': direction, 'dependency_path': 'lib/api.py', 'write_path': 'lib/api.py',
        'basis': 'portable_path', 'alias_paths': None}]


def test_required_source_and_check_paths_map_through_package_target(forge, capsys, repositories, tmp_path):
    root, _, _ = repositories
    configure(forge, capsys, root)
    target = root / 'packages/api'
    target.mkdir(parents=True)
    planning = make_plan(tmp_path / 'private-plan')
    declared = contract(**INPUTS)
    declare(planning, value=declared)
    result = start(forge, capsys, target, planning)
    assert result['session']['dependencies'] == {
        'paths': ['packages/api/checks/api.py', 'packages/api/lib/api.py'], 'complete': True}
    assert result['session']['paths'] == ['packages/api/src/labels.py', 'packages/api/tests/test_labels.py']
    assert result['dependency_awareness']['status'] == 'observed'
    assert result['dependency_awareness']['warnings'] == []
    assert not (planning / 'implementation').exists()


@pytest.mark.parametrize('first_decl,second_decl,expected', [
    (contract(**INPUTS), None, {'paths': ['checks/api.py', 'lib/api.py'], 'complete': False}),
    (contract(**INPUTS), NOT_REQUIRED, {'paths': ['checks/api.py', 'lib/api.py'], 'complete': False}),
    (None, None, None),
    (NOT_REQUIRED, NOT_REQUIRED, None),
])
def test_whole_plan_retains_known_partial_inputs_without_inventing_empty_coverage(
        forge, capsys, repositories, tmp_path, first_decl, second_decl, expected):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = two_sections(tmp_path / 'private-plan')
    declare(planning, SECTION, first_decl)
    declare(planning, SECOND, second_decl)
    result = start(forge, capsys, root, planning, section=None)
    if expected is None:
        assert 'dependencies' not in result['session']
    else:
        assert result['session']['dependencies'] == expected
    assert result['dependency_awareness']['status'] == 'partial'
    assert result['dependency_awareness']['unknown_sessions'] == 1
    assert result['dependency_awareness']['warnings'] == []


def test_whole_plan_parses_each_required_section_once_and_keeps_complete_union(
        forge, capsys, repositories, tmp_path, monkeypatch):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = two_sections(tmp_path / 'private-plan')
    declare(planning, SECTION, contract(**INPUTS))
    declare(planning, SECOND, contract(source_paths=['lib/consumer.py'], check_paths=['checks/consumer.py']))
    parse = forge.compatibility.parse_declaration
    observed = []
    def count(text):
        observed.append(text)
        return parse(text)
    monkeypatch.setattr(forge.compatibility, 'parse_declaration', count)
    result = start(forge, capsys, root, planning, section=None)
    assert len(observed) == 2
    assert set(observed) == {(planning / 'sections' / f'{name}.md').read_text() for name in (SECTION, SECOND)}
    assert result['session']['dependencies'] == {
        'paths': ['checks/api.py', 'checks/consumer.py', 'lib/api.py', 'lib/consumer.py'], 'complete': True}
    assert result['dependency_awareness']['status'] == 'observed'


def test_plan_refresh_replaces_inputs_but_unplanned_update_and_bare_check_do_not(
        forge, capsys, repositories, tmp_path):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = make_plan(tmp_path / 'private-plan')
    declare(planning, value=contract(**INPUTS))
    row = start(forge, capsys, root, planning)['session']
    replacement = contract(source_paths=['lib/replacement.py'], check_paths=['checks/replacement.py'])
    declare(planning, value=replacement)
    updated = team(forge, capsys, root, 'update', '--session', row['id'], '--generation', row['generation'],
                   '--note', 'Unplanned status refresh')['session']
    assert updated['dependencies'] == row['dependencies']
    code, bare = call(forge, capsys, *check_args(root, row))
    assert code == 0 and bare['session']['dependencies'] == row['dependencies'], bare
    before = files(planning)
    revision = team(forge, capsys, root, 'status')['revision']
    code, blocked = call(forge, capsys, *check_args(root, row, planning, SECTION))
    assert code == 1 and blocked['error_code'] == 'team-dependency-binding', blocked
    assert files(planning) == before
    assert team(forge, capsys, root, 'status')['revision'] == revision
    repair = blocked['commands']['team_update']
    assert repair[repair.index('--section') + 1] == SECTION
    assert repair[repair.index('--session') + 1] == row['id']
    assert repair[repair.index('--generation') + 1] == row['generation']
    code, repaired = call(forge, capsys, *repair[2:])
    assert code == 0, repaired
    assert repaired['session']['dependencies'] == {
        'paths': ['checks/replacement.py', 'lib/replacement.py'], 'complete': True}
    assert repaired['session']['generation'] == row['generation']
    assert repaired['session']['paths'] == row['paths']
    assert call(forge, capsys, *check_args(root, row, planning, SECTION))[0] == 0
    declare(planning, value=NOT_REQUIRED)
    code, unknown = call(forge, capsys, *repair[2:])
    assert code == 0 and 'dependencies' not in unknown['session'], unknown


def test_malformed_refresh_cannot_publish_unknown_coverage_or_replace_accepted_claim(
        forge, capsys, repositories, tmp_path):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = make_plan(tmp_path / 'private-plan')
    declare(planning, value=contract(**INPUTS))
    row = start(forge, capsys, root, planning)['session']
    transport = client(forge, root)
    accepted = transport.read()
    local = forge.team_config.LocalTeam(forge.team_git.Repository.discover(root))
    checkout = deepcopy(local.load()['checkouts'][local.key])
    declare(planning, value=contract(source_paths=[], check_paths=['checks/api.py']))
    before = files(planning)
    code, result = call(forge, capsys, 'team', 'update', '--target-dir', root,
                        '--session', row['id'], '--generation', row['generation'],
                        '--planning-dir', planning, '--section', SECTION)
    assert code == 1 and result['success'] is False and result['clearance'] is False, result
    assert isinstance(result.get('error'), str) and result['error'].strip()
    current = transport.read()
    assert current.revision == accepted.revision and current.board == accepted.board
    assert current.board['sessions'][row['id']]['dependencies'] == row['dependencies']
    assert current.board['sessions'][row['id']]['paths'] == row['paths']
    assert local.load()['checkouts'][local.key] == checkout
    assert files(planning) == before


@pytest.mark.parametrize('boundary,change', [
    ('entry', 'source'), ('setup', 'coverage'), ('verify', 'checks'), ('record', 'source'),
])
def test_whole_plan_binding_detects_other_section_drift_before_work(
        forge, capsys, repositories, tmp_path, boundary, change):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = two_sections(tmp_path / 'private-plan')
    # Keep A legacy except for the coverage case, where both sections advertise
    # the same paths: removing B changes only complete, not the union of paths.
    original = contract(**INPUTS)
    if change == 'coverage':
        declare(planning, SECTION, original)
    declare(planning, SECOND, original)
    inputs(root, original)
    row = start(forge, capsys, root, planning, section=None)['session']
    assert setup(forge, capsys, planning, root)[0] == 0
    changed = deepcopy(original)
    if change == 'coverage':
        changed = None
    else:
        field = 'source_paths' if change == 'source' else 'check_paths'
        changed[field] = ['lib/other.py' if change == 'source' else 'checks/other.py']
    declare(planning, SECOND, changed)
    before = files(planning)
    revision = team(forge, capsys, root, 'status')['revision']
    marker = tmp_path / 'caller-executed'
    if boundary == 'entry':
        code, blocked = call(forge, capsys, 'next-section', '--planning-dir', planning, '--target-dir', root)
    elif boundary == 'setup':
        code, blocked = setup(forge, capsys, planning, root)
    elif boundary == 'record':
        code, blocked = record(forge, capsys, planning, root)
    else:
        code, blocked = verify(forge, capsys, planning, root, '--section', SECTION, '--', sys.executable,
                               '-c', f'from pathlib import Path; Path({str(marker)!r}).touch()')
    assert code == 1 and blocked['error_code'] == 'team-dependency-binding', blocked
    assert not marker.exists() and files(planning) == before
    assert team(forge, capsys, root, 'status')['revision'] == revision
    repair = blocked['commands']['team_update']
    assert '--section' not in repair  # A's fallback must refresh B as well.
    assert repair[repair.index('--planning-dir') + 1] == str(planning)
    assert repair[repair.index('--session') + 1] == row['id']
    assert repair[repair.index('--generation') + 1] == row['generation']
    code, repaired = call(forge, capsys, *repair[2:])
    assert code == 0, repaired
    if change == 'coverage':
        assert repaired['session']['dependencies'] == {
            'paths': sorted(original['source_paths'] + original['check_paths']), 'complete': False}
    else:
        assert repaired['session']['dependencies'] == {
            'paths': sorted(changed['source_paths'] + changed['check_paths']), 'complete': False}
    assert repaired['session']['paths'] == row['paths']
    assert call(forge, capsys, *check_args(root, row, planning, SECTION))[0] == 0


@pytest.mark.parametrize('writer_first', [False, True], ids=['reader-first', 'writer-first'])
def test_second_arrival_and_later_reader_check_observe_declared_overlap(
        forge, capsys, repositories, tmp_path, writer_first):
    root, peer_root = join_second(forge, capsys, repositories)
    planning = make_plan(tmp_path / 'private-plan')
    declare(planning, value=contract(**INPUTS))
    if writer_first:
        writer = team(forge, capsys, peer_root, 'start', '--task', 'Edit API', '--path', 'lib/api.py')
        reader = start(forge, capsys, root, planning)
        assert_warning(reader, writer['session'], 'reads_peer_writes')
    else:
        reader = start(forge, capsys, root, planning)
        assert reader['dependency_awareness']['warnings'] == []
        writer = team(forge, capsys, peer_root, 'start', '--task', 'Edit API', '--path', 'lib/api.py')
        assert_warning(writer, reader['session'], 'writes_peer_reads')
    assert reader['clearance'] and writer['clearance']  # Advisory inputs do not reserve writes.
    code, checked = call(forge, capsys, *check_args(root, reader['session'], planning, SECTION))
    assert code == 0 and checked['clearance'], checked
    assert_warning(checked, writer['session'], 'reads_peer_writes')


def test_warning_uses_peer_from_accepted_cas_retry(forge, capsys, repositories, tmp_path, monkeypatch):
    root, peer_root = join_second(forge, capsys, repositories)
    peer = team(forge, capsys, peer_root, 'start', '--task', 'Peer planning')['session']
    planning = make_plan(tmp_path / 'private-plan')
    declare(planning, value=contract(**INPUTS))
    original_push = forge.team_git.GitBoard._push
    attempts = []
    def contend(transport, receipt):
        if transport.repo.root == root.resolve():
            attempts.append(receipt.copy())
            if len(attempts) == 1:
                other = client(forge, peer_root)
                current = other.read()
                changed = deepcopy(current.board)
                changed['sessions'][peer['id']].update(paths=['lib/api.py'], state='working')
                other.write(changed, current.revision, lambda receipt: None)
        return original_push(transport, receipt)
    monkeypatch.setattr(forge.team_git.GitBoard, '_push', contend)
    result = start(forge, capsys, root, planning)
    assert len(attempts) == 2 and attempts[0]['expected'] != attempts[1]['expected']
    assert result['revision'] == client(forge, root).read().revision
    assert_warning(result, peer, 'reads_peer_writes')




def test_saved_current_record_keeps_warning_separate_from_unclaimed_next_entry(
        forge, capsys, repositories, tmp_path):
    root, peer_root = join_second(forge, capsys, repositories)
    planning = two_sections(tmp_path / 'private-plan')
    declared = contract(**INPUTS)
    declare(planning, SECTION, declared)
    inputs(root, declared)
    start(forge, capsys, root, planning)
    writer = team(forge, capsys, peer_root, 'start', '--task', 'Edit API', '--path', 'lib/api.py')['session']
    assert setup(forge, capsys, planning, root)[0] == 0
    for stage in ('baseline', 'candidate'):
        assert verify(forge, capsys, planning, root, '--section', SECTION, '--stage', stage,
                      '--', sys.executable, '-B', 'checks/api.py')[0] == 0
    code, result = record(forge, capsys, planning, root)
    assert code == 0 and result['success'] and result['recorded'], result
    assert_warning(result['team'], writer, 'reads_peer_writes')
    assert result['next_section'] == SECOND
    assert not result['entry']['success']
    assert result['entry']['team']['error_code'] == 'team-binding-required'
    assert 'record' not in result['entry']['commands']
    saved = forge.state.load_implementation_state(planning)['completed_sections'][SECTION]
    assert saved == result['record']
    assert 'team' not in saved and 'dependency_awareness' not in saved
    assert saved['compatibility']['candidate']['outcome'] == 'passed'
