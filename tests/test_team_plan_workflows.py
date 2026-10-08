"""Prepared contract validation protects the existing mutable workflow boundaries."""
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from forge_test_helpers import load_zagrosi_module
from test_compact_plan import SECTION, make_plan
from test_compatibility_checks import block, contract
from test_team_git import git
from test_team_workflows import call, configure, record, repositories, reserve, setup, team, verify


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    forge = load_zagrosi_module()
    target = tmp_path / 'repo'
    target.mkdir()
    planning = make_plan(target / '.git/forge-plans/prepared')
    current = {'stale': False, 'descriptor': {'path': '.forge/plans/labels', 'digest': 'a' * 64,
                                            'commit': 'b' * 40, 'section': None}}
    api = ModuleType(f'{forge.package.__name__}.team_plans')

    def validate(path, selected_target=None):
        if path.resolve() != planning.resolve():
            return None
        if current['stale']:
            raise forge.team_state.TeamError('team-plan-stale', 'The canonical plan changed; prepare it again.')
        if selected_target is not None and selected_target.resolve() != target.resolve():
            raise forge.team_state.TeamError('team-plan-target', 'The prepared target changed.')
        return dict(current['descriptor'])

    api.validate = validate
    api.mutable_path = lambda root, destination: destination.is_relative_to(root / 'implementation')
    monkeypatch.setitem(sys.modules, api.__name__, api)
    monkeypatch.setattr(forge.package, 'team_plans', api, raising=False)
    return forge, planning, target, current


def inventory(path):
    return {str(item.relative_to(path)): item.read_bytes() if item.is_file() else None
            for item in path.rglob('*')}


def authoring_plan(path):
    planning = make_plan(path)
    index = planning / 'sections/index.md'
    marker, body = index.read_text(encoding='utf-8').split('END_FORGE_META -->\n', 1)
    section = planning / 'sections' / f'{SECTION}.md'
    (planning / 'claude-plan.md').write_text(
        marker + 'END_FORGE_META -->\n' + section.read_text(encoding='utf-8'), encoding='utf-8')
    index.write_text(body, encoding='utf-8')
    return planning


@pytest.fixture
def committed_prepared(tmp_path, capsys):
    forge = load_zagrosi_module()
    target = tmp_path / 'repo'
    git(tmp_path, 'init', str(target))
    git(target, 'config', 'commit.gpgsign', 'false')
    canonical = authoring_plan(target / '.forge/plans/shared')
    git(target, 'add', '.')
    git(target, 'commit', '-m', 'Review shared contract')
    code, result = call(forge, capsys, 'team', 'prepare', '--planning-dir', canonical,
                        '--target-dir', target)
    assert code == 0 and result['success'], result
    return forge, canonical, Path(result['planning_dir']), target


@pytest.mark.parametrize('command', ['write-governance-stubs', 'migrate'])
def test_prepared_authoring_helpers_reject_without_mutation(committed_prepared, capsys, command):
    forge, canonical, planning, target = committed_prepared
    canonical_before, prepared_before = inventory(canonical), inventory(planning)

    code, result = call(forge, capsys, command, '--planning-dir', planning)

    assert code == 1 and result['error_code'] == 'team-plan-readonly', result
    assert inventory(canonical) == canonical_before
    assert inventory(planning) == prepared_before
    code, result = call(forge, capsys, 'lint-plan', '--planning-dir', planning, '--strict')
    assert code == 0 and result['success'], result
    code, result = call(forge, capsys, 'team', 'prepare', '--planning-dir', canonical,
                        '--target-dir', target)
    assert code == 0 and result['planning_dir'] == str(planning), result


@pytest.mark.parametrize('command', ['write-governance-stubs', 'migrate'])
def test_ordinary_authoring_helpers_remain_available(tmp_path, capsys, command):
    forge = load_zagrosi_module()
    planning = authoring_plan(tmp_path / 'ordinary-plan')
    before = inventory(planning)

    code, result = call(forge, capsys, command, '--planning-dir', planning)

    assert code == 0 and result['success'], result
    for name in ('decisions.md', 'risk-register.md', 'traceability.md', 'quality-gates.md'):
        assert (planning / name).read_text(encoding='utf-8').strip()
    assert all(inventory(planning)[name] == content for name, content in before.items())
    if command == 'migrate':
        assert (planning / 'codex-plan.md').read_bytes() == (planning / 'claude-plan.md').read_bytes()


