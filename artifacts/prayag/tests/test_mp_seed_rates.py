"""
Tests for the June 2026 per-hour rate seeding (A) and min_run_block default (B).

Covers:
  - parse_june_pipe_tab: CPVC/SWR/AGRI parsed; UPVC (blank col F) skipped
  - parse_june_machine_tab: UPVC parsed; #N/A / #DIV/0! / blanks skipped
  - Two-tab merge: PIPE PURD PLAN preferred over MACHINE for shared codes
  - "PW 11"-style code normalisation (spaces + hyphens stripped)
  - seed_params default: min_run_block_hours=2, mat rates populated
  - min_run_block default = 2 in MpParams dataclass and scheduler fallback
  - Engine uses stored mat rates to override computed mat_avg
  - Day blocks never shorter than min_run_block
  - Night shift = single item, zero changeovers
  - Excess_kg falls when min_run_block drops from 5 to 2
"""
import pytest


# ─── parse_june_pipe_tab ────────────────────────────────────────────────────

def _pipe_rows():
    """Minimal PIPE PURD PLAN tab: 3-row preamble + data."""
    return [
        ["https://example.com/link"],          # row 0: URL (preamble)
        [],                                     # row 1: blank
        ["S.NO.", "Row Labels", "MACHINE", "TYPE", "CPVC WT", "PER HOUR OUT PUT"],  # row 2: header
        ["1", "PS-2",  "1", "CPVC", "0.62", "95"],    # CPVC
        ["2", "PS-12", "1", "CPVC", "0.52", "95"],    # CPVC
        ["3", "PW 11", "2", "SWR",  "0.30", "310"],   # SWR — "PW 11" style code
        ["4", "PW-12", "2", "SWR",  "0.35", "290"],   # SWR — hyphen style
        ["5", "PA-1",  "3", "AGRI", "0.25", "300"],   # AGRI
        ["6", "PU-1",  "4", "UPVC", "0.40", ""],      # UPVC — col F blank → skip
        ["7", "PU-2",  "4", "UPVC", "0.50", ""],      # UPVC blank
        ["8", "",      "5", "CPVC", "0.30", "110"],   # blank item code → skip
        ["9", "99999999", "6", "SWR", "0.40", "300"], # spurious numeric → skip
    ]


def test_pipe_tab_parses_cpvc():
    from mp_seed import parse_june_pipe_tab
    result = parse_june_pipe_tab(_pipe_rows())
    assert "PS2" in result,  f"PS-2 (CPVC) should be in result: {list(result.keys())}"
    assert "PS12" in result, f"PS-12 (CPVC) should be in result"
    assert result["PS2"]  == 95.0
    assert result["PS12"] == 95.0


def test_pipe_tab_upvc_skipped_when_blank():
    from mp_seed import parse_june_pipe_tab
    result = parse_june_pipe_tab(_pipe_rows())
    assert "PU1" not in result, "UPVC with blank col F must be skipped"
    assert "PU2" not in result


def test_pipe_tab_swr_agri_parsed():
    from mp_seed import parse_june_pipe_tab
    result = parse_june_pipe_tab(_pipe_rows())
    assert "PW11" in result,  f"PW 11 (SWR) should normalise to PW11: {list(result.keys())}"
    assert "PW12" in result,  f"PW-12 (SWR) should normalise to PW12"
    assert "PA1"  in result,  f"PA-1 (AGRI) should be in result"
    assert result["PW11"] == 310.0
    assert result["PA1"]  == 300.0


def test_pipe_tab_blank_code_skipped():
    from mp_seed import parse_june_pipe_tab
    result = parse_june_pipe_tab(_pipe_rows())
    assert "" not in result


def test_pipe_tab_spurious_numeric_skipped():
    from mp_seed import parse_june_pipe_tab
    result = parse_june_pipe_tab(_pipe_rows())
    assert "99999999" not in result


# ─── parse_june_machine_tab ─────────────────────────────────────────────────

