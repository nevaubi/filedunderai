"""Situation screens: independent, sourced components per company, combined into preset screens and a custom builder.

Every component is a statement of fact with the records behind it (filing links, award totals, Form 4 rows, filing
text), not a score. Components are kept separate so the app can show which independent signals line up for a company;
the "aligned" count is a count of those components, nothing more. Technical components carry the historical base rate
of the setup from tech/signals.json, with its survivorship caveat.

Inputs (all already published by other jobs):
  screener.json                         valuation vs own history, Piotroski, Altman, goodwill/assets, SBC, names
  insider_transactions (DB, 90 days)    discretionary open-market buys and sells (codes P/S, non-derivative)
  filings (DB, 8-K items, 180 days)     4.01 auditor change, 4.02 non-reliance, 2.04 triggering event, 3.01 listing notice,
                                        2.06 material impairment (routine 2.03 debt issuance is not counted)
  redlines/index.json + redlines/{T}    text added in the latest 10-K/10-Q: material weakness, going concern, covenant
                                        waivers, bridge financing
  supply/co/{T}.json                    single-source dependencies and customer concentration by filing
  altdata/{T}.json                      USAspending prime contract obligations by fiscal year
  facts (DB)                            receivables, inventory, revenue, stock-based comp by quarter
  tech/states.json + tech/signals.json  recent technical events and their base rates

Outputs (public-data):
  situations/index.json   screens, component definitions, one row per company with any component on
  situations/{T}.json     the full evidence for one company

Per-source extracts are cached in the private raw bucket (situations/cache.json.gz) keyed by the source object's
update time, so a daily run only re-reads files that changed.
"""
import datetime as dt
import gzip
import json
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .common import Run, now_iso, request

VERSION = 1
CACHE_PATH = "situations/cache.json.gz"
SEC = "https://www.sec.gov"

# ---------------------------------------------------------------------------------------------------------------- defs

COMPONENTS = {
    "insider_buy": {"label": "Insider buying", "group": "Insiders", "dir": "bullish",
                    "desc": "Discretionary open-market purchases (Form 4 code P, non-derivative) by individual officers or directors "
                            "in the last 90 days. Funds, holding companies and 10% owners are shown separately and not counted."},
    "insider_sell": {"label": "Insider selling", "group": "Insiders", "dir": "bearish",
                     "desc": "Discretionary open-market sales (code S, not under a 10b5-1 plan) by two or more individual officers or "
                             "directors in the last 90 days, at least $1M in total."},
    "holder_buy": {"label": "Large-holder buying", "group": "Insiders", "dir": "bullish",
                   "desc": "Open-market purchases by funds, holding companies or 10% owners in the last 90 days (information; these are "
                           "investment decisions by outside capital, not management conviction)."},
    "cheap_hist": {"label": "Cheap vs own history", "group": "Valuation", "dir": "bullish",
                   "desc": "P/E in the bottom 30% of its own monthly history over five years, or FCF yield in the top 30%."},
    "rich_hist": {"label": "Expensive vs own history", "group": "Valuation", "dir": "bearish",
                  "desc": "P/E in the top 10% of its own five-year monthly history."},
    "contracts_up": {"label": "Federal obligations up", "group": "Alt data", "dir": "bullish",
                     "desc": "USAspending prime contract obligations in the latest complete federal fiscal year up 30% or more on the "
                             "year before (prior year at least $100M)."},
    "contracts_down": {"label": "Federal obligations down", "group": "Alt data", "dir": "bearish",
                       "desc": "USAspending prime contract obligations in the latest complete fiscal year down 30% or more (prior year "
                               "at least $100M, latest year still above zero: a year with no recorded awards is treated as missing "
                               "data, not a decline)."},
    "distress": {"label": "Distress flag", "group": "Filings", "dir": "bearish",
                 "desc": "Material weakness or going-concern language added in the latest 10-K/10-Q, a covenant waiver, or an 8-K "
                         "for an auditor change (4.01), non-reliance on past financials (4.02), a triggering event that accelerates "
                         "an obligation (2.04) or a listing-standard notice (3.01), in the last 180 days."},
    "financing": {"label": "Financing event", "group": "Filings", "dir": "neutral",
                  "desc": "New bridge financing disclosed in the latest periodic filing, or an 8-K reporting a material impairment "
                          "(2.06), in the last 180 days. Routine debt issuance (8-K Item 2.03) is not counted."},
    "new_single_source": {"label": "New single-source dependency", "group": "Supply chain", "dir": "bearish",
                          "desc": "A sole- or single-source supplier named in the latest annual filing that the previous filing did "
                                  "not describe as single-source."},
    "customer_conc_up": {"label": "Customer concentration rising", "group": "Supply chain", "dir": "bearish",
                         "desc": "The largest disclosed customer share is 10% of revenue or more and up at least 3 points on the "
                                 "previous annual filing."},
    "mean_reversion": {"label": "Mean-reversion setup", "group": "Technicals", "dir": "bullish",
                       "desc": "A new 52-week low or oversold RSI in the last five sessions: setups whose 60-day excess return has "
                               "been positive in this universe since 2008. Base rates are past frequencies with survivorship bias "
                               "(only currently tracked companies)."},
    "quality_down": {"label": "Quality deterioration", "group": "Fundamentals", "dir": "bearish",
                     "desc": "Two or more of: Piotroski F-score 3 or lower, Altman Z below 1.8 (non-financials), receivables or "
                             "inventory growing 15 points faster than revenue year on year, stock-based comp up 2 points of "
                             "revenue year on year, goodwill above 40% of total assets."},
}

