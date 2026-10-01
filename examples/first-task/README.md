# Your first Forge task

A small, runnable task for either host. Python 3.11+ is the only dependency.
Copy this directory outside the Forge checkout, then open the copy in your host.
The starter has one failing case: surrounding whitespace is not removed.

Run the baseline from the copied directory:

```bash
python3 -m unittest discover -s tests
```

Ask **Codex**:

```text
Use $zagrosi-forge:zagrosi-forge to implement @requirements.md in this repository. Keep it lean and finish verification.
```

Or **Claude Code**:

```text
/zagrosi-forge:zagrosi-forge Implement @requirements.md in this repository. Keep it lean and finish verification.
```

Forge should inspect the baseline, produce one compact reviewed section, repair
the function, preserve its public contract, and capture successful verification.
The supplied tests pass when done; `normalize(' Ada  Lovelace ')` returns
`'Ada  Lovelace'`. No dependency or framework is needed.

To resume, tell the same skill: “Continue the existing Forge plan and verify the
remaining work.” You can switch between Codex and Claude Code in this same copy;
the plan and records are ordinary files. Changed code or contracts require fresh
verification. Missing coverage is addressed before claiming completion.

For direct inspection, resolve `plugin_root` from the loaded Forge skill and use
the planning directory reported by the run:

```bash
python3 "{plugin_root}/scripts/zagrosi_skills.py" status --path "{planning_dir}" --pretty
python3 "{plugin_root}/scripts/zagrosi_skills.py" next-section --planning-dir "{planning_dir}" --pretty
```

Readable output keeps blockers and the next command visible. A saved section and
a blocked next section are reported separately; continue from that recovery action.
Current integration evidence can be reused; changed inputs require a fresh check.

To add independent reviews, use either host's
[two-provider prompt](../../README.md#multiple-models), replacing “this feature”
with `@requirements.md`. Reviews share one packet and leave one writer in control.
Missing provider access remains explicit and does not count as completed review.

To try cleanup on your own repository:
`/zagrosi-forge:zagrosi-cleanup` in Claude, or ask Codex to use
`$zagrosi-forge:zagrosi-cleanup`. Start with a coherent module or subsystem.

If Forge is unavailable, restart the host after installing the plugin. If Python
is unavailable, install Python 3.11+; on Windows use `python` or `py -3`. A failed
admission/check should show the failing requirement and recovery action; repair
that cause and continue instead of discarding the plan.
