"""KPI insights: what a company's operating KPIs, its alternative data and the market are saying together.

Background loop over published files, run on a schedule and never on page load:
1. Detect (code, no model). Deterministic detectors read the IR KPI series (kpis/{T}.json, with document, page,
   sheet and cell locators for every value), the standardized KPIs (kpistd/t/{T}.json, kpistd/latest.json for peers),
   guidance outcomes (estimates/{T}.json), federal contract obligations, lobbying, FINRA short volume and SEC
   fails-to-deliver (altdata/{T}.json), congressional trades and hearings (gov/{T}.json), short interest
   (flows/{T}.json) and daily prices (prices/{T}.json). Each detector emits signals: a kind, a strength used only
   to order them, a plain reason, and facts with exact sources.
     trend      a KPI's latest quarterly change is outside its own usual range, or a multi-year high or low
     mix        a KPI's components moved apart (traffic vs ticket inside comparable sales, one segment's share)
     units      operating volume against reported revenue (implied price or mix), backlog against revenue
     margin     a segment margin moved by more than its usual quarterly swing
     peers      the latest value or its change sits at the top or bottom of the industry
     guidance   how recent guidance turned out, and the company's record
     contracts  federal obligations for the fiscal year against the prior year, next to backlog and revenue
     policy     lobbying spend change, Federal Register rules naming the company, laws it lobbied on
     trading    short volume share or fails-to-deliver well above their own recent range
     congress   trades by Members of Congress in the 30 days before a hearing that mentioned the company
     reaction   how the stock moved the day after each release, against whether the main KPI sped up or slowed
     analysts   price-target and rating actions in the last 30 days next to the KPI direction
2. Write (model). The strongest signals' facts go to one model, which writes 3 to 6 insights (title and text), each
   citing fact ids. Every number must appear in a cited fact (verified in code, etl/gov_insights.verify); a second
   model with different instructions must support each insight; insights whose facts were all used in the last 30
   days are dropped. If no model key is set or the check fails, the signals are still published, marked as such.
3. Publish kpi_insights/{T}.json (insights, signals with sources, models, run id) and kpi_insights/index.json (one
   row per company, ordered by signal strength, with the reason for the order).
"""
import datetime as dt
import hashlib
import json
import math
import os
import re
import statistics
import time
import uuid

from .common import now_iso

STATE = "meta/kpi_insights_state.json"
INDEX = "kpi_insights/index.json"
WINDOW = 30
MAX_INSIGHTS = 6
MAX_SIGNALS_TO_MODEL = 14
LEVEL_UNITS = ("USD", "count", "units", "number")

# ---------------------------------------------------------------- formatting


def _q(label, end):
    return f"{label} (period ended {end})" if label else f"the period ended {end}"


def fmt_val(v, unit):
    """Value as printed in facts: percentages to one decimal, money compact, counts with separators."""
    if v is None:
        return "n/a"
    u = (unit or "").lower()
    if u in ("%", "pct", "percent"):
        return f"{v:.1f}%"
    if u.startswith("usd_per") or u in ("usd_per_share",):
        return f"${v:,.2f}"
    if u == "usd" or u.startswith("usd"):
        a = abs(v)
        if a >= 1e9:
            return f"${v / 1e9:,.2f}B"
        if a >= 1e6:
            return f"${v / 1e6:,.1f}M"
        if a >= 1e3:
            return f"${v / 1e3:,.1f}K"
        return f"${v:,.0f}"
    if abs(v) >= 100:
        return f"{v:,.0f}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def pp(v, unit="%"):
    """Change of a rate: percentage points for percentages, a plain difference for ratios and multiples."""
    u = (unit or "%").lower()
    if u in ("%", "pct", "percent"):
        return f"{v:+.1f} pp"
    if u == "bps":
        return f"{v:+.0f} bps"
    return f"{v:+.2f}"


LOW_VALUE = re.compile(r"capex|capital expenditure|dividend|unallocated|interest expense|income tax|other revenue|other income|"
                       r"corporate and other|eliminations|share repurchase|depreciation|amortization|stock-based|restructuring", re.I)
NOT_ADDITIVE = re.compile(r"income|profit|margin|ebit|earnings|loss|reconciliation|eps|per share|cash flow|tax|expense|cost", re.I)
FRESH_DAYS = 200


def pct(v):
    return f"{v * 100:+.1f}%"


def _money(v):
    return fmt_val(v, "USD")


def is_rate(unit):
    return (unit or "").lower() in ("%", "pct", "percent", "bps", "x", "ratio")


def is_level(unit):
    u = (unit or "").lower()
    return u.startswith("usd") and not u.startswith("usd_per") or u in ("count", "units", "number", "boe", "mwh", "sqft", "tons", "barrels")


# ---------------------------------------------------------------- series helpers


def series(item, freq="Q"):
    """[(end, value, source)] ascending; source carries the document, page, sheet, cell and quote locators."""
    out = []
    for end, v in ((item.get("values") or {}).get(freq) or {}).items():
        val = v.get("v")
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(val):
            continue
        src = {k: v.get(k) for k in ("doc", "page", "sheet", "cell", "quote", "url", "doc_title", "label", "path") if v.get(k) is not None}
        out.append((end, float(val), src))
    out.sort(key=lambda r: r[0])
    return out


def _d(s):
    return dt.date.fromisoformat(s[:10])


def year_ago(rows, i, tol=25):
    """Index of the row about a year before rows[i] (fiscal quarters drift a few days)."""
    target = _d(rows[i][0]) - dt.timedelta(days=364)
    best, gap = None, None
    for j in range(i):
        g = abs((_d(rows[j][0]) - target).days)
        if g <= tol and (gap is None or g < gap):
            best, gap = j, g
    return best


def prev_q(rows, i, lo=60, hi=120):
    """Index of the row one quarter before rows[i], or None when quarters are missing."""
    if i == 0:
        return None
    g = (_d(rows[i][0]) - _d(rows[i - 1][0])).days
    return i - 1 if lo <= g <= hi else None


def yoy_series(rows):
    """[(end, growth, i, j)] for level KPIs."""
    out = []
    for i in range(len(rows)):
        j = year_ago(rows, i)
        if j is not None and rows[j][1] and rows[j][1] > 0 and rows[i][1] >= 0:
            out.append((rows[i][0], rows[i][1] / rows[j][1] - 1, i, j))
    return out


def robust_sd(xs):
    if len(xs) < 4:
        return None
    med = statistics.median(xs)
    mad = statistics.median(abs(x - med) for x in xs)
    sd = mad * 1.4826
    if sd <= 1e-12:
        sd = statistics.pstdev(xs)
    return sd if sd > 1e-12 else None


def src_ref(src):
    """Compact, stable source reference for the app."""
    return {k: src.get(k) for k in ("doc", "page", "sheet", "cell", "quote", "url", "doc_title", "label", "path") if src.get(k) is not None}


class Facts:
    """Fact registry: id -> (text, sources); ids are short and stable within a run."""

    def __init__(self):
        self.text, self.sources, self.series = {}, {}, {}

    def add(self, fid, text, sources=None, chart=None):
        self.text[fid] = text
        self.sources[fid] = [s for s in (sources or []) if s]
        if chart:
            self.series[fid] = chart
        return fid


def _sig(kind, strength, reason, facts, item=None, chart=None):
    return {"kind": kind, "strength": round(float(strength), 3), "reason": reason, "facts": facts, "kpi": item, "chart": chart}


def _chart(rows, unit, name, comp=None, comp_name=None, comp_unit=None):
    """Small series for a sparkline: the KPI and optionally a comparator, last 12 points."""
    c = {"name": name, "unit": unit, "points": [[e, round(v, 6)] for e, v, _ in rows[-12:]]}
    if comp:
        c["comp"] = {"name": comp_name, "unit": comp_unit, "points": [[e, round(v, 6)] for e, v in comp[-12:]]}
    return c


# ---------------------------------------------------------------- KPI detectors


