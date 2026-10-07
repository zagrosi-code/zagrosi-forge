"""Forge policy."""

from __future__ import annotations

import re

SPLIT_RE = re.compile(r"^\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")


SPLIT_TOKEN_RE = re.compile(r"\b\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\b")


SECTION_RE = re.compile(r"^section-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")


SECTION_TOKEN_RE = re.compile(r"\bsection-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\b")


CONFIG_RE = re.compile(r"^[a-z][a-z0-9_]*:\s*.+$")


REQ_ID_RE = re.compile(r"\bREQ-[A-Z0-9][A-Z0-9-]*\b")


FILE_PATH_RE = re.compile(
    r"`?[\w./-]+\.(?:avif|css|gif|go|htm|html|ico|java|jpeg|jpg|js|json|jsx|less|md|php|png|py|rb|rs|sass|scss|sh|sql|svg|toml|ts|tsx|webp|yaml|yml)(?:`|\b)"
)


OWNED_PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.{1,2}(?:/|$))(?!.*//)[\w.-]+(?:/[\w.-]+)*$")


OWNERSHIP_TITLE_RE = re.compile(
    r"\b(?:exact(?:\s+(?:file|path))?\s+ownership|(?:file|path)\s+ownership|owned\s+(?:files|paths))\b",
    re.IGNORECASE,
)


OWNERSHIP_DECLARATION_RE = re.compile(
    r"\b(?:(?:this|the)\s+section|it)\s+owns\s+exactly(?:\s+\w+){0,3}\s+(?:files?|paths?)\b"
    r"|\bonly these(?:\s+\w+){0,3}\s+paths?\s+may change\b",
    re.IGNORECASE,
)


SHELL_FENCE_LANGUAGES = frozenset({"bash", "sh", "shell", "zsh"})


SHELL_HEREDOC_DELIMITER_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


FORGE_META_START = "FORGE_META"


LEGACY_META_START = "DEEP_META"


REVIEW_BOARD_PASSES = [
    "architecture",
    "test-strategy",
    "security-privacy",
    "migration-data",
    "product-ambiguity",
    "implementation-feasibility",
]


SENSITIVE_KEY_RE = re.compile(r"(token|secret|key|password|credential|authorization|bearer)", re.I)


WORKFLOW_AMBIGUITY_TERMS = ["choose", "either", "tradeoff", "unknown", "tbd"]


LOCAL_TOOL_NAMES = ["gh", "codex", "claude", "gemini"]


DEFAULT_DEPTH = "lean"


DEPTH_MODES = {"lean", "fast", "standard", "deep"}


PROJECT_CONTRACT_VERSION = "compact-v1"


WORD_BUDGETS = {
    "lean": {
        "spec": 500,
        "research": 400,
        "plan": 800,
        "tdd": 400,
        "review": 150,
        "integration_notes": 150,
        "section_index": 150,
        "section": 300,
    },
    "fast": {
        "spec": 500,
        "research": 400,
        "plan": 800,
        "tdd": 400,
        "review": 150,
        "integration_notes": 150,
        "section_index": 150,
        "section": 300,
    },
    "standard": {
        "spec": 1200,
        "research": 1200,
        "plan": 2500,
        "tdd": 1200,
        "review": 500,
        "integration_notes": 500,
        "section_index": 500,
        "section": 700,
    },
    "deep": {
        "spec": 1800,
        "research": 1800,
        "plan": 4000,
        "tdd": 1600,
        "review": 800,
        "integration_notes": 800,
        "section_index": 700,
        "section": 1000,
    },
}


PROJECT_WORD_BUDGETS = {
    "lean": {"manifest": 300, "spec": 250},
    "fast": {"manifest": 300, "spec": 250},
    "standard": {"manifest": 700, "spec": 500},
    "deep": {"manifest": 1000, "spec": 800},
}


LEAN_PLAN_TERMS = {
    "goal": ["goal", "non-goal", "out of scope"],
    "evidence": ["current state", "current-state", "verified", "evidence"],
    "design-contract": ["design", "contract", "interface", "schema", "approach"],
    "testing": ["test", "expected failure", "verification"],
    "risk": ["risk", "security", "failure"],
    "rollback": ["rollback", "revert", "disable"],
    "acceptance": ["acceptance", "done when", "complete when"],
}


LEAN_SECTION_TERMS = {
    "goal": ["goal", "purpose"],
    "dependencies": ["dependencies", "depends on"],
    "tests-first": ["tests first", "expected failure", "red"],
    "implementation": ["implementation", "modify", "create"],
    "risks": ["risk", "failure", "rollback"],
    "acceptance": ["acceptance", "done when", "verification"],
}


QUALITY_PROFILES = {
    "solo": {
        "security": 1.0,
        "traceability": 0.8,
        "testing": 1.0,
        "scope": 1.0,
        "migration": 0.8,
        "readiness": 1.0,
        "general": 1.0,
    },
    "startup": {
        "security": 1.0,
        "traceability": 0.7,
        "testing": 0.9,
        "scope": 1.2,
        "migration": 0.8,
        "readiness": 1.1,
        "general": 1.0,
    },
    "enterprise": {
        "security": 1.3,
        "traceability": 1.2,
        "testing": 1.2,
        "scope": 1.0,
        "migration": 1.2,
        "readiness": 1.1,
        "general": 1.0,
    },
    "regulated": {
        "security": 1.6,
        "traceability": 1.6,
        "testing": 1.3,
        "scope": 1.0,
        "migration": 1.4,
        "readiness": 1.2,
        "general": 1.0,
    },
    "oss-maintainer": {
        "security": 1.1,
        "traceability": 1.0,
        "testing": 1.3,
        "scope": 1.2,
        "migration": 1.0,
        "readiness": 1.2,
        "general": 1.0,
    },
    "oss": {
        "security": 1.1,
        "traceability": 1.0,
        "testing": 1.3,
        "scope": 1.2,
        "migration": 1.0,
        "readiness": 1.2,
        "general": 1.0,
    },
    "incident-response": {
        "security": 1.5,
        "traceability": 1.1,
        "testing": 1.1,
        "scope": 1.4,
        "migration": 1.3,
        "readiness": 1.5,
        "general": 1.0,
    },
}


VAGUE_SECTION_NAMES = {"misc", "cleanup", "utils", "frontend", "backend", "api", "stuff", "polish"}


PROMPT_TYPES = {
    "codebase-researcher": "Research the existing codebase for relevant files, patterns, tests, risks, and commands. Return concise findings only.",
    "spec-reviewer": "Review the normalized spec for missing requirements, ambiguous acceptance criteria, scope drift, and unverified assumptions.",
    "security-reviewer": "Review the plan or implementation for auth, privacy, data exposure, injection, secrets, and abuse cases. Return severity-ranked findings.",
    "test-strategist": "Review test strategy. Identify missing tests, brittle fixtures, untestable design, and the smallest useful red/green path.",
    "section-writer": "Write one bounded implementation section: IDs, dependencies, exact owned paths, tests first, contracts, risks, rollback, and acceptance. Reference the plan; do not copy it.",
    "release-reviewer": "Review final readiness for rollout, rollback, docs, observability, migration safety, and residual risks.",
    "implementation-reviewer": "Review changed code against the original source requirements and section file. Reject assumptions that narrow the source promise. Check caller-level regressions against the unchanged baseline, concrete cleanup gains and justified deferrals. Prioritize correctness, security, scope drift, and missing tests.",
}


EVIDENCE_TERMS = {
    "file-evidence": ["current state", "existing", "verified", "found in", "file tree", "rg --files", "grep"],
    "command-evidence": ["rg ", "rg --files", "pytest", "unittest", "npm test", "npm run", "uv run", "cargo test", "go test", "pnpm", "yarn", "bun "],
    "test-discovery": ["existing test", "tests discovered", "test command", "test matrix", "fixtures"],
    "runtime-detection": ["package.json", "pyproject.toml", "go.mod", "cargo.toml", "runtime", "framework"],
    "assumption-ledger": ["assumption", "unknown", "open question", "stop-line", "stop line"],
}


EVIDENCE_IGNORE_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}


