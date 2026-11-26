"""A section group reserves its own edits and rechecks a portable Git identity."""
import json
import sys
from pathlib import Path

import pytest

from test_compact_plan import SECTION
from test_team_git import git
from test_team_plans import commit_plan
from test_team_workflows import call, configure, forge, repositories, team

SECOND = 'section-02-tables'
THIRD = 'section-03-api'


def group_plan(root):
    planning = commit_plan(root)
    index = planning / 'sections/index.md'
    original = (planning / 'sections' / f'{SECTION}.md').read_text()
    for name, stem in [(SECOND, 'tables'), (THIRD, 'api')]:
        index.write_text(index.read_text().replace('END_MANIFEST', f'{name}\nEND_MANIFEST'))
        (planning / 'sections' / f'{name}.md').write_text(original.replace('labels', stem))
    git(root, 'add', str(planning))
    git(root, 'commit', '-m', 'Group sections')
    return planning


def test_group_reservation(forge, repositories, capsys):
    root, _, _ = repositories
    configure(forge, capsys, root)
    canonical = group_plan(root)
    planning = Path(forge.team_plans.prepare(canonical, root)['planning_dir'])
    task = team(forge, capsys, root, 'start', '--task', 'Labels and tables',
                '--planning-dir', planning, '--sections', SECOND, SECTION)['session']
    assert task['paths'] == ['src/labels.py', 'src/tables.py', 'tests/test_labels.py', 'tests/test_tables.py']
    for name in [SECTION, SECOND]:
        assert forge.team_workflow.guard(planning, root, name)['success']
    assert not forge.team_workflow.guard(planning, root, THIRD)['success']
    assert not forge.team_workflow.guard(planning, root)['success']
    team(forge, capsys, root, 'update', '--session', task['id'], '--generation', task['generation'],
         '--planning-dir', planning, '--sections', SECTION)
    assert forge.team_workflow.guard(planning, root, SECTION)['success']
    assert not forge.team_workflow.guard(planning, root, SECOND)['success']


def test_group_handoff(forge, repositories, capsys, tmp_path):
    root, _, _ = repositories
    plan = group_plan(root)
    code, description = call(forge, capsys, 'section-group', 'create', '--target-dir', root,
                             '--planning-dir', plan, '--sections', SECOND, SECTION)
    assert code == 0, description
    other = tmp_path / 'receiving'
    git(root, 'worktree', 'add', '--detach', str(other), 'HEAD')
    handoff = tmp_path / 'group.json'
    handoff.write_text(json.dumps(description))
    flags = ['--target-dir', other, '--handoff', handoff]
    code, inspected = call(forge, capsys, 'section-group', 'inspect', *flags)
    assert code == 0 and inspected['handoff'] == description['handoff']
    code, verified = call(forge, capsys, 'section-group', 'verify', *flags, '--', sys.executable, '-c',
                          'from pathlib import Path; assert Path("README.md").read_text() == "Example\\n"')
    assert code == 0 and verified['verification']['returncode'] == 0, verified
    assert verified['verification']['started_at'] <= verified['verification']['finished_at']
    assert verified['handoff']['tree_sha'] == git(other, 'rev-parse', 'HEAD^{tree}').stdout.strip()
    code, mutated = call(forge, capsys, 'section-group', 'verify', *flags, '--', sys.executable, '-c',
                         'from pathlib import Path; Path("README.md").write_text("changed")')
    assert code == 1 and 'changed' in mutated['error'].lower()
    assert (other / 'README.md').read_text() == 'Example\n'
    handoff.write_text(json.dumps({'success': True, 'handoff': {**description['handoff'], 'tree_sha': '0'*40}}))
    assert call(forge, capsys, 'section-group', 'inspect', *flags)[0] == 1
    # Foreign check records cannot run commands or authorize verification.
    handoff.write_text(json.dumps({**description, 'verification': {'returncode': 0, 'command': ['untrusted']}}))
    code, failed = call(forge, capsys, 'section-group', 'verify', *flags, '--', sys.executable, '-c', 'exit(9)')
    assert code == 1 and failed['verification']['returncode'] == 9
    handoff.write_text(json.dumps(description))
    fp = other / plan.relative_to(root) / 'sections' / f'{SECTION}.md'
    fp.write_text(fp.read_text() + '\nchanged\n')
    assert call(forge, capsys, 'section-group', 'inspect', *flags)[0] == 1
    git(other, 'add', str(fp))
    git(other, 'commit', '-m', 'Changed plan')
    assert call(forge, capsys, 'section-group', 'inspect', *flags)[0] == 1


