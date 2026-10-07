"""Published collaboration examples stay callable and reachable from every skill."""
from pathlib import Path
import json
import re
import shlex

import pytest

from forge_test_helpers import ROOT, load_zagrosi_module
from test_team_git import git, repositories
from test_team_plans import commit_plan


SKILLS = ('zagrosi-forge', 'zagrosi-project', 'zagrosi-plan', 'zagrosi-implement', 'zagrosi-cleanup')
REFERENCE = ROOT / 'skills/zagrosi-forge/references/collaboration.md'
GUIDE = ROOT / 'docs/collaboration.md'


def links(path):
    return re.findall(r'\[[^\]]*\]\(([^)]+)\)', path.read_text(encoding="utf-8"))


@pytest.mark.parametrize('name', SKILLS)
def test_each_workflow_routes_to_the_shared_collaboration_contract(name):
    skill = ROOT / 'skills' / name / 'SKILL.md'
    targets = {(skill.parent / value).resolve() for value in links(skill) if not value.startswith(('https:', '#'))}
    assert REFERENCE in targets


@pytest.mark.parametrize('path', [GUIDE, REFERENCE])
def test_collaboration_document_links_resolve(path):
    for target in links(path):
        if not target.startswith(('https:', 'http:', '#')):
            assert (path.parent / target.split('#', 1)[0]).exists(), (path, target)


def test_documented_helper_examples_match_the_public_command_parser():
    parser = load_zagrosi_module().cli.build_parser()
    documented = set()
    for path in (GUIDE, REFERENCE):
        for block in re.findall(r'```bash\n(.*?)```', path.read_text(encoding="utf-8"), re.S):
            for line in block.replace('\\\n', ' ').splitlines():
                argv = shlex.split(line)
                if not argv:
                    continue
                assert argv[0] == 'python3' and argv[1].endswith('/zagrosi_skills.py'), line
                args = parser.parse_args([value for value in argv[2:] if value != '--pretty'])
                assert args.handler == ('team_cli', 'run')
                assert args.target_dir, line
                documented.add(args.team_action)
    assert documented == {'init', 'join', 'status', 'prepare', 'start', 'update', 'check', 'finish', 'recover', 'retry', 'leave'}


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_readable_preparation_returns_workspace_without_editing_clearance(repositories, capsys, host):
    _, (root, _) = repositories
    forge = load_zagrosi_module()
    planning = commit_plan(root)
    if host == 'claude':
        (planning / 'codex-plan.md').rename(planning / 'claude-plan.md')
        git(root, 'add', '.forge/plans/shared')
        git(root, 'commit', '-m', 'Use Claude contract')
    index = forge.team_git.Repository.discover(root).git_dir / 'index'
    before = {path: path.read_bytes() for path in [index, *planning.rglob('*.md')]}
    command = ['team', 'prepare', '--target-dir', str(root), '--planning-dir', str(planning)]
    assert forge.entrypoint.main([*command, '--pretty']) == 0
    readable = capsys.readouterr()
    assert not readable.err
    assert 'Forge team: prepared' in readable.out
    assert 'Editing clearance: not established' in readable.out
    assert forge.entrypoint.main(command) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared['success'] and prepared['status'] == 'prepared'
    assert prepared['planning_dir'] in readable.out
    assert prepared['sections_dir'] in readable.out
    assert prepared['next_action'] in readable.out
    assert before == {path: path.read_bytes() for path in before}
    assert forge.team_plans.validate(Path(prepared['planning_dir']), root) == prepared['source']


def test_readable_team_results_keep_identifiers_for_followup_actions(repositories, capsys):
    _, (root, _) = repositories
    forge = load_zagrosi_module()
    assert forge.entrypoint.main(['team', 'init', '--target-dir', str(root), '--name', 'Alex']) == 0
    capsys.readouterr()
    assert forge.entrypoint.main(['team', 'start', '--target-dir', str(root), '--task', 'Readable task',
                                 '--path', 'code.txt', '--pretty']) == 0
    started = capsys.readouterr().out
    status = ['team', 'status', '--target-dir', str(root)]
    assert forge.entrypoint.main(status) == 0
    observed = json.loads(capsys.readouterr().out)
    row, = observed['sessions']
    assert row['id'] in started and row['generation'] in started
    assert observed['revision'] in started
    assert 'Editing clearance: current reservation checked' in started
    assert forge.entrypoint.main([*status, '--pretty']) == 0
    roster = capsys.readouterr().out
    assert row['id'] in roster and row['generation'] in roster
    assert observed['revision'] in roster
    assert 'Editing clearance: not established' in roster
    assert forge.entrypoint.main(['team', 'finish', '--target-dir', str(root), '--session', row['id'],
                                 '--generation', row['generation'], '--note', 'Done', '--pretty']) == 0
    assert 'Editing clearance: not established' in capsys.readouterr().out


def test_readable_preparation_failure_remains_actionable(repositories, capsys):
    _, (root, _) = repositories
    forge = load_zagrosi_module()
    planning = commit_plan(root)
    (planning / 'codex-plan.md').write_text('Uncommitted replacement\n', encoding='utf-8')
    assert forge.entrypoint.main(['team', 'prepare', '--target-dir', str(root),
                                 '--planning-dir', str(planning), '--pretty']) == 1
    captured = capsys.readouterr()
    assert not captured.err
    assert 'Forge team: unavailable' in captured.out
    assert 'Editing clearance: not established' in captured.out
    assert 'contract' in captured.out.lower()
