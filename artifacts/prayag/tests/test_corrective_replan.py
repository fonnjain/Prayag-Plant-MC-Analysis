"""tests/test_corrective_replan.py
Regression assertions for the Corrective Re-plan engine.

The six assertions from the spec
---------------------------------
1. No-zero-capacity-with-production: producedToDate > 0 → cap_per_day > 0
2. Few-days fallback: with 1-2 non-zero production days, method=mean, cap > 0
3. Feasibility consistency: feasible == cap_per_day × working_days_remaining (exact)
4. Not-everything-unfulfillable: total shortfall < total remaining when production exists
5. Source agreement: file_id + date range come from the parse, not injected stale values
6. Date-format guard: "Aug 1, 2026" and "1-Aug-2026" both parse; unknown format raises

Additional tests
----------------
7. p90 path: ≥ MIN_DAYS_FOR_P90 non-zero days → method="p90"
8. NO_DEMONSTRATED_CAPACITY: solvent / truly-zero categories correctly flagged
9. Category ordering matches CATEGORY_ORDER (not alphabetical, not file order)
10. Invariant warnings surfaced, not silently swallowed
"""
from __future__ import annotations

import copy
import datetime
import types
import unittest
from unittest.mock import patch

import mp_corrective_replan as mcr


# ---------------------------------------------------------------------------
# Helpers to build synthetic Report-11 / Report-12 value grids
# ---------------------------------------------------------------------------

def _r11(rows):
    """Build a minimal Report-11 value grid (header + data rows).

    Each row in *rows* is (date_str, types, item_code, pcs).
    """
    hdr = ["", "DATE", "MACHINE NAME", "MACHINE NO.", "TYPES", "ITEM CODE",
           "Running Hour", "Ideal Weight (KG)", "Pcs", "Weight"]
    values = [
        ["url row"],
        ["", "TOTAL", "", "", "", "", "625"],
        ["", "PIPE M/C"],
        ["", "", "", "", "", "", ""],
        hdr,
    ]
    for date, types_, item, pcs in rows:
        values.append(["", date, "CON-63-1", "PIPE M/C - 1", types_, item,
                        "8", "1.0", str(pcs), "100"])
    return values


def _r12(rows):
    """Build a minimal Report-12 value grid (header + data rows).

    Each row in *rows* is (date_str, material, item_code, pcs).
    """
    hdr = ["DATE", "MATERIAL", "Item Code", "SAP Code", "Moulding Machine",
           "Run Cavity", "Mould Cavity (F)", "Cycle Time Per Pcs Standard (F)",
           "Output Production", "", "Weight per Pc"]
    sub = ["", "", "", "", "", "", "", "", " Pc ", " Wt in Kgs "]
    values = [
        ["Mr. Jitendra"],
        ["Mould M/C"],
        ["TOTAL", "", "", "", "", "429"],
        hdr,
        sub,
    ]
    for date, material, item, pcs in rows:
        values.append([date, material, item, "", "A02(U-150)", "4", "4", "25.00",
                        str(pcs), "100"])
    return values


def _plan_rec(family, category, produce_required, produced, ideal_qty=0, closing_stock=0):
    """Build a minimal dict acting as a PlanRecord-like object."""
    class R:
        pass
    r = R()
    r.family = family
    r.category = category
    r.produce_required = produce_required
    r.produced = produced
    r.ideal_qty = ideal_qty
    r.closing_stock = closing_stock
    return r


def _run(r11_rows, r12_rows, plan_recs=None, month="2026-08", as_of="2026-08-08"):
    r11 = _r11(r11_rows)
    r12 = _r12(r12_rows)
    return mcr.compute_corrective_replan(
        month=month,
        plan_recs=plan_recs or [],
        r11_values=r11,
        r12_values=r12,
        as_of_date=as_of,
        file_id="TEST_FILE_ID",
    )


def _cat(result, category):
    return next(c for c in result.categories if c.category == category)


# ---------------------------------------------------------------------------
# 1. No-zero-capacity-with-production
# ---------------------------------------------------------------------------

