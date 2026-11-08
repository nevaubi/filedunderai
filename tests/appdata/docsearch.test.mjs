import test from "node:test";
import assert from "node:assert/strict";
import { searchParams } from "../../api/_lib/docsearch.js";

test("docsearch parameters", () => {
  assert.deepEqual(searchParams({ q: "  backlog  ", tickers: "aapl, msft", types: "release", since: "2024-01-01", limit: "500" }).args,
    { q: "backlog", tickers: ["AAPL", "MSFT"], doc_types: ["release"], since: "2024-01-01", lim: 100 });
  assert.equal(searchParams({ q: "x" }).error, "Query must be 2 to 300 characters");
  assert.equal(searchParams({ q: "ok", types: "memo" }).error, "Invalid document type");
  assert.equal(searchParams({ q: "ok", tickers: "AAPL;DROP" }).error, "Invalid tickers");
  assert.equal(searchParams({ q: "ok", since: "yesterday" }).error, "since must be YYYY-MM-DD");
  assert.deepEqual(searchParams({ q: "ok" }).args, { q: "ok", tickers: null, doc_types: null, since: null, lim: 30 });
});
