"""Evaluation documents name only harness flags and helper subcommands that exist."""
import re

from forge_test_helpers import ROOT

PROTOCOL = ROOT / "examples/evals/session-break-protocol.md"
PROPOSAL = ROOT / "docs/proposals/state-first-lean.md"


def shell_commands(path):
    blocks = re.findall(r"^```bash\n(.*?)^```", path.read_text(), re.M | re.S)
    lines = "\n".join(blocks).replace("\\\n", " ").splitlines()
    return [line for line in lines if line.strip() and not line.lstrip().startswith("#")]


def test_protocol_commands_match_the_harness():
    commands = [command for command in shell_commands(PROTOCOL) if "tools/" in command]
    assert commands
    for command in commands:
        tool, arguments = re.search(r"tools/(\w+\.py)\s*(.*)", command).groups()
        source = (ROOT / "tools" / tool).read_text()
        operations = re.search(r'add_argument\("operation", choices=\(([^)]*)\)', source)
        if operations:
            assert arguments.split()[0] in re.findall(r'"([a-z-]+)"', operations[1]), command
        for flag in re.findall(r"(?<!\S)(--[a-z][a-z-]*)", command):
            assert f'"{flag}"' in source, f"{tool} does not define {flag}"
    for readme in ("examples/evals/README.md", "examples/evals/coding/README.md"):
        assert "session-break-protocol.md" in (ROOT / readme).read_text(), readme
    assert "../../docs/proposals/state-first-lean.md" in PROTOCOL.read_text()


def test_proposal_names_existing_helper_commands():
    cli = "".join((ROOT / "scripts/forge" / name).read_text() for name in ("cli.py", "team_cli.py"))
    subcommands = set(re.findall(r'add_parser\(\s*"([a-z][a-z-]*)"', cli))
    text = PROPOSAL.read_text()
    current = {"plan-setup", "postflight", "implement-setup", "implement-verify", "implement-progress",
               "implement-record-section", "lint-plan", "traceability", "forge-score", "status"}
    assert current <= subcommands
    assert all(f"`{name}`" in text for name in current), sorted(name for name in current if f"`{name}`" not in text)
    assert "../../examples/evals/session-break-protocol.md" in text