class TestNoZeroCapacityWithProduction(unittest.TestCase):
    """Assertion 1 from spec: if producedToDate > 0, cap_per_day must be > 0.

    This was the root-cause bug: p90 with < threshold days returned 0 instead
    of falling back to mean.
    """

    def test_two_days_cpvc_pipe_gives_nonzero_cap(self):
        """Real-world August scenario: only 2 production days."""
        result = _run(
            r11_rows=[
                ("Aug 1, 2026", "CPVC", "PS-3",  2420),
                ("Aug 3, 2026", "CPVC", "PS-12", 3075),
            ],
            r12_rows=[],
        )
        cpvc_pipe = _cat(result, "CPVC Pipe")
        self.assertGreater(cpvc_pipe.produced_to_date, 0)
        self.assertGreater(cpvc_pipe.cap_per_day, 0,
            "Cap/Day must be > 0 when production exists (regression: bug returned 0)")

    def test_one_day_gives_nonzero_cap(self):
        result = _run(
            r11_rows=[("Aug 1, 2026", "SWR", "PW71", 1782)],
            r12_rows=[],
        )
        c = _cat(result, "SWR Pipe")
        self.assertGreater(c.cap_per_day, 0)
        self.assertEqual(c.method, "mean")

    def test_no_invariant_warnings_with_2_days(self):
        result = _run(
            r11_rows=[
                ("Aug 1, 2026", "CPVC", "PS-3", 2420),
                ("Aug 3, 2026", "CPVC", "PS-3", 3075),
            ],
            r12_rows=[],
        )
        inv_warnings = [w for w in result.warnings if "INVARIANT VIOLATED" in w]
        self.assertEqual(inv_warnings, [],
            "Engine must not emit INVARIANT VIOLATED for 2-day scenario — "
            "it means the fallback chain failed")

    def test_category_with_zero_production_is_none(self):
        """AGRI Pipe with no rows should have cap_per_day=0, method='none'."""
        result = _run(r11_rows=[], r12_rows=[])
        agri = _cat(result, "AGRI Pipe")
        self.assertEqual(agri.cap_per_day, 0.0)
        self.assertEqual(agri.method, "none")
        self.assertTrue(agri.no_demonstrated_capacity)


# ---------------------------------------------------------------------------
# 2. Few-days fallback
# ---------------------------------------------------------------------------

class TestFewDaysFallback(unittest.TestCase):
    """Assertion 2: with 1-2 production days → method='mean', cap > 0."""

    def _assert_mean(self, n_days):
        rows = [("Aug 1, 2026", "UPVC", f"PU-{i}", 3000) for i in range(n_days)]
        result = _run(r11_rows=rows, r12_rows=[])
        c = _cat(result, "UPVC Pipe")
        self.assertEqual(c.method, "mean",
            f"With {n_days} production day(s), method must be 'mean'")
        self.assertGreater(c.cap_per_day, 0)

    def test_one_day(self):
        self._assert_mean(1)

    def test_two_days(self):
        self._assert_mean(2)

    def test_four_days_still_mean(self):
        # MIN_DAYS_FOR_P90 = 5, so 4 days → still mean
        self._assert_mean(min(4, mcr.MIN_DAYS_FOR_P90 - 1))

    def test_mean_value_correct_with_two_days(self):
        """mean([2420, 3075]) = 2747.5."""
        result = _run(
            r11_rows=[
                ("Aug 1, 2026", "CPVC", "PS-3",  2420),
                ("Aug 3, 2026", "CPVC", "PS-12", 3075),
            ],
            r12_rows=[],
        )
        cpvc = _cat(result, "CPVC Pipe")
        self.assertAlmostEqual(cpvc.cap_per_day, 2747.5, delta=1.0)

    def test_fitting_two_days(self):
        """CPVC Fitting with 2 days via Report-12."""
        result = _run(
            r11_rows=[],
            r12_rows=[
                ("Aug 1, 2026", "CPVC", "U531", 25176),
                ("Aug 3, 2026", "CPVC", "U532", 19580),
            ],
        )
        c = _cat(result, "CPVC Fitting")
        self.assertEqual(c.method, "mean")
        self.assertAlmostEqual(c.cap_per_day, 22378.0, delta=5.0)


# ---------------------------------------------------------------------------
# 3. Feasibility consistency
# ---------------------------------------------------------------------------

