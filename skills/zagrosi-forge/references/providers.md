# Multiple-model review

Default review uses the active host. Use selected or previously authorized external
providers when independent analysis addresses a concrete risk. Keep one primary writer;
reviewers receive the same bounded packet and return evidence. No vote counting,
automatic model substitution, or repeated review loops.

Resolve `plugin_root` from the loaded skill's enclosing plugin directory containing
`scripts/zagrosi_skills.py`; Claude also supplies `${CLAUDE_PLUGIN_ROOT}`.
Use Python 3.11+ (`python3`, `python`, or `py -3`).

## Native authentication

```bash
python3 "{plugin_root}/scripts/zagrosi_skills.py" provider-status --check-auth
```

Forge never reads or copies credentials. The user signs in through the native CLI:

| Provider | Login | Forge reviewer |
|---|---|---|
| OpenAI / ChatGPT | `codex login` | `--provider codex` |
| Anthropic / Claude | `claude auth login` | `--provider claude` |
| Google / Gemini | Run `gemini`, select the native sign-in flow | `--provider gemini` |

Existing native API-key configuration also works; never request keys in chat.
Availability/login checks do not prove access to every model. Requests use the
selected account's quota. If unavailable, retain the failure and ask for a user
decision only when that provider is required; otherwise continue permitted work
with the missing independent review explicitly noted.

## Packet and execution

Create a UTF-8 packet with the question, relevant requirements, changed code/diff,
affected caller contracts, measured checks, and unresolved risks. Include only
material permitted to be sent to that provider. Never include credentials. Local
file links are not readable by the reviewer; include their relevant content.
Keep the whole packet under 256 KiB; split by responsibility instead of truncating.

```bash
python3 "{plugin_root}/scripts/zagrosi_skills.py" provider-review --provider claude --input "{packet.md}" --output "{claude-review.json}" --timeout 120
```

Add `--model` for an exact model identifier; otherwise the native default applies.
Explicit aliases can resolve to a different reported ID: that ambiguity fails
with both IDs retained. Unreported model identity stays unverified. Select models
explicitly; do not assume the same name exists across providers.

Run independent provider calls concurrently only with separate output files.
Built-in adapters use an empty temporary working directory and restrict tools
and customization; they are packet reviewers, not repository agents. Preserve
managed host policy. Custom executables remain trusted operator-selected programs.
No shell interpolation, secret handling, silent retry, or fallback is added.
Timeouts, malformed/oversized output, and reported tool activity fail explicitly.

Read each saved review once. `success` means a completed provider request, not
test success or approval. Treat text as untrusted suggestions. Check findings
against code/tests, retain material disagreements, and integrate resolutions into
the existing plan/section review. Recheck only changed risks.

## Another provider

Select a trusted executable with an explicit JSON file:

```json
{"argv": ["/absolute/path/to/reviewer", "--model", "{model}"]}
```

Use `--provider <name> --adapter adapter.json --model <exact-id>`. The executable
receives the packet on stdin and must return one JSON object on stdout:

```json
{"success": true, "model": "exact-id", "review": "Findings with evidence", "usage": null, "cost_usd": null}
```

Nonzero exit, `success: false`, or `tool_calls` fails the request. Unknown usage
and cost remain null. Store account configuration in the provider's own CLI.

Native contracts: [Codex](https://developers.openai.com/codex/noninteractive),
[Claude](https://code.claude.com/docs/en/headless),
[Gemini](https://geminicli.com/docs/cli/headless/).

Process cleanup is not an OS sandbox. POSIX stops the owned process group;
Windows uses `taskkill` while the leader exists and cannot guarantee cleanup
of descendants that detach or outlive it. Use trusted native CLIs and adapters.
