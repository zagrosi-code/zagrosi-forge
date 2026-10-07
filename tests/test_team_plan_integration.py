"""Shared contracts and private evidence exercised through the public CLI."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from forge_test_helpers import SCRIPT
from test_compact_plan import SECTION, make_plan


OTHER = 'section-02-consumer'
CANONICAL = '.forge/plans/shared'
CONTRACT_TEXT = 'Reviewed shared contract sentinel: preserve internal whitespace and case.'
PRIVATE_TEXT = 'Private planning interview sentinel: do not distribute this text.'


def git(root, *args):
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'Test engineer', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
           'GIT_COMMITTER_NAME': 'Test engineer', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}
    result = subprocess.run(['git', '-C', str(root), *map(str, args)], capture_output=True,
                            encoding='utf-8', env=env, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def raw_cli(root, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=root,
                          capture_output=True, encoding='utf-8', timeout=90)


def cli(root, *args, ok=True):
    result = raw_cli(root, *args)
    assert result.returncode == (0 if ok else 1), (args, result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload['success'] is ok, payload
    return payload


def team(root, action, *args, ok=True):
    return cli(root, 'team', action, '--target-dir', root, *args, ok=ok)


def inventory(path):
    return {str(item.relative_to(path)): item.read_bytes() for item in path.rglob('*') if item.is_file()}


def state(workspace):
    return json.loads((workspace / 'implementation/zagrosi_implement_state.json').read_text(encoding='utf-8'))


def receipt(workspace, section):
    return workspace / 'implementation/verification' / f'{section}.json'


def prepare(root):
    result = team(root, 'prepare', '--planning-dir', root / CANONICAL)
    return Path(result['planning_dir']), result['source']


def reserve(root, workspace, section, host):
    return team(root, 'start', '--task', f'Implement {section}', '--host', host,
                '--planning-dir', workspace, '--section', section)['session']


def setup(root, workspace, section, *, ok=True):
    return cli(root, 'implement-setup', '--sections-dir', workspace / 'sections',
               '--target-dir', root, '--section', section, '--flight', 'off', ok=ok)


def verify(root, workspace, section, label):
    module = 'labels' if section == SECTION else 'consumer'
    code = (f'from src.{module} import normalize; '
            'assert normalize(" Ada  Lovelace ") == "Ada  Lovelace"; '
            'assert normalize("") == ""; '
            f'print({label!r})')
    result = cli(root, 'implement-verify', '--planning-dir', workspace, '--target-dir', root,
                 '--section', section, '--', sys.executable, '-B', '-c', code)
    saved = json.loads(receipt(workspace, section).read_text(encoding='utf-8'))
    assert saved['outcome'] == 'passed' and saved['source'] == 'captured'
    assert saved['snapshot']['target_dir'] == str(root.resolve())
    assert saved['snapshot']['planning_dir'] == str(workspace.resolve())
    assert label in saved['stdout_tail']
    return result


def record(root, workspace, section, *, source=None, ok=True):
    return cli(root, 'implement-record-section', '--sections-dir', workspace / 'sections',
               '--target-dir', root, '--section', section, '--review-status', 'pass',
               '--verification-receipt', source or receipt(workspace, section), '--flight', 'off', ok=ok)


def implement(root, section):
    module = 'labels' if section == SECTION else 'consumer'
    (root / 'src' / f'{module}.py').write_text('def normalize(value):\n    return value.strip()\n', encoding='utf-8')


@pytest.fixture
def collaboration_repo(tmp_path):
    """Create genuine clones of one deliberately committed contract and discovery file."""
    def build(depth='lean', *, dependent=False, linked=False, crlf=False):
        remote = tmp_path / 'shared.git'
        git(tmp_path, 'init', '--bare', '--initial-branch=main', remote)
        first = tmp_path / 'alice'
        git(tmp_path, 'init', '--initial-branch=main', first)
        git(first, 'config', 'commit.gpgsign', 'false')
        git(first, 'config', 'core.autocrlf', 'false')
        git(first, 'remote', 'add', 'origin', remote)
        (first / '.gitignore').write_text('**/private-notes.md\n', encoding='utf-8')
        (first / '.gitattributes').write_text('*.md text eol=lf\n', encoding='utf-8')
        planning = make_plan(first / CANONICAL, depth)
        source = planning / 'spec.md'
        source.write_text(source.read_text(encoding='utf-8') + CONTRACT_TEXT + '\n', encoding='utf-8')
        index = planning / 'sections/index.md'
        marker, body = index.read_text(encoding='utf-8').split('END_FORGE_META -->\n', 1)
        section_text = (planning / 'sections' / f'{SECTION}.md').read_text(encoding='utf-8')
        (planning / 'codex-plan.md').write_text(marker + 'END_FORGE_META -->\n' + section_text +
            f'\n[Consumer contract](sections/{OTHER}.md)\n', encoding='utf-8')
        second_text = section_text.replace(SECTION, OTHER).replace('labels.py', 'consumer.py')
        if dependent:
            second_text = second_text.replace('## Dependencies\nNone.', f'## Dependencies\n{SECTION}.')
        (planning / 'sections' / f'{OTHER}.md').write_text(second_text + '\n[Source](../spec.md)\n', encoding='utf-8')
        body = body.replace('END_MANIFEST', OTHER + '\nEND_MANIFEST')
        body = body.split('Dependencies: none.', 1)[0]
        body += ('\n| Section | Depends on |\n|---|---|\n'
                 f'| {SECTION} | none |\n| {OTHER} | {SECTION if dependent else "none"} |\n'
                 f'\nExecution order: {SECTION}, {OTHER}. Parallel: {"no" if dependent else "independent sections"}.\n')
        index.write_text(body, encoding='utf-8')
        (first / 'src').mkdir()
        (first / 'tests').mkdir()
        for module in ('labels', 'consumer'):
            (first / 'src' / f'{module}.py').write_text('def normalize(value):\n    return value\n', encoding='utf-8')
            (first / 'tests' / f'test_{module}.py').write_text(
                f'from src.{module} import normalize\ndef test_trim_edges():\n'
                '    assert normalize(" Ada  Lovelace ") == "Ada  Lovelace"\n', encoding='utf-8')
        git(first, 'add', '.')
        git(first, 'commit', '-m', 'Review shared contract and existing source')
        team(first, 'init', '--remote', 'origin', '--name', 'Alice')
        git(first, 'add', '.forge/team.json')
        git(first, 'commit', '-m', 'Share team discovery')
        git(first, 'push', '-u', 'origin', 'main')
        second = tmp_path / ('linked' if linked else 'bob')
        if linked:
            git(first, 'worktree', 'add', '-b', 'claude-task', second)
        else:
            options = ['-c', 'core.autocrlf=true'] if crlf else []
            git(tmp_path, 'clone', *options, remote, second)
        git(second, 'config', 'commit.gpgsign', 'false')
        team(second, 'join', '--remote', 'origin', '--name', 'Blair')
        for root in (first, second):
            (root / CANONICAL / 'private-notes.md').write_text(PRIVATE_TEXT, encoding='utf-8')
        return first, second, remote
    return build


@pytest.mark.parametrize('depth,linked', [('lean', False), ('standard', False), ('deep', False), ('lean', True)])
def test_independent_sections_run_concurrently_with_private_evidence(collaboration_repo, depth, linked):
    first, second, remote = collaboration_repo(depth, linked=linked)
    before = {root: inventory(root / CANONICAL) for root in (first, second)}
    one, descriptor_one = prepare(first)
    two, descriptor_two = prepare(second)
    assert one != two and descriptor_one == descriptor_two
    for root, workspace in ((first, one), (second, two)):
        administrative = Path(git(root, 'rev-parse', '--absolute-git-dir'))
        assert workspace.parent == administrative / 'forge-plans'
        assert not (workspace / 'private-notes.md').exists()
        assert not (workspace / 'implementation').exists()
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda args: reserve(*args),
                              [(first, one, SECTION, 'codex'), (second, two, OTHER, 'claude')]))
        list(pool.map(lambda args: setup(*args), [(first, one, SECTION), (second, two, OTHER)]))
    for row, section in zip(claims, (SECTION, OTHER)):
        assert row['plan'] == {**descriptor_one, 'section': section}
        assert not any(path.startswith('.forge') for path in row['paths'])
    implement(first, SECTION)
    implement(second, OTHER)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda args: verify(*args), [(first, one, SECTION, 'Private result Alice'),
                                                 (second, two, OTHER, 'Private result Blair')]))
    record(first, one, SECTION)
    record(second, two, OTHER)
    assert set(state(one)['completed_sections']) == {SECTION}
    assert set(state(two)['completed_sections']) == {OTHER}
    assert not receipt(one, OTHER).exists() and not receipt(two, SECTION).exists()
    assert before == {root: inventory(root / CANONICAL) for root in (first, second)}
    board_text = git(remote, 'show', 'refs/heads/forge/team:board.json')
    board = json.loads(board_text)
    rows = list(board['sessions'].values())
    assert {row['host'] for row in rows} == {'codex', 'claude'}
    assert {row['plan']['section'] for row in rows} == {SECTION, OTHER}
    assert all(row['plan']['digest'] == descriptor_one['digest'] for row in rows)
    history = git(remote, 'log', '--format=%B', 'refs/heads/forge/team')
    for private in (CONTRACT_TEXT, PRIVATE_TEXT, 'Private result Alice', 'Private result Blair', str(one), str(two)):
        assert private not in board_text and private not in history


def test_handoff_requires_receiver_preparation_binding_generation_and_checks(collaboration_repo):
    first, second, _ = collaboration_repo()
    one, _ = prepare(first)
    old = reserve(first, one, SECTION, 'codex')
    setup(first, one, SECTION)
    implement(first, SECTION)
    verify(first, one, SECTION, 'Private first engineer result')
    team(first, 'update', '--session', old['id'], '--generation', old['generation'],
         '--state', 'handoff', '--note', 'Source ready; receiving engineer must verify locally.')
    git(first, 'add', 'src/labels.py')
    git(first, 'commit', '-m', 'Implement label normalization')
    git(first, 'push', 'origin', 'main')
    git(second, 'pull', '--ff-only')
    revision = team(second, 'status')['revision']
    taken = team(second, 'recover', '--session', old['id'], '--expect', revision,
                 '--reason', 'Agreed section handoff', '--host', 'claude')['session']
    assert taken['generation'] != old['generation']
    before = inventory(one)
    setup(second, one, SECTION, ok=False)
    assert inventory(one) == before
    two, _ = prepare(second)
    assert not receipt(two, SECTION).exists()
    unbound = setup(second, two, SECTION, ok=False)
    assert unbound['error_code'] == 'team-binding-required'
    assert not (two / 'implementation/zagrosi_implement_state.json').exists()
    missing = raw_cli(second, 'team', 'update', '--target-dir', second, '--session', taken['id'],
                      '--planning-dir', two, '--section', SECTION)
    assert missing.returncode == 2 and '--generation' in missing.stderr
    team(second, 'update', '--session', taken['id'], '--generation', taken['generation'],
         '--state', 'working', '--planning-dir', two, '--section', SECTION)
    setup(second, two, SECTION)
    before = inventory(two)
    foreign = record(second, two, SECTION, source=receipt(one, SECTION), ok=False)
    assert 'changed' in foreign['error'].lower() or 'current' in foreign['error'].lower()
    assert inventory(two) == before
    lost = team(first, 'check', '--session', old['id'], '--generation', old['generation'], ok=False)
    assert lost['error_code'] == 'team-ownership-lost'
    before = inventory(one)
    setup(first, one, SECTION, ok=False)
    assert inventory(one) == before
    verify(second, two, SECTION, 'Private receiving engineer result')
    record(second, two, SECTION)
    assert set(state(two)['completed_sections']) == {SECTION}


def test_canonical_readers_block_edits_and_mixed_revisions_without_losing_evidence(collaboration_repo):
    first, second, _ = collaboration_repo()
    one, original = prepare(first)
    reserve(first, one, SECTION, 'codex')
    setup(first, one, SECTION)
    before = inventory(one)
    revision = team(second, 'status')['revision']
    blocked = team(second, 'start', '--task', 'Edit reviewed contract', '--path', '.forge', ok=False)
    assert blocked['error_code'] == 'team-conflict'
    assert team(second, 'status')['revision'] == revision
    source = second / CANONICAL / 'spec.md'
    source.write_text(source.read_text(encoding='utf-8') + 'Reviewed additional requirement.\n', encoding='utf-8')
    git(second, 'add', str(source))
    git(second, 'commit', '-m', 'Review next contract revision')
    two, changed = prepare(second)
    assert changed['digest'] != original['digest']
    blocked = team(second, 'start', '--task', 'Implement updated consumer', '--host', 'claude',
                   '--planning-dir', two, '--section', OTHER, ok=False)
    assert blocked['error_code'] == 'team-conflict'
    assert team(second, 'status')['revision'] == revision
    assert inventory(one) == before and not (two / 'implementation').exists()


def test_identical_contract_after_code_commits_and_real_crlf_checkout_coexists(collaboration_repo):
    first, second, _ = collaboration_repo(crlf=True)
    # Force effective CRLF conversion through attributes, not a platform assumption.
    attributes = second / '.gitattributes'
    attributes.write_text('*.md text eol=crlf\n', encoding='utf-8')
    git(second, 'add', '.gitattributes')
    git(second, 'commit', '-m', 'Use CRLF checkout for Markdown')
    for path in (second / CANONICAL).rglob('*.md'):
        if path.name != 'private-notes.md':
            path.unlink()
    git(second, 'checkout', '--', CANONICAL)
    checked_out = (second / CANONICAL / 'spec.md').read_bytes()
    assert b'\r\n' in checked_out
    assert not git(second, 'status', '--porcelain', '--', CANONICAL)
    one, original = prepare(first)
    reserve(first, one, SECTION, 'codex')
    (second / 'README.md').write_text('Ordinary unrelated application documentation.\n', encoding='utf-8')
    git(second, 'add', 'README.md')
    git(second, 'commit', '-m', 'Document application')
    two, same = prepare(second)
    assert same['digest'] == original['digest'] and same['commit'] != original['commit']
    assert (one / 'spec.md').read_bytes() == (two / 'spec.md').read_bytes()
    assert b'\r\n' not in (two / 'spec.md').read_bytes()
    reserve(second, two, OTHER, 'claude')
    setup(first, one, SECTION)
    setup(second, two, OTHER)


def test_changed_branch_or_target_preserves_prepared_state_and_receipts(collaboration_repo):
    first, _, _ = collaboration_repo()
    workspace, _ = prepare(first)
    reserve(first, workspace, SECTION, 'codex')
    setup(first, workspace, SECTION)
    implement(first, SECTION)
    verify(first, workspace, SECTION, 'Private preserved result')
    before = inventory(workspace)
    git(first, 'checkout', '-b', 'different-task')
    blocked = setup(first, workspace, SECTION, ok=False)
    assert blocked['error_code'] == 'team-checkout-changed'
    assert inventory(workspace) == before
    git(first, 'checkout', 'main')
    target = first / 'packages/api'
    target.mkdir(parents=True)
    setup(target, workspace, SECTION, ok=False)
    assert inventory(workspace) == before


def test_peer_completion_and_copied_receipts_do_not_unlock_dependent_section(collaboration_repo):
    first, second, _ = collaboration_repo(dependent=True)
    one, _ = prepare(first)
    two, _ = prepare(second)
    original = reserve(first, one, SECTION, 'codex')
    reserve(second, two, OTHER, 'claude')
    setup(first, one, SECTION)
    implement(first, SECTION)
    verify(first, one, SECTION, 'Peer predecessor passed')
    record(first, one, SECTION)
    team(first, 'update', '--session', original['id'], '--generation', original['generation'],
         '--state', 'handoff', '--note', 'Predecessor completed and verified in first checkout.')
    git(first, 'add', 'src/labels.py')
    git(first, 'commit', '-m', 'Integrate completed predecessor source')
    git(first, 'push', 'origin', 'main')
    git(second, 'pull', '--ff-only')
    copied = receipt(two, SECTION)
    copied.parent.mkdir(parents=True)
    copied.write_bytes(receipt(one, SECTION).read_bytes())
    # Complete source and a passed foreign receipt still cannot become local completion.
    blocked = setup(second, two, OTHER, ok=False)
    assert OTHER not in blocked['ready_sections'] and SECTION in blocked['ready_sections']
    assert not (two / 'implementation/zagrosi_implement_state.json').exists()
    attempted = record(second, two, OTHER, source=copied, ok=False)
    assert attempted['error_code'] == 'incomplete-predecessors'
    assert attempted['incomplete_predecessors'] == [SECTION]
    assert copied.read_bytes() == receipt(one, SECTION).read_bytes()
    assert set(state(one)['completed_sections']) == {SECTION}
