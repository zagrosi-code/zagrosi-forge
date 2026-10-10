"""Declared dependency warnings retain bounded witnesses and honest coverage."""
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

from forge_test_helpers import load_zagrosi_module
from test_team_state import FIRST, SECOND, board, make_link, session


NOW = 1791367200
MISSING = object()


@pytest.fixture
def forge():
    return load_zagrosi_module()


def row(number, writes=(), reads=MISSING, *, complete=True, **changes):
    value = session(checkout_id=f'{number + 100:032x}', generation=f'{number + 200:032x}',
                    name=f'Engineer {number}', paths=list(writes), updated_at=NOW, **changes)
    if reads is not MISSING:
        value['dependencies'] = {'paths': list(reads), 'complete': complete}
    return value


def observed_board(rows):
    return {**board(**rows), 'version': 2}


def project(forge, value, root, identity=FIRST, *, offline=False):
    return forge.team_dependencies.project(value, identity, root=root, now=NOW, offline=offline)


def warning(identity, entry, direction, dependency, write, *, basis='portable_path', aliases=None):
    return {'session_id': identity, 'name': entry['name'], 'stale': NOW - entry['updated_at'] > 3600,
            'direction': direction, 'dependency_path': dependency, 'write_path': write,
            'basis': basis, 'alias_paths': aliases}


@pytest.mark.parametrize('peer_state', ['handoff', 'blocked'])
def test_both_directions_keep_stale_same_person_peer_and_exclude_only_actor(forge, tmp_path, peer_state):
    actor = row(1, ['result.py'], ['lib/api.py', 'result.py'])
    peer = row(2, ['lib/api.py'], ['result.py'], state=peer_state)
    peer['updated_at'] = NOW - 3601
    assert actor['participant_id'] == peer['participant_id']
    value = observed_board({FIRST: actor, SECOND: peer})
    before = deepcopy(value)
    result = project(forge, value, tmp_path)
    assert result == {
        'status': 'observed', 'reason': None,
        'warnings': [warning(SECOND, peer, 'reads_peer_writes', 'lib/api.py', 'lib/api.py'),
                     warning(SECOND, peer, 'writes_peer_reads', 'result.py', 'result.py')],
        'omitted_warnings': 0, 'unknown_sessions': 0, 'alias_issues': {'count': 0, 'first': None},
    }
    assert value == before


def test_shared_read_inputs_and_independent_writes_are_not_warning_groups(forge, tmp_path):
    value = observed_board({FIRST: row(1, ['result-a.py'], ['contract.py']),
                            SECOND: row(2, ['result-b.py'], ['contract.py'])})
    result = project(forge, value, tmp_path)
    assert result['status'] == 'observed'
    assert result['warnings'] == [] and result['omitted_warnings'] == 0
    assert result['unknown_sessions'] == 0 and result['alias_issues'] == {'count': 0, 'first': None}


@pytest.mark.parametrize('dependency,write,overlap', [
    ('lib/api', 'LIB/api/child.py', True),
    ('lib/api/child.py', 'LIB/api', True),
    ('café/api.py', 'CAFE\u0301/API.PY', True),
    ('lib/api', 'lib/api-extra/child.py', False),
])
def test_portable_matching_uses_component_ancestry_in_both_directions(forge, tmp_path, dependency, write, overlap):
    actor = row(1, ['result.py'], [dependency])
    peer = row(2, [write], ['unrelated.py'])
    result = project(forge, observed_board({FIRST: actor, SECOND: peer}), tmp_path)
    expected = [warning(SECOND, peer, 'reads_peer_writes', dependency, write)] if overlap else []
    assert result['warnings'] == expected
    assert result['status'] == 'observed' and result['omitted_warnings'] == 0


@pytest.mark.parametrize('hard', [False, True], ids=['local-symlink', 'local-hardlink'])
def test_local_alias_and_inode_witnesses_preserve_original_paths(forge, tmp_path, hard):
    original, alias = tmp_path / 'original.py', tmp_path / 'alias.py'
    original.write_text('shared')
    make_link(alias, original, hard=hard)
    actor = row(1, ['result.py'], ['alias.py'])
    peer = row(2, ['original.py'], ['unrelated.py'])
    value = observed_board({FIRST: actor, SECOND: peer})
    before = deepcopy(value)
    result = project(forge, value, tmp_path)
    assert result['warnings'] == [warning(
        SECOND, peer, 'reads_peer_writes', 'alias.py', 'original.py',
        basis='local_inode' if hard else 'local_alias',
        aliases=None if hard else {'dependency': 'original.py', 'write': 'original.py'})]
    assert result['status'] == 'observed' and result['alias_issues']['count'] == 0
    assert value == before


def test_local_alias_observations_do_not_survive_into_next_projection(forge, tmp_path):
    for name in ('old.py', 'new.py'):
        (tmp_path / name).write_text(name)
    alias = tmp_path / 'alias.py'
    make_link(alias, tmp_path / 'old.py')
    third = f'{4:032x}'
    value = observed_board({FIRST: row(1, ['result.py'], ['alias.py']),
                            SECOND: row(2, ['old.py'], ['unrelated.py']),
                            third: row(3, ['new.py'], ['unrelated.py'])})
    assert [item['session_id'] for item in project(forge, value, tmp_path)['warnings']] == [SECOND]
    alias.unlink()
    make_link(alias, tmp_path / 'new.py')
    result = project(forge, value, tmp_path)
    assert [item['session_id'] for item in result['warnings']] == [third]
    assert result['warnings'][0]['alias_paths'] == {'dependency': 'new.py', 'write': 'new.py'}