def kpi_items(kp, max_depth=2):
    """Operating KPI items worth reading: depth <= 2, at least 6 quarterly points, and current (latest point within
    45 days of the company's latest reported quarter, so a series that stopped being reported is not read as news)."""
    out = []
    for g in kp.get("groups") or []:
        workbook = (g.get("category") or "").startswith("Workbook:")
        for it in g.get("items") or []:
            if (it.get("depth") or 0) > max_depth:
                continue
            rows = series(it)
            if len(rows) < 6:
                continue
            out.append((g.get("category") or "", workbook, it, rows))
    if not out:
        return out
    latest = max(_d(rows[-1][0]) for _, _, _, rows in out)
    return [x for x in out if (latest - _d(x[3][-1][0])).days <= 45]


def _weight(it, workbook):
    w = 1.0 if (it.get("depth") or 0) == 0 else 0.8 if it.get("depth") == 1 else 0.6
    if workbook:
        w *= 0.6
    if LOW_VALUE.search(it.get("name") or ""):
        w *= 0.4
    return w


def detect_trend(F, tk, items):
    """Latest quarterly change against the KPI's own history of quarterly changes; multi-year highs and lows."""
    sigs = []
    for cat, workbook, it, rows in items:
        unit, name, iid = it.get("unit"), it.get("name") or it.get("id"), it.get("id")
        weight = _weight(it, workbook)
        last = rows[-1]
        if _d(last[0]) < dt.date.today() - dt.timedelta(days=200):
            continue  # stale series
        if is_rate(unit):
            diffs = []
            for i in range(1, len(rows)):
                if prev_q(rows, i) is not None:
                    diffs.append((rows[i][0], rows[i][1] - rows[i - 1][1], i))
            if len(diffs) < 6 or diffs[-1][2] != len(rows) - 1:
                continue
            hist = [d for _, d, _ in diffs[-13:-1]]
            sd = robust_sd(hist)
            d_last = diffs[-1][1]
            if sd is None:
                continue
            z = d_last / sd
            prev = rows[-2]
            ya = year_ago(rows, len(rows) - 1)
            n_hist = len(rows[-13:-1])
            window = rows[-13:]
            top = max(window, key=lambda r: r[1])[0] == last[0]
            bottom = min(window, key=lambda r: r[1])[0] == last[0]
            small = 0.5 if (unit or "").lower() in ("%", "pct", "percent") else 0.05 * max(abs(prev[1]), 1e-9)
            if abs(z) >= 2.0 and abs(d_last) >= small:
                fid = F.add(f"k:{iid}:chg", f"{name} was {fmt_val(last[1], unit)} in {_q(last[2].get('label'), last[0])}, against "
                            f"{fmt_val(prev[1], unit)} the quarter before ({pp(d_last, unit)}); its usual quarterly move over the prior "
                            f"{len(hist)} quarters was about {pp(sd, unit).lstrip('+')}." + (f" A year earlier it was {fmt_val(rows[ya][1], unit)}." if ya is not None else ""),
                            [src_ref(last[2]), src_ref(prev[2])] + ([src_ref(rows[ya][2])] if ya is not None else []))
                sigs.append(_sig("trend", min(abs(z), 6) * weight, f"{name}: {pp(d_last, unit)} in a quarter, {abs(z):.1f}x its usual move", [fid],
                                 iid, _chart(rows, unit, name)))
            elif (top or bottom) and n_hist >= 8:
                fid = F.add(f"k:{iid}:ext", f"{name} was {fmt_val(last[1], unit)} in {_q(last[2].get('label'), last[0])}, the "
                            f"{'highest' if top else 'lowest'} of the last {len(window)} quarters reported.", [src_ref(last[2])])
                sigs.append(_sig("trend", 2.0 * weight, f"{name}: {'highest' if top else 'lowest'} in {len(window)} quarters", [fid], iid,
                                 _chart(rows, unit, name)))
        elif is_level(unit):
            ys = yoy_series(rows)
            if len(ys) < 5 or ys[-1][2] != len(rows) - 1:
                continue
            accel = [(ys[k][0], ys[k][1] - ys[k - 1][1]) for k in range(1, len(ys)) if prev_q(rows, ys[k][2]) is not None and ys[k - 1][2] == ys[k][2] - 1]
            if len(accel) < 4 or accel[-1][0] != ys[-1][0]:
                continue
            sd = robust_sd([a for _, a in accel[-13:-1]])
            if sd is None:
                continue
            a_last = accel[-1][1]
            z = a_last / sd
            g_now, g_prev = ys[-1][1], ys[-2][1]
            i, j = ys[-1][2], ys[-1][3]
            med = statistics.median([v for _, v, _ in rows[-9:-1]]) if len(rows) >= 9 else None
            base_ok = abs(g_now) <= 1.0 and abs(g_prev) <= 1.0 and (med is None or rows[j][1] >= 0.4 * med)
            if not base_ok:
                continue
            if abs(z) >= 2.0 and abs(a_last) >= 0.03:
                fid = F.add(f"k:{iid}:yoy", f"{name} was {fmt_val(rows[i][1], unit)} in {_q(rows[i][2].get('label'), rows[i][0])}, "
                            f"{pct(g_now)} year over year (from {fmt_val(rows[j][1], unit)}); the quarter before it grew {pct(g_prev)}. "
                            f"The change in growth, {pp(a_last * 100)}, is {abs(z):.1f} times its usual quarter-to-quarter swing.",
                            [src_ref(rows[i][2]), src_ref(rows[j][2])])
                sigs.append(_sig("trend", min(abs(z), 6) * weight, f"{name}: growth {'sped up' if a_last > 0 else 'slowed'} by {abs(a_last) * 100:.1f} pp",
                                 [fid], iid, _chart(rows, unit, name)))
            else:
                # streak of acceleration or deceleration
                run = 0
                for _, a in reversed(accel):
                    if a > 0 and run >= 0:
                        run += 1
                    elif a < 0 and run <= 0:
                        run -= 1
                    else:
                        break
                if abs(run) >= 4 and (it.get("depth") or 0) <= 1:
                    fid = F.add(f"k:{iid}:streak", f"{name} growth {'rose' if run > 0 else 'fell'} for {abs(run)} quarters in a row, to "
                                f"{pct(g_now)} year over year in {_q(rows[i][2].get('label'), rows[i][0])}.", [src_ref(rows[i][2]), src_ref(rows[j][2])])
                    sigs.append(_sig("trend", 1.5 + 0.2 * abs(run), f"{name}: {abs(run)} straight quarters of {'faster' if run > 0 else 'slower'} growth",
                                     [fid], iid, _chart(rows, unit, name)))
    return sigs


def _children(items):
    by_parent = {}
    ids = {it["id"]: (cat, wb, it, rows) for cat, wb, it, rows in items}
    for cat, wb, it, rows in items:
        p = it.get("parent")
        if p and p in ids:
            by_parent.setdefault(p, []).append((cat, wb, it, rows))
    return ids, by_parent


