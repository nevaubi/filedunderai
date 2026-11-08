"""What changed: one sourced, ranked event stream per company (90 days) and for the universe (30 days).

Every event is built from a stored record the app already shows, carries the evidence it rests on (excerpt and link)
and a stable id so monitors and digests can tell new events from ones already seen. Materiality is a tier (high,
medium, low) set by explicit rules; the rule that fired is published as `reason`, and the real quantities behind it
(dollars, share of market cap, growth) are in `metrics`. There is no composite score.

Sources (all already published or stored by other jobs):
  filings table (8-K items), redlines/{T}.json (year-over-year filing comparisons), insiders/{T}.json (deduplicated
  Form 4 rows), estimates/{T}.json (analyst actions, surprises, consensus snapshots), altdata/{T}.json (USAspending
  prime contract obligations by fiscal year), ir_documents table (IR decks, releases, transcripts),
  tech/states.json + tech/signals.json (technical events and their 2008+ base rates), calendars/dividends.json,
  calendars/ipos.json, macro/releases.json, screener.json (market cap, macro correlations).

Outputs: public-data changes/feed.json, changes/{T}.json; private raw/changes/state.json.gz (per-source caches,
first-seen times, upload hashes).
"""
import datetime as dt
import gzip
import hashlib
import json
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .common import Run, now_iso
from .universe import UNIVERSE

STATE_PATH = "changes/state.json.gz"
FEED_DAYS = 30
COMPANY_DAYS = 90
FEED_MAX = 1200
VERSION = 1
TIERS = ("high", "medium", "low")
TIER_ORDER = {t: i for i, t in enumerate(TIERS)}
BY_TICKER = {t: {"cik": c, "name": n, "sector": s} for t, c, n, s in UNIVERSE}

METHOD = ("Events come from stored filings, Form 4s, analyst actions, contract obligations, IR documents, dividend "
          "declarations, IPO filings, macro release calendars and technical events. Tiers follow fixed rules stated "
          "on each event (reason); the quantities behind them are in metrics. Technical base rates are past "
          "frequencies across currently tracked companies (survivorship-biased), not forecasts.")