def test_group_rejects_unknown_and_repeated_members(forge, repositories, capsys):
    root, _, _ = repositories
    plan = group_plan(root)
    for names in ([SECOND, SECOND], ['section-04-unknown'], ['../outside']):
        code, result = call(forge, capsys, 'section-group', 'create', '--target-dir', root,
                            '--planning-dir', plan, '--sections', *names)
        assert code == 1 and not result['success']


@pytest.mark.parametrize('version', [1, 2])
def test_group_check_requires_membership_even_for_equal_paths(forge, repositories, capsys, monkeypatch, version):
    root, _, _ = repositories
    if version == 1:
        monkeypatch.setattr(forge.team, 'new_board', lambda identity: {'version': 1, 'board_id': identity, 'sessions': {}})
    configure(forge, capsys, root)
    canonical = group_plan(root)
    third = canonical / 'sections' / f'{THIRD}.md'
    third.write_text((canonical / 'sections' / f'{SECOND}.md').read_text())
    git(root, 'add', str(third))
    git(root, 'commit', '-m', 'Share the selected file')
    planning = Path(forge.team_plans.prepare(canonical, root)['planning_dir'])
    row = team(forge, capsys, root, 'start', '--task', 'two sections',
               '--planning-dir', planning, '--sections', SECTION, SECOND)['session']
    code, result = call(forge, capsys, 'team', 'check', '--target-dir', root, '--session', row['id'],
                        '--generation', row['generation'], '--planning-dir', planning, '--section', THIRD)
    assert code == 1 and not result['clearance']
    assert not forge.team_workflow.guard(planning, root, THIRD)['success']


def test_group_history_roundtrip(forge, repositories, capsys, tmp_path):
    root, _, _ = repositories
    canonical = group_plan(root)
    planning = Path(forge.team_plans.prepare(canonical, root)['planning_dir'])
    state = planning / 'implementation' / 'zagrosi_implement_state.json'
    state.parent.mkdir(exist_ok=True)
    commit = git(root, 'rev-parse', 'HEAD').stdout.strip()
    state.write_text(json.dumps({'completed_sections': {SECTION: {'commit': commit, 'notes': '/private/notes', 'verification_result': {'source': 'captured', 'outcome': 'passed', 'stdout': '/private/stderr'}}}}))
    code, created = call(forge, capsys, 'section-group', 'create', '--target-dir', root, '--planning-dir',
                         planning, '--sections', SECTION, SECOND, '--pr', 'example/app#42')
    assert code == 0, created
    assert created['handoff']['pull_request'] == 'example/app#42'
    rows = created['history']['sections']
    assert rows[SECTION]['status'] == 'recorded' and rows[SECTION]['commit_sha'] == commit
    assert rows[SECOND] == {'status': 'pending'}
    assert rows[SECTION]['verification_source'] == 'captured' and rows[SECTION]['verification_outcome'] == 'passed'
    assert '/private' not in json.dumps(rows)
    handoff = tmp_path / 'group.json'
    handoff.write_text(json.dumps(created))
    code, checked = call(forge, capsys, 'section-group', 'verify', '--target-dir', root, '--handoff', handoff,
                         '--', sys.executable, '-c', 'pass')
    assert code == 0, checked
    handoff.write_text(json.dumps(checked))
    receiving = tmp_path / 'receiver'
    git(root, 'worktree', 'add', '--detach', str(receiving), commit)
    code, inspected = call(forge, capsys, 'section-group', 'inspect', '--target-dir', receiving, '--handoff', handoff)
    assert code == 0 and 'verification' not in inspected
    assert inspected['history']['sections'] == rows
    assert inspected['history']['group_check']['run_id'] == checked['verification']['run_id']
    assert not (receiving / canonical.relative_to(root) / 'implementation').exists()