SCREENS = [
    {"id": "insider_conviction", "title": "Insider conviction", "all": ["insider_buy", "cheap_hist"],
     "desc": "Individual officers or directors buying in the open market while the stock is cheap against its own history."},
    {"id": "contracts_accel", "title": "Federal contract acceleration", "any": ["contracts_up"],
     "desc": "Prime contract obligations up 30%+ in the latest complete federal fiscal year.",
     "caveat": "Obligations are lumpy: multi-year production lots are often obligated in one year, so a single-year change is a "
               "prompt to look at the awards, not a trend."},
    {"id": "contracts_decline", "title": "Federal contract decline", "any": ["contracts_down"],
     "desc": "Prime contract obligations down 30%+ in the latest complete fiscal year.",
     "caveat": "Obligations are lumpy; compare with backlog disclosures before reading this as lost business."},
    {"id": "distress_financing", "title": "Distress & financing", "any": ["distress", "financing"],
     "desc": "Material weakness, going concern, covenant waivers, auditor changes, restatements, accelerated obligations, listing "
             "notices, bridge loans and material impairments."},
    {"id": "concentration", "title": "Concentration changes", "any": ["new_single_source", "customer_conc_up"],
     "desc": "New single-source suppliers and rising customer concentration, from the annual filings."},
    {"id": "mean_reversion", "title": "Mean-reversion setups", "any": ["mean_reversion"],
     "desc": "Recent 52-week lows and oversold RSI, with each setup's historical base rate.",
     "caveat": "Survivorship bias: companies that failed after their lows are not in the history, which flatters buying lows."},
    {"id": "quality_deterioration", "title": "Quality deterioration", "any": ["quality_down"],
     "desc": "Two or more accounting-quality or balance-sheet warnings at once."},
]

# Setups that buy weakness and whose 60-day excess return CI is above zero in tech/signals.json. Gaps are left out: most
# are earnings reactions (a different event), and they would swamp the screen.
MEAN_REV = ("low_52w", "rsi_oversold")
EIGHTK = {"4.01": ("distress", "Auditor change (8-K Item 4.01)"),
          "4.02": ("distress", "Non-reliance on previously issued financial statements (8-K Item 4.02)"),
          "2.04": ("distress", "Triggering event accelerating a financial obligation (8-K Item 2.04)"),
          "3.01": ("distress", "Notice of delisting or failure to meet a listing standard (8-K Item 3.01)"),
          "2.06": ("financing", "Material impairment (8-K Item 2.06)")}
DISTRESS_TAGS = {"material weakness", "going concern", "waiver", "covenant", "default"}

# ------------------------------------------------------------------------------------------------------------- helpers

ENTITY = re.compile(r"\b(L\.?P\.?|LLC|L\.L\.C\.|INC\.?|CORP\.?|CORPORATION|LTD\.?|ADVISORS?|ADVISERS?|CAPITAL|PARTNERS|FUND|FUNDS|"
                    r"MANAGEMENT|HOLDINGS?|TRUST|INVESTMENTS?|GROUP|FOUNDATION|ASSOCIATES|VENTURES|PLC|N\.V\.|S\.A\.|GMBH|AG)\b", re.I)
OFFICER = re.compile(r"\b(chief|ceo|cfo|coo|cto|president|officer|vp|vice president|evp|svp|treasurer|secretary|controller|"
                     r"general counsel|chair(man|woman|person)?)\b", re.I)


def clean_owner(name):
    """Owner name without the '(+N)' suffix added when a Form 4 lists several reporting owners."""
    return re.sub(r"\s*\(\+\d+\)\s*$", "", (name or "").strip())


def owner_kind(row):
    """officer | director | holder (fund, holding company or 10% owner) | other. Prefers an upstream classification
    (owner_kind / owner_type / is_fund) when the insiders job provides one."""
    up = (row.get("owner_kind") or row.get("owner_type") or "").lower()
    if up in ("officer", "director", "holder", "other"):
        return up
    if up in ("fund", "entity", "10% owner", "ten_percent_owner"):
        return "holder"
    if row.get("is_fund") is True:
        return "holder"
    role, name = (row.get("role") or ""), clean_owner(row.get("owner"))
    if ENTITY.search(name):
        return "holder"
    if OFFICER.search(role):
        return "officer"
    if re.search(r"\bdirector\b", role, re.I):
        return "director"
    if "10%" in role:
        return "holder"
    return "other"


