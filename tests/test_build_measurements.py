"""Public measurement reports count executions rather than receipt copies."""
import json
import sys

from test_compact_plan import SECTION
from test_verification_receipts import invoke, verify_args, workspace


def report(workspace, capsys, *args):
    code, result = invoke(workspace, capsys, 'build-report', '--planning-dir', str(workspace[1]), *args)
    assert code == 0, result
    return result


def test_repeated_actual_check_and_one_dual_scope_capture(workspace, capsys):
    command = [sys.executable, '-c', 'print("ok")']
    for extra in [[], ['--integration']]:
        code, result = invoke(workspace, capsys, *verify_args(workspace, '--section', SECTION, *extra, '--', *command))
        assert code == 0, result
    result = report(workspace, capsys)
    for row in (result['summary'], result['sections'][SECTION]):
        assert row['checks'] == 2 and row['repeated_checks'] == 1
        assert row['check_seconds'] > 0
        assert row['review_rounds'] is None and row['input_tokens'] is None
    assert result['integration']['checks'] == 1


def test_legacy_checks_stay_unknown_and_cannot_count_as_zero(workspace, capsys):
    _, planning, _ = workspace
    path = planning / 'implementation/verification' / f'{SECTION}.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'source': 'captured', 'command': ['test'], 'outcome': 'passed', 'seconds': 4}))
    result = report(workspace, capsys)
    assert result['summary']['checks'] is None
    assert result['sections'][SECTION]['observed_checks'] == 0


def test_native_usage_deduplicates_sessions_and_marks_incomplete_observations(workspace, capsys, tmp_path):
    session = tmp_path / 'claude.jsonl'
    result = {'type': 'result', 'subtype': 'success', 'session_id': 'claude-1',
              'modelUsage': {'claude': {'inputTokens': 20, 'cacheReadInputTokens': 15,
                                        'cacheCreationInputTokens': 5, 'outputTokens': 6}}}
    session.write_text(json.dumps(result) + '\n')
    clone = tmp_path / 'copy.jsonl'
    clone.write_text(session.read_text())
    row = report(workspace, capsys, '--usage', SECTION, str(session), '--review', SECTION, str(clone))['summary']
    assert (row['input_tokens'], row['cached_input_tokens'], row['output_tokens'], row['review_rounds']) == (40, 15, 6, 1)
    partial = tmp_path / 'interrupted.jsonl'
    partial.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'codex-2'}}) + '\n')
    row = report(workspace, capsys, '--usage', SECTION, str(session), '--usage', SECTION, str(partial))['summary']
    assert row['input_tokens'] is None and row['observed_input_tokens'] == 40


def test_state_first_retries_and_failed_attempts_remain_countable(workspace, capsys, tmp_path):
    from test_state_first import cli, ready, write_workspace

    run = cli(workspace[0], capsys)
    target = tmp_path / 'state-first'
    target.mkdir()
    planning = ready(run, write_workspace(target))
    section = 'section-01-trim'
    for outcome in (1, 1, 0):
        code, _ = run('implement-progress', '--planning-dir', str(planning), '--section', section, '--stage', 'done',
                      '--notes', 'Recorded trial.', '--', sys.executable, '-c', f'raise SystemExit({outcome})')
        assert code == outcome
    code, result = run('build-report', '--planning-dir', str(planning), '--section', section)
    assert code == 0
    assert result['summary']['checks'] == 3 and result['summary']['repeated_checks'] == 1
    assert result['summary']['check_wall_span_seconds'] > 0


def committed_group_identity(workspace, root, pr):
    from test_team_git import git

    forge, planning, _ = workspace
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    index = planning / 'sections/index.md'
    marker, rest = index.read_text().split('END_FORGE_META -->\n', 1)
    section = planning / 'sections' / f'{SECTION}.md'
    (planning / 'codex-plan.md').write_text(marker + 'END_FORGE_META -->\n' + section.read_text())
    index.write_text(rest)
    git(root, 'add', str(index), str(section), str(planning / 'spec.md'), str(planning / 'codex-plan.md'))
    git(root, 'commit', '-m', 'Reviewed plan')
    repo = forge.team_git.Repository.discover(root)
    files, _ = forge.team_plans._bundle(repo, planning.name)
    return {'schema': 'forge-section-group-v1', 'sections': [SECTION], 'pull_request': pr,
            'plan_path': planning.name, 'plan_digest': forge.team_plans._digest(files),
            'commit_sha': repo.head, 'tree_sha': forge.team_git._output(root, 'rev-parse', 'HEAD^{tree}')}


def test_group_verification_has_its_own_scope_and_replay_counts_once(workspace, capsys, tmp_path):
    identity = committed_group_identity(workspace, tmp_path, 'PR-2')
    group = tmp_path / 'group.json'
    verification = {'command': ['pytest'], 'run_id': 'abcd', 'seconds': 4.2, 'returncode': 0}
    group.write_text(json.dumps({'success': True, 'handoff': identity,
                                 'verification': verification, 'history': {'group_check': verification}}))
    result = report(workspace, capsys, '--group', str(group), '--group', str(group))
    assert result['sections'][SECTION]['checks'] is None
    assert result['groups'][0]['verification']['checks'] == 1 and len(result['groups']) == 1
    assert result['pull_requests']['PR-2']['observed_checks'] == 1 and result['summary']['observed_check_seconds'] == 4.2


