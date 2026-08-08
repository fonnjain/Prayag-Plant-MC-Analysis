"""tests/test_source_discovery.py — Unit tests for source_registry.py.

Tests cover:
  - Title pattern matching across case/spacing variants
  - Multiple-match tie-break (prefer preeti@, else newest)
  - No-match error path
  - sources.py pin override precedence
  - _patch_daily_sources never overwrites a pin
  - get_pipe_file_id short-circuits on pinned ID
  - ensure_fy_months_registered aggregates registered/missing correctly
"""
from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Isolate the module under test from real DB / Drive / sheets
# ---------------------------------------------------------------------------

def _load_registry_fresh():
    """Reload source_registry with all external deps stubbed out."""
    # Stub store with AVAILABLE=False so DB calls are no-ops
    stub_store = types.ModuleType("store")
    stub_store.AVAILABLE = False
    stub_store._conn = MagicMock()

    # Stub sources with a minimal DAILY_SOURCES + FY_MONTHS
    stub_sources = types.ModuleType("sources")
    stub_sources.DAILY_SOURCES = {
        "PIPE": {
            "files": {
                "2026-04": "ID_APR",
                "2026-07": "ID_JUL",
            }
        }
    }
    stub_sources.EMPTY_SOURCES = set()
    stub_sources.FY_MONTHS = [
        "2026-04", "2026-05", "2026-06", "2026-07",
        "2026-08", "2026-09",
    ]

    # Stub sheets so _get_drive_token is accessible
    stub_sheets = types.ModuleType("sheets")
    stub_sheets._get_drive_token = MagicMock(return_value="fake-drive-token")

    for name, mod in [("store", stub_store), ("sources", stub_sources),
                      ("sheets", stub_sheets)]:
        sys.modules[name] = mod

    # Force a clean reimport of source_registry
    sys.modules.pop("source_registry", None)
    import source_registry
    return source_registry, stub_sources


class TestTitleMatching(unittest.TestCase):
    """matches_pipe_fitting_title — pattern, case, and spacing variants."""

    def setUp(self):
        self.reg, _ = _load_registry_fresh()

    def _match(self, name, year=2026, month=8):
        return self.reg.matches_pipe_fitting_title(name, year, month)

    # ── Positive cases ───────────────────────────────────────────────────────

    def test_canonical_aug_2026(self):
        self.assertTrue(self._match(
            "5. Pipe & Fitting Plant Date Sheet & Monthly Report - AUG ' 2026"
        ))

    def test_no_leading_number(self):
        self.assertTrue(self._match(
            "Pipe & Fitting Plant Date Sheet & Monthly Report - AUG ' 2026"
        ))

    def test_mixed_case_month(self):
        self.assertTrue(self._match(
            "5. Pipe & Fitting Plant Date Sheet & Monthly Report - Aug ' 2026"
        ))

    def test_no_space_around_apostrophe(self):
        self.assertTrue(self._match(
            "5. Pipe & Fitting Plant Date Sheet & Monthly Report - AUG '2026"
        ))

    def test_jul_2026(self):
        self.assertTrue(self._match(
            "5. Pipe & Fitting Plant Date Sheet & Monthly Report - JUL ' 2026",
            year=2026, month=7
        ))

    def test_lowercase_throughout(self):
        self.assertTrue(self._match(
            "pipe & fitting monthly report - aug ' 2026"
        ))

    # ── Negative cases ───────────────────────────────────────────────────────

    def test_wrong_month(self):
        # July name does not match August query
        self.assertFalse(self._match(
            "5. Pipe & Fitting Plant Date Sheet & Monthly Report - JUL ' 2026",
            year=2026, month=8
        ))

    def test_wrong_year(self):
        self.assertFalse(self._match(
            "5. Pipe & Fitting Plant Date Sheet & Monthly Report - AUG ' 2025",
            year=2026, month=8
        ))

    def test_missing_fitting_keyword(self):
        self.assertFalse(self._match(
            "Pipe Monthly Report - AUG ' 2026"
        ))

    def test_missing_pipe_keyword(self):
        self.assertFalse(self._match(
            "Fitting Monthly Report - AUG ' 2026"
        ))

    def test_empty_string(self):
        self.assertFalse(self._match(""))


