"""Read-only data access for agent runs and monitors: the published Filed Under files (public-data bucket),
summarized into compact, sourced dicts. Every helper tolerates a missing or not-yet-built file (returns None or an
`available: False` marker) because several feeds (changes, situations, earnings packs, themes, KPI insights) are
built by other jobs and may not exist yet.
"""
import datetime as dt
import json
import re
import threading

TICKER_RX = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
SEC = "https://www.sec.gov"


def T(ticker):
    t = str(ticker or "").strip().upper()
    if not TICKER_RX.match(t):
        raise ValueError(f"bad ticker {ticker!r}")
    return t


def _days_ago(n):
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()


def _r(x, n=4):
    return round(x, n) if isinstance(x, float) else x


PINNED = {"screener.json", "tech/states.json", "tech/signals.json", "estimates/index.json", "redlines/index.json", "meta/insiders.json",
          "calendars/dividends.json", "calendars/ipos.json", "macro/releases.json", "market.json", "tech/breadth.json", "macro.json"}


class Data:
    """Per-tick cache of published files. Thread-safe enough for the tick's small worker pool."""

    def __init__(self, sb):
        self.sb = sb
        self._cache = {}
        self._lock = threading.Lock()

    def get(self, path):
        with self._lock:
            if path in self._cache:
                return self._cache[path]
        try:
            v = self.sb.get_json(path)
        except Exception:
            v = None
        with self._lock:
            self._cache[path] = v
            if len(self._cache) > 900:
                for k in list(self._cache):
                    if k not in PINNED:
                        self._cache.pop(k)
                        break
        return v

    def prefetch(self, paths, workers=16):
        """Loads many files in parallel into the cache (monitors read hundreds of per-company files)."""
        from concurrent.futures import ThreadPoolExecutor
        todo = [p for p in dict.fromkeys(paths) if p not in self._cache]
        if todo:
            with ThreadPoolExecutor(min(workers, len(todo))) as ex:
                list(ex.map(self.get, todo))

    # ---- universe and screener
    def screener(self):
        s = self.get("screener.json") or {}
        if "_by" not in s and s.get("rows"):
            cols = s["cols"]
            s["_by"] = {r[0]: dict(zip(cols, r)) for r in s["rows"]}
            s["_fields"] = {f["id"]: f for f in s.get("fields") or []}
        return s

    def row(self, ticker):
        return (self.screener().get("_by") or {}).get(T(ticker))

    def tickers(self):
        return list((self.screener().get("_by") or {}).keys())

    def sector_of(self, ticker):
        r = self.row(ticker) or {}
        return r.get("sector")

    # ---- tools
    def company(self, ticker):
        t = T(ticker)
        r = self.row(t)
        if not r:
            return {"ticker": t, "available": False, "note": "not in coverage"}
        keep = ["name", "sector", "industry", "price", "mcap", "pe", "pe_p5", "ev_ebit", "fcf_yield", "fcf_yield_p5", "div_yield",
                "rev_g_q", "rev_g_ttm", "rev_accel", "eps_g_q", "gm", "om", "fcfm", "roic", "nd_ebitda", "altman_z", "pio_f",
                "ret_1m", "ret_3m", "ret_ytd", "ret_1y", "dist_hi52", "dist_lo52", "rsi14", "beta", "next_earn_days",
                "ins_net_90d", "ins_buyers_90d", "kpi_count", "seg_count", "lq", "lq_end"]
        out = {"ticker": t, **{k: _r(r.get(k)) for k in keep if r.get(k) is not None}}
        est = self.estimate_row(t)
        if est:
            out["estimates"] = est
        ins = self.get(f"insiders/{t}.json") or {}
        if ins.get("w90"):
            out["insiders_90d"] = ins["w90"]
        st = self.tech_state(t)
        if st:
            out["technicals"] = {k: st.get(k) for k in ("trend", "rsi", "vs50", "vs200", "from_hi", "from_lo", "st", "squeeze", "r21", "r63")}
            out["technicals"]["recent_events"] = (st.get("events") or [])[-5:]
        out["sources"] = [f"screener.json ({(self.screener().get('asof') or {}).get('prices')})", f"insiders/{t}.json", "estimates/index.json", "tech/states.json"]
        return out

    def estimate_row(self, ticker):
        idx = self.get("estimates/index.json") or {}
        by = idx.get("_by")
        if by is None and idx.get("companies"):
            by = idx["_by"] = {e["ticker"]: e for e in idx["companies"]}
        e = (by or {}).get(T(ticker))
        if not e:
            return None
        earn = e.get("earnings") or {}
        last = earn.get("last") or {}
        return {"next_earnings": e.get("next_earnings"), "eps_next_q": _r(e.get("eps_0q")), "ratings": e.get("ratings"),
                "target_median": _r((e.get("targets") or {}).get("median")), "target_upside_median": _r((e.get("targets") or {}).get("upside_median")),
                "target_n": (e.get("targets") or {}).get("n"), "last_surprise_pct": _r(last.get("surprise_pct")),
                "last_quarter": last.get("quarter_end"), "consensus_as_of": e.get("consensus_as_of")}

    def estimates(self, ticker):
        t = T(ticker)
        d = self.get(f"estimates/{t}.json")
        if not d:
            return {"ticker": t, "available": False}
        cons = d.get("consensus") or {}
        return {"ticker": t, "as_of": cons.get("as_of"), "periods": cons.get("periods"), "next_earnings": cons.get("next_earnings"),
                "revisions": d.get("revisions"), "surprises": (d.get("surprises") or [])[:6],
                "source": cons.get("url") or "Yahoo Finance consensus", "summary": self.estimate_row(t)}

    def financials(self, ticker, metrics=None, periods=8, annual=False):
        t = T(ticker)
        d = self.get(f"companies/{t}.json")
        if not d or not isinstance(d.get("statements"), dict):
            return {"ticker": t, "available": False}
        want = [m.lower() for m in (metrics or [])]
        out = []
        for stmt, rows in d["statements"].items():
            for r in rows or []:
                lab = r.get("label") or ""
                if want and not any(w in lab.lower() for w in want):
                    continue
                series = r.get("annual" if annual else "quarterly") or []
                pts = [{"end": p.get("end"), "v": p.get("v"), "accn": p.get("accn"), "form": p.get("form")} for p in series[-int(periods):]]
                out.append({"statement": stmt, "label": lab, "unit": r.get("unit"), "values": pts})
        cite = f"filing:{t}:{out[0]['values'][-1]['accn']}" if out and out[0]["values"] and out[0]["values"][-1].get("accn") else None
        return {"ticker": t, "basis": "annual" if annual else "quarterly", "lines": out[:24], "cite_example": cite,
                "note": "Values in reported units (USD unless unit says otherwise). Cite with filing:TICKER:ACCESSION."}

    def insiders(self, ticker, days=90):
        t = T(ticker)
        d = self.get(f"insiders/{t}.json")
        if not d:
            return {"ticker": t, "available": False}
        cut = _days_ago(int(days))
        rows = [r for r in d.get("rows") or [] if (r.get("tx_date") or "") >= cut and r.get("code") in ("P", "S") and not r.get("derivative")]
        slim = [{k: r.get(k) for k in ("tx_date", "owner", "role", "code", "shares", "price", "plan_10b5", "ownership", "accession", "joint")} for r in rows[:60]]
        for s in slim:
            s["value"] = round((s.get("shares") or 0) * (s.get("price") or 0))
        return {"ticker": t, "window_days": days, "w90": d.get("w90"), "w365": d.get("w365"), "owners": d.get("owners", [])[:8], "transactions": slim,
                "source": f"SEC Form 4 (insiders/{t}.json); cite filing:{t}:ACCESSION"}

    def filing_changes(self, ticker, max_changes=12):
        t = T(ticker)
        d = self.get(f"redlines/{t}.json")
        if not d:
            return {"ticker": t, "available": False}
        comps = []
        for c in d.get("comparisons") or []:
            changes = []
            for s in c.get("sections") or []:
                for ch in s.get("changes") or []:
                    if ch.get("score", 0) >= 30 or ch.get("signals"):
                        changes.append({"section": s.get("title"), "kind": ch.get("kind"), "score": ch.get("score"), "signals": ch.get("signals"),
                                        "text": (ch.get("new") or ch.get("old") or "")[:420]})
            changes.sort(key=lambda x: -(x.get("score") or 0))
            new = c.get("new") or {}
            comps.append({"kind": c.get("kind"), "form": c.get("form"), "filed": new.get("filed"), "old_filed": (c.get("old") or {}).get("filed"),
                          "totals": c.get("totals"), "cite": f"filing:{t}:{new.get('accn')}" if new.get("accn") else None, "url": new.get("url"),
                          "top_changes": changes[:max_changes]})
        return {"ticker": t, "comparisons": comps, "source": f"redlines/{t}.json (SEC EDGAR primary documents)"}

    def tech_state(self, ticker):
        s = self.get("tech/states.json") or {}
        by = s.get("_by")
        if by is None and s.get("rows"):
            by = s["_by"] = {r["t"]: r for r in s["rows"]}
        return (by or {}).get(T(ticker))

    def base_rate(self, signal, horizon="60"):
        sig = ((self.get("tech/signals.json") or {}).get("signals") or {}).get(signal) or {}
        h = sig.get(str(horizon)) or {}
        if not h:
            return None
        return {"signal": signal, "horizon_days": int(horizon), "events": h.get("n"), "mean": _r(h.get("mean")), "hit": _r(h.get("hit")),
                "excess": _r(h.get("excess")), "excess_ci": h.get("excess_ci"),
                "caveat": "Past frequencies across currently tracked companies since 2008 (survivorship bias), not forecasts."}

    def technicals(self, ticker):
        t = T(ticker)
        st = self.tech_state(t)
        if not st:
            return {"ticker": t, "available": False}
        events = [{"signal": e[0], "date": e[1], "base_rate_60d": self.base_rate(e[0])} for e in (st.get("events") or [])[-6:]]
        return {"ticker": t, "as_of": st.get("d"), "state": {k: st.get(k) for k in st if k not in ("events",)}, "recent_signals": events,
                "source": "tech/states.json, tech/signals.json (Filed Under daily history)"}

    def kpis(self, ticker, query=None, limit=30):
        t = T(ticker)
        d = self.get(f"kpis/{t}.json")
        if not d:
            return {"ticker": t, "available": False, "note": "No IR KPI file yet"}
        q = (query or "").lower()
        out = []
        for g in d.get("groups") or []:
            for it in g.get("items") or []:
                name = it.get("name") or it.get("id") or ""
                if q and q not in name.lower() and q not in (g.get("category") or "").lower():
                    continue
                vals = (it.get("values") or {}).get("Q") or {}
                ks = sorted(vals)[-6:]
                pts = [{"period_end": k, "v": vals[k].get("v"), "label": vals[k].get("label"), "doc": vals[k].get("doc_title"), "url": vals[k].get("url"),
                        "quote": (vals[k].get("quote") or "")[:80]} for k in ks]
                yoy = None
                if len(ks) >= 1:
                    last = ks[-1]
                    prior = [k for k in vals if k[:4] == str(int(last[:4]) - 1) and abs(int(k[5:7]) - int(last[5:7])) <= 1]
                    if prior and vals[prior[-1]].get("v") and vals[last].get("v") is not None and vals[prior[-1]]["v"] != 0:
                        yoy = round(vals[last]["v"] / vals[prior[-1]]["v"] - 1, 4)
                out.append({"category": g.get("category"), "name": name, "unit": it.get("unit"), "is_total": it.get("is_total"), "latest": pts[-1] if pts else None,
                            "yoy": yoy, "history": pts})
                if len(out) >= limit:
                    break
        return {"ticker": t, "generated": d.get("generated"), "kpis": out, "source": f"kpis/{t}.json (IR releases and decks, each value with its document and quote)"}

    def ir_documents(self, ticker, limit=15, doc_type=None):
        t = T(ticker)
        d = self.get(f"ir/{t}.json")
        if not d:
            return {"ticker": t, "available": False}
        docs = d.get("documents") or []
        if doc_type:
            docs = [x for x in docs if doc_type.lower() in (x.get("doc_type") or "").lower()]
        docs = sorted(docs, key=lambda x: x.get("published") or x.get("period_end") or "", reverse=True)
        return {"ticker": t, "site": d.get("site"), "total": len(d.get("documents") or []), "coverage": (d.get("coverage") or {}).get("status"),
                "documents": [{k: x.get(k) for k in ("id", "title", "doc_type", "fiscal_label", "period_end", "published", "url", "ext")} for x in docs[:int(limit)]]}

    def ir_read(self, ticker, doc_id, query=None, max_chars=7000):
        t = T(ticker)
        d = self.get(f"ir/{t}.json") or {}
        row = next((x for x in d.get("documents") or [] if x.get("id") == doc_id), None)
        if not row:
            return {"ticker": t, "error": "unknown document id"}
        path = row.get("text_path")
        text = ""
        if path:
            try:
                b = self.sb.get_object("ir", path)
                j = json.loads(b) if b else {}
                pages = j.get("pages") or []
                if isinstance(pages, list):
                    text = "\n".join(p if isinstance(p, str) else (p.get("text") or "") for p in pages)
                if not text and isinstance(j.get("sheets"), dict):
                    text = "\n".join(f"[{k}]\n" + "\n".join(" | ".join(str(c) for c in r) for r in (v or [])[:200]) for k, v in j["sheets"].items())
            except Exception as e:
                return {"ticker": t, "error": f"text unavailable: {str(e)[:80]}", "url": row.get("url")}
        if query and text:
            hits = [m.start() for m in re.finditer(re.escape(query), text, re.I)][:6]
            text = "\n…\n".join(text[max(0, h - 600):h + 900] for h in hits) or text[:max_chars]
        return {"ticker": t, "title": row.get("title"), "url": row.get("url"), "published": row.get("published"), "text": text[:max_chars]}

    def alt_data(self, ticker):
        t = T(ticker)
        d = self.get(f"altdata/{t}.json")
        if not d:
            return {"ticker": t, "available": False}
        con = (d.get("contracts") or {}).get("rows") or []
        fy = {r.get("fy"): r.get("amount") for r in con}
        yrs = sorted(k for k in fy if isinstance(k, int))
        latest_full = [y for y in yrs if not next((r for r in con if r.get("fy") == y and r.get("partial")), None)]
        yoy = None
        if len(latest_full) >= 2 and fy.get(latest_full[-2]):
            yoy = round(fy[latest_full[-1]] / fy[latest_full[-2]] - 1, 4)
        sv = ((d.get("short_volume") or {}).get("rows") or [])[-20:]
        short_ratio = round(sum(r[1] for r in sv) / max(1, sum(r[3] for r in sv)), 4) if sv else None
        lob = ((d.get("lobbying") or {}).get("rows") or [])[-4:]
        return {"ticker": t, "federal_obligations_by_fy": {y: round(fy[y]) for y in yrs[-6:] if fy.get(y) is not None},
                "latest_full_fy": latest_full[-1] if latest_full else None, "prior_full_fy": latest_full[-2] if len(latest_full) >= 2 else None,
                "partial_fys": [y for y in yrs if y not in latest_full], "latest_full_fy_change": yoy, "contracts_source": d.get("contracts_source"),
                "short_volume_ratio_20d": short_ratio, "lobbying_last_quarters": [{k: r.get(k) for k in ("year", "quarter", "amount")} for r in lob],
                "ftd_recent": ((d.get("ftd") or {}).get("rows") or [])[-5:], "updated_at": d.get("updated_at")}

    def gov(self, ticker):
        t = T(ticker)
        d = self.get(f"gov/{t}.json")
        if not d:
            return {"ticker": t, "available": False}
        ct = d.get("congress_trades") or []
        return {"ticker": t, "congress_trades": [{k: x.get(k) for k in ("member", "party", "chamber", "type", "tx_date", "amount", "disclosed")} for x in ct[:12]],
                "hearings": [{k: h.get(k) for k in ("title", "date", "chamber", "mentions")} for h in (d.get("hearings") or [])[:6]],
                "rules": [{k: r.get(k) for k in ("title", "date", "type")} for r in ((d.get("regulation") or {}).get("rules") or [])[:6]],
                "lobbying": ((d.get("lobbying") or {}).get("quarters") or [])[-4:], "sources": d.get("sources")}

    def supply(self, ticker):
        t = T(ticker)
        d = self.get(f"supply/co/{t}.json")
        if not d:
            return {"ticker": t, "available": False}
        links = [{k: l.get(k) for k in ("rel", "cp", "cpt", "det", "sh", "ss", "amt", "rg", "q", "accn", "form", "filed")} for l in d.get("links") or []]
        return {"ticker": t, "links": links[:40], "single_source": [l["cp"] for l in links if l.get("ss")], "concentration": d.get("conc"),
                "policy": [{k: p.get(k) for k in ("kind", "countries", "q")} for p in (d.get("policy") or [])[:6]],
                "source": "Latest 10-K/10-Q text, each link with its verbatim quote and accession"}

    def regulatory(self, ticker):
        t = T(ticker)
        d = self.get(f"regulatory/{t}.json")
        if not d:
            return {"ticker": t, "available": False, "note": "Only Health Care companies are covered (ClinicalTrials.gov, openFDA)"}
        tr = d.get("trials") or {}
        s = tr.get("summary") or {}
        keep = ("nct", "title", "acronym", "phase", "status", "why_stopped", "primary_completion", "enrollment", "conditions", "interventions", "updated")
        return {"ticker": t, "counts": d.get("counts"), "active_by_phase": s.get("by_phase_active"), "late_stage_active": s.get("late_stage_active"),
                "upcoming_readouts": [{k: r.get(k) for k in keep} for r in (s.get("upcoming") or [])[:15]],
                "recent_status_changes": [{k: r.get(k) for k in keep} for r in (s.get("recent_changes") or [])[:12]],
                "approvals": [{k: a.get(k) for k in ("date", "type", "brands", "ingredients", "class", "priority", "application", "url")} for a in (d.get("approvals") or [])[:20]],
                "recalls": [{k: r.get(k) for k in ("date", "kind", "class", "product", "reason", "status")} for r in (d.get("recalls") or [])[:15]],
                "clearances_510k": [{k: c.get(k) for k in ("date", "device", "k", "url")} for c in (d.get("clearances") or [])[:10]],
                "sources": d.get("sources")}

    def proposed_sales(self, ticker):
        t = T(ticker)
        d = self.get(f"form144/{t}.json")
        if not d:
            return {"ticker": t, "available": False}
        keep = ("filed", "person", "relationship", "value", "shares", "pct_outstanding", "sale_date", "plan", "plan_adopted", "status", "url")
        return {"ticker": t, "summary": d.get("summary"), "notices": [{**{k: n.get(k) for k in keep},
                                                                       "executed": (n.get("executed") or {}).get("filled_pct")} for n in (d.get("notices") or [])[:25]],
                "source": "SEC Form 144 notices matched to Form 4 sales by the same person within 14 days"}

    def sec_review(self, ticker):
        t = T(ticker)
        d = self.get(f"comments/{t}.json")
        if not d:
            return {"ticker": t, "available": False}
        names = d.get("topics") or {}
        out = []
        for th in (d.get("threads") or [])[:8]:
            out.append({"form": th.get("form"), "period": th.get("period"), "status": th.get("status"), "first": th.get("first"), "last": th.get("last"),
                        "rounds": th.get("rounds"), "topics": [names.get(x, x) for x in th.get("topics") or []],
                        "comments": [c for l in th.get("letters") or [] if l.get("form") == "UPLOAD" for c in (l.get("comments") or [])][:10],
                        "letters": [{k: l.get(k) for k in ("form", "filed", "kind", "url")} for l in th.get("letters") or []]})
        return {"ticker": t, "threads": out, "source": "SEC Corporation Finance comment letters (UPLOAD) and company responses (CORRESP)"}

    def calendar(self, days=14, kinds=None, tickers=None):
        kinds = set(kinds or ["earnings", "ex_dividend", "macro", "ipo"])
        today = dt.date.today().isoformat()
        end = (dt.date.today() + dt.timedelta(days=int(days))).isoformat()
        want = {T(x) for x in tickers} if tickers else None
        out = []
        if "earnings" in kinds:
            for e in (self.get("estimates/index.json") or {}).get("companies") or []:
                d = e.get("next_earnings") or ""
                if today <= d <= end and (not want or e["ticker"] in want):
                    out.append({"kind": "earnings", "date": d, "ticker": e["ticker"], "estimate": bool((e.get("earnings") or {}).get("is_estimate")),
                                "eps_consensus": _r(e.get("eps_0q"))})
        if "ex_dividend" in kinds:
            for r in (self.get("calendars/dividends.json") or {}).get("rows") or []:
                for u in r.get("upcoming") or []:
                    d = u.get("ex_date") or ""
                    if today <= d <= end and (not want or r["ticker"] in want):
                        out.append({"kind": "ex_dividend", "date": d, "ticker": r["ticker"], "amount": u.get("amount"), "increase": u.get("increase"), "url": u.get("url")})
        if "macro" in kinds and not want:
            for r in self.get("macro/releases.json") or []:
                d = r.get("release_date") or ""
                if today <= d <= end:
                    out.append({"kind": "macro", "date": d, "release": r.get("release"), "title": r.get("title"), "time": r.get("release_time"), "url": r.get("source_url")})
        if "trial_readout" in kinds:
            for r in (self.get("regulatory/catalysts.json") or {}).get("rows") or []:
                d = r.get("primary_completion") or ""
                if today <= d <= end and (not want or r.get("ticker") in want):
                    out.append({"kind": "trial_readout", "date": d, "ticker": r.get("ticker"), "phase": r.get("phase"), "title": r.get("acronym") or r.get("title"),
                                "nct": r.get("nct"), "url": f"https://clinicaltrials.gov/study/{r.get('nct')}"})
        if "proposed_sale" in kinds:
            for r in (self.get("form144/recent.json") or {}).get("rows") or []:
                d = r.get("sale_date") or ""
                if r.get("status") == "open" and today <= d <= end and (not want or r.get("ticker") in want):
                    out.append({"kind": "proposed_sale", "date": d, "ticker": r.get("ticker"), "person": r.get("person"), "value": r.get("value"),
                                "plan": r.get("plan"), "url": r.get("url")})
        if "ipo" in kinds and not want:
            for r in (self.get("calendars/ipos.json") or {}).get("rows") or []:
                d = r.get("priced") or r.get("effective") or ""
                if d and today <= d[:10] <= end:
                    out.append({"kind": "ipo", "date": d[:10], "name": r.get("name"), "symbol": r.get("symbol"), "status": r.get("status")})
        out.sort(key=lambda x: (x["date"], x["kind"]))
        return {"from": today, "to": end, "events": out[:200], "count": len(out)}

    def market(self):
        m = self.get("market.json") or {}
        syms = {}
        for s, v in (m.get("symbols") or {}).items():
            bars = v.get("bars") or []
            if len(bars) >= 2:
                last, prev = bars[-1][1], bars[-2][1]
                m21 = bars[-22][1] if len(bars) > 22 else bars[0][1]
                syms[s] = {"last": last, "chg_1d": round(last / prev - 1, 4) if prev else None, "chg_1m": round(last / m21 - 1, 4) if m21 else None}
        b = self.get("tech/breadth.json") or {}
        breadth = None
        if b.get("d"):
            i = -1
            breadth = {"date": b["d"][i], "pct_above_50d": round(b["above50"][i] / max(1, b["n50"][i]), 3),
                       "pct_above_200d": round(b["above200"][i] / max(1, b["n200"][i]), 3), "new_highs": b["new_high"][i], "new_lows": b["new_low"][i]}
        macro = (self.get("macro.json") or {}).get("brief") or {}
        return {"generated": m.get("generated"), "symbols": syms, "breadth": breadth, "macro_brief": {"headline": macro.get("headline"), "bullets": (macro.get("bullets") or [])[:5]}}

    def screen(self, filters=None, sort=None, limit=25, fields=None):
        s = self.screener()
        by = s.get("_by") or {}
        ops = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b, "=": lambda a, b: a == b, "!=": lambda a, b: a != b}
        rows = []
        for t, r in by.items():
            ok = True
            for f in filters or []:
                v = r.get(f.get("field"))
                op = f.get("op", ">=")
                if op == "in":
                    ok = v in (f.get("value") or [])
                elif v is None or op not in ops:
                    ok = False
                else:
                    try:
                        ok = ops[op](v, f.get("value"))
                    except TypeError:
                        ok = False
                if not ok:
                    break
            if ok:
                rows.append(r)
        if sort and sort.get("field"):
            rows = [r for r in rows if r.get(sort["field"]) is not None]
            rows.sort(key=lambda r: r[sort["field"]], reverse=bool(sort.get("desc", True)))
        cols = ["ticker", "name", "sector"] + [c for c in (fields or []) if c not in ("ticker", "name", "sector")] + \
               [f.get("field") for f in filters or [] if f.get("field") not in (fields or [])]
        cols = list(dict.fromkeys(c for c in cols if c))
        return {"matches": len(rows), "columns": cols, "rows": [[_r(r.get(c)) for c in cols] for r in rows[:int(limit)]],
                "source": f"screener.json (prices as of {(s.get('asof') or {}).get('prices')})"}

    def screen_fields(self):
        return [{"id": k, "name": f.get("n"), "unit": f.get("u"), "coverage": f.get("cov")} for k, f in (self.screener().get("_fields") or {}).items()]

    def feed_file(self, path):
        """Feeds built by other jobs (changes, situations, earnings packs, themes, KPI insights)."""
        d = self.get(path)
        if d is None:
            return {"path": path, "available": False, "note": "This feed has not been published yet."}
        txt = json.dumps(d, default=str)
        if len(txt) > 20000:
            if isinstance(d, dict):
                d = {k: (v[:40] if isinstance(v, list) else v) for k, v in d.items()}
        return d
