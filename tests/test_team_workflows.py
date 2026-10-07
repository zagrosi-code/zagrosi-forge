"""Team reservations gate mutable work without changing solo evidence contracts."""
import json
from pathlib import Path
import sys

import pytest

from forge_test_helpers import load_zagrosi_module
from test_compact_plan import SECTION, make_plan
from test_team_collaboration import git, repositories
from test_workflow_admission import two_sections


@pytest.fixture
def forge():
    return load_zagrosi_module()


def call(forge, capsys, *args):
    code = forge.entrypoint.main([str(arg) for arg in args])
    return code, json.loads(capsys.readouterr().out)


def team(forge, capsys, root, action, *args):
    code, result = call(forge, capsys, 'team', action, '--target-dir', root, *args)
    assert code == 0, result
    return result


def reserve(forge, capsys, root, planning, section=None):
    extra = ['--section', section] if section else []
    return team(forge, capsys, root, 'start', '--task', 'Implement labels', '--host', 'codex',
                '--planning-dir', planning, *extra)['session']


def setup(forge, capsys, planning, target, *extra):
    return call(forge, capsys, 'implement-setup', '--sections-dir', planning / 'sections',
                '--target-dir', target, '--flight', 'off', *extra)


def verify(forge, capsys, planning, target, *extra):
    return call(forge, capsys, 'implement-verify', '--planning-dir', planning,
                '--target-dir', target, *extra)


def record(forge, capsys, planning, target, *extra):
    return call(forge, capsys, 'implement-record-section', '--sections-dir', planning / 'sections',
                '--target-dir', target, '--section', SECTION, '--review-status', 'pass',
                '--verification-source', 'inspection', '--verification-outcome', 'passed',
                '--verification', 'Inspected requested behavior and callers.', '--flight', 'off', *extra)


def files(path):
    return {item.relative_to(path): item.read_bytes() for item in path.rglob('*')
            if item.is_file() and not item.name.endswith('.lock')}


def configure(forge, capsys, root):
    team(forge, capsys, root, 'init', '--remote', 'origin', '--name', 'Alex')


@pytest.mark.parametrize('action', ['update', 'check', 'finish'])
def test_session_commands_require_explicit_generation(forge, capsys, action):
    parser = forge.cli.build_parser()
    arguments = ['team', action, '--session', 'a' * 32]
    if action == 'finish':
        arguments += ['--note', 'Reviewed delivery']
    with pytest.raises(SystemExit) as failed:
        parser.parse_args(arguments)
    assert failed.value.code == 2
    assert '--generation' in capsys.readouterr().err
    parsed = parser.parse_args([*arguments, '--generation', 'b' * 32])
    assert parsed.generation == 'b' * 32


def test_recovery_requires_reviewed_revision_without_current_generation(forge):
    parsed = forge.cli.build_parser().parse_args([
        'team', 'recover', '--session', 'a' * 32, '--expect', 'f' * 40,
        '--reason', 'Explicit handoff agreement',
    ])
    assert parsed.expect == 'f' * 40


def test_plan_scope_translates_subdirectory_paths_and_binds_target(forge, tmp_path):
    root = tmp_path / 'repo'
    target = root / 'packages/api'
    target.mkdir(parents=True)
    planning = make_plan(tmp_path / 'external-plan')
    key, paths = forge.team_workflow.plan_scope(planning, target, root, SECTION)
    assert paths == ['packages/api/src/labels.py', 'packages/api/tests/test_labels.py']
    other, _ = forge.team_workflow.plan_scope(planning, root, root, SECTION)
    full, _ = forge.team_workflow.plan_scope(planning, target, root)
    assert len({key, other, full}) == 3
    assert forge.team_workflow.plan_scope(planning / '.', target, root, SECTION) == (key, paths)


@pytest.mark.parametrize('relative', ['plans/current', 'packages/api/plans/current', '.'])
def test_in_repository_planning_directory_is_reserved_at_repository_root(forge, tmp_path, relative):
    root = tmp_path / 'repo'
    target = root / 'packages/api'
    target.mkdir(parents=True)
    planning = make_plan(root / relative)
    expected = sorted({relative, 'packages/api/src/labels.py', 'packages/api/tests/test_labels.py'})
    for section in (None, SECTION):
        _, paths = forge.team_workflow.plan_scope(planning, target, root, section)
        assert paths == expected


