"""Forge cli."""

from __future__ import annotations

from typing import Any, NoReturn
from importlib import import_module

from . import exact_handoff_cli_shape
import argparse
import sys

from . import detached_contract as _detached_contract
from . import output as _output
from . import policy as _policy
from . import session as _session

def invoke_command(args: argparse.Namespace) -> int:
    module, handler = args.handler
    return getattr(import_module(f".{module}", __package__), handler)(args)


def command_catalog(args: argparse.Namespace) -> int:
    phase = getattr(args, "phase", None)
    commands = []
    for item in _policy.COMMAND_CATALOG:
        if phase and item["phase"] != phase and item["phase"] not in {"all", "quality", "utility"}:
            continue
        commands.append(
            dict(item)
            if getattr(args, "verbose", False)
            else {key: item[key] for key in ("name", "phase", "summary")}
        )
    return _output.print_json({"success": True, "phase_filter": phase, "commands": commands})


def add_quality_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", choices=sorted(_policy.QUALITY_PROFILES), default="solo")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--export")
    parser.add_argument("--export-format", choices=["jsonl", "sarif"], default="jsonl")


def add_flight_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--flight",
        dest="flight_mode",
        choices=["auto", "strict", "advisory", "off"],
        default="auto",
        help="Run phase-aware flight gates automatically.",
    )


def command_help(name: str) -> str | None:
    return _policy.COMMAND_SUMMARIES.get(name)


class ZagrosiArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message: str | None, file: Any = None) -> None:
        streams = _session._GATE_STREAMS.get()
        if streams is None:
            super()._print_message(message, file)
        elif message:
            streams[0 if file is sys.stdout else 1].write(message)

    def error(self, message: str) -> NoReturn:
        if self.prog.endswith(" implement-evidence-handoff"):
            raise SystemExit(2)
        super().error(message)


class SingleHandoffSectionAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str,
        option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            parser.error("privileged evidence handoff accepts exactly one section selector")
        setattr(namespace, self.dest, values)


def add_project_commands(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("project-setup", aliases=["project", "zagrosi-project-setup", "deep-project-setup"], help=command_help("project-setup"))
    p.add_argument("--file", help="Markdown requirements file. Optional when --brief is provided.")
    p.add_argument("--brief", help="Chat-supplied project brief to materialize into requirements.md.")
    p.add_argument("--planning-dir", help="Directory for generated project artifacts when using --brief.")
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES), default=_policy.DEFAULT_DEPTH)
    p.add_argument("--plugin-root")
    add_flight_args(p)
    p.set_defaults(func=invoke_command, handler=('workflows', 'deep_project_setup'))

    p = sub.add_parser("project-create-dirs", aliases=["zagrosi-project-create-dirs", "deep-project-create-dirs"], help=command_help("project-create-dirs"))
    p.add_argument("--planning-dir", required=True)
    add_flight_args(p)
    p.set_defaults(func=invoke_command, handler=('workflows', 'deep_project_create_dirs'))

    p = sub.add_parser("lint-project-manifest")
    p.add_argument("--planning-dir", required=True)
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('projects', 'lint_project_manifest'))


