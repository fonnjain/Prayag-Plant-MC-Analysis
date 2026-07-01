import { defineConfig } from "drizzle-kit";
import path from "path";

if (!process.env.DATABASE_URL) {
  throw new Error("DATABASE_URL, ensure the database is provisioned");
}

// This repl shares ONE Postgres database between the Node/api-server side
// (managed here by drizzle) and the Prayag Flask app, which owns and manages
// its own tables directly via psycopg2 in the `public` schema (sign-offs, acks,
// ideal-hours overrides, the sheet cache, source fingerprints, manual inputs).
//
// Without isolation, `drizzle-kit push` diffs this schema against the live DB
// and tries to DROP every Prayag-owned table — a data-loss landmine that
// `push-force` would execute with no confirmation. `tablesFilter` negation does
// NOT reliably prevent this (drizzle still generated the DROPs in testing), so
// we isolate drizzle to its OWN Postgres schema instead: with `schemaFilter`
// set to `app`, drizzle-kit only ever introspects/manages the `app` schema and
// never sees — let alone drops — the Prayag tables in `public`.
//
// IMPORTANT: every drizzle-managed table MUST be declared in the `app` schema
// via `pgSchema("app")` (see lib/db/src/schema/index.ts). A plain `pgTable`
// lands in `public` and will be silently ignored by push.
const DRIZZLE_SCHEMA = "app";

export default defineConfig({
  schema: path.join(__dirname, "./src/schema/index.ts"),
  dialect: "postgresql",
  dbCredentials: {
    url: process.env.DATABASE_URL,
  },
  schemaFilter: [DRIZZLE_SCHEMA],
});
