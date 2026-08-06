"""Tests for mp_seed_provenance — provenance recording, staleness detection,
and plan-time warning generation.

All DB tests use the live dev Postgres DB (store.AVAILABLE must be True).
Drive-dependent tests mock sheets.drive_file_meta so no network calls are made.
"""
from __future__ import annotations

import datetime
import sys
import types
import unittest
from typing import Optional
from unittest.mock import MagicMock, patch

# ── make sure the prayag package root is on the path ─────────────────────────
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import store
import mp_seed_provenance as prov


# ── helpers ───────────────────────────────────────────────────────────────────

def _iso(offset_days: int = 0) -> str:
    """Return a UTC ISO-8601 string N days from now."""
    dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=offset_days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _wipe_prov_row(table_name: str) -> None:
    """Remove a test provenance row, ignore if absent."""
    if not store.AVAILABLE:
        return
    with store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mp_seed_provenance WHERE table_name = %s", (table_name,)
        )


# ── Provenance recording ──────────────────────────────────────────────────────

class TestRecordSeed(unittest.TestCase):
    """record_seed() writes the expected columns and upserts correctly."""

    _TBL = "__test_prov_table__"

    def setUp(self):
        if not store.AVAILABLE:
            self.skipTest("DB unavailable")
        prov.init_provenance_table()
        _wipe_prov_row(self._TBL)

    def tearDown(self):
        _wipe_prov_row(self._TBL)

    def _fetch(self):
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT table_name, source_file_ids, source_file_names,
                          source_modified_time, seeded_at, row_count
                     FROM mp_seed_provenance
                    WHERE table_name = %s""",
                (self._TBL,),
            )
            row = cur.fetchone()
        return row

    def test_insert_with_mod_time(self):
        mod = _iso(-5)  # 5 days ago
        prov.record_seed(
            self._TBL,
            source_file_ids="fileA",
            source_file_names="Sheet A",
            source_modified_time=mod,
            row_count=42,
        )
        row = self._fetch()
        self.assertIsNotNone(row, "provenance row must be written")
        table_name, fids, fnames, mod_ts, seeded_at, count = row
        self.assertEqual(table_name, self._TBL)
        self.assertEqual(fids, "fileA")
        self.assertEqual(fnames, "Sheet A")
        self.assertIsNotNone(mod_ts, "source_modified_time must be stored")
        self.assertIsNotNone(seeded_at, "seeded_at must be set")
        self.assertEqual(count, 42)

    def test_upsert_updates_row_count(self):
        prov.record_seed(self._TBL, row_count=10)
        prov.record_seed(self._TBL, row_count=99)
        row = self._fetch()
        self.assertEqual(row[5], 99, "upsert must update row_count")

    def test_null_mod_time_stored_as_null(self):
        prov.record_seed(self._TBL, source_modified_time=None, row_count=1)
        row = self._fetch()
        self.assertIsNone(row[3], "None mod_time must be stored as NULL")

    def test_invalid_mod_time_stored_as_null(self):
        prov.record_seed(self._TBL, source_modified_time="not-a-date", row_count=1)
        row = self._fetch()
        self.assertIsNone(row[3], "unparseable mod_time must be stored as NULL")


# ── Staleness warnings ────────────────────────────────────────────────────────

class TestBuildStalenessWarnings(unittest.TestCase):
    """build_staleness_warnings() issues correct warnings for each scenario."""

    _SEG = "PLUMBING"
    _TBL = "__test_stale_table__"
    _saved_rows: list  # provenance rows saved before Drive-sensitive tests

    def setUp(self):
        if not store.AVAILABLE:
            self.skipTest("DB unavailable")
        prov.init_provenance_table()
        _wipe_prov_row(self._TBL)
        self._saved_rows = []

    def tearDown(self):
        _wipe_prov_row(self._TBL)
        # Restore any rows that were saved+wiped for Drive-sensitive tests
        for row in self._saved_rows:
            prov.record_seed(
                row["table_name"],
                source_file_ids=row.get("source_file_ids") or "",
                source_file_names=row.get("source_file_names") or "",
                source_modified_time=(
                    row["source_modified_time"].isoformat()
                    if row.get("source_modified_time") else None
                ),
                row_count=row.get("row_count") or 0,
            )

    def _wipe_all_and_save(self) -> None:
        """Save and delete all provenance rows so Drive tests run in isolation."""
        self._saved_rows = prov.get_all_provenance()
        if not store.AVAILABLE:
            return
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM mp_seed_provenance")

    # ── Helper: temporarily empty the rejection / wastage tables ─────────────
    def _count(self, table: str, segment: str) -> int:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE segment=%s", (segment,))
            return cur.fetchone()[0]

    # ── Tests ──────────────────────────────────────────────────────────────────

    def test_no_warnings_when_data_present_and_seed_fresh(self):
        """When rejection + wastage exist and no Drive token → no missing warnings."""
        if (self._count("mp_rejection_summary", self._SEG) == 0
                or self._count("mp_wastage_summary", self._SEG) == 0):
            self.skipTest("Dev DB has no rejection/wastage data — run reseeds first")
        # No drive_token → Drive staleness check is skipped entirely
        warnings = prov.build_staleness_warnings(self._SEG, drive_token=None)
        self.assertIsInstance(warnings, list)
        # Neither missing-rejection nor missing-wastage warnings should fire
        rej_warn = [w for w in warnings if "rejection" in w.lower() and "never" in w.lower()]
        wst_warn = [w for w in warnings if "wastage" in w.lower() and "never" in w.lower()]
        self.assertEqual(rej_warn, [], "should not warn about missing rejection when rows exist")
        self.assertEqual(wst_warn, [], "should not warn about missing wastage when rows exist")

    def test_rejection_missing_warning(self):
        """0 rows in mp_rejection_summary → warning about 0% rejection rate."""
        # Temporarily empty rejection for our test segment using a fake segment
        fake_seg = "__test_seg_rej__"
        warnings = prov.build_staleness_warnings(fake_seg, drive_token=None)
        rej_warns = [w for w in warnings if "rejection" in w.lower()]
        self.assertTrue(len(rej_warns) >= 1, "missing rejection must produce a warning")
        self.assertIn("0 %", rej_warns[0])
        self.assertIn("8–11", rej_warns[0])

    def test_wastage_missing_warning(self):
        """0 rows in mp_wastage_summary → warning about 0% wastage rate."""
        fake_seg = "__test_seg_wst__"
        warnings = prov.build_staleness_warnings(fake_seg, drive_token=None)
        wst_warns = [w for w in warnings if "wastage" in w.lower()]
        self.assertTrue(len(wst_warns) >= 1, "missing wastage must produce a warning")
        self.assertIn("0 %", wst_warns[0])
        self.assertIn("0.51", wst_warns[0])

    def test_drive_stale_warning(self):
        """Seed stored 10 days ago; Drive says file was modified 5 days ago → red."""
        # Wipe real rows so only our test row is visible to the Drive check
        self._wipe_all_and_save()
        stored_mod = _iso(-10)   # seed captured file at T-10
        current_mod = _iso(-5)   # Drive says file was updated at T-5 (after seed)
        prov.record_seed(
            self._TBL,
            source_file_ids="fakeFileID",
            source_file_names="Fake Sheet",
            source_modified_time=stored_mod,
            row_count=5,
        )
        fake_seg = "__test_seg_stale__"
        # Mock drive_file_meta to return the "newer" time
        with patch("sheets.drive_file_meta", return_value={"modified_time": current_mod}):
            warnings = prov.build_staleness_warnings(fake_seg, drive_token="fake-token")
        stale = [w for w in warnings if "day" in w and "behind" in w]
        self.assertTrue(len(stale) >= 1, "stale Drive file must produce a days-behind warning")
        # Should report ~5 days (T-5 minus T-10)
        self.assertTrue(
            any("5" in w for w in stale),
            f"expected '5' in a stale warning; got: {stale}",
        )

    def test_drive_fresh_no_warning(self):
        """Seed stored 5 days ago; Drive says file was NOT modified since → no staleness."""
        # Wipe real rows so only our test row is visible to the Drive check
        self._wipe_all_and_save()
        stored_mod = _iso(-5)
        current_mod = _iso(-5)   # same time — no drift
        prov.record_seed(
            self._TBL,
            source_file_ids="fakeFileID2",
            source_file_names="Fresh Sheet",
            source_modified_time=stored_mod,
            row_count=5,
        )
        fake_seg = "__test_seg_fresh__"
        with patch("sheets.drive_file_meta", return_value={"modified_time": current_mod}):
            warnings = prov.build_staleness_warnings(fake_seg, drive_token="fake-token")
        stale = [w for w in warnings if "day" in w and "behind" in w]
        self.assertEqual(stale, [], "equal mod times must NOT produce a staleness warning")


# ── Status panel ──────────────────────────────────────────────────────────────

class TestGetStatusPanel(unittest.TestCase):
    """get_status_panel() returns correct freshness dots."""

    _TBL = "__test_panel_table__"

    def setUp(self):
        if not store.AVAILABLE:
            self.skipTest("DB unavailable")
        prov.init_provenance_table()
        _wipe_prov_row(self._TBL)

    def tearDown(self):
        _wipe_prov_row(self._TBL)

    def _row_for(self, panel, table_name: str):
        return next((r for r in panel if r["table_name"] == table_name), None)

    def test_missing_status_when_row_count_zero(self):
        prov.record_seed(self._TBL, row_count=0)
        panel = prov.get_status_panel(drive_token=None)
        row = self._row_for(panel, self._TBL)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "missing")

    def test_green_when_fresh_no_file(self):
        """Seed with no Drive file and seeded recently → green (no comparison possible)."""
        prov.record_seed(self._TBL, source_file_ids="", row_count=10)
        panel = prov.get_status_panel(drive_token=None)
        row = self._row_for(panel, self._TBL)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "green")

    def test_red_when_drive_newer(self):
        stored_mod = _iso(-10)
        current_mod = _iso(-3)
        prov.record_seed(self._TBL, source_file_ids="fID",
                         source_modified_time=stored_mod, row_count=10)
        with patch("sheets.drive_file_meta", return_value={"modified_time": current_mod}):
            panel = prov.get_status_panel(drive_token="tok")
        row = self._row_for(panel, self._TBL)
        self.assertEqual(row["status"], "red")
        self.assertIsNotNone(row["days_behind"])
        self.assertGreater(row["days_behind"], 0)

    def test_known_tables_always_present(self):
        """All 7 known tables must appear in the panel even if never seeded."""
        panel = prov.get_status_panel(drive_token=None)
        names = {r["table_name"] for r in panel}
        for tbl in prov.TABLE_LABELS:
            self.assertIn(tbl, names, f"{tbl} must appear in status panel")


if __name__ == "__main__":
    unittest.main()