def detect_mix(F, tk, items):
    """Rate KPIs split into rate components (comparable sales = traffic + ticket); level KPIs split into segments."""
    sigs = []
    ids, kids = _children(items)
    for pid, ch in kids.items():
        _, pwb, parent, prow = ids[pid]
        if len(ch) < 2 or len(prow) < 2:
            continue
        pend = prow[-1][0]
        if _d(pend) < dt.date.today() - dt.timedelta(days=200):
            continue
        if is_rate(parent.get("unit")) and all(is_rate(c[2].get("unit")) for c in ch):
            # each component's latest and prior-quarter value at the parent's latest period
            parts = []
            for _, _, it, rows in ch:
                m = {e: (v, s) for e, v, s in rows}
                if pend in m and len(rows) >= 2:
                    k = [e for e, _, _ in rows].index(pend)
                    if k >= 1 and prev_q(rows, k) is not None:
                        parts.append((it, m[pend], rows[k - 1]))
            if len(parts) < 2 or prev_q(prow, len(prow) - 1) is None:
                continue
            moves = [(it, now[0] - prev[1]) for it, now, prev in parts]
            spread = max(m for _, m in moves) - min(m for _, m in moves)
            opposite = any(m > 0.4 for _, m in moves) and any(m < -0.4 for _, m in moves)
            if spread < 1.5 and not opposite:
                continue
            text = (f"{parent['name']} was {fmt_val(prow[-1][1], parent.get('unit'))} in {_q(prow[-1][2].get('label'), pend)} against "
                    f"{fmt_val(prow[-2][1], parent.get('unit'))} the quarter before. Components: " +
                    "; ".join(f"{it['name']} {fmt_val(now[0], it.get('unit'))} (prior quarter {fmt_val(prev[1], it.get('unit'))})" for it, now, prev in parts) + ".")
            srcs = [src_ref(prow[-1][2])] + [src_ref(now[1]) for _, now, _ in parts] + [src_ref(prev[2]) for _, _, prev in parts]
            fid = F.add(f"m:{pid}", text, srcs)
            lead = max(moves, key=lambda x: x[1])
            lag = min(moves, key=lambda x: x[1])
            sigs.append(_sig("mix", 2.0 + min(spread, 6) * 0.5, f"{parent['name']}: {lead[0]['name']} {pp(lead[1], lead[0].get('unit'))} while {lag[0]['name']} {pp(lag[1], lag[0].get('unit'))}",
                             [fid], pid, {"name": parent["name"], "unit": parent.get("unit"),
                                          "points": [[e, round(v, 4)] for e, v, _ in prow[-12:]],
                                          "comp": {"name": lag[0]["name"], "unit": lag[0].get("unit"),
                                                   "points": [[e, round(v, 4)] for e, v, _ in next(r for _, _, it, r in ch if it is lag[0])[-12:]]}}))
        elif is_level(parent.get("unit")) and parent.get("unit") == "USD" and not NOT_ADDITIVE.search(parent.get("name") or ""):
            # segment share of the parent, latest vs a year earlier
            pi = len(prow) - 1
            pj = year_ago(prow, pi)
            if pj is None or not prow[pi][1] or not prow[pj][1]:
                continue
            best = None
            if abs(prow[pi][1] / prow[pj][1] - 1) > 0.5:
                continue  # total moved too much for a share comparison to say anything
            for _, _, it, rows in ch:
                if it.get("unit") != "USD" or NOT_ADDITIVE.search(it.get("name") or ""):
                    continue
                m = {e: (v, s) for e, v, s in rows}
                if prow[pi][0] not in m or prow[pj][0] not in m:
                    continue
                s_now, s_then = m[prow[pi][0]][0] / prow[pi][1], m[prow[pj][0]][0] / prow[pj][1]
                if not (0 < s_now <= 1.05 and 0 < s_then <= 1.05):
                    continue
                g_seg = m[prow[pi][0]][0] / m[prow[pj][0]][0] - 1 if m[prow[pj][0]][0] > 0 else None
                g_par = prow[pi][1] / prow[pj][1] - 1
                shift = s_now - s_then
                if abs(shift) >= 0.025 and s_now >= 0.08 and g_seg is not None:
                    if best is None or abs(shift) > abs(best[1]):
                        best = (it, shift, s_now, s_then, g_seg, g_par, m[prow[pi][0]][1], m[prow[pj][0]][1])
            if best:
                it, shift, s_now, s_then, g_seg, g_par, sn, st = best
                fid = F.add(f"m:{pid}:{it['id']}", f"{it['name']} was {s_now * 100:.1f}% of {parent['name']} in "
                            f"{_q(prow[pi][2].get('label'), prow[pi][0])}, against {s_then * 100:.1f}% a year earlier; it grew {pct(g_seg)} "
                            f"while the total grew {pct(g_par)}.", [src_ref(sn), src_ref(st), src_ref(prow[pi][2]), src_ref(prow[pj][2])])
                sigs.append(_sig("mix", (1.5 + min(abs(shift), 0.15) * 30) * _weight(it, pwb), f"{it['name']}: share of {parent['name']} {pp(shift * 100)} in a year", [fid], it["id"],
                                 _chart(next(r for _, _, i2, r in ch if i2 is it), "USD", it["name"])))
    return sigs


UNIT_WORDS = re.compile(r"deliver|shipment|units? sold|subscriber|customers?|members|paid|households|accounts|policies|orders|rooms|"
                        r"passengers|enplanements|vehicles|volume|production|boe|barrels|tons|loads|transactions|visits|users|dau|mau", re.I)
BACKLOG = re.compile(r"backlog|remaining performance obligation|\brpo\b|book(?:ings)?\b|orders? received|net orders", re.I)
REVENUE = re.compile(r"^(total )?(net )?(revenues?|sales|net sales)( total)?$|total net revenues?|total revenues?|total net sales|^revenue$", re.I)


def _total_revenue(items, std):
    """Quarterly total revenue rows from the IR KPIs (depth 0 revenue item), else the standardized series."""
    for _, _, it, rows in items:
        if it.get("unit") == "USD" and (it.get("depth") or 0) == 0 and (REVENUE.search(it.get("name") or "") or it.get("id") in ("revenue_total", "total_revenue", "net_revenue_total")):
            return it, rows
    s = ((std or {}).get("series") or {}).get("total_revenue")
    if s and s.get("Q"):
        rows = []
        for r in s["Q"]:
            if isinstance(r[1], (int, float)):
                rows.append((r[0], float(r[1]) * (s.get("factor") or 1), {"doc": r[3], "label": r[2], "quote": r[5]}))
        if rows:
            return {"id": "total_revenue", "name": "Total revenue", "unit": "USD"}, rows
    return None, None


def detect_units(F, tk, items, std):
    """Operating volume and backlog against revenue growth."""
    sigs = []
    rit, rrows = _total_revenue(items, std)
    if not rit:
        return sigs
    rm = {e: (v, s) for e, v, s in rrows}
    for cat, wb, it, rows in items:
        if wb or (it.get("depth") or 0) > 1 or it is rit:
            continue
        name = it.get("name") or ""
        unit = it.get("unit")
        backlog = BACKLOG.search(name) and unit == "USD"
        volume = UNIT_WORDS.search(name) and is_level(unit) and unit != "USD"
        if not (backlog or volume):
            continue
        i = len(rows) - 1
        if _d(rows[i][0]) < dt.date.today() - dt.timedelta(days=FRESH_DAYS):
            continue
        j = year_ago(rows, i)
        if j is None or rows[j][1] <= 0 or rows[i][0] not in rm:
            continue
        ri = [e for e, _, _ in rrows].index(rows[i][0])
        rj = year_ago(rrows, ri)
        if rj is None or rrows[rj][1] <= 0:
            continue
        g_k = rows[i][1] / rows[j][1] - 1
        g_r = rrows[ri][1] / rrows[rj][1] - 1
        gap = g_r - g_k
        if volume and (g_k <= -0.99 or rows[i][1] < 20):
            continue  # tiny counts (a handful of deliveries) swing too much to compare
        if abs(gap) < (0.08 if volume else 0.10):
            continue
        if volume:
            per_unit = (1 + g_r) / (1 + g_k) - 1 if g_k > -1 else None
            if per_unit is None:
                continue
            text = (f"{name} was {fmt_val(rows[i][1], unit)} in {_q(rows[i][2].get('label'), rows[i][0])}, {pct(g_k)} year over year, while "
                    f"{rit['name']} grew {pct(g_r)} to {_money(rrows[ri][1])}; revenue per unit of this measure changed by about {pct(per_unit)}.")
            reason = f"{name} {pct(g_k)} vs revenue {pct(g_r)}"
        else:
            text = (f"{name} was {_money(rows[i][1])} at {_q(rows[i][2].get('label'), rows[i][0])}, {pct(g_k)} year over year, while quarterly "
                    f"{rit['name']} grew {pct(g_r)} to {_money(rrows[ri][1])}.")
            reason = f"{name} {pct(g_k)} vs revenue {pct(g_r)}"
        fid = F.add(f"u:{it['id']}", text, [src_ref(rows[i][2]), src_ref(rows[j][2]), src_ref(rrows[ri][2]), src_ref(rrows[rj][2])])
        comp = [(e, v) for e, v, _ in rrows]
        sigs.append(_sig("units", 2.0 + min(abs(gap), 0.6) * 6, reason, [fid], it["id"],
                         _chart(rows, unit, name, comp, rit["name"], "USD")))
    return sigs


