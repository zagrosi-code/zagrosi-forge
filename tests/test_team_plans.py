"""Committed shared contracts prepare private, immutable execution workspaces."""
import json
from pathlib import Path

import pytest

from forge_test_helpers import load_zagrosi_module
from test_compact_plan import SECTION, make_plan
from test_team_git import git, repositories


@pytest.fixture
def forge():
    return load_zagrosi_module()


def commit_plan(root, depth='lean'):
    planning = make_plan(root / '.forge/plans/shared', depth)
    index = planning / 'sections/index.md'
    marker, rest = index.read_text(encoding='utf-8').split('END_FORGE_META -->\n', 1)
    section = planning / 'sections' / f'{SECTION}.md'
    (planning / 'codex-plan.md').write_text(marker + 'END_FORGE_META -->\n' + section.read_text(encoding='utf-8'), encoding='utf-8')
    index.write_text(rest, encoding='utf-8')
    git(root, 'add', '.forge/plans/shared')
    git(root, 'commit', '-m', 'Review shared contract')
    return planning


def prepare(forge, root, planning):
    result = forge.team_plans.prepare(planning, root)
    assert result['success'], result
    return result, Path(result['planning_dir'])


@pytest.mark.parametrize('depth', ['lean', 'standard', 'deep'])
def test_prepare_committed_physical_contract_preserves_source_and_runs_only_git(forge, repositories, monkeypatch, depth):
    _, (root, _) = repositories
    planning = commit_plan(root, depth)
    repo = forge.team_git.Repository.discover(root)
    before = {path: path.read_bytes() for path in [repo.git_dir / 'index', *planning.rglob('*.md')]}
    execute = forge.team_git.execute
    calls = []
    def only_git(argv, *args, **kwargs):
        assert argv[0] == 'git'
        calls.append(argv)
        return execute(argv, *args, **kwargs)
    monkeypatch.setattr(forge.team_git, 'execute', only_git)
    result, workspace = prepare(forge, root, planning)
    assert workspace.parent == repo.git_dir / 'forge-plans'
    assert Path(result['sections_dir']) == workspace / 'sections'
    descriptor = forge.team_plans.validate(workspace, root)
    assert descriptor == result['source']
    assert descriptor['path'] == '.forge/plans/shared' and descriptor['section'] is None
    assert len(descriptor['digest']) == 64 and descriptor['commit'] == repo.head
    assert result['commands']['team_start'] and result['commands']['implement_setup']
    assert (workspace / '.forge-team-plan.json').is_file()
    assert {path.relative_to(workspace) for path in workspace.rglob('*.md')} == {
        path.relative_to(planning) for path in planning.rglob('*.md')}
    assert all((workspace / path.relative_to(planning)).read_bytes() == path.read_bytes() for path in planning.rglob('*.md'))
    assert before == {path: path.read_bytes() for path in before}
    assert any('ls-tree' in call for call in calls) and any('cat-file' in call and '--batch' in call for call in calls)


def test_prepare_follows_normalized_contract_links_and_active_artifacts_only(forge, repositories):
    _, (root, _) = repositories
    planning = commit_plan(root)
    (planning / 'details').mkdir()
    (planning / 'details/rules.md').write_text('[Source](../spec.md)\n', encoding='utf-8')
    plan = planning / 'codex-plan.md'
    plan.write_text(plan.read_text(encoding='utf-8') + '\n[Rules](sections/../details/rules.md)\n', encoding='utf-8')
    (planning / 'risk-register.md').write_text('# Risks\n\nA reviewed source contract.\n', encoding='utf-8')
    (planning / 'codex-interview.md').write_text('Private discussion\n', encoding='utf-8')
    (planning / 'traceability.md').write_text('Imported passing evidence must not be copied\n', encoding='utf-8')
    (planning / 'local.json').write_text('{"private": true}\n', encoding='utf-8')
    git(root, 'add', '.forge/plans/shared')
    git(root, 'commit', '-m', 'Review linked contract')
    (planning / 'untracked-notes.md').write_text('Uncommitted private notes\n', encoding='utf-8')
    _, workspace = prepare(forge, root, planning)
    assert (workspace / 'details/rules.md').is_file()
    assert (workspace / 'risk-register.md').is_file()
    for name in ('codex-interview.md', 'traceability.md', 'local.json', 'untracked-notes.md'):
        assert not (workspace / name).exists()