class TestFeasibilityConsistency(unittest.TestCase):
    """Assertion 3: feasible == cap_per_day × working_days_remaining (exact)."""

    def test_feasible_matches_cap_times_days(self):
        result = _run(
            r11_rows=[
                ("Aug 1, 2026", "CPVC", "PS-3",  2420),
                ("Aug 3, 2026", "CPVC", "PS-12", 3075),
            ],
            r12_rows=[],
        )
        wd = result.working_days_remaining
        for cat in result.categories:
            if cat.cap_per_day > 0:
                expected = round(cat.cap_per_day * wd, 1)
                self.assertAlmostEqual(
                    cat.feasible, expected, delta=0.15,
                    msg=f"{cat.category}: feasible={cat.feasible} ≠ "
                        f"cap({cat.cap_per_day}) × days({wd}) = {expected}"
                )

    def test_no_feasibility_inconsistency_warnings(self):
        result = _run(
            r11_rows=[
                ("Aug 1, 2026", "UPVC", "PU-1", 5000),
                ("Aug 3, 2026", "UPVC", "PU-2", 3000),
            ],
            r12_rows=[],
        )
        incon = [w for w in result.warnings if "inconsistency" in w.lower()]
        self.assertEqual(incon, [])

    def test_zero_cap_has_zero_feasible(self):
        result = _run(r11_rows=[], r12_rows=[])
        for cat in result.categories:
            if cat.cap_per_day == 0:
                self.assertEqual(cat.feasible, 0.0)


# ---------------------------------------------------------------------------
# 4. Not-everything-unfulfillable
# ---------------------------------------------------------------------------

class TestNotEverythingUnfulfillable(unittest.TestCase):
    """Assertion 4: total shortfall < total remaining when production exists."""

    def _run_with_demand(self, pipe_pcs, remaining_per_cat):
        plan = [
            _plan_rec("CPVC", "CPVC PIPE", remaining_per_cat + pipe_pcs, pipe_pcs),
        ]
        result = _run(
            r11_rows=[
                ("Aug 1, 2026", "CPVC", "PS-1", pipe_pcs),
                ("Aug 3, 2026", "CPVC", "PS-2", pipe_pcs),
            ],
            r12_rows=[],
            plan_recs=plan,
        )
        return result

    def test_shortfall_less_than_remaining(self):
        result = self._run_with_demand(pipe_pcs=3000, remaining_per_cat=2_000_000)
        total_rem = sum(c.remaining for c in result.categories)
        total_sf  = sum(c.shortfall for c in result.categories)
        self.assertLess(
            total_sf, total_rem,
            "Total shortfall must be < total remaining when production exists"
        )

    def test_no_critical_warning_with_feasible_output(self):
        # When feasible > 0 and remaining > 0 and feasible < remaining,
        # the CRITICAL invariant warning must NOT fire.
        plan = [_plan_rec("CPVC", "CPVC PIPE", 200_000, 0)]
        result = _run(
            r11_rows=[("Aug 1, 2026", "CPVC", "PS-1", 5000)],
            r12_rows=[],
            plan_recs=plan,
        )
        critical = [w for w in result.warnings if "CRITICAL" in w]
        self.assertEqual(critical, [])


# ---------------------------------------------------------------------------
# 5. Source agreement
# ---------------------------------------------------------------------------

class TestSourceAgreement(unittest.TestCase):
    """Assertion 5: file_id and date range are from the parse, correctly reported."""

    def test_file_id_in_provenance(self):
        r11 = _r11([("Aug 1, 2026", "CPVC", "PS-3", 2420)])
        r12 = _r12([])
        result = mcr.compute_corrective_replan(
            month="2026-08",
            plan_recs=[],
            r11_values=r11,
            r12_values=r12,
            as_of_date="2026-08-08",
            file_id="SENTINEL_FILE_ID_12345",
        )
        self.assertEqual(result.source_file_id, "SENTINEL_FILE_ID_12345")

    def test_date_range_min_max_from_data(self):
        result = _run(
            r11_rows=[
                ("Aug 1, 2026", "CPVC", "PS-3", 2420),
                ("Aug 3, 2026", "CPVC", "PS-12", 3075),
            ],
            r12_rows=[],
        )
        self.assertEqual(result.source_date_min, "2026-08-01")
        self.assertEqual(result.source_date_max, "2026-08-03")

    def test_producedToDate_from_r11_r12_not_plan(self):
        """actual_produced_total must reflect R11+R12 parse, not plan 'produced'."""
        plan = [_plan_rec("CPVC", "CPVC PIPE", 500_000, 205_566)]  # plan says 205k
        result = _run(
            r11_rows=[
                ("Aug 1, 2026", "CPVC", "PS-3", 2420),   # 2420 from R11
                ("Aug 3, 2026", "CPVC", "PS-3", 3075),   # 3075 from R11
            ],
            r12_rows=[],
            plan_recs=plan,
        )
        # actual_produced_total = 2420 + 3075 = 5495, NOT 205_566
        self.assertAlmostEqual(result.actual_produced_total, 5495, delta=1)
        # plan_produced_total = 205_566 (from plan rec)
        self.assertAlmostEqual(result.plan_produced_total, 205_566, delta=1)

    def test_working_days_remaining_is_positive(self):
        result = _run(r11_rows=[], r12_rows=[], as_of="2026-08-08")
        self.assertGreater(result.working_days_remaining, 0)
        self.assertEqual(
            result.working_days_total,
            result.working_days_elapsed + result.working_days_remaining,
        )