MARGIN = re.compile(r"margin", re.I)


def detect_margin(F, tk, items):
    sigs = []
    for cat, wb, it, rows in items:
        if wb or not MARGIN.search(it.get("name") or "") or not is_rate(it.get("unit")):
            continue
        i = len(rows) - 1
        j = year_ago(rows, i)
        if j is None or _d(rows[i][0]) < dt.date.today() - dt.timedelta(days=200):
            continue
        yo = [rows[k][1] - rows[year_ago(rows, k)][1] for k in range(len(rows)) if year_ago(rows, k) is not None]
        if len(yo) < 5:
            continue
        sd = robust_sd(yo[:-1][-12:])
        d = rows[i][1] - rows[j][1]
        if sd is None or abs(d) < 1.5 or abs(d / sd) < 2:
            continue
        fid = F.add(f"g:{it['id']}", f"{it['name']} was {fmt_val(rows[i][1], it.get('unit'))} in {_q(rows[i][2].get('label'), rows[i][0])}, against "
                    f"{fmt_val(rows[j][1], it.get('unit'))} a year earlier ({pp(d)}); its typical year-over-year move is about {sd:.1f} pp.",
                    [src_ref(rows[i][2]), src_ref(rows[j][2])])
        sigs.append(_sig("margin", min(abs(d / sd), 6) * 0.9, f"{it['name']} {pp(d)} year over year", [fid], it["id"], _chart(rows, "%", it["name"])))
    return sigs


def detect_peers(F, tk, latest, std):
    """Latest standardized KPI value and its year-over-year change against the same KPI in the industry."""
    sigs = []
    names = {m.get("canonical"): m.get("name") for m in (std or {}).get("map") or [] if m.get("canonical")}
    for cid, rows in (latest or {}).items():
        me = rows.get(tk)
        if not me or not me.get("q"):
            continue
        end, val, label, yoy = (me["q"] + [None] * 4)[:4]
        if not isinstance(val, (int, float)):
            continue
        if _d(end) < dt.date.today() - dt.timedelta(days=200):
            continue
        peers = {t: r for t, r in rows.items() if t != tk and r.get("q") and r.get("ind") == me.get("ind") and isinstance(r["q"][1], (int, float))
                 and _d(r["q"][0]) >= dt.date.today() - dt.timedelta(days=220)}
        scope = me.get("ind")
        if len(peers) < 6:
            peers = {t: r for t, r in rows.items() if t != tk and r.get("q") and r.get("sec") == me.get("sec") and isinstance(r["q"][1], (int, float))
                     and _d(r["q"][0]) >= dt.date.today() - dt.timedelta(days=220)}
            scope = me.get("sec")
        if len(peers) < 6:
            continue
        rate = any(w in cid for w in ("growth", "margin", "ratio", "rate", "yield", "nim", "retention", "occupancy", "load", "return", "rotce", "cet1"))
        if rate:
            pick = lambda r: r["q"][1]
            mine, what = val, "was"
        else:
            if not isinstance(yoy, (int, float)):
                continue
            pick = lambda r: r["q"][3] if len(r["q"]) > 3 and isinstance(r["q"][3], (int, float)) else None
            mine, what = yoy, "grew"
            peers = {t: r for t, r in peers.items() if pick(r) is not None}
            if len(peers) < 6:
                continue
        vals = sorted([pick(r) for r in peers.values()] + [mine])
        rank = vals.index(mine) + 1
        n = len(vals)
        if 1 < rank < n and 0.1 < rank / n < 0.9:
            continue
        best = sorted(peers.items(), key=lambda kv: -pick(kv[1]))
        show = (lambda v: fmt_val(v, "%")) if rate else (lambda v: pct(v))
        top3 = ", ".join(f"{t} {show(pick(r))}" for t, r in best[:3])
        nm = names.get(cid) or cid.replace("_", " ")
        body = f"was {fmt_val(val, '%')}" if rate else f"grew {pct(yoy)} year over year"
        fid = F.add(f"p:{cid}", f"{tk}'s {nm} {body} for {label} (period ended {end}), rank {n - rank + 1} of {n} companies in "
                    f"{scope} reporting it (highest: {top3}).", [{"doc_title": "Standardized KPI snapshot (kpistd/latest.json)", "label": label}])
        where = 'highest' if rank == n else 'lowest' if rank == 1 else 'top decile' if rank / n >= 0.9 else 'bottom decile'
        sigs.append(_sig("peers", 1.6, f"{nm}{' growth' if not rate else ''}: {where} of {n} in {scope}", [fid], cid))
    return sigs


def detect_guidance(F, tk, est):
    sigs = []
    g = (est or {}).get("guidance") or {}
    cred = g.get("credibility") or {}
    outs = sorted([o for o in g.get("outcomes") or [] if o.get("actual") is not None and o.get("period_end")], key=lambda o: o["period_end"])
    recent = [o for o in outs if _d(o["period_end"]) >= dt.date.today() - dt.timedelta(days=420)]
    if cred.get("n_scored", 0) >= 6:
        fid = F.add("gd:record", f"Of {cred['n_scored']} guided figures scored since {cred.get('first_issued')}, {cred.get('n_met')} were met or beaten; "
                    f"{round((cred.get('below_rate') or 0) * 100)}% came in below the final range and {round((cred.get('above_rate') or 0) * 100)}% above it.",
                    [{"doc_title": "Guidance record (estimates)", "label": "guidance"}])
        strength = 1.2 + abs((cred.get("above_rate") or 0) - (cred.get("below_rate") or 0)) * 2
        sigs.append(_sig("guidance", strength, f"guidance record: {cred.get('n_met')} of {cred['n_scored']} met", [fid]))
    for o in recent[-3:]:
        lo, hi, act = o.get("final_low"), o.get("final_high"), o["actual"]
        unit = "%" if o.get("unit") == "pct" else ("USD_per_share" if "share" in (o.get("unit") or "") else o.get("unit"))
        rng = f"{fmt_val(lo, unit)} to {fmt_val(hi, unit)}" if lo is not None and hi is not None and lo != hi else fmt_val(o.get("final_mid"), unit)
        revs = f", after {o['revisions']} revisions" if o.get("revisions") else ""
        fid = F.add(f"gd:{o.get('final_id') or o['period']}", f"{o.get('label')} ({o.get('basis')}) for {o['period']}: final guidance {rng}, actual "
                    f"{fmt_val(act, unit)} ({o.get('result')} the range){revs}.",
                    [{"doc_title": "Guidance outcome (estimates)", "label": o["period"]}])
        sigs.append(_sig("guidance", 1.0 if o.get("result") == "in" else 1.6, f"{o.get('label')} {o['period']}: {o.get('result')} guidance", [fid]))
    items = [i for i in g.get("items") or [] if (i.get("issued") or "") >= (dt.date.today() - dt.timedelta(days=100)).isoformat()
             and i.get("action") in ("raised", "lowered", "raise", "lower")]
    for i in items[:2]:
        unit = "%" if i.get("unit") == "pct" else i.get("unit")
        lo, hi = i.get("low"), i.get("high")
        rng = f"{fmt_val(lo, unit)} to {fmt_val(hi, unit)}" if lo is not None and hi is not None and lo != hi else fmt_val(lo if lo is not None else hi, unit)
        fid = F.add(f"gi:{i['id']}", f"Guidance issued {i['issued']}: {i.get('label')} ({i.get('basis')}) for {i.get('period')} {i['action']} to {rng}.",
                    [{"doc": i.get("doc"), "page": i.get("page"), "quote": i.get("quote")}])
        sigs.append(_sig("guidance", 1.8, f"{i.get('label')} guidance {i['action']}", [fid]))
    return sigs


# ---------------------------------------------------------------- alternative data detectors


