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

For direct inspection, stay in the copied project directory and use its
absolute path as `target_dir`. Resolve absolute `plugin_root` from the loaded
Forge skill's enclosing plugin directory containing `scripts/zagrosi_skills.py`;
use the absolute `planning_dir` reported by the run. Before implementation setup
saves the target, `status` uses the working directory.

```bash
python3 "{plugin_root}/scripts/zagrosi_skills.py" status --path "{planning_dir}" --pretty
python3 "{plugin_root}/scripts/zagrosi_skills.py" next-section --planning-dir "{planning_dir}" --target-dir "{target_dir}" --pretty
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

## Worked review

The starter fails `test_surrounding_whitespace_only` because it returns the input
unchanged. Calling `value.strip()` and translating `AttributeError` into the
expected `TypeError` looks plausible, but accepts a non-string object with its
own `strip()` method. In this constructed example, that patch passes the original
three tests. REQ-002 forbids calling that method at all.

`test_non_string_methods_are_not_called` uses such an object, records method
calls, and requires the exact error with no calls. Keep the existing type guard
and change the string result to `return value.strip()`. Inspect the diff for
that boundary, then rerun the tests: the adjacent cases preserve case, internal
whitespace, empty input and the public error. Passing these checks establishes
the tested normalization behavior.