def filing_index_url(cik, accession):
    try:
        return f"{SEC}/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
    except (TypeError, ValueError):
        return None


def filing_doc_url(cik, accession, doc):
    base = filing_index_url(cik, accession)
    return base + doc if base and doc else base


def usd(v):
    v = _f(v)
    if v is None:
        return "n/a"
    a = abs(v)
    return f"${v / 1e9:,.2f}B" if a >= 1e9 else f"${v / 1e6:,.1f}M" if a >= 1e6 else f"${v:,.0f}"


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z(\"'])", text or "") if s.strip()]


NEGATION = re.compile(r"\b(no|not|none|did not|have not|has not|had not|absence|without|alleviat\w*|remediat(ed|ion of)|"
                      r"previously (reported|disclosed|identified))\b", re.I)


# Risk-factor boilerplate is conditional ("If we identify a material weakness ... could"); a disclosure is not.
HYPOTHETICAL = re.compile(r"\b(if|should|could|may|might|would|whether|in the event|any future|future)\b", re.I)
MW_EXISTING = re.compile(r"(identified|identify|existence of|resulted in|constitute[sd]?|remediat\w* (the|these|this|those|our|such)|"
                         r"\b(the|these|this|those|such) material weakness(es)?|material weakness(es)? (that|which) (was|were|existed|exist|is|are)|"
                         r"(was|were|is|are|has been|have been) (a |an )?material weakness)", re.I)
WAIVER_DONE = re.compile(r"(obtained|received|granted|entered into|executed|agreed to|secured)\b[^.;]{0,80}\bwaiver|"
                         r"\bwaiver[^.;]{0,60}\b(was|were|has been|have been) (obtained|granted|received|executed)", re.I)


def _affirmative(sentence, phrase):
    low = sentence.lower()
    i = low.find(phrase)
    if i < 0 or NEGATION.search(low[:i]) or HYPOTHETICAL.search(low[:i]):
        return False
    # Resolution language anywhere in the sentence ("... has been alleviated", "... was remediated") is not a new flag.
    return not re.search(r"alleviat|no longer|(has|have|was|were|been) (fully )?remediated", low)


def text_flags(text):
    """Distress and financing flags in one added/modified filing passage: [(component, label, sentence)].
    Only disclosures count: negated, conditional (risk-factor boilerplate) and resolved statements are skipped."""
    out = []
    for s in sentences(text):
        low = s.lower()
        if "material weakness" in low and _affirmative(s, "material weakness") and MW_EXISTING.search(s):
            out.append(("distress", "Material weakness disclosed", s))
        elif "substantial doubt" in low and "going concern" in low and _affirmative(s, "substantial doubt"):
            out.append(("distress", "Going-concern doubt disclosed", s))
        elif "waiver" in low and "covenant" in low and WAIVER_DONE.search(s) and not re.search(r"\bno waiver\b", low) \
                and not HYPOTHETICAL.search(low[:low.find("waiver")]):
            out.append(("distress", "Covenant waiver", s))
        elif (m := re.search(r"\bbridge (loan|facility|credit|commitment|term loan|financing)", low)) and not HYPOTHETICAL.search(low[:m.start()]):
            out.append(("financing", "Bridge financing", s))
    return out


def redline_flags(doc, since):
    """Flags from the passages added or rewritten in each comparison filed on/after `since`."""
    out, seen = [], set()
    for comp in doc.get("comparisons") or []:
        new = comp.get("new") or {}
        if (new.get("filed") or "") < since:
            continue
        for sec in comp.get("sections") or []:
            changes = sec.get("changes") or []
            items = changes[1] if len(changes) == 2 and isinstance(changes[1], list) else changes if isinstance(changes, list) else []
            for ch in items:
                if not isinstance(ch, dict) or ch.get("kind") not in ("added", "modified") or not ch.get("new"):
                    continue
                for kind, label, s in text_flags(ch["new"]):
                    key = (label, s[:120])
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({"component": kind, "label": label, "quote": s[:600], "date": new.get("filed"), "form": new.get("form"),
                                "section": sec.get("title"), "url": new.get("url")})
    return out


def eightk_flags(rows):
    out = []
    for r in rows:
        items = [x.strip() for x in (r.get("items") or "").split(",")]
        for it in items:
            if it in EIGHTK:
                comp, label = EIGHTK[it]
                out.append({"component": comp, "label": label, "date": r.get("filed"), "form": "8-K", "url": r.get("url"),
                            "quote": r.get("description") or None})
    return out


# ----------------------------------------------------------------------------------------------------------- components

