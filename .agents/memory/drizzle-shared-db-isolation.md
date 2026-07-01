---
name: Drizzle / Prayag shared-DB isolation
description: Why the Node drizzle side is scoped to the `app` Postgres schema and must never manage `public`.
---

The repl has ONE Postgres database (single `DATABASE_URL`) shared by two owners:
- the **Prayag Flask app** owns everything in the `public` schema, created/managed
  directly via psycopg2 (store.py) — NOT drizzle-managed;
- the **Node/api-server** side manages its tables via drizzle (`lib/db`).

**Rule:** drizzle is scoped to a dedicated `app` schema via `schemaFilter: ["app"]`
in `lib/db/drizzle.config.ts`. Every drizzle table MUST be declared with
`appSchema.table(...)` (`appSchema = pgSchema("app")` in
`lib/db/src/schema/_schema.ts`), never a bare `pgTable(...)` (that lands in
`public` and push silently ignores it).

**Why:** with an empty drizzle schema + default `public` scope, `drizzle-kit push`
(run by `scripts/post-merge.sh`) treats Prayag's `public` tables as extraneous and
generates `DROP` for all of them — a data-loss landmine (`push-force` would execute
it uncontested). Confirmed empirically: `tablesFilter` **negation does NOT** prevent
the drops (drizzle still emitted them); `schemaFilter` is the reliable control
because drizzle then never introspects `public` at all.

**How to apply:** never add public-schema tables to the drizzle schema; if the
api-server needs DB tables, put them in `appSchema`. push is a clean no-op when
`app` exists and auto-creates `app` when it doesn't (verified — `public` stays
intact in both cases), so no manual `CREATE SCHEMA` bootstrap is needed.
