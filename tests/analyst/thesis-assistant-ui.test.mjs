import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";
const rec = (end, start, v, filed) => ({
  end,
  start,
  v,
  filed,
  accn: "0000000001-26-000001",
});
async function setup({ delay = false } = {}) {
  const dom = new JSDOM('<main id="page"></main>', {
      url: "https://research.example/#theses",
      runScripts: "outside-only",
    }),
    w = dom.window;
  w.page = w.document.querySelector("#page");
  w.el = (tag, attrs = {}, ...kids) => {
    const e = w.document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const c of kids.flat())
      if (c != null && c !== false) e.append(c.nodeType ? c : String(c));
    return e;
  };
  w.TICKERS = ["AMD", "MSFT"];
  w.IDX = {
    AMD: { name: "AMD", sector: "Technology" },
    MSFT: { name: "Microsoft", sector: "Technology" },
  };
  w.SHORT = { AMD: "AMD", MSFT: "Microsoft" };
  w.PAGES = [];
  w.PX = {};
  w.watchlist = () => ["AMD"];
  w.store = {
    get: (k, v) => {
      const value = w.localStorage.getItem("fu:" + k);
      return value == null ? v : JSON.parse(value);
    },
    set: (k, v) => w.localStorage.setItem("fu:" + k, JSON.stringify(v)),
  };
  w.toast = (m) => (w.lastToast = m);
  w.route = () => {
    w.routeCount = (w.routeCount || 0) + 1;
  };
  w.openSource = () => {};
  w.fmtMoney = (v) => String(v);
  w.cls = () => "";
  w.ensureCompany = async () => ({});
  let release;
  const gate = delay ? new Promise((r) => (release = r)) : Promise.resolve();
  w.getJSON = async (p) => {
    if (p.startsWith("companies/")) {
      await gate;
      return {
        ticker: p.split("/")[1].split(".")[0],
        cik: 1,
        filings: [],
        statements: {
          income: [
            {
              label: "Revenue",
              unit: "USD",
              annual: [],
              quarterly: [
                rec("2026-03-31", "2026-01-01", 100, "2026-05-01"),
                rec("2026-06-30", "2026-04-01", 110, "2026-08-01"),
                rec("2026-09-30", "2026-07-01", 120, "2026-11-01"),
              ],
            },
            {
              label: "Operating income",
              unit: "USD",
              annual: [],
              quarterly: [rec("2026-09-30", "2026-07-01", 24, "2026-11-01")],
            },
          ],
          cashflow: [],
          balance: [],
        },
      };
    }
    return {};
  };
  w.chatPanel = (host, cfg) => {
    w.thesisChat = cfg;
    return {
      ask: () => {
        throw Error("No production model calls in tests");
      },
    };
  };
  for (const path of [
    "public/lib/evidence.js",
    "public/lib/analyst.js",
    "public/pages/analyst.js",
  ])
    w.eval(fs.readFileSync(path, "utf8"));
  await w.FU_PAGES.theses();
  const button = (label) =>
    [...w.document.querySelectorAll("button")].find(
      (b) => b.textContent === label,
    );
  const set = (label, v) => {
    const n = w.document.querySelector('[aria-label="' + label + '"]');
    assert.ok(n, "Missing " + label);
    n.value = v;
    n.dispatchEvent(
      new w.Event(n.tagName === "SELECT" ? "change" : "input", {
        bubbles: true,
      }),
    );
    return n;
  };
  return { w, button, set, release, close: () => dom.window.close() };
}
const trace = () => ({ done() {}, fail() {} });
async function assistant(h, title = "AMD revenue resilience") {
  h.set("Your investment thesis", title);
  assert.ok(
    h.button("Open thesis assistant"),
    "optional assistant button exists",
  );
  h.button("Open thesis assistant").click();
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(h.w.thesisChat, "chatPanel must be wired");
  const tools = h.w.thesisChat.tools(trace);
  return {
    read: tools.find((t) => t.name === "read_thesis_draft"),
    propose: tools.find((t) => t.name === "propose_thesis_criteria"),
  };
}
const criterion = (read, overrides = {}) => ({
  type: "threshold",
  metric: "Revenue",
  unit: "USD",
  operator: "gte",
  threshold: 125,
  evidenceIds: [
    read.metrics.find((m) => m.metric === "Revenue").observations.at(-1)
      .evidenceId,
  ],
  thresholdBasis: "user_review_estimate",
  rationale: "Illustrative review target, not reported guidance.",
  ...overrides,
});
test("thesis assistant reads exact units/evidence, stages review and appends without saving until Track", async () => {
  const h = await setup();
  try {
    const tools = await assistant(h);
    h.set("Metric", "Revenue");
    h.set("Criterion threshold", "80");
    h.button("Add criterion").click();
    const read = await tools.read.execute({ metric: "Revenue" });
    assert.equal(read.ticker, "AMD");
    assert.equal(read.metrics[0].unit, "USD");
    assert.equal(read.metrics[0].observations.at(-1).value, 120);
    assert.ok(read.draftToken);
    const result = await tools.propose.execute({
      draftToken: read.draftToken,
      summary: "Revenue condition",
      criteria: [criterion(read)],
      gaps: [],
    });
    assert.equal(result.status, "pending_review");
    assert.equal(h.w.localStorage.getItem("fu:analyst:theses"), null);
    assert.ok(h.w.page.textContent.includes("user-review estimate"));
    h.button("Add proposed criteria to draft").click();
    assert.equal(h.w.document.querySelectorAll(".an-criteria>div").length, 2);
    assert.equal(h.w.localStorage.getItem("fu:analyst:theses"), null);
    h.button("Track this thesis").click();
    await new Promise((r) => setTimeout(r, 0));
    const saved = JSON.parse(h.w.localStorage.getItem("fu:analyst:theses"));
    assert.equal(saved[0].criteria.length, 2);
    assert.equal(saved[0].criteria[0].threshold, 80);
    assert.equal(saved[0].criteria[1].threshold, 125);
    assert.equal(saved[0].criteria[1].thresholdBasis, "user_review_estimate");
  } finally {
    h.close();
  }
});
test("thesis proposals reject wrong unit, unknown evidence, unsupported metric/type and nonfinite targets", async () => {
  const h = await setup();
  try {
    const tools = await assistant(h),
      read = await tools.read.execute({ metric: "Revenue" });
    for (const patch of [
      { unit: "pct" },
      { evidenceIds: ["made-up"] },
      { metric: "Data-center market share" },
      { type: "insider_drawdown" },
      { threshold: Infinity },
      { thresholdBasis: "reported_fact" },
    ])
      await assert.rejects(() =>
        tools.propose.execute({
          draftToken: read.draftToken,
          criteria: [criterion(read, patch)],
        }),
      );
    assert.equal(h.w.document.querySelectorAll(".an-criteria>div").length, 0);
    assert.equal(h.w.localStorage.getItem("fu:analyst:theses"), null);
  } finally {
    h.close();
  }
});
test("changed thesis, ticker or manually edited pending draft prevents stale AI acceptance", async () => {
  const h = await setup();
  try {
    const tools = await assistant(h);
    let read = await tools.read.execute({ metric: "Revenue" });
    await tools.propose.execute({
      draftToken: read.draftToken,
      criteria: [criterion(read)],
    });
    h.set("Your investment thesis", "A different question");
    assert.equal(
      h.button("Add proposed criteria to draft")?.disabled ?? true,
      true,
    );
    assert.equal(h.w.document.querySelectorAll(".an-criteria>div").length, 0);
    await assert.rejects(
      () =>
        tools.propose.execute({
          draftToken: read.draftToken,
          criteria: [criterion(read)],
        }),
      /changed|stale|read/i,
    );
    read = await tools.read.execute({ metric: "Revenue" });
    await tools.propose.execute({
      draftToken: read.draftToken,
      criteria: [criterion(read)],
    });
    h.set("Company", "MSFT");
    assert.equal(
      h.button("Add proposed criteria to draft")?.disabled ?? true,
      true,
    );
    assert.equal(h.w.localStorage.getItem("fu:analyst:theses"), null);
  } finally {
    h.close();
  }
});
test("market-share decomposition explains missing numerator/denominator instead of substituting revenue", async () => {
  const h = await setup();
  try {
    const tools = await assistant(h, "AMD gains data-center share"),
      read = await tools.read.execute({ metric: "Revenue" });
    await assert.rejects(
      () =>
        tools.propose.execute({
          draftToken: read.draftToken,
          criteria: [criterion(read)],
        }),
      /market.share|numerator|denominator/i,
    );
    const result = await tools.propose.execute({
      draftToken: read.draftToken,
      criteria: [],
      gaps: [
        "Market share requires a sourced data-center market numerator and denominator. Revenue alone does not measure share.",
      ],
    });
    assert.equal(result.status, "coverage_gaps");
    assert.ok(h.w.page.textContent.includes("denominator"));
    assert.equal(h.w.document.querySelectorAll(".an-criteria>div").length, 0);
  } finally {
    h.close();
  }
});
test("async metric read cannot overwrite a draft changed during company loading", async () => {
  const h = await setup({ delay: true });
  try {
    const tools = await assistant(h),
      pending = tools.read.execute({ metric: "Revenue" });
    h.set("Your investment thesis", "Keep this newer text");
    h.release();
    const result = await pending;
    assert.equal(result.status, "stale");
    assert.equal(
      h.w.document.querySelector('[aria-label="Your investment thesis"]').value,
      "Keep this newer text",
    );
    assert.equal(h.w.document.querySelectorAll(".an-criteria>div").length, 0);
  } finally {
    h.close();
  }
});
test("trend proposals require complete observed evidence and valid counts, and reject leaves the manual draft untouched", async () => {
  const h = await setup();
  try {
    const tools = await assistant(h),
      read = await tools.read.execute({ metric: "Revenue" }),
      trend = {
        type: "trend",
        metric: "Revenue",
        unit: "USD",
        direction: "up",
        consecutive: 2,
        evidenceIds: read.metrics[0].observations.map((x) => x.evidenceId),
      };
    for (const patch of [
      { consecutive: 0 },
      { consecutive: 9 },
      { consecutive: 1.5 },
      { direction: "sideways" },
      { evidenceIds: trend.evidenceIds.slice(-1) },
    ])
      await assert.rejects(() =>
        tools.propose.execute({
          draftToken: read.draftToken,
          criteria: [{ ...trend, ...patch }],
        }),
      );
    await tools.propose.execute({
      draftToken: read.draftToken,
      criteria: [trend],
    });
    h.button("Reject proposed criteria").click();
    assert.equal(h.w.document.querySelectorAll(".an-criteria>div").length, 0);
    assert.equal(h.w.localStorage.getItem("fu:analyst:theses"), null);
    await tools.propose.execute({
      draftToken: read.draftToken,
      criteria: [trend],
    });
    h.button("Add proposed criteria to draft").click();
    h.button("Edit criterion").click();
    h.set("Criterion consecutive quarters", "1");
    h.button("Save criterion").click();
    h.button("Track this thesis").click();
    const saved = JSON.parse(h.w.localStorage.getItem("fu:analyst:theses"));
    assert.equal(saved[0].criteria[0].type, "trend");
    assert.equal(saved[0].criteria[0].consecutive, 1);
  } finally {
    h.close();
  }
});
test("manual draft additions invalidate a staged proposal without losing those additions", async () => {
  const h = await setup();
  try {
    const tools = await assistant(h),
      read = await tools.read.execute({ metric: "Revenue" });
    await tools.propose.execute({
      draftToken: read.draftToken,
      criteria: [criterion(read)],
    });
    h.set("Metric", "Revenue");
    h.set("Criterion threshold", "90");
    h.button("Add criterion").click();
    assert.equal(
      h.button("Add proposed criteria to draft")?.disabled ?? true,
      true,
    );
    assert.equal(h.w.document.querySelectorAll(".an-criteria>div").length, 1);
    assert.ok(h.w.page.textContent.includes("90"));
    assert.equal(h.w.localStorage.getItem("fu:analyst:theses"), null);
  } finally {
    h.close();
  }
});