COMMAND_CATALOG = [
    {
        "name": "team", "phase": "utility",
        "summary": "Coordinate tasks and shared plans across engineers and Git clones.",
        "aliases": [], "examples": ["python3 scripts/zagrosi_skills.py team status"],
    },
    {
        "name": "provider-status", "phase": "utility",
        "summary": "Show reviewer availability and login.",
        "aliases": [], "examples": ["python3 scripts/zagrosi_skills.py provider-status --check-auth"],
    },
    {
        "name": "provider-review", "phase": "quality",
        "summary": "Review a packet with a chosen model.",
        "aliases": [], "examples": ["python3 scripts/zagrosi_skills.py provider-review --provider claude --input packet.md --output review.json"],
    },
    {
        "name": "project-setup",
        "phase": "project",
        "summary": "Start or resume project decomposition.",
        "aliases": ["project", "zagrosi-project-setup", "deep-project-setup"],
        "examples": [
            "python3 scripts/zagrosi_skills.py project-setup --file planning/requirements.md",
            "python3 scripts/zagrosi_skills.py project-setup --brief 'Build auth and billing' --planning-dir planning/app",
        ],
    },
    {
        "name": "project-create-dirs",
        "phase": "project",
        "summary": "Create directories from the manifest.",
        "aliases": ["zagrosi-project-create-dirs", "deep-project-create-dirs"],
        "examples": ["python3 scripts/zagrosi_skills.py project-create-dirs --planning-dir planning/app"],
    },
    {
        "name": "plan-setup",
        "phase": "plan",
        "summary": "Start or resume a spec's plan.",
        "aliases": ["plan", "zagrosi-plan-setup", "deep-plan-setup"],
        "examples": ["python3 scripts/zagrosi_skills.py plan-setup --file planning/01-auth/spec.md --plugin-root ."],
    },
    {
        "name": "plan-check-sections",
        "phase": "plan",
        "summary": "Find missing sections and index errors.",
        "aliases": ["zagrosi-plan-check-sections", "deep-plan-check-sections"],
        "examples": ["python3 scripts/zagrosi_skills.py plan-check-sections --planning-dir planning/01-auth"],
    },
    {
        "name": "plan-generate-section-prompts",
        "phase": "plan",
        "summary": "Generate bounded section prompts.",
        "aliases": ["zagrosi-plan-generate-section-prompts", "deep-plan-generate-section-prompts"],
        "examples": ["python3 scripts/zagrosi_skills.py plan-generate-section-prompts --planning-dir planning/01-auth"],
    },
    {
        "name": "implement-setup",
        "phase": "implement",
        "summary": "Start or resume implementation.",
        "aliases": ["implement", "zagrosi-implement-setup", "deep-implement-setup"],
        "examples": ["python3 scripts/zagrosi_skills.py implement-setup --sections-dir planning/01-auth/sections --target-dir ."],
    },
    {
        "name": "next-section", "phase": "implement",
        "summary": "Resume with section context and actions.",
        "aliases": [], "examples": ["python3 scripts/zagrosi_skills.py next-section --planning-dir planning/01-auth"],
    },
    {
        "name": "implement-progress", "phase": "implement",
        "summary": "Save a checkpoint and its evidence.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py implement-progress --planning-dir planning/01-auth --section section-01-auth --stage red --command 'python3 -m pytest tests/test_auth.py' --result 'Observed regression failure'"],
    },
    {
        "name": "context-brief", "phase": "all",
        "summary": "Read complete, bounded plan context.",
        "aliases": [], "examples": ["python3 scripts/zagrosi_skills.py context-brief --planning-dir planning/01-auth --section section-01-auth"],
    },
    {
        "name": "implementation-packet", "phase": "implement",
        "summary": "Save a bounded section packet.",
        "aliases": [], "examples": ["python3 scripts/zagrosi_skills.py implementation-packet --planning-dir planning/01-auth --section section-01-auth"],
    },
    {
        "name": "parallel-plan", "phase": "implement",
        "summary": "Group sections with disjoint ownership.",
        "aliases": [], "examples": ["python3 scripts/zagrosi_skills.py parallel-plan --planning-dir planning/01-auth"],
    },
    {
        "name": "implement-evidence-handoff",
        "phase": "implement",
        "summary": "Verify and save privileged S26/S28 evidence.",
        "aliases": [],
        "examples": [
            "python3 scripts/zagrosi_skills.py implement-evidence-handoff --implementation-root /external/implementation --section S26"
        ],
    },
    {
        "name": "implement-record-section",
        "phase": "implement",
        "summary": "Record a verified section.",
        "aliases": ["zagrosi-implement-record-section", "deep-implement-record-section"],
        "examples": ["python3 scripts/zagrosi_skills.py implement-record-section --sections-dir planning/01-auth/sections --section section-01-auth --review-status pass --verification-receipt planning/01-auth/implementation/verification/section-01-auth.json"],
    },
    {
        "name": "preflight",
        "phase": "all",
        "summary": "Check phase readiness.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py preflight --phase plan --file planning/01-auth/spec.md"],
    },
    {
        "name": "postflight",
        "phase": "all",
        "summary": "Check phase completion.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py postflight --phase plan --planning-dir planning/01-auth --strict"],
    },
    {
        "name": "implement-verify",
        "phase": "implement",
        "summary": "Bind checks to current code/contracts.",
        "aliases": [],
        "examples": [
            "python3 scripts/zagrosi_skills.py implement-verify --planning-dir planning/01-auth --target-dir . --section section-01-auth -- python3 -m pytest tests/test_auth.py",
            "python3 scripts/zagrosi_skills.py implement-verify --planning-dir planning/01-auth --target-dir . -- python3 -m pytest",
            "python3 scripts/zagrosi_skills.py implement-verify --planning-dir planning/01-auth --target-dir . --section section-01-auth --integration -- python3 -m pytest",
            "python3 scripts/zagrosi_skills.py implement-verify --planning-dir planning/01-auth --target-dir . --source attestation --outcome passed --evidence 'Full suite passed in the active host'",
        ],
    },
    {
        "name": "status",
        "phase": "all",
        "summary": "Show workflow progress and next action.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py status --path planning/01-auth"],
    },
    {
        "name": "commands",
        "phase": "utility",
        "summary": "List commands by phase.",
        "aliases": ["help-commands"],
        "examples": [
            "python3 scripts/zagrosi_skills.py commands --pretty",
            "python3 scripts/zagrosi_skills.py commands --phase plan",
        ],
    },
    {
        "name": "workflow-options",
        "phase": "utility",
        "summary": "Suggest workflow preferences.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py workflow-options --brief 'Improve this project'"],
    },
    {
        "name": "capability-inventory",
        "phase": "utility",
        "summary": "List tools and redacted Codex config.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py capability-inventory --plugin-root ."],
    },
    {
        "name": "review-capabilities",
        "phase": "utility",
        "summary": "List active and external reviewers.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py review-capabilities --planning-dir planning/01-auth"],
    },
    {
        "name": "planning-consistency",
        "phase": "quality",
        "summary": "Find requirement drift.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py planning-consistency --planning-dir planning/01-auth --strict"],
    },
    {
        "name": "doctor",
        "phase": "release",
        "summary": "Check package, skills and host support.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py doctor --plugin-root ."],
    },
    {
        "name": "install-codex",
        "phase": "release",
        "summary": "Install Codex plugin config and cache.",
        "aliases": ["install", "install-plugin"],
        "examples": ["python3 scripts/zagrosi_skills.py install --plugin-root . --dry-run"],
    },
    {
        "name": "update-check",
        "phase": "utility",
        "summary": "Compare installed Codex bytes.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py update-check --plugin-root ."],
    },
    {
        "name": "self-update",
        "phase": "release",
        "summary": "Refresh Codex config and plugin bytes.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py self-update --plugin-root ."],
    },
    {
        "name": "codebase-evidence",
        "phase": "plan",
        "summary": "Capture files, tests and check commands.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py codebase-evidence --target-dir . --planning-dir planning/01-auth --write"],
    },
    {
        "name": "lint-plan",
        "phase": "quality",
        "summary": "Validate plan depth and review.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py lint-plan --planning-dir planning/01-auth --depth standard --strict"],
    },
    {
        "name": "lint-sections",
        "phase": "quality",
        "summary": "Validate sections and their index.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py lint-sections --planning-dir planning/01-auth --depth standard --strict"],
    },
    {
        "name": "lint-evidence",
        "phase": "quality",
        "summary": "Validate evidence and assumptions.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py lint-evidence --planning-dir planning/01-auth --strict"],
    },
    {
        "name": "lint-implementation-readiness",
        "phase": "quality",
        "summary": "Check ownership, tests and contracts.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py lint-implementation-readiness --planning-dir planning/01-auth --strict"],
    },
    {
        "name": "lint-plan-artifacts",
        "phase": "quality",
        "summary": "Check required planning artifacts.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py lint-plan-artifacts --planning-dir planning/01-auth --strict"],
    },
    {
        "name": "traceability",
        "phase": "quality",
        "summary": "Trace requirements into tests/sections.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py traceability --planning-dir planning/01-auth --strict"],
    },
    {
        "name": "forge-score",
        "phase": "quality",
        "summary": "Combine planning quality scores.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py forge-score --planning-dir planning/01-auth --depth standard"],
    },
    {
        "name": "eval-suite",
        "phase": "release",
        "summary": "Score example planning fixtures.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py eval-suite --examples-dir examples --check-snapshots"],
    },
    {
        "name": "release-check",
        "phase": "release",
        "summary": "Run package, install and example checks.",
        "aliases": [],
        "examples": ["python3 scripts/zagrosi_skills.py release-check --plugin-root ."],
    },
]