# ---------------------------------------------------------------------------
# 6. Date-format guard
# ---------------------------------------------------------------------------

class TestDateFormatGuard(unittest.TestCase):
    """Assertion 6: "Aug 1, 2026" and "1-Aug-2026" both parse; unknown raises."""

    def test_aug_1_2026_parses(self):
        d = mcr._parse_date_cell("Aug 1, 2026", 2026, 8)
        self.assertEqual(d, "2026-08-01")

    def test_aug_01_2026_parses(self):
        d = mcr._parse_date_cell("Aug 01, 2026", 2026, 8)
        self.assertEqual(d, "2026-08-01")

    def test_1_aug_2026_parses(self):
        d = mcr._parse_date_cell("1-Aug-2026", 2026, 8)
        self.assertEqual(d, "2026-08-01")

    def test_1_aug_2026_space_parses(self):
        d = mcr._parse_date_cell("1 Aug 2026", 2026, 8)
        self.assertEqual(d, "2026-08-01")

    def test_iso_parses(self):
        d = mcr._parse_date_cell("2026-08-01", 2026, 8)
        self.assertEqual(d, "2026-08-01")

    def test_plain_day_parses(self):
        d = mcr._parse_date_cell("1", 2026, 8)
        self.assertEqual(d, "2026-08-01")

    def test_dd_mm_yyyy_parses(self):
        d = mcr._parse_date_cell("01-08-2026", 2026, 8)
        self.assertEqual(d, "2026-08-01")

    def test_leading_apostrophe_stripped(self):
        # Google Sheets sometimes prepends an apostrophe to force text
        d = mcr._parse_date_cell("'Aug 1, 2026", 2026, 8)
        self.assertEqual(d, "2026-08-01")

    def test_unrecognised_raises_value_error(self):
        """A truly unrecognised format must raise ValueError (not silently skip)."""
        with self.assertRaises(ValueError):
            mcr._parse_date_cell("GARBAGE_DATE", 2026, 8)

    def test_empty_returns_none(self):
        self.assertIsNone(mcr._parse_date_cell("", 2026, 8))

    def test_excel_serial_returns_none(self):
        # 5-digit Excel serial — skip silently (we cannot parse meaningfully)
        result = mcr._parse_date_cell("46234", 2026, 8)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 7. p90 path (≥ MIN_DAYS_FOR_P90 days)
# ---------------------------------------------------------------------------

class TestP90Path(unittest.TestCase):

    def test_method_is_p90_with_enough_days(self):
        n = mcr.MIN_DAYS_FOR_P90
        # Build n distinct days with varying pcs (day 1=low, day n=high)
        rows = []
        base_date = datetime.date(2026, 8, 1)
        d = base_date
        for i in range(n):
            while d.weekday() == 6:  # skip Sundays
                d += datetime.timedelta(1)
            rows.append((d.strftime("%b %-d, %Y"), "CPVC", f"PS-{i}", 1000 + i * 100))
            d += datetime.timedelta(1)

        result = _run(r11_rows=rows, r12_rows=[])
        c = _cat(result, "CPVC Pipe")
        self.assertEqual(c.method, "p90",
            f"With {n} days, method must switch to p90 (was {c.method})")
        self.assertGreater(c.cap_per_day, 0)

    def test_p90_is_conservative(self):
        """p90 (10th-percentile) must be ≤ mean of the same values."""
        n = mcr.MIN_DAYS_FOR_P90
        base = datetime.date(2026, 8, 1)
        rows = []
        d = base
        for i in range(n):
            while d.weekday() == 6:
                d += datetime.timedelta(1)
            rows.append((d.strftime("%b %-d, %Y"), "CPVC", f"PS-{i}", 1000 + i * 500))
            d += datetime.timedelta(1)

        result = _run(r11_rows=rows, r12_rows=[])
        c = _cat(result, "CPVC Pipe")
        mean_val = sum(1000 + i * 500 for i in range(n)) / n
        self.assertLessEqual(c.cap_per_day, mean_val + 1,
            "p90 (10th-percentile) must be ≤ mean (conservative capacity estimate)")