def insider_components(rows, today):
    """insider_buy / insider_sell / holder_buy from one company's Form 4 rows (already joint-filer deduped)."""
    cut = (today - dt.timedelta(days=90)).isoformat()
    buys, sells, holder = [], [], []
    for r in rows:
        if r.get("derivative") or not r.get("tx_date") or r["tx_date"] < cut or r.get("code") not in ("P", "S"):
            continue
        v = (_f(r.get("shares")) or 0) * (_f(r.get("price")) or 0)
        if v <= 0:
            continue
        kind = owner_kind(r)
        ev = {"label": f"{clean_owner(r.get('owner'))} ({r.get('role') or 'insider'})", "date": r["tx_date"],
              "value": round(v), "shares": _f(r.get("shares")), "price": _f(r.get("price")), "code": r["code"],
              "plan": bool(r.get("plan_10b5")), "url": filing_index_url(r.get("cik"), r.get("accession")),
              "joint": r.get("joint") or None}
        if r["code"] == "P":
            (buys if kind in ("officer", "director") else holder if kind == "holder" else []).append(ev)
        elif kind in ("officer", "director") and not r.get("plan_10b5"):
            sells.append(ev)
    out = {}
    if buys:
        who = {e["label"].split(" (")[0] for e in buys}
        total = sum(e["value"] for e in buys)
        out["insider_buy"] = {"headline": f"{len(who)} insider{'s' if len(who) > 1 else ''} bought ${total / 1e6:,.2f}M in {len(buys)} "
                                          f"open-market trade{'s' if len(buys) > 1 else ''} (90 days)",
                              "metrics": {"value": total, "buyers": len(who), "trades": len(buys)},
                              "evidence": sorted(buys, key=lambda e: -e["value"])[:25]}
    if sells:
        who = {e["label"].split(" (")[0] for e in sells}
        total = sum(e["value"] for e in sells)
        if len(who) >= 2 and total >= 1e6:
            out["insider_sell"] = {"headline": f"{len(who)} insiders sold ${total / 1e6:,.1f}M outside 10b5-1 plans (90 days)",
                                   "metrics": {"value": total, "sellers": len(who), "trades": len(sells)},
                                   "evidence": sorted(sells, key=lambda e: -e["value"])[:25]}
    if holder:
        who = {e["label"].split(" (")[0] for e in holder}
        total = sum(e["value"] for e in holder)
        out["holder_buy"] = {"headline": f"{', '.join(sorted(who)[:2])}{' and others' if len(who) > 2 else ''} bought "
                                         f"${total / 1e6:,.1f}M (90 days)",
                             "metrics": {"value": total, "buyers": len(who), "trades": len(holder)},
                             "evidence": sorted(holder, key=lambda e: -e["value"])[:25]}
    return out


def valuation_components(row):
    out = {}
    pe, pe_p, fy_p, fy = _f(row.get("pe")), _f(row.get("pe_p5")), _f(row.get("fcf_yield_p5")), _f(row.get("fcf_yield"))
    ev = []
    if pe_p is not None and pe is not None:
        ev.append({"label": f"P/E {pe:.1f}x, {pe_p * 100:.0f}th percentile of its own 5-year monthly history"})
    if fy_p is not None and fy is not None:
        ev.append({"label": f"FCF yield {fy * 100:.1f}%, {fy_p * 100:.0f}th percentile of its own 5-year history (higher = cheaper)"})
    src = "/app/screener"
    for e in ev:
        e["url"] = src
    if (pe_p is not None and pe_p <= 0.30) or (fy_p is not None and fy_p >= 0.70):
        bits = []
        if pe_p is not None and pe_p <= 0.30:
            bits.append(f"P/E at {pe_p * 100:.0f}th pct of own history")
        if fy_p is not None and fy_p >= 0.70:
            bits.append(f"FCF yield at {fy_p * 100:.0f}th pct")
        out["cheap_hist"] = {"headline": "; ".join(bits), "metrics": {"pe": pe, "pe_p5": pe_p, "fcf_yield": fy, "fcf_yield_p5": fy_p},
                             "evidence": ev}
    if pe_p is not None and pe is not None and pe_p >= 0.90:
        out["rich_hist"] = {"headline": f"P/E {pe:.1f}x at {pe_p * 100:.0f}th pct of own 5-year history",
                            "metrics": {"pe": pe, "pe_p5": pe_p}, "evidence": ev}
    return out


def contracts_components(fy_rows, source=None):
    """fy_rows: [{fy, amount, partial}] (USAspending prime obligations by federal fiscal year)."""
    full = sorted((r for r in fy_rows or [] if not r.get("partial") and _f(r.get("amount")) is not None), key=lambda r: r["fy"])
    if len(full) < 2:
        return {}
    a, b = full[-2], full[-1]
    prior, latest = _f(a["amount"]), _f(b["amount"])
    if prior is None or prior < 100e6 or latest is None or latest <= 0:
        return {}
    chg = latest / prior - 1
    ev = [{"label": f"FY{r['fy']} prime contract obligations", "value": round(_f(r["amount"])), "date": f"FY{r['fy']}",
           "url": (source or {}).get("url")} for r in full[-5:]]
    metrics = {"fy": b["fy"], "latest": latest, "prior": prior, "change": chg}
    head = f"FY{b['fy']} obligations {usd(latest)} vs {usd(prior)} in FY{a['fy']} ({chg * 100:+.0f}%)"
    if chg >= 0.30:
        return {"contracts_up": {"headline": head, "metrics": metrics, "evidence": ev}}
    if chg <= -0.30:
        return {"contracts_down": {"headline": head, "metrics": metrics, "evidence": ev}}
    return {}