def test_plan_scope_rejects_outside_target_and_unknown_section(forge, tmp_path):
    planning = make_plan(tmp_path / 'plan')
    root = tmp_path / 'repo'
    root.mkdir()
    for target, section in [(tmp_path / 'outside', SECTION), (root, 'section-99-unknown')]:
        with pytest.raises(forge.team_state.TeamError):
            forge.team_workflow.plan_scope(planning, target, root, section)


def test_solo_entry_and_setup_do_not_add_team_fields(forge, capsys, tmp_path):
    planning = make_plan(tmp_path / 'plan')
    target = tmp_path / 'target'
    target.mkdir()
    code, result = setup(forge, capsys, planning, target)
    assert code == 0 and 'team' not in result
    code, result = call(forge, capsys, 'next-section', '--planning-dir', planning)
    assert code == 0 and 'team' not in result


def test_marker_without_join_blocks_before_activation(forge, capsys, repositories, tmp_path):
    first, second, _ = repositories
    configure(forge, capsys, first)
    (second / '.forge').mkdir()
    (second / '.forge/team.json').write_bytes((first / '.forge/team.json').read_bytes())
    planning = make_plan(tmp_path / 'plan')
    before = files(planning)
    code, result = setup(forge, capsys, planning, second)
    assert code == 1 and not result['success']
    assert files(planning) == before
    assert result['commands']['team_join']


def test_joined_entry_requires_binding_and_returns_start_arguments(forge, capsys, repositories, tmp_path):
    first, _, _ = repositories
    configure(forge, capsys, first)
    planning = make_plan(tmp_path / 'plan')
    code, result = call(forge, capsys, 'next-section', '--planning-dir', planning, '--target-dir', first)
    assert code == 1 and not result['success']
    assert result['commands']['team_start']
    assert 'record' not in result['commands']
    assert not (planning / 'implementation').exists()


@pytest.mark.parametrize('host', ['codex', 'claude'])
@pytest.mark.parametrize('shared', [False, True], ids=['ordinary-plan', 'prepared-plan'])
def test_missing_binding_reuses_owned_task_without_losing_identity_or_scope(
        forge, capsys, repositories, tmp_path, host, shared):
    first, _, _ = repositories
    configure(forge, capsys, first)
    if shared:
        from test_team_plans import commit_plan
        canonical = commit_plan(first)
        planning = Path(team(forge, capsys, first, 'prepare', '--planning-dir', canonical)['planning_dir'])
    else:
        planning = make_plan(tmp_path / 'plan')
    session = team(forge, capsys, first, 'start', '--task', 'Existing feature task', '--host', host,
                   '--path', 'src', '--path', 'tests', '--path', 'notes')['session']
    before = files(planning)
    revision = team(forge, capsys, first, 'status')['revision']

    code, blocked = setup(forge, capsys, planning, first)

    assert code == 1 and blocked['error_code'] == 'team-binding-required', blocked
    assert files(planning) == before
    assert team(forge, capsys, first, 'status')['revision'] == revision
    assert 'team_start' not in blocked['commands']
    repair = blocked['commands']['team_update']
    assert repair[repair.index('--session') + 1] == session['id']
    assert repair[repair.index('--generation') + 1] == session['generation']
    assert '--host' not in repair
    code, repaired = call(forge, capsys, *repair[2:])
    assert code == 0, repaired
    current = repaired['session']
    assert {key: current[key] for key in ('id', 'generation', 'host', 'task')} == {
        key: session[key] for key in ('id', 'generation', 'host', 'task')}
    assert set(session['paths']) <= set(current['paths'])
    assert len(repaired['sessions']) == 1
    assert setup(forge, capsys, planning, first)[0] == 0


def test_missing_binding_offers_explicit_choices_for_multiple_owned_tasks(
        forge, capsys, repositories, tmp_path):
    first, _, _ = repositories
    configure(forge, capsys, first)
    planning = make_plan(tmp_path / 'plan')
    sessions = [team(forge, capsys, first, 'start', '--task', task, '--host', host)['session']
                for task, host in [('Implement labels', 'codex'), ('Review labels', 'claude')]]
    before = files(planning)

    code, blocked = setup(forge, capsys, planning, first)

    assert code == 1 and blocked['error_code'] == 'team-binding-required', blocked
    assert files(planning) == before
    assert 'team_start' not in blocked['commands']
    repairs = [argv for argv in blocked['commands'].values() if argv[2:4] == ['team', 'update']]
    assert {argv[argv.index('--session') + 1] for argv in repairs} == {row['id'] for row in sessions}
    candidates = blocked['details']['candidates']
    assert {(row['id'], row['generation'], row['host'], row['task']) for row in candidates} == {
        (row['id'], row['generation'], row['host'], row['task']) for row in sessions}
    selected = sessions[1]
    repair = next(argv for argv in repairs if argv[argv.index('--session') + 1] == selected['id'])
    code, repaired = call(forge, capsys, *repair[2:])
    assert code == 0 and repaired['session']['generation'] == selected['generation'], repaired
    assert repaired['session']['host'] == selected['host']
    untouched = next(row for row in repaired['sessions'] if row['id'] == sessions[0]['id'])
    assert untouched['paths'] == [] and untouched['state'] == 'planning'
    assert setup(forge, capsys, planning, first)[0] == 0


