// Planning for the partner-app jobs (pre-written reports, supply-chain links). Pure functions;
// the HTTP driver is api/cron/partner.js.

export const KIND_PRIORITY = ["primer", "memo", "preview", "recap"];
const REFRESH_DAYS = 7;

/**
 * Report slots to (re)write, most valuable first: missing primers, then failed slots, then the other
 * kinds, then ready slots older than a week (ensureReport skips those whose data didn't move).
 */
export function planReports(tickers, rows, { kinds = KIND_PRIORITY, now = Date.now() } = {}) {
  const have = new Map(rows.map((r) => [`${r.ticker}:${r.kind}`, r]));
  const missing = [], failed = [], stale = [];
  for (const kind of kinds) {
    for (const t of tickers) {
      const r = have.get(`${t}:${kind}`);
      if (!r) missing.push({ ticker: t, kind });
      else if (r.status === "failed") failed.push({ ticker: t, kind });
      else if (r.status === "ready" && now - Date.parse(r.updated_at) > REFRESH_DAYS * 86400_000) stale.push({ ticker: t, kind, at: Date.parse(r.updated_at) });
    }
  }
  const primers = missing.filter((x) => x.kind === "primer");
  const rest = missing.filter((x) => x.kind !== "primer");
  stale.sort((a, b) => a.at - b.at);
  return [...primers, ...failed, ...rest, ...stale.map(({ ticker, kind }) => ({ ticker, kind }))];
}

/** Covered tickers without current supply links, in universe order. */
export function planSupply(tickers, supplyCounts, tried = new Set()) {
  return tickers.filter((t) => !(supplyCounts[t] > 0) && !tried.has(t));
}

/** Run `fn` over items with `n` workers until the deadline or `stop()` says so. */
export async function drain(items, n, deadline, fn, stop = () => false) {
  const out = [];
  let i = 0;
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => {
    while (i < items.length && Date.now() < deadline && !stop()) {
      const item = items[i++];
      out.push(await fn(item));
    }
  }));
  return out;
}