def filing_components(flags):
    out = {}
    for comp in ("distress", "financing"):
        ev = [f for f in flags if f["component"] == comp]
        if not ev:
            continue
        ev.sort(key=lambda f: f.get("date") or "", reverse=True)
        labels = sorted({f["label"] for f in ev})
        out[comp] = {"headline": "; ".join(labels[:3]) + (f" (+{len(labels) - 3} more)" if len(labels) > 3 else ""),
                     "metrics": {"flags": labels, "n": len(ev)}, "evidence": [{k: v for k, v in f.items() if k != "component"} for f in ev[:20]]}
    return out


def _link_key(link):
    return (link.get("cpt") or link.get("cp") or "").strip().lower()


def concentration_components(co):
    """new_single_source / customer_conc_up from supply/co/{T}.json (current links, history and concentration quotes)."""
    out = {}
    cik = co.get("cik")
    filings = sorted((f for f in co.get("filings") or [] if f.get("form") in ("10-K", "20-F", "10-K/A")), key=lambda f: f.get("filed") or "")
    cur_links = [l for l in co.get("links") or [] if l.get("ss")]
    hist = co.get("history") or []
    if cur_links and filings:
        latest = filings[-1]
        prior_ss = {_link_key(l) for l in hist if l.get("ss") and (l.get("filed") or "") < (latest.get("filed") or "")}
        prior_any = {_link_key(l) for l in hist if (l.get("filed") or "") < (latest.get("filed") or "")}
        had_prior = any((l.get("filed") or "") < (latest.get("filed") or "") for l in hist)
        new = [l for l in cur_links if _link_key(l) and _link_key(l) not in prior_ss]
        if new and had_prior:
            ev = [{"label": f"{l.get('cp')} ({l.get('det') or l.get('rel')})" + ("" if _link_key(l) in prior_any else ", not named before"),
                   "quote": (l.get("q") or "")[:600], "date": latest.get("filed"), "form": latest.get("form"),
                   "url": latest.get("url") or filing_doc_url(cik, latest.get("accn"), latest.get("doc"))} for l in new[:10]]
            out["new_single_source"] = {"headline": f"New single-source: {', '.join(l.get('cp') for l in new[:3])}"
                                                    + (f" (+{len(new) - 3})" if len(new) > 3 else ""),
                                        "metrics": {"n": len(new)}, "evidence": ev}
    conc = [c for c in co.get("conc") or [] if _f(c.get("pct")) is not None and c.get("form") in ("10-K", "20-F", "10-K/A")]
    by_filing = {}
    for c in conc:
        by_filing.setdefault(c.get("filed") or "", []).append(c)
    dates = sorted(d for d in by_filing if d)
    if len(dates) >= 2:
        cur, prev = by_filing[dates[-1]], by_filing[dates[-2]]
        top_c, top_p = max(cur, key=lambda c: _f(c["pct"])), max(prev, key=lambda c: _f(c["pct"]))
        a, b = _f(top_p["pct"]), _f(top_c["pct"])
        # A jump of more than 30 points between annual filings is usually a different measure being quoted (share of a
        # segment, of one product), so it is left out rather than shown as concentration.
        if b >= 10 and 3 <= b - a <= 30:
            ev = [{"label": f"Largest customer share {x:.0f}% ({c.get('filed')})", "quote": (c.get("q") or "")[:600], "date": c.get("filed"),
                   "form": c.get("form"), "url": filing_doc_url(cik, c.get("accn"), c.get("doc"))} for c, x in ((top_c, b), (top_p, a))]
            out["customer_conc_up"] = {"headline": f"Largest customer {a:.0f}% -> {b:.0f}% of revenue", "metrics": {"latest": b, "prior": a},
                                       "evidence": ev}
    return out


def base_rate(signals, sig, horizon="60"):
    s = ((signals or {}).get("signals") or {}).get(sig) or {}
    h = s.get(horizon) or {}
    if h.get("excess") is None:
        return None
    return {"signal": sig, "horizon": int(horizon), "excess": h.get("excess"), "ci": h.get("excess_ci"), "n": h.get("n"),
            "hit": h.get("hit"), "first": s.get("first"), "last": s.get("last")}


SIG_LABEL = {"low_52w": "New 52-week low", "rsi_oversold": "RSI oversold"}


def mean_reversion_component(state, signals, as_of):
    if not state:
        return {}
    cut = (dt.date.fromisoformat(as_of) - dt.timedelta(days=7)).isoformat() if as_of else ""
    ev = []
    for name, d in state.get("events") or []:
        if name in MEAN_REV and d >= cut:
            br = base_rate(signals, name)
            ev.append({"label": f"{SIG_LABEL[name]} on {d}", "date": d, "signal": name, "base_rate": br, "url": "/app/markets?view=indicators"})
    if not ev:
        return {}
    ev.sort(key=lambda e: e["date"], reverse=True)
    return {"mean_reversion": {"headline": ", ".join(sorted({SIG_LABEL[e['signal']] for e in ev})) + f" (last {ev[0]['date']})",
                               "metrics": {"signals": sorted({e["signal"] for e in ev}), "rsi": _f(state.get("rsi")),
                                           "from_lo": _f(state.get("from_lo")), "from_hi": _f(state.get("from_hi"))},
                               "evidence": ev}}


