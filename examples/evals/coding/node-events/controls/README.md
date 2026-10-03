# Event-bus controls

`good` is an overlay on `../fixture`, never a candidate workspace by itself.
Copy the fixture to a fresh directory, then copy `good` over it. Run these
commands, replacing `WORKSPACE` with that directory and `REPO` with the evaluator
repository:

```sh
cd WORKSPACE
node --test tests/events.test.cjs
node REPO/tools/event_trial_checks.cjs WORKSPACE node-events
```

The fixture's four ordinary tests pass; the external checker fails because
`subscribeOnce` is missing. `good` shares registration/cancellation bookkeeping
and the delivery loop, adds one-shot subscriptions, and includes three focused
tests. Its seven tests and all 208 external assertions pass.

`mutants.json` contains seven single-fault replacement recipes against the good
source. Each entry names a workspace-relative `path`, literal `old` and `new`
text, required match `count`, and the public contract it `violates`. Start from a
fresh good workspace for each recipe, assert that `old` occurs exactly `count`
times, and replace it with `new`. Every mutant fails the external checker with a
semantic `AssertionError`, rather than an import or syntax error.

To measure the additional coverage beyond the modest baseline tests, instead
copy only `good/src` over a fresh fixture and apply one recipe. All four baseline
tests pass for all seven mutants while the external checker rejects each one.

| Control | Regression checked |
| --- | --- |
| `once-after-callback` | Cancels one-shot listeners after user code, breaking recursive publication and callback-visible state. |
| `cancelled-snapshot-runs` | Invokes a registration cancelled after the snapshot was taken. |
| `late-global-snapshot` | Global observers added by a topic callback run during that same publication. |
| `cloned-payload` | Copies payloads, breaking reference identity and visible listener mutations. |
| `deduplicated-listeners` | Collapses separate registrations of the same function. |
| `swallowed-error` | Swallows callback errors and continues delivery. |
| `global-before-topic` | Delivers global observers before matching topic listeners. |

These controls establish behavior sensitivity. They do not establish independent
cleanup-review approval or completed Forge workflow. No source length or layout
threshold determines an oracle result.
