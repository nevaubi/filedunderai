import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";
const current = {
  call_id: "MSFT-2026-10-28-current",
  doc: "qa",
  alt_docs: ["prepared"],
  kind: "transcript",
  event_type: "earnings",
  date: "2026-10-28",
  label: "Q1 FY27",
  period_end: "2026-09-30",
  has_qa: true,
};
const prior = {
  call_id: "MSFT-2026-07-23-prior",
  doc: "prior",
  kind: "transcript",
  event_type: "earnings",
  date: "2026-07-23",
  label: "Q4 FY26",
  period_end: "2026-06-30",
  has_qa: true,
};
const remarks = {
  call_id: "MSFT-2026-04-22-remarks",
  doc: "remarks",
  kind: "remarks",
  event_type: "earnings",
  date: "2026-04-22",
  label: "Q3 FY26",
  period_end: "2026-03-31",
};
const docs = {
  qa: {
    url: "https://www.microsoft.com/current-qa.docx",
    doc_type: "transcript",
  },
  prepared: {
    url: "https://www.microsoft.com/current-prepared.docx",
    doc_type: "transcript",
  },
  prior: {
    url: "https://www.microsoft.com/prior-call.pdf",
    doc_type: "transcript",
  },
  remarks: {
    url: "https://www.microsoft.com/prior-remarks.pdf",
    doc_type: "remarks",
  },
};
async function setup(calls = [current, prior, remarks], overrides = {}) {
  const dom = new JSDOM('<main id="page"></main>', {
      url: "https://research.example/#ideas",
      runScripts: "outside-only",
    }),
    w = dom.window,
    requests = [];
  w.page = w.document.getElementById("page");
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
  w.TICKERS = ["MSFT"];
  w.IDX = { MSFT: { sector: "Technology", name: "Microsoft" } };
  w.INDEX = { companies: [{ ticker: "MSFT", sector: "Technology" }] };
  w.SHORT = { MSFT: "Microsoft" };
  w.PX = {};
  w.PAGES = [];
  w.fmtMoney = (v) => String(v);
  w.cls = () => "";
  w.openSource = () => {};
  w.toast = (message) => {
    w.lastToast = message;
  };
  w.route = () => {};
  w.renderRail = () => {};
  w.watchlist = () => ["MSFT"];
  w.store = { get: (k, v) => v, set: () => {} };
  w.ensureCompany = async () => ({});
  const payloads = {
    ["calls/MSFT/" + current.call_id + ".json"]: {
      call_id: current.call_id,
      turns: [
        { i: 0, sp: 0, sec: "prepared", x: "We see a headwind." },
        { i: 1, sp: 1, sec: "qa", x: "The complete question and answer." },
      ],
    },
    ["calls/MSFT/" + prior.call_id + ".json"]: {
      call_id: prior.call_id,
      turns: [
        { i: 0, sp: 0, sec: "prepared", x: "We see strong demand." },
        { i: 1, sp: 1, sec: "qa", x: "The earlier question and answer." },
      ],
    },
    ["calls/MSFT/" + remarks.call_id + ".json"]: {
      call_id: remarks.call_id,
      turns: [{ i: 0, sp: 0, sec: "prepared", x: "Prepared remarks only." }],
    },
    ...overrides,
  };
  w.getJSON = async (p) => {
    requests.push(p);
    if (p.startsWith("companies/"))
      return {
        ticker: "MSFT",
        cik: 1,
        statements: { income: [], cashflow: [], balance: [] },
        filings: [],
        sector: "Technology",
      };
    if (p === "calls/MSFT.json") return { ticker: "MSFT", calls, docs };
    if (payloads[p]) return payloads[p];
    return {};
  };
  for (const p of [
    "public/lib/evidence.js",
    "public/lib/analyst.js",
    "public/pages/analyst.js",
  ])
    w.eval(fs.readFileSync(p, "utf8"));
  await w.FU_PAGES.ideas();
  return { dom, w, requests, close: () => dom.window.close() };
}
async function settle() {
  await new Promise((r) => setTimeout(r, 10));
}
const findButton = (w, label) =>
  [...w.document.querySelectorAll("button")].find(
    (n) => n.textContent === label,
  );
