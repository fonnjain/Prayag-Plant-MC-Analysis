"""Per-user authentication and role checks for the Prayag dashboard."""
import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import abort, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import store

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration helper
# ---------------------------------------------------------------------------

def app_password() -> "str | None":
    """Return the shared password from the environment, or None if not set."""
    return os.environ.get("PRAYAG_APP_PASSWORD") or None


# ---------------------------------------------------------------------------
# Account bootstrap and credential checks
# ---------------------------------------------------------------------------

_SEED_ADMIN_EMAILS = (
    "preeti.chauhan@prayagindia.com",
    "deepakj@prayagindia.com",
    "ceo@prayagindia.com",
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalise_email(value: str) -> str:
    """Return a canonical email, or an empty string when it is malformed."""
    email = (value or "").strip().lower()
    return email if _EMAIL_RE.fullmatch(email) else ""


def hash_password(password: str) -> str:
    """Generate a salted password hash; plaintext never reaches the store."""
    return generate_password_hash(password)


def _seed_initial_admins() -> None:
    """Create the requested administrator accounts once, from the bootstrap secret."""
    pw = app_password()
    if not pw or not store.AVAILABLE:
        return
    password_hash = hash_password(pw)
    store.seed_initial_users(
        [
            {"email": email, "password_hash": password_hash, "role": "admin"}
            for email in _SEED_ADMIN_EMAILS
        ]
    )


def _verify_credentials(username: str, password: str) -> "dict | None":
    """Return a user identity on success, without exposing password material."""
    pw = app_password()
    if pw is None:
        return None

    email = normalise_email(username)
    if store.AVAILABLE:
        try:
            _seed_initial_admins()
        except store.StoreError:
            _log.exception("auth: unable to initialise user accounts")
            return None
        user = store.get_user_auth(email) if email else None
        if not user:
            return None
        if check_password_hash(user["password_hash"], password):
            _log.info("auth: successful login identity=%s role=%s", email, user["role"])
            return {"id": user["id"], "email": user["email"], "role": user["role"]}
    return None

    # Preserve the existing shared-password fallback only when the database is
    # unavailable. Once Postgres is reachable, removed accounts cannot log in.
    if hmac.compare_digest(password.encode("utf-8"), pw.encode("utf-8")):
        return {"id": None, "email": email or "shared", "role": "admin"}
    return None


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def current_user() -> "str | None":
    """Return the authenticated identity string, or None if not logged in."""
    return session.get("auth_user")


def current_user_id() -> "int | None":
    """Return the current account id, when backed by the user table."""
    value = session.get("auth_user_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def current_user_role() -> str:
    """Return the current session role."""
    return session.get("auth_role", "")


def is_admin() -> bool:
    """True only for a signed-in administrator."""
    return current_user_role() == "admin"


def csrf_token() -> str:
    """Return the current session's anti-CSRF token, creating it as needed."""
    token = session.get("auth_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["auth_csrf"] = token
    return token


def valid_csrf(submitted: str) -> bool:
    """Constant-time CSRF validation for user-management form submissions."""
    expected = session.get("auth_csrf", "")
    return bool(expected and submitted and hmac.compare_digest(expected, submitted))


def admin_required(view):
    """Require a database-backed administrator for sensitive account actions."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not store.AVAILABLE:
            abort(503, "User management requires the configured Postgres database.")
        if not is_admin():
            abort(403)
        return view(*args, **kwargs)
    return wrapped


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

    # The database is authoritative once available: a removed account or a
    # role change takes effect on the very next request, not merely at re-login.
    if store.AVAILABLE:
        user_id = current_user_id()
        user = store.get_user_by_id(user_id) if user_id is not None else None
        if not user or not user["is_active"]:
            session.clear()
            return redirect(url_for("login"))
        session["auth_user"] = user["email"]
        session["auth_role"] = user["role"]

    return None
