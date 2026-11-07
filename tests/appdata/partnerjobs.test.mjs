// Planning for the partner-app report and supply jobs.
import test from "node:test";
import assert from "node:assert/strict";
import { planReports, planSupply, drain } from "../../api/_lib/partnerjobs.js";

const now = Date.parse("2026-11-07T00:00:00Z");
const day = (n) => new Date(now - n * 86400_000).toISOString();

test("planReports orders missing primers, failed, other kinds, then week-old slots", () => {
  const rows = [
    { ticker: "A", kind: "primer", status: "ready", updated_at: day(1) },
    { ticker: "A", kind: "memo", status: "failed", updated_at: day(1) },
    { ticker: "B", kind: "primer", status: "ready", updated_at: day(10) },
    { ticker: "B", kind: "memo", status: "ready", updated_at: day(20) },
    { ticker: "B", kind: "preview", status: "ready", updated_at: day(2) },
    { ticker: "B", kind: "recap", status: "generating", updated_at: day(1) },
  ];
  const plan = planReports(["A", "B", "C"], rows, { now }).map((x) => `${x.ticker}/${x.kind}`);
  assert.deepEqual(plan, ["C/primer", "A/memo", "C/memo", "A/preview", "C/preview", "A/recap", "C/recap", "B/memo", "B/primer"]);
});

test("planSupply skips companies with links and ones already tried", () => {
  assert.deepEqual(planSupply(["A", "B", "C", "D"], { A: 5, B: 0 }, new Set(["D"])), ["B", "C"]);
});

test("drain respects worker count, deadline and stop", async () => {
  let live = 0, peak = 0;
  const out = await drain([1, 2, 3, 4, 5], 2, Date.now() + 10_000, async (x) => {
    live++; peak = Math.max(peak, live);
    await new Promise((r) => setTimeout(r, 5));
    live--;
    return x * 2;
  });
  assert.deepEqual(out.sort((a, b) => a - b), [2, 4, 6, 8, 10]);
  assert.equal(peak, 2);
  let n = 0;
  const stopped = await drain([1, 2, 3, 4], 1, Date.now() + 10_000, async (x) => (n++, x), () => n >= 2);
  assert.equal(stopped.length, 2);
  assert.deepEqual(await drain([1, 2], 1, Date.now() - 1, async (x) => x), []);
});