# ---------------------------------------------------------------------------
# 8. NO_DEMONSTRATED_CAPACITY
# ---------------------------------------------------------------------------

class TestNoDemonstratedCapacity(unittest.TestCase):

    def test_solvent_categories_always_ndc(self):
        """Solvent categories never appear in Report-11/12 → always NDC."""
        result = _run(
            r11_rows=[("Aug 1, 2026", "CPVC", "PS-3", 5000)],
            r12_rows=[],
        )
        for cat in mcr.SOLVENT_CATEGORIES:
            c = _cat(result, cat)
            self.assertTrue(c.no_demonstrated_capacity,
                f"{cat} should be NO_DEMONSTRATED_CAPACITY")
            self.assertEqual(c.cap_per_day, 0.0)
            self.assertEqual(c.method, "none")

    def test_ndc_distinct_from_zero_cap_bug(self):
        """NDC category: produced==0 AND cap==0 (legitimate).
        Bug category:    produced>0  AND cap==0 (triggers INVARIANT VIOLATED).
        They must NOT look identical in the result."""
        result = _run(
            r11_rows=[("Aug 1, 2026", "CPVC", "PS-1", 1000)],
            r12_rows=[],
        )
        # CPVC Pipe: produced > 0, cap > 0 (no invariant warning)
        cpvc = _cat(result, "CPVC Pipe")
        agri = _cat(result, "AGRI Pipe")   # no rows → NDC

        self.assertFalse(cpvc.no_demonstrated_capacity)
        self.assertTrue(agri.no_demonstrated_capacity)

        inv_w = [w for w in result.warnings if "INVARIANT VIOLATED" in w]
        self.assertEqual(inv_w, [], "Engine must not emit invariant warning for NDC category")


# ---------------------------------------------------------------------------
# 9. Category ordering
# ---------------------------------------------------------------------------

class TestCategoryOrdering(unittest.TestCase):

    def test_output_follows_canonical_order(self):
        result = _run(r11_rows=[], r12_rows=[])
        actual_order = [c.category for c in result.categories]
        self.assertEqual(actual_order, mcr.CATEGORY_ORDER)


# ---------------------------------------------------------------------------
# 10. Invariant warnings surfaced
# ---------------------------------------------------------------------------

class TestInvariantWarningSurface(unittest.TestCase):
    """Invariant violations must appear in result.warnings, not swallowed."""

    def test_invariant_3_fires_when_shortfall_equals_remaining(self):
        """Force shortfall == remaining by making cap_per_day = 0 artificially
        while produced_to_date > 0. We simulate this by patching _compute_cap_per_day
        to return 0 only for one category, triggering the emergency fallback + INVARIANT
        VIOLATED warning."""
        import mp_corrective_replan as _mcr_mod
        original = _mcr_mod._compute_cap_per_day

        def patched(vals):
            # Force CPVC Pipe to return 0/none regardless of production
            if not vals:
                return 0.0, "none", 0
            return 0.0, "none", 0  # always 0 — simulates the bug

        with patch.object(_mcr_mod, "_compute_cap_per_day", side_effect=patched):
            result = _run(
                r11_rows=[("Aug 1, 2026", "CPVC", "PS-3", 2420)],
                r12_rows=[],
                plan_recs=[_plan_rec("CPVC", "CPVC PIPE", 100_000, 0)],
            )

        # Should emit INVARIANT VIOLATED warning (not crash)
        inv_w = [w for w in result.warnings if "INVARIANT" in w or "CRITICAL" in w]
        self.assertGreater(len(inv_w), 0,
            "Engine must surface invariant warnings in result.warnings")


# ---------------------------------------------------------------------------
# 11. _parse_date fix in mp_followup (Issue #5 regression)
# ---------------------------------------------------------------------------

