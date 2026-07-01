// Export your models here. Add one export per file
// export * from "./posts";
//
// IMPORTANT: this database is SHARED with the Prayag Flask app, which owns the
// `public` schema (managed via psycopg2). Drizzle is scoped to the `app` schema
// (see ../../drizzle.config.ts `schemaFilter`). Every table MUST therefore be
// declared inside the shared `app` pgSchema below — a plain `pgTable` lands in
// `public` and will be silently ignored by `drizzle-kit push`.
//
// Each model/table should ideally be split into different files.
// Each model/table should define a Drizzle table, insert schema, and types:
//
//   import { text, serial } from "drizzle-orm/pg-core";
//   import { createInsertSchema } from "drizzle-zod";
//   import { z } from "zod/v4";
//   import { appSchema } from "./_schema";
//
//   export const postsTable = appSchema.table("posts", {
//     id: serial("id").primaryKey(),
//     title: text("title").notNull(),
//   });
//
//   export const insertPostSchema = createInsertSchema(postsTable).omit({ id: true });
//   export type InsertPost = z.infer<typeof insertPostSchema>;
//   export type Post = typeof postsTable.$inferSelect;

export * from "./_schema";