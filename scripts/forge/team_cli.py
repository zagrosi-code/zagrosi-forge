"""Small public collaboration commands, shared by Codex and Claude Code."""
from __future__ import annotations

from pathlib import Path


def add_team_commands(sub, invoke_command):
    parser = sub.add_parser("team", help="Coordinate work with engineers using the same Git repository.")
    actions = parser.add_subparsers(dest="team_action", required=True)
    descriptions = {
        "init": "Create a shared work board and discovery file.",
        "join": "Opt this clone into an existing board.",
        "status": "Read active tasks and visibility freshness.",
        "prepare": "Prepare a committed shared plan in a private execution workspace.",
        "start": "Announce work and atomically reserve its paths.",
        "update": "Refresh your task, scope or handoff note.",
        "check": "Check current ownership before work.",
        "finish": "Release your task and preserve its delivery note.",
        "recover": "Take an explicitly agreed handoff at an observed revision.",
        "retry": "Reconcile or retry the exact pending publication.",
        "leave": "Disconnect local participation; preserve remote history.",
    }
    for action, help_text in descriptions.items():
        command = actions.add_parser(action, help=help_text)
        command.add_argument("--target-dir", default=".")
        command.set_defaults(handler=("team_cli", "run"))
        command.set_defaults(func=invoke_command)
        if action in {"init", "join"}:
            command.add_argument("--remote", default="origin")
            command.add_argument("--name", required=True)
        if action == "status":
            command.add_argument("--offline", action="store_true")
        if action == "leave":
            command.add_argument("--abandon", action="store_true", help="Forget local participation without releasing remote claims.")
        if action in {"update", "check", "finish", "recover"}:
            command.add_argument("--session", required=True)
            command.add_argument("--generation", required=action != "recover")
        if action in {"start", "update", "check"}:
            command.add_argument("--path", action="append")
            command.add_argument("--planning-dir")
            command.add_argument("--section")
        if action == "prepare":
            command.add_argument("--planning-dir", required=True)
        if action == "start":
            command.add_argument("--task", required=True)
        if action in {"start", "update"}:
            command.add_argument("--upgrade-protocol", action="store_true",
                                 help="Publish protocol v2; all participants need a compatible Forge client.")
            command.add_argument("--expect", help="Exact v1 board revision reviewed for this protocol upgrade.")
        if action in {"start", "update", "recover"}:
            command.add_argument("--host", choices=["codex", "claude", "other"], default=None)
        if action == "update":
            command.add_argument("--state", choices=["planning", "working", "blocked", "review", "handoff"])
        if action in {"update", "finish"}:
            command.add_argument("--note", required=action == "finish")
        if action == "recover":
            command.add_argument("--expect", required=True, help="Exact board revision reviewed for this handoff.")
            command.add_argument("--reason", required=True)