def test_contract_link_closure_ignores_code_examples_and_keeps_references(forge, repositories):
    _, (root, _) = repositories
    planning = commit_plan(root)
    (planning / 'details').mkdir()
    for name in ('rules.md', 'referenced.md'):
        (planning / 'details' / name).write_text('# Reviewed contract\n', encoding='utf-8')
    plan = planning / 'codex-plan.md'
    examples = ('\n`[example](missing-example.md)`\n'
                '```markdown\n[fenced](missing-fenced.md)\n```\n'
                '``example\n[code-reference]: missing-code-reference.md\n``\n'
                '<!-- [hidden](missing-comment.md) -->\n'
                '![Diagram](diagram.png)\n'
                '[Rules](details/rules.md)\n'
                '[Additional contract][reviewed]\n[reviewed]: details/referenced.md\n')
    plan.write_text(plan.read_text(encoding='utf-8') + examples, encoding='utf-8')
    git(root, 'add', '.forge/plans/shared')
    git(root, 'commit', '-m', 'Review literal examples and contract links')
    _, workspace = prepare(forge, root, planning)
    assert (workspace / 'details/rules.md').is_file()
    assert (workspace / 'details/referenced.md').is_file()
    assert not list(workspace.glob('missing-*'))


@pytest.mark.parametrize('name,link', [
    ('rules one.md', '[Spaced rule](<details/rules one.md>)'),
    ('rules one.md', '[Titled rule](<details/rules one.md> "Reviewed rule")'),
    ('rules one.md', '[Reference rule][rule]\n[rule]: <details/rules one.md> "Reviewed rule"'),
    ('rules.md', '[Titled rule](details/rules.md "Reviewed rule")'),
])
def test_prepare_preserves_full_source_and_link_paths_with_spaces(forge, repositories, name, link):
    _, (root, _) = repositories
    planning = commit_plan(root)
    source = planning / 'source specification.md'
    (planning / 'spec.md').rename(source)
    (planning / 'details').mkdir()
    dependency = planning / 'details' / name
    dependency.write_text('# Reviewed rule\n', encoding='utf-8')
    plan = planning / 'codex-plan.md'
    text = plan.read_text(encoding='utf-8').replace('"source": "spec.md"', '"source": "source specification.md"')
    plan.write_text(text + '\n' + link + '\n', encoding='utf-8')
    git(root, 'add', '.forge/plans/shared')
    git(root, 'commit', '-m', 'Review spaced source and titled contract link')
    _, workspace = prepare(forge, root, planning)
    assert (workspace / source.name).read_bytes() == source.read_bytes()
    assert (workspace / 'details' / name).read_bytes() == dependency.read_bytes()
    assert forge.team_plans.validate(workspace, root)


@pytest.mark.parametrize('link', ['[Missing](missing.md)', '[Missing][required]\n[required]: missing.md'])
def test_real_inline_and_reference_contract_links_require_committed_targets(forge, repositories, link):
    _, (root, _) = repositories
    planning = commit_plan(root)
    plan = planning / 'codex-plan.md'
    plan.write_text(plan.read_text(encoding='utf-8') + '\n' + link + '\n', encoding='utf-8')
    git(root, 'add', str(plan))
    git(root, 'commit', '-m', 'Review missing dependency declaration')
    with pytest.raises(forge.team_state.TeamError):
        forge.team_plans.prepare(planning, root)