def test_missing_binding_does_not_offer_a_peer_task_as_local_repair(forge, capsys, repositories, tmp_path):
    first, second, _ = repositories
    configure(forge, capsys, first)
    (second / '.forge').mkdir()
    (second / '.forge/team.json').write_bytes((first / '.forge/team.json').read_bytes())
    team(forge, capsys, second, 'join', '--name', 'Blair')
    peer = team(forge, capsys, second, 'start', '--task', 'Peer planning', '--host', 'claude')['session']
    planning = make_plan(tmp_path / 'plan')

    code, blocked = setup(forge, capsys, planning, first)

    assert code == 1 and blocked['error_code'] == 'team-binding-required', blocked
    assert blocked['commands']['team_start']
    assert all(peer['id'] not in argv for argv in blocked['commands'].values())


@pytest.mark.parametrize('depth', ['lean', 'standard', 'deep'])
@pytest.mark.parametrize('scoped', [False, True], ids=['full-plan', 'section'])
def test_bound_claim_admits_setup_and_targeted_verification(forge, capsys, repositories, tmp_path, depth, scoped):
    first, _, _ = repositories
    configure(forge, capsys, first)
    planning = make_plan(tmp_path / 'plan', depth)
    reserve(forge, capsys, first, planning, SECTION if scoped else None)
    code, result = setup(forge, capsys, planning, first)
    assert code == 0, result
    assert result['team']['success']
    code, result = verify(forge, capsys, planning, first, '--section', SECTION,
                          '--', sys.executable, '-c', 'pass')
    assert code == 0, result


def test_changed_scope_blocks_before_setup_writes(forge, capsys, repositories, tmp_path):
    first, _, _ = repositories
    configure(forge, capsys, first)
    planning = make_plan(tmp_path / 'plan')
    reserve(forge, capsys, first, planning, SECTION)
    section = planning / 'sections' / f'{SECTION}.md'
    section.write_text(section.read_text().replace('src/labels.py\n', 'src/labels.py\nsrc/extra.py\n'))
    before = files(planning)
    code, result = setup(forge, capsys, planning, first)
    assert code == 1 and result['commands']['team_update']
    assert files(planning) == before


def test_peer_planning_claim_blocks_setup_and_scope_repair_without_mutation(forge, capsys, repositories):
    first, second, _ = repositories
    configure(forge, capsys, first)
    (second / '.forge').mkdir()
    (second / '.forge/team.json').write_bytes((first / '.forge/team.json').read_bytes())
    team(forge, capsys, second, 'join', '--name', 'Blair')
    planning = make_plan(first / 'plans/current')
    session = reserve(forge, capsys, first, planning, SECTION)
    # Retain the binding while reproducing an older claim containing only source paths.
    source_paths = ['src/labels.py', 'tests/test_labels.py']
    source_args = [value for path in source_paths for value in ('--path', path)]
    team(forge, capsys, first, 'update', '--session', session['id'], '--generation', session['generation'],
         *source_args)
    peer = team(forge, capsys, second, 'start', '--task', 'Review shared plan evidence',
                '--path', 'plans/current/implementation')['session']
    revision = team(forge, capsys, first, 'status')['revision']
    before = files(planning)
    code, blocked = setup(forge, capsys, planning, first)
    assert code == 1 and blocked['error_code'] == 'team-scope-changed', blocked
    assert files(planning) == before
    repair = blocked['commands']['team_update']
    code, conflict = call(forge, capsys, *repair[2:])
    assert code == 1 and conflict['error_code'] == 'team-conflict', conflict
    assert team(forge, capsys, first, 'status')['revision'] == revision
    assert files(planning) == before
    team(forge, capsys, second, 'finish', '--session', peer['id'], '--generation', peer['generation'],
         '--note', 'Plan evidence review complete')
    code, repaired = call(forge, capsys, *repair[2:])
    assert code == 0, repaired
    assert set(source_paths + ['plans/current']) <= set(repaired['session']['paths'])
    assert setup(forge, capsys, planning, first)[0] == 0