def _machine_rows():
    """Minimal MACHINE tab (11 NO SHEET)."""
    return [
        ["11 NO SHEET", "", "", "QTY", "KG", "", "", "PC IN HOURS"],  # row 0: header
        ["", "PU-13",  "1", "0",   "0",      "250",     "0",   "#DIV/0!"],
        ["", "PU-13S", "1", "0",   "0",      "250",     "0",   "#DIV/0!"],
        ["", "PS-3",   "1", "30928","31843", "105",     "303", "102"],    # CPVC duplicate
        ["", "PU-3S",  "1", "840", "3720",   "250",     "15",  "56"],
        ["", "PS-6",   "1", "#N/A","#N/A",   "#N/A",    "#N/A","#N/A"],  # all N/A → skip
        ["", "PU-14",  "1", "0",   "0",      "250",     "0",   "#DIV/0!"],
        ["", "",       "1", "100", "200",     "250",     "2",   "50"],    # blank code → skip
        ["", "PU-15",  "1", "381", "1048",   "250",     "4",   "91"],
        ["", "BAD-NA", "1", "#N/A","#N/A",   "#DIV/0!", "#N/A","#N/A"],  # error in col F → skip
    ]


def test_machine_tab_upvc_parsed():
    from mp_seed import parse_june_machine_tab
    result = parse_june_machine_tab(_machine_rows())
    assert "PU13"  in result, f"PU-13 should be PU13: {list(result.keys())}"
    assert "PU13S" in result
    assert "PU3S"  in result
    assert "PU14"  in result
    assert "PU15"  in result
    assert all(result[k] == 250.0 for k in ("PU13", "PU13S", "PU3S", "PU14", "PU15"))


def test_machine_tab_na_skipped():
    from mp_seed import parse_june_machine_tab
    result = parse_june_machine_tab(_machine_rows())
    assert "PS6"   not in result, "Row with #N/A col F must be skipped"
    assert "BADNA" not in result, "Row with #DIV/0! col F must be skipped"


def test_machine_tab_blank_code_skipped():
    from mp_seed import parse_june_machine_tab
    result = parse_june_machine_tab(_machine_rows())
    assert "" not in result


def test_machine_tab_cpvc_also_parsed():
    """PS-3 (CPVC) from MACHINE tab should be parsed (merge logic will prefer PIPE PURD PLAN)."""
    from mp_seed import parse_june_machine_tab
    result = parse_june_machine_tab(_machine_rows())
    assert "PS3" in result
    assert result["PS3"] == 105.0


# ─── Two-tab merge (prefer PIPE PURD PLAN) ──────────────────────────────────

def test_merge_prefers_pipe_purd_plan():
    """When PS-3 appears in both tabs with different rates, PIPE PURD PLAN wins."""
    from mp_seed import parse_june_pipe_tab, parse_june_machine_tab

    pipe_rows = [
        [],
        [],
        ["S.NO.", "Row Labels", "MACHINE", "TYPE", "WT", "PER HOUR OUT PUT"],
        ["1", "PS-3", "1", "CPVC", "0.99", "108"],   # PIPE PURD PLAN rate = 108
    ]
    machine_rows = [
        ["11 NO SHEET", "", "", "QTY", "KG", "", "", "PC IN HOURS"],
        ["", "PS-3", "1", "30928", "31843", "105", "303", "102"],  # MACHINE rate = 105
        ["", "PU-13", "1", "0", "0", "250", "0", "#DIV/0!"],       # UPVC from machine
    ]

    pplan   = parse_june_pipe_tab(pipe_rows)
    machine = parse_june_machine_tab(machine_rows)

    # Merge: machine first, then pplan overwrites
    merged = {**machine, **pplan}
    assert merged["PS3"] == 108.0,   f"PIPE PURD PLAN (108) should override MACHINE (105), got {merged['PS3']}"
    assert "PU13" in merged,         "UPVC from MACHINE should still be present"
    assert merged["PU13"] == 250.0


def test_pw_code_normalisation():
    """'PW 11' in sheet normalises to 'PW11' (no spaces, no hyphens)."""
    from mp_seed import norm_code
    assert norm_code("PW 11") == "PW11"
    assert norm_code("PW-11") == "PW11"
    assert norm_code(" ps-16 ") == "PS16"
    assert norm_code("PU-3S") == "PU3S"


# ─── seed_params defaults ────────────────────────────────────────────────────

def test_seed_params_defaults_min_run_block():
    """seed_params returns min_run_block_hours=2 in its report."""
    from mp_seed import seed_params
    import mp_model as _mpm

    original = _mpm.upsert_params

    saved = {}

    def mock_upsert(row):
        saved["row"] = row
        return 1

    _mpm.upsert_params = mock_upsert
    try:
        result = seed_params("2026-07")
    finally:
        _mpm.upsert_params = original

    assert result["min_run_block_hours"] == 2.0
    assert saved["row"].min_run_block_hours == 2.0