def _yoy(series):
    """Latest value and the value four quarters earlier from [(end, value)] sorted ascending (ends within ~13 months)."""
    if len(series) < 5:
        return None, None, None
    end, v = series[-1]
    target = dt.date.fromisoformat(end) - dt.timedelta(days=365)
    best = min(series[:-1], key=lambda p: abs((dt.date.fromisoformat(p[0]) - target).days))
    if abs((dt.date.fromisoformat(best[0]) - target).days) > 45:
        return None, None, None
    return v, best[1], end


def quality_component(row, facts):
    """facts: {label: [(end, value)]} quarterly, latest filed value per period."""
    flags, ev = [], []
    fin = row.get("sector") == "Financials"
    pio = _f(row.get("pio_f"))
    if pio is not None and pio <= 3:
        flags.append("Piotroski F-score " + f"{pio:.0f}")
        ev.append({"label": f"Piotroski F-score {pio:.0f} of 9 (trailing four quarters vs the four before)", "url": "/app/screener"})
    z = _f(row.get("altman_z"))
    if z is not None and not fin and z < 1.8:
        flags.append(f"Altman Z {z:.2f}")
        ev.append({"label": f"Altman Z {z:.2f} (below 1.8 is the distress zone)", "url": "/app/screener"})
    gw = _f(row.get("gw_assets"))
    if gw is not None and gw > 0.40:
        flags.append(f"Goodwill {gw * 100:.0f}% of assets")
        ev.append({"label": f"Goodwill {gw * 100:.0f}% of total assets", "url": "/app/screener"})
    rev_now, rev_ago, end = _yoy(facts.get("Revenue") or [])
    rev_g = rev_now / rev_ago - 1 if rev_now and rev_ago and rev_ago > 0 else None
    if rev_g is not None and not -0.5 <= rev_g <= 1.0:
        # A halving or doubling of quarterly revenue is usually M&A, a divestiture or a period mismatch: working-capital
        # comparisons against it say nothing about collection or inventory quality.
        rev_g = None
    for lab, name in (("Accounts receivable", "Receivables"), ("Inventory", "Inventory")):
        now, ago, e2 = _yoy(facts.get(lab) or [])
        if rev_g is None or not now or not ago or ago <= 0 or e2 != end:
            continue
        g = now / ago - 1
        if g - rev_g >= 0.15 and now >= 0.05 * (rev_now or 0):
            flags.append(f"{name} {g * 100:+.0f}% vs revenue {rev_g * 100:+.0f}%")
            ev.append({"label": f"{name} {g * 100:+.0f}% YoY vs revenue {rev_g * 100:+.0f}% (quarter ended {end})", "date": end})
    sbc_now, sbc_ago, e3 = _yoy(facts.get("Stock-based comp") or [])
    if rev_now and rev_ago and sbc_now is not None and sbc_ago is not None and e3 == end and rev_now > 0 and rev_ago > 0:
        a, b = sbc_ago / rev_ago, sbc_now / rev_now
        if b - a >= 0.02:
            flags.append(f"SBC {a * 100:.1f}% -> {b * 100:.1f}% of revenue")
            ev.append({"label": f"Stock-based comp {a * 100:.1f}% -> {b * 100:.1f}% of revenue (quarter ended {end} vs a year earlier)", "date": end})
    if len(flags) >= 2:
        return {"quality_down": {"headline": "; ".join(flags[:3]) + (f" (+{len(flags) - 3})" if len(flags) > 3 else ""),
                                 "metrics": {"flags": flags, "n": len(flags)}, "evidence": ev}}
    return {}


def screen_members(rows, screen):
    out = []
    for r in rows:
        on = set(r["c"])
        if screen.get("all") and not all(c in on for c in screen["all"]):
            continue
        if screen.get("any") and not any(c in on for c in screen["any"]):
            continue
        out.append(r["t"])
    return out


# --------------------------------------------------------------------------------------------------------------- loaders

def _list(sb, prefix):
    """{name: updated_at} for public-data objects under prefix (one level)."""
    out, off = {}, 0
    while True:
        body = request(f"{sb.url}/storage/v1/object/list/public-data", method="POST",
                       data={"prefix": prefix, "limit": 1000, "offset": off, "sortBy": {"column": "name", "order": "asc"}},
                       headers=sb._h(), timeout=60)
        page = json.loads(body)
        for x in page:
            if x.get("metadata"):
                out[x["name"]] = x.get("updated_at") or ""
        if len(page) < 1000:
            return out
        off += 1000


def _load_cache(sb):
    try:
        raw = sb.get_object("raw", CACHE_PATH)
        c = json.loads(gzip.decompress(raw)) if raw else {}
    except Exception:
        c = {}
    return c if c.get("_v") == VERSION else {"_v": VERSION}