COMMAND_SUMMARIES = {item["name"]: item["summary"] for item in COMMAND_CATALOG}


READINESS_TERMS = {
    "tdd": ["tests first", "expected failure", "red", "fixture"],
    "contract": ["contract", "interface", "schema", "result", "shape", "public api"],
    "commands": ["test command", "verification", "npm test", "pytest", "cargo test", "go test"],
    "rollback": ["rollback", "disable", "revert", "back out"],
    "ownership": ["owns", "ownership", "file tree", "modify", "create"],
}


FORGE_COMPONENT_WEIGHTS = {
    "solo": {
        "plan_depth": 1.0,
        "section_readiness": 1.0,
        "traceability": 1.0,
        "evidence_quality": 1.0,
        "implementation_readiness": 1.0,
    },
    "startup": {
        "plan_depth": 0.9,
        "section_readiness": 1.2,
        "traceability": 0.9,
        "evidence_quality": 1.0,
        "implementation_readiness": 1.2,
    },
    "enterprise": {
        "plan_depth": 1.1,
        "section_readiness": 1.1,
        "traceability": 1.3,
        "evidence_quality": 1.2,
        "implementation_readiness": 1.2,
    },
    "regulated": {
        "plan_depth": 1.2,
        "section_readiness": 1.1,
        "traceability": 1.6,
        "evidence_quality": 1.4,
        "implementation_readiness": 1.3,
    },
    "oss-maintainer": {
        "plan_depth": 1.0,
        "section_readiness": 1.2,
        "traceability": 1.1,
        "evidence_quality": 1.0,
        "implementation_readiness": 1.3,
    },
    "oss": {
        "plan_depth": 1.0,
        "section_readiness": 1.2,
        "traceability": 1.1,
        "evidence_quality": 1.0,
        "implementation_readiness": 1.3,
    },
    "incident-response": {
        "plan_depth": 0.9,
        "section_readiness": 1.2,
        "traceability": 1.0,
        "evidence_quality": 1.2,
        "implementation_readiness": 1.6,
    },
}