def test_seed_params_defaults_mat_rates():
    """seed_params seeds CPVC=145.6, UPVC=250, SWR=295, AGRI=300."""
    from mp_seed import seed_params
    import mp_model as _mpm

    saved = {}

    def mock_upsert(row):
        saved["row"] = row
        return 1

    _mpm_orig = _mpm.upsert_params
    _mpm.upsert_params = mock_upsert
    try:
        seed_params("2026-07")
    finally:
        _mpm.upsert_params = _mpm_orig

    row = saved["row"]
    assert abs(row.cpvc_mat_rate - 145.6) < 0.01
    assert row.upvc_mat_rate == 250.0
    assert row.swr_mat_rate  == 295.0
    assert row.agri_mat_rate == 300.0


# ─── MpParams dataclass default ─────────────────────────────────────────────

def test_mp_params_min_run_block_default():
    """MpParams.min_run_block_hours default is 2.0, not 5.0."""
    from mp_model import MpParams
    p = MpParams(segment="PLUMBING", effective_month="2026-07")
    assert p.min_run_block_hours == 2.0, f"Expected 2.0, got {p.min_run_block_hours}"


def test_mp_params_mat_rate_defaults():
    """MpParams.*_mat_rate defaults are 0.0 (meaning 'use computed')."""
    from mp_model import MpParams
    p = MpParams(segment="PLUMBING")
    assert p.cpvc_mat_rate == 0.0
    assert p.upvc_mat_rate == 0.0


# ─── Scheduler min_run_block default ────────────────────────────────────────

def test_scheduler_min_run_block_fallback():
    """Scheduler uses 2.0 when no params_row is available."""
    import mp_scheduler as _sch
    # Monkeypatch _mp.get_params to return None
    import mp_model as _mpm
    orig = _mpm.get_params
    _mpm.get_params = lambda *a, **k: None
    try:
        # build a minimal engine result with one SWR item
        from tests.test_mp_reports import _make_item, _make_engine_result
        result = _make_engine_result([
            _make_item("SWR-100", material="SWR", qty_pcs=500.0,
                       wt_per_pc=0.3, material_kg=156.0, rate_kg_per_hr=295.0,
                       machine="M/C-1"),
        ])
        sched = _sch.run_shift_schedule(
            result.items, [], "PLUMBING", "2026-07"
        )
        assert sched.params_used.get("min_run_block_hours") == 2.0, \
            f"Expected 2.0 fallback, got {sched.params_used.get('min_run_block_hours')}"
    finally:
        _mpm.get_params = orig


# ─── Day blocks never shorter than min_run_block ────────────────────────────

def test_day_blocks_never_shorter_than_min():
    """Every non-idle DAY block must have planned_hours >= min_run_block."""
    from tests.test_mp_reports import _make_item, _make_engine_result
    import mp_scheduler as _sch
    import mp_model as _mpm

    # Force min_run_block = 2 via params
    p = _mpm.MpParams(segment="PLUMBING", effective_month="2026-07",
                      min_run_block_hours=2.0)
    orig = _mpm.get_params
    _mpm.get_params = lambda *a, **k: p
    try:
        # Many small items so several blocks are scheduled
        items = [
            _make_item(f"SWR-{i:03d}", material="SWR",
                       qty_pcs=100.0, wt_per_pc=0.3, material_kg=31.2,
                       rate_kg_per_hr=295.0, machine="M/C-1")
            for i in range(20)
        ]
        result = _make_engine_result(items)
        sched = _sch.run_shift_schedule(result.items, [], "PLUMBING", "2026-07")
        for b in sched.blocks:
            if not b.is_idle and b.shift == "DAY":
                assert b.planned_hours >= 2.0, \
                    f"DAY block {b} has planned_hours={b.planned_hours} < 2.0"
    finally:
        _mpm.get_params = orig


def test_night_blocks_are_single_item():
    """NIGHT shift must always be a single block per machine-day (zero night changeovers)."""
    from tests.test_mp_reports import _make_item, _make_engine_result
    import mp_scheduler as _sch
    import mp_model as _mpm

    p = _mpm.MpParams(segment="PLUMBING", effective_month="2026-07",
                      min_run_block_hours=2.0, night_changeover_allowed=False)
    orig = _mpm.get_params
    _mpm.get_params = lambda *a, **k: p
    try:
        items = [
            _make_item(f"SWR-{i:03d}", material="SWR",
                       qty_pcs=200.0, wt_per_pc=0.3, material_kg=62.4,
                       rate_kg_per_hr=295.0, machine="M/C-1")
            for i in range(30)
        ]
        result = _make_engine_result(items)
        sched = _sch.run_shift_schedule(result.items, [], "PLUMBING", "2026-07")

        # Count NIGHT blocks per (machine, day) — should be at most 1
        from collections import Counter
        night_counts = Counter(
            (b.machine, b.day)
            for b in sched.blocks
            if b.shift == "NIGHT" and not b.is_idle
        )
        for key, count in night_counts.items():
            assert count == 1, f"Machine-day {key} has {count} NIGHT blocks (expected 1)"
    finally:
        _mpm.get_params = orig