def _save_cache(sb, cache):
    sb.put_object("raw", CACHE_PATH, gzip.compress(json.dumps(cache, separators=(",", ":")).encode()), "application/gzip", cache="no-store")


def _refresh(sb, cache, key, prefix, tickers, extract, deadline, workers=16, log=print):
    """Re-extract per-ticker files whose storage update time changed. Cache entry: {u: updated_at, x: extract}."""
    store = cache.setdefault(key, {})
    listing = _list(sb, prefix)
    todo = [t for t in tickers if f"{t}.json" in listing and (store.get(t) or {}).get("u") != listing[f"{t}.json"]]
    for t in list(store):
        if f"{t}.json" not in listing:
            store.pop(t, None)

    def one(t):
        if time.time() > deadline:
            return t, None
        try:
            return t, extract(sb.get_json(f"{prefix}{t}.json") or {})
        except Exception as e:
            log(f"situations: {prefix}{t}.json: {str(e)[:120]}")
            return t, None
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for t, x in ex.map(one, todo):
            if x is not None:
                store[t] = {"u": listing[f"{t}.json"], "x": x}
                done += 1
    log(f"situations: {prefix} {done}/{len(todo)} refreshed")
    return {t: v["x"] for t, v in store.items()}


def _x_contracts(d):
    c = d.get("contracts") or {}
    return {"rows": [{"fy": r.get("fy"), "amount": r.get("amount"), "partial": bool(r.get("partial"))} for r in c.get("rows") or []],
            "source": d.get("contracts_source")}


def _x_supply(d):
    keep = ("cp", "cpt", "rel", "det", "ss", "q", "filed", "form", "accn", "doc")
    return {"cik": d.get("cik"), "filings": [{k: f.get(k) for k in ("accn", "form", "filed", "doc", "url")} for f in d.get("filings") or []],
            "links": [{k: l.get(k) for k in keep} for l in d.get("links") or [] if l.get("ss")],
            "history": [{k: l.get(k) for k in keep} for l in d.get("history") or []],
            "conc": [{k: c.get(k) for k in ("pct", "q", "filed", "form", "accn", "doc")} for c in d.get("conc") or []]}


def _paged(sb, table, query, deadline):
    out, off = [], 0
    while time.time() < deadline:
        page = json.loads(request(f"{sb.url}/rest/v1/{table}?{query}&offset={off}&limit=1000", headers=sb._h(), timeout=90))
        out += page
        if len(page) < 1000:
            break
        off += 1000
    return out


def load_insiders(sb, today, deadline):
    from .insiders import dedupe_joint
    since = (today - dt.timedelta(days=95)).isoformat()
    rows = _paged(sb, "insider_transactions", f"select=*&tx_date=gte.{since}&code=in.(P,S)&derivative=eq.false&order=ticker.asc,accession.asc,seq.asc", deadline)
    by = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r)
    return {t: dedupe_joint(rs) for t, rs in by.items()}


def load_8k(sb, today, deadline):
    since = (today - dt.timedelta(days=180)).isoformat()
    ors = ",".join(f"items.like.*{k}*" for k in EIGHTK)
    rows = _paged(sb, "filings", f"select=ticker,accession,filed,items,description,url&form=eq.8-K&filed=gte.{since}&or=({ors})&order=filed.desc,accession.asc", deadline)
    by = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r)
    return by


def load_facts(sb, today, deadline):
    since = (today - dt.timedelta(days=int(365 * 2.2))).isoformat()
    out = {}
    for label in ("Revenue", "Accounts receivable", "Inventory", "Stock-based comp"):
        q = (f"select=ticker,period_end,value,filed&label=eq.{urllib.parse.quote(label)}&kind=eq.Q&period_end=gte.{since}"
             f"&order=ticker.asc,period_end.asc,filed.asc")
        for r in _paged(sb, "facts", q, deadline):
            v = _f(r.get("value"))
            if v is None:
                continue
            out.setdefault(r["ticker"], {}).setdefault(label, {})[r["period_end"]] = v  # later filings overwrite earlier ones
    return {t: {lab: sorted(d.items()) for lab, d in labs.items()} for t, labs in out.items()}


