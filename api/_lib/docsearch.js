// Validation for /api/app-docsearch query parameters (pure, tested).
const TYPES = new Set(["release", "supplement", "presentation", "transcript", "remarks", "annual_report", "other"]);
const TICKER = /^[A-Z0-9.^=-]{1,12}$/;

export function searchParams(query) {
  const q = String(query.q || "").trim();
  if (q.length < 2 || q.length > 300) return { error: "Query must be 2 to 300 characters" };
  const list = (v) => String(v || "").split(",").map((s) => s.trim()).filter(Boolean);
  const tickers = list(query.tickers).map((t) => t.toUpperCase());
  if (tickers.length > 50 || tickers.some((t) => !TICKER.test(t))) return { error: "Invalid tickers" };
  const types = list(query.types);
  if (types.some((t) => !TYPES.has(t))) return { error: "Invalid document type" };
  const since = String(query.since || "").trim();
  if (since && !/^\d{4}-\d{2}-\d{2}$/.test(since)) return { error: "since must be YYYY-MM-DD" };
  const limit = Math.min(100, Math.max(1, Number.parseInt(query.limit || "30", 10) || 30));
  return { args: { q, tickers: tickers.length ? tickers : null, doc_types: types.length ? types : null, since: since || null, lim: limit } };
}