def test_prepared_generated_output_remains_available(committed_prepared, capsys):
    forge, canonical, planning, _ = committed_prepared
    canonical_before, prepared_before = inventory(canonical), inventory(planning)
    output = planning / 'implementation/context.md'

    code, result = call(forge, capsys, 'context-brief', '--planning-dir', planning, '--output', output)

    assert code == 0 and result['success'], result
    assert output.read_text(encoding='utf-8').strip()
    assert inventory(canonical) == canonical_before
    assert all(inventory(planning)[name] == content for name, content in prepared_before.items())
    code, result = call(forge, capsys, 'lint-plan', '--planning-dir', planning, '--strict')
    assert code == 0 and result['success'], result


@pytest.mark.parametrize('command', ['setup', 'progress', 'verify', 'packet', 'status'])
def test_stale_prepared_commands_reject_before_any_write(prepared, capsys, command):
    forge, planning, target, current = prepared
    current['stale'] = True
    before = inventory(planning)
    arguments = {
        'setup': ['implement-setup', '--sections-dir', planning / 'sections', '--target-dir', target, '--flight', 'off'],
        'progress': ['implement-progress', '--planning-dir', planning, '--section', SECTION, '--stage', 'started'],
        'verify': ['implement-verify', '--planning-dir', planning, '--target-dir', target, '--section', SECTION,
                   '--', sys.executable, '-c', "raise RuntimeError('must not execute')"],
        'packet': ['implementation-packet', '--planning-dir', planning, '--section', SECTION],
        'status': ['status', '--path', planning / 'sections' / f'{SECTION}.md'],
    }
    code, result = call(forge, capsys, *arguments[command])
    assert code == 1 and result['error_code'] == 'team-plan-stale', result
    assert inventory(planning) == before


@pytest.mark.parametrize('destination', ['outside', 'contract'])
def test_prepared_output_cannot_overwrite_shared_or_immutable_files(prepared, capsys, destination):
    forge, planning, target, _ = prepared
    output = target / 'result.md' if destination == 'outside' else planning / 'spec.md'
    before = inventory(planning)
    code, result = call(forge, capsys, 'context-brief', '--planning-dir', planning, '--output', output)
    assert code == 1 and result['error_code'] == 'team-plan-output', result
    assert inventory(planning) == before
    if destination == 'outside':
        assert not output.exists()


@pytest.mark.parametrize('destination', ['canonical', 'private'])
def test_quality_export_preserves_shared_and_private_contracts(prepared, capsys, destination):
    forge, planning, target, _ = prepared
    source = planning / 'spec.md' if destination == 'private' else target / '.forge/plans/labels/spec.md'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('Reviewed canonical source.\n', encoding='utf-8')
    before = source.read_bytes()
    code, result = call(forge, capsys, 'lint-plan', '--planning-dir', planning, '--export', source)
    assert code == 1 and result['error_code'] == 'team-plan-output', result
    assert source.read_bytes() == before


def test_prepared_plan_authoring_is_rejected_without_new_config(prepared, capsys):
    forge, planning, target, _ = prepared
    before = inventory(planning)
    code, result = call(forge, capsys, 'plan-setup', '--file', planning / 'spec.md',
                        '--target-dir', target, '--flight', 'off')
    assert code == 1 and result['error_code'] == 'team-plan-readonly', result
    assert inventory(planning) == before


@pytest.mark.parametrize('relative', ['references/contract.md', 'references/forge-plans/other/contract.md'])
def test_nested_prepared_contract_cannot_be_rewritten_by_authoring_helper(prepared, capsys, relative):
    forge, planning, _, _ = prepared
    source = planning / relative
    source.parent.mkdir(parents=True)
    source.write_text('- Must preserve label case.\n', encoding='utf-8')
    before = inventory(planning)
    code, result = call(forge, capsys, 'extract-requirements', '--file', source, '--write')
    assert code == 1 and result['error_code'] == 'team-plan-readonly', result
    assert inventory(planning) == before


def test_prepared_scope_reserves_only_code_and_validates_target(prepared):
    forge, planning, target, _ = prepared
    _, paths = forge.team_workflow.plan_scope(planning, target, target, SECTION)
    assert paths == ['src/labels.py', 'tests/test_labels.py']
    with pytest.raises(forge.team_state.TeamError, match='target'):
        forge.team_workflow.plan_scope(planning, target / 'other', target, SECTION)