@pytest.mark.parametrize('stage', [None, 'baseline', 'candidate'])
def test_unavailable_board_never_executes_or_overwrites_receipts(forge, capsys, repositories, tmp_path, stage):
    first, _, remote = repositories
    configure(forge, capsys, first)
    planning = make_plan(tmp_path / 'plan')
    reserve(forge, capsys, first, planning, SECTION)
    saved = planning / 'implementation/verification' / f'{SECTION}.json'
    saved.parent.mkdir(parents=True)
    saved.write_text('{"existing":"retain verification"}\n')
    before = files(planning)
    remote.rename(remote.with_name('unavailable.git'))
    marker = tmp_path / 'executed'
    extra = ['--stage', stage] if stage else []
    code, result = verify(forge, capsys, planning, first, '--section', SECTION, *extra,
                          '--', sys.executable, '-c', f'from pathlib import Path; Path({str(marker)!r}).touch()')
    assert code == 1 and not result['success']
    assert not marker.exists()
    assert files(planning) == before


@pytest.mark.parametrize('combined', [False, True], ids=['integration-only', 'section-and-integration'])
def test_integration_requires_full_plan_binding(forge, capsys, repositories, tmp_path, combined):
    first, _, _ = repositories
    configure(forge, capsys, first)
    planning = make_plan(tmp_path / 'plan')
    session = reserve(forge, capsys, first, planning, SECTION)
    extra = ['--section', SECTION, '--integration'] if combined else []
    marker = tmp_path / 'executed'
    command = ['--', sys.executable, '-c', f'from pathlib import Path; Path({str(marker)!r}).touch()']
    code, result = verify(forge, capsys, planning, first, *extra, *command)
    assert code == 1 and not marker.exists()
    assert not (planning / 'implementation/verification').exists()
    team(forge, capsys, first, 'update', '--session', session['id'], '--generation', session['generation'],
         '--planning-dir', planning)
    code, result = verify(forge, capsys, planning, first, *extra, *command)
    assert code == 0 and marker.exists(), result


def test_record_checks_extra_changed_paths_even_with_flight_off(forge, capsys, repositories, tmp_path):
    first, _, _ = repositories
    configure(forge, capsys, first)
    planning = make_plan(tmp_path / 'plan')
    reserve(forge, capsys, first, planning, SECTION)
    assert setup(forge, capsys, planning, first)[0] == 0
    before = files(planning)
    code, result = record(forge, capsys, planning, first, '--file', 'unclaimed.txt')
    assert code == 1 and not result['success']
    assert files(planning) == before


def test_record_publication_survives_unclaimed_successor(forge, capsys, repositories, tmp_path):
    first, _, _ = repositories
    configure(forge, capsys, first)
    planning = two_sections(tmp_path / 'plan')
    reserve(forge, capsys, first, planning, SECTION)
    code, result = record(forge, capsys, planning, first)
    assert code == 0 and result['success'] and result['recorded'], result
    assert result['next_section'] == 'section-02-consumer'
    assert not result['entry']['success']
    assert 'record' not in result['entry']['commands']
    assert SECTION in forge.state.load_implementation_state(planning)['completed_sections']


def test_setup_reuses_one_board_observation_but_refreshes_next_command(forge, capsys, repositories, tmp_path, monkeypatch):
    first, _, _ = repositories
    configure(forge, capsys, first)
    planning = make_plan(tmp_path / 'plan')
    reserve(forge, capsys, first, planning)
    original = forge.team_git.GitBoard.read
    observations = []
    def read(board, *args, **kwargs):
        observations.append(board)
        return original(board, *args, **kwargs)
    monkeypatch.setattr(forge.team_git.GitBoard, 'read', read)
    assert setup(forge, capsys, planning, first)[0] == 0
    assert len(observations) == 1
    assert call(forge, capsys, 'next-section', '--planning-dir', planning)[0] == 0
    assert len(observations) == 2