def detect_contracts(F, tk, alt, items):
    sigs = []
    c = (alt or {}).get("contracts") or {}
    rows = {r["fy"]: r for r in c.get("rows") or [] if isinstance(r.get("amount"), (int, float))}
    full = sorted(fy for fy, r in rows.items() if not r.get("partial"))
    if len(full) < 2:
        return sigs
    fy, prev = full[-1], full[-2]
    a, b = rows[fy]["amount"], rows[prev]["amount"]
    if b < 2e8 or a <= 0:
        return sigs
    g = a / b - 1
    if abs(g) < 0.25:
        return sigs
    hist = [rows[y]["amount"] for y in full[-6:-1]]
    url = ((alt or {}).get("contracts_source") or {}).get("url") or "https://www.usaspending.gov/search"
    text = (f"Federal prime contract obligations to {tk} were {_money(a)} in fiscal {fy} (October to September), against {_money(b)} in fiscal {prev} "
            f"({pct(g)}); the prior five fiscal years ranged from {_money(min(hist))} to {_money(max(hist))}.")
    facts = [F.add("c:fy", text, [{"doc_title": "USAspending.gov prime contract obligations", "url": url}])]
    for _, wb, it, krows in items:
        if wb or not BACKLOG.search(it.get("name") or "") or it.get("unit") != "USD" or (it.get("depth") or 0) > 0:
            continue
        i = len(krows) - 1
        j = year_ago(krows, i)
        if j is None or krows[j][1] <= 0:
            continue
        facts.append(F.add(f"c:{it['id']}", f"{it['name']} was {_money(krows[i][1])} at {_q(krows[i][2].get('label'), krows[i][0])}, "
                           f"{pct(krows[i][1] / krows[j][1] - 1)} year over year.", [src_ref(krows[i][2]), src_ref(krows[j][2])]))
        break
    top = sorted([w for w in c.get("awards") or [] if isinstance(w.get("amount"), (int, float)) and (w.get("start") or "") >= f"{fy - 1}-10-01"],
                 key=lambda w: -w["amount"])[:2]
    for k, w in enumerate(top):
        facts.append(F.add(f"c:aw{k}", f"Award {w['id']} from {w.get('agency')}: {_money(w['amount'])} obligated, starting {w.get('start')}.",
                           [{"doc_title": "USAspending.gov award", "url": w.get("url")}]))
    sigs.append(_sig("contracts", 2.0 + min(abs(g), 1.5) * 2, f"federal obligations {pct(g)} in fiscal {fy}", facts, None,
                     {"name": "Federal obligations by fiscal year", "unit": "USD", "points": [[f"{y}-09-30", rows[y]["amount"]] for y in full[-8:]]}))
    return sigs


def detect_policy(F, tk, alt, gov):
    sigs = []
    lob = [r for r in ((alt or {}).get("lobbying") or {}).get("rows") or [] if isinstance(r.get("amount"), (int, float))]
    lob.sort(key=lambda r: (r["year"], r["quarter"]))
    if len(lob) >= 8:
        last4, prev4 = lob[-4:], lob[-8:-4]
        a, b = sum(r["amount"] for r in last4), sum(r["amount"] for r in prev4)
        if b >= 4e5 and abs(a / b - 1) >= 0.4:
            q0, q1 = f"Q{last4[0]['quarter']} {last4[0]['year']}", f"Q{last4[-1]['quarter']} {last4[-1]['year']}"
            facts = [F.add("l:spend", f"Reported federal lobbying spend was {_money(a)} in the four quarters {q0} to {q1}, against {_money(b)} in the four quarters "
                           f"before ({pct(a / b - 1)}).", [{"doc_title": "Senate LDA filings", "url": ((alt or {}).get("lobbying_source") or {}).get("url")}])]
            rules = [r for r in ((gov or {}).get("regulation") or {}).get("rules") or [] if (r.get("date") or "") >= (dt.date.today() - dt.timedelta(days=365)).isoformat()]
            if rules:
                kinds = {}
                for r in rules:
                    kinds[r.get("type") or "Document"] = kinds.get(r.get("type") or "Document", 0) + 1
                facts.append(F.add("l:rules", f"The Federal Register named {tk} in {len(rules)} documents in the last 12 months (" +
                                   ", ".join(f"{n} {k.lower()}{'s' if n > 1 else ''}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])) + ").",
                                   [{"doc_title": "Federal Register", "url": "https://www.federalregister.gov"}]))
            laws = [x for x in (gov or {}).get("legislation") or [] if (x.get("date") or "") >= (dt.date.today() - dt.timedelta(days=365)).isoformat()]
            if laws:
                facts.append(F.add("l:laws", f"{len(laws)} laws enacted in the last 12 months were among the bills {tk} reported lobbying on, including "
                                   f"{laws[0].get('label')} ({(laws[0].get('short_titles') or [laws[0].get('title') or ''])[0][:90]}).",
                                   [{"doc_title": laws[0].get("label"), "url": laws[0].get("url")}]))
            sigs.append(_sig("policy", 1.5 + min(abs(a / b - 1), 2), f"lobbying spend {pct(a / b - 1)} year over year", facts, None,
                             {"name": "Lobbying spend by quarter", "unit": "USD", "points": [[f"{r['year']}-{r['quarter'] * 3:02d}-28", r["amount"]] for r in lob[-12:]]}))
    return sigs


def _bars(px):
    out = []
    for b in (px or {}).get("d") or []:
        if len(b) >= 5 and isinstance(b[4], (int, float)):
            out.append((dt.datetime.fromtimestamp(b[0], dt.timezone.utc).date().isoformat(), float(b[4])))
    return out


MARKET_SAMPLE = ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "JPM", "XOM", "JNJ", "PG", "V", "HD", "KO", "PEP", "WMT", "CVX", "MRK", "ABBV",
                 "CSCO", "ORCL", "INTC", "T", "VZ", "DIS", "NFLX", "ADBE", "CRM", "BAC", "WFC", "C", "MCD", "NKE", "COST", "TSLA", "AMD", "QCOM"]


def market_ftd_dates(sb):
    """Settlement dates where fails to deliver spiked across a sample of large stocks at once (a market-wide event,
    not a company signal): a date counts when at least 40% of the sample peaked within 3 days of it."""
    peaks = []
    cut = (dt.date.today() - dt.timedelta(days=75)).isoformat()
    for t in MARKET_SAMPLE:
        a = _safe(sb, f"altdata/{t}.json") or {}
        rows = [(r[0], r[1] * (r[2] or 0)) for r in ((a.get("ftd") or {}).get("rows") or []) if isinstance(r[1], (int, float)) and r[0] >= cut]
        if rows:
            peaks.append(_d(max(rows, key=lambda r: r[1])[0]))
    out = set()
    for p in peaks:
        near = sum(1 for q in peaks if abs((q - p).days) <= 3)
        if peaks and near >= 0.4 * len(peaks):
            for k in range(-3, 4):
                out.add((p + dt.timedelta(days=k)).isoformat())
    return out


MARKET_FTD = set()


