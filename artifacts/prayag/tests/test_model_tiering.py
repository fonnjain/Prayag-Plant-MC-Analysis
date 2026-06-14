"""Regression tests for tiered Anthropic model selection.

Daily/weekly windows are the frequent, low-stakes runs → fast tier. A whole
fiscal month, quarter, or FY is the infrequent, high-stakes review → deep tier.
A manual override always wins. Selection affects ONLY which model writes the
prose; it never touches a computed figure.

Run: cd artifacts/prayag && python3 -m tests.test_model_tiering
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import narrative
import app


def test_period_type_mapping():
    assert app._period_type("yesterday") == "weekly"
    assert app._period_type("last_week") == "weekly"
    assert app._period_type("5") == "monthly"
    assert app._period_type("current_fy") == "fiscal_year"
    assert app._period_type("prior_fy") == "fiscal_year"
    print("PASS: _period_type maps periods to tiers")


def test_select_model_by_tier():
    fast_model, fast_max, fast_tier = narrative.select_model("weekly")
    deep_model, deep_max, deep_tier = narrative.select_model("monthly")
    assert fast_tier == "fast" and fast_model == narrative.FAST_MODEL
    assert deep_tier == "deep" and deep_model == narrative.DEEP_MODEL
    assert deep_max >= fast_max
    # FY/quarterly are also deep.
    assert narrative.select_model("fiscal_year")[2] == "deep"
    print("PASS: select_model picks fast for weekly, deep for monthly/FY")


def test_override_wins():
    # Override forces the tier regardless of the period kind.
    assert narrative.select_model("weekly", override=True)[2] == "deep"
    assert narrative.select_model("monthly", override=False)[2] == "fast"
    print("PASS: explicit override beats the period-based default")


def test_deep_override_parsing():
    assert app._deep_override({"deep_analysis": "on"}) is True
    assert app._deep_override({"deep": "fast"}) is False
    assert app._deep_override({}) is None
    print("PASS: _deep_override reads on/off/unset from the request")


def test_model_label_provenance():
    label = narrative.model_label("monthly")
    assert narrative.DEEP_MODEL in label and "deep tier" in label
    label2 = narrative.model_label("weekly")
    assert narrative.FAST_MODEL in label2 and "fast tier" in label2
    print("PASS: model_label exposes model + tier provenance")


def test_cache_key_separates_tiers():
    # The cache key must include the resolved model so a fast-tier response is
    # never served for a forced-deep request (and vice versa).
    base = ("Overview", "pk1", {"oee": 50})
    fast_key = narrative._cache_key(*base, narrative.FAST_MODEL)
    deep_key = narrative._cache_key(*base, narrative.DEEP_MODEL)
    assert fast_key != deep_key
    print("PASS: cache key distinguishes fast vs deep model for the same data")


def test_tier_label_reflects_actual_model():
    assert "deep tier" in narrative.tier_label(narrative.DEEP_MODEL)
    assert "fast tier" in narrative.tier_label(narrative.FAST_MODEL)
    print("PASS: tier_label reports provenance for the model actually used")


def test_review_cache_key_separates_tiers():
    # The app-level Claude-review cache must key on fingerprint AND resolved
    # model, so a fast-tier review is never reused when deep is later forced for
    # the SAME data state (the bug the architect flagged).
    base = {"period_type": "weekly", "confirmation": {"fingerprint": "FP1"}}
    fast_ck = app._review_cache_key({**base, "deep_override": None})
    deep_ck = app._review_cache_key({**base, "deep_override": True})
    assert fast_ck != deep_ck, "forcing deep must produce a different review key"
    assert fast_ck.startswith("FP1:") and deep_ck.startswith("FP1:")
    print("PASS: review cache key separates fast vs forced-deep for same data")


if __name__ == "__main__":
    test_period_type_mapping()
    test_select_model_by_tier()
    test_override_wins()
    test_deep_override_parsing()
    test_model_label_provenance()
    test_cache_key_separates_tiers()
    test_tier_label_reflects_actual_model()
    test_review_cache_key_separates_tiers()
    print("\nAll model-tiering regression tests passed.")
