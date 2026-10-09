"""Supplemental pure guard; preserve the original six-case RED unchanged."""
import pytest

from test_trial_suite_cleanup_review import _review, review_context


def test_failed_verdict_cannot_claim_meaningful_cleanup_without_changed_files():
    with pytest.raises(ValueError):
        _review(*review_context(verdict="fail", meaningful=True))
