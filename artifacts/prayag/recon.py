"""Standardized report reconciliation — recomputed (daily-first) vs final sheet.

Pure module, no network. Callers pass figures that have *already* been computed
(daily-first recompute on one side, the final summary sheet on the other); this
only compares and classifies them into a single badge dict the templates render.

Invariants:
  * Never fabricates a mismatch. When no final-sheet figure exists the result is
    informational ("recomputed only"), never a fake fail and never a fake 0%.
  * The recomputed value is always authoritative — reconciliation is a cross-check
    that surfaces drift, it never rewrites a figure.
  * PIPE legitimately *exceeds* its final summary (Report-11 adds days Report-5
    omits), so a positive delta there is expected, not an error (``expect_exceeds``).
"""
from __future__ import annotations

from typing import Optional

TOL = 0.03  # spec: flag any cell differing by more than 3%


def _delta_pct(recomputed: Optional[float], final: Optional[float]) -> Optional[float]:
    """Signed percentage difference of recomputed vs final, or None when N/A."""
    if final is None or recomputed is None:
        return None
    if final == 0:
        return None if (recomputed or 0) == 0 else 100.0
    return (recomputed - final) / final * 100.0


def _norm_row(row):
    """Accept (key, rec, fin) or (key, rec, fin, expect_exceeds)."""
    if len(row) == 4:
        return row[0], row[1], row[2], bool(row[3])
    key, rec, fin = row
    return key, rec, fin, False


def reconcile(recomputed_total, final_total, *, rows=None, unit="",
              expect_exceeds=False, tol=TOL, no_final_note=None):
    """Compare recomputed (daily-first) figures to the final summary sheet.

    ``recomputed_total`` / ``final_total`` are scalar totals (``final_total`` may
    be None when no final sheet is wired). ``rows`` is an optional per-cell list
    of ``(key, recomputed, final[, expect_exceeds])`` for value-by-value checks;
    a row's ``final`` may be None (a daily-only item absent from the final sheet).

    Returns a badge dict consumed by ``_recon_badge.html``.
    """
    tol_pct = tol * 100.0
    cell_rows = []
    n_aligned = n_match = 0
    flagged = []

    for row in (rows or []):
        key, rec, fin, row_expect = _norm_row(row)
        d = _delta_pct(rec, fin)
        if fin is None:
            cell_rows.append({"key": key, "recomputed": rec, "final": None,
                              "delta_pct": None, "status": "daily-only", "ok": None})
            continue
        n_aligned += 1
        within = d is not None and abs(d) <= tol_pct
        # PIPE-style: recomputed higher than final is expected, not a flag.
        ok = within or (row_expect and d is not None and d >= 0)
        if ok:
            n_match += 1
        else:
            flagged.append(key)
        cell_rows.append({"key": key, "recomputed": rec, "final": fin,
                          "delta_pct": round(d, 1) if d is not None else None,
                          "status": "match" if ok else "flag", "ok": ok})

    total_delta = _delta_pct(recomputed_total, final_total)
    has_final = final_total is not None and final_total != 0

    if not has_final and n_aligned == 0:
        return {
            "available": False, "status": "info",
            "label": "Recomputed only — no summary grid",
            "note": no_final_note or ("No summary grid is wired for this report, "
                    "so figures are shown as recomputed (daily-first) only."),
            "recomputed_total": recomputed_total, "final_total": None,
            "total_delta_pct": None, "match_pct": None,
            "unit": unit, "rows": cell_rows, "n_aligned": 0,
            "n_match": 0, "n_flagged": 0, "flagged": [], "tol_pct": tol_pct,
            "expect_exceeds": expect_exceeds,
        }

    match_pct = (n_match / n_aligned * 100.0) if n_aligned else None
    total_within = total_delta is not None and abs(total_delta) <= tol_pct
    total_ok = total_within or (expect_exceeds and total_delta is not None and total_delta >= 0)

    expected_undercount = (
        expect_exceeds and total_delta is not None and total_delta > tol_pct)

    if flagged:
        # Shortfall cells (daily-first below the grid) are the ONLY genuine concern
        # under the undercounting-grid model — they must never be downgraded to an
        # "expected" info badge just because the total delta is positive.
        status = "warn"
        label = (f"{len(flagged)} cell(s) below grid >\u00b1{tol_pct:.0f}%"
                 + (f" — daily-first total {total_delta:+.1f}% (grid undercounts)"
                    if expected_undercount else ""))
    elif expected_undercount:
        status = "info"
        label = f"Daily-first {total_delta:+.1f}% vs summary grid (expected — grid undercounts)"
    elif total_ok:
        status = "ok"
        label = (f"Reconciled \u2713 — {match_pct:.0f}% cell match"
                 if match_pct is not None else "Reconciled \u2713")
    else:
        status = "fail"
        label = (f"Off by {total_delta:+.1f}% vs final"
                 if total_delta is not None else "Reconciliation mismatch")

    return {
        "available": True, "status": status, "label": label, "note": None,
        "recomputed_total": recomputed_total, "final_total": final_total,
        "total_delta_pct": round(total_delta, 1) if total_delta is not None else None,
        "match_pct": round(match_pct, 1) if match_pct is not None else None,
        "rows": cell_rows, "n_aligned": n_aligned, "n_match": n_match,
        "n_flagged": len(flagged), "flagged": flagged,
        "unit": unit, "tol_pct": tol_pct, "expect_exceeds": expect_exceeds,
    }