COMPACT_PLAN_HEADINGS = {
    "review": "review", "integration_notes": "review", "tdd": "tests first",
    "research": "evidence", "decisions": "decisions", "risks": "risks",
}


PINNER_STATE_RECORD_FIELDS = {
    "completed_at",
    "commit",
    "commit_status",
    "notes",
    "files_changed",
    "test_files",
    "review_artifacts",
    "evidence_rows",
    "verification",
    "pinner_path",
    "pinner_file_sha256",
}


SUCCESS_GATE_PAYLOAD_KEYS = (
    "gate",
    "score",
    "finding_count",
    "forge_score",
    "state",
    "next_section",
    "progress",
    "deferred_gate",
    "output",
    "check_count",
    "checks",
    "duration_seconds",
)


LOCAL_GATE_COMMANDS = frozenset({
    "doctor", "status", "codebase-evidence", "lint-interview", "lint-project-manifest", "lint-plan",
    "lint-sections", "traceability", "lint-evidence", "lint-artifact-schema",
    "lint-review-integration", "lint-plan-artifacts", "lint-implementation-readiness", "forge-score",
})


LOCAL_GATE_VALUE_OPTIONS = frozenset({
    "--planning-dir", "--plugin-root", "--path", "--phase", "--depth", "--profile",
    "--max-files", "--min-files", "--target-dir", "--max-tests",
})