def test_subdirectory_binding_cannot_be_borrowed_by_another_target(forge, capsys, repositories, tmp_path):
    root, _, _ = repositories
    configure(forge, capsys, root)
    target = root / 'packages/api'
    target.mkdir(parents=True)
    planning = make_plan(tmp_path / 'external-plan')
    session = reserve(forge, capsys, target, planning, SECTION)
    assert session['paths'] == ['packages/api/src/labels.py', 'packages/api/tests/test_labels.py']
    assert setup(forge, capsys, planning, target)[0] == 0
    before = files(planning)
    code, result = setup(forge, capsys, planning, root)
    assert code == 1 and result['error_code'] == 'team-binding-required'
    assert files(planning) == before


def test_corrupt_binding_is_rejected_without_mutation(forge, capsys, repositories, tmp_path):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = make_plan(tmp_path / 'plan')
    reserve(forge, capsys, root, planning, SECTION)
    local = forge.team_config.LocalTeam(forge.team_git.Repository.discover(root))
    with local.locked():
        saved = local.load()
        checkout = local.checkout(saved)
        key, _ = forge.team_workflow.plan_scope(planning, root, root, SECTION)
        checkout['bindings'][key] = {'session_id': [], 'generation': 'broken'}
        local.path.write_bytes((json.dumps(saved) + '\n').encode())
    corrupt = local.path.read_bytes()
    before = files(planning)
    code, result = setup(forge, capsys, planning, root)
    assert code == 1 and result['error_code'] == 'team-local-state'
    assert files(planning) == before
    assert local.path.read_bytes() == corrupt


@pytest.mark.parametrize('state', ['planning', 'handoff'])
def test_announced_or_handed_off_work_has_no_editing_clearance(forge, capsys, repositories, tmp_path, state):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = make_plan(tmp_path / 'plan')
    session = reserve(forge, capsys, root, planning, SECTION)
    team(forge, capsys, root, 'update', '--session', session['id'], '--generation', session['generation'], '--state', state)
    before = files(planning)
    code, result = setup(forge, capsys, planning, root)
    assert code == 1 and result['error_code'] == 'team-no-reservation'
    assert files(planning) == before


def test_binding_repair_identifies_handoff_without_resuming(forge, capsys, repositories, tmp_path):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = make_plan(tmp_path / 'plan')
    session = team(forge, capsys, root, 'start', '--task', 'Handed-off implementation', '--host', 'claude',
                   '--path', 'src', '--path', 'tests')['session']
    team(forge, capsys, root, 'update', '--session', session['id'], '--generation', session['generation'],
         '--state', 'handoff')
    before = files(planning)

    code, blocked = setup(forge, capsys, planning, root)

    assert code == 1 and blocked['error_code'] == 'team-binding-required', blocked
    assert blocked['details']['candidates'] == [{key: session[key] for key in ('id', 'generation', 'host', 'task')}
                                               | {'state': 'handoff'}]
    assert 'team_resume' not in blocked['commands']
    repair = blocked['commands']['team_update']
    assert '--state' not in repair
    code, repaired = call(forge, capsys, *repair[2:])
    assert code == 0 and repaired['session']['state'] == 'handoff', repaired
    assert repaired['session']['generation'] == session['generation']
    code, still_blocked = setup(forge, capsys, planning, root)
    assert code == 1 and still_blocked['error_code'] == 'team-no-reservation', still_blocked
    assert files(planning) == before


def test_handoff_requires_explicit_resume_and_rejects_replaced_generation(forge, capsys, repositories, tmp_path):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = make_plan(tmp_path / 'plan')
    session = team(forge, capsys, root, 'start', '--task', 'Resume reviewed handoff', '--host', 'claude',
                   '--planning-dir', planning, '--path', 'notes')['session']
    team(forge, capsys, root, 'update', '--session', session['id'], '--generation', session['generation'],
         '--state', 'handoff')
    before = files(planning)

    code, blocked = setup(forge, capsys, planning, root)

    assert code == 1 and blocked['error_code'] == 'team-no-reservation', blocked
    assert files(planning) == before
    assert 'resume' in blocked['next_action'].lower()
    resume = blocked['commands']['team_resume']
    assert resume[resume.index('--state') + 1] == 'working'
    assert resume[resume.index('--session') + 1] == session['id']
    assert resume[resume.index('--generation') + 1] == session['generation']
    assert '--host' not in resume
    code, resumed = call(forge, capsys, *resume[2:])
    assert code == 0 and resumed['clearance'], resumed
    assert {key: resumed['session'][key] for key in ('id', 'generation', 'host', 'paths')} == {
        key: session[key] for key in ('id', 'generation', 'host', 'paths')}
    assert setup(forge, capsys, planning, root)[0] == 0

    status = team(forge, capsys, root, 'status')
    recovered = team(forge, capsys, root, 'recover', '--session', session['id'], '--expect', status['revision'],
                     '--reason', 'Explicit handoff generation change')['session']
    team(forge, capsys, root, 'update', '--session', recovered['id'], '--generation', recovered['generation'],
         '--state', 'handoff')
    before = files(planning)
    code, replaced = setup(forge, capsys, planning, root)
    assert code == 1 and replaced['error_code'] == 'team-ownership-lost', replaced
    assert 'team_resume' not in replaced['commands']
    assert files(planning) == before