def add_plan_commands(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("plan-setup", aliases=["plan", "zagrosi-plan-setup", "deep-plan-setup"], help=command_help("plan-setup"))
    p.add_argument("--file", required=True)
    p.add_argument("--plugin-root")
    p.add_argument("--target-dir")
    p.add_argument("--write-evidence", action="store_true")
    p.add_argument("--review-mode", choices=["codex_review", "external_llm", "skip"], default="codex_review")
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES), default=_policy.DEFAULT_DEPTH)
    add_flight_args(p)
    p.set_defaults(func=invoke_command, handler=('workflows', 'deep_plan_setup'))

    p = sub.add_parser("plan-check-sections", aliases=["zagrosi-plan-check-sections", "deep-plan-check-sections"], help=command_help("plan-check-sections"))
    p.add_argument("--planning-dir", required=True)
    p.set_defaults(func=invoke_command, handler=('prompts', 'deep_plan_check_sections'))

    p = sub.add_parser(
        "plan-generate-section-prompts",
        aliases=["zagrosi-plan-generate-section-prompts", "deep-plan-generate-section-prompts"],
        help=command_help("plan-generate-section-prompts"),
    )
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES), default=_policy.DEFAULT_DEPTH)
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=invoke_command, handler=('prompts', 'deep_plan_generate_section_prompts'))

    p = sub.add_parser("lint-plan", help=command_help("lint-plan"))
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES))
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('validation', 'lint_plan'))

    p = sub.add_parser("review-capabilities", help=command_help("review-capabilities"))
    p.add_argument("--planning-dir")
    p.add_argument("--config")
    p.set_defaults(func=invoke_command, handler=('capabilities', 'review_capabilities'))

    p = sub.add_parser("planning-consistency", help=command_help("planning-consistency"))
    p.add_argument("--planning-dir", required=True)
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('planning_tools', 'planning_consistency'))

    p = sub.add_parser("traceability", help=command_help("traceability"))
    p.add_argument("--planning-dir", required=True)
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('traceability', 'traceability'))

    p = sub.add_parser("section-estimates")
    p.add_argument("--planning-dir", required=True)
    p.set_defaults(func=invoke_command, handler=('scheduling', 'section_estimates'))

    p = sub.add_parser("trace-export")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--format", choices=["json", "csv", "md"], default="json")
    p.add_argument("--output")
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('prompts', 'trace_export'))

    p = sub.add_parser("agent-prompts")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--type", choices=["all", *sorted(_policy.PROMPT_TYPES)], default="all")
    p.set_defaults(func=invoke_command, handler=('prompts', 'agent_prompts'))

    p = sub.add_parser("context-budget")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--max-words", type=int, default=3000)
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('context', 'context_budget'))

    p = sub.add_parser("lint-plan-artifacts")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--for-detached", dest="allow_compact", action="store_false", help="Check physical plan/review compatibility before freezing detached inputs.")
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('validation', 'lint_plan_artifacts'))

    p = sub.add_parser("assumption-ledger")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--write", action="store_true")
    p.set_defaults(func=invoke_command, handler=('context', 'assumption_ledger'))

    p = sub.add_parser("context-brief")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--section")
    p.add_argument("--lines-per-artifact", type=int, default=20)
    p.add_argument("--max-words", type=int, default=2000)
    p.add_argument("--output")
    p.set_defaults(func=invoke_command, handler=('context', 'context_brief'))

    p = sub.add_parser("tdd-skeletons")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--framework", choices=["pytest", "vitest", "go", "rust"], default="pytest")
    p.add_argument("--implementation-root", help="Existing external detached implementation root.")
    p.add_argument("--output-dir")
    p.set_defaults(func=invoke_command, handler=('context', 'tdd_skeletons'))

    p = sub.add_parser("plan-diff")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.set_defaults(func=invoke_command, handler=('context', 'plan_diff'))

    p = sub.add_parser("review-board-prompts")
    p.add_argument("--planning-dir", required=True)
    p.set_defaults(func=invoke_command, handler=('planning_tools', 'review_board_prompts'))