def detect_trading(F, tk, alt, px):
    sigs = []
    sv = ((alt or {}).get("short_volume") or {}).get("rows") or []
    if len(sv) >= 80:
        ratio = [(r[0], r[1] / r[3]) for r in sv if r[3]]
        last10 = [x for _, x in ratio[-10:]]
        base = [x for _, x in ratio[-90:-10]]
        sd = robust_sd(base)
        if sd and len(last10) == 10:
            m10, mb = statistics.mean(last10), statistics.median(base)
            z = (m10 - mb) / sd
            if abs(z) >= 2.5 and abs(m10 - mb) >= 0.08:
                bars = dict(_bars(px))
                d0, d1 = ratio[-11][0], ratio[-1][0]
                move = (bars[d1] / bars[d0] - 1) if d0 in bars and d1 in bars and bars[d0] else None
                fid = F.add("t:sv", f"Short sales were {m10 * 100:.1f}% of off-exchange volume reported to FINRA over the 10 sessions to {d1}, against a median of "
                            f"{mb * 100:.1f}% over the prior {len(base)} sessions." + (f" The share price moved {pct(move)} from {d0} to {d1}." if move is not None else ""),
                            [{"doc_title": "FINRA Reg SHO daily short sale volume", "url": ((alt or {}).get("short_volume") or {}).get("url")}])
                sigs.append(_sig("trading", min(abs(z), 6) * 0.5, f"short-sale share of volume {pp((m10 - mb) * 100)} vs its median", [fid], None,
                                 {"name": "Short share of off-exchange volume", "unit": "%", "points": [[d, round(x * 100, 2)] for d, x in ratio[-60:]]}))
    ftd = ((alt or {}).get("ftd") or {}).get("rows") or []
    if len(ftd) >= 30:
        vals = [(r[0], r[1] * (r[2] or 0)) for r in ftd if isinstance(r[1], (int, float))]
        recent = [v for d, v in vals if d >= (dt.date.today() - dt.timedelta(days=75)).isoformat()]
        base = [v for d, v in vals if d < (dt.date.today() - dt.timedelta(days=75)).isoformat()][-120:]
        if recent and len(base) >= 20:
            peak = max(recent)
            med = statistics.median(base)
            pd = next((d for d, v in vals if v == peak), None)
            if med > 0 and peak >= 5 * med and peak >= 5e6 and pd not in MARKET_FTD:
                fid = F.add("t:ftd", f"Fails to deliver reached {_money(peak)} (shares times price) on settlement date {pd}, against a median of {_money(med)} per "
                            f"reported day over the prior {len(base)} reported days.", [{"doc_title": "SEC fails-to-deliver data", "url": ((alt or {}).get("ftd") or {}).get("url")}])
                sigs.append(_sig("trading", 1.4 + min(peak / med, 40) / 20, f"fails to deliver {peak / med:.0f}x their median", [fid]))
    return sigs


def detect_congress(F, tk, gov):
    sigs = []
    trades = (gov or {}).get("congress_trades") or []
    hearings = [h for h in (gov or {}).get("hearings") or [] if h.get("date") and (h.get("mentions") or 0) >= 2]
    hits, seen = [], set()
    for h in hearings:
        hd = _d(h["date"])
        if hd < dt.date.today() - dt.timedelta(days=540):
            continue
        for t in trades:
            if not t.get("tx_date"):
                continue
            gap = (hd - _d(t["tx_date"])).days
            if 0 <= gap <= 30 and t.get("id") not in seen:
                seen.add(t.get("id"))
                hits.append((h, t, gap))
    if not hits:
        return sigs
    hits.sort(key=lambda x: x[0]["date"], reverse=True)
    facts = []
    for h, t, gap in hits[:3]:
        facts.append(F.add(f"cg:{t.get('id')}", f"{t.get('member')} ({t.get('party')}, {t.get('chamber')}) reported a {(t.get('type') or '').lower()} of {tk} on "
                           f"{t['tx_date']} ({t.get('amount') or t.get('range') or 'amount range not stated'}), {gap} days before the {h.get('chamber')} hearing "
                           f"\"{(h.get('title') or '')[:110]}\" on {h['date']}, which mentioned the company.",
                           [{"doc_title": "Congressional periodic transaction report", "url": t.get("url")},
                            {"doc_title": h.get("title"), "url": f"https://www.govinfo.gov/app/details/{h.get('id')}"}]))
    sigs.append(_sig("congress", 0.9 + 0.1 * min(len(hits), 5), f"{len(hits)} congressional trade(s) within 30 days before a hearing mentioning {tk}", facts))
    return sigs


def release_dates(std):
    """{period label: release date} from the KPI source documents (8-K exhibit 99 filing dates)."""
    out = {}
    for d in ((std or {}).get("docs") or {}).values():
        if not isinstance(d, list) or len(d) < 6:
            continue
        m = re.match(r"8-K (\d{4}-\d{2}-\d{2})", d[0] or "")
        if m and d[4] == "release" and d[5]:
            out.setdefault(d[5], m.group(1))
    return out


def detect_reaction(F, tk, items, std, px):
    """Next-day price move after each release against whether the main KPI's growth sped up or slowed."""
    sigs = []
    rel = release_dates(std)
    bars = _bars(px)
    if not rel or len(bars) < 200:
        return sigs
    days = [d for d, _ in bars]
    close = dict(bars)
    # main KPI: the depth-0 rate KPI with the longest history (comparable sales, organic growth), else revenue growth
    cand = [(it, rows) for _, wb, it, rows in items if not wb and (it.get("depth") or 0) == 0 and is_rate(it.get("unit"))
            and re.search(r"comparable|same.store|organic|comp\b|growth", it.get("name") or "", re.I)]
    if cand:
        it, rows = max(cand, key=lambda c: len(c[1]))
        pts = [(rows[k][0], rows[k][1] - rows[k - 1][1], rows[k][2].get("label")) for k in range(1, len(rows)) if prev_q(rows, k) is not None]
        label_kind = "rate"
    else:
        it, rows = _total_revenue(items, std)
        if not it:
            return sigs
        ys = yoy_series(rows)
        pts = [(ys[k][0], ys[k][1] - ys[k - 1][1], rows[ys[k][2]][2].get("label")) for k in range(1, len(ys)) if abs(ys[k][1]) <= 1.0]
        label_kind = "growth"
    obs = []
    for end, chg, label in pts:
        rd = rel.get(label)
        if not rd or rd < days[1]:
            continue
        k = next((i for i, d in enumerate(days) if d >= rd), None)
        if k is None or k == 0 or k + 1 >= len(days):
            continue
        before, after = close[days[k - 1]], close[days[k] if k + 1 >= len(days) else days[k + 1]]
        if not before or not after:
            continue
        obs.append((label, rd, chg, after / before - 1))
    obs = [o for o in obs if abs(o[2]) > 1e-9][-8:]
    if len(obs) < 5:
        return sigs
    agree = sum(1 for _, _, c, r in obs if (c > 0) == (r > 0))
    if 0.25 < agree / len(obs) < 0.75:
        return sigs
    up = [o for o in obs if o[2] > 0]
    dn = [o for o in obs if o[2] < 0]
    what = it["name"] if label_kind == "rate" else f"{it['name']} growth"
    txt = (f"Across the last {len(obs)} releases with price history, the stock's move from the close before the release to the next session's close matched "
           f"the direction of {what} (faster or slower than the prior quarter) {agree} times. "
           f"Quarters where it sped up: {len(up)}, average move {pct(statistics.mean([o[3] for o in up])) if up else 'n/a'}; quarters where it slowed: "
           f"{len(dn)}, average move {pct(statistics.mean([o[3] for o in dn])) if dn else 'n/a'}. Latest: {obs[-1][0]} released {obs[-1][1]}, "
           f"move {pct(obs[-1][3])}.")
    fid = F.add("r:kpi", txt, [{"doc_title": "Release dates from the 8-K exhibits behind the KPI values; daily closes (prices)", "label": obs[-1][0]}])
    sigs.append(_sig("reaction", 1.5 + abs(agree / len(obs) - 0.5) * 3, f"stock followed {what} direction in {agree} of {len(obs)} releases", [fid], it.get("id")))
    return sigs


def detect_analysts(F, tk, est, trend_sigs):
    sigs = []
    acts = [a for a in (est or {}).get("actions") or [] if (a.get("date") or "") >= (dt.date.today() - dt.timedelta(days=30)).isoformat()]
    if len(acts) < 3 or not trend_sigs:
        return sigs
    raised = sum(1 for a in acts if a.get("pt_prior") is not None and a.get("pt_current") is not None and a["pt_current"] > a["pt_prior"])
    cut = sum(1 for a in acts if a.get("pt_prior") is not None and a.get("pt_current") is not None and a["pt_current"] < a["pt_prior"])
    up = sum(1 for a in acts if (a.get("action") or "").lower().startswith("upgrade"))
    down = sum(1 for a in acts if (a.get("action") or "").lower().startswith("downgrade"))
    fid = F.add("a:30d", f"Analyst actions in the last 30 days: {len(acts)} in total, {raised} price-target raises, {cut} cuts, {up} upgrades and {down} downgrades.",
                [{"doc_title": "Analyst actions (estimates)"}])
    sigs.append(_sig("analysts", 1.0 + abs(raised - cut) / max(1, len(acts)), f"{raised} target raises vs {cut} cuts in 30 days", [fid]))
    return sigs


