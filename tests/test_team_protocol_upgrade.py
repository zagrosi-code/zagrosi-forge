"""Versioned collaboration records preserve identity and bound declared inputs."""
from copy import deepcopy
import json

import pytest

from forge_test_helpers import load_zagrosi_module
from test_team_state import FIRST, board, session


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
