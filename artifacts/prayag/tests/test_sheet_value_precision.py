"""Regression coverage for full-precision Google Sheets transport."""

import sheets


def test_read_values_requests_raw_numbers_and_formatted_dates(monkeypatch):
    seen = {}

    def fake_get(url, token):
        seen["url"] = url
        assert token == "token"
        return {"values": [[83.80030000000002]]}

    monkeypatch.setattr(sheets, "_api_get", fake_get)

    assert sheets.read_values("file-id", "Report-5", "token") == [[83.80030000000002]]
    assert "valueRenderOption=UNFORMATTED_VALUE" in seen["url"]
    assert "dateTimeRenderOption=FORMATTED_STRING" in seen["url"]


def test_batch_get_requests_raw_numbers_and_formatted_dates(monkeypatch):
    seen = {}

    def fake_get(url, token):
        seen["url"] = url
        assert token == "token"
        return {"valueRanges": [{"values": [[172.01]]}, {"values": [[1]]}]}

    monkeypatch.setattr(sheets, "_api_get", fake_get)

    assert sheets.batch_get("file-id", ["Report-5", "Report-12"], "token") == {
        "Report-5": [[172.01]],
        "Report-12": [[1]],
    }
    assert "ranges=Report-5" in seen["url"]
    assert "ranges=Report-12" in seen["url"]
    assert "valueRenderOption=UNFORMATTED_VALUE" in seen["url"]
    assert "dateTimeRenderOption=FORMATTED_STRING" in seen["url"]