"""Bind local Forge plans to cooperative team claims at mutation boundaries."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import actions, ownership, sections, storage, team_state


def command_guard(args) -> None:
    """Reject stale prepared inputs before handlers create locks or generated files."""
    if getattr(args, 'implementation_root', None) or args.handler[0] == 'team_cli':
        return

    planning = None
    if value := getattr(args, 'planning_dir', None):
        planning = storage.absolute_path_no_follow(value)
    elif value := getattr(args, 'sections_dir', None):
        planning = storage.absolute_path_no_follow(value).parent
    elif value := getattr(args, 'section_file', None):
        planning = storage.absolute_path_no_follow(value).parent.parent
    elif value := getattr(args, 'file', None):
        planning = storage.absolute_path_no_follow(value).parent
    elif value := getattr(args, 'path', None):
        path = storage.absolute_path_no_follow(value)
        if path.is_file() or path.name in {'zagrosi_implement_config.json', 'deep_implement_config.json',
                                          'zagrosi_implement_state.json', 'deep_implement_state.json', 'forge-progress.json'}:
            path = path.parent
        planning = path.parent if path.name in {'sections', 'implementation'} else path
    if planning is None:
        return
    from . import team_plans

    target = getattr(args, 'target_dir', None)
    target = storage.resolve_path(target) if target else None
    # Nested contract files may also have ordinary directories named forge-plans.
    # Only validation can identify the actual prepared ancestor.
    candidates = [planning, *(path for path in planning.parents if path.parent.name == 'forge-plans')]
    for candidate in candidates:
        if team_plans.validate(candidate, target) is not None:
            planning = candidate
            break
    else:
        return
    handler = args.handler[1]
    if handler in {'deep_project_setup', 'deep_project_create_dirs', 'deep_plan_setup',
                   'deep_plan_generate_section_prompts', 'agent_prompts', 'review_board_prompts',
                   'e2e_trial_record', 'write_governance_stubs', 'migrate'} or handler in {'extract_requirements', 'codebase_evidence'} and getattr(args, 'write', False):
        raise team_state.TeamError('team-plan-readonly', 'Author the canonical plan before preparing it; prepared contracts are immutable.')
    for name in ('output', 'output_dir', 'export'):
        if value := getattr(args, name, None):
            if not team_plans.mutable_path(planning, storage.absolute_path_no_follow(value)):
                raise team_state.TeamError('team-plan-output', 'Prepared-plan output must stay in its private generated-artifact locations.')


def _repo_paths(target: Path, root: Path, paths) -> list[str]:
    try:
        prefix = target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise team_state.TeamError('team-target-outside', 'The plan target must be inside the team repository.') from exc
    names = team_state.normalize_paths(list(paths))
    return team_state.normalize_paths([(prefix / name).as_posix() for name in names], root=root)


def plan_scope(planning: Path, target: Path, repo_root: Path, section: str | None = None) -> tuple[str, list[str]]:
    """Reserve declared source paths and any shared planning directory."""
    from . import team_plans

    prepared = team_plans.validate(planning, target)
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
    if prepared is None and planning.is_relative_to(repo_root):
        repo_paths.append(planning.relative_to(repo_root).as_posix())
    identity = json.dumps([str(planning), str(target), section], separators=(',', ':'))
    return hashlib.sha256(identity.encode()).hexdigest(), team_state.normalize_paths(sorted(set(repo_paths)), root=repo_root)


def guard(planning: Path, target: Path, section: str | None = None, *, required_paths=()) -> dict | None:
    """Return None for solo work; configured work needs a fresh matching claim."""
    from . import team, team_plans

    scope_args = ['--planning-dir', str(planning)] + (['--section', section] if section else [])
    def command(action, *args):
        return actions.command('team', action, '--target-dir', str(target), *args)

    commands = {'team_status': command('status')}
    try:
        prepared = team_plans.validate(planning, target)
        context = team.read_context(target)
        if context is None:
            return None
        repo, checkout = context['repo'], context['checkout']
        snapshot = context['snapshot']
        key, required = plan_scope(planning, target, repo.root, section)
        bindings = checkout.get('bindings', {})
        binding = bindings.get(key)
        binding_args = scope_args
        bound_section = section
        if binding is None and section is not None:
            full_key, _ = plan_scope(planning, target, repo.root)
            binding = bindings.get(full_key)
            binding_args = ['--planning-dir', str(planning)]
            bound_section = None
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
        checked = team.check(context, session_id, generation=generation, paths=required)
        if prepared is not None and checked['session'].get('plan') != {**prepared, 'section': bound_section}:
            raise team_state.TeamError('team-plan-binding', 'Update the team session to bind this prepared contract and section before working.')
        return {'success': True, 'session_id': session_id, 'generation': generation,
                'revision': snapshot.revision, 'paths': required}
    except team_state.TeamError as exc:
        if exc.code in {'join_required', 'team-join-required'}:
            commands['team_join'] = command('join', '--remote', '<remote-name>', '--name', '<display-name>')
        return {'success': False, 'error_code': exc.code, 'error': str(exc),
                'details': exc.details, 'next_action': str(exc), 'commands': commands}
