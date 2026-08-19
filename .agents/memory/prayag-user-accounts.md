---
name: Prayag user accounts
description: Rules for the dashboard's per-user login, initial admin seed, and role boundaries.
---

# Per-user accounts are database-authoritative

The shared app-password secret is only the one-time bootstrap source for the
initial administrators. Sign-in then validates a salted password hash in the
Postgres user table, and each request refreshes the session role from that table.

**Why:** removing or demoting an account must take effect immediately; a shared
password fallback would let removed people regain access.
**How to apply:** only use the legacy shared-password check when the database is
unavailable. Never persist, log, or render plaintext passwords.

# Bootstrap must run once, not on every login

Initial administrators are guarded by a durable seed marker. They are inserted
only once, so later account deletion, role edits, and password resets remain
administrator-controlled.

**Why:** re-seeding on every login silently undoes an admin's removal decision.
**How to apply:** change seed membership deliberately with a new migration/seed
version, never by deleting the marker or creating an unconditional upsert.

# Admin-only security boundary

Only administrators can manage accounts or API keys. User-changing forms use a
session-bound CSRF token; the service prevents removing or demoting the last
active administrator.

**Why:** API keys expose broad production data, and account-management actions
are credential-changing operations.
**How to apply:** any future security-sensitive settings page should require the
database-backed admin role and protect state-changing forms with CSRF validation.