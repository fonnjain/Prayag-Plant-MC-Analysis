import { pgSchema } from "drizzle-orm/pg-core";

// All drizzle-managed tables live in the dedicated `app` Postgres schema so
// drizzle never touches the Prayag Flask app's tables in `public`. See
// ../../drizzle.config.ts (`schemaFilter`) for the rationale. Declare tables as
// `appSchema.table(...)` — never a bare `pgTable(...)`.
export const appSchema = pgSchema("app");