def test_invalid_peer_aliases_keep_retained_matches_and_count_each_session_path_once(forge, tmp_path):
    root, outside = tmp_path / 'repo', tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    for name in ('escape-a', 'escape-b'):
        make_link(root / name, outside)
    actor = row(1, ['result.py'], ['retained/source.py'])
    peer = row(2, ['escape-a', 'retained/source.py'], ['escape-a', 'escape-b'])
    value = observed_board({FIRST: actor, SECOND: peer})
    before = deepcopy(value)
    result = project(forge, value, root)
    assert result['status'] == 'partial' and result['reason'] is None
    assert result['warnings'] == [warning(SECOND, peer, 'reads_peer_writes',
                                          'retained/source.py', 'retained/source.py')]
    assert result['unknown_sessions'] == 0
    assert result['alias_issues'] == {'count': 2, 'first': {'session_id': SECOND, 'path': 'escape-a'}}
    assert value == before


def test_unknown_session_count_includes_actor_without_erasing_known_overlap(forge, tmp_path):
    third, fourth = f'{4:032x}', f'{5:032x}'
    actor = row(1, ['result.py'], ['lib/api.py'], complete=False)
    missing = row(2, ['lib/api.py'])
    partial = row(3, ['other.py'], ['unused.py'], complete=False)
    known = row(4, [], ['unused.py'])
    value = observed_board({FIRST: actor, SECOND: missing, third: partial, fourth: known})
    result = project(forge, value, tmp_path)
    assert result['status'] == 'partial' and result['reason'] is None
    assert result['unknown_sessions'] == 3
    assert result['warnings'] == [warning(SECOND, missing, 'reads_peer_writes', 'lib/api.py', 'lib/api.py')]
    assert result['alias_issues'] == {'count': 0, 'first': None}


def test_warning_limit_counts_groups_not_pairs_and_orders_witnesses_stably(forge, tmp_path):
    actor = row(1, ['result.py'], ['lib', 'lib/1.py'])
    peers = [(f'{number + 2:032x}', row(number + 1,
              [f'lib/{number}.py', f'lib/{number}.py/child'], ['result.py', 'result.py/child']))
             for number in range(1, 8)]
    value = observed_board(dict([*reversed(peers), (FIRST, actor)]))
    before = deepcopy(value)
    expected = []
    for number, (identity, peer) in enumerate(peers, 1):
        expected += [warning(identity, peer, 'reads_peer_writes', 'lib', f'lib/{number}.py'),
                     warning(identity, peer, 'writes_peer_reads', 'result.py', 'result.py')]
    result = project(forge, value, tmp_path)
    assert result['warnings'] == expected[:5]
    assert result['omitted_warnings'] == 9
    assert result['status'] == 'observed' and result['unknown_sessions'] == 0
    reordered = deepcopy(value)
    reordered['sessions'] = dict(reversed(list(reordered['sessions'].items())))
    assert project(forge, reordered, tmp_path) == result
    assert value == before


@pytest.mark.parametrize('version,identity,offline,reason', [
    (1, None, True, 'offline'),
    (1, None, False, 'protocol-v1'),
    (2, None, False, 'no-session'),
    (None, None, True, 'offline'),
])
def test_unavailable_precedence_does_not_claim_measured_empty_coverage(forge, tmp_path, monkeypatch,
                                                                   version, identity, offline, reason):
    value = None if version is None else {**board(**{FIRST: row(1, ['result.py'])}), 'version': version}
    def forbidden_observation(*args, **kwargs):
        pytest.fail('Unavailable projection observed local aliases')
    state = forge.team_state
    monkeypatch.setattr(state, 'normalize_paths', forbidden_observation)
    monkeypatch.setattr(state, '_inode', forbidden_observation)
    assert project(forge, value, tmp_path, identity, offline=offline) == {
        'status': 'unavailable', 'reason': reason, 'warnings': [], 'omitted_warnings': None,
        'unknown_sessions': None, 'alias_issues': {'count': None, 'first': None},
    }


def test_repeated_literals_share_observations_within_one_projection(forge, tmp_path, monkeypatch):
    baseline = observed_board({FIRST: row(1, ['owned.py'], ['shared/a.py', 'shared/b.py']),
                               SECOND: row(2, [], ['shared/a.py', 'shared/b.py'])})
    expanded = deepcopy(baseline)
    expanded['sessions'].update({f'{number + 1:032x}': row(number, [], ['shared/a.py', 'shared/b.py'])
                                 for number in (3, 4)})
    literals = {'owned.py', 'shared/a.py', 'shared/b.py'}
    for literal in literals:
        path = tmp_path / literal
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(literal)
    # Load before instrumentation; both calls have identical distinct literals.
    matcher = forge.team_dependencies
    root = tmp_path.resolve()
    resolve, stat = Path.resolve, Path.stat
    def measured(value):
        observed = Counter()
        def remember(method, path):
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                return  # Runtime/import paths outside the fixture are not this claim.
            observed[(method, relative)] += 1
        def observe_resolve(path, *args, **kwargs):
            remember('resolve', path)
            return resolve(path, *args, **kwargs)
        def observe_stat(path, *args, **kwargs):
            remember('stat', path)
            return stat(path, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path, 'resolve', observe_resolve)
            patch.setattr(Path, 'stat', observe_stat)
            result = matcher.project(value, FIRST, root=root, now=NOW)
        return result, observed
    result, original_observations = measured(baseline)
    repeated, repeated_observations = measured(expanded)
    assert result['warnings'] == [] and result['status'] == 'observed'
    assert repeated == result
    assert original_observations  # This fixture actually observes local paths.
    assert repeated_observations == original_observations