class TestFindMonthlyWorkbook(unittest.TestCase):
    """find_monthly_workbook — drive search, tie-break, no-match error."""

    def setUp(self):
        self.reg, self.stub_sources = _load_registry_fresh()

    def _drive_result(self, fid, name, modified, owner_email=None):
        owners = [{"emailAddress": owner_email}] if owner_email else []
        return {"id": fid, "name": name, "modifiedTime": modified, "owners": owners}

    def _call(self, year=2026, month=8, candidates=None):
        def fake_search(query, token):
            return candidates or []
        with patch.object(self.reg, "_drive_search", fake_search):
            return self.reg.find_monthly_workbook(year, month, drive_token="tok")

    # ── Single exact match ───────────────────────────────────────────────────

    def test_single_match_returns_correct_fields(self):
        r = self._call(candidates=[
            self._drive_result(
                "FILE_AUG",
                "5. Pipe & Fitting Plant Date Sheet & Monthly Report - AUG ' 2026",
                "2026-08-01T10:30:00.000Z",
            )
        ])
        self.assertEqual(r["file_id"], "FILE_AUG")
        self.assertEqual(r["match_count"], 1)
        self.assertIn("2026-08-01", r["modified_time"])

    # ── Multiple matches: prefer preeti@ ──────────────────────────────────────

    def test_multiple_matches_prefer_preeti(self):
        r = self._call(candidates=[
            # Newest by modifiedTime, but NOT preeti@
            self._drive_result(
                "FILE_OTHER", "Pipe & Fitting Monthly Report - AUG ' 2026",
                "2026-08-10T10:00:00.000Z", "deepakj@prayagindia.com"
            ),
            # Older, but preeti@
            self._drive_result(
                "FILE_PREETI", "5. Pipe & Fitting Plant Date Sheet - AUG ' 2026",
                "2026-08-01T08:00:00.000Z", "preeti.chauhan@prayagindia.com"
            ),
        ])
        self.assertEqual(r["file_id"], "FILE_PREETI",
                         "Should choose preeti@ even though it's older")
        self.assertEqual(r["match_count"], 2)

    # ── Multiple matches: no preeti@ → pick newest ───────────────────────────

    def test_multiple_matches_no_preeti_picks_newest(self):
        # Drive returns newest-first, so index 0 should win
        r = self._call(candidates=[
            self._drive_result(
                "NEWEST", "Pipe & Fitting Monthly - AUG ' 2026",
                "2026-08-10T10:00:00.000Z", "deepakj@prayagindia.com"
            ),
            self._drive_result(
                "OLDER", "5. Pipe & Fitting Monthly - AUG ' 2026",
                "2026-08-01T08:00:00.000Z", "bhawna@prayagindia.com"
            ),
        ])
        self.assertEqual(r["file_id"], "NEWEST")
        self.assertEqual(r["match_count"], 2)

    # ── No match raises ValueError with useful message ────────────────────────

    def test_no_match_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            self._call(candidates=[])
        self.assertIn("AUG", str(ctx.exception))
        self.assertIn("2026", str(ctx.exception))

    def test_candidates_that_fail_title_filter_are_excluded(self):
        """Drive returns files but none match the tight title check."""
        with self.assertRaises(ValueError):
            self._call(candidates=[
                self._drive_result(
                    "UNRELATED", "Pipe Report - JUL ' 2026 (old)",
                    "2026-07-01T00:00:00.000Z"
                )
            ])

    # ── Drive unavailable ────────────────────────────────────────────────────

    def test_no_drive_token_raises(self):
        import sheets as _sh
        _sh._get_drive_token = MagicMock(return_value=None)
        with self.assertRaises(ValueError) as ctx:
            self.reg.find_monthly_workbook(2026, 8)
        self.assertIn("Drive", str(ctx.exception))


