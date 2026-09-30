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

To try cleanup on your own repository:
`/zagrosi-forge:zagrosi-cleanup` in Claude, or ask Codex to use
`$zagrosi-forge:zagrosi-cleanup`. Start with a coherent module or subsystem.

If Forge is unavailable, restart the host after installing the plugin. If Python
is unavailable, install Python 3.11+; on Windows use `python` or `py -3`. A failed
admission/check should show the failing requirement and recovery action; repair
that cause and continue instead of discarding the plan.
