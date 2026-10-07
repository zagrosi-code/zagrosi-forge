"""Bind local Forge plans to cooperative team claims at mutation boundaries."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import actions, ownership, sections, storage, team_state


def _repo_paths(target: Path, root: Path, paths) -> list[str]:
    try:
        prefix = target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise team_state.TeamError('team-target-outside', 'The plan target must be inside the team repository.') from exc
    names = team_state.normalize_paths(list(paths))
    return team_state.normalize_paths([(prefix / name).as_posix() for name in names], root=root)


def plan_scope(planning: Path, target: Path, repo_root: Path, section: str | None = None) -> tuple[str, list[str]]:
    """Reserve declared source paths and any shared planning directory."""
    planning, target, repo_root = planning.resolve(), target.resolve(), repo_root.resolve()
    progress = sections.check_section_progress(planning)
    known = progress.get('sections', [])
    if progress.get('state') != 'complete' or not known or section is not None and section not in known:
        raise team_state.TeamError('team-invalid-plan', 'Choose a complete section manifest and a known section.')
    names = [section] if section else known
    paths = []
    for name in names:
        declared = ownership.extract_section_owned_paths(storage.read_text(planning / 'sections' / f'{name}.md'))
        if not declared:
            raise team_state.TeamError('team-unknown-scope', 'Declare owned paths before reserving implementation work.', section=name)
        paths.extend(declared)
    repo_paths = _repo_paths(target, repo_root, sorted(set(paths)))
    if planning.is_relative_to(repo_root):
        repo_paths.append(planning.relative_to(repo_root).as_posix())
    identity = json.dumps([str(planning), str(target), section], separators=(',', ':'))
    return hashlib.sha256(identity.encode()).hexdigest(), team_state.normalize_paths(sorted(set(repo_paths)), root=repo_root)


def guard(planning: Path, target: Path, section: str | None = None, *, required_paths=()) -> dict | None:
    """Return None for solo work; configured work needs a fresh matching claim."""
    from . import team

    scope_args = ['--planning-dir', str(planning)] + (['--section', section] if section else [])
    def command(action, *args):
        return actions.command('team', action, '--target-dir', str(target), *args)

    commands = {'team_status': command('status')}
    try:
        context = team.read_context(target)
        if context is None:
            return None
        repo, checkout = context['repo'], context['checkout']
        snapshot = context['snapshot']
        key, required = plan_scope(planning, target, repo.root, section)
        bindings = checkout.get('bindings', {})
        binding = bindings.get(key)
        binding_args = scope_args
        if binding is None and section is not None:
            full_key, _ = plan_scope(planning, target, repo.root)
            binding = bindings.get(full_key)
            binding_args = ['--planning-dir', str(planning)]
        if binding is None:
            commands['team_start'] = command('start', '--task', '<task>', '--host', 'other', *scope_args)
            raise team_state.TeamError('team-binding-required', 'Start or bind a team session for this plan and target before working.')
        if not isinstance(binding, dict) or any(
            not isinstance(binding.get(name), str) or not re.fullmatch(r'[a-f0-9]{32}', binding[name])
            for name in ('session_id', 'generation')
        ):
            raise team_state.TeamError('team-local-state', 'The saved plan binding is invalid; preserve local state before recovery.')
        session_id, generation = binding['session_id'], binding['generation']
        identity = ['--session', session_id, '--generation', generation]
        extra_paths = _repo_paths(target, repo.root, required_paths)
        path_args = [value for path in extra_paths for value in ('--path', path)]
        commands['team_check'] = command('check', *identity, *scope_args, *path_args)
        commands['team_update'] = command('update', *identity, *binding_args, *path_args)
        required = sorted(set(required + extra_paths))
        team.check(context, session_id, generation=generation, paths=required)
        return {'success': True, 'session_id': session_id, 'generation': generation,
                'revision': snapshot.revision, 'paths': required}
    except team_state.TeamError as exc:
        if exc.code in {'join_required', 'team-join-required'}:
            commands['team_join'] = command('join', '--remote', '<remote-name>', '--name', '<display-name>')
        return {'success': False, 'error_code': exc.code, 'error': str(exc),
                'details': exc.details, 'next_action': str(exc), 'commands': commands}