def add_implement_commands(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("implement-setup", aliases=["implement", "zagrosi-implement-setup", "deep-implement-setup"], help=command_help("implement-setup"))
    p.add_argument("--sections-dir", required=True)
    p.add_argument("--target-dir")
    p.add_argument("--plugin-root")
    p.add_argument("--implementation-root", help="External state/review/evidence/pinner root; activates detached frozen-planning mode.")
    p.add_argument("--admission-pinner", help="External canonical admission pinner required with --implementation-root.")
    p.add_argument("--expected-admission-pinner-sha256", help="Exact complete-file sha256 identity required for --admission-pinner.")
    p.add_argument("--expected-implement-tool-sha256", help="Exact complete-file sha256 of the running scripts/zagrosi_skills.py required in detached mode.")
    p.add_argument("--expected-implement-skill-sha256", help="Exact complete-file sha256 of skills/zagrosi-implement/SKILL.md required in detached mode.")
    p.add_argument("--expected-implement-test-sha256", help="Exact complete-file sha256 of tests/test_zagrosi_skills.py required in detached mode.")
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES), default=_policy.DEFAULT_DEPTH)
    p.add_argument("--profile", choices=sorted(_policy.QUALITY_PROFILES), default="solo")
    add_flight_args(p)
    p.set_defaults(func=invoke_command, handler=('workflows', 'deep_implement_setup'))

    p = sub.add_parser("implement-evidence-handoff", help=command_help("implement-evidence-handoff"))
    p.add_argument("--implementation-root", required=True)
    p.add_argument(
        "--section",
        required=True,
        choices=tuple(_detached_contract.HANDOFF_SECTION_CONTRACTS),
        action=SingleHandoffSectionAction,
    )
    p.set_defaults(func=invoke_command, handler=('handoff', 'detached_implement_evidence_handoff'))

    p = sub.add_parser(
        "implement-record-section",
        aliases=["zagrosi-implement-record-section", "deep-implement-record-section"],
        help=command_help("implement-record-section"),
    )
    p.add_argument("--sections-dir", required=True)
    p.add_argument("--section", required=True)
    p.add_argument("--commit")
    p.add_argument("--notes")
    p.add_argument("--file", action="append", dest="files_changed", default=[])
    p.add_argument("--test-file", action="append", dest="test_files", default=[])
    p.add_argument("--review-artifact", action="append", dest="review_artifacts", default=[])
    p.add_argument("--review-status", choices=["pass", "fixed", "blocked"])
    p.add_argument("--evidence-row", action="append", dest="evidence_rows", default=[], help="Detached canonical evidence binding as lower_snake_name=path.")
    p.add_argument("--verification", action="append", default=[])
    p.add_argument("--commit-status")
    p.add_argument("--target-dir")
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES), default=_policy.DEFAULT_DEPTH)
    p.add_argument("--profile", choices=sorted(_policy.QUALITY_PROFILES), default="solo")
    p.add_argument("--write-report", action="store_true")
    p.add_argument("--implementation-root", help="External detached implementation root created by implement-setup.")
    add_flight_args(p)
    p.set_defaults(func=invoke_command, handler=('workflows', 'deep_implement_record_section'))

    p = sub.add_parser("next-section")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--implementation-root", help="External detached implementation root created by implement-setup.")
    p.set_defaults(func=invoke_command, handler=('scheduling', 'next_section'))

    p = sub.add_parser("parallel-plan")
    p.add_argument("--planning-dir", required=True)
    p.set_defaults(func=invoke_command, handler=('scheduling', 'parallel_plan'))

    p = sub.add_parser("patch-scope")
    p.add_argument("--section-file", required=True)
    p.add_argument("--repo", default=".")
    p.add_argument("--diff-file")
    p.add_argument("--staged", action="store_true")
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('diffs', 'patch_scope'))

    p = sub.add_parser("commit-message")
    p.add_argument("--section-file", required=True)
    p.add_argument("--style", choices=["conventional", "simple"], default="conventional")
    p.set_defaults(func=invoke_command, handler=('diffs', 'commit_message'))

    p = sub.add_parser("implementation-drift")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--repo", default=".")
    p.add_argument("--diff-file")
    p.add_argument("--staged", action="store_true")
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('diffs', 'implementation_drift'))

    p = sub.add_parser("implementation-packet")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--section", required=True)
    p.add_argument("--implementation-root", help="Existing external detached implementation root.")
    p.add_argument("--output-dir")
    p.add_argument("--max-words", type=int, default=2000)
    p.set_defaults(func=invoke_command, handler=('context', 'implementation_packet'))

    p = sub.add_parser("implement-progress")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--implementation-root", help="External detached implementation root created by implement-setup.")
    p.add_argument("--section", required=True)
    p.add_argument("--stage", choices=["started", "red", "green", "refactor", "review", "verified", "recorded"], required=True)
    p.add_argument("--command")
    p.add_argument("--result")
    p.add_argument("--notes")
    p.set_defaults(func=invoke_command, handler=('scheduling', 'implement_progress'))


