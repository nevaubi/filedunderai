// Applies db/schema.sql during the Vercel build. The schema is idempotent.
import { readFile } from "node:fs/promises";
import pg from "pg";

const url = process.env.POSTGRES_URL_NON_POOLING || process.env.POSTGRES_URL;
if (!url) {
  console.log("migrate: no POSTGRES_URL_NON_POOLING set, skipping");
  process.exit(0);
}

const sql = await readFile(new URL("../db/schema.sql", import.meta.url), "utf8");
const client = new pg.Client({ connectionString: url.replace(/[?&]sslmode=[^&]*/g, ""), ssl: { rejectUnauthorized: false } });
try {
  await client.connect();
  await client.query(sql);
  const { rows } = await client.query("select count(*)::int as n from information_schema.tables where table_schema = 'public'");
  console.log(`migrate: schema applied, ${rows[0].n} public tables`);
} catch (e) {
  console.error("migrate: failed", e.message);
  process.exit(1);
} finally {
  await client.end().catch(() => {});
}