def test_excess_kg_falls_with_smaller_min_block():
    """Excess kg with min_run_block=2 must be ≤ excess with min_run_block=5."""
    from tests.test_mp_reports import _make_item, _make_engine_result
    import mp_scheduler as _sch
    import mp_model as _mpm

    items = [
        _make_item(f"SWR-{i:03d}", material="SWR",
                   qty_pcs=80.0, wt_per_pc=0.3, material_kg=24.96,
                   rate_kg_per_hr=295.0, machine="M/C-1")
        for i in range(40)
    ]
    result = _make_engine_result(items)

    orig = _mpm.get_params

    def make_params(min_block):
        return _mpm.MpParams(
            segment="PLUMBING", effective_month="2026-07",
            min_run_block_hours=min_block,
        )

    _mpm.get_params = lambda *a, **k: make_params(5.0)
    sched5 = _sch.run_shift_schedule(result.items, [], "PLUMBING", "2026-07")

    _mpm.get_params = lambda *a, **k: make_params(2.0)
    sched2 = _sch.run_shift_schedule(result.items, [], "PLUMBING", "2026-07")

    _mpm.get_params = orig

    assert sched2.total_excess_kg <= sched5.total_excess_kg, (
        f"excess_kg at min=2 ({sched2.total_excess_kg:.1f}) should be ≤ "
        f"excess at min=5 ({sched5.total_excess_kg:.1f})"
    )


# ─── Engine stored mat rates override computed mat_avg ───────────────────────

def test_engine_uses_stored_mat_rate():
    """When params.swr_mat_rate=295, engine applies 295 to SWR items with no seeded rate."""
    import mp_engine as _eng
    from mp_model import MpParams
    from mp_engine import DemandItem

    params = MpParams(
        segment="PLUMBING", effective_month="2026-07",
        swr_mat_rate=295.0,  # stored override
    )

    # Empty ph_dict + routing → mat_avg computed from nothing → would be 1.0 overall
    ph_dict, mat_avg, overall_avg = _eng._build_rate_lookups([], [])

    # Simulate what run_engine does after _build_rate_lookups
    for _mat, _attr in [
        ("CPVC", "cpvc_mat_rate"), ("UPVC", "upvc_mat_rate"),
        ("SWR",  "swr_mat_rate"),  ("AGRI", "agri_mat_rate"),
    ]:
        _v = float(getattr(params, _attr, 0.0) or 0.0)
        if _v > 0.0:
            mat_avg[_mat] = _v

    rate, estimated, tier = _eng._get_rate("SWR-UNKNOWN", "SWR", ph_dict, mat_avg, overall_avg)
    assert rate == 295.0,  f"Expected 295.0 from stored mat rate, got {rate}"
    assert estimated is True,  "Material-level fallback must keep rate_estimated=True"
    assert tier == "mat_avg", f"Expected tier='mat_avg', got {tier!r}"


def test_engine_stored_rate_does_not_affect_seeded_items():
    """A seeded per-item rate must still be used even when mat rate is stored."""
    import mp_engine as _eng
    from mp_model import MpParams

    params = MpParams(
        segment="PLUMBING", effective_month="2026-07",
        swr_mat_rate=295.0,
    )

    ph_dict = {"SWRKNOWN": 280.0}   # seeded for this specific code
    routing  = [{"item_code": "SWRKNOWN", "machine": "M/C-1", "material": "SWR", "capable": True}]
    _, mat_avg, overall_avg = _eng._build_rate_lookups(
        [{"item_code": "SWRKNOWN", "basis": "kg_per_hr", "value": 280.0}], routing
    )
    for _mat, _attr in [
        ("SWR", "swr_mat_rate"),
    ]:
        _v = float(getattr(params, _attr, 0.0) or 0.0)
        if _v > 0.0:
            mat_avg[_mat] = _v

    rate, estimated, tier = _eng._get_rate("SWRKNOWN", "SWR", ph_dict, mat_avg, overall_avg)
    assert rate == 280.0,  "Seeded per-item rate should win over stored mat rate"
    assert estimated is False
    assert tier == "item"
