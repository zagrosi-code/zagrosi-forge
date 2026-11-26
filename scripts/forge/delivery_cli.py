"""Command arguments for repository-local PR and delivery evidence."""
from __future__ import annotations


def add_delivery_commands(sub, invoke_command):
    parser = sub.add_parser("delivery", help="Track PR stacks, tested commits and delivery evidence locally.")
    actions = parser.add_subparsers(dest="delivery_action", required=True)
    for name, help_text in (
        ("track", "Register or retarget a PR and its expected base."),
        ("test", "Record the exact checkout or CI merge tested."),
        ("observe", "Record one independent delivery observation."),
        ("status", "Compare current local refs with recorded evidence."),
    ):
        command = actions.add_parser(name, help=help_text)
        command.set_defaults(func=invoke_command, handler=("delivery", "run"))
        command.add_argument("--target-dir", default=".")
        command.add_argument("--pr", required=name != "status", help="Repository-local PR identifier.")
        if name == "track":
            command.add_argument("--head-ref", required=True, help="Full local branch or remote-tracking ref.")
            command.add_argument("--base-ref", required=True, help="Full local branch or remote-tracking ref.")
            command.add_argument("--expected-base", help="Full base commit observed when choosing these refs.")
            command.add_argument("--parent", help="Previously tracked parent PR. Omit for a stack root.")
        if name == "test":
            subject = command.add_mutually_exclusive_group(required=True)
            subject.add_argument("--tested-sha", help="Exact commit exercised by the cited external check.")
            subject.add_argument("--group-report", help="Saved result of section-group verify for this PR.")
            subject.add_argument("--checkout", action="store_true", help="Read HEAD and require a clean checkout.")
            command.add_argument("--outcome", choices=("passed", "failed", "skipped"), help="Manual result, default passed. Group reports supply their outcome.")
            command.add_argument("--check", help="Stable name for a manual check, default pr.")
        if name == "observe":
            command.add_argument("--stage", choices=("review", "merge", "ci", "deploy", "live", "acceptance"), required=True)
            command.add_argument("--outcome", required=True, help="Stage-specific outcome listed in docs/delivery.md.")
            command.add_argument("--sha", required=True, help="Exact observed commit (landing commit for a merge).")
            command.add_argument("--ref", help="CI run ref, either the tracked PR head or target ref.")
            command.add_argument("--base-sha", help="Exact base SHA used by review or PR CI, or the pre-merge base.")
            command.add_argument("--head-sha", help="Exact source PR head identified by merge evidence.")
        if name in ("test", "observe"):
            command.add_argument("--source", required=True, help="Evidence URL, file reference or other locator.")
            command.add_argument("--actor", required=True, help="Person or system that produced the observation.")
            command.add_argument("--observed-at", help="ISO 8601 timestamp with timezone, default now.")
        if name in ("observe", "status"):
            command.add_argument("--environment", help="Named environment for deploy, live check and acceptance.")
        if name == "status":
            command.add_argument("--format", choices=("json", "markdown"), default="json")
            command.add_argument("--history", action="store_true", help="Include all previous tests and stage observations.")
