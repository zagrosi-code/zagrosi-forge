"""Public review-only counterexamples; authoring performs no execution.

Parent activates this non-default-collected module alongside the existing
assessment/observation cases. Those fixtures explicitly double Docker execution.
"""
import pytest

from trial_suite_assessment_cases import (
    assess_suite, load_json, load_ref, read_reference, review_for, run_case, save_review,
)
from trial_suite_observation_cases import observing_case


def retained_target(trial, verdict, target):
    behavior = load_ref(trial, verdict["behavior"])
    if target == "descriptor":
        return read_reference(trial, behavior["descriptor"])[0]
    if target.startswith("oracle-"):
        return read_reference(trial, behavior["oracle"][target.removeprefix("oracle-")])[0]
    if target.startswith("native-"):
        reference = behavior["native"][0]["record"]
        if target == "native-record":
            return read_reference(trial, reference)[0]
        execution = load_ref(trial, reference)
        reference = (execution["isolation"]["qualification"] if target == "native-qualification"
                     else execution["isolation"]["lifecycle"]["evidence"][0])
        return read_reference(trial, reference)[0]
    if target.startswith("worker-"):
        reference = behavior["observations"][0]
        if target == "worker-observation":
            return read_reference(trial, reference)[0]
        observation = load_ref(trial, reference)
        if target != "worker-lifecycle":
            return read_reference(trial, observation[target.removeprefix("worker-")])[0]
        execution = load_ref(trial, observation["execution"])
        return read_reference(trial, execution["isolation"]["lifecycle"]["evidence"][0])[0]
    if target == "git-terminal-history":
        # This is the fixed capture location; the low-level Git audit originally
        # names history.txt relative to its own evidence root, not TRIAL.
        return trial / "assessor/git/history.txt"
    assert target == "git-initial-history"
    return read_reference(trial, load_json(trial / "trial.json")["initial_git"]["history"])[0]


class TestSuiteReviewLineage:
    @pytest.mark.parametrize("target,change", [
        ("oracle-receipt", "append"), ("oracle-receipt", "missing"),
        ("oracle-process", "append"), ("descriptor", "append"),
        ("native-record", "append"), ("native-qualification", "append"),
        ("native-lifecycle", "append"), ("worker-observation", "append"),
        ("worker-request", "append"), ("worker-response", "append"),
        ("worker-execution", "append"), ("worker-lifecycle", "append"),
        ("git-terminal-history", "append"), ("git-initial-history", "append"),
    ])
    def test_review_refuses_changed_nested_evidence_without_repeating_execution(
            self, tmp_path, monkeypatch, target, change):
        build = observing_case if target.startswith("worker-") else run_case
        trial, _, _, verdict = build(tmp_path, monkeypatch)
        assert verdict["common_quality"] == "pending", "The unchanged fixture must reach material review"
        outer_path, outer_bytes = read_reference(trial, verdict["behavior"])
        review = save_review(trial, review_for(trial, verdict))
        changed = retained_target(trial, verdict, target)
        assert changed.is_file()
        if change == "missing":
            changed.unlink()
        else:
            # Whitespace retains JSON validity where relevant. The byte identity
            # must still fail, without relying on a parser or semantic error.
            changed.write_bytes(changed.read_bytes() + b"\n ")

        def forbidden(*args, **kwargs):
            pytest.fail("Review-only lineage validation must not execute a process or qualify a profile")

        import coding_trial_isolation
        import coding_trial_process
        monkeypatch.setattr(coding_trial_isolation, "execute_isolated", forbidden)
        monkeypatch.setattr(coding_trial_isolation, "qualify_profile", forbidden)
        monkeypatch.setattr(coding_trial_process, "execute", forbidden)
        reviewed = assess_suite(trial, review=review)
        assert outer_path.read_bytes() == outer_bytes
        assert reviewed["common_quality"] == "failed"
        assert reviewed["synthetic_validation"] != "passed" and reviewed["acceptance"] != "passed"
        assert reviewed["errors"], f"Changed {target} needs a retained lineage failure"