# ---------------------------------------------------------------- helpers
def eid(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _d(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def money(v):
    v = _num(v)
    if v is None:
        return "n/a"
    a = abs(v)
    s = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{s}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{s}${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{s}${a / 1e3:.0f}K"
    return f"{s}${a:,.0f}"


def pct(v, d=1, sign=True):
    v = _num(v)
    if v is None:
        return "n/a"
    return f"{v * 100:+.{d}f}%" if sign else f"{v * 100:.{d}f}%"


def clip(s, n=320):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def first_sentence(s, n=180):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    m = re.match(r"(.{30,}?[.;:])(\s|$)", s)
    return clip(m.group(1) if m else s, n)


def event(type_, sub, t, date, title, tier, reason, key, evidence=None, detail=None, metrics=None, base=None, tags=None, upcoming=False):
    meta = BY_TICKER.get(t) or {}
    return {"id": eid(type_, sub, t, key), "type": type_, "sub": sub, "t": t, "name": meta.get("name"), "sector": meta.get("sector"),
            "date": str(date)[:10], "title": title, "detail": detail, "tier": tier, "reason": reason,
            "evidence": [e for e in (evidence or []) if e and e.get("url")], "metrics": metrics or {},
            "base_rate": base, "tags": tags or [], "upcoming": bool(upcoming)}


def sec_url(url):
    return {"label": "SEC filing", "url": url, "kind": "sec"} if url else None


def app_link(label, path):
    return {"label": label, "url": path, "kind": "app"}


# ---------------------------------------------------------------- 8-K items
ITEMS = {
    "1.01": ("medium", "Entered a material definitive agreement"),
    "1.02": ("medium", "Terminated a material definitive agreement"),
    "1.03": ("high", "Bankruptcy or receivership"),
    "2.01": ("medium", "Completed an acquisition or disposition of assets"),
    "2.03": ("medium", "Created a direct financial obligation (new debt or off-balance-sheet arrangement)"),
    "2.04": ("high", "Triggering event accelerating a financial obligation"),
    "2.05": ("medium", "Committed to exit or disposal costs (restructuring)"),
    "2.06": ("high", "Material impairment"),
    "3.01": ("high", "Notice of delisting or failure to meet a listing rule"),
    "3.02": ("low", "Unregistered sale of equity securities"),
    "3.03": ("low", "Material modification to the rights of security holders"),
    "4.01": ("high", "Change in the company's certifying accountant (auditor change)"),
    "4.02": ("high", "Non-reliance on previously issued financial statements (restatement)"),
    "5.01": ("high", "Change in control of the company"),
    "5.02": ("low", "Director or officer departure, election or appointment, or compensation arrangement"),
    "5.03": ("low", "Amendment to articles or bylaws, or change in fiscal year"),
    "1.05": ("high", "Material cybersecurity incident"),
    "5.07": ("low", "Shareholder vote results"),
    "1.04": ("low", "Mine safety violation or order"),
    "5.05": ("low", "Amendment to or waiver of the code of ethics"),
}


def filing_8k_events(rows, since):
    """One event per filing per notable item; 2.02 (results) is handled by earnings_events."""
    out = []
    for r in rows:
        t, filed = r.get("ticker"), _d(r.get("filed"))
        if t not in BY_TICKER or not filed or filed < since:
            continue
        items = [i.strip() for i in str(r.get("items") or "").split(",") if i.strip()]
        for it in items:
            if it not in ITEMS:
                continue
            tier, what = ITEMS[it]
            out.append(event("filing_8k", f"item_{it.replace('.', '_')}", t, filed, f"8-K Item {it}: {what}", tier,
                             f"8-K Item {it} is a {tier}-tier disclosure under fixed rules ({what.lower()})",
                             (r.get("accession"), it), evidence=[sec_url(r.get("url"))],
                             detail=clip(r.get("description"), 160) if r.get("description") else None,
                             metrics={"items": items}, tags=["8-K", f"Item {it}"]))
    return out


def form144_events(rows, mcap, since):
    """Notices of proposed sales (Form 144) not yet matched to a Form 4 sale. Executed notices are left to the Form 4
    events. Plan sales need a larger size to show, since their timing was fixed months earlier."""
    out = []
    for r in rows or []:
        t, filed = r.get("ticker"), _d(r.get("filed"))
        if t not in BY_TICKER or not filed or filed < since or r.get("status") == "executed":
            continue
        val = _num(r.get("value")) or 0
        bps = val / mcap[t] * 1e4 if mcap.get(t) else None
        scale = 4 if r.get("plan") else 1
        tier = ("high" if val >= 2.5e7 * scale or (bps or 0) >= 25 * scale else
                "medium" if val >= 5e6 * scale or (bps or 0) >= 5 * scale else
                "low" if val >= 1e6 * scale else None)
        if not tier:
            continue
        who = (r.get("person") or "An insider").strip()
        role = r.get("relationship") or "affiliate"
        plan = f"under a 10b5-1 plan adopted {r['plan_adopted'][0]}" if r.get("plan") and r.get("plan_adopted") else "with no 10b5-1 plan stated"
        size = money(val) + (f" ({bps:.1f} bps of market cap)" if bps is not None else "")
        sale = f", sale expected around {r['sale_date']}" if r.get("sale_date") else ""
        out.append(event("insider", "form144", t, filed, f"{who} ({clip(role, 40)}) filed to sell {money(val)}", tier,
                         f"Form 144 notice of a proposed sale of {size} {plan}{sale}; no matching Form 4 sale reported yet",
                         (r.get("accession"),), evidence=[sec_url(r.get("url")), app_link("Insiders", f"/app/companies/{t}/insiders")],
                         metrics={"value": round(val), "bps_mcap": round(bps, 2) if bps is not None else None, "shares": r.get("shares"),
                                  "plan": bool(r.get("plan")), "sale_date": r.get("sale_date"), "status": r.get("status")},
                         tags=["Form 144", "10b5-1" if r.get("plan") else "discretionary"]))
    return out


def short_interest_events(companies, since):
    """Large moves in short interest between FINRA settlement dates, sized by days to cover."""
    out = []
    for t, si in (companies or {}).items():
        d = _d((si or {}).get("date"))
        if t not in BY_TICKER or not d or d < since or si.get("chg") is None or not si.get("prev"):
            continue
        chg, dtc = si["chg"], si.get("dtc") or 0
        if chg >= 0.5 and dtc >= 8 or chg >= 1.0 and dtc >= 4:
            tier = "high"
        elif chg >= 0.3 and dtc >= 4 or chg <= -0.4 and dtc >= 6:
            tier = "medium"
        else:
            continue
        verb = "rose" if chg > 0 else "fell"
        out.append(event("short_interest", "si_change", t, si["date"], f"Short interest {verb} {pct(abs(chg), 0, sign=False)} to {si['short']:,.0f} shares",
                         tier, f"FINRA short interest {verb} {abs(chg) * 100:.0f}% between settlements with {dtc:.1f} days to cover; "
                               "rule: +50% with 8+ days or +100% with 4+ days is high, +30% with 4+ days or -40% with 6+ days is medium",
                         (si["date"],), evidence=[{"label": "FINRA short interest", "url": "https://www.finra.org/finra-data/browse-catalog/equity-short-interest/data", "kind": "web"},
                                                  app_link("Flows", f"/app/companies/{t}/insiders")],
                         metrics={"short": si["short"], "prev": si["prev"], "chg": round(chg, 4), "dtc": dtc},
                         tags=["FINRA", "short interest"]))
    return out


def comment_letter_events(rows, topic_names, since):
    """Released SEC staff letters on periodic reports and proxies: a letter with comments is medium (high after two
    or more rounds on the same filing), a review-complete letter is low."""
    out = []
    for r in rows or []:
        t, filed = r.get("ticker"), _d(r.get("filed"))
        if t not in BY_TICKER or not filed or filed < since or not r.get("periodic"):
            continue
        topics = [topic_names.get(x, x) for x in (r.get("topics") or [])][:4]
        what = f"{r.get('form') or 'filing'}" + (f" for the period ended {r['period']}" if r.get("period") else "")
        if r.get("kind") == "comments":
            n = r.get("n_comments") or 0
            tier = "medium"
            title = f"SEC staff comment letter on the {what}" + (f": {', '.join(topics)}" if topics else "")
            reason = (f"Corporation Finance released a review letter with {n} comment{'s' if n != 1 else ''}"
                      + (f" on {', '.join(topics).lower()}" if topics else "") + "; letters are public 20+ business days after a review closes")
        elif r.get("kind") == "review_complete":
            tier, title = "low", f"SEC staff completed its review of the {what}"
            reason = "Corporation Finance released the letter closing its review"
        else:
            continue
        out.append(event("sec_review", "comment_letter", t, filed, title, tier, reason, (r.get("accession"),),
                         evidence=[sec_url(r.get("url")), app_link("Filings", f"/app/companies/{t}/filings")],
                         metrics={"n_comments": r.get("n_comments"), "topics": r.get("topics") or [], "form": r.get("form"),
                                  "period": r.get("period"), "thread_status": r.get("thread_status")},
                         tags=["SEC review", r.get("form") or "filing"]))
    return out


def results_8k_events(rows, since, have_earnings):
    """Item 2.02 filings without a matched consensus surprise: results filed, low tier."""
    out = []
    for r in rows:
        t, filed = r.get("ticker"), _d(r.get("filed"))
        if t not in BY_TICKER or not filed or filed < since or "2.02" not in str(r.get("items") or ""):
            continue
        if (t, r.get("accession")) in have_earnings:
            continue
        out.append(event("earnings", "results_filed", t, filed, "Reported quarterly results (8-K Item 2.02)", "low",
                         "Results filed; no consensus estimate matched this report", r.get("accession"),
                         evidence=[sec_url(r.get("url"))], tags=["8-K", "Item 2.02"]))
    return out


# ---------------------------------------------------------------- filing changes (redlines)
# The comparison engine's keyword tags are broad ("restatement" also fires on "amended and restated" agreements), so
# high-tier rules read the changed text itself.
CREDIT_SIGNALS = {"default", "waiver", "covenant", "liquidity"}
MEDIUM_SIGNALS = {"investigation", "customer concentration", "impairment", "tariffs/trade", "supply", "accounting change", "litigation"}
TEXT_RULES = [
    ("going concern", re.compile(r"substantial doubt.{0,120}going concern|going concern.{0,120}substantial doubt", re.I | re.S)),
    ("material weakness", re.compile(r"\bmaterial weakness", re.I)),
    ("restatement", re.compile(r"(?<!amended and )\brestate(?:d|ment|ments)?\b(?!d? (?:and|limited|certificate|agreement|credit|bylaws|articles))(?=.{0,160}\b(?:previously issued|prior[- ]period|financial statements|error)\b)|non-reliance", re.I | re.S)),
    ("bridge loan", re.compile(r"\bbridge (?:loan|facility|credit|commitment)", re.I)),
    ("auditor change", re.compile(r"\b(?:dismiss\w*|engage\w*|appoint\w*|replac\w*)\b.{0,80}\b(?:independent registered public accounting firm|as (?:our|its) (?:new )?(?:independent )?auditor)", re.I | re.S)),
    ("delisting", re.compile(r"\bdelist\w*|\bdeficiency (?:notice|letter)|minimum bid price requirement", re.I)),
    ("event of default", re.compile(r"\b(?:event of default|forbearance|not in compliance with (?:the |certain |its )?(?:financial )?covenants?)", re.I)),
]
HIGH_TEXT = {"going concern", "material weakness", "restatement", "bridge loan", "auditor change", "delisting", "event of default"}


def change_text(c):
    """(changed text, full new paragraph). For modified paragraphs the changed text is the inserted wording."""
    kind = c.get("kind")
    if kind == "modified":
        ops = c.get("ops") or []
        added = " ".join(t for op, t in ops if op == "+")
        full = " ".join(t for op, t in ops if op in ("=", "+"))
        return added, full
    if kind == "removed":
        return c.get("old") or "", c.get("old") or ""
    return c.get("new") or "", c.get("new") or ""


def classify_change(c, section_title):
    """(tier, reason, tags, rule) for one paragraph-level change, or None when it is not notable."""
    kind = c.get("kind")
    if kind not in ("added", "removed", "modified"):
        return None
    text, _ = change_text(c)
    if len(text) < 25:
        return None
    sig = set(c.get("signals") or [])
    risk = section_title.lower().startswith("item 1a") or "risk factor" in section_title.lower()
    hits = [name for name, rx in TEXT_RULES if rx.search(text)]
    hi = [h for h in hits if h in HIGH_TEXT]
    if hi:
        rule = hi[0]
        if kind == "removed":
            return "medium", f"Removed {rule} language that was in the year-ago filing", sorted(sig) + hi, rule
        return "high", f"{'Added' if kind == 'added' else 'New wording with'} {rule} language not in the year-ago filing", sorted(sig) + hi, rule
    cr = sig & CREDIT_SIGNALS
    me = sig & MEDIUM_SIGNALS
    if kind == "added" and len(cr) >= 2:
        return "medium", f"New credit language ({', '.join(sorted(cr))})", sorted(sig), "credit"
    if risk and kind == "added" and (me or cr):
        return "medium", f"New risk-factor paragraph ({', '.join(sorted(me | cr))})", sorted(sig), "risk_added"
    if risk and kind == "added":
        return "low", "New risk-factor paragraph", sorted(sig), "risk_added_other"
    if risk and kind == "removed" and (me or cr):
        return "low", f"Removed risk-factor paragraph ({', '.join(sorted(me | cr))})", sorted(sig), "risk_removed"
    return None


def redline_events(t, doc, since):
    out = []
    for comp in (doc or {}).get("comparisons") or []:
        new, old = comp.get("new") or {}, comp.get("old") or {}
        filed = _d(new.get("filed"))
        if not filed or filed < since:
            continue
        form = new.get("form") or comp.get("form")
        link = [sec_url(new.get("url")), app_link("Redline", f"/app/companies/{t}/redline")]
        cands = []
        for s in comp.get("sections") or []:
            title = s.get("title") or s.get("id") or ""
            for c in s.get("changes") or []:
                got = classify_change(c, title)
                if not got:
                    continue
                tier, reason, tags, rule = got
                text, full = change_text(c)
                verb = {"added": "Added", "removed": "Removed", "modified": "Changed"}.get(c.get("kind"), "Changed")
                cands.append((TIER_ORDER[tier], -(c.get("score") or 0), rule, event(
                    "filing_change", rule, t, filed, f"{verb} in {form} {title}: {first_sentence(text, 150)}", tier,
                    reason + f" (compared with the {old.get('form') or form} filed {old.get('filed')})",
                    (new.get("accn"), s.get("id"), hashlib.sha1(text.encode()).hexdigest()[:12]),
                    evidence=[{"label": title, "excerpt": clip(text if c.get("kind") != "modified" else full, 420),
                               "url": new.get("url") if c.get("kind") != "removed" else old.get("url"), "kind": "sec"}] + link,
                    metrics={"form": form, "filed": str(filed), "old_filed": old.get("filed"), "section": title, "kind": c.get("kind"), "score": c.get("score")},
                    tags=tags + [form])))
        cands.sort(key=lambda x: (x[0], x[1]))
        keep, per_tier, per_rule = [], {"high": 0, "medium": 0, "low": 0}, {}
        for _, _, rule, e in cands:
            cap = {"high": 4, "medium": 3, "low": 2}[e["tier"]]
            if per_tier[e["tier"]] < cap and per_rule.get(rule, 0) < 2:
                keep.append(e)
                per_tier[e["tier"]] += 1
                per_rule[rule] = per_rule.get(rule, 0) + 1
        out += keep
        tot = comp.get("totals") or {}
        top = sorted((s for s in comp.get("sections") or []), key=lambda s: -(s.get("score") or 0))[:3]
        out.append(event("filing_change", "summary", t, filed,
                         f"{form} vs year-ago: {tot.get('added', 0)} paragraphs added, {tot.get('removed', 0)} removed, {tot.get('modified', 0)} changed",
                         "low", "Summary of the year-over-year comparison", (new.get("accn"), "summary"), evidence=link,
                         detail="Most-changed sections: " + ", ".join(s.get("title") or s.get("id") for s in top) if top else None,
                         metrics={**{k: tot.get(k) for k in ("added", "removed", "modified", "numbers")}, "form": form, "old_filed": old.get("filed")},
                         tags=[form]))
    return out


# ---------------------------------------------------------------- insiders
RX_ENTITY = re.compile(r"\b(LLC|L\.?L\.?C|LP|L\.P\.|INC|CORP|CORPORATION|CO\.|COMPANY|TRUST|FUND|FUNDS|PARTNERS|PARTNERSHIP|CAPITAL|HOLDINGS?|"
                       r"ADVISORS?|ADVISERS?|MANAGEMENT|LTD|LIMITED|ASSOCIATES|FOUNDATION|GROUP|BANK|PLC|INVESTMENTS?|VENTURES|S\.A\.|N\.V\.|GMBH|AG)\b", re.I)
RX_TOP = re.compile(r"\b(CEO|chief executive|CFO|chief financial|president|chair(man|woman|person)?)\b", re.I)
RX_OFFICER = re.compile(r"\b(officer|CEO|CFO|COO|CTO|president|chief|EVP|SVP|VP|vice president|treasurer|secretary|controller|counsel)\b", re.I)


def owner_id(name):
    return re.sub(r"\s*\(\+\d+\)\s*$", "", str(name or "")).strip().upper()


def owner_kind(name, role):
    """'top' (CEO/CFO/president/chair), 'officer', 'director', or 'holder' (entities and 10% owners)."""
    role = str(role or "")
    if RX_ENTITY.search(owner_id(name)):
        return "holder"
    if RX_TOP.search(role):
        return "top"
    if RX_OFFICER.search(role):
        return "officer"
    if "director" in role.lower():
        return "director"
    return "holder"


def _week(d):
    return d - dt.timedelta(days=d.weekday())


def insider_events(t, bundle, mcap, since, today):
    rows = [r for r in (bundle or {}).get("rows") or [] if r.get("code") in ("P", "S") and not r.get("derivative") and _d(r.get("tx_date"))]
    out = []
    v = lambda r: (_num(r.get("shares")) or 0) * (_num(r.get("price")) or 0)
    year_ago = today - dt.timedelta(days=365)
    prior_buys = [r for r in rows if r["code"] == "P" and year_ago <= _d(r["tx_date"]) < since]
    groups = {}
    for r in rows:
        d = _d(r["tx_date"])
        if d < since:
            continue
        groups.setdefault((owner_id(r["owner"]), r["code"], _week(d)), []).append(r)
    buyers30 = {}
    for (who, code, wk), rs in groups.items():
        kind = owner_kind(rs[0]["owner"], rs[0].get("role"))
        val = sum(v(r) for r in rs)
        plan = [r for r in rs if r.get("plan_10b5")]
        disc = [r for r in rs if not r.get("plan_10b5")]
        if code == "S" and not disc:
            continue
        if code == "S":
            val = sum(v(r) for r in disc)
        bps = val / mcap * 1e4 if mcap else None
        last = max(rs, key=lambda r: r["tx_date"])
        held_after = _num(min(disc or rs, key=lambda r: r["tx_date"]).get("owned_after"))
        sh = sum(_num(r.get("shares")) or 0 for r in (disc if code == "S" else rs))
        frac = sh / (sh + held_after) if code == "S" and held_after is not None and sh + held_after > 0 else None
        role = rs[0].get("role") or ""
        price_lo = min(_num(r.get("price")) or 0 for r in rs)
        price_hi = max(_num(r.get("price")) or 0 for r in rs)
        px = f"${price_lo:,.2f}" if abs(price_hi - price_lo) < 0.01 else f"${price_lo:,.2f} to ${price_hi:,.2f}"
        joint = sorted({j for r in rs for j in (r.get("joint") or [])})
        who_name = owner_id(rs[0]["owner"]).title()
        size = f"{money(val)}" + (f" ({bps:.1f} bps of market cap)" if bps is not None else "")
        if code == "P":
            if kind == "holder":
                tier = "high" if val >= 1e8 or (bps or 0) >= 50 else "medium" if val >= 1e7 or (bps or 0) >= 10 else "low"
                reason = f"Large-holder purchase of {size}; holder tier rules: $100M or 50 bps high, $10M or 10 bps medium"
                sub = "holder_buy"
            else:
                tier = ("high" if (kind == "top" and val >= 2.5e5) or (val >= 1e6 and (bps is None or bps >= 1)) else
                        "medium" if val >= 1e5 else "low")
                rarity = "first open-market buy at this company in the prior 12 months" if not prior_buys else f"{len(prior_buys)} open-market buys at this company in the prior 12 months"
                reason = f"{'CEO/CFO/chair' if kind == 'top' else kind.title()} open-market purchase of {size}; {rarity}"
                sub = "insider_buy"
                buyers30.setdefault(who, []).append(_d(last["tx_date"]))
            title = f"{who_name} ({clip(role, 40)}) bought {money(val)} at {px}"
        else:
            if kind == "holder":
                tier = "high" if (bps or 0) >= 100 else "medium" if val >= 5e7 or (bps or 0) >= 25 else "low"
                reason = f"Large-holder sale of {size} outside a 10b5-1 plan"
                sub = "holder_sell"
            else:
                tier = ("high" if (kind == "top" and ((frac or 0) >= 0.5 or val >= 2.5e7)) or (val >= 5e7) else
                        "medium" if val >= 5e6 or (frac or 0) >= 0.25 else "low")
                reason = (f"Discretionary sale (not under a 10b5-1 plan) of {size}"
                          + (f", {frac * 100:.0f}% of the seller's directly reported holding" if frac is not None else ""))
                sub = "insider_sell"
            title = f"{who_name} ({clip(role, 40)}) sold {money(val)} at {px}"
        out.append(event("insider", sub, t, last["tx_date"], title, tier, reason, (who, code, str(wk)),
                         evidence=[sec_url(f"https://www.sec.gov/Archives/edgar/data/{BY_TICKER.get(t, {}).get('cik', '')}/{str(last.get('accession', '')).replace('-', '')}/")
                                   if last.get("accession") else None, app_link("Insiders", f"/app/companies/{t}/insiders")],
                         detail=(f"Also reported by {', '.join(joint)}" if joint else None) or (f"Plus {money(sum(v(r) for r in plan))} under 10b5-1 plans" if plan else None),
                         metrics={"value": round(val), "bps_mcap": round(bps, 2) if bps is not None else None, "transactions": len(rs),
                                  "owner_kind": kind, "role": role, "fraction_of_holding": round(frac, 3) if frac is not None else None,
                                  "plan_value": round(sum(v(r) for r in plan)) if plan else 0},
                         tags=["Form 4", "discretionary" if code == "P" or disc else "10b5-1"]))
    # Cluster: three or more distinct officers/directors buying within 30 days.
    dates = sorted((d, w) for w, ds in buyers30.items() for d in ds)
    for i, (d0, _) in enumerate(dates):
        win = {w for d, w in dates if d0 <= d <= d0 + dt.timedelta(days=30)}
        if len(win) >= 3:
            end = max(d for d, w in dates if d0 <= d <= d0 + dt.timedelta(days=30))
            out.append(event("insider", "cluster_buy", t, end, f"{len(win)} insiders bought within 30 days", "high",
                             f"Cluster rule: 3 or more different officers or directors making open-market purchases within 30 days ({', '.join(sorted(w.title() for w in win))})",
                             (str(d0), "cluster"), evidence=[app_link("Insiders", f"/app/companies/{t}/insiders")],
                             metrics={"buyers": len(win)}, tags=["Form 4", "cluster"]))
            break
    return out


# ---------------------------------------------------------------- analysts, earnings, consensus
def analyst_events(t, est, since):
    by_day = {}
    for a in (est or {}).get("actions") or []:
        d = _d(a.get("date"))
        if d and d >= since:
            by_day.setdefault(d, []).append(a)
    out = []
    for d, acts in by_day.items():
        up = [a for a in acts if "upgrade" in str(a.get("action")).lower()]
        down = [a for a in acts if "downgrade" in str(a.get("action")).lower()]
        init = [a for a in acts if "initiat" in str(a.get("action")).lower()]
        moves = [(_num(a.get("adj_pt_current")) or _num(a.get("pt_current")), _num(a.get("adj_pt_prior")) or _num(a.get("pt_prior"))) for a in acts]
        moves = [c / p - 1 for c, p in moves if c and p]
        raises, cuts = sum(m > 0.001 for m in moves), sum(m < -0.001 for m in moves)
        med = sorted(moves)[len(moves) // 2] if moves else None
        if len(up) >= 2 or len(down) >= 2:
            tier, why = "high", f"{max(len(up), len(down))} {'upgrades' if len(up) >= 2 else 'downgrades'} on one day"
        elif up or down:
            tier, why = "medium", f"Rating change ({len(up)} upgrade{'s' if len(up) != 1 else ''}, {len(down)} downgrade{'s' if len(down) != 1 else ''})"
        elif raises >= 4 or cuts >= 4:
            tier, why = "medium", f"{max(raises, cuts)} price-target {'raises' if raises >= cuts else 'cuts'} on one day (rule: 4 or more in the same direction)"
        else:
            tier, why = "low", "Analyst actions without a rating change"
        parts = []
        if up:
            parts.append(f"{len(up)} upgrade{'s' if len(up) > 1 else ''} ({', '.join(a.get('firm') or '?' for a in up[:3])})")
        if down:
            parts.append(f"{len(down)} downgrade{'s' if len(down) > 1 else ''} ({', '.join(a.get('firm') or '?' for a in down[:3])})")
        if init:
            parts.append(f"{len(init)} initiation{'s' if len(init) > 1 else ''}")
        if raises or cuts:
            parts.append(f"{raises} target raise{'s' if raises != 1 else ''}, {cuts} cut{'s' if cuts != 1 else ''}" + (f", median move {pct(med)}" if med is not None else ""))
        if not parts:
            continue
        out.append(event("analyst", "actions", t, d, "Analysts: " + "; ".join(parts), tier, why, str(d),
                         evidence=[{"label": "Analyst actions", "url": (acts[0].get("url") or "https://www.benzinga.com/"), "kind": "web"},
                                   app_link("Estimates", f"/app/companies/{t}/estimates")],
                         detail="; ".join(f"{a.get('firm')}: {a.get('action')}" + (f" {a.get('rating_prior')}→{a.get('rating_current')}" if a.get('rating_prior') != a.get('rating_current') else "")
                                          + (f", target {a.get('pt_prior')}→{a.get('pt_current')}" if a.get('pt_current') else "") for a in acts[:5]),
                         metrics={"upgrades": len(up), "downgrades": len(down), "initiations": len(init), "target_raises": raises, "target_cuts": cuts,
                                  "median_target_move": round(med, 4) if med is not None else None, "actions": len(acts)},
                         tags=["analysts"]))
    return out


def earnings_events(t, est, since):
    out, seen = [], set()
    for s in (est or {}).get("surprises") or []:
        rep = s.get("report") or {}
        filed = _d(rep.get("filed"))
        if not filed or filed < since:
            continue
        seen.add((t, rep.get("accn")))
        act, est_eps, sp = _num(s.get("eps_actual")), _num(s.get("eps_estimate")), _num(s.get("surprise_pct"))
        meaningful = sp is not None and abs(sp) <= 3 and est_eps is not None and abs(est_eps) >= 0.02
        r = s.get("reaction") or {}
        r1, spy = _num(r.get("r1")), _num(r.get("spy1"))
        rel = r1 - spy if r1 is not None and spy is not None else None
        if rel is not None and abs(rel) >= 0.10:
            tier, why = "high", f"Next-day move of {pct(r1)} vs S&P 500 {pct(spy)} (rule: 10 points or more away from the index)"
        elif meaningful and abs(sp) >= 0.10:
            tier, why = "medium", f"EPS surprise of {pct(sp)} (rule: 10% or more)"
        elif rel is not None and abs(rel) >= 0.05:
            tier, why = "medium", f"Next-day move of {pct(r1)} vs S&P 500 {pct(spy)} (rule: 5 points or more away from the index)"
        else:
            tier, why = "low", "Surprise under 10% and next-day move within 5 points of the index"
        rev = (s.get("revenue") or {}).get("v")
        title = (f"Q ending {s.get('quarter_end')}: EPS {act:.2f} vs {est_eps:.2f} consensus"
                 + (f" ({pct(sp)})" if meaningful else " (surprise % not meaningful: estimate near zero)" if sp is not None else "")) if act is not None and est_eps is not None else f"Reported results for the quarter ending {s.get('quarter_end')}"
        out.append(event("earnings", "surprise", t, filed, title, tier, why, rep.get("accn") or s.get("quarter_end"),
                         evidence=[sec_url(rep.get("url")), app_link("Earnings", f"/app/companies/{t}/earnings")],
                         detail=(f"Revenue {money(rev)}. " if rev else "") + (f"Next trading day {pct(r1)}, S&P 500 {pct(spy)}." if r1 is not None else ""),
                         metrics={"eps_actual": act, "eps_estimate": est_eps, "surprise_pct": sp if meaningful else None, "r1": r1, "spy1": spy, "revenue": rev},
                         tags=["earnings"]))
    return out, seen


def consensus_events(t, est, today):
    snaps = sorted((est or {}).get("snapshots") or [], key=lambda s: s.get("as_of") or "")
    if len(snaps) < 2:
        return []
    last = snaps[-1]
    ref = None
    for s in reversed(snaps[:-1]):
        d0, d1 = _d(s.get("as_of")), _d(last.get("as_of"))
        if d0 and d1 and (d1 - d0).days >= 25:
            ref = s
            break
    if not ref:
        return []
    out = []
    for code, label in (("0q", "current quarter"), ("0y", "current year"), ("+1y", "next year")):
        a, b = ((ref.get("p") or {}).get(code) or {}), ((last.get("p") or {}).get(code) or {})
        if a.get("end") != b.get("end"):
            continue
        e0, e1 = _num(a.get("eps")), _num(b.get("eps"))
        if not e0 or e1 is None or abs(e0) < 0.05:
            continue
        ch = e1 / e0 - 1
        if abs(ch) < 0.03:
            continue
        tier = "high" if abs(ch) >= 0.15 else "medium" if abs(ch) >= 0.05 else "low"
        out.append(event("analyst", "eps_revision", t, last.get("as_of"), f"Consensus EPS for the {label} {'raised' if ch > 0 else 'cut'} {pct(ch)} since {ref.get('as_of')}",
                         tier, "Consensus revision rule: 15% high, 5% medium, 3% low", (code, last.get("as_of")),
                         evidence=[app_link("Estimates", f"/app/companies/{t}/estimates")],
                         detail=f"{e0:.2f} to {e1:.2f} ({b.get('eps_n') or '?'} estimates)", metrics={"from": e0, "to": e1, "change": round(ch, 4), "period": code},
                         tags=["consensus"]))
    return out


# ---------------------------------------------------------------- federal contracts
def contract_events(t, contracts, first_seen):
    rows = {r.get("fy"): r for r in (contracts or {}).get("rows") or [] if r.get("fy")}
    full = sorted(fy for fy, r in rows.items() if not r.get("partial"))
    if len(full) < 2:
        return []
    f1 = full[-1]
    a0, a1 = _num(rows.get(f1 - 1, {}).get("amount")) or 0, _num(rows[f1].get("amount")) or 0
    if max(a0, a1) < 1e8 or a0 < 2.5e7:
        return []
    # A prior year far below the company's own history is usually a recipient-name matching gap in USAspending
    # (renamed or reorganized subsidiaries), not a real swing: skip rather than report a fake surge.
    earlier = sorted(_num(rows[fy].get("amount")) or 0 for fy in full[:-2][-4:])
    if earlier:
        med = earlier[len(earlier) // 2]
        if med and (a0 < 0.1 * med or a1 > 20 * max(med, a0)):
            return []
    ch = a1 / a0 - 1
    if abs(ch) < 0.25:
        return []
    big = max(a0, a1)
    tier = "high" if abs(ch) >= 0.5 and big >= 1e9 else "medium" if big >= 2.5e8 else "low"
    start, end = dt.date(f1 - 1, 10, 1), dt.date(f1, 9, 30)
    awards = [a for a in (contracts or {}).get("awards") or [] if (_d(a.get("start")) or dt.date.min) >= start and (_d(a.get("start")) or dt.date.max) <= end]
    search = (contracts or {}).get("search") or []
    q = urllib.parse.quote(search[0]) if search else ""
    ev_ = [{"label": f"{a.get('sub_agency') or a.get('agency')}: {clip(a.get('description'), 90)}", "excerpt": f"{money(a.get('amount'))}, {a.get('start')} to {a.get('end')}",
            "url": a.get("url"), "kind": "web"} for a in awards[:3]]
    ev_.append({"label": "USAspending.gov", "url": f"https://www.usaspending.gov/search/?hash=&keyword={q}" if q else "https://www.usaspending.gov/search", "kind": "web"})
    ev_.append(app_link("Alt data", f"/app/companies/{t}/alt-data"))
    return [event("contracts", "fy_change", t, first_seen or end, f"Federal prime contract obligations FY{f1} {money(a1)}, {pct(ch, 0)} vs FY{f1 - 1} ({money(a0)})",
                  tier, "Obligation-change rule: 50% and $1B high, $250M medium; obligations are lumpy, multi-year awards can land in one fiscal year",
                  (f1,), evidence=ev_, detail=f"Fiscal year Oct {f1 - 1} to Sep {f1}.", metrics={"fy": f1, "amount": a1, "prior": a0, "change": round(ch, 4)},
                  tags=["USAspending"])]


# ---------------------------------------------------------------- dividends, IPOs, IR documents
def dividend_events(rows, since):
    out = []
    for r in rows or []:
        t = r.get("ticker")
        if t not in BY_TICKER:
            continue
        hist = sorted((h for h in r.get("history") or [] if isinstance(h, list) and len(h) == 2), key=lambda h: h[0])
        for dcl in r.get("declared") or []:
            filed = _d(dcl.get("filed"))
            amt = _num(dcl.get("amount"))
            if not filed or filed < since or not amt:
                continue
            ex = dcl.get("ex_date") or ""
            prev = [h for h in hist if h[0] < ex] if ex else hist
            prior = _num(prev[-1][1]) if prev else None
            kind = dcl.get("kind") or "regular"
            if kind != "regular":
                tier, sub, title = "medium", "special", f"Declared a {kind} dividend of ${amt:.4g} per share"
            elif prior is None:
                tier, sub, title = "medium", "initiation", f"Declared a dividend of ${amt:.4g} per share (no dividend in the price history)"
            elif amt > prior * 1.001:
                tier, sub, title = "medium", "increase", f"Raised dividend {pct(amt / prior - 1)} to ${amt:.4g} per share"
            elif amt < prior * 0.999:
                tier, sub, title = "high", "cut", f"Cut dividend {pct(amt / prior - 1)} to ${amt:.4g} per share"
            else:
                continue
            out.append(event("dividend", sub, t, filed, title, tier, f"Dividend {sub} vs the prior paid amount (${prior:.4g})" if prior else f"Dividend {sub}",
                             (dcl.get("accn"), amt), evidence=[{"label": "Declaration", "excerpt": clip(dcl.get("quote"), 300), "url": dcl.get("url"), "kind": "sec"}],
                             detail=f"Ex-date {dcl.get('ex_date')}, payable {dcl.get('payable')}.", metrics={"amount": amt, "prior": prior},
                             tags=["dividend"]))
    return out


def ipo_events(rows, since, today):
    out = []
    for r in rows or []:
        sym, name = r.get("symbol"), r.get("name")
        label = f"{name}" + (f" ({sym})" if sym else "")
        url = ((r.get("price") or {}).get("url")) or (((r.get("filings") or [{}])[-1] or {}).get("url") if r.get("filings") else None)
        pr = _d(r.get("priced"))
        if pr and pr >= since:
            p = (r.get("price") or {}).get("value")
            fd = r.get("first_day") or {}
            out.append(event("ipo", "priced", None, pr, f"IPO priced: {label}" + (f" at ${p:g}" if p else ""), "low" if r.get("spac") else "medium",
                             "IPO pricing (SPACs low tier)", (r.get("cik"), "priced"),
                             evidence=[sec_url(url), app_link("IPO calendar", "/app/calendars")],
                             detail=(f"First day close {pct(fd.get('close_ret'))} vs offer." if fd.get("close_ret") is not None else None),
                             metrics={"price": p, "spac": bool(r.get("spac")), "cik": r.get("cik"), "symbol": sym}, tags=["IPO"]))
        lock = pr + dt.timedelta(days=180) if pr else None
        if lock and today <= lock <= today + dt.timedelta(days=30) and not r.get("spac"):
            out.append(event("ipo", "lockup", None, lock, f"Typical 180-day lockup ends for {label}", "low",
                             "Lockups commonly run 180 days from pricing; check the prospectus for the actual term", (r.get("cik"), "lockup"),
                             evidence=[sec_url(url)], metrics={"priced": str(pr), "symbol": sym}, tags=["IPO", "lockup"], upcoming=True))
        wd = _d(r.get("withdrawn"))
        if wd and wd >= since:
            out.append(event("ipo", "withdrawn", None, wd, f"IPO withdrawn: {label}", "low", "Registration withdrawn", (r.get("cik"), "withdrawn"),
                             evidence=[app_link("IPO calendar", "/app/calendars")], metrics={"symbol": sym}, tags=["IPO"]))
        ff = _d(r.get("first_filed"))
        if ff and ff >= since:
            out.append(event("ipo", "filed", None, ff, f"New IPO filing: {label}", "low", "First registration statement in the window", (r.get("cik"), "filed"),
                             evidence=[app_link("IPO calendar", "/app/calendars")], metrics={"symbol": sym, "spac": bool(r.get("spac"))}, tags=["IPO"]))
    return out


DOC_LABEL = {"presentation": "Investor presentation", "transcript": "Call transcript", "remarks": "Prepared remarks",
             "supplement": "Financial supplement", "release": "Press release", "other": "IR document"}


def ir_doc_events(rows, since):
    groups = {}
    for r in rows or []:
        t, d = r.get("ticker"), _d(r.get("published"))
        if t not in BY_TICKER or not d or d < since:
            continue
        web = "sec.gov" not in str(r.get("url") or "")
        if not web and r.get("doc_type") not in ("presentation", "transcript", "remarks"):
            continue
        groups.setdefault((t, d), []).append(r)
    out = []
    for (t, d), rs in groups.items():
        kinds = sorted({DOC_LABEL.get(r.get("doc_type"), "IR document") for r in rs})
        tier = "medium" if any(r.get("doc_type") in ("transcript", "presentation") and "sec.gov" not in str(r.get("url")) for r in rs) else "low"
        def name(r):
            ttl = str(r.get("title") or "")
            if ttl.startswith("8-K "):
                return f"{DOC_LABEL.get(r.get('doc_type'), 'IR document')} (8-K exhibit)"
            return clip(ttl, 90) or DOC_LABEL.get(r.get("doc_type"), "IR document")
        out.append(event("ir_doc", "new", t, d, f"New on investor relations: {', '.join(kinds)}", tier,
                         "Investor-site decks and transcripts are medium tier; SEC-exhibit decks and other documents low",
                         tuple(sorted(r.get("url") or "" for r in rs)),
                         evidence=[{"label": name(r), "url": r.get("url"), "kind": "ir" if "sec.gov" not in str(r.get("url")) else "sec"} for r in rs[:4]]
                                  + [app_link("IR documents", f"/app/companies/{t}/ir")],
                         metrics={"documents": len(rs), "types": kinds}, tags=["IR"]))
    return out


# ---------------------------------------------------------------- technical events with base rates
TECH_LABEL = {"golden_cross": "Golden cross (50-day above 200-day)", "death_cross": "Death cross (50-day below 200-day)",
              "high_52w": "New 52-week high", "low_52w": "New 52-week low", "gap_up": "Gap up", "gap_down": "Gap down",
              "rsi_oversold": "RSI oversold", "rsi_overbought": "RSI overbought", "macd_bull": "MACD bullish cross", "macd_bear": "MACD bearish cross",
              "squeeze_bull": "Volatility squeeze released upward", "squeeze_bear": "Volatility squeeze released downward",
              "st_up": "Supertrend turned up", "st_down": "Supertrend turned down", "rsi_bull_div": "RSI bullish divergence", "rsi_bear_div": "RSI bearish divergence"}


def base_rate(sig, signals):
    s = ((signals or {}).get("signals") or {}).get(sig) or {}
    h = s.get("60") or {}
    if not h:
        return None
    ci = h.get("excess_ci") or [None, None]
    sig_flag = "above zero" if ci[0] is not None and ci[0] > 0 else "below zero" if ci[1] is not None and ci[1] < 0 else "not distinguishable from zero"
    return {"horizon_days": 60, "excess": h.get("excess"), "excess_ci": ci, "hit": h.get("hit"), "n": h.get("n"), "months": h.get("months"),
            "verdict": sig_flag, "since": s.get("first"), "caveat": "Past frequencies across currently tracked companies (survivorship-biased), not forecasts"}


def technical_events(states, signals, since):
    out = []
    for r in (states or {}).get("rows") or []:
        t = r.get("t")
        if t not in BY_TICKER:
            continue
        latest = {}
        for sig, d in r.get("events") or []:
            if sig in TECH_LABEL and _d(d) and _d(d) >= since and d > latest.get(sig, ""):
                latest[sig] = d
        for sig, d in latest.items():
            br = base_rate(sig, signals)
            why = "Technical events are low tier"
            if br and br.get("excess") is not None:
                why += f"; after past {TECH_LABEL[sig].lower()} events the 60-day excess return averaged {pct(br['excess'])} (95% range {pct(br['excess_ci'][0])} to {pct(br['excess_ci'][1])}, n={br['n']:,}), {br['verdict']}"
            out.append(event("technical", sig, t, d, f"{TECH_LABEL[sig]} at ${_num(r.get('close')) or 0:,.2f}" if sig in ("high_52w", "low_52w") else TECH_LABEL[sig],
                             "low", why, (sig, d), evidence=[app_link("Technicals", f"/app/markets?view=indicators&symbol={t}")],
                             metrics={"close": r.get("close"), "rsi": r.get("rsi"), "vs200": r.get("vs200"), "from_hi": r.get("from_hi")},
                             base=br, tags=["technical"]))
    return out


# ---------------------------------------------------------------- macro releases
MACRO = {"BLS:CPI": ("medium", "cm_cpi", "CPI"), "BLS:EMPSIT": ("medium", "cm_payems", "payrolls"), "FOMC:DECISION": ("medium", "cm_ffr", "the fed funds rate"),
         "BEA:GDP": ("medium", None, None), "BLS:PPI": ("low", None, None), "BLS:JOLTS": ("low", None, None), "CEN:RETAIL": ("low", None, None),
         "FOMC:MINUTES": ("low", "cm_ffr", "the fed funds rate"), "BLS:ECI": ("low", None, None), "BEA:PIO": ("low", None, None)}


def macro_events(releases, corr, since, today):
    out = []
    horizon = today + dt.timedelta(days=7)
    for r in releases or []:
        code, d = r.get("release"), _d(r.get("release_date"))
        if code not in MACRO or not d or d < since or d > horizon:
            continue
        tier, col, what = MACRO[code]
        names = []
        if col and corr.get(col):
            ranked = sorted(corr[col].items(), key=lambda kv: -abs(kv[1]))[:6]
            names = [{"t": t, "corr": round(c, 2)} for t, c in ranked]
        out.append(event("macro", code.lower().replace(":", "_"), None, d, f"{'Upcoming' if d > today else 'Released'}: {r.get('title')}", tier,
                         "Key macro releases are medium tier, secondary releases low", (code, str(d)),
                         evidence=[{"label": r.get("provider") or "Release calendar", "url": r.get("source_url"), "kind": "web"}],
                         detail=(f"Most correlated with {what} over 60 months: " + ", ".join(f"{n['t']} ({n['corr']:+.2f})" for n in names)) if names else None,
                         metrics={"release": code, "time": r.get("release_time"), "sensitive": names}, tags=["macro"], upcoming=d > today))
    return out


# ---------------------------------------------------------------- ranking and assembly
def magnitude(e):
    """The event's own size, for ordering within a tier: share of market cap, then dollars, then relative change."""
    m = e.get("metrics") or {}
    num = lambda k: _num(m.get(k))
    if num("bps_mcap") is not None:
        return abs(num("bps_mcap"))
    for k in ("value", "amount"):
        if num(k):
            return abs(num(k)) / 1e8
    for k in ("change", "surprise_pct", "median_target_move"):
        if num(k) is not None:
            return abs(num(k)) * 10
    return 0.0


def rank(events):
    """Tier first, then the event's own magnitude, then recency. Returns a new list with `order` set."""
    # Within a tier, larger first, then newer.
    out = sorted(events, key=lambda e: (TIER_ORDER.get(e["tier"], 3), -round(magnitude(e), 3), -(_d(e["date"]) or dt.date.min).toordinal()))
    for i, e in enumerate(out):
        e["order"] = i
    return out


def slim(e):
    """Feed copy of an event: shorter excerpts (the per-company file keeps the full evidence)."""
    return {**e, "evidence": [{**x, "excerpt": clip(x["excerpt"], 220)} if x.get("excerpt") else x for x in e.get("evidence") or []]}


def counts(events):
    by_type, by_tier = {}, {}
    for e in events:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        by_tier[e["tier"]] = by_tier.get(e["tier"], 0) + 1
    return {"by_type": by_type, "by_tier": by_tier, "total": len(events)}


def stamp(events, seen, now):
    """first_seen: when this job first published the event (used for 'new since your last visit')."""
    for e in events:
        e["first_seen"] = seen.setdefault(e["id"], now)
    return events


# ---------------------------------------------------------------- job
def _get_state(sb):
    raw = sb.get_object("raw", STATE_PATH)
    if not raw:
        return {}
    try:
        return json.loads(gzip.decompress(raw))
    except OSError:
        return json.loads(raw)


def _put_state(sb, state):
    sb.put_object("raw", STATE_PATH, gzip.compress(json.dumps(state, separators=(",", ":"), default=str).encode()), "application/gzip", "no-store")


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        return e


def _screener_frame(scr):
    if not scr:
        return {}, {}
    cols = scr.get("cols") or []
    ix = {c: i for i, c in enumerate(cols)}
    mcap, corr = {}, {c: {} for c in ("cm_cpi", "cm_payems", "cm_ffr")}
    for row in scr.get("rows") or []:
        t = row[ix["ticker"]]
        if "mcap" in ix and _num(row[ix["mcap"]]):
            mcap[t] = _num(row[ix["mcap"]])
        sp = "sp500" in ix and row[ix["sp500"]] == 1
        for c in corr:
            if sp and c in ix and _num(row[ix[c]]) is not None:
                corr[c][t] = _num(row[ix[c]])
    return mcap, corr


def changes_job(sb, today=None, time_budget=700, log=print, tickers=None, workers=16):
    t0 = time.time()
    deadline = t0 + time_budget
    today = today or dt.date.today()
    now = now_iso()
    since_c, since_f = today - dt.timedelta(days=COMPANY_DAYS), today - dt.timedelta(days=FEED_DAYS)
    with Run(sb, "changes") as run:
        state = _get_state(sb)
        state.setdefault("version", VERSION)
        cache = {k: state.setdefault(k, {}) for k in ("redlines", "insiders", "estimates", "contracts", "uploaded")}
        seen = state.setdefault("seen", {})
        first_seen = state.setdefault("contract_first_seen", {})

        def pub(path):
            try:
                return sb.get_json(path)
            except Exception as e:
                log("read failed", path, str(e)[:120])
                return None
        with ThreadPoolExecutor(8) as ex:
            names = ["screener.json", "quant/cross.json", "redlines/index.json", "estimates/index.json", "meta/insiders.json",
                     "tech/states.json", "tech/signals.json", "calendars/dividends.json", "calendars/ipos.json", "macro/releases.json",
                     "form144/recent.json", "flows/short_interest.json", "comments/recent.json"]
            loaded = dict(zip(names, ex.map(pub, names)))
        mcap, corr = _screener_frame(loaded["screener.json"])
        qc = loaded["quant/cross.json"] or {}
        if qc.get("cols"):
            qi = {c: i for i, c in enumerate(qc["cols"])}
            for row in qc.get("rows") or []:
                if row[qi["sym"]] not in mcap and _num(row[qi["mcap"]]):
                    mcap[row[qi["sym"]]] = _num(row[qi["mcap"]])
        universe = [t for t in (tickers or BY_TICKER)]
        rl_ix = (loaded["redlines/index.json"] or {}).get("companies") or {}
        es_ix = {e["ticker"]: e for e in (loaded["estimates/index.json"] or {}).get("companies") or []}
        in_ix = (loaded["meta/insiders.json"] or {}).get("companies") or {}

        filings = sb.select("filings", f"select=ticker,accession,filed,items,description,url&form=eq.8-K&filed=gte.{since_c.isoformat()}&order=filed.desc,accession.asc")
        irdocs = sb.select("ir_documents", f"select=ticker,doc_type,title,published,url&published=gte.{since_f.isoformat()}&order=published.desc,id.asc")

        # Per-company sources, refreshed only when their index fingerprint changed.
        def rl_key(t):
            v = rl_ix.get(t) or {}
            comps = v.get("comparisons") or []
            if not any((_d(c.get("filed")) or dt.date.min) >= since_c for c in comps):
                return None
            return v.get("generated") or "|".join(c.get("filed") or "" for c in comps)

        def in_key(t):
            v = in_ix.get(t)
            return json.dumps([v.get("last"), v.get("w90")], sort_keys=True) if v else None

        def es_key(t):
            v = es_ix.get(t)
            if not v:
                return None
            last = (v.get("earnings") or {}).get("last") or {}
            return json.dumps([v.get("last_action"), v.get("consensus_as_of"), (last.get("report") or {}).get("filed")])

        todo = []
        for t in universe:
            for src, keyf, path in (("redlines", rl_key, f"redlines/{t}.json"), ("insiders", in_key, f"insiders/{t}.json"), ("estimates", es_key, f"estimates/{t}.json")):
                k = keyf(t)
                if k is None:
                    cache[src].pop(t, None)
                    continue
                if (cache[src].get(t) or {}).get("key") != k:
                    todo.append((src, t, k, path))
        # Contracts change once a fiscal year; refresh a quarter of the universe per run plus anything never read.
        cursor = int(state.get("contracts_cursor", 0))
        slice_ = [t for i, t in enumerate(universe) if (i + cursor) % 4 == 0 or t not in cache["contracts"]]
        state["contracts_cursor"] = (cursor + 1) % 4
        for t in slice_:
            todo.append(("contracts", t, None, f"altdata/{t}.json"))
        log(f"changes: {len(todo)} per-company reads ({sum(1 for x in todo if x[0] == 'contracts')} contracts)")

        def work(item):
            src, t, k, path = item
            if time.time() > deadline - 180:
                return item, "deadline"
            doc = _safe(pub, path)
            if isinstance(doc, Exception) or doc is None:
                return item, "missing"
            try:
                if src == "redlines":
                    evs = redline_events(t, doc, since_c)
                elif src == "insiders":
                    evs = insider_events(t, doc, mcap.get(t), since_c, today)
                elif src == "estimates":
                    a = analyst_events(t, doc, since_c)
                    e_, matched = earnings_events(t, doc, since_c)
                    evs = a + e_ + consensus_events(t, doc, today)
                    return item, {"key": k, "events": evs, "matched": [m[1] for m in matched]}
                else:
                    fy = [r.get("fy") for r in ((doc.get("contracts") or {}).get("rows") or []) if not r.get("partial")]
                    fs_key = f"{t}:{max(fy) if fy else ''}"
                    fs = first_seen.setdefault(fs_key, today.isoformat())
                    evs = contract_events(t, doc.get("contracts"), fs)
                    return item, {"key": fs_key, "events": evs}
            except Exception as e:
                log("changes build failed", src, t, str(e)[:200])
                return item, "error"
            return item, {"key": k, "events": evs}
        done = {"redlines": 0, "insiders": 0, "estimates": 0, "contracts": 0}
        skipped = 0
        with ThreadPoolExecutor(workers) as ex:
            for (src, t, k, path), res in ex.map(work, todo):
                if isinstance(res, dict):
                    cache[src][t] = res
                    done[src] += 1
                elif res == "missing":
                    cache[src].pop(t, None)
                else:
                    skipped += 1

        matched = {(t, a) for t, v in cache["estimates"].items() for a in v.get("matched") or []}
        company = {t: [] for t in universe}
        for src in ("redlines", "insiders", "estimates", "contracts"):
            for t, v in cache[src].items():
                if t in company:
                    company[t] += v.get("events") or []
        extra = (filing_8k_events(filings, since_c) + results_8k_events(filings, since_c, matched) + ir_doc_events(irdocs, since_f)
                 + dividend_events((loaded["calendars/dividends.json"] or {}).get("rows"), since_c)
                 + form144_events((loaded["form144/recent.json"] or {}).get("rows"), mcap, since_f)
                 + short_interest_events((loaded["flows/short_interest.json"] or {}).get("companies"), since_c)
                 + comment_letter_events((loaded["comments/recent.json"] or {}).get("rows"), (loaded["comments/recent.json"] or {}).get("topics") or {}, since_c)
                 + technical_events(loaded["tech/states.json"], loaded["tech/signals.json"], since_f))
        for e in extra:
            if e["t"] in company:
                company[e["t"]].append(e)
        universe_events = (ipo_events((loaded["calendars/ipos.json"] or {}).get("rows"), since_f, today)
                           + macro_events(loaded["macro/releases.json"], corr, today - dt.timedelta(days=14), today))

        # Publish per-company files that changed.
        uploads = 0
        feed = []
        def put(t, evs):
            body = {"ticker": t, "name": BY_TICKER[t]["name"], "sector": BY_TICKER[t]["sector"], "generated": now, "as_of": today.isoformat(),
                    "window_days": COMPANY_DAYS, "counts": counts(evs), "events": evs, "method": METHOD}
            sb.put_json(f"changes/{t}.json", body, cache="max-age=600")
        pending = []
        for t, evs in company.items():
            uniq = {e["id"]: e for e in evs if e.get("upcoming") or (_d(e["date"]) or dt.date.min) >= since_c}
            evs = stamp(sorted(uniq.values(), key=lambda e: (e["date"], -TIER_ORDER.get(e["tier"], 3)), reverse=True), seen, now)
            h = hashlib.sha1(json.dumps([[e["id"], e["tier"], e["title"]] for e in evs]).encode()).hexdigest()[:16]
            if cache["uploaded"].get(t) != h:
                pending.append((t, evs, h))
            feed += [e for e in evs if (_d(e["date"]) or dt.date.min) >= since_f]
        with ThreadPoolExecutor(workers) as ex:
            for (t, evs, h), res in zip(pending, ex.map(lambda p: _safe(put, p[0], p[1]), pending)):
                if not isinstance(res, Exception):
                    cache["uploaded"][t] = h
                    uploads += 1
        feed += stamp(universe_events, seen, now)
        ranked = rank(feed)
        top = [slim(e) for e in ranked[:FEED_MAX]]
        sb.put_json("changes/feed.json", {"generated": now, "as_of": today.isoformat(), "window_days": FEED_DAYS, "version": VERSION,
                                          "counts": counts(ranked), "shown": len(top), "events": top, "method": METHOD,
                                          "tiers": {"high": "Rare or distress-type disclosures, large insider or holder trades relative to market cap, big surprises or reactions",
                                                    "medium": "Material but common: rating changes, dividend changes, new risk-factor paragraphs, sizeable insider trades",
                                                    "low": "Context: technical events with base rates, routine filings and documents"}},
                    cache="max-age=600")
        # Forget first-seen times for events no longer in any window (keeps state small).
        live = {e["id"] for evs in company.values() for e in evs} | {e["id"] for e in universe_events}
        state["seen"] = {k: v for k, v in seen.items() if k in live}
        state["generated"] = now
        _put_state(sb, state)
        run.stats.update({"done": len(universe), "reads": done, "skipped": skipped, "uploads": uploads, "feed_events": len(ranked),
                          "feed_shown": len(top), "by_tier": counts(ranked)["by_tier"], "by_type": counts(ranked)["by_type"]})
        if skipped:
            run.stats["partial"] = f"{skipped} reads deferred to the next run"
        log(json.dumps(run.stats)[:800])
        return run.stats
