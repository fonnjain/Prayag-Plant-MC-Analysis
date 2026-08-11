"""Tests for _mp2_result_from_session / _mp3_fitting_result_from_session caching.

Verifies that run_engine / run_fitting_engine is called only ONCE even when the
session helper is invoked multiple times with the same run_id and unchanged
rejection/wastage state.  This prevents the Machine Plan Comparison route from
re-running the heavy optimiser four times in a single request.

Run from the prayag directory:
    cd artifacts/prayag && python3 -m pytest tests/test_mp_result_cache.py -v
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import MagicMock, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_demand_dict(**kw):
    defaults = dict(
        item_code="CPVC-1",
        raw_code="CPVC-1",
        material="CPVC",
        qty_pcs=100,
    )
    defaults.update(kw)
    return defaults


def _make_fitting_demand_dict(**kw):
    defaults = dict(
        item_code="F-001",
        raw_code="F-001",
        material="CPVC",
        qty_pcs=50,
    )
    defaults.update(kw)
    return defaults


_DUMMY_PAYLOAD = {
    "demand": [_make_demand_dict()],
    "fitting_demand": [_make_fitting_demand_dict()],
    "effective_month": "2026-07",
    "segment": "PLUMBING",
}

_DUMMY_REJ = {"has_data": False, "material": {}, "overall": {}, "items": {}}
_DUMMY_WASTAGE = {"rates": {}, "all": 0.0, "basis": "default", "has_data": False}


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_engine_cache():
    """Wipe _MP2_ENGINE_CACHE before each test so tests are isolated."""
    import app as flask_app
    flask_app._MP2_ENGINE_CACHE.clear()
    yield
    flask_app._MP2_ENGINE_CACHE.clear()


# ── pipe engine cache ──────────────────────────────────────────────────────────

class TestMp2ResultFromSessionCache:
    """_mp2_result_from_session caches EngineResult: run_engine called once."""

    def test_run_engine_called_once_on_repeated_calls(self):
        import app as flask_app
        import mp_engine as eng

        flask_app.app.config["TESTING"] = True
        flask_app.app.config["SECRET_KEY"] = "test-cache"

        fake_result = MagicMock(spec=eng.EngineResult)

        with flask_app.app.test_request_context("/"):
            from flask import session
            session["mp2_run_id"] = "42"

            with (
                patch.object(flask_app, "_mp2_load_run", return_value=_DUMMY_PAYLOAD),
                patch.object(flask_app, "_build_plan_lookups",
                             return_value=(_DUMMY_REJ, _DUMMY_WASTAGE)),
                patch.object(eng, "run_engine", return_value=fake_result) as mock_run,
            ):
                r1 = flask_app._mp2_result_from_session()
                r2 = flask_app._mp2_result_from_session()

        assert r1 is fake_result
        assert r2 is fake_result
        mock_run.assert_called_once(), (
            f"run_engine should be called once but was called {mock_run.call_count} times"
        )

    def test_run_engine_recalled_when_fingerprint_changes(self):
        """If rejection/wastage DB changes, the fingerprint differs → cache miss → re-run."""
        import app as flask_app
        import mp_engine as eng

        flask_app.app.config["TESTING"] = True
        flask_app.app.config["SECRET_KEY"] = "test-cache"

        result_a = MagicMock(spec=eng.EngineResult, name="result_a")
        result_b = MagicMock(spec=eng.EngineResult, name="result_b")
        results_iter = iter([result_a, result_b])

        rej_a = {"has_data": True, "material": {"PIPE:CPVC": 0.05}, "overall": {}, "items": {}}
        rej_b = {"has_data": True, "material": {"PIPE:CPVC": 0.08}, "overall": {}, "items": {}}

        with flask_app.app.test_request_context("/"):
            from flask import session
            session["mp2_run_id"] = "42"

            with patch.object(flask_app, "_mp2_load_run", return_value=_DUMMY_PAYLOAD):
                with patch.object(eng, "run_engine",
                                  side_effect=lambda *a, **kw: next(results_iter)) as mock_run:
                    # First call: rejection state A
                    with patch.object(flask_app, "_build_plan_lookups",
                                      return_value=(rej_a, _DUMMY_WASTAGE)):
                        r1 = flask_app._mp2_result_from_session()

                    # Second call: rejection state B (DB changed)
                    with patch.object(flask_app, "_build_plan_lookups",
                                      return_value=(rej_b, _DUMMY_WASTAGE)):
                        r2 = flask_app._mp2_result_from_session()

        assert r1 is result_a
        assert r2 is result_b
        assert mock_run.call_count == 2

    def test_returns_none_when_no_session_run_id(self):
        import app as flask_app
        flask_app.app.config["TESTING"] = True
        flask_app.app.config["SECRET_KEY"] = "test-cache"

        with flask_app.app.test_request_context("/"):
            from flask import session
            session.clear()
            result = flask_app._mp2_result_from_session()

        assert result is None


# ── fitting engine cache ───────────────────────────────────────────────────────

class TestMp3FittingResultFromSessionCache:
    """_mp3_fitting_result_from_session caches FittingEngineResult: run_fitting_engine called once."""

    def test_fitting_engine_called_once_on_repeated_calls(self):
        import app as flask_app
        import mp_engine as eng

        flask_app.app.config["TESTING"] = True
        flask_app.app.config["SECRET_KEY"] = "test-cache"

        fake_result = MagicMock(spec=eng.FittingEngineResult)

        with flask_app.app.test_request_context("/"):
            from flask import session
            session["mp2_run_id"] = "42"

            with (
                patch.object(flask_app, "_mp2_load_run", return_value=_DUMMY_PAYLOAD),
                patch.object(flask_app, "_build_plan_lookups",
                             return_value=(_DUMMY_REJ, _DUMMY_WASTAGE)),
                patch.object(eng, "run_fitting_engine",
                             return_value=fake_result) as mock_run,
            ):
                r1 = flask_app._mp3_fitting_result_from_session()
                r2 = flask_app._mp3_fitting_result_from_session()

        assert r1 is fake_result
        assert r2 is fake_result
        mock_run.assert_called_once(), (
            f"run_fitting_engine should be called once but was called {mock_run.call_count} times"
        )

    def test_returns_none_when_no_fitting_demand(self):
        """Returns None (not an error) when fitting_demand list is empty."""
        import app as flask_app
        flask_app.app.config["TESTING"] = True
        flask_app.app.config["SECRET_KEY"] = "test-cache"

        payload_no_fit = {**_DUMMY_PAYLOAD, "fitting_demand": []}

        with flask_app.app.test_request_context("/"):
            from flask import session
            session["mp2_run_id"] = "42"

            with (
                patch.object(flask_app, "_mp2_load_run", return_value=payload_no_fit),
                patch.object(flask_app, "_build_plan_lookups",
                             return_value=(_DUMMY_REJ, _DUMMY_WASTAGE)),
            ):
                result = flask_app._mp3_fitting_result_from_session()

        assert result is None


# ── params_fingerprint ─────────────────────────────────────────────────────────

class TestParamsFingerprint:
    """_params_fingerprint is stable and changes when inputs change."""

    def test_same_inputs_give_same_fingerprint(self):
        import app as flask_app
        fp1 = flask_app._params_fingerprint(_DUMMY_REJ, _DUMMY_WASTAGE)
        fp2 = flask_app._params_fingerprint(_DUMMY_REJ, _DUMMY_WASTAGE)
        assert fp1 == fp2

    def test_different_rej_lookup_gives_different_fingerprint(self):
        import app as flask_app
        rej_alt = {"has_data": True, "material": {"PIPE:CPVC": 0.09}, "overall": {}, "items": {}}
        fp1 = flask_app._params_fingerprint(_DUMMY_REJ, _DUMMY_WASTAGE)
        fp2 = flask_app._params_fingerprint(rej_alt, _DUMMY_WASTAGE)
        assert fp1 != fp2

    def test_different_wastage_lookup_gives_different_fingerprint(self):
        import app as flask_app
        wastage_alt = {"rates": {"PIPE": 0.03}, "all": 0.03, "basis": "measured", "has_data": True}
        fp1 = flask_app._params_fingerprint(_DUMMY_REJ, _DUMMY_WASTAGE)
        fp2 = flask_app._params_fingerprint(_DUMMY_REJ, wastage_alt)
        assert fp1 != fp2

    def test_fingerprint_is_16_hex_chars(self):
        import app as flask_app
        fp = flask_app._params_fingerprint(_DUMMY_REJ, _DUMMY_WASTAGE)
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)