# ---------------------------------------------------------------- per company


def detect_all(tk, data, latest):
    """All detectors over one company's files; returns (Facts, signals sorted strongest first)."""
    F = Facts()
    kp, std = data.get("kpis") or {}, data.get("std") or {}
    items = kpi_items(kp) if kp else []
    sigs = []
    steps = [
        ("trend", lambda: detect_trend(F, tk, items)),
        ("mix", lambda: detect_mix(F, tk, items)),
        ("units", lambda: detect_units(F, tk, items, std)),
        ("margin", lambda: detect_margin(F, tk, items)),
        ("peers", lambda: detect_peers(F, tk, latest, std)),
        ("guidance", lambda: detect_guidance(F, tk, data.get("est"))),
        ("contracts", lambda: detect_contracts(F, tk, data.get("alt"), items)),
        ("policy", lambda: detect_policy(F, tk, data.get("alt"), data.get("gov"))),
        ("trading", lambda: detect_trading(F, tk, data.get("alt"), data.get("px"))),
        ("congress", lambda: detect_congress(F, tk, data.get("gov"))),
        ("reaction", lambda: detect_reaction(F, tk, items, std, data.get("px"))),
    ]
    errors = []
    for name, step in steps:
        try:
            sigs += step()
        except Exception as e:  # one bad series must not lose the company's other signals
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
    trend = [s for s in sigs if s["kind"] in ("trend", "units", "mix")]
    try:
        sigs += detect_analysts(F, tk, data.get("est"), trend)
    except Exception as e:
        errors.append(f"analysts: {str(e)[:120]}")
    # one signal per KPI and kind (the strongest); then strongest first
    best = {}
    for s in sigs:
        k = (s["kind"], s.get("kpi") or s["reason"])
        if k not in best or s["strength"] > best[k]["strength"]:
            best[k] = s
    sigs = sorted(best.values(), key=lambda s: -s["strength"])
    seen, out = set(), []
    for s in sigs:
        nums = tuple(sorted(re.findall(r"\d[\d,]*\.?\d*", F.text[s["facts"][0]])))
        if nums in seen:
            continue
        seen.add(nums)
        out.append(s)
    return F, out, errors


def load(sb, tk):
    k = tk.replace(".", "-")
    get = lambda p: _safe(sb, p)
    return {"kpis": get(f"kpis/{tk}.json"), "std": get(f"kpistd/t/{tk}.json"), "est": get(f"estimates/{tk}.json"),
            "alt": get(f"altdata/{tk}.json"), "gov": get(f"gov/{tk}.json"), "px": get(f"prices/{k}.json")}


def _safe(sb, path):
    try:
        return sb.get_json(path)
    except Exception:
        return None


# ---------------------------------------------------------------- model layer

PROMPT = """You write the "What the operating data says" section of an equity research page for {name} ({tk}). The facts below come from the
company's own reported KPIs (investor relations releases and workbooks), its federal contracts, lobbying and trading records, guidance
history and price reactions. Detected signals are listed strongest first, each with the reason it was flagged.

Write 3 to 6 insights. Each has a short title (at most 10 words) and a text of 1 or 2 sentences (at most 55 words). Prefer insights that
connect facts from different signals (for example traffic slowing while price rises, or federal obligations rising ahead of backlog), and
say plainly what the combination shows. Use ONLY the facts. Every number you write must appear in a cited fact. No investment advice, no
predictions, no adjectives like "significant", "strong" or "impressive". Do not explain what a KPI is. Cite every fact id you use.

Signals:
{signals}

Facts:
{facts}

Return JSON: {{"insights": [{{"title": "...", "text": "...", "kind": "trend|mix|units|margin|peers|guidance|contracts|policy|trading|congress|reaction|analysts", "refs": ["fact id", ...]}}]}}"""

CHECK_PROMPT = """You audit insights written about a company from its reported operating data and public records. For each insight you get
the facts it cites. Try to refute it. An insight is supported only if the cited facts state everything it says: names, numbers, periods,
direction of change and any comparison or combination. A causal claim, a motive or an importance the facts do not state is not supported.

Return JSON: {{"verdicts": [{{"n": <insight number>, "supported": true or false, "reason": "at most 20 words"}}]}}

{items}"""


def _complete(prompt, max_tokens=7000):
    from .llm import chat_json
    from .gov_insights import PRIMARY, FALLBACK
    obj, model, meta = chat_json([PRIMARY, FALLBACK], [{"role": "user", "content": prompt}], max_tokens=max_tokens, temperature=0.2, timeout=200)
    return obj, model


def _post(model, prompt, max_tokens=7000):
    from .llm import chat_json
    obj, _, _ = chat_json([model], [{"role": "user", "content": prompt}], max_tokens=max_tokens, timeout=200)
    return obj


def check(ok, facts, draft_model, post=None):
    from .gov_insights import PRIMARY, FALLBACK
    by = dict(facts)
    items = "\n\n".join(f"INSIGHT {n}: {s['title']}. {s['text']}\nFACTS: " + " | ".join(f"[{r}] {by[r]}" for r in s["refs"]) for n, s in enumerate(ok, 1))
    model = FALLBACK if draft_model == PRIMARY else PRIMARY
    j = (post or _post)(model, CHECK_PROMPT.format(items=items))
    verdicts = {v.get("n"): v for v in j.get("verdicts") or [] if isinstance(v, dict)}
    kept, dropped = [], []
    for n, s in enumerate(ok, 1):
        v = verdicts.get(n)
        if v and v.get("supported") is True:
            kept.append(s)
        else:
            dropped.append({"title": s["title"], "text": s["text"], "reason": "check: " + ((v or {}).get("reason") or "no verdict")[:160]})
    return kept, dropped, model


def write(tk, name, F, sigs, state, complete_fn=None, post=None, today=None):
    """Model layer over the strongest signals. Returns (insights, dropped, models) or raises if the model call fails."""
    from . import gov_insights as G
    today = today or dt.date.today()
    top = sigs[:MAX_SIGNALS_TO_MODEL]
    fids = list(dict.fromkeys(f for s in top for f in s["facts"]))
    pairs = [(f, F.text[f]) for f in fids]
    sig_txt = "\n".join(f"- [{s['kind']}] {s['reason']} (facts: {', '.join(s['facts'])})" for s in top)
    j, model = (complete_fn or _complete)(PROMPT.format(name=name, tk=tk, signals=sig_txt, facts="\n".join(f"[{i}] {t}" for i, t in pairs)))
    raw = [x for x in j.get("insights") or [] if isinstance(x, dict)]
    # verify title and text together: every number must be in the cited facts
    as_sent = [{"text": f"{(x.get('title') or '').strip()}. {(x.get('text') or '').strip()}", "refs": x.get("refs") or []} for x in raw]
    ok_s, dropped = G.verify(as_sent, pairs)
    ok_texts = {s["text"] for s in ok_s}
    ok = []
    for x, s in zip(raw, as_sent):
        if s["text"] in ok_texts:
            ok.append({"title": (x.get("title") or "").strip()[:120], "text": (x.get("text") or "").strip(), "kind": x.get("kind") if x.get("kind") in KINDS else "other",
                       "refs": [r for r in x.get("refs") if r in F.text]})
    check_model = None
    if ok:
        ok, more, check_model = check(ok, pairs, model, post=post)
        dropped += more
    recent = [set(h["refs"]) for h in state.get("history") or [] if h.get("date", "") >= (today - dt.timedelta(days=WINDOW)).isoformat()
              and h.get("key") != state.get("pending_key")]
    used = set().union(*recent) if recent else set()
    kept = []
    for s in ok:
        if used and set(s["refs"]) <= used:
            dropped.append({"title": s["title"], "text": s["text"], "reason": "not new: every cited fact was in an insight in the last 30 days"})
            continue
        kept.append(s)
    return kept[:MAX_INSIGHTS], dropped, {"draft": model, "check": check_model}