@pytest.mark.parametrize('defect', ['worktree', 'index', 'untracked-link', 'outside-link', 'excluded-link', 'missing-section', 'ambiguous-plan'])
def test_prepare_rejects_unreviewed_or_incomplete_contract_without_creating_workspace(forge, repositories, defect):
    _, (root, _) = repositories
    planning = commit_plan(root)
    source = planning / 'spec.md'
    plan = planning / 'codex-plan.md'
    if defect in {'worktree', 'index'}:
        source.write_text(source.read_text(encoding='utf-8') + 'Unreviewed change\n', encoding='utf-8')
        if defect == 'index':
            git(root, 'add', str(source))
    elif defect == 'missing-section':
        git(root, 'rm', str(planning / 'sections' / f'{SECTION}.md'))
        git(root, 'commit', '-m', 'Incomplete contract')
    elif defect == 'ambiguous-plan':
        (planning / 'claude-plan.md').write_bytes(plan.read_bytes())
        git(root, 'add', str(planning / 'claude-plan.md'))
        git(root, 'commit', '-m', 'Ambiguous plan')
    else:
        name = {'untracked-link': 'untracked.md', 'outside-link': '../../../outside.md',
                'excluded-link': 'codex-interview.md'}[defect]
        plan.write_text(plan.read_text(encoding='utf-8') + f'\n[Dependency]({name})\n', encoding='utf-8')
        if defect == 'excluded-link':
            (planning / name).write_text('Private interview\n', encoding='utf-8')
            git(root, 'add', str(planning / name))
        git(root, 'add', str(plan))
        git(root, 'commit', '-m', 'Review dependency declaration')
        if defect == 'untracked-link':
            (planning / name).write_text('Private unreviewed note\n', encoding='utf-8')
    before = {path: path.read_bytes() for path in planning.rglob('*') if path.is_file()}
    with pytest.raises(forge.team_state.TeamError):
        forge.team_plans.prepare(planning, root)
    assert before == {path: path.read_bytes() for path in before}
    managed = forge.team_git.Repository.discover(root).git_dir / 'forge-plans'
    assert not managed.exists() or not list(managed.iterdir())


def test_equivalent_crlf_is_accepted_but_copies_preserve_committed_bytes(forge, repositories):
    _, (root, _) = repositories
    planning = commit_plan(root)
    source = planning / 'spec.md'
    committed = source.read_bytes()
    source.write_bytes(committed.replace(b'\n', b'\r\n'))
    _, workspace = prepare(forge, root, planning)
    assert (workspace / 'spec.md').read_bytes() == committed
    assert forge.team_plans.validate(workspace, root)


def test_reuse_preserves_progress_and_new_contract_keeps_old_evidence(forge, repositories):
    _, (root, _) = repositories
    planning = commit_plan(root)
    first, workspace = prepare(forge, root, planning)
    progress = workspace / 'implementation/progress.json'
    progress.parent.mkdir()
    progress.write_text('{"work":"retained"}\n', encoding='utf-8')
    git(root, 'commit', '--allow-empty', '-m', 'Unrelated application work')
    again, reused = prepare(forge, root, planning)
    assert reused == workspace and again['source'] == first['source']
    assert progress.read_text(encoding='utf-8') == '{"work":"retained"}\n'
    source = planning / 'spec.md'
    source.write_text(source.read_text(encoding='utf-8') + 'Additional reviewed constraint.\n', encoding='utf-8')
    git(root, 'add', str(source))
    git(root, 'commit', '-m', 'Review revised contract')
    latest, changed = prepare(forge, root, planning)
    assert changed != workspace and latest['source']['digest'] != first['source']['digest']
    assert progress.is_file() and not (changed / 'implementation').exists()
    with pytest.raises(forge.team_state.TeamError):
        forge.team_plans.validate(workspace, root)


