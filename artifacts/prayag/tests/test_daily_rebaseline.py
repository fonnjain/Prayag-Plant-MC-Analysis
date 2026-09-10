import sheets


def test_rebaseline_refuses_live_count_mismatch(monkeypatch):
    monkeypatch.setattr(
        sheets,
        "_DAILY_LAYOUTS",
        {"PIPE": [{"emit": "PIPE"}]},
    )
    monkeypatch.setattr(
        sheets.sources,
        "DAILY_SOURCES",
        {"PIPE": {"files": {"2026-07": "file"}}},
    )
    monkeypatch.setattr(sheets, "_get_access_token", lambda: "token")
    monkeypatch.setattr(
        sheets,
        "_load_daily",
        lambda plant, ym, token: [([object()] * 176, {"emit": "PIPE"})],
    )

    try:
        sheets.rebaseline_daily_logical_population("PIPE", "2026-07", 177)
    except sheets.SheetReadError as exc:
        assert "live parse returned 176 records, expected 177" in str(exc)
    else:
        raise AssertionError("mismatched live count must be refused")


def test_rebaseline_updates_only_requested_logical_emitter(monkeypatch):
    monkeypatch.setattr(
        sheets,
        "_DAILY_LAYOUTS",
        {"PIPE": [{"emit": "PIPE"}, {"emit": "MOULDING"}]},
    )
    monkeypatch.setattr(
        sheets.sources,
        "DAILY_SOURCES",
        {"PIPE": {"files": {"2026-06": "file"}}},
    )
    monkeypatch.setattr(sheets, "_get_access_token", lambda: "token")
    monkeypatch.setattr(
        sheets,
        "_load_daily",
        lambda plant, ym, token: [
            ([object()] * 70, {"emit": "PIPE"}),
            ([object()] * 568, {"emit": "MOULDING"}),
        ],
    )
    monkeypatch.setattr(
        sheets._store,
        "daily_read_count",
        lambda scope, ym: 71,
    )
    writes = []
    monkeypatch.setattr(
        sheets._store,
        "rebaseline_daily_read_count",
        lambda scope, ym, count: writes.append((scope, ym, count)),
    )
    monkeypatch.setattr(sheets._store, "pg_cache_clear", lambda key: None)

    result = sheets.rebaseline_daily_logical_population("PIPE", "2026-06", 70)

    assert writes == [("emit:PIPE", "2026-06", 70)]
    assert result["previous_count"] == 71
    assert result["record_count"] == 70