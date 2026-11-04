// Screen agent: turns a plain-English request into a screen spec (filters, universe, sort, columns, grouping) over the
// fields of screener.json. The model only chooses fields and thresholds; every number shown on the page comes from
// screener.json. The spec is validated against the field catalogue here and again in the browser; a spec that names
// unknown fields is sent back to the model once with the errors.
import { guard, readBody } from "./_lib/guard.js";
import "../public/pages/screenspec.js";

const PRIMARY = process.env.FU_SCREEN_MODEL || "kimi-k3";
const FALLBACK = process.env.FU_SCREEN_FALLBACK || "deepseek-v4p1-flash";
const SPEC = globalThis.FU_SCREENSPEC;
let CAT = null, CAT_AT = 0;

async function catalogue() {
  if (CAT && Date.now() - CAT_AT < 10 * 60e3) return CAT;
  const base = (process.env.SUPABASE_URL || "").replace(/\/$/, "");
  const r = await fetch(`${base}/storage/v1/object/public/public-data/screener.json`);
  if (!r.ok) throw new Error(`screener.json ${r.status}`);
  const d = await r.json();
  const col = Object.fromEntries(d.cols.map((c, i) => [c, i]));
  const count = (i) => { const m = new Map(); d.rows.forEach(r => m.set(r[i], (m.get(r[i]) || 0) + 1)); return [...m.entries()].filter(([k]) => k).sort((a, b) => b[1] - a[1]); };
  CAT = {
    fields: d.fields, groups: d.groups, sectors: count(col.sector).map(x => x[0]), sectorCounts: count(col.sector),
    industries: count(col.industry).map(x => x[0]), industryCounts: count(col.industry), tickers: d.rows.map(r => r[col.ticker]),
    names: d.rows.map(r => [r[col.ticker], r[col.name]]), asof: d.asof,
  };
  CAT_AT = Date.now();
  return CAT;
}

const UNIT_TEXT = {
  usd: "US dollars (absolute: 10e9 = $10B)", pct: "fraction (0.15 = 15%)", pctl: "percentile 0 to 1", x: "multiple (times)", corr: "correlation −1 to 1",
  num: "number", days: "days", bool: "yes/no", score: "score",
};
const fmtQ = (f) => f.q ? ` | p5 ${f.q[0]}, median ${f.q[1]}, p95 ${f.q[2]}` : f.true != null ? ` | true for ${f.true} of ${f.cov}` : "";