def test_targets_and_linked_checkouts_get_independent_private_workspaces(forge, repositories):
    _, (root, _) = repositories
    planning = commit_plan(root)
    _, first = prepare(forge, root, planning)
    target = root / 'packages/api'
    target.mkdir(parents=True)
    _, scoped = prepare(forge, target, planning)
    linked = root.parent / 'linked-plan'
    git(root, 'worktree', 'add', '-b', 'linked-plan', str(linked))
    _, other = prepare(forge, linked, linked / '.forge/plans/shared')
    assert len({first, scoped, other}) == 3
    assert other.parent == forge.team_git.Repository.discover(linked).git_dir / 'forge-plans'


@pytest.mark.parametrize('location', ['source', 'copy', 'marker'])
@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_linked_contract_or_marker_is_rejected(forge, repositories, tmp_path, location, kind):
    _, (root, _) = repositories
    planning = commit_plan(root)
    workspace = None
    if location == 'source':
        path = planning / 'spec.md'
    else:
        _, workspace = prepare(forge, root, planning)
        path = workspace / ('.forge-team-plan.json' if location == 'marker' else 'spec.md')
    outside = tmp_path / 'linked-content'
    try:
        if kind == 'symlink':
            outside.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(outside)
        else:
            outside.hardlink_to(path)
    except OSError:
        pytest.skip(f'{kind} unavailable')
    retained = outside.read_bytes()
    with pytest.raises(forge.team_state.TeamError):
        if workspace is None:
            forge.team_plans.prepare(planning, root)
        else:
            forge.team_plans.validate(workspace, root)
    assert outside.read_bytes() == retained


@pytest.mark.parametrize('content', [b'\xffinvalid UTF-8\n', b'x' * (128 * 1024 + 1)], ids=['invalid-utf8', 'oversized-blob'])
def test_ineligible_committed_blob_is_rejected(forge, repositories, content):
    _, (root, _) = repositories
    planning = commit_plan(root)
    source = planning / 'spec.md'
    source.write_bytes(content)
    git(root, 'add', str(source))
    git(root, 'commit', '-m', 'Ineligible contract bytes')
    with pytest.raises(forge.team_state.TeamError):
        forge.team_plans.prepare(planning, root)


@pytest.mark.parametrize('corruption', ['trailing', 'wrong-hash'])
def test_batch_objects_require_complete_framing_and_matching_git_hash(forge, repositories, monkeypatch, corruption):
    _, (root, _) = repositories
    planning = commit_plan(root)
    original = forge.team_git._run
    def corrupt(repo, *args, **kwargs):
        result = original(repo, *args, **kwargs)
        if 'cat-file' in args and '--batch' in args:
            raw = result['stdout']
            return {**result, 'stdout': raw + 'unframed bytes' if corruption == 'trailing' else raw.replace('REQ-001', 'REQ-999')}
        return result
    monkeypatch.setattr(forge.team_git, '_run', corrupt)
    with pytest.raises(forge.team_state.TeamError):
        forge.team_plans.prepare(planning, root)


@pytest.mark.parametrize('mutation', ['marker-missing', 'marker-replaced', 'immutable-copy', 'shadow-plan', 'shadow-config', 'canonical', 'index', 'wrong-target'])
def test_validate_rejects_drift_without_replacing_existing_progress(forge, repositories, mutation):
    _, (root, _) = repositories
    planning = commit_plan(root)
    _, workspace = prepare(forge, root, planning)
    marker = workspace / '.forge-team-plan.json'
    if mutation == 'marker-missing':
        marker.unlink()
    elif mutation == 'marker-replaced':
        value = json.loads(marker.read_text(encoding='utf-8'))
        value['digest'] = 'a' * 64
        marker.write_text(json.dumps(value), encoding='utf-8')
    elif mutation == 'immutable-copy':
        (workspace / 'spec.md').write_text('Changed copy\n', encoding='utf-8')
    elif mutation == 'shadow-plan':
        (workspace / 'claude-plan.md').write_text('Shadow contract\n', encoding='utf-8')
    elif mutation == 'shadow-config':
        (workspace / 'zagrosi_plan_config.json').write_text('{}\n', encoding='utf-8')
    elif mutation in {'canonical', 'index'}:
        source = planning / 'spec.md'
        source.write_text('Changed canonical source\n', encoding='utf-8')
        if mutation == 'index':
            git(root, 'add', str(source))
    target = root
    if mutation == 'wrong-target':
        target = root / 'subproject'
        target.mkdir()
    before = {path: path.read_bytes() for path in workspace.rglob('*') if path.is_file()}
    with pytest.raises(forge.team_state.TeamError):
        forge.team_plans.validate(workspace, target)
    assert before == {path: path.read_bytes() for path in before}


