// Web Worker for the Technicals page: runs the heavier per-symbol analysis off the main thread.
importScripts("/pages/technicals-lib.js");
self.onmessage = e => {
  const { id, kind, data } = e.data;
  try { self.postMessage({ id, ok: true, result: run(kind, data) }); }
  catch (err) { self.postMessage({ id, ok: false, error: String(err && err.message || err) }); }
};
function run(kind, x) {
  if (kind === "analyze") return TA_analyze(x);
  if (kind === "risk") return TA_risk(x);
  throw new Error("unknown task " + kind);
}
const TA_analyze = x => TA.analyze(x), TA_risk = x => TA.risk(x);