function systemPrompt(cat) {
  const byGroup = new Map();
  cat.fields.forEach(f => { if (!byGroup.has(f.g)) byGroup.set(f.g, []); byGroup.get(f.g).push(f); });
  const groupName = Object.fromEntries((cat.groups || []).map(g => [g.id, g.n]));
  const fieldText = [...byGroup.entries()].map(([g, list]) => `## ${groupName[g] || g}\n` + list.map(f => `${f.id} | ${f.n} | ${f.u} | ${f.d.replace(/\s+/g, " ").slice(0, 170)}${fmtQ(f)}`).join("\n")).join("\n\n");
  return `You build stock screens for an S&P 500 screener. You do not answer with numbers about companies and you never state facts about companies; you only translate the user's request into a screen spec that the screener applies to its own data.

Return one JSON object, nothing else:
{
  "filters": [ {"field": "<field id>", "op": ">"|">="|"<"|"<="|"between"|"="|"!="|"top"|"bottom"|"true"|"false"|"exists", "value": <number or [low, high]>, "pct": <0-100, only for top/bottom>, "scope": "all"|"sector"|"industry" (only for top/bottom)} ],
  "sectors": [<sector names to keep>], "exclude_sectors": [...], "industries": [<industry names to keep>], "exclude_industries": [...],
  "index": "sp500"|"sp100", "tickers": [<only these tickers>], "exclude_tickers": [...],
  "similar_to": null | {"ticker": "NVDA", "n": 25},
  "sort": {"field": "<field id>" | "similarity", "dir": "desc"|"asc"},
  "columns": [<field ids to show, 4 to 10, most relevant first>],
  "group_by": null | "sector" | "industry" | "mcap_bucket" | "corr_cluster" | "quintile:<field id>",
  "limit": null | <number>,
  "explanation": "<one or two plain sentences saying what the screen keeps and how it is sorted>",
  "assumptions": ["<each judgment call you made, e.g. what threshold 'cheap' became>"]
}

Units: pct fields are fractions (15% is 0.15, 2 percentage points is 0.02). usd fields are absolute dollars (market cap $10B is 10000000000). pctl fields run 0 to 1. bool fields use op "true" or "false" with no value. "top"/"bottom" keep the highest/lowest pct percent of that field, across all companies (scope "all") or within each sector (scope "sector").

Conventions (use them unless the user says otherwise, and list the ones you used in assumptions):
- "small caps" in this S&P 500 universe: mcap < 20e9. "mid caps": mcap between 20e9 and 60e9. "large caps": mcap > 60e9. "mega caps": mcap > 300e9.
- "profitable": nm > 0. "unprofitable"/"losing money": nm < 0. "cash generative"/"positive free cash flow": fcf_yield > 0.
- "growing": rev_g_ttm > 0. "fast growing"/"high growth": rev_g_ttm > 0.15. "accelerating revenue": rev_accel > 0. "accelerating earnings": eps_accel > 0. "decelerating": the _accel field < 0.
- "cheap"/"undervalued"/"value": pe bottom 30% within sector (op "bottom", pct 30, scope "sector") unless a multiple is named. "cheap vs its own history": pe_p5 < 0.3 (or the named multiple's _p5). "expensive": pe top 30% within sector.
- "high FCF yield": fcf_yield > 0.05. "low debt": nd_ebitda < 1 (for Financials use de < 1). "net cash": net_debt < 0. "high margin": om > 0.25. "quality": roic > 0.15 and gm_chg3 >= 0 unless the user names other tests. "quality compounders"/"compounders": exactly roic > 0.15, gm_chg3 >= 0, rev_cagr5 > 0.07 (the preset of the same name). Do not add filters the user did not ask for beyond these conventions.
- "dividend payers": div_yield > 0. "high dividend": div_yield > 0.03. "dividend growers": div_yield > 0 plus a rising payout proxy: eps_cagr5 > 0 and fcf_g_ttm > 0. "buybacks": bb_yield > 0.02 or shr_chg1 < 0.
- "above the 200-day": above_sma200 true. "below the 200-day": above_sma200 false. "momentum leaders": mom_rank_3m > 0.8 and above_sma200 true. "near 52-week highs": dist_hi52 > -0.05. "oversold": rsi14 < 30. "overbought": rsi14 > 70.
- Before using a fixed threshold, check it against the field's distribution below; a threshold beyond the p5/p95 range keeps almost nothing, so prefer "top"/"bottom" there.
- "low volatility": vol_1y bottom 20%. "high beta": beta > 1.3. "defensive": beta < 0.8.
- Correlations are small numbers in this data (see each field's distribution), so express correlation requests as ranks, not fixed thresholds: "correlated with X" / "moves with X" / "exposed to X" / "sensitive to X": op "top", pct 20, scope "all" on the matching field (oil c_oil, gold c_gold, dollar c_dxy, bitcoin c_btc, 10Y yield c_tnx, SPY c_spy, Nasdaq c_qqq, sector c_sector), sorted by it desc. Ranks are taken within the chosen index/sectors/industries before the other filters apply, so "banks most hurt by falling rates" = industries banks + c_tnx top 20 + sort c_tnx desc.
- "uncorrelated with the market" / "barely moves with the S&P 500": c_spy bottom 20. "hurt by a stronger dollar" / "dollar-hurt": c_dxy bottom 20 (sort asc). "rate-sensitive" / "hurt by rising rates" / "falls when yields rise": c_tnx bottom 20, sort asc. "hurt by falling rates" / "benefits from rising rates": c_tnx top 20, sort desc. "inflation hedge": cm_cpi top 20.
- "insider buying": ins_buyers_90d >= 1 and ins_net_90d > 0. "insider cluster buying": ins_buyers_90d >= 3. "insider selling": ins_net_90d < 0.
- "reports soon"/"earnings coming up": next_earn_days between 0 and 21. "just reported": days_10q < 21 or days_10k < 21 (pick days_10q unless annual is said).
- "turnaround": rev_accel > 0 and om_chg3 < 0 and ret_1y < 0 unless the user specifies.
- "Altman Z distress": altman_z < 1.8. "strong Piotroski": pio_f >= 7.
- "banks": industries containing "Commercial Banks" (use the exact industry names below). "REITs": sector "Real Estate". "tech": sector "Information Technology". "semis"/"semiconductors": industry "Semiconductors & Related Devices".
- "drop X"/"exclude X": add to exclude_sectors, exclude_industries or exclude_tickers. "only X": sectors/industries/tickers.
- "like TICKER"/"similar to TICKER"/"peers of TICKER": similar_to with that ticker, sort by "similarity" asc. "like TICKER but cheaper": similar_to plus a valuation filter (pe below the median) and say so. Requests about a company's past ("like NVDA in 2016") cannot be matched on history: use the traits instead (high revenue growth, high gross margin, accelerating) and say so in assumptions.
- Sort by the field that best expresses the request (desc for "highest", asc for "lowest"). Columns: the fields you filtered or sorted on first, then 2 to 4 context fields.
- Pick the most specific field available. Never invent a field id. If the request cannot be expressed with the fields, return the closest screen and explain the gap in assumptions.

Follow-ups: when a current screen is given, return the full updated spec: keep every existing filter and setting unless the user changes or removes it, and apply the change ("now only above the 200-day" adds above_sma200 true; "drop banks" adds exclude_industries; "sort by FCF yield" changes only sort).

Sectors: ${cat.sectorCounts.map(([s, n]) => `${s} (${n})`).join("; ")}
Index: sp500 (all) or sp100.
Industries (SEC SIC descriptions, count): ${cat.industryCounts.map(([s, n]) => `${s} (${n})`).join("; ")}
Data as of: prices ${cat.asof?.prices}, latest quarter ${cat.asof?.facts}.

Fields (id | name | unit | definition | distribution across the S&P 500):
${fieldText}`;
}

