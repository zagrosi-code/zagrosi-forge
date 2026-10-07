"""Published collaboration examples stay callable and reachable from every skill."""
from pathlib import Path
import re
import shlex

import pytest

from forge_test_helpers import ROOT, load_zagrosi_module


SKILLS = ('zagrosi-forge', 'zagrosi-project', 'zagrosi-plan', 'zagrosi-implement', 'zagrosi-cleanup')
REFERENCE = ROOT / 'skills/zagrosi-forge/references/collaboration.md'
GUIDE = ROOT / 'docs/collaboration.md'


def links(path):
    return re.findall(r'\[[^\]]*\]\(([^)]+)\)', path.read_text())


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
        for block in re.findall(r'```bash\n(.*?)```', path.read_text(), re.S):
            for line in block.replace('\\\n', ' ').splitlines():
                argv = shlex.split(line)
                if not argv:
                    continue
                assert argv[0] == 'python3' and argv[1].endswith('/zagrosi_skills.py'), line
                args = parser.parse_args([value for value in argv[2:] if value != '--pretty'])
                assert args.handler == ('team_cli', 'run')
                assert args.target_dir, line
                documented.add(args.team_action)
    assert documented == {'init', 'join', 'status', 'start', 'update', 'check', 'finish', 'recover', 'retry', 'leave'}