class TestGetPipeFileId(unittest.TestCase):
    """get_pipe_file_id — pin override, cache, discovery, patch_daily_sources."""

    def setUp(self):
        self.reg, self.stub_sources = _load_registry_fresh()

    # ── Pin override ─────────────────────────────────────────────────────────

    def test_pinned_month_returns_pinned(self):
        result = self.reg.get_pipe_file_id("2026-07")
        self.assertIsNotNone(result)
        self.assertEqual(result["file_id"], "ID_JUL")
        self.assertEqual(result["source"], "pinned")

    def test_pinned_apr(self):
        result = self.reg.get_pipe_file_id("2026-04")
        self.assertEqual(result["file_id"], "ID_APR")
        self.assertEqual(result["source"], "pinned")

    # ── In-process cache ─────────────────────────────────────────────────────

    def test_mem_cache_hit_returns_cached(self):
        self.reg._mem_cache[("PIPE", "2026-09")] = {
            "file_id": "CACHED_SEP",
            "file_name": "Sep file",
            "modified_time": "2026-09-01T00:00:00.000Z",
        }
        result = self.reg.get_pipe_file_id("2026-09")
        self.assertIsNotNone(result)
        self.assertEqual(result["file_id"], "CACHED_SEP")
        self.assertEqual(result["source"], "cached")

    # ── Drive discovery for an unpinned month ────────────────────────────────

    def test_discovery_patches_daily_sources(self):
        """A successfully discovered month must be added to DAILY_SOURCES."""
        discovered_entry = {
            "file_id": "DISC_AUG",
            "file_name": "5. Pipe & Fitting ... AUG ' 2026",
            "modified_time": "2026-08-01T10:00:00.000Z",
            "match_count": 1,
        }
        with patch.object(self.reg, "find_monthly_workbook",
                          return_value=discovered_entry):
            result = self.reg.get_pipe_file_id("2026-08")

        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "discovered")
        self.assertEqual(result["file_id"], "DISC_AUG")
        # Must be visible in DAILY_SOURCES now
        self.assertEqual(
            self.stub_sources.DAILY_SOURCES["PIPE"]["files"].get("2026-08"),
            "DISC_AUG",
        )

    # ── _patch_daily_sources never overwrites a pin ──────────────────────────

    def test_patch_never_overwrites_pin(self):
        before = self.stub_sources.DAILY_SOURCES["PIPE"]["files"]["2026-07"]
        self.reg._patch_daily_sources("PIPE", "2026-07", "SHOULD_NOT_WIN")
        after = self.stub_sources.DAILY_SOURCES["PIPE"]["files"]["2026-07"]
        self.assertEqual(before, after, "Pinned ID must not be overwritten")

    def test_patch_adds_new_month(self):
        self.reg._patch_daily_sources("PIPE", "2026-10", "NEW_OCT")
        self.assertEqual(
            self.stub_sources.DAILY_SOURCES["PIPE"]["files"].get("2026-10"),
            "NEW_OCT",
        )

    # ── Resolution failure returns None, not an exception ────────────────────

    def test_failed_discovery_returns_none(self):
        with patch.object(self.reg, "find_monthly_workbook",
                          side_effect=ValueError("no workbook")):
            result = self.reg.get_pipe_file_id("2026-11")
        self.assertIsNone(result)


class TestEnsureFyMonthsRegistered(unittest.TestCase):
    """ensure_fy_months_registered — aggregation of registered/missing."""

    def setUp(self):
        self.reg, self.stub_sources = _load_registry_fresh()

    def test_pinned_months_counted_as_registered(self):
        result = self.reg.ensure_fy_months_registered(["2026-04", "2026-07"])
        self.assertIn("2026-04", result["registered"])
        self.assertIn("2026-07", result["registered"])
        self.assertEqual(result["missing"], [])

    def test_missing_month_counted_correctly(self):
        # 2026-08 not pinned; discovery will fail with no drive token
        with patch.object(self.reg, "find_monthly_workbook",
                          side_effect=ValueError("no workbook")):
            result = self.reg.ensure_fy_months_registered(["2026-04", "2026-08"])
        self.assertIn("2026-04", result["registered"])
        self.assertIn("2026-08", result["missing"])

    def test_returns_all_keys(self):
        result = self.reg.ensure_fy_months_registered([])
        self.assertIn("registered", result)
        self.assertIn("missing", result)
        self.assertIn("errors", result)


if __name__ == "__main__":
    unittest.main()