def _dispatch(args):
    from . import team
    from .team_state import TeamError
    from .team_workflow import _binding_scope, _check_dependencies, _declared_dependencies, _plan_scope

    action = args.team_action
    if action == "prepare":
        from . import team_plans

        return team_plans.prepare(Path(args.planning_dir), Path(args.target_dir))
    if action == "status" and not team._team_hint(Path(args.target_dir).resolve()):
        return team.roster(None)
    with team.workspace(args.target_dir) as context:
        if action in {"init", "join"}:
            return team.onboard(context, action, args.remote, args.name)
        if action == "leave":
            return team.leave(context, abandon=args.abandon)
        observed = team._observe(context, offline=getattr(args, "offline", False))
        if action == "status":
            return team.roster(observed)
        if observed is None:
            raise TeamError("team-not-configured", "Initialize or join a team board before publishing work.")
        if context.get("reconciled") and action in {"start", "update", "finish", "recover", "retry"}:
            return {**team._result(context, context["reconciled"]), "recovered_operation": True,
                    "next_action": "Previous publication confirmed. Review its result before starting another operation."}
        if action == "retry":
            return team.retry(context)
        paths, binding = getattr(args, "path", None), None
        plan_fields, contracts = {}, {}
        if getattr(args, "section", None) and not args.planning_dir:
            raise TeamError("team-invalid-plan", "A section selector requires --planning-dir.")
        if getattr(args, "planning_dir", None):
            from . import team_plans

            prepared = team_plans.validate(Path(args.planning_dir), Path(args.target_dir))
            binding, declared = _plan_scope(Path(args.planning_dir), Path(args.target_dir), context["repo"].root,
                                            args.section, prepared=prepared, contracts=contracts)
            paths = sorted(set((paths or []) + declared))
            plan_fields["plan"] = {**prepared, "section": args.section} if prepared is not None else None
        if action == "check":
            result = team.check(context, args.session, generation=args.generation, paths=paths or [])
            if plan_fields.get("plan") is not None:
                current, expected = result["session"].get("plan"), plan_fields["plan"]
                if current not in (expected, {**expected, "section": None}):
                    raise TeamError("team-plan-binding", "Bind this prepared plan revision before continuing.")
            if contracts and result["protocol_version"] == 2:
                from . import actions

                planning, target = Path(args.planning_dir), Path(args.target_dir)
                _, bound_section = _binding_scope(planning, target, args.section,
                                                  context["checkout"].get("bindings", {}),
                                                  actor=(args.session, args.generation))
                if args.section is not None and bound_section is None:
                    _plan_scope(planning, target, context["repo"].root, None,
                                prepared=prepared, contracts=contracts)
                try:
                    _check_dependencies(result["session"],
                                        _declared_dependencies(contracts, target, context["repo"].root))
                except TeamError as exc:
                    selection = ["--section", bound_section] if bound_section else []
                    command = actions.command("team", "update", "--target-dir", str(target),
                                              "--session", args.session, "--generation", args.generation,
                                              "--planning-dir", str(planning), *selection)
                    raise TeamError(exc.code, str(exc), **exc.details,
                                    commands={"team_update": command}) from exc
            return result
        if contracts:
            snapshot = context["snapshot"]
            upgrading = (getattr(args, "upgrade_protocol", False) and snapshot.board["version"] == 1
                         and getattr(args, "expect", None) == snapshot.revision)
            if snapshot.board["version"] == 2 or upgrading:
                plan_fields["dependencies"] = _declared_dependencies(
                    contracts, Path(args.target_dir), context["repo"].root)
        fields = {key: getattr(args, key) for key in ("generation", "task", "host", "state", "note", "expect", "reason", "upgrade_protocol")
                  if hasattr(args, key)}
        return team.mutate(context, action, identity=getattr(args, "session", None), paths=paths,
                           binding=binding, **plan_fields, **fields)


def run(args):
    from . import output, session
    from .team_state import TeamError

    try:
        result = _dispatch(args)
    except TeamError as exc:
        result = {"success": False, "clearance": False, "status": "unavailable",
                  "error_code": exc.code, "error": str(exc), **exc.details}
    except (OSError, ValueError) as exc:
        result = {"success": False, "clearance": False, "status": "unavailable",
                  "error_code": "team-local-error", "error": "The local team operation failed; preserve its state and inspect repository access.",
                  "failure_type": type(exc).__name__}
    if (session._CLI_CONTEXT.get() or {}).get("pretty"):
        lines = [f"Forge team: {result.get('status', 'unknown')}"]
        for key, label in (("revision", "Board revision"), ("planning_dir", "Planning directory"),
                           ("sections_dir", "Sections directory")):
            if result.get(key):
                lines.append(f"{label}: {result[key]}")
        for row in result.get("sessions", []):
            scope = ", ".join(row["paths"]) or "no paths reserved"
            lines.append(f"{row['name']} · {row['id']} · {row['host']} · {row['state']}: {row['task']} [{scope}]"
                         + (" — stale; reservation retained" if row["stale"] else ""))
            lines.append(f"  Generation: {row['generation']}")
            if plan := row.get("plan"):
                lines.append(f"  Plan: {plan['path']} · {plan['digest'][:12]} · {plan['section'] or 'whole plan'}")
        lines.extend(output.format_dependency_awareness(result))
        lines.extend(output.format_actions(result))
        if result.get("note"):
            lines.append(result["note"])
        lines.append("Editing clearance: " + ("current reservation checked" if result.get("clearance") else "not established"))
        print("\n".join(lines))
        return 0 if result["success"] else 1
    return output.print_json(result, 0 if result["success"] else 1)