def add_utility_commands(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("preflight", help=command_help("preflight"))
    p.add_argument("--phase", choices=["project", "plan", "implement", "release"], required=True)
    p.add_argument("--file")
    p.add_argument("--brief")
    p.add_argument("--planning-dir")
    p.add_argument("--sections-dir")
    p.add_argument("--target-dir")
    p.add_argument("--plugin-root")
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES), default=_policy.DEFAULT_DEPTH)
    p.add_argument("--write-evidence", action="store_true")
    p.add_argument("--run-tests", action="store_true")
    add_quality_args(p)
    add_flight_args(p)
    p.set_defaults(func=invoke_command, handler=('flights', 'preflight'))

    p = sub.add_parser("postflight", help=command_help("postflight"))
    p.add_argument("--phase", choices=["project", "plan", "implement", "release"], required=True)
    p.add_argument("--file")
    p.add_argument("--planning-dir")
    p.add_argument("--sections-dir")
    p.add_argument("--target-dir")
    p.add_argument("--plugin-root")
    p.add_argument("--section-file")
    p.add_argument("--diff-file")
    p.add_argument("--staged", action="store_true")
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES), default=_policy.DEFAULT_DEPTH)
    p.add_argument("--write-report", action="store_true")
    p.add_argument("--run-tests", action="store_true")
    add_quality_args(p)
    add_flight_args(p)
    p.set_defaults(func=invoke_command, handler=('flights', 'postflight'))

    p = sub.add_parser("lint-sections", help=command_help("lint-sections"))
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES))
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('validation', 'lint_sections'))

    p = sub.add_parser("lint-implementation-state")
    p.add_argument("--sections-dir", required=True)
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('state', 'lint_implementation_state'))

    p = sub.add_parser("status", help=command_help("status"))
    p.add_argument("--path", required=True)
    p.set_defaults(func=invoke_command, handler=('status', 'status'))

    p = sub.add_parser("commands", aliases=["help-commands"], help=command_help("commands"))
    p.add_argument("--phase", choices=sorted({item["phase"] for item in _policy.COMMAND_CATALOG}))
    p.add_argument("--verbose", action="store_true", help="Include aliases and examples.")
    p.set_defaults(func=invoke_command, handler=("cli", "command_catalog"))

    p = sub.add_parser("workflow-options", help=command_help("workflow-options"))
    p.add_argument("--brief")
    p.add_argument("--spec-file")
    p.add_argument("--planning-dir")
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES))
    p.add_argument("--profile", choices=sorted(_policy.QUALITY_PROFILES), default="solo")
    p.set_defaults(func=invoke_command, handler=('capabilities', 'workflow_options'))

    p = sub.add_parser("capability-inventory", help=command_help("capability-inventory"))
    p.add_argument("--plugin-root")
    p.add_argument("--config")
    p.add_argument("--planning-dir")
    p.set_defaults(func=invoke_command, handler=('capabilities', 'capability_inventory'))

    p = sub.add_parser("doctor", help=command_help("doctor"))
    p.add_argument("--plugin-root")
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('doctor', 'doctor'))

    p = sub.add_parser("lint-interview")
    p.add_argument("--phase", choices=["project", "plan"], required=True)
    p.add_argument("--planning-dir", required=True)
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('projects', 'lint_interview'))

    p = sub.add_parser("update-check", help=command_help("update-check"))
    p.add_argument("--plugin-root", default=".")
    p.add_argument("--config")
    p.set_defaults(func=invoke_command, handler=('installation', 'update_check'))

    p = sub.add_parser("install-codex", aliases=["install", "install-plugin"], help=command_help("install-codex"))
    p.add_argument("--plugin-root", default=".")
    p.add_argument("--config")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-backup", action="store_true")
    p.add_argument(
        "--verify-codex",
        action="store_true",
        help="Fail if the codex CLI is unavailable or the installed skills are not visible.",
    )
    p.add_argument("--no-verify-codex", action="store_true", help="Skip codex debug prompt-input verification.")
    p.set_defaults(func=invoke_command, handler=('installation', 'install_codex'))

    p = sub.add_parser("self-update", help=command_help("self-update"))
    p.add_argument("--plugin-root", default=".")
    p.add_argument("--config")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-backup", action="store_true")
    p.add_argument(
        "--verify-codex",
        action="store_true",
        help="Fail if the codex CLI is unavailable or the installed skills are not visible.",
    )
    p.add_argument("--no-verify-codex", action="store_true", help="Skip codex debug prompt-input verification.")
    p.set_defaults(func=invoke_command, handler=('installation', 'install_codex'))

    p = sub.add_parser("extract-requirements")
    p.add_argument("--file", required=True)
    p.add_argument("--write", action="store_true")
    p.set_defaults(func=invoke_command, handler=('prompts', 'extract_requirements'))

    p = sub.add_parser("forge-score", help=command_help("forge-score"))
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES), default=_policy.DEFAULT_DEPTH)
    p.add_argument("--min-files", type=int, default=3)
    p.add_argument("--max-files", type=int, default=8)
    p.add_argument("--write-history", action="store_true")
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('scoring', 'forge_score'))

    p = sub.add_parser("lint-evidence", help=command_help("lint-evidence"))
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--min-files", type=int, default=3)
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('validation', 'lint_evidence'))

    p = sub.add_parser("lint-implementation-readiness", help=command_help("lint-implementation-readiness"))
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--max-files", type=int, default=8)
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('scoring', 'lint_implementation_readiness'))

    p = sub.add_parser("lint-review-integration")
    p.add_argument("--planning-dir", required=True)
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('validation', 'lint_review_integration'))

    p = sub.add_parser("lint-artifact-schema")
    p.add_argument("--planning-dir", required=True)
    add_quality_args(p)
    p.set_defaults(func=invoke_command, handler=('validation', 'lint_artifact_schema'))

    p = sub.add_parser("suggest-section-splits")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--max-files", type=int, default=8)
    p.add_argument("--max-words", type=int, default=700)
    p.set_defaults(func=invoke_command, handler=('planning_tools', 'suggest_section_splits'))

    p = sub.add_parser("codebase-evidence", help=command_help("codebase-evidence"))
    p.add_argument("--target-dir", default=".")
    p.add_argument("--planning-dir")
    p.add_argument("--max-tests", type=int, default=80)
    p.add_argument("--write", action="store_true")
    p.set_defaults(func=invoke_command, handler=('evidence', 'codebase_evidence'))

    p = sub.add_parser("report")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES), default=_policy.DEFAULT_DEPTH)
    p.add_argument("--profile", choices=sorted(_policy.QUALITY_PROFILES), default="solo")
    p.add_argument("--output")
    p.set_defaults(func=invoke_command, handler=('evaluations', 'html_report'))

    p = sub.add_parser("e2e-trial-record")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--target-repo")
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES), default=_policy.DEFAULT_DEPTH)
    p.add_argument("--profile", choices=sorted(_policy.QUALITY_PROFILES), default="solo")
    p.add_argument("--time-to-plan-minutes", type=float)
    p.add_argument("--implementation-success", choices=["unknown", "yes", "no", "partial"], default="unknown")
    p.add_argument("--rework-notes")
    p.add_argument("--notes")
    p.add_argument("--output-dir")
    p.set_defaults(func=invoke_command, handler=('evaluations', 'e2e_trial_record'))

    p = sub.add_parser("eval-suite", help=command_help("eval-suite"))
    p.add_argument("--examples-dir", default="examples")
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES), default=_policy.DEFAULT_DEPTH)
    p.add_argument("--profile", choices=sorted(_policy.QUALITY_PROFILES), default="solo")
    p.add_argument("--output")
    p.add_argument("--check-snapshots", action="store_true")
    p.add_argument("--update-snapshots", action="store_true")
    p.set_defaults(func=invoke_command, handler=('evaluations', 'eval_suite'))

    p = sub.add_parser("release-check", help=command_help("release-check"))
    p.add_argument("--plugin-root", default=".")
    p.add_argument("--run-tests", action="store_true")
    p.add_argument("--verbose", action="store_true", help="Include per-check commands and output tails on success.")
    p.set_defaults(func=invoke_command, handler=('installation', 'release_check'))

    p = sub.add_parser("write-governance-stubs")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES), default=_policy.DEFAULT_DEPTH)
    p.set_defaults(func=invoke_command, handler=('planning_tools', 'write_governance_stubs'))

    p = sub.add_parser("migrate")
    p.add_argument("--planning-dir", required=True)
    p.add_argument("--depth", choices=sorted(_policy.DEPTH_MODES), default=_policy.DEFAULT_DEPTH)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=invoke_command, handler=('planning_tools', 'migrate'))


