"""Bind local Forge plans to cooperative team claims at mutation boundaries."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import actions, compatibility, ownership, sections, storage, team_state


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
    return _plan_scope(planning, target, repo_root, section, prepared=prepared)


def _plan_key(planning: Path, target: Path, section: str | None) -> str:
    identity = json.dumps([str(planning), str(target), section], separators=(',', ':'))
    return hashlib.sha256(identity.encode()).hexdigest()


def _plan_scope(planning: Path, target: Path, repo_root: Path, section: str | None, *, prepared,
                contracts: dict[str, str] | None = None) -> tuple[str, list[str]]:
    """Derive scope using the contract validated in this same observation."""
    planning, target, repo_root = planning.resolve(), target.resolve(), repo_root.resolve()
    progress = sections.check_section_progress(planning)
    known = progress.get('sections', [])
    if progress.get('state') != 'complete' or not known or section is not None and section not in known:
        raise team_state.TeamError('team-invalid-plan', 'Choose a complete section manifest and a known section.')
    names = [section] if section else known
    contracts = {} if contracts is None else contracts
    paths = []
    for name in names:
        if name not in contracts:
            contracts[name] = storage.read_text(planning / 'sections' / f'{name}.md')
        declared = ownership.extract_section_owned_paths(contracts[name])
        if not declared:
            raise team_state.TeamError('team-unknown-scope', 'Declare owned paths before reserving implementation work.', section=name)
        paths.extend(declared)
    repo_paths = _repo_paths(target, repo_root, sorted(set(paths)))
    if prepared is None and planning.is_relative_to(repo_root):
        repo_paths.append(planning.relative_to(repo_root).as_posix())
    return _plan_key(planning, target, section), team_state.normalize_paths(sorted(set(repo_paths)), root=repo_root)


def _binding_scope(planning: Path, target: Path, section: str | None, bindings: dict, *, actor=None):
    """Prefer the selected section, then its whole-plan binding for the same actor."""
    planning, target = planning.resolve(), target.resolve()
    for selected in ([section, None] if section is not None else [None]):
        binding = bindings.get(_plan_key(planning, target, selected))
        if binding is None:
            continue
        if actor is not None and (not isinstance(binding, dict) or
                (binding.get('session_id'), binding.get('generation')) != actor):
            continue
        return binding, selected
    return None, section


def _declared_dependencies(contracts: dict[str, str], target: Path, repo_root: Path) -> dict | None:
    """Parse each bound section once; absence is unknown, malformed input is not."""
    paths, complete = [], True
    for name, text in contracts.items():
        try:
            declaration = compatibility.parse_declaration(text)
        except ValueError as exc:
            raise team_state.TeamError('team-invalid-plan', f'Invalid Compatibility declaration in {name}: {exc}',
                                       section=name) from exc
        if declaration is None or declaration['mode'] == 'not_required':
            complete = False
        else:
            paths.extend(declaration['source_paths'])
            paths.extend(declaration['check_paths'])
    return {'paths': _repo_paths(target, repo_root, sorted(set(paths))), 'complete': complete} if paths else None


def _check_dependencies(row: dict, declared: dict | None) -> None:
    if row.get('dependencies') != declared:
        raise team_state.TeamError('team-dependency-binding',
                                   'Declared inputs changed; explicitly refresh this task\'s actual plan binding before continuing.')


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
        contracts = {}
        _, required = _plan_scope(planning, target, repo.root, section, prepared=prepared, contracts=contracts)
        extra_paths = _repo_paths(target, repo.root, required_paths)
        bindings = checkout.get('bindings', {})
        binding, bound_section = _binding_scope(planning, target, section, bindings)
        if section is not None and (binding is None or bound_section is None):
            _plan_scope(planning, target, repo.root, None, prepared=prepared, contracts=contracts)
        binding_args = ['--planning-dir', str(planning)] + (['--section', bound_section] if bound_section else [])
        if binding is None:
            candidates = [{'id': identity, **row} for identity, row in snapshot.board['sessions'].items()
                          if (row['participant_id'], row['checkout_id'], row['generation']) == (
                              context['state']['participant_id'], checkout['checkout_id'],
                              checkout['sessions'].get(identity))]
            if candidates:
                for row in candidates:
                    name = 'team_update' if len(candidates) == 1 else f"team_update_{row['id']}"
                    paths = sorted(set(row['paths'] + extra_paths))
                    path_args = [value for path in paths for value in ('--path', path)]
                    commands[name] = command('update', '--session', row['id'], '--generation', row['generation'],
                                             *scope_args, *path_args)
                message = ('Bind the existing task to this plan and target before working.' if len(candidates) == 1 else
                           'Choose which existing task to bind to this plan and target before working.')
                raise team_state.TeamError('team-binding-required', message,
                                          candidates=[{key: row[key] for key in ('id', 'generation', 'host', 'task', 'state')}
                                                      for row in candidates])
            path_args = [value for path in extra_paths for value in ('--path', path)]
            commands['team_start'] = command('start', '--task', '<task>', '--host', 'other', *scope_args, *path_args)
            raise team_state.TeamError('team-binding-required', 'Start a team session for this plan and target before working.')
        if not isinstance(binding, dict) or any(
            not isinstance(binding.get(name), str) or not re.fullmatch(r'[a-f0-9]{32}', binding[name])
            for name in ('session_id', 'generation')
        ):
            raise team_state.TeamError('team-local-state', 'The saved plan binding is invalid; preserve local state before recovery.')
        session_id, generation = binding['session_id'], binding['generation']
        identity = ['--session', session_id, '--generation', generation]
        path_args = [value for path in extra_paths for value in ('--path', path)]
        commands['team_check'] = command('check', *identity, *scope_args, *path_args)
        commands['team_update'] = command('update', *identity, *binding_args, *path_args)
        required = sorted(set(required + extra_paths))
        try:
            checked = team.check(context, session_id, generation=generation, paths=required)
        except team_state.TeamError as exc:
            if exc.code == 'team-no-reservation':
                # This error follows the ownership and checkout checks. Binding
                # alone must not resume a handoff; offer an explicit user choice.
                row = snapshot.board['sessions'][session_id]
                if row['state'] == 'handoff':
                    paths = sorted(set(row['paths'] + extra_paths))
                    resume_paths = [value for path in paths for value in ('--path', path)]
                    commands['team_resume'] = command('update', *identity, *binding_args, *resume_paths,
                                                      '--state', 'working')
                    raise team_state.TeamError(exc.code, 'This task is handed off. If you choose to resume it, run team_resume before working.') from exc
            raise
        if prepared is not None and checked['session'].get('plan') != {**prepared, 'section': bound_section}:
            raise team_state.TeamError('team-plan-binding', 'Update the team session to bind this prepared contract and section before working.')
        if checked.get('protocol_version') == 2:
            _check_dependencies(checked['session'], _declared_dependencies(contracts, target, repo.root))
        return {'success': True, 'session_id': session_id, 'generation': generation,
                'revision': snapshot.revision, 'paths': required,
                **{key: checked[key] for key in ('protocol_version', 'dependency_awareness') if key in checked}}
    except team_state.TeamError as exc:
        if exc.code in {'join_required', 'team-join-required'}:
            commands['team_join'] = command('join', '--remote', '<remote-name>', '--name', '<display-name>')
        return {'success': False, 'error_code': exc.code, 'error': str(exc),
                'details': exc.details, 'next_action': str(exc), 'commands': commands}