INTERVIEW_FILES = {
    "project": ["zagrosi_project_interview.md", "deep_project_interview.md"],
    "plan": ["codex-interview.md", "claude-interview.md"],
}


INTERVIEW_PLACEHOLDER_RE = re.compile(
    r"\b(TBD|TODO|placeholder)\b|synthetic interview|generated without|not interviewed|no user interview|assumed answers?",
    re.I,
)


SECTION_PROMPT = """Write ONLY `{section}.md` as raw Markdown.

Read `{index_path}` and the section format: `{format_path}`.
Apply `{depth}` investigation/review depth: `{depth_path}`.
Apply the engineering standard: `{engineering_path}`.
Use `{plan_path}` for shared contracts when it is another existing file.
Clarify only from `{source_path}` when required.

{max_words} words max. Do not copy plan context. Reference REQ/DEC/RISK IDs.
For a canonical compact section use ## Contract, ## Owned files, ## Evidence,
and ## Review. Contract maps each ### REQ-* to Source, Behavior, Expected, Command
using the section format. Preserve source IDs; never edit the brief to add IDs.
Keep inputs/outputs/errors, exact ownership, expected test failure, verification,
and rollback explicit. Add ## Decisions or ## Risks only for material detail;
otherwise keep those facts in evidence. Other sections link shared contracts and
record local deltas; detached mode uses its physical format. Review needs literal
`Verdict: pass` or `Verdict: fixed`, plus
`Reviewed: <concrete scope and result>`.
No production implementation. Every sentence must change an implementation decision.
"""


PLUGIN_CACHE_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "planning",
    "node_modules",
    "dist",
    "build",
    ".release-check",
    "reports",
}


PLUGIN_CACHE_IGNORE_FILES = {".DS_Store"}
