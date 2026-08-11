"""
auth.py — Minimal shared-password gate for the Prayag dashboard.

Shaped so per-user accounts can be added later by swapping ONE function
(_verify_credentials) without touching the gate, any route, or any template.

Fail-safe: if PRAYAG_APP_PASSWORD is not set the gate is completely inactive
and every page behaves exactly as it did before this module was added.
"""
import hmac
import logging
import os
from datetime import datetime, timezone

from flask import redirect, request, session, url_for

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration helper
# ---------------------------------------------------------------------------

def app_password() -> "str | None":
    """Return the shared password from the environment, or None if not set."""
    return os.environ.get("PRAYAG_APP_PASSWORD") or None


# ---------------------------------------------------------------------------
# Credential check — the ONLY place the password is compared
# ---------------------------------------------------------------------------

def _verify_credentials(username: str, password: str) -> "str | None":
    """Return an identity string on success, None on failure.

    username is accepted (and available to callers) but not checked today —
    the gate is a single shared password.  When per-user accounts arrive,
    replace this function body with a user-table lookup.  The gate, routes,
    and templates do not change.

    Args:
        username: The submitted username (ignored today, stored for future use).
        password: The submitted password.

    Returns:
        "shared" on success; None on failure or when gate is inactive.
    """
    pw = app_password()
    if pw is None:
        return None  # gate inactive — caller must handle this separately
    if hmac.compare_digest(password.encode("utf-8"), pw.encode("utf-8")):
        _log.info("auth: successful login identity=shared")
        return "shared"
    return None


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def current_user() -> "str | None":
    """Return the authenticated identity string, or None if not logged in."""
    return session.get("auth_user")


# ---------------------------------------------------------------------------
# Exempt paths (listed explicitly per spec)
# ---------------------------------------------------------------------------

# These paths are always reachable, regardless of auth state.
_EXEMPT_EXACT: frozenset = frozenset({"/login", "/logout", "/health"})
_EXEMPT_PREFIX: tuple = ("/static/",)


def _is_exempt(path: str) -> bool:
    return path in _EXEMPT_EXACT or any(path.startswith(p) for p in _EXEMPT_PREFIX)


# ---------------------------------------------------------------------------
# before_request gate — registered in app.py via app.before_request(auth.gate)
# ---------------------------------------------------------------------------

def gate():
    """Redirect unauthenticated requests to /login when the gate is active.

    Gate is INACTIVE when PRAYAG_APP_PASSWORD is not set — every page behaves
    exactly as it did before this auth module was added (no redirects, no
    errors).  The only visible change in that state is the banner rendered by
    base.html warning that access control is not configured.
    """
    if app_password() is None:
        return None  # gate inactive

    if _is_exempt(request.path):
        return None

    if not current_user():
        return redirect(url_for("login"))

    return None
