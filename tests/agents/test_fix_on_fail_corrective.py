"""Tests for `RequirementsAgent._build_fix_on_fail_corrective`.

Validates the corrective re-prompt builder used by the fix-on-fail retry
loop in `agents/requirements_agent.py`. The helper translates a red-team
audit report into a user-message string that asks the LLM to re-emit
`generate_requirements` with the BOM corrected.

Behaviour gates:
  - Only critical / high severity issues count.
  - Only categories in `_FIX_ON_FAIL_CATEGORIES` are retried; the rest
    return None so the caller skips a wasted LLM round-trip.
  - The verified candidate pool (`_offered_candidate_mpns`) is rendered
    inline so the LLM has a closed list to pick from.
  - Pool is capped at 80 MPNs to keep the prompt readable on huge BOMs.
"""
from __future__ import annotations

import pytest

from agents.requirements_agent import RequirementsAgent


@pytest.fixture
def agent():
    a = RequirementsAgent.__new__(RequirementsAgent)
    a._offered_candidate_mpns = set()
    return a


class TestEmptyOrIrrelevant:
    def test_empty_audit_returns_none(self, agent):
        assert agent._build_fix_on_fail_corrective({}) is None
        assert agent._build_fix_on_fail_corrective({"issues": []}) is None

    def test_only_medium_severity_returns_none(self, agent):
        audit = {"issues": [
            {"severity": "medium", "category": "hallucinated_part",
             "location": "x", "detail": "d"},
        ]}
        assert agent._build_fix_on_fail_corrective(audit) is None

    def test_non_fixable_categories_return_none(self, agent):
        audit = {"issues": [
            {"severity": "critical", "category": "cascade_error",
             "location": "x", "detail": "NF mismatch"},
            {"severity": "high", "category": "topology",
             "location": "x", "detail": "missing preselector"},
            {"severity": "high", "category": "missing_citation",
             "location": "x", "detail": "no IEEE clause"},
        ]}
        assert agent._build_fix_on_fail_corrective(audit) is None

    def test_none_audit_returns_none(self, agent):
        assert agent._build_fix_on_fail_corrective(None) is None  # type: ignore[arg-type]


class TestFixableBlockers:
    def test_hallucinated_part_triggers_corrective(self, agent):
        agent._offered_candidate_mpns = {"HMC1023LP5E", "TQP3M9035"}
        audit = {"issues": [
            {"severity": "critical", "category": "hallucinated_part",
             "location": "component_recommendations/MADL-011017",
             "detail": "Part MADL-011017 was not found on DigiKey, Mouser, or local seed",
             "suggested_fix": "Replace with verifiable MPN"},
        ]}
        msg = agent._build_fix_on_fail_corrective(audit)
        assert msg is not None
        assert "AUDIT FAILURE" in msg
        assert "hallucinated_part" in msg
        assert "MADL-011017" in msg
        assert "VERIFIED CANDIDATE POOL" in msg
        assert "HMC1023LP5E" in msg
        assert "TQP3M9035" in msg

    def test_not_from_candidate_pool_triggers_corrective(self, agent):
        audit = {"issues": [
            {"severity": "high", "category": "not_from_candidate_pool",
             "location": "component_recommendations/XYZ",
             "detail": "MPN bypassed retrieval shortlist"},
        ]}
        msg = agent._build_fix_on_fail_corrective(audit)
        assert msg is not None
        assert "not_from_candidate_pool" in msg

    def test_datasheet_url_triggers_corrective(self, agent):
        audit = {"issues": [
            {"severity": "high", "category": "datasheet_url",
             "location": "component_recommendations/MCP1726",
             "detail": "URL did not resolve"},
        ]}
        msg = agent._build_fix_on_fail_corrective(audit)
        assert msg is not None
        assert "datasheet_url" in msg

    def test_banned_obsolete_nrnd_all_trigger(self, agent):
        for cat in ("banned_part", "obsolete_part", "nrnd_part"):
            audit = {"issues": [
                {"severity": "high", "category": cat,
                 "location": "x", "detail": "d"},
            ]}
            assert agent._build_fix_on_fail_corrective(audit) is not None, cat

    def test_mixed_fixable_and_unfixable_still_triggers(self, agent):
        audit = {"issues": [
            {"severity": "high", "category": "cascade_error",
             "location": "x", "detail": "non-fixable"},
            {"severity": "critical", "category": "hallucinated_part",
             "location": "y", "detail": "fixable"},
        ]}
        msg = agent._build_fix_on_fail_corrective(audit)
        assert msg is not None
        # Only the fixable category should appear in the corrective body.
        assert "hallucinated_part" in msg
        assert "cascade_error" not in msg


class TestCandidatePoolRendering:
    def test_empty_pool_emits_call_find_candidate_parts_hint(self, agent):
        agent._offered_candidate_mpns = set()
        audit = {"issues": [
            {"severity": "critical", "category": "hallucinated_part",
             "location": "x", "detail": "d"},
        ]}
        msg = agent._build_fix_on_fail_corrective(audit)
        assert msg is not None
        assert "NO candidate pool was built" in msg
        assert "find_candidate_parts" in msg

    def test_large_pool_is_truncated_to_80(self, agent):
        agent._offered_candidate_mpns = {f"PART-{i:04d}" for i in range(120)}
        audit = {"issues": [
            {"severity": "critical", "category": "hallucinated_part",
             "location": "x", "detail": "d"},
        ]}
        msg = agent._build_fix_on_fail_corrective(audit)
        assert msg is not None
        # Pool list lines start with "  - ". Sample 100 line range should yield
        # exactly 80 + 1 ellipsis line.
        pool_lines = [ln for ln in msg.splitlines() if ln.startswith("  - PART-")]
        assert len(pool_lines) == 80
        assert "(+40 more)" in msg


class TestSeverityFilter:
    def test_low_severity_fixable_category_is_skipped(self, agent):
        audit = {"issues": [
            {"severity": "low", "category": "hallucinated_part",
             "location": "x", "detail": "d"},
        ]}
        assert agent._build_fix_on_fail_corrective(audit) is None


class TestRetryCap:
    """The fix-on-fail retry budget drives worst-case P1 wall-clock: each
    extra retry is another ~3-5 min round-trip on glm-5.1. This guard
    fails loudly if someone bumps the cap back up without updating the
    perf note in the agent source, which would reintroduce the 12-min
    pathological case we fixed on 2026-04-22.
    """

    def test_max_retries_bounded_at_one(self):
        # Deterministic `_auto_fix_blockers` handles the common case for
        # free; one LLM retry is enough to catch the residual. Going back
        # to 2 was observed never to converge when retry 1 didn't — all
        # it bought was minutes of latency.
        assert RequirementsAgent._FIX_ON_FAIL_MAX_RETRIES == 1, (
            "Perf guardrail: raising _FIX_ON_FAIL_MAX_RETRIES inflates P1 "
            "worst-case wall-clock by ~3-5 min per extra retry. If this "
            "change is intentional, update the perf note in "
            "agents/requirements_agent.py and this test together."
        )