def test_pending_publication_does_not_grant_guard_clearance(forge, capsys, repositories, tmp_path, monkeypatch):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = make_plan(tmp_path / 'plan')
    reserve(forge, capsys, root, planning, SECTION)
    context = forge.team.read_context(root)
    context['checkout']['pending'] = {'action': 'update', 'revision': 'a' * 40}
    monkeypatch.setattr(forge.team, 'read_context', lambda target: context)
    before = files(planning)
    code, result = setup(forge, capsys, planning, root)
    assert code == 1 and result['error_code'] == 'team-pending-write'
    assert files(planning) == before


def test_recovery_cannot_silently_refresh_an_old_plan_binding(forge, capsys, repositories, tmp_path):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = make_plan(tmp_path / 'plan')
    session = reserve(forge, capsys, root, planning, SECTION)
    status = team(forge, capsys, root, 'status')
    recovered = team(forge, capsys, root, 'recover', '--session', session['id'],
                     '--expect', status['revision'], '--reason', 'Explicit test handoff agreement')
    assert recovered['session']['generation'] != session['generation']
    before = files(planning)
    code, result = setup(forge, capsys, planning, root)
    assert code == 1 and result['error_code'] == 'team-ownership-lost'
    assert files(planning) == before


def test_branch_change_requires_rebind_but_normal_commit_does_not(forge, capsys, repositories, tmp_path):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = make_plan(tmp_path / 'plan')
    reserve(forge, capsys, root, planning, SECTION)
    git(root, 'commit', '--allow-empty', '-m', 'Normal progress')
    assert setup(forge, capsys, planning, root)[0] == 0
    git(root, 'checkout', '-b', 'another-task')
    before = files(planning)
    code, result = setup(forge, capsys, planning, root)
    assert code == 1 and result['error_code'] == 'team-checkout-changed'
    assert files(planning) == before


def test_scope_repair_keeps_full_plan_reservations(forge, capsys, repositories, tmp_path):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = two_sections(root / 'plans/current')
    successor = planning / 'sections/section-02-consumer.md'
    successor.write_text(successor.read_text().replace('labels.py', 'consumer.py'))
    reserved = reserve(forge, capsys, root, planning)
    assert 'src/consumer.py' in reserved['paths']
    assert 'plans/current' in reserved['paths']
    section = planning / 'sections' / f'{SECTION}.md'
    body = section.read_text()
    assert '- `src/labels.py`\n' in body
    section.write_text(body.replace('- `src/labels.py`\n', '- `src/labels.py`\n- `src/new.py`\n'))
    code, result = setup(forge, capsys, planning, root)
    assert code == 1, result
    update = result['commands']['team_update']
    assert '--section' not in update
    code, updated = call(forge, capsys, *update[2:])
    assert code == 0, updated
    assert 'src/consumer.py' in updated['session']['paths']
    assert 'src/new.py' in updated['session']['paths']
    assert set(reserved['paths']) <= set(updated['session']['paths'])
    assert setup(forge, capsys, planning, root)[0] == 0


def test_extra_record_path_repair_reserves_the_reported_path(forge, capsys, repositories, tmp_path):
    root, _, _ = repositories
    configure(forge, capsys, root)
    planning = make_plan(tmp_path / 'plan')
    reserve(forge, capsys, root, planning, SECTION)
    code, result = record(forge, capsys, planning, root, '--file', 'unclaimed.txt')
    assert code == 1, result
    code, updated = call(forge, capsys, *result['commands']['team_update'][2:])
    assert code == 0 and 'unclaimed.txt' in updated['session']['paths'], updated
    assert record(forge, capsys, planning, root, '--file', 'unclaimed.txt')[0] == 0