test("disclosure UI resolves bundle doc URLs, loads real turn-file paths and compares two matching periods with exact sources", async () => {
  const { w, requests, close } = await setup();
  try {
    const earlier = w.document.querySelector(
        '[aria-label="Earlier sourced disclosure"]',
      ),
      latest = w.document.querySelector(
        '[aria-label="Current sourced disclosure"]',
      );
    assert.ok(earlier, "earlier source selector must exist");
    assert.ok(latest);
    assert.equal(latest.value, current.call_id);
    assert.equal(earlier.value, prior.call_id);
    assert.ok(latest.selectedOptions[0].textContent.includes("Q1 FY27"));
    assert.ok(latest.selectedOptions[0].textContent.includes("Transcript"));
    await settle();
    assert.ok(requests.includes("calls/MSFT/" + current.call_id + ".json"));
    assert.ok(requests.includes("calls/MSFT/" + prior.call_id + ".json"));
    assert.equal(
      w.document.querySelector('[aria-label="Loaded current disclosure text"]')
        .value,
      "We see a headwind.\n\nThe complete question and answer.",
    );
    findButton(w, "Compare loaded disclosures").click();
    await settle();
    assert.ok(w.page.textContent.includes("“headwind”"));
    assert.ok(w.page.textContent.includes("0 → 1 mentions"));
    assert.ok(w.page.textContent.includes("1 → 0 mentions"));
    for (const url of [docs.qa.url, docs.prepared.url, docs.prior.url])
      assert.ok([...w.page.querySelectorAll("a")].some((a) => a.href === url));
    earlier.value = remarks.call_id;
    earlier.dispatchEvent(new w.Event("change", { bubbles: true }));
    findButton(w, "Load selected sources").click();
    await settle();
    assert.equal(findButton(w, "Compare loaded disclosures").disabled, true);
    assert.ok(w.page.textContent.includes("same source type"));
  } finally {
    close();
  }
});
test("one sourced call preloads current text without fabricating a prior period and keeps a manual fallback", async () => {
  const { w, requests, close } = await setup([current]);
  try {
    await settle();
    assert.equal(
      w.document.querySelector('[aria-label="Earlier sourced disclosure"]')
        .value,
      "",
    );
    assert.equal(
      w.document.querySelector('[aria-label="Loaded current disclosure text"]')
        .value,
      "We see a headwind.\n\nThe complete question and answer.",
    );
    assert.equal(findButton(w, "Compare loaded disclosures").disabled, true);
    assert.ok(w.page.textContent.includes("Only one sourced disclosure"));
    assert.ok(
      w.document.querySelector('[aria-label="Earlier disclosure text"]'),
    );
    assert.equal(requests.filter((p) => p.startsWith("calls/MSFT/")).length, 1);
  } finally {
    close();
  }
});
test("mismatched or empty turn payload shows source-loading failure and cannot produce a comparison", async () => {
  const path = "calls/MSFT/" + current.call_id + ".json";
  const { w, close } = await setup([current, prior], {
    [path]: { call_id: "WRONG", turns: [{ x: "Never display this text" }] },
  });
  try {
    await settle();
    assert.equal(findButton(w, "Compare loaded disclosures").disabled, true);
    assert.ok(!w.page.textContent.includes("Never display this text"));
    assert.ok(w.page.textContent.includes("does not match"));
  } finally {
    close();
  }
});
test("no sourced calls retains an honest manual comparison with source URLs and no network text invention", async () => {
  const { w, requests, close } = await setup([]);
  try {
    assert.equal(
      w.document.querySelector('[aria-label="Current sourced disclosure"]'),
      null,
    );
    assert.ok(w.page.textContent.includes("No sourced call transcript"));
    assert.equal(w.page.querySelector(".an-details").open, true);
    w.document.querySelector('[aria-label="Earlier source URL"]').value =
      "https://example.com/earlier";
    w.document.querySelector('[aria-label="Current source URL"]').value =
      "https://example.com/current";
    w.document.querySelector('[aria-label="Earlier disclosure text"]').value =
      "We see strong demand.";
    w.document.querySelector('[aria-label="Current disclosure text"]').value =
      "We see a headwind.";
    findButton(w, "Compare sourced language").click();
    await settle();
    assert.ok(w.page.textContent.includes("0 → 1 mentions"));
    assert.ok(w.page.textContent.includes("user-supplied"));
    assert.equal(requests.filter((p) => p.startsWith("calls/MSFT/")).length, 0);
  } finally {
    close();
  }
});