def test_duplicate_rollout_checkpoints_and_messages_do_not_add_cumulative_usage(workspace, capsys, tmp_path):
    log = tmp_path / 'rollout.jsonl'
    count = {'input_tokens': 30, 'cached_input_tokens': 12, 'output_tokens': 6}
    event = {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': count}}}
    log.write_text('\n'.join(json.dumps(row) for row in [
        {'type': 'session_meta', 'payload': {'id': 'c-1'}}, event, event,
        {'type': 'event_msg', 'payload': {'type': 'task_complete'}}]))
    row = report(workspace, capsys, '--usage', SECTION, str(log), '--usage', SECTION, str(log))['summary']
    assert row['input_tokens'] == 30 and row['output_tokens'] == 6
    partial = tmp_path / 'claude.jsonl'
    message = {'type': 'assistant', 'session_id': 'a', 'message': {'id': 'msg-1', 'usage':
               {'input_tokens': 7, 'cache_creation_input_tokens': 1, 'cache_read_input_tokens': 2, 'output_tokens': 4}}}
    partial.write_text(json.dumps(message) + '\n' + json.dumps(message))
    row = report(workspace, capsys, '--usage', SECTION, str(partial))['summary']
    assert row['input_tokens'] is None and row['observed_input_tokens'] == 10


def test_provider_review_execution_is_attributed_once(workspace, capsys, tmp_path):
    path = tmp_path / 'review.json'
    path.write_text(json.dumps({'schema': 'forge-provider-review-v1', 'provider': 'codex', 'review_id': 'abc',
                               'success': True, 'returncode': 0, 'usage':
                               {'input_tokens': 10, 'cached_input_tokens': 4, 'output_tokens': 2}}))
    row = report(workspace, capsys, '--review', SECTION, str(path), '--review', SECTION, str(path))['summary']
    assert row['review_rounds'] == 1 and row['input_tokens'] == 10
    old = json.loads(path.read_text())
    del old['review_id']
    path.write_text(json.dumps(old))
    row = report(workspace, capsys, '--review', SECTION, str(path))['summary']
    assert row['observed_review_rounds'] == 1 and row['review_rounds'] is None


def test_independent_compatibility_attempts_and_a_failed_group_remain_visible(workspace, capsys, tmp_path):
    from test_compatibility_checks import activate, block, contract
    forge, planning, target = workspace
    section = planning / 'sections' / f'{SECTION}.md'
    section.write_text(section.read_text() + block(contract()))
    (target / 'checks.py').write_text('print("passing")')
    activate(workspace)
    for stage in ('baseline', 'candidate', 'candidate'):
        code, result = invoke(workspace, capsys, *verify_args(workspace, '--section', SECTION, '--stage', stage,
                                                              '--', sys.executable, 'checks.py'))
        assert code == 0, result
    local = report(workspace, capsys)['sections'][SECTION]
    assert local['checks'] == 3 and local['repeated_commands'][0]['executions'] == 3
    identity = committed_group_identity(workspace, tmp_path, '42')
    group = tmp_path / 'group.json'
    group.write_text(json.dumps({'success': False, 'handoff': identity,
        'verification': {'run_id': 'group-run', 'command': ['make', 'check'], 'returncode': 0,
                         'snapshot_error': 'changed source', 'seconds': 2.0,
                         'started_at': '2026-10-01T10:00:00+00:00', 'finished_at': '2026-10-01T10:00:02+00:00'}}))
    result = report(workspace, capsys, '--group', str(group))
    assert result['pull_requests']['42']['check_outcomes'] == {'failed': 1}
    assert result['pull_requests']['42']['section_history']['check_outcomes'] == {'passed': 3}
    assert result['groups'][0]['verification']['check_wall_span_seconds'] == 2.0

    changed = json.loads(group.read_text())
    changed['handoff']['tree_sha'] = 'f' * 40
    group.write_text(json.dumps(changed))
    code, result = invoke(workspace, capsys, 'build-report', '--planning-dir', str(planning), '--group', str(group))
    assert code == 1 and 'group tree' in result['error']


def test_shared_section_does_not_assign_local_work_to_two_prs_or_targets(workspace, capsys, tmp_path):
    import uuid

    command = [sys.executable, '-c', 'pass']
    assert invoke(workspace, capsys, *verify_args(workspace, '--section', SECTION, '--', *command))[0] == 0
    identity = committed_group_identity(workspace, tmp_path, '101')
    args = []
    for value, (pr, target) in enumerate([('101', '.'), ('102', '.'), ('102', 'service')], start=1):
        group = tmp_path / f'group-{value}.json'
        verification = {'command': ['check', pr, target], 'run_id': uuid.uuid4().hex, 'seconds': value,
                        'returncode': 0, 'timed_out': False}
        group.write_text(json.dumps({'success': True, 'handoff': {**identity, 'target_path': target,
                'owned_paths': [f'{target}/src/labels.py'] if target != '.' else ['src/labels.py'], 'pull_request': pr},
                'verification': verification, 'history': {'group_check': verification}}))
        args.extend(['--group', str(group)])
    session = tmp_path / 'pr.json'
    session.write_text(json.dumps({'schema': 'forge-provider-review-v1', 'provider': 'codex', 'review_id': 'pr-check',
                                   'returncode': 0, 'success': True, 'usage':
                                   {'input_tokens': 21, 'cached_input_tokens': 5, 'output_tokens': 2}}))
    result = report(workspace, capsys, *args, *args, '--review', 'pr:102', str(session))
    assert result['summary']['observed_checks'] == 4
    assert [group['verification']['checks'] for group in result['groups']] == [1, 1, 1]
    summaries = result['pull_requests']
    assert summaries['101']['checks'] == 1 and summaries['101']['check_seconds'] == 1
    assert summaries['102']['checks'] == 2 and summaries['102']['check_seconds'] == 5
    assert summaries['102']['input_tokens'] == 21 and summaries['102']['review_rounds'] == 1
    assert summaries['101']['input_tokens'] is None
    for pr in summaries.values():
        assert pr['section_history']['checks'] == 1 and pr['sections'] == [SECTION]