KINDS = {"trend", "mix", "units", "margin", "peers", "guidance", "contracts", "policy", "trading", "congress", "reaction", "analysts"}


def key_of(sigs, F):
    return hashlib.sha1(json.dumps([[s["kind"], s["reason"]] + [F.text[f] for f in s["facts"]] for s in sigs[:MAX_SIGNALS_TO_MODEL]]).encode()).hexdigest()[:16]


def build(sb, tk, name, sector, latest, state, use_model=None, complete_fn=None, post=None, data=None):
    """One company: detectors, then the model layer when available. Returns the published document (or None)."""
    data = data if data is not None else load(sb, tk)
    t0 = time.time()
    F, sigs, errors = detect_all(tk, data, latest)
    if not sigs:
        return None, "no signals"
    key = key_of(sigs, F)
    use_model = (bool(os.environ.get("FIREWORKS_API_KEY")) or complete_fn is not None) if use_model is None else use_model
    insights, dropped, models, status = [], [], {}, "signals_only"
    if use_model and len(sigs) >= 2:
        state["pending_key"] = key
        try:
            insights, dropped, models = write(tk, name, F, sigs, state, complete_fn=complete_fn, post=post)
            status = "ok" if insights else "all_dropped"
        except Exception as e:
            status, errors = "model_failed", errors + [f"model: {str(e)[:160]}"]
        state.pop("pending_key", None)
    elif use_model:
        status = "too_few_signals"
    used = set(f for s in insights for f in s["refs"])
    shown = list(dict.fromkeys([f for s in sigs[:20] for f in s["facts"]] + list(used)))
    doc = {"ticker": tk, "name": name, "sector": sector, "generated": now_iso(), "run_id": uuid.uuid4().hex[:12], "status": status,
           "insights": insights, "dropped": dropped[:20], "models": models,
           "signals": [{k: v for k, v in s.items() if v is not None} for s in sigs[:20]],
           "facts": {f: {"text": F.text[f], "sources": F.sources.get(f) or []} for f in shown},
           "kpi_count": len(kpi_items(data.get("kpis") or {})) if data.get("kpis") else 0,
           "errors": errors[:10], "seconds": round(time.time() - t0, 1), "key": key,
           "label": ("AI-written from the company's reported KPIs and public records, every number checked against the cited facts and each insight "
                     "checked by a second model" if insights else "Signals detected in code from the company's reported KPIs and public records")}
    if insights:
        state.setdefault("history", []).append({"date": dt.date.today().isoformat(), "refs": sorted(used), "key": key})
        state["history"] = state["history"][-12:]
    state.update({"key": key, "at": doc["generated"], "status": status})
    return doc, None


def index_row(doc):
    top = (doc.get("insights") or [None])[0]
    sig = (doc.get("signals") or [None])[0]
    return {"ticker": doc["ticker"], "name": doc.get("name"), "sector": doc.get("sector"), "generated": doc["generated"], "status": doc["status"],
            "n_insights": len(doc.get("insights") or []), "n_signals": len(doc.get("signals") or []),
            "kinds": sorted({s["kind"] for s in doc.get("signals") or []}),
            "strength": (sig or {}).get("strength", 0), "reason": (sig or {}).get("reason"),
            "top": {"title": top["title"], "text": top["text"], "kind": top["kind"]} if top else None,
            "signals": [{"kind": s["kind"], "reason": s["reason"], "strength": s["strength"]} for s in (doc.get("signals") or [])[:5]]}


def merge_index(sb, rows):
    """Update index rows for the companies just written; keeps everyone else's row."""
    idx = _safe(sb, INDEX) or {"companies": {}}
    comp = idx.get("companies") or {}
    comp.update({r["ticker"]: r for r in rows})
    ordered = sorted(comp.values(), key=lambda r: (0 if r.get("n_insights") else 1, -(r.get("strength") or 0)))
    out = {"generated": now_iso(), "count": len(comp), "companies": {r["ticker"]: r for r in ordered},
           "order": [r["ticker"] for r in sorted(comp.values(), key=lambda r: -(r.get("strength") or 0))],
           "method": "Companies ordered by their strongest detected signal (how far a KPI or record moved against its own history, "
                     "a peer group, or a prior period); the reason column says what was detected. Not a score of the stock."}
    sb.put_json(INDEX, out, cache="max-age=600")
    return out


def _source_stamp(data):
    return "|".join(str((data.get(k) or {}).get("generated") or (data.get(k) or {}).get("updated_at") or "") for k in ("kpis", "std", "est", "alt", "gov"))


def run(sb, time_budget=600, per_run=40, workers=4, tickers=None, use_model=None, log=print):
    """Rotation: companies whose source files changed since their last run (KPI companies first), else a weekly refresh."""
    from concurrent.futures import ThreadPoolExecutor
    from .universe import UNIVERSE
    t0 = time.time()
    deadline = t0 + time_budget * 0.85
    st = _safe(sb, STATE) or {"tickers": {}}
    latest = (_safe(sb, "kpistd/latest.json") or {}).get("kpis") or {}
    global MARKET_FTD
    today = dt.date.today().isoformat()
    if (st.get("market_ftd") or {}).get("date") != today:
        st["market_ftd"] = {"date": today, "dates": sorted(market_ftd_dates(sb))}
    MARKET_FTD = set(st["market_ftd"]["dates"])
    cov = (_safe(sb, "meta/coverage_ir.json") or {}).get("companies") or {}
    meta = {t: (n, s) for t, _c, n, s in UNIVERSE}
    if tickers:
        order = [t for t in tickers if t in meta]
    else:
        week = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        has_kpi = lambda t: bool((cov.get(t) or {}).get("kpis"))
        order = sorted(meta, key=lambda t: (0 if has_kpi(t) else 1, (st["tickers"].get(t) or {}).get("at", "")))
        order = [t for t in order if (st["tickers"].get(t) or {}).get("at", "") < week or (st["tickers"].get(t) or {}).get("stamp") is None
                 or (st["tickers"].get(t) or {}).get("status") in ("model_failed",)] + \
                [t for t in order if not ((st["tickers"].get(t) or {}).get("at", "") < week)]
    model_on = (bool(os.environ.get("FIREWORKS_API_KEY")) if use_model is None else use_model)
    if model_on and not tickers:
        # companies published without the model layer (signals only) go first once a model is available
        order = [t for t in order if (st["tickers"].get(t) or {}).get("status") == "signals_only"] + \
                [t for t in order if (st["tickers"].get(t) or {}).get("status") != "signals_only"]
    done, failed, unchanged, rows = [], {}, [], []

    def one(tk):
        if len(done) >= per_run or time.time() > deadline - 90:
            return
        s = st["tickers"].setdefault(tk, {})
        try:
            data = load(sb, tk)
            stamp = hashlib.sha1(_source_stamp(data).encode()).hexdigest()[:12]
            fresh = s.get("at", "") >= (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
            needs_model = model_on and s.get("status") in ("signals_only", "model_failed")
            if not tickers and s.get("stamp") == stamp and fresh and not needs_model:
                unchanged.append(tk)
                return
            name, sector = meta[tk]
            doc, why = build(sb, tk, name, sector, latest, s, use_model=use_model, data=data)
        except Exception as e:
            failed[tk] = str(e)[:200]
            s["at"] = now_iso()
            return
        s["stamp"] = stamp
        if doc is None:
            s.update({"at": now_iso(), "status": why})
            return
        sb.put_json(f"kpi_insights/{tk}.json", doc, cache="max-age=1800")
        rows.append(index_row(doc))
        done.append({"ticker": tk, "signals": len(doc["signals"]), "insights": len(doc["insights"]), "status": doc["status"]})

    candidates = order[: per_run * 4]
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, candidates))
    if rows:
        merge_index(sb, rows)
    st["generated"] = now_iso()
    sb.put_json(STATE, st, cache="no-store")
    log(f"kpi insights: {len(done)} written ({sum(1 for d in done if d['insights'])} with insights), {len(unchanged)} unchanged, "
        f"{len(failed)} failed, {round(time.time() - t0)}s")
    return {"done": done, "failed": failed, "unchanged": len(unchanged)}