def build_parser() -> argparse.ArgumentParser:
    parser = ZagrosiArgumentParser(description="Helpers for Zagrosi Forge Codex skills")
    parser.add_argument("--pretty", action="store_true", help="Print a human-readable report instead of JSON.")
    sub = parser.add_subparsers(dest="command", required=True)
    add_project_commands(sub)
    add_plan_commands(sub)
    add_implement_commands(sub)
    add_utility_commands(sub)
    return parser




def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if "implement-evidence-handoff" in raw_args and not exact_handoff_cli_shape(raw_args):
        return 2
    pretty = "--pretty" in raw_args
    raw_args = [item for item in raw_args if item != "--pretty"]
    parser = build_parser()
    args = parser.parse_args(raw_args)
    local_gates = (
        args.command in _policy.LOCAL_GATE_COMMANDS
        or args.handler[1] in {"deep_project_setup", "deep_plan_setup"}
        or (args.command in {"preflight", "postflight"} and args.phase in {"project", "plan"})
    )
    token = _session._CLI_CONTEXT.set({
        "parser": parser, "pretty": pretty or getattr(args, "pretty", False),
        "local_gates": local_gates, "texts": {} if local_gates else None,
    })
    try:
        return args.func(args)
    finally:
        _session._CLI_CONTEXT.reset(token)