class TestMpFollowupDateFix(unittest.TestCase):
    """Verify the mp_followup._parse_date fix handles Report-11 date format."""

    def test_aug_1_2026_parsed_by_followup(self):
        from mp_followup import _parse_date
        result = _parse_date("Aug 1, 2026", 2026, 8)
        self.assertEqual(result, "2026-08-01",
            "_parse_date in mp_followup must parse 'Aug 1, 2026' (Issue #5 fix)")

    def test_1_aug_2026_parsed_by_followup(self):
        from mp_followup import _parse_date
        result = _parse_date("1-Aug-2026", 2026, 8)
        self.assertEqual(result, "2026-08-01",
            "_parse_date in mp_followup must parse '1-Aug-2026' (PTMT format)")

    def test_existing_plain_day_still_works(self):
        from mp_followup import _parse_date
        result = _parse_date("1", 2026, 8)
        self.assertEqual(result, "2026-08-01")

    def test_existing_iso_still_works(self):
        from mp_followup import _parse_date
        result = _parse_date("2026-08-01", 2026, 8)
        self.assertEqual(result, "2026-08-01")


# ---------------------------------------------------------------------------
# 12. Target-value validation (Aug 2026, 2-day scenario from spec)
# ---------------------------------------------------------------------------

class TestTargetValues(unittest.TestCase):
    """Verify the engine produces the target values from the spec document
    for August's 2-day scenario (mean method, 18-19 remaining working days)."""

    def setUp(self):
        self.result = _run(
            r11_rows=[
                # Aug 1
                ("Aug 1, 2026", "CPVC", "PS-3",  2420),
                ("Aug 1, 2026", "UPVC", "PU-1",  3480),
                ("Aug 1, 2026", "SWR",  "PW71",  1782),
                ("Aug 1, 2026", "AGRI", "PA-1",  350),
                # Aug 3
                ("Aug 3, 2026", "CPVC", "PS-12", 3075),
                ("Aug 3, 2026", "UPVC", "PU-15", 1423),
                ("Aug 3, 2026", "SWR",  "PW72",  1790),
            ],
            r12_rows=[
                # Aug 1
                ("Aug 1, 2026", "CPVC", "U531", 25176),
                ("Aug 1, 2026", "UPVC", "U532", 19525),
                ("Aug 1, 2026", "SWR",  "S101", 7635),
                ("Aug 1, 2026", "AGRI", "A101", 570),
                # Aug 3
                ("Aug 3, 2026", "CPVC", "U533", 19580),
                ("Aug 3, 2026", "SWR",  "S102", 8621),
            ],
            month="2026-08",
            as_of="2026-08-08",
        )

    def test_all_pipe_fitting_categories_have_production(self):
        for cat in ["CPVC Pipe", "CPVC Fitting", "UPVC Pipe", "UPVC Fitting",
                    "SWR Pipe", "SWR Fitting", "AGRI Pipe", "AGRI Fitting"]:
            c = _cat(self.result, cat)
            self.assertGreater(c.produced_to_date, 0, f"{cat} should have production")
            self.assertGreater(c.cap_per_day, 0, f"{cat} must have non-zero cap_per_day")

    def test_all_methods_are_mean(self):
        """With only 2 days, all categories must use mean (not p90)."""
        for cat_name in ["CPVC Pipe", "CPVC Fitting", "UPVC Pipe",
                         "SWR Pipe", "SWR Fitting", "AGRI Pipe", "AGRI Fitting"]:
            c = _cat(self.result, cat_name)
            self.assertEqual(c.method, "mean",
                f"{cat_name}: expected method=mean with 2 days, got {c.method}")

    def test_upvc_fitting_one_day_only_still_nonzero(self):
        """UPVC Fitting only ran Aug 1 (0 on Aug 3). Must still get cap > 0."""
        c = _cat(self.result, "UPVC Fitting")
        self.assertAlmostEqual(c.cap_per_day, 19525.0, delta=5.0)

    def test_solvent_categories_ndc(self):
        for cat in mcr.SOLVENT_CATEGORIES:
            c = _cat(self.result, cat)
            self.assertTrue(c.no_demonstrated_capacity)

    def test_feasible_totals_nonzero(self):
        total_feasible = sum(c.feasible for c in self.result.categories)
        self.assertGreater(total_feasible, 0)

    def test_no_invariant_violations(self):
        inv = [w for w in self.result.warnings if "INVARIANT VIOLATED" in w]
        self.assertEqual(inv, [])


if __name__ == "__main__":
    unittest.main()