async function complete(model, messages) {
  const r = await fetch("https://api.fireworks.ai/inference/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: `Bearer ${process.env.FIREWORKS_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model: `accounts/fireworks/models/${model}`, messages, max_tokens: 6000, temperature: 0.1, response_format: { type: "json_object" } }),
  });
  if (!r.ok) { const e = new Error(`model ${r.status}`); e.status = r.status; throw e; }
  const d = await r.json();
  const text = String(d.choices?.[0]?.message?.content || "").replace(/<think>[\s\S]*?<\/think>/g, "").trim();
  const m = text.match(/\{[\s\S]*\}/);
  return JSON.parse(m ? m[0] : text);
}

async function ask(messages) {
  try { return { raw: await complete(PRIMARY, messages), model: PRIMARY }; }
  catch (e) { if (e.status && e.status < 500 && e.status !== 404) throw e; return { raw: await complete(FALLBACK, messages), model: FALLBACK }; }
}

export async function buildSpec({ prompt, history = [], current = null }) {
  const cat = await catalogue();
  const messages = [{ role: "system", content: systemPrompt(cat) }];
  for (const h of history.slice(-6)) {
    if (h && typeof h.prompt === "string" && h.spec) {
      messages.push({ role: "user", content: h.prompt.slice(0, 1500) });
      messages.push({ role: "assistant", content: JSON.stringify(h.spec).slice(0, 6000) });
    }
  }
  const cur = current ? SPEC.validate(current, cat).spec : null;
  messages.push({ role: "user", content: (cur && (cur.filters.length || cur.similar_to || cur.sectors.length || cur.industries.length) ? `Current screen:\n${JSON.stringify(cur)}\n\nRequest: ` : "Request: ") + String(prompt).slice(0, 1500) });
  let { raw, model } = await ask(messages);
  let res = SPEC.validate(raw, cat);
  let retried = false;
  if (res.errors.length) {
    retried = true;
    messages.push({ role: "assistant", content: JSON.stringify(raw).slice(0, 6000) });
    messages.push({ role: "user", content: `That spec has errors: ${res.errors.join("; ")}. Use only field ids, sectors, industries and tickers from the catalogue. Return the corrected full JSON spec.` });
    ({ raw, model } = await ask(messages));
    res = SPEC.validate(raw, cat);
  }
  return { spec: res.spec, errors: res.errors, warnings: res.warnings, retried, model, asof: cat.asof };
}

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST" });
  if (!(await guard(req, res, { name: "screen", perIp: 200, global: 5000 }))) return;
  if (!process.env.FIREWORKS_API_KEY) return res.status(500).json({ error: "The screen agent is not configured" });
  const body = readBody(req);
  if (JSON.stringify(body).length > 60_000) return res.status(413).json({ error: "Request too large" });
  const prompt = String(body.prompt || "").trim();
  if (!prompt) return res.status(400).json({ error: "prompt is required" });
  try {
    const out = await buildSpec({ prompt, history: Array.isArray(body.history) ? body.history : [], current: body.current || null });
    res.setHeader("Cache-Control", "no-store");
    return res.status(200).json(out);
  } catch (e) {
    return res.status(502).json({ error: "The screen agent did not return a usable screen. Try rephrasing.", detail: String(e.message || e).slice(0, 200) });
  }
}
