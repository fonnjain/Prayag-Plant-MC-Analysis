"""Regression test: a raw socket-level TimeoutError from urlopen must be
wrapped in SheetReadError, not allowed to escape _api_get.

A read timeout that fires mid-response surfaces as a bare TimeoutError
(subclass of OSError, NOT of urllib.error.URLError). If _api_get does not
catch it, it propagates past the per-(plant, month) isolation in
get_daily_records (which only catches SheetReadError) and 500s the whole
page even though the failure is transient.

Run: cd artifacts/prayag && python3 -m tests.test_api_get_timeout
"""
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sheets


def _patch_urlopen(exc, calls):
    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        raise exc
    return fake_urlopen


def test_raw_timeout_is_wrapped_and_retried():
    calls = []
    orig = urllib.request.urlopen
    orig_sleep = sheets.time.sleep
    urllib.request.urlopen = _patch_urlopen(TimeoutError("read timed out"), calls)
    sheets.time.sleep = lambda *_a, **_k: None  # no real backoff delay
    try:
        raised = False
        try:
            sheets._api_get("https://sheets.googleapis.com/v4/x", "tok")
        except sheets.SheetReadError:
            raised = True
        assert raised, "a raw TimeoutError must surface as SheetReadError"
        assert len(calls) == sheets._API_MAX_RETRIES, (
            f"transient timeout must be retried {sheets._API_MAX_RETRIES}x, "
            f"got {len(calls)}")
        assert calls == [sheets._API_REQUEST_TIMEOUT_SECONDS] * sheets._API_MAX_RETRIES
    finally:
        urllib.request.urlopen = orig
        sheets.time.sleep = orig_sleep
    print("PASS: raw TimeoutError is retried and wrapped in SheetReadError")


def test_connection_reset_is_wrapped():
    calls = []
    orig = urllib.request.urlopen
    orig_sleep = sheets.time.sleep
    urllib.request.urlopen = _patch_urlopen(ConnectionResetError("reset"), calls)
    sheets.time.sleep = lambda *_a, **_k: None
    try:
        raised = False
        try:
            sheets._api_get("https://sheets.googleapis.com/v4/x", "tok")
        except sheets.SheetReadError:
            raised = True
        assert raised, "a dropped/reset connection must surface as SheetReadError"
    finally:
        urllib.request.urlopen = orig
        sheets.time.sleep = orig_sleep
    print("PASS: ConnectionResetError is wrapped in SheetReadError")


if __name__ == "__main__":
    test_raw_timeout_is_wrapped_and_retried()
    test_connection_reset_is_wrapped()
    print("\nAll _api_get transient-timeout regression tests passed.")