def test_ordinary_validation_is_filesystem_only_and_mutable_outputs_are_bounded(forge, tmp_path, monkeypatch):
    ordinary = tmp_path / 'ordinary'
    ordinary.mkdir()
    plans = forge.team_plans
    monkeypatch.setattr(forge.team_git.Repository, 'discover', lambda *args:
                        (_ for _ in ()).throw(AssertionError('ordinary plan must not inspect Git')))
    assert plans.validate(ordinary) is None
    for name in ('implementation/check.json', 'traceability.md', 'forge-report.md', 'assumption-ledger.md',
                 '.forge/packets', '.forge/packets/context.md', '.forge/tdd-skeletons', '.forge/tdd-skeletons/test.py',
                 '.forge/scores/history.jsonl', '.forge/report.html'):
        assert plans.mutable_path(ordinary, ordinary / name), name
    for name in ('codex-plan.md', 'spec.md', '.forge-team-plan.json', 'zagrosi_plan_config.json', '../outside.md'):
        assert not plans.mutable_path(ordinary, ordinary / name), name


def test_ordinary_forge_plans_directory_is_not_a_managed_workspace(forge, repositories):
    _, (root, _) = repositories
    ordinary = root / 'docs/forge-plans/legacy'
    ordinary.mkdir(parents=True)
    assert forge.team_plans.validate(ordinary, root) is None
    assert forge.team_plans.validate(ordinary) is None


def test_missing_managed_marker_is_rejected_without_explicit_target(forge, repositories):
    _, (root, _) = repositories
    planning = commit_plan(root)
    _, workspace = prepare(forge, root, planning)
    (workspace / '.forge-team-plan.json').unlink()
    with pytest.raises(forge.team_state.TeamError):
        forge.team_plans.validate(workspace)


def test_unhashable_compact_depth_has_a_structured_rejection(forge, repositories):
    _, (root, _) = repositories
    planning = commit_plan(root)
    plan = planning / 'codex-plan.md'
    text = plan.read_text(encoding='utf-8')
    metadata, _ = forge.markdown.parse_forge_meta(text)
    metadata['depth_mode'] = []
    plan.write_text('<!-- FORGE_META\n' + json.dumps(metadata) + '\nEND_FORGE_META -->\n'
                    + text.split('END_FORGE_META -->\n', 1)[1], encoding='utf-8')
    git(root, 'add', str(plan))
    git(root, 'commit', '-m', 'Malformed depth')
    with pytest.raises(forge.team_state.TeamError):
        forge.team_plans.prepare(planning, root)


def test_marker_file_size_cannot_substitute_boolean_for_integer(forge, repositories):
    _, (root, _) = repositories
    planning = commit_plan(root)
    (planning / 'spec.md').write_bytes(b'X')
    git(root, 'add', str(planning / 'spec.md'))
    git(root, 'commit', '-m', 'One byte source')
    _, workspace = prepare(forge, root, planning)
    marker = workspace / '.forge-team-plan.json'
    value = json.loads(marker.read_text(encoding='utf-8'))
    assert value['files']['spec.md']['size'] == 1
    value['files']['spec.md']['size'] = True
    marker.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(forge.team_state.TeamError):
        forge.team_plans.validate(workspace, root)
