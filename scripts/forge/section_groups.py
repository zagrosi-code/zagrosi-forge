"""Portable section groups bound to a committed plan and an exact Git tree."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import re
import os
from pathlib import Path
import tempfile
import uuid


def add_commands(sub, invoke_command):
    command = sub.add_parser('section-group', help='Hand off and verify selected sections on an exact Git tree.')
    actions = command.add_subparsers(dest='group_action', required=True)
    for action in ('create', 'inspect', 'verify'):
        parser = actions.add_parser(action)
        parser.set_defaults(func=invoke_command, handler=('section_groups', 'run'))
        parser.add_argument('--target-dir', default='.')
        if action == 'create':
            parser.add_argument('--planning-dir', required=True)
            parser.add_argument('--pr', help='Pull request identifier for reporting.')
            parser.add_argument('--sections', nargs='+', required=True)
        else:
            parser.add_argument('--handoff', required=True)
        if action == 'verify':
            parser.add_argument('--timeout', type=int, default=540)
            parser.add_argument('argv', nargs=argparse.REMAINDER)


def _fail(message):
    from . import team_state

    raise team_state.TeamError('section-group-invalid', message)


def _describe(repo, target, planning, selected, pull_request):
    from . import markdown, ownership, policy, team_git, team_plans, team_state

    if pull_request is not None and not team_state.is_plain_text(pull_request, 200):
        _fail('Use a nonempty pull request identifier of at most 200 characters.')
    prepared = team_plans.validate(planning, target)
    canonical = repo.root / prepared['path'] if prepared else planning
    relative = canonical.relative_to(repo.root).as_posix()
    files, content = team_plans._bundle(repo, relative)
    digest = team_plans._digest(files)
    if prepared and prepared['digest'] != digest:
        _fail('The prepared plan differs from the committed plan on this checkout.')
    team_plans._contract(canonical)
    known, errors = markdown.parse_numbered_manifest(content['sections/index.md'].decode('utf-8'),
                                                    'SECTION_MANIFEST', policy.SECTION_RE, prefix='section-')
    if errors or not set(selected).issubset(known):
        _fail('Choose sections included in the committed plan manifest.')
    prefix = target.relative_to(repo.root)
    owned = set()
    for name in selected:
        declared = ownership.extract_section_owned_paths(content[f'sections/{name}.md'].decode('utf-8'))
        if not declared:
            _fail(f'Section {name} must declare its owned paths.')
        owned.update((prefix / path).as_posix() for path in team_state.normalize_paths(declared))
    owned = team_state.normalize_paths(sorted(owned))
    tree = team_git._output(repo.root, 'rev-parse', '--verify', f'{repo.head}^{{tree}}')
    return {'schema': 'forge-section-group-v1', 'commit_sha': repo.head, 'tree_sha': tree,
            'plan_digest': digest, 'plan_path': relative, 'target_path': target.relative_to(repo.root).as_posix(),
            'sections': selected, 'owned_paths': owned, 'pull_request': pull_request}


def _read_handoff(path, repo, target):
    from . import team_git, team_plans, team_workflow

    record = team_git.decode_json(team_plans._read(path))
    identity = record.get('handoff') if isinstance(record, dict) else None
    if not isinstance(identity, dict) or identity.get('schema') != 'forge-section-group-v1':
        _fail('Expected a Forge section-group handoff.')
    if identity.get('commit_sha') != repo.head:
        _fail('The receiving checkout must be at the exact handoff commit.')
    canonical = '.' if identity.get('plan_path') == '.' else team_plans._name(identity.get('plan_path'))
    selected = team_workflow.group_selection(identity.get('sections'))
    current = _describe(repo, target, repo.root / canonical, selected, identity.get('pull_request'))
    if current != identity:
        _fail('The handoff differs from the locally derived plan, scope, target or Git tree.')
    return current, _received_history(record, selected)


def _local_history(planning, selected, repo):
    from . import mutable_inputs, state, team_git, team_plans

    path = state.implementation_state_path(planning)
    saved = None
    if path.exists():
        team_plans._no_links(path.parent, planning)
        saved = state.validate_implementation_state(team_git.decode_json(team_plans._read(path, 8 * 1024 * 1024)), path)
    rows = {}
    for name in selected:
        record = (saved or {}).get('completed_sections', {}).get(name)
        row = {'status': 'unknown' if saved is None else 'pending'}
        if isinstance(record, dict):
            row = {'status': 'recorded', 'record_sha256': mutable_inputs.digest(record)}
            result = record.get('verification_result')
            if isinstance(result, dict):
                if result.get('source') in ('captured', 'attestation', 'inspection'):
                    row['verification_source'] = result['source']
                if result.get('outcome') in ('passed', 'failed', 'skipped', 'unknown'):
                    row['verification_outcome'] = result['outcome']
                row['verification_sha256'] = mutable_inputs.digest(result)
            commit = record.get('commit')
            if isinstance(commit, str) and re.fullmatch(r'[0-9a-f]{7,64}', commit):
                raw = team_git._run(repo.root, 'rev-parse', '--verify', f'{commit}^{{commit}}', check=False)
                if raw['returncode'] == 0:
                    row['commit_sha'] = raw['stdout'].strip()
                    row['tree_sha'] = team_git._output(repo.root, 'rev-parse', f"{row['commit_sha']}^{{tree}}")
        rows[name] = row
    return {'sections': rows, 'group_check': None}


def _received_history(record, selected):
    # Sender-supplied summaries can travel again. They never enter implementation state.
    history = record.get('history')
    history = history if isinstance(history, dict) else {}
    reported = history.get('sections')
    reported = reported if isinstance(reported, dict) else {}
    sections = {}
    for name in selected:
        value = reported.get(name)
        row = {'status': 'unknown'}
        if isinstance(value, dict) and value.get('status') in ('recorded', 'pending', 'unknown'):
            row['status'] = value['status']
            if value.get('verification_source') in ('captured', 'attestation', 'inspection'):
                row['verification_source'] = value['verification_source']
            if value.get('verification_outcome') in ('passed', 'failed', 'skipped', 'unknown'):
                row['verification_outcome'] = value['verification_outcome']
            for key, pattern in [('record_sha256', r'[a-f0-9]{64}'), ('verification_sha256', r'[a-f0-9]{64}'), ('commit_sha', r'(?:[a-f0-9]{40}|[a-f0-9]{64})'),
                                 ('tree_sha', r'(?:[a-f0-9]{40}|[a-f0-9]{64})')]:
                if isinstance(value.get(key), str) and re.fullmatch(pattern, value[key]):
                    row[key] = value[key]
        sections[name] = row
    previous = history.get('group_check')
    check = None
    if isinstance(previous, dict):
        run = previous.get('run_id')
        duration = previous.get('seconds')
        if (isinstance(run, str) and re.fullmatch(r'[a-f0-9]{32}', run)
                and type(duration) in (int, float) and 0 <= duration <= 172800
                and type(previous.get('returncode')) is int and type(previous.get('passed')) is bool
                and isinstance(previous.get('command_sha256'), str)
                and re.fullmatch(r'[a-f0-9]{64}', previous['command_sha256'])):
            check = {k: previous[k] for k in ('run_id', 'seconds', 'returncode', 'passed', 'command_sha256')}
    return {'sections': sections, 'group_check': check}


def _check_snapshot(root, expected):
    from . import team_git

    if (team_git._output(root, 'rev-parse', 'HEAD') != expected
            or team_git._output(root, 'status', '--porcelain', '--untracked-files=normal')):
        _fail('The verification checkout changed during setup or execution.')


def verify(repo, identity, argv, timeout):
    from . import child_process, team_git, team_state

    if not argv or not 0 < timeout <= 86400:
        _fail('Supply a local check command after -- and a timeout between 1 and 86400 seconds.')
    with tempfile.TemporaryDirectory(prefix='forge-section-group-') as private:
        clone = Path(private) / 'checkout'
        team_git._run(Path(private), 'clone', '--shared', '--no-checkout', '--', str(repo.root), str(clone))
        team_git._run(clone, '-c', f'core.hooksPath={Path(private) / "empty-hooks"}',
                      'checkout', '--detach', identity['commit_sha'])
        _check_snapshot(clone, identity['commit_sha'])
        env = {key: value for key, value in os.environ.items() if key not in team_git._REPOSITORY_ENV}
        env['GIT_NO_REPLACE_OBJECTS'] = '1'
        started_at = datetime.now(timezone.utc).isoformat()
        workspace = clone / identity['target_path']
        command = argv
        if os.path.dirname(argv[0]) and not os.path.isabs(argv[0]):
            command = [str(workspace / argv[0]), *argv[1:]]
        observed = child_process.execute(command, workspace, timeout=timeout, inherit_env=False, env=env)
        finished_at = datetime.now(timezone.utc).isoformat()
        try:
            _check_snapshot(clone, identity['commit_sha'])
        except team_state.TeamError as exc:
            observed['snapshot_error'] = str(exc)
        return {'run_id': uuid.uuid4().hex, 'command': argv, 'started_at': started_at,
                'finished_at': finished_at, **observed}


def run(args):
    from . import mutable_inputs, output, team_git, team_workflow

    try:
        target = Path(args.target_dir).resolve()
        repo = team_git.Repository.discover(target)
        if args.group_action == 'create':
            planning = Path(args.planning_dir).resolve()
            selected = team_workflow.group_selection(args.sections)
            identity = _describe(repo, target, planning, selected, args.pr)
            history = _local_history(planning, selected, repo)
        else:
            identity, history = _read_handoff(Path(args.handoff), repo, target)
        result = {'success': True, 'handoff': identity, 'history': history}
        if args.group_action == 'verify':
            argv = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
            observed = verify(repo, identity, argv, args.timeout)
            passed = observed['returncode'] == 0 and not any(observed.get(key) for key in
                                                             ('timed_out', 'termination_error', 'snapshot_error'))
            result.update(success=passed, verification=observed)
            if observed.get('snapshot_error'):
                result['error'] = observed['snapshot_error']
            history['group_check'] = {key: observed[key] for key in ('run_id', 'seconds', 'returncode')}
            history['group_check'].update(passed=bool(passed), command_sha256=mutable_inputs.digest(argv))
        return output.print_json(result, 0 if result['success'] else 1)
    except (OSError, ValueError, RuntimeError) as exc:
        return output.print_json({'success': False, 'error': str(exc)}, 1)