@pytest.mark.parametrize('change', ['missing', 'path', 'digest', 'commit', 'section'])
def test_guard_requires_exact_prepared_contract_binding(prepared, monkeypatch, change):
    forge, planning, target, current = prepared
    descriptor = {**current['descriptor'], 'section': SECTION}
    if change == 'missing':
        descriptor = None
    else:
        descriptor[change] = {'path': 'different/plan', 'digest': 'c' * 64,
                              'commit': 'd' * 40, 'section': 'section-02-other'}[change]
    bind_guard(forge, planning, target, monkeypatch, descriptor)
    result = forge.team_workflow.guard(planning, target, SECTION)
    assert not result['success'] and result['error_code'] == 'team-plan-binding', result


def bind_guard(forge, planning, target, monkeypatch, descriptor, *, full=False):
    key, _ = forge.team_workflow.plan_scope(planning, target, target, None if full else SECTION)
    context = {'repo': SimpleNamespace(root=target),
               'checkout': {'bindings': {key: {'session_id': 'a' * 32, 'generation': 'b' * 32}}},
               'snapshot': SimpleNamespace(revision='c' * 40)}
    monkeypatch.setattr(forge.team, 'read_context', lambda target: context)
    monkeypatch.setattr(forge.team, 'check', lambda *args, **kwargs: {'session': {'plan': descriptor}})


@pytest.mark.parametrize('full', [False, True])
def test_matching_prepared_binding_and_full_plan_binding_cover_section(prepared, monkeypatch, full):
    forge, planning, target, current = prepared
    descriptor = {**current['descriptor'], 'section': None if full else SECTION}
    bind_guard(forge, planning, target, monkeypatch, descriptor, full=full)
    assert forge.team_workflow.guard(planning, target, SECTION)['success']


@pytest.mark.parametrize('full', [False, True], ids=['section-binding', 'full-plan-fallback'])
def test_guard_validates_once_and_rechecks_the_next_observation(prepared, monkeypatch, full):
    forge, planning, target, current = prepared
    descriptor = {**current['descriptor'], 'section': None if full else SECTION}
    bind_guard(forge, planning, target, monkeypatch, descriptor, full=full)
    validate = forge.package.team_plans.validate
    calls = []

    def observed(*args):
        calls.append(args)
        return validate(*args)

    monkeypatch.setattr(forge.package.team_plans, 'validate', observed)
    assert forge.team_workflow.guard(planning, target, SECTION)['success']
    assert calls == [(planning, target)]
    current['stale'] = True
    result = forge.team_workflow.guard(planning, target, SECTION)
    assert not result['success'] and result['error_code'] == 'team-plan-stale', result
    assert calls == [(planning, target), (planning, target)]


def test_public_plan_check_validates_once_and_rejects_a_changed_contract(
        repositories, capsys, monkeypatch):
    from test_team_plans import commit_plan

    forge = load_zagrosi_module()
    target, _, _ = repositories
    configure(forge, capsys, target)
    canonical = commit_plan(target)
    planning = Path(team(forge, capsys, target, 'prepare', '--planning-dir', canonical)['planning_dir'])
    session = reserve(forge, capsys, target, planning)
    validate = forge.team_plans.validate
    calls = []

    def observed(*args):
        calls.append(args)
        return validate(*args)

    monkeypatch.setattr(forge.team_plans, 'validate', observed)
    args = ['team', 'check', '--target-dir', target, '--planning-dir', planning,
            '--session', session['id'], '--generation', session['generation']]
    code, checked = call(forge, capsys, *args)
    assert code == 0 and checked['clearance'], checked
    assert calls == [(planning, target)]
    spec = canonical / 'spec.md'
    spec.write_text(spec.read_text(encoding='utf-8') + '\nChanged contract.\n', encoding='utf-8')
    before = inventory(planning)
    code, blocked = call(forge, capsys, *args)
    assert code == 1 and blocked['error_code'] == 'team-plan-stale', blocked
    assert calls == [(planning, target), (planning, target)]
    assert inventory(planning) == before


def test_prepared_contract_changes_invalidate_completion_snapshot(prepared):
    forge, planning, target, current = prepared
    before = forge.mutable_inputs.contract_snapshot(planning, SECTION, target_dir=target)
    current['descriptor']['digest'] = 'e' * 64
    after = forge.mutable_inputs.contract_snapshot(planning, SECTION, target_dir=target)
    assert before['contract'] != after['contract']
    current['stale'] = True
    with pytest.raises(ValueError, match='canonical plan changed'):
        forge.mutable_inputs.contract_snapshot(planning, SECTION, target_dir=target)


