"""Offline regression tests for dashboard-detected spreadsheet-change tracking.

``freshness.compute_fingerprints`` hashes the parsed values read from each
source workbook; ``freshness.build`` reconciles that against the durable store
and flags a workbook "updated" only when its content actually changed within the
recent window. Because the connected Google account cannot read Drive's
``modifiedTime`` (drive.file scope only), this content fingerprint IS the
last-updated signal — so it must be:

  * deterministic: re-reading identical data (any row order, int-vs-float
    jitter) yields the SAME hash, or every page load would false-flag changes;
  * a faithful change detector: first sight = baseline (not "updated"); an
    unchanged re-read stays quiet; a real value change flips "updated"; a change
    older than the recent window is remembered but no longer badged.

Run: cd artifacts/prayag && python3 -m tests.test_source_fingerprints
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import freshness
import store
from metrics import Record


def _rec(file_id, plant, machine, total, *, grain="monthly",
         period="2026-05", hours=100.0) -> Record:
    return Record(
        grain=grain, plant=plant, machine=machine, period=period,
        total_count=total, actual_hours=hours, unit="kg",
        source_file=file_id, source_tab="Report-1",
    )


class _FakeStore:
    """In-memory stand-in for the fingerprint store that mirrors the real one:
    one row per ``(file_id, fingerprint)`` version, ``ON CONFLICT`` touches
    ``observed_at`` (so reverts converge), latest = newest ``observed_at``.

    ``clock`` lets a test pin the write timestamp (the real store stamps DB
    ``now()``; ``build`` reads it back, so we control it here)."""

    def __init__(self, available=True):
        self.AVAILABLE = available
        self.rows = {}  # (file_id, fingerprint) -> {observed_at, row_count, seq}
        self._seq = 0
        self.clock = None

    def _now(self):
        return self.clock or datetime.datetime.now(datetime.timezone.utc)

    def fingerprint_state(self):
        by_file = {}
        for (fid, fp), r in self.rows.items():
            by_file.setdefault(fid, []).append((fp, r))
        state = {}
        for fid, versions in by_file.items():
            versions.sort(key=lambda t: (t[1]["observed_at"], t[1]["seq"]))
            fp, r = versions[-1]  # latest version wins
            state[fid] = {
                "fingerprint": fp,
                "observed_at": r["observed_at"],
                "row_count": r["row_count"],
                "snapshots": len(versions),
            }
        return state

    def fingerprint_record(self, *, file_id, fingerprint, label="", plant="",
                           grain="", row_count=0):
        key = (file_id, fingerprint)
        self._seq += 1
        ts = self._now()
        if key in self.rows:          # ON CONFLICT DO UPDATE SET observed_at=now()
            self.rows[key]["observed_at"] = ts
            self.rows[key]["row_count"] = row_count
            self.rows[key]["seq"] = self._seq
        else:
            self.rows[key] = {
                "observed_at": ts, "row_count": row_count, "seq": self._seq}
        return {
            "file_id": file_id, "fingerprint": fingerprint,
            "observed_at": self.rows[key]["observed_at"],
            "row_count": row_count, "snapshots": 1,
        }


def _check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


def test_deterministic_regardless_of_order_and_numeric_jitter():
    a = [
        _rec("F1", "PIPE", "M/C-1", 100.0),
        _rec("F1", "PIPE", "M/C-2", 50.0),
    ]
    # Same data, reversed order, ints instead of floats.
    b = [
        _rec("F1", "PIPE", "M/C-2", 50),
        _rec("F1", "PIPE", "M/C-1", 100),
    ]
    fa = freshness.compute_fingerprints(a)["F1"]["fp"]
    fb = freshness.compute_fingerprints(b)["F1"]["fp"]
    _check("identical data hashes the same regardless of order / int-vs-float",
           fa == fb)

    c = [
        _rec("F1", "PIPE", "M/C-1", 101.0),  # one value changed
        _rec("F1", "PIPE", "M/C-2", 50.0),
    ]
    _check("a changed value changes the hash",
           freshness.compute_fingerprints(c)["F1"]["fp"] != fa)


def test_build_change_lifecycle():
    fake = _FakeStore()
    orig = (store.fingerprint_state, store.fingerprint_record, store.AVAILABLE)
    store.fingerprint_state = fake.fingerprint_state
    store.fingerprint_record = fake.fingerprint_record
    store.AVAILABLE = True
    try:
        t0 = datetime.datetime(2026, 6, 15, 10, 0, tzinfo=datetime.timezone.utc)
        recs = [_rec("F1", "PIPE", "M/C-1", 100.0)]

        # First sight = baseline, NOT an update.
        fake.clock = t0
        out1 = freshness.build(recs, now=t0)
        s1 = out1["sources"][0]
        _check("first sight records a baseline, not flagged updated",
               out1["n_updated"] == 0 and s1["updated"] is False)
        _check("baseline persisted one snapshot", len(fake.rows) == 1)

        # Unchanged re-read a minute later stays quiet (no new snapshot).
        fake.clock = t0 + datetime.timedelta(minutes=1)
        out2 = freshness.build(recs, now=t0 + datetime.timedelta(minutes=1))
        _check("unchanged re-read is not flagged and adds no snapshot",
               out2["n_updated"] == 0 and len(fake.rows) == 1)

        # A genuine value change flips "updated" and appends a snapshot.
        recs2 = [_rec("F1", "PIPE", "M/C-1", 175.0)]
        fake.clock = t0 + datetime.timedelta(hours=1)
        out3 = freshness.build(recs2, now=t0 + datetime.timedelta(hours=1))
        s3 = out3["sources"][0]
        _check("a real change is flagged updated and appends a snapshot",
               out3["n_updated"] == 1 and s3["updated"] is True
               and len(fake.rows) == 2)

        # The same change, viewed 8 days later, is remembered but no longer
        # badged "updated" (outside the recent window).
        fake.clock = t0 + datetime.timedelta(hours=1, days=8)
        out4 = freshness.build(
            recs2, now=t0 + datetime.timedelta(hours=1, days=8))
        s4 = out4["sources"][0]
        _check("a change older than the recent window is not badged",
               out4["n_updated"] == 0 and s4["updated"] is False
               and s4["ever_changed"] is True)
    finally:
        store.fingerprint_state, store.fingerprint_record, store.AVAILABLE = orig


def test_revert_to_prior_version_converges():
    """A→B→A: reverting to a previously-seen version is honestly flagged as a
    change, re-uses that version's row (no inflation), and then CONVERGES — an
    unchanged re-read does not keep re-detecting a change or bumping the time."""
    fake = _FakeStore()
    orig = (store.fingerprint_state, store.fingerprint_record, store.AVAILABLE)
    store.fingerprint_state = fake.fingerprint_state
    store.fingerprint_record = fake.fingerprint_record
    store.AVAILABLE = True
    try:
        t0 = datetime.datetime(2026, 6, 15, 10, 0, tzinfo=datetime.timezone.utc)
        rev_at = t0 + datetime.timedelta(hours=2)
        A = [_rec("F1", "PIPE", "M/C-1", 100.0)]
        B = [_rec("F1", "PIPE", "M/C-1", 200.0)]

        fake.clock = t0
        freshness.build(A, now=t0)                       # baseline A
        fake.clock = t0 + datetime.timedelta(hours=1)
        ob = freshness.build(B, now=t0 + datetime.timedelta(hours=1))  # A->B
        _check("change A->B is flagged", ob["n_updated"] == 1)

        # Revert B->A: flagged updated, timestamped NOW, no third version row.
        fake.clock = rev_at
        oa = freshness.build(A, now=rev_at)
        sa = oa["sources"][0]
        _check("revert to a prior version is flagged updated",
               oa["n_updated"] == 1 and sa["updated"] is True)
        _check("revert re-uses the prior version row (no inflation)",
               len(fake.rows) == 2)
        _check("revert timestamp is when it changed back, not first-ever sight",
               sa["last_changed_ts"] == rev_at.timestamp())

        # Converge: an unchanged re-read does NOT bump the time or add a row.
        fake.clock = rev_at + datetime.timedelta(minutes=1)
        oa2 = freshness.build(A, now=rev_at + datetime.timedelta(minutes=1))
        sa2 = oa2["sources"][0]
        _check("after a revert, unchanged re-read converges (no time bump)",
               sa2["last_changed_ts"] == rev_at.timestamp()
               and len(fake.rows) == 2)

        # And it ages out of the recent window like any other change.
        fake.clock = rev_at + datetime.timedelta(days=10)
        oa3 = freshness.build(A, now=rev_at + datetime.timedelta(days=10))
        _check("revert older than the recent window is no longer badged",
               oa3["n_updated"] == 0)
    finally:
        store.fingerprint_state, store.fingerprint_record, store.AVAILABLE = orig


def test_no_database_degrades_to_no_flags():
    fake = _FakeStore(available=False)
    orig = (store.fingerprint_state, store.fingerprint_record, store.AVAILABLE)
    store.fingerprint_state = lambda: {}
    store.fingerprint_record = lambda **k: None  # store unavailable -> no write
    store.AVAILABLE = False
    try:
        out = freshness.build([_rec("F1", "PIPE", "M/C-1", 100.0)])
        _check("without a database it still lists the read workbook",
               any(s["file_id"] == "F1" for s in out["sources"]))
        _check("without a database nothing is flagged updated",
               out["available"] is False and out["n_updated"] == 0)
    finally:
        store.fingerprint_state, store.fingerprint_record, store.AVAILABLE = orig


if __name__ == "__main__":
    test_deterministic_regardless_of_order_and_numeric_jitter()
    test_build_change_lifecycle()
    test_revert_to_prior_version_converges()
    test_no_database_degrades_to_no_flags()
    print("\nAll source-fingerprint tests passed.")