def test_group_committed_scope_and_root_roundtrip(forge, repositories, capsys, tmp_path):
    from shutil import copytree
    root, _, _ = repositories
    canonical = group_plan(root)
    copytree(canonical, root, dirs_exist_ok=True)
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'Canonical root plan')
    flags = ['--target-dir', root, '--planning-dir', root, '--sections', SECOND]
    code, group = call(forge, capsys, 'section-group', 'create', *flags)
    assert code == 0 and group['handoff']['plan_path'] == '.', group
    handoff = tmp_path / 'group.json'
    handoff.write_text(json.dumps(group))
    assert call(forge, capsys, 'section-group', 'inspect', '--target-dir', root, '--handoff', handoff)[0] == 0
    section = root / 'sections' / f'{SECOND}.md'
    section.write_text(section.read_text().replace('src/tables.py', 'src/untracked-mutant.py'))
    assert call(forge, capsys, 'section-group', 'create', *flags)[0] == 1
    assert call(forge, capsys, 'section-group', 'inspect', '--target-dir', root, '--handoff', handoff)[0] == 1


@pytest.mark.parametrize('flag', ['timed_out', 'termination_error'])
def test_group_incomplete_execution_never_passes(forge, repositories, capsys, tmp_path, monkeypatch, flag):
    root, _, _ = repositories
    plan = group_plan(root)
    _, group = call(forge, capsys, 'section-group', 'create', '--target-dir', root,
                    '--planning-dir', plan, '--sections', SECOND)
    handoff = tmp_path / 'group.json'
    handoff.write_text(json.dumps(group))
    execute = forge.child_process.execute
    def incomplete(argv, *args, **kwargs):
        if argv == ['local-check']:
            if flag == 'timed_out':
                return {'returncode': 124, 'timed_out': True, 'seconds': 1}
            return {'returncode': 0, 'timed_out': False, 'seconds': 1, 'termination_error': 'Cleanup incomplete'}
        return execute(argv, *args, **kwargs)
    monkeypatch.setattr(forge.child_process, 'execute', incomplete)
    code, checked = call(forge, capsys, 'section-group', 'verify', '--target-dir', root,
                         '--handoff', handoff, '--', 'local-check')
    assert code == 1 and not checked['history']['group_check']['passed']


def test_relative_check_executable_runs_from_the_committed_group_checkout(
        forge, repositories, capsys, tmp_path, monkeypatch):
    import os
    import shutil
    import subprocess

    root, _, _ = repositories
    planning = group_plan(root)
    executable = root / 'tools' / ('check.exe' if os.name == 'nt' else 'check')
    executable.parent.mkdir()
    if os.name == 'nt':
        shutil.copyfile(shutil.which('cmd.exe'), executable)
        args = ['/d', '/c', 'exit 0']
    else:
        attributes = executable.parent / '.gitattributes'
        attributes.write_text('check text eol=lf\n', encoding='utf-8')
        git(root, 'add', str(attributes))
        executable.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        executable.chmod(0o755)
        args = []
    git(root, 'add', str(executable))
    git(root, 'commit', '-m', 'Add repository check')
    _, group = call(forge, capsys, 'section-group', 'create', '--target-dir', root,
                    '--planning-dir', planning, '--sections', SECOND)
    handoff = tmp_path / 'group.json'
    handoff.write_text(json.dumps(group))
    relative_command = str(executable.relative_to(root))
    monkeypatch.chdir(tmp_path)
    if os.name != 'nt':
        # On Windows CreateProcess resolves the executable from the caller even with cwd set.
        popen = subprocess.Popen
        def windows_lookup(argv, *rest, **kwargs):
            if argv[0] == relative_command:
                argv = [str(Path.cwd() / argv[0]), *argv[1:]]
            return popen(argv, *rest, **kwargs)
        monkeypatch.setattr(subprocess, 'Popen', windows_lookup)
    code, checked = call(forge, capsys, 'section-group', 'verify', '--target-dir', root,
                         '--handoff', handoff, '--', relative_command, *args)
    assert code == 0, checked
    assert checked['verification']['command'] == [relative_command, *args]