@pytest.mark.parametrize('kind', ['verification', 'completion'])
def test_snapshot_validates_once_and_rechecks_next_observation(prepared, monkeypatch, kind):
    forge, planning, target, current = prepared
    index = planning / 'sections/index.md'
    marker, rest = index.read_text().split('END_FORGE_META -->\n', 1)
    body = (planning / 'sections' / f'{SECTION}.md').read_text()
    (planning / 'codex-plan.md').write_text(marker + 'END_FORGE_META -->\n' + body, encoding='utf-8')
    index.write_text(rest.replace('END_MANIFEST', 'section-02-other\nEND_MANIFEST'), encoding='utf-8')
    (planning / 'sections/section-02-other.md').write_text(body.replace(SECTION, 'section-02-other'), encoding='utf-8')
    validate = forge.package.team_plans.validate
    calls = []

    def observed(*args):
        calls.append(args)
        return validate(*args)

    monkeypatch.setattr(forge.package.team_plans, 'validate', observed)
    def snapshot():
        return (forge.mutable_inputs.verification_snapshot(planning, target) if kind == 'verification' else
                forge.mutable_inputs.contract_snapshot(planning, SECTION, target_dir=target))

    assert snapshot()
    assert calls == [(planning, target)]
    current['stale'] = True
    with pytest.raises(ValueError, match='canonical plan changed'):
        snapshot()
    assert calls == [(planning, target), (planning, target)]


def test_setup_rechecks_before_publishing_configuration(prepared, monkeypatch, capsys):
    forge, planning, target, current = prepared
    monkeypatch.setattr(forge.compatibility, 'activate', lambda *args: current.update(stale=True))
    code, result = setup(forge, capsys, planning, target)
    assert code == 1 and not result['success'], result
    assert not (planning / 'implementation/zagrosi_implement_config.json').exists()
    assert not (planning / 'implementation/zagrosi_implement_state.json').exists()


def test_record_rechecks_after_postflight_before_completing(prepared, monkeypatch, capsys):
    forge, planning, target, current = prepared
    assert setup(forge, capsys, planning, target)[0] == 0

    def postflight(*args, **kwargs):
        current['stale'] = True
        return {'success': True}

    monkeypatch.setattr(forge.flights, 'implement_postflight_report', postflight)
    code, result = record(forge, capsys, planning, target, '--flight', 'strict')
    assert code == 1 and not result['success'], result
    state = json.loads((planning / 'implementation/zagrosi_implement_state.json').read_text())
    assert SECTION not in state['completed_sections']
    assert SECTION in state['pending_sections']


@pytest.mark.parametrize('stage', [None, 'baseline'])
def test_changed_canonical_contract_cannot_produce_passing_capture(prepared, monkeypatch, capsys, stage):
    forge, planning, target, current = prepared
    if stage:
        section = planning / 'sections' / f'{SECTION}.md'
        section.write_text(section.read_text() + block(contract()), encoding='utf-8')
        (target / 'source.py').write_text('value = 1\n', encoding='utf-8')
        (target / 'checks.py').write_text('assert True\n', encoding='utf-8')
        forge.compatibility.activate(planning, target, SECTION)

    def capture(command, *args):
        current['stale'] = True
        return {'source': 'captured', 'command': command, 'exit_code': 0, 'outcome': 'passed'}

    monkeypatch.setattr(forge.verification, '_capture', capture)
    extra = ['--stage', stage] if stage else []
    code, result = verify(forge, capsys, planning, target, '--section', SECTION, *extra,
                          '--', sys.executable, '-c', 'pass')
    assert code == 1 and not result['success'], result
    assert result['outcome'] == 'failed'
    receipt = json.loads((forge.compatibility.receipt_path(planning, SECTION) if stage else
                          forge.verification.receipt_path(planning, SECTION)).read_text())
    assert (receipt[stage] if stage else receipt)['outcome'] == 'failed'


def test_drift_after_pending_receipt_prevents_execution(prepared, monkeypatch, capsys):
    forge, planning, target, current = prepared
    write = forge.storage.write_json
    executed = []

    def write_pending(path, value):
        write(path, value)
        if value.get('outcome') == 'pending':
            current['stale'] = True

    def capture(command, *args):
        executed.append(command)
        return {'source': 'captured', 'command': command, 'exit_code': 0, 'outcome': 'passed'}

    monkeypatch.setattr(forge.storage, 'write_json', write_pending)
    monkeypatch.setattr(forge.verification, '_capture', capture)
    code, result = verify(forge, capsys, planning, target, '--section', SECTION,
                          '--', sys.executable, '-c', 'pass')
    assert code == 1 and not result['success'], result
    assert not executed
