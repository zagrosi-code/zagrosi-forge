# Controlled coding pilot — 24 September 2026

Updated Forge used fewer input tokens in this pilot. **It did not establish faster or better completed coding tasks:** previous and updated Forge both finished 0/4 trials with every requirement satisfied; the plain agent finished 2/4.

## Method

Two Python tasks (godfile extraction and import preview), three approaches, two fresh repetitions: **12 serial runs**, with arm order rotated. Requested model `gpt-5.5`, medium effort, Codex CLI `0.154.0`, Python `3.12.13`, standard depth, 900-second timeout. Backend model revision is not attested.

Previous source: [`16cc917`](https://github.com/zagrosi-code/zagrosi-forge/commit/16cc91791be5713fdc96d3ff4c77f5c39cb74efb). Updated source and fixed evaluator: [`f60efdc`](https://github.com/zagrosi-code/zagrosi-forge/commit/f60efdc7d76d65edb1dfc860d27d4fa225ac69e3). Both arms used identical fixtures and evaluators. The plain agent used its normal workflow without Forge. Candidates were not repaired or selectively rerun. Independent reviewers received anonymous source/test packets with arm labels and performance metrics withheld; blinding is partial because code can reveal workflow choices.

## Observed medians

Two runs per row. Input includes cached tokens; uncached input is reported separately. Times cover the agent's planning, coding and verification, excluding external evaluation and blind review.

| Task | Approach | Seconds | Input tokens | Uncached input | Output tokens |
|---|---|---:|---:|---:|---:|
| Godfile | Previous Forge | 440.8 | 1,530,926 | 86,958 | 20,551 |
| Godfile | Updated Forge | 424.5 | 1,111,305 | 61,385 | 21,003 |
| Godfile | Plain agent | 270.3 | 368,206 | 33,230 | 13,684 |
| Import preview | Previous Forge | 339.5 | 1,428,925 | 65,469 | 16,266 |
| Import preview | Updated Forge | 181.7 | 670,366 | 45,086 | 8,009 |
| Import preview | Plain agent | 74.9 | 165,132 | 17,100 | 3,203 |

Updated Forge's median input use was **27% lower on godfile and 53% lower on import preview**. Observed times were 4% and 46% lower respectively, but incomplete work prevents interpreting these as equal-work speedups. Individual runs vary substantially; see the [per-run data](controlled-results-2026-09-24.json).

## Correctness and completion

| Approach | Frozen automatic checks | Forge workflow | Cleanup approved | Fully complete |
|---|---:|---:|---:|---:|
| Previous Forge | 4/4 | 2/4 | 0/4 | 0/4 |
| Updated Forge | 4/4 | 3/4 | 1/4 | 0/4 |
| Plain agent | 4/4 | Not applicable | 2/4 | 2/4 |

All runners exited successfully, with no timeouts or scope violations. Automatic checks had a blind spot; their passing results are not full compatibility approval.

- **Public imports:** all four Forge godfile candidates removed promised `dispatch` imports: `Path`, `csv`, `date`, `deepcopy`, `io`, and `json`. Blind review caught this; both plain candidates preserved them. The frozen oracle's 75 assertions missed the regression.
- **Workflow:** both previous godfile runs omitted persisted depth metadata. The second updated import run used `postflight --flight off`, skipping required planning evidence despite returning an explicit strict command earlier.
- **Incidental cleanup:** five of six import candidates left duplicated receipt parsing/pricing intact. Only updated Forge's second candidate shared receipt parsing with `parse_orders`; it still failed workflow verification.
- **Relative quality:** reviewers preferred plain-agent code in three blocks and updated Forge in one. Preference does not grant cleanup or workflow approval.

Planning documents were not consistently smaller: updated Forge's godfile plans had more words; import plans were shorter, but one lacked required evidence. Structural counts and document sizes are descriptive, not quality scores or consumed-token measurements.

## Follow-up and limits

The live oracle now checks the six public imports, with **12 regression cases** for hidden or replaced bindings and 81 total godfile assertions. Read-only supplemental checks reproduced all four compatibility failures on the saved candidates. Engineering guidance now explicitly protects promised public exports. These follow-ups have not received new model trials; the original frozen results remain intact.

Separately, replaying 13 saved failed-flight reports reduced JSON characters by **60.7%**, with full reports retained. That measures diagnostic output, not whole-task speed. Regression tests also verify that final verification reuses analysis while invalidating changed inputs.

This is a small synthetic pilot at standard depth. It supports lower observed input use and clearer failure detection, **not a general speed or generated-code-quality claim**, nor performance conclusions for lean/deep. Command phases are heuristic; phase-specific model tokens, API retries and reliable phase durations remain unknown.

Full events, workspaces, reviews and frozen source trees remain in the ignored local trial archive. The [compact JSON](controlled-results-2026-09-24.json) retains fingerprints, every attempt, review outcomes and measurements. Use the frozen commits above with the [comparison runner](README.md#controlled-previouscurrentplain-comparison) to reproduce this evaluator; the live oracle intentionally has stronger coverage.
