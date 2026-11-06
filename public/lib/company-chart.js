/* Company price history. Requires company-chart.css; window.TA from pages/technicals-lib.js
 * enables the existing SMA, Wilder RSI and MACD calculations.
 *
 * FU_COMPANY_CHART.render(container, { ticker, companyName, prices, benchmarkPrices })
 * returns an idempotent cleanup function. Rendering again in the same container disposes
 * its previous chart. This module does not fetch data or modify the shared price cache.
 *
 * Accepted Filed Under daily formats:
 *   PX:         { meta, bars: [{ t: Date|ISO|epoch, o, h, l, c, v }], light?: boolean }
 *   prices/*:   { meta, d: [[epochSeconds, open, high, low, close, volume]] }
 *   tech/bars/*:{ meta, d: ["YYYY-MM-DD"], o: [], h: [], l: [], c: [], v: [] }
 * benchmarkPrices must contain actual SPY bars in one of these formats.
 * These OHLC values are already split adjusted. Never apply splits again, substitute
 * metadata regularMarketPrice, or mix dividend-adjusted close with these OHLC values.
 */
(function (root) {
  "use strict";
  const NS = "http://www.w3.org/2000/svg", DAY = 86400000;
  const RANGES = ["1M", "3M", "6M", "YTD", "1Y", "3Y", "All"];
  const COLORS = { price: "#315b86", up: "#18725d", down: "#b13d42", sma20: "#16828e", sma50: "#7952b3", sma200: "#b77620", rsi: "#7952b3", signal: "#b77620", benchmark: "#687789" };
  const mounted = new WeakMap();
  let sequence = 0;
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const positive = value => finite(value) && value > 0;
  const signed = (value, digits = 2, suffix = "%") => finite(value) ? `${value < 0 ? "−" : "+"}${Math.abs(value).toFixed(digits)}${suffix}` : "Unavailable";
  const number = (value, digits = 2) => finite(value) ? value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "Unavailable";
  const compact = value => !finite(value) ? "—" : Math.abs(value) >= 1e9 ? `${(value / 1e9).toFixed(1)}B` : Math.abs(value) >= 1e6 ? `${(value / 1e6).toFixed(1)}M` : Math.abs(value) >= 1e3 ? `${(value / 1e3).toFixed(1)}K` : number(value, 0);

  function day(value) {
    if (value == null || value === "") return null;
    let time;
    if (typeof value?.getTime === "function") time = value.getTime();
    else if (finite(value)) time = Math.abs(value) < 1e12 ? value * 1000 : value;
    else if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}(?:T|$)/.test(value)) time = Date.parse(value);
    if (!finite(time) || !finite(new Date(time).getTime())) return null;
    const date = new Date(time).toISOString().slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
    if (typeof value === "string" && value.length === 10 && date !== value) return null;
    return date;
  }

  function normalize(input) {
    const source = input || {}, meta = source.meta || {};
    let rows = Array.isArray(source) ? source : Array.isArray(source.bars) ? source.bars : [];
    if (Array.isArray(source.d)) {
      rows = Array.isArray(source.c) ? source.d.map((date, i) => [date, source.o?.[i], source.h?.[i], source.l?.[i], source.c[i], source.v?.[i]]) : source.d;
    }
    const dates = new Map();
    let omitted = 0;
    for (const row of rows) {
      if (!row || typeof row !== "object") { omitted++; continue; }
      const b = Array.isArray(row) ? { t: row[0], o: row[1], h: row[2], l: row[3], c: row[4], v: row[5] } : row;
      const date = day(b.t ?? b.date);
      if (!date || !positive(b.c)) { omitted++; continue; }
      const ohlc = !source.light && [b.o, b.h, b.l].every(positive) && b.h >= Math.max(b.o, b.l, b.c) && b.l <= Math.min(b.o, b.h, b.c);
      dates.set(date, { date, time: Date.parse(date), o: ohlc ? b.o : null, h: ohlc ? b.h : null, l: ohlc ? b.l : null, c: b.c, v: !source.light && finite(b.v) && b.v >= 0 ? b.v : null });
    }
    const bars = [...dates.values()].sort((a, b) => a.date.localeCompare(b.date)).map((b, index) => ({ ...b, index }));
    return { bars, meta, omitted, light: !!source.light, generated: source.generated, symbol: String(meta.symbol || source.symbol || source.ticker || "").toUpperCase() };
  }

  function monthsBefore(date, months) {
    const d = new Date(date), target = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() - months, 1));
    const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
    target.setUTCDate(Math.min(d.getUTCDate(), lastDay));
    return target.toISOString().slice(0, 10);
  }

  function selectRange(bars, range) {
    if (!bars.length || range === "All") return { bars, partial: false };
    const last = bars.at(-1).date;
    const cutoff = range === "YTD" ? `${last.slice(0, 4)}-01-01` : monthsBefore(last, { "1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36 }[range]);
    const first = bars.findIndex(b => b.date >= cutoff);
    // Include the preceding real close, especially the prior-year close for YTD.
    return { bars: bars.slice(Math.max(0, first - 1)), partial: bars[0].date > cutoff };
  }

  function compare(bars, benchmark) {
    const map = new Map(benchmark.map(b => [b.date, b.c]));
    const shared = bars.filter(b => map.has(b.date));
    if (shared.length < 2) return null;
    const first = shared[0], last = shared.at(-1), base = map.get(first.date);
    const visible = bars.filter(b => b.date >= first.date && b.date <= last.date);
    return {
      bars: visible, first: first.date, last: last.date, map,
      company: visible.map(b => 100 * (b.c / first.c - 1)),
      benchmark: visible.map(b => map.has(b.date) ? 100 * (map.get(b.date) / base - 1) : null),
      companyReturn: 100 * (last.c / first.c - 1), benchmarkReturn: 100 * (map.get(last.date) / base - 1),
    };
  }

  function bucketKey(date, interval) {
    if (interval === "monthly") return date.slice(0, 7);
    const d = new Date(date), offset = (d.getUTCDay() + 6) % 7;
    return new Date(+d - offset * DAY).toISOString().slice(0, 10);
  }

  function aggregate(bars, maxBars) {
    if (bars.length <= maxBars) return { bars, interval: "Daily" };
    function group(interval) {
      const buckets = new Map();
      for (const bar of bars) {
        const key = bucketKey(bar.date, interval);
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(bar);
      }
      return [...buckets.values()].map(group => {
        const first = group[0], last = group.at(-1), full = group.every(b => b.o != null);
        return { ...last, startDate: first.date, sessions: group.length, o: full ? first.o : null, h: full ? Math.max(...group.map(b => b.h)) : null, l: full ? Math.min(...group.map(b => b.l)) : null, v: group.every(b => b.v != null) ? group.reduce((sum, b) => sum + b.v, 0) : null };
      });
    }
    const weeks = group("weekly");
    return weeks.length <= maxBars ? { bars: weeks, interval: "Weekly" } : { bars: group("monthly"), interval: "Monthly" };
  }

  function extent(values, includeZero = false) {
    let low = includeZero ? 0 : Infinity, high = includeZero ? 0 : -Infinity;
    for (const value of values) if (finite(value)) { low = Math.min(low, value); high = Math.max(high, value); }
    if (!finite(low) || !finite(high)) return [-1, 1];
    const padding = (high - low || Math.abs(high) * 0.05 || 1) * 0.09;
    return [low - padding, high + padding];
  }

  function pathFor(values, x, y) {
    let d = "", connected = false;
    values.forEach((value, i) => {
      if (!finite(value)) { connected = false; return; }
      d += `${connected ? "L" : "M"}${x(i).toFixed(2)},${y(value).toFixed(2)}`;
      connected = true;
    });
    return d;
  }

  function render(container, options = {}) {
    if (!container?.ownerDocument) throw new TypeError("A chart container element is required");
    mounted.get(container)?.();
    const doc = container.ownerDocument, win = doc.defaultView || root, id = `fcc-${++sequence}`;
    const ticker = String(options.ticker || "Company").toUpperCase();
    const data = normalize(options.prices), spy = normalize(options.benchmarkPrices), bars = data.bars;
    const meta = data.meta, currency = /^[A-Z]{3}$/.test(meta.currency || "") ? meta.currency : null;
    const price = value => {
      if (!finite(value)) return "Unavailable";
      const decimals = Math.abs(value) < 1 ? 4 : 2;
      try { return new Intl.NumberFormat("en-US", { ...(currency ? { style: "currency", currency } : {}), minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(value); }
      catch { return number(value, decimals); }
    };
    const e = (tag, attrs = {}, ...children) => {
      const node = doc.createElement(tag);
      for (const [key, value] of Object.entries(attrs)) if (value != null) node.setAttribute(key, String(value));
      children.flat().forEach(child => { if (child != null) node.append(child?.nodeType ? child : doc.createTextNode(String(child))); });
      return node;
    };
    const s = (tag, attrs = {}, text) => {
      const node = doc.createElementNS(NS, tag);
      for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
      if (text != null) node.textContent = text;
      return node;
    };
    const shell = e("section", { class: "fu-company-chart", "aria-label": `${options.companyName || ticker} price history and technical indicators` });
    const listeners = [];
    let disposed = false, observer, width = 0, drawing = null;
    const on = (node, type, handler) => { node.addEventListener(type, handler); listeners.push(() => node.removeEventListener(type, handler)); };
    function cleanup() {
      if (disposed) return;
      disposed = true;
      observer?.disconnect();
      listeners.forEach(remove => remove());
      shell.remove();
      if (mounted.get(container) === cleanup) mounted.delete(container);
    }
    mounted.set(container, cleanup);
    container.append(shell);

    if (!bars.length) {
      shell.append(e("div", { class: "fcc-empty", role: "status" }, e("h2", {}, "Price history unavailable"), e("p", {}, `No usable daily price observations were supplied for ${ticker}. A chart will appear when stored history is available.`)));
      return cleanup;
    }

    const TA = root.TA;
    const technicals = !!TA && ["sma", "rsi", "macd"].every(key => typeof TA[key] === "function");
    const closes = bars.map(b => b.c), empty = () => bars.map(() => null);
    const indicators = technicals ? { sma20: TA.sma(closes, 20), sma50: TA.sma(closes, 50), sma200: TA.sma(closes, 200), rsi: TA.rsi(closes, 14), macd: TA.macd(closes, 12, 26, 9) } : { sma20: empty(), sma50: empty(), sma200: empty(), rsi: empty(), macd: { line: empty(), signal: empty(), hist: empty() } };
    const latest = bars.at(-1), previous = bars.at(-2);
    const canCompare = ticker !== "SPY" && (!spy.symbol || spy.symbol === "SPY") && spy.bars.length >= 2;
    const state = { range: "1Y", mode: bars.every(b => b.o != null) ? "candle" : "line", sma20: false, sma50: true, sma200: true, volume: true, rsi: true, macd: false, benchmark: false, inspection: null };
    const header = e("div", { class: "fcc-header" },
      e("div", {}, e("p", { class: "fcc-eyebrow" }, "Price & technicals"), e("h2", { class: "fcc-title" }, "Market history"),
        e("div", { class: "fcc-quote" }, e("span", { class: "fcc-latest-value" }, price(latest.c)), previous ? e("span", { class: `fcc-change ${latest.c >= previous.c ? "fcc-positive" : "fcc-negative"}` }, signed(100 * (latest.c / previous.c - 1)), e("span", {}, " vs prior stored session")) : null)),
      e("div", { class: "fcc-asof" }, e("span", {}, "Latest stored daily bar"), e("strong", {}, latest.date), e("span", {}, `${currency || "Currency not supplied"} · not a live quote`)));
    shell.append(header);

    const toolbar = e("div", { class: "fcc-toolbar" }), ranges = e("div", { class: "fcc-segment", role: "group", "aria-label": "Price history timeframe" });
    const rangeButtons = new Map();
    for (const range of RANGES) {
      const button = e("button", { type: "button", "data-range": range, "aria-pressed": state.range === range }, range);
      on(button, "click", () => { state.range = range; state.inspection = null; draw(); });
      rangeButtons.set(range, button); ranges.append(button);
    }
    const modes = e("div", { class: "fcc-segment", role: "group", "aria-label": "Chart style" }), modeButtons = new Map();
    for (const [mode, label] of [["candle", "Candles"], ["line", "Line"]]) {
      const button = e("button", { type: "button", "data-mode": mode, "aria-pressed": state.mode === mode }, label);
      on(button, "click", () => { state.mode = mode; state.inspection = null; draw(); });
      modeButtons.set(mode, button); modes.append(button);
    }
    toolbar.append(ranges, modes); shell.append(toolbar);

    const settings = e("div", { class: "fcc-settings", role: "group", "aria-label": "Chart overlays and panels" });
    const controls = new Map();
    for (const [name, label] of [["sma20", "SMA 20"], ["sma50", "SMA 50"], ["sma200", "SMA 200"], ["volume", "Volume"], ["rsi", "RSI 14"], ["macd", "MACD"], ["benchmark", "Compare SPY"]]) {
      const input = e("input", { type: "checkbox", id: `${id}-${name}`, "data-indicator": name });
      input.checked = state[name];
      const swatch = COLORS[name] ? e("i", { class: "fcc-swatch", style: `--fcc-swatch:${COLORS[name]}`, "aria-hidden": "true" }) : null;
      const labelNode = e("label", { for: input.id, class: "fcc-toggle" }, input, swatch, label);
      controls.set(name, { input, label: labelNode }); settings.append(labelNode);
      on(input, "change", () => { state[name] = input.checked; state.inspection = null; draw(); });
    }
    shell.append(settings);
    const rangeSummary = e("div", { class: "fcc-range-summary", "aria-live": "polite" });
    const benchmarkSummary = e("div", { class: "fcc-benchmark-summary", "aria-live": "polite" });
    shell.append(rangeSummary, benchmarkSummary);
    const readout = e("dl", { class: "fcc-readout", "aria-label": "Inspected price bar" }), fields = {};
    for (const [name, label] of [["date", "Session"], ["open", "Open"], ["high", "High"], ["low", "Low"], ["close", "Close"], ["volume", "Volume"], ["benchmark", "SPY change"]]) {
      const value = e("dd", { "data-field": name }), group = e("div", { class: `fcc-readout-${name}` }, e("dt", {}, label), value);
      readout.append(group); fields[name] = value;
    }
    shell.append(readout);
    const plot = e("div", { class: "fcc-plot", tabindex: "0", role: "group", "aria-label": `${ticker} interactive price chart`, "aria-describedby": `${id}-help` });
    const svg = s("svg", { role: "img", "aria-labelledby": `${id}-svg-title ${id}-svg-desc`, preserveAspectRatio: "xMidYMid meet" });
    plot.append(svg); shell.append(plot);
    const help = e("p", { id: `${id}-help`, class: "fcc-help" }, "Hover or tap to inspect. Keyboard: ← → move one bar, Home / End jump to the first / last bar, Esc clears inspection.");
    const announcement = e("div", { class: "fcc-announcement fcc-sr-only", role: "status", "aria-live": "polite", "aria-atomic": "true" });
    shell.append(help, announcement);

    const insights = e("div", { class: "fcc-insights" }, e("div", { class: "fcc-insights-heading" }, e("h3", {}, "Technical snapshot"), e("span", {}, `Daily calculations · ${latest.date}`)));
    const metrics = e("dl", { class: "fcc-metrics" });
    function metric(key, label, value, detail) {
      metrics.append(e("div", { "data-metric": key }, e("dt", {}, label), e("dd", { class: "fcc-metric-value" }, value), e("dd", { class: "fcc-metric-detail" }, detail)));
    }
    for (const period of [50, 200]) {
      const value = indicators[`sma${period}`].at(-1);
      metric(`sma${period}`, `${period}-day average`, price(value), value == null ? technicals ? `Requires ${period} daily closes; ${bars.length} available.` : "Technical calculations unavailable for this chart." : `${signed(100 * (latest.c / value - 1))} distance from the average.`);
    }
    const rsi = indicators.rsi.at(-1), macd = indicators.macd.line.at(-1), signal = indicators.macd.signal.at(-1), hist = indicators.macd.hist.at(-1);
    metric("rsi", "RSI · 14 sessions", number(rsi, 1), rsi == null ? technicals ? "Requires 15 daily closes." : "Technical calculations unavailable for this chart." : `${rsi >= 70 ? "At or above" : rsi <= 30 ? "At or below" : "Between"} the ${rsi >= 70 ? "70" : rsi <= 30 ? "30" : "30 / 70"} reference ${rsi > 30 && rsi < 70 ? "levels" : "level"}. Wilder smoothing.`);
    metric("macd", "MACD · 12 / 26 / 9", signal == null ? "Unavailable" : number(macd, 2), signal == null ? technicals ? "Requires 34 daily closes for the signal line." : "Technical calculations unavailable for this chart." : `Signal ${number(signal, 2)} · histogram ${signed(hist, 2, "")}. In price units.`);
    const recent = bars.slice(-20), volume = recent.length === 20 && recent.every(b => b.v != null) ? recent.reduce((sum, b) => sum + b.v, 0) / 20 : null;
    metric("volume", "20-session average volume", volume == null ? "Unavailable" : compact(volume), volume == null ? "Requires 20 reported daily volume observations." : `${number(volume, 0)} shares · latest ${latest.v == null ? "unavailable" : volume > 0 ? (latest.v / volume).toFixed(2) + "× average" : number(latest.v, 0) + " shares"}.`);
    const year = bars.filter(b => b.time >= latest.time - 364 * DAY), fullOHLC = year.every(b => b.o != null);
    const lo = Math.min(...year.map(b => fullOHLC ? b.l : b.c)), hi = Math.max(...year.map(b => fullOHLC ? b.h : b.c));
    metric("range", fullOHLC ? "52-week low / high" : "52-week closing range", `${price(lo)} – ${price(hi)}`, `${year.length} observed sessions${bars[0].time > latest.time - 364 * DAY ? " · partial history" : ""}. ${signed(100 * (latest.c / hi - 1))} from the ${fullOHLC ? "high" : "highest close"}.`);
    insights.append(metrics, e("p", { class: "fcc-calculation-note" }, "Averages, RSI and MACD use all supplied daily closes before the selected view. Reference levels describe historical price behavior; they do not forecast returns."));
    shell.append(insights);

    const quality = [];
    if (data.omitted) quality.push(`${data.omitted} unusable price ${data.omitted === 1 ? "record" : "records"} omitted.`);
    if (bars.some(b => b.o == null)) quality.push("Close-only observations are shown without invented open, high or low values.");
    if (!technicals) quality.push("Technical indicators are unavailable; daily prices remain usable.");
    if (quality.length) shell.append(e("p", { class: "fcc-quality", role: "note" }, quality.join(" ")));
    const provenance = e("div", { class: "fcc-provenance" });
    provenance.append(e("p", { class: "fcc-basis" }, "Basis: split-adjusted OHLC as supplied · dividends excluded from price returns. Volume is reported shares; no further split adjustment is applied."));
    const retrieved = meta.updated || meta.retrieved || data.generated;
    provenance.append(e("p", { class: "fcc-source" }, `${meta.source || "Stored daily market prices"}${retrieved ? ` · ${meta.updated ? "History updated" : "Retrieved"} ${retrieved}` : " · Retrieval timestamp not supplied"}.`));
    shell.append(provenance);

    function setControl(name, available, explanation = "") {
      const control = controls.get(name);
      control.input.disabled = !available;
      control.input.checked = state[name] && available;
      control.label.title = explanation;
      control.label.classList.toggle("fcc-disabled", !available);
    }

    function draw() {
      if (disposed) return;
      width = Math.max(280, Math.round(container.getBoundingClientRect().width || container.clientWidth || 960));
      const selected = selectRange(bars, state.range);
      const aligned = canCompare ? compare(selected.bars, spy.bars) : null;
      if (!aligned) state.benchmark = false;
      const comparison = state.benchmark ? aligned : null;
      const allVisible = comparison ? comparison.bars : selected.bars;
      const candlesAvailable = allVisible.every(b => b.o != null) && !comparison;
      const mode = comparison || !candlesAvailable ? "line" : state.mode;
      const grouped = mode === "candle" ? aggregate(allVisible, Math.max(35, Math.floor((width - 100) / 4))) : { bars: allVisible, interval: "Daily" };
      const visible = grouped.bars, count = visible.length, last = count - 1;
      const hasVolume = visible.some(b => b.v != null);
      const showVolume = hasVolume && state.volume, showRSI = technicals && bars.length >= 15 && state.rsi, showMACD = technicals && bars.length >= 34 && state.macd;
      const left = 16, right = width < 500 ? 72 : 86, chartWidth = width - left - right;
      const top = 18, priceHeight = width < 500 ? 236 : 276, paneGap = 22;
      let bottom = top + priceHeight;
      const panes = {};
      if (showVolume) { panes.volume = { top: bottom + paneGap, height: 56 }; bottom = panes.volume.top + panes.volume.height; }
      if (showRSI) { panes.rsi = { top: bottom + paneGap, height: 72 }; bottom = panes.rsi.top + panes.rsi.height; }
      if (showMACD) { panes.macd = { top: bottom + paneGap, height: 84 }; bottom = panes.macd.top + panes.macd.height; }
      const height = bottom + 38, step = chartWidth / count, x = i => left + step * (i + 0.5);
      const ma = Object.fromEntries([20, 50, 200].map(n => [`sma${n}`, visible.map(b => indicators[`sma${n}`][b.index])]));
      const main = comparison ? comparison.company : visible.map(b => b.c);
      const scaleValues = comparison ? [...comparison.company, ...comparison.benchmark] : visible.flatMap(b => mode === "candle" && b.o != null ? [b.l, b.h] : [b.c]);
      if (!comparison) for (const name of ["sma20", "sma50", "sma200"]) if (state[name]) scaleValues.push(...ma[name]);
      const [low, high] = extent(scaleValues), y = value => top + priceHeight * (1 - (value - low) / (high - low));
      const firstDate = visible[0].startDate || visible[0].date, lastDate = visible.at(-1).date;
      const rangeReturn = allVisible.length > 1 ? 100 * (allVisible.at(-1).c / allVisible[0].c - 1) : null;
      rangeSummary.replaceChildren(e("span", {}, `${firstDate} – ${lastDate}`), e("strong", { class: rangeReturn >= 0 ? "fcc-positive" : "fcc-negative" }, rangeReturn == null ? "One observation" : signed(rangeReturn)), e("span", {}, `${allVisible.length.toLocaleString("en-US")} daily sessions · ${grouped.interval.toLowerCase()} ${mode === "candle" ? "candles" : "line"}${selected.partial && !comparison ? " · partial history for this range" : ""}`));
      benchmarkSummary.hidden = !comparison && !!aligned;
      benchmarkSummary.textContent = comparison ? `${ticker} ${signed(aligned.companyReturn)} · SPY ${signed(aligned.benchmarkReturn)} · relative ${signed(aligned.companyReturn - aligned.benchmarkReturn, 2, " pp")}. Rebased on shared sessions, ${aligned.first} – ${aligned.last}; missing SPY sessions are not filled.` : "SPY comparison unavailable for the selected dates.";
      fields.benchmark.parentElement.hidden = !comparison;
      for (const [range, button] of rangeButtons) button.setAttribute("aria-pressed", String(state.range === range));
      for (const [key, button] of modeButtons) button.setAttribute("aria-pressed", String(mode === key));
      modeButtons.get("candle").disabled = !candlesAvailable;
      modeButtons.get("candle").title = comparison ? "Comparison uses percentage lines. Turn off Compare SPY for candles." : candlesAvailable ? "Open, high, low and close for each bar" : "Complete open, high, low and close observations are required.";
      for (const n of [20, 50, 200]) setControl(`sma${n}`, technicals && bars.length >= n && !comparison, comparison ? "Moving averages use the price scale. Turn off Compare SPY to view them." : `Simple mean of ${n} daily closes; requires ${n} observations.`);
      setControl("volume", hasVolume, "Reported volume in shares. Weekly or monthly bars sum their observed daily volume.");
      setControl("rsi", technicals && bars.length >= 15, "Wilder RSI using 14 sessions; reference levels at 30 and 70.");
      setControl("macd", technicals && bars.length >= 34, "12- and 26-session EMA difference, with a 9-session EMA signal.");
      setControl("benchmark", !!aligned, aligned ? "SPY and company price returns, starting at the same observed session. Dividends excluded." : "At least two shared sessions of actual SPY history are required.");

      svg.replaceChildren(s("title", { id: `${id}-svg-title` }, `${ticker} ${comparison ? "and SPY price returns" : "price history"}`), s("desc", { id: `${id}-svg-desc` }, `${firstDate} to ${lastDate}. ${grouped.interval} ${mode === "candle" ? "candlesticks" : "closing-price line"}${comparison ? ", rebased to zero percent on the first shared date" : ", split-adjusted prices"}. The values for the inspected bar appear above the chart. Use the chart's keyboard controls to inspect every bar.`));
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`); svg.setAttribute("width", width); svg.setAttribute("height", height);
      svg.style.aspectRatio = `${width} / ${height}`;
      const grid = s("g", { class: "fcc-grid" }); svg.append(grid);
      for (let i = 0; i <= 4; i++) {
        const value = low + (high - low) * i / 4, py = y(value);
        grid.append(s("line", { x1: left, x2: width - right, y1: py, y2: py }), s("text", { x: width - right + 10, y: py + 4 }, comparison ? `${value.toFixed(1)}%` : price(value)));
      }
      function line(values, color, name, yScale = y, attrs = {}) {
        const path = s("path", { d: pathFor(values, x, yScale), stroke: color, fill: "none", "stroke-width": 1.5, "stroke-linejoin": "round", "stroke-linecap": "round", "data-series": name, ...attrs });
        svg.append(path);
        // Isolated benchmark points remain visible without bridging missing observations.
        values.forEach((value, i) => { if (finite(value) && !finite(values[i - 1]) && !finite(values[i + 1])) svg.append(s("circle", { cx: x(i), cy: yScale(value), r: 2.2, fill: color })); });
        return path;
      }
      if (mode === "candle") {
        const candles = s("g", { "data-series": "candles" }), bodyWidth = Math.max(1.3, Math.min(9, step * 0.64));
        visible.forEach((b, i) => {
          const color = b.c >= b.o ? COLORS.up : COLORS.down, center = x(i);
          candles.append(s("line", { x1: center, x2: center, y1: y(b.h), y2: y(b.l), stroke: color, "stroke-width": 1 }), s("rect", { x: center - bodyWidth / 2, y: Math.min(y(b.o), y(b.c)), width: bodyWidth, height: Math.max(1, Math.abs(y(b.c) - y(b.o))), fill: b.c >= b.o ? "#ffffff" : color, stroke: color, "stroke-width": 1 }));
        });
        svg.append(candles);
      } else {
        if (!comparison && count > 1) svg.append(s("path", { d: `${pathFor(main, x, y)}L${x(last)},${top + priceHeight}L${x(0)},${top + priceHeight}Z`, fill: COLORS.price, opacity: 0.045 }));
        line(main, COLORS.price, "price", y, { "stroke-width": 1.8 });
      }
      if (comparison) line(comparison.benchmark, COLORS.benchmark, "benchmark", y, { "stroke-width": 1.8, "stroke-dasharray": "5 3" });
      else for (const name of ["sma20", "sma50", "sma200"]) if (state[name] && ma[name].some(finite)) line(ma[name], COLORS[name], name);
      const latestY = y(main.at(-1));
      svg.append(s("line", { x1: left, x2: width - right, y1: latestY, y2: latestY, stroke: COLORS.price, "stroke-opacity": 0.35, "stroke-dasharray": "3 4" }));
      const marker = s("g", { class: "fcc-last-marker" });
      marker.append(s("rect", { x: width - right + 2, y: latestY - 10, width: right - 5, height: 20, rx: 3, fill: COLORS.price }), s("text", { x: width - right + 8, y: latestY + 4, fill: "#ffffff" }, comparison ? signed(main.at(-1)) : price(latest.c)));
      svg.append(marker);

      function paneFrame(name, label, ticks, scale, formatter) {
        const pane = panes[name], g = s("g", { "data-pane": name, class: "fcc-grid" });
        g.append(s("text", { x: left, y: pane.top - 9, class: "fcc-pane-title" }, label));
        for (const value of ticks) g.append(s("line", { x1: left, x2: width - right, y1: scale(value), y2: scale(value) }), s("text", { x: width - right + 10, y: scale(value) + 4 }, formatter(value)));
        svg.append(g);
        return g;
      }
      if (showVolume) {
        const pane = panes.volume, max = Math.max(1, ...visible.map(b => b.v ?? 0)), vy = v => pane.top + pane.height * (1 - v / max);
        const group = paneFrame("volume", `VOLUME · ${grouped.interval.toUpperCase()} SHARES`, [0, max], vy, compact);
        visible.forEach((b, i) => { if (b.v != null) group.append(s("rect", { x: x(i) - Math.max(1, step * 0.63) / 2, y: vy(b.v), width: Math.max(1, step * 0.63), height: pane.top + pane.height - vy(b.v), fill: b.c >= (b.o ?? visible[i - 1]?.c ?? b.c) ? COLORS.up : COLORS.down, opacity: 0.32 })); });
      }
      if (showRSI) {
        const pane = panes.rsi, ry = v => pane.top + pane.height * (1 - v / 100);
        paneFrame("rsi", "RSI · 14 SESSIONS", [30, 70], ry, v => number(v, 0));
        line(visible.map(b => indicators.rsi[b.index]), COLORS.rsi, "rsi", ry);
      }
      if (showMACD) {
        const pane = panes.macd, values = name => visible.map(b => indicators.macd[name][b.index]);
        const m = values("line"), sig = values("signal"), histogram = values("hist"), [min, max] = extent([...m, ...sig, ...histogram], true), my = v => pane.top + pane.height * (1 - (v - min) / (max - min));
        const group = paneFrame("macd", "MACD · 12 / 26 / 9 · SIGNAL IN GOLD", [min, 0, max], my, v => number(v, 2));
        histogram.forEach((value, i) => { if (finite(value)) group.append(s("rect", { x: x(i) - Math.max(1, step * 0.62) / 2, y: Math.min(my(0), my(value)), width: Math.max(1, step * 0.62), height: Math.max(0.5, Math.abs(my(value) - my(0))), fill: value >= 0 ? COLORS.up : COLORS.down, opacity: 0.42 })); });
        line(m, COLORS.price, "macd", my); line(sig, COLORS.signal, "macd-signal", my);
      }
      const dates = s("g", { class: "fcc-dates" }), ticks = Math.min(count, width < 500 ? 3 : 6), used = new Set();
      for (let i = 0; i < ticks; i++) {
        const index = ticks === 1 ? 0 : Math.round(last * i / (ticks - 1));
        if (used.has(index)) continue;
        used.add(index);
        const date = visible[index].date, label = allVisible.length > 250 ? `${date.slice(0, 4)}-${date.slice(5, 7)}` : date.slice(5);
        dates.append(s("text", { x: x(index), y: bottom + 24, "text-anchor": index === 0 ? "start" : index === last ? "end" : "middle" }, label));
      }
      svg.append(dates);
      const crosshair = s("g", { "data-crosshair": "", visibility: "hidden", "pointer-events": "none" });
      const vertical = s("line", { y1: top, y2: bottom, stroke: "#77869a", "stroke-dasharray": "3 3" }), horizontal = s("line", { x1: left, x2: width - right, stroke: "#77869a", "stroke-dasharray": "3 3" }), point = s("circle", { r: 3, fill: COLORS.price, stroke: "#fff", "stroke-width": 1.5 });
      crosshair.append(vertical, horizontal, point); svg.append(crosshair);
      drawing = { visible, comparison, x, y, left, chartWidth, width, main, crosshair, vertical, horizontal, point, interval: grouped.interval };
      if (state.inspection != null) inspect(Math.min(state.inspection, last));
      else updateReadout(last);
    }

    function updateReadout(index, announce = false) {
      const b = drawing.visible[index], { comparison } = drawing;
      fields.date.textContent = b.startDate && b.startDate !== b.date ? `${b.startDate} – ${b.date}` : b.date;
      for (const [name, key] of [["open", "o"], ["high", "h"], ["low", "l"], ["close", "c"]]) fields[name].textContent = price(b[key]);
      fields.volume.textContent = number(b.v, 0);
      fields.benchmark.textContent = comparison ? signed(comparison.benchmark[index]) : "Unavailable";
      readout.setAttribute("aria-label", `${drawing.interval} price bar${b.sessions ? ` covering ${b.sessions} observed sessions` : ""}`);
      if (announce) announcement.textContent = `${fields.date.textContent}. Open ${fields.open.textContent}; high ${fields.high.textContent}; low ${fields.low.textContent}; close ${fields.close.textContent}; volume ${fields.volume.textContent}${comparison ? `; SPY change ${fields.benchmark.textContent}` : ""}.`;
    }

    function inspect(index, announce = false) {
      if (!drawing || disposed) return;
      index = Math.max(0, Math.min(drawing.visible.length - 1, index)); state.inspection = index;
      const x = drawing.x(index), y = drawing.y(drawing.main[index]);
      drawing.vertical.setAttribute("x1", x); drawing.vertical.setAttribute("x2", x);
      drawing.horizontal.setAttribute("y1", y); drawing.horizontal.setAttribute("y2", y);
      drawing.point.setAttribute("cx", x); drawing.point.setAttribute("cy", y);
      drawing.crosshair.setAttribute("visibility", "visible"); updateReadout(index, announce);
    }

    function clearInspection() {
      if (!drawing || disposed) return;
      state.inspection = null; drawing.crosshair.setAttribute("visibility", "hidden"); updateReadout(drawing.visible.length - 1);
    }

    function pointer(event) {
      if (!drawing || disposed) return;
      const rect = svg.getBoundingClientRect(), local = (event.clientX - rect.left) * drawing.width / (rect.width || drawing.width);
      inspect(Math.floor((local - drawing.left) / drawing.chartWidth * drawing.visible.length));
    }
    on(svg, "pointermove", pointer); on(svg, "pointerdown", pointer);
    on(plot, "pointerleave", () => { if (doc.activeElement !== plot) clearInspection(); });
    on(plot, "blur", clearInspection);
    on(plot, "keydown", event => {
      if (!drawing || !["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown", "Escape"].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      if (event.key === "Escape") { clearInspection(); return; }
      const last = drawing.visible.length - 1, current = state.inspection ?? last;
      const next = event.key === "Home" ? 0 : event.key === "End" ? last : current + ({ ArrowLeft: -1, ArrowRight: 1, PageUp: -20, PageDown: 20 }[event.key] || 0);
      inspect(next, true);
    });
    draw();
    if (typeof win.ResizeObserver === "function") {
      observer = new win.ResizeObserver(() => { if (!disposed && Math.abs((container.getBoundingClientRect().width || container.clientWidth || 960) - width) > 1) draw(); });
      observer.observe(container);
    } else on(win, "resize", () => { if (!disposed) draw(); });
    return cleanup;
  }

  root.FU_COMPANY_CHART = Object.freeze({ render });
})(typeof window !== "undefined" ? window : globalThis);