def load_redline_flags(sb, cache, today, deadline, log=print):
    """Distress/financing flags from the latest periodic filings. Only companies whose latest comparisons carry one of
    the distress tags are read in full; results are cached by the redline file's generated time."""
    since = (today - dt.timedelta(days=180)).isoformat()
    idx = sb.get_json("redlines/index.json") or {}
    # Bump the key when text_flags changes so cached flags are recomputed.
    store = cache.setdefault("redlines_v2", {})
    cache.pop("redlines", None)
    cands = {}
    for t, v in (idx.get("companies") or {}).items():
        comps = [c for c in v.get("comparisons") or [] if (c.get("filed") or "") >= since]
        if any(DISTRESS_TAGS & set(c.get("signals") or []) for c in comps):
            cands[t] = v.get("generated") or ""
    todo = [t for t, g in cands.items() if (store.get(t) or {}).get("u") != g]

    def one(t):
        if time.time() > deadline:
            return t, None
        try:
            return t, redline_flags(sb.get_json(f"redlines/{t}.json") or {}, since)
        except Exception as e:
            log(f"situations: redlines/{t}.json: {str(e)[:120]}")
            return t, None
    n = 0
    with ThreadPoolExecutor(12) as ex:
        for t, flags in ex.map(one, todo):
            if flags is not None:
                store[t] = {"u": cands[t], "x": flags}
                n += 1
    for t in list(store):
        if t not in cands:
            store.pop(t, None)
    log(f"situations: redlines {n}/{len(todo)} read ({len(cands)} candidates)")
    return {t: [f for f in v["x"] if (f.get("date") or "") >= since] for t, v in store.items()}


# ------------------------------------------------------------------------------------------------------------------ job

def situations_job(sb, time_budget=700, log=print):
    t0 = time.time()
    deadline = t0 + time_budget - 60
    today = dt.date.today()
    with Run(sb, "situations") as run:
        scr = sb.get_json("screener.json") or {}
        cols = scr.get("cols") or []
        rows = {r[0]: dict(zip(cols, r)) for r in scr.get("rows") or []}
        tickers = sorted(rows)
        signals = sb.get_json("tech/signals.json") or {}
        states_doc = sb.get_json("tech/states.json") or {}
        states = {r["t"]: r for r in states_doc.get("rows") or []}
        as_of = states_doc.get("as_of") or today.isoformat()
        cache = _load_cache(sb)
        stats = {}
        insiders = load_insiders(sb, today, deadline)
        eightk = load_8k(sb, today, deadline)
        facts = load_facts(sb, today, deadline)
        contracts = _refresh(sb, cache, "contracts", "altdata/", tickers, _x_contracts, deadline, log=log)
        supply = _refresh(sb, cache, "supply", "supply/co/", tickers, _x_supply, deadline, log=log)
        red = load_redline_flags(sb, cache, today, deadline, log=log)
        _save_cache(sb, cache)
        stats.update(insiders=len(insiders), eightk=len(eightk), facts=len(facts), contracts=len(contracts), supply=len(supply), redlines=len(red))

        index_rows, details = [], {}
        for t in tickers:
            row = rows[t]
            comps = {}
            comps.update(insider_components(insiders.get(t) or [], today))
            comps.update(valuation_components(row))
            c = contracts.get(t)
            if c:
                comps.update(contracts_components(c["rows"], c.get("source")))
            comps.update(filing_components((red.get(t) or []) + eightk_flags(eightk.get(t) or [])))
            s = supply.get(t)
            if s:
                comps.update(concentration_components(s))
            comps.update(mean_reversion_component(states.get(t), signals, as_of))
            comps.update(quality_component(row, facts.get(t) or {}))
            if not comps:
                continue
            for k, v in comps.items():
                v["dir"] = COMPONENTS[k]["dir"]
            details[t] = comps
            index_rows.append({"t": t, "n": row.get("name"), "s": row.get("sector"), "mcap": row.get("mcap"), "px": row.get("price"),
                               "c": {k: {"h": v["headline"], "m": {mk: mv for mk, mv in (v.get("metrics") or {}).items()
                                                                     if isinstance(mv, (int, float, str)) or mv is None}}
                                     for k, v in comps.items()}})
        screens = []
        for sc in SCREENS:
            members = screen_members([{"t": r["t"], "c": r["c"]} for r in index_rows], sc)
            screens.append({**sc, "n": len(members)})
        gen = now_iso()
        doc = {"version": VERSION, "generated": gen, "as_of": as_of, "components": COMPONENTS, "screens": screens, "rows": index_rows,
               "base_rates": {k: base_rate(signals, k) for k in MEAN_REV},
               "sources": {"insiders": "SEC Form 4 (insider_transactions)", "filings": "SEC EDGAR 8-K items and 10-K/10-Q redlines",
                           "contracts": "USAspending.gov prime contract obligations (award types A-D)",
                           "supply": "Supply-chain links and customer concentration quoted from 10-K filings",
                           "valuation": "Filed Under screener (SEC filings, daily closes)", "technicals": "tech/signals.json base rates since 2008"},
               "caveats": ["Components are independent facts with their sources; the aligned count is a count, not a score.",
                           "Technical base rates are past frequencies in currently tracked companies (survivorship bias)."]}
        sb.put_json("situations/index.json", doc, cache="max-age=900")

        def put(t):
            sb.put_json(f"situations/{t}.json", {"ticker": t, "generated": gen, "as_of": as_of, "components": details[t]}, cache="max-age=900")
        with ThreadPoolExecutor(16) as ex:
            list(ex.map(put, list(details)))
        stats.update(companies=len(index_rows), screens={s["id"]: s["n"] for s in screens})
        run.stats.update(stats)
        log(json.dumps(stats))
        return len(index_rows), {}
