"""Standardized KPIs: maps every company KPI id onto the canonical taxonomy (etl/kpi_taxonomy.py) and publishes
peer-comparable series.

Inputs (database): kpi_definitions, kpi_values (source_kind ir), ir_documents, companies.
Method per company KPI id (each reporting-basis id is mapped on its own):
  1. rules on name, unit, category, subcategory and parent (etl/kpi_taxonomy.classify)
  2. ids the rules leave open but whose name and unit fit a taxonomy entry go to a Fireworks model, which proposes a
     canonical id and scope; the proposal is kept only when it passes the rule review (candidate list, unit, exclusions,
     segment qualifiers, value range)
  3. per company and canonical id, one total-company id is the primary series: the current basis (latest values),
     then the plainest definition. Other total-company ids stay separate series (older bases or alternative
     definitions). Values are never stitched across ids.
Outputs:
  table kpi_std_map, kpi_canonical                  (when db/schema.sql has been applied)
  public-data kpistd/taxonomy.json                  taxonomy with coverage
  public-data kpistd/map.json                       {ticker: {canonical: primary kpi id}} plus alternative ids
  public-data kpistd/t/{T}.json                     one company's mapping (with reasons) and its canonical series
  public-data kpistd/series/{canonical}.json        every company's series for one canonical KPI, with documents
  raw kpistd/state.json.gz                          per-company signatures and the model-proposal cache (incremental runs)
"""
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import statistics
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import kpi_taxonomy as K
from .common import HTTPError, now_iso, request
from .sectors import fetch_all, load_universe

STATE_PATH = "kpistd/state.json.gz"
# (model, reasoning effort): short reasoning keeps a 30-item batch well inside the token budget
MODELS = [(os.environ.get("FU_KPISTD_MODEL", "kimi-k3"), "low"), (os.environ.get("FU_KPISTD_FALLBACK", "deepseek-v4p1-flash"), "none")]
MAX_Q, MAX_A = 48, 15
# plausible ranges for the median of a mapped series (canonical units); a series outside is not a match
RANGES = {"pct": (-100, 400), "nim": (0, 15), "efficiency_ratio": (10, 150), "cet1_ratio": (3, 40), "combined_ratio": (40, 200), "loss_ratio": (10, 150),
          "medical_care_ratio": (50, 110), "occupancy": (20, 100.5), "load_factor": (30, 100), "take_rate": (0, 60), "net_revenue_retention": (50, 250),
          "rotce": (-60, 80), "roe": (-60, 80), "net_charge_off_rate": (-1, 20), "book_to_bill": (0.2, 5), "net_leverage": (-5, 20),
          "adjusted_ebitda_margin": (-100, 100), "adjusted_operating_margin": (-100, 100), "fcf_margin": (-100, 100), "postpaid_phone_churn": (0, 10),
          "adjusted_eps": (-100, 500), "ffo_per_share": (-5, 50), "core_ffo_per_share": (-5, 50), "affo_per_share": (-5, 50), "book_value_per_share": (0, 2000),
          "tbv_per_share": (-50, 2000), "arpu": (0, 100000), "revpar": (5, 2000), "adr": (20, 3000)}


def log_(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- storage helpers
def _raw_get(sb, path):
    b = sb.get_object("raw", path)
    if not b:
        return None
    try:
        b = gzip.decompress(b)
    except OSError:
        pass
    return json.loads(b)


def _raw_put(sb, path, obj):
    sb.put_object("raw", path, gzip.compress(json.dumps(obj, separators=(",", ":"), default=str).encode()), "application/gzip", cache="no-store")


def _pub_get(sb, path):
    try:
        return sb.get_json(path)
    except Exception:
        return None


_MISSING = set()


def _try(sb, fn, what, table):
    """Writes to this workstream's tables; they exist once db/schema.sql has been applied, so a missing table is logged once, not fatal."""
    if table in _MISSING:
        return None
    try:
        return fn()
    except HTTPError as e:
        if e.code == 404:
            _MISSING.add(table)
        log_(f"  {what} skipped: {str(e)[:160]}")
        return None


def _q(ids):
    return urllib.parse.quote(",".join('"' + i.replace('"', '') + '"' for i in ids))


# ---------------------------------------------------------------- model proposals
PROMPT = """You map company KPI line items from investor materials onto a fixed taxonomy of standard KPIs.
For each item choose one id from its "options" list only if the item measures exactly that thing, otherwise null.
Scope:
  "total"   - the whole company figure
  "segment" - one segment, region, country, product, brand, business line or subsidiary
  "variant" - the same measure on a narrower or differently adjusted basis (excluding fuel, underlying, ex-catastrophe,
              accident year, managed basis, trailing twelve months, guidance, per-segment contribution, a reconciling item)
The same whole-company figure on another reporting basis (a predecessor company's basis, an older segment structure, a dated basis such as "(2012-2016 basis)") is still "total"; put the basis in member.
A reconciling line (an adjustment, a tax effect, a change versus prior year, a GAAP starting measure) is never the measure itself: use null.
GAAP EPS is not adjusted EPS: use null for reported or GAAP EPS.

Taxonomy:
{tax}

Items:
{items}

Return JSON: {{"items": [{{"i": <item number>, "id": <option id or null>, "scope": "total"|"segment"|"variant", "member": <segment/region/variant text or null>, "reason": <at most 18 words>}}]}}"""


def _llm(prompt, max_tokens=12000):
    last = None
    for model, effort in MODELS:
        try:
            d = json.loads(request("https://api.fireworks.ai/inference/v1/chat/completions", method="POST", data={
                "model": f"accounts/fireworks/models/{model}", "messages": [{"role": "user", "content": prompt}], "reasoning_effort": effort,
                "max_tokens": max_tokens, "temperature": 0, "response_format": {"type": "json_object"}},
                headers={"Authorization": f"Bearer {os.environ['FIREWORKS_API_KEY']}"}, timeout=180, retries=2))
            txt = re.sub(r"(?s)<think>.*?</think>", "", d["choices"][0]["message"]["content"] or "").strip()
            m = re.search(r"(?s)\{.*\}", txt)
            return json.loads(m.group(0) if m else txt), model
        except Exception as e:
            last = e
            log_("  kpistd model failed", model, str(e)[:160])
    raise last


def _llm_key(d, parent):
    s = "|".join(str(x or "") for x in (d["ticker"], d["kpi_id"], d["name"], d.get("category"), d.get("subcategory"), d.get("unit"),
                                        parent.get("name") if parent else "", d.get("sums_to_parent"), K.VERSION))
    return hashlib.sha1(s.encode()).hexdigest()[:20]


def propose(items, cache, deadline=None, workers=4):
    """items: [(key, def, parent, [taxonomy entries])]. Fills cache[key] = proposal for each answered item."""
    todo = [x for x in items if x[0] not in cache]
    batches = [todo[i:i + 30] for i in range(0, len(todo), 30)]

    def one(batch):
        if deadline and time.time() > deadline:
            return []
        ids = sorted({t["id"] for *_, opts in batch for t in opts})
        tax = "\n".join(f"- {i}: {K.BY_ID[i]['name']} ({K.BY_ID[i]['unit']}). {K.BY_ID[i]['definition']}" for i in ids)
        lines = []
        for n, (_k, d, p, opts) in enumerate(batch):
            lines.append(json.dumps({"i": n, "name": d["name"], "unit": d.get("unit"), "category": d.get("category"), "subcategory": d.get("subcategory"),
                                     "parent": p.get("name") if p else None, "component_of_parent": bool(d.get("sums_to_parent")),
                                     "company": d["ticker"], "options": [t["id"] for t in opts]}, ensure_ascii=False))
        try:
            out, model = _llm(PROMPT.format(tax=tax, items="\n".join(lines)))
        except Exception as e:
            log_("  proposal batch failed:", str(e)[:200])
            return []
        res = []
        for it in out.get("items") or []:
            try:
                n = int(it.get("i"))
            except (TypeError, ValueError):
                continue
            if 0 <= n < len(batch):
                res.append((batch[n][0], {"id": it.get("id"), "scope": it.get("scope"), "member": it.get("member"), "reason": (it.get("reason") or "")[:200], "model": model}))
        # items the model skipped are recorded as no match so they are not asked again
        got = {k for k, _ in res}
        res += [(k, {"id": None, "scope": None, "member": None, "reason": "no answer", "model": model}) for k, *_ in batch if k not in got]
        return res

    n = 0
    with ThreadPoolExecutor(workers) as ex:
        for res in ex.map(one, batches):
            for k, v in res:
                cache[k] = v
                n += 1
    return n


def review(d, parent, prop, opts, company_words):
    """Rule review of a model proposal. Returns a mapping dict or None."""
    cid = prop.get("id")
    if not cid or cid not in {t["id"] for t in opts}:
        return None
    t = K.BY_ID[cid]
    name = K.nn(d["name"])
    f = K.unit_factor(t["uclass"], d.get("unit"))
    if f is None or (t["_excl"] and t["_excl"].search(re.sub(r"\(.*?\)", " ", name))):
        return None
    scope = prop.get("scope") if prop.get("scope") in ("total", "segment", "variant") else "segment"
    member = prop.get("member")
    checks = [f"unit {d.get('unit')} fits {t['unit']}"]
    res = K.residual(t, name, company_words)
    if scope == "total":
        if d.get("sums_to_parent"):
            scope, member = "segment", member or (parent.get("name") if parent else None)
            checks.append("downgraded to segment: it sums to its parent")
        elif res:
            scope, member = ("segment" if K._segmentish(res) else "variant"), res
            checks.append(f"downgraded to {scope}: qualifier {res!r}")
        elif res is None and not K._loose(t, name) and not t["_kw"].search(name):
            return None
        else:
            checks.append("no segment or variant qualifier")
    conf = 0.7 if scope == "total" else 0.6
    return {"canonical": cid, "scope": scope, "member": member, "era": K.era_of(d["name"], d.get("category"), d.get("subcategory")), "confidence": conf,
            "factor": f, "reason": f"model: {prop.get('reason') or ''}; review: " + "; ".join(checks), "method": "llm+rules"}


# ---------------------------------------------------------------- mapping
STOP = {"inc", "corp", "corporation", "company", "co", "the", "group", "holdings", "holding", "plc", "ltd", "and", "of", "class", "common", "stock", "incorporated", "international"}


def company_words(c):
    words = set()
    for s in (c.get("name"), c.get("legal"), c.get("tk")):
        for w in re.findall(r"[a-z0-9]+", K.nn(s)):
            if len(w) > 2 and w not in STOP:
                words.add(w)
    return words


def signature(defs):
    s = json.dumps(sorted([d["kpi_id"], d["name"], d.get("category"), d.get("subcategory"), d.get("unit"), d.get("parent_id"), bool(d.get("sums_to_parent")),
                           d.get("updated_at")] for d in defs), default=str)
    return hashlib.sha1((s + str(K.VERSION)).encode()).hexdigest()[:16]


def classify_company(tk, defs, uni, cache, pending):
    """Rule mapping for one company; ids the rules leave open with plausible options go to `pending` for the model."""
    by = {d["kpi_id"]: d for d in defs}
    cw = company_words(uni.get(tk) or {"tk": tk})
    out = {}
    for d in defs:
        p = by.get(d.get("parent_id")) if d.get("parent_id") else None
        r = K.classify(d, p, cw)
        if r:
            r["method"] = "rules"
            out[d["kpi_id"]] = r
            continue
        opts = K.candidates(d)
        if opts:
            pending.append((_llm_key(d, p), d, p, opts, cw))
    return out


def fetch_values(sb, tk, ids):
    rows = []
    ids = sorted(ids)
    for i in range(0, len(ids), 60):
        rows += sb.select("kpi_values", f"select=kpi_id,period_kind,period_end,fiscal_label,value,source_id,anchor,derived"
                                        f"&ticker=eq.{tk}&source_kind=eq.ir&kpi_id=in.({_q(ids[i:i + 60])})&order=kpi_id,period_end")
    return rows


def fetch_docs(sb, ids):
    out = {}
    ids = sorted(i for i in ids if i and i != "derived")
    for i in range(0, len(ids), 80):
        for d in sb.select("ir_documents", f"select=id,title,storage_path,ext,url,doc_type,fiscal_label,published&id=in.({_q(ids[i:i + 80])})"):
            out[d["id"]] = [(d.get("title") or "").split("||")[0][:140], d.get("storage_path"), d.get("ext"), d.get("url"), d.get("doc_type"), d.get("fiscal_label")]
    return out


def _fye_match(end, fye):
    """Is a period end on the fiscal year end (MMDD), within a week."""
    if not fye or len(fye) != 4:
        return False
    e = dt.date.fromisoformat(end)
    for y in (e.year - 1, e.year, e.year + 1):
        try:
            f = dt.date(y, int(fye[:2]), min(int(fye[2:]), 28 if fye[:2] == "02" else int(fye[2:])))
        except ValueError:
            continue
        if abs((e - f).days) <= 7:
            return True
    return False


def build_series(rows, factor, fye):
    """{"Q": [...], "A": [...]} with values in canonical units. Point: [end, value, label, doc, page, quote, sheet, cell, formula, printed-unit value]."""
    q, a = {}, {}
    for r in rows:
        k = r["period_kind"]
        anc = r.get("anchor") or {}
        der = r.get("derived")
        v = float(r["value"])
        pt = [r["period_end"], round(v * factor, 6), r.get("fiscal_label"), r.get("source_id"), anc.get("page"), (anc.get("quote") or "")[:220],
              anc.get("sheet"), anc.get("cell"), (der or {}).get("formula") if der else None, v]
        if k == "Q":
            q[r["period_end"]] = pt
        elif k == "I":
            q.setdefault(r["period_end"], pt)
            if _fye_match(r["period_end"], fye):
                a.setdefault(r["period_end"], pt)
        elif k == "A":
            a[r["period_end"]] = pt
    return {"Q": [q[e] for e in sorted(q)][-MAX_Q:], "A": [a[e] for e in sorted(a)][-MAX_A:]}


def _median(pts):
    vs = [p[1] for p in pts if p[1] is not None]
    return statistics.median(vs) if vs else None


def choose(tk, maps, defs_by, values, fye, latest_all):
    """Primary and alternative total-company series per canonical id, with a range check on the values."""
    cut = (dt.date.fromisoformat(latest_all) - dt.timedelta(days=455)).isoformat() if latest_all else "0000"
    by_c = {}
    for kid, m in maps.items():
        if m["scope"] == "total" and m.get("confidence", 0) > 0:
            by_c.setdefault(m["canonical"], []).append(kid)
    series = {}
    for cid, kids in by_c.items():
        cands = []
        for kid in kids:
            m = maps[kid]
            s = build_series(values.get(kid, []), m["factor"], fye)
            pts = s["Q"] + s["A"]
            if not pts:
                m["role"] = None
                m["reason"] += "; no values"
                continue
            lo, hi = RANGES.get(cid) or (RANGES["pct"] if K.BY_ID[cid]["uclass"] == "pct" else (None, None))
            med = _median(pts)
            if lo is not None and med is not None and not (lo <= med <= hi):
                m["role"], m["confidence"] = "rejected", 0.0
                m["reason"] += f"; rejected: median value {med:g} outside the plausible range {lo:g} to {hi:g}"
                continue
            last = max(p[0] for p in pts)
            first = min(p[0] for p in pts)
            plain = 0 if m.get("era") else 1
            cands.append(((last >= cut, m["confidence"], plain, len(s["Q"]) + len(s["A"]), last, -len(kid)), kid, s, first, last))
        if not cands:
            continue
        cands.sort(reverse=True)
        _, pk, ps, pfirst, plast = cands[0]
        maps[pk]["role"] = "primary"
        d = defs_by[pk]
        ent = {"kpi_id": pk, "name": d["name"], "unit": d.get("unit"), "factor": maps[pk]["factor"], "Q": ps["Q"], "A": ps["A"], "alts": []}
        for _, kid, s, first, last in cands[1:]:
            m = maps[kid]
            m["role"] = "alt"
            label = m.get("era") or (f"earlier basis, to {last[:7]}" if last < pfirst or last < cut else "alternative definition")
            m["basis"] = label
            ent["alts"].append({"kpi_id": kid, "name": defs_by[kid]["name"], "unit": defs_by[kid].get("unit"), "basis": label, "Q": s["Q"], "A": s["A"]})
        series[cid] = ent
    return series


def map_job(sb, tickers=None, full=False, use_llm=True, time_budget=None, log=log_):
    t0 = time.time()
    deadline = t0 + time_budget if time_budget else None
    run_ts = now_iso()
    state = (None if full else _raw_get(sb, STATE_PATH)) or {}
    if state.get("version") != K.VERSION:
        # proposal cache keys carry the taxonomy version, so older entries are simply never looked up again
        state = {"version": K.VERSION, "sig": {}, "llm": {k: v for k, v in (state.get("llm") or {}).items()}}
    uni, _ix = load_universe(sb)
    cos = {c["ticker"]: c for c in sb.select("companies", "select=ticker,name,display_name,fiscal_year_end")}
    q = "select=ticker,kpi_id,name,category,subcategory,unit,parent_id,sums_to_parent,updated_at&source_kind=eq.ir&order=ticker,kpi_id"
    if tickers:
        q += f"&ticker=in.({','.join(tickers)})"
    defs = fetch_all(sb, "kpi_definitions", q, workers=4)
    by_t = {}
    for d in defs:
        by_t.setdefault(d["ticker"], []).append(d)
    sigs = {tk: signature(ds) for tk, ds in by_t.items()}
    todo = [tk for tk in sorted(by_t) if full or tickers or state["sig"].get(tk) != sigs[tk]]
    log(f"kpistd: {len(defs)} definitions, {len(by_t)} companies, {len(todo)} to map")

    # rules first, then model proposals for the ids the rules leave open
    maps, pending = {}, []
    for tk in todo:
        maps[tk] = classify_company(tk, by_t[tk], uni, state["llm"], pending)
    if use_llm and pending:
        n = propose([(k, d, p, o) for k, d, p, o, _ in pending], state["llm"], deadline=deadline - 90 if deadline else None)
        log(f"  model proposals: {n} new, {len(pending)} open ids")
    llm_done = set()
    for k, d, p, opts, cw in pending:
        prop = state["llm"].get(k)
        if prop is None:
            continue
        llm_done.add(d["ticker"])
        r = review(d, p, prop, opts, cw)
        if r:
            maps[d["ticker"]][d["kpi_id"]] = r
    # companies whose open ids did not all get an answer in this run are finished on the next one
    unanswered = {d["ticker"] for k, d, *_ in pending if k not in state["llm"]}

    old_map = _pub_get(sb, "kpistd/map.json") or {}
    touched = set()
    tfiles = {}
    done = []
    for tk in todo:
        if deadline and time.time() > deadline:
            break
        defs_by = {d["kpi_id"]: d for d in by_t[tk]}
        m = maps.get(tk, {})
        ids = [kid for kid, r in m.items() if r["scope"] == "total"]
        try:
            rows = fetch_values(sb, tk, ids) if ids else []
        except Exception as e:
            log("  values failed", tk, str(e)[:160])
            continue
        values = {}
        for r in rows:
            values.setdefault(r["kpi_id"], []).append(r)
        latest_all = max((r["period_end"] for r in rows), default=None)
        fye = (cos.get(tk) or {}).get("fiscal_year_end") or "1231"
        series = choose(tk, m, defs_by, values, fye, latest_all)
        docs = fetch_docs(sb, {p[3] for s in series.values() for pts in [s["Q"], s["A"]] + [a["Q"] + a["A"] for a in s["alts"]] for p in pts})
        c = uni.get(tk) or {}
        tf = {"ticker": tk, "generated": run_ts, "taxonomy_version": K.VERSION, "fye": fye, "sector": c.get("sector"), "industry": c.get("industry"),
              "name": c.get("name") or (cos.get(tk) or {}).get("display_name"),
              "map": [{"kpi_id": kid, "name": defs_by[kid]["name"], "unit": defs_by[kid].get("unit"), "category": defs_by[kid].get("category"),
                       "subcategory": defs_by[kid].get("subcategory"), "canonical": r["canonical"], "scope": r["scope"], "member": r.get("member"), "era": r.get("era"),
                       "role": r.get("role"), "basis": r.get("basis"), "confidence": r["confidence"], "method": r["method"], "reason": r["reason"]}
                      for kid, r in sorted(m.items(), key=lambda x: (x[1]["canonical"], x[1]["scope"] != "total", x[0]))],
              "series": series, "docs": docs}
        sb.put_json(f"kpistd/t/{tk}.json", tf, cache="max-age=600")
        tfiles[tk] = tf
        prev = set(((old_map.get("primary") or {}).get(tk) or {}).keys())
        touched |= prev | set(series)
        rows_db = [{"ticker": tk, "kpi_id": kid, "canonical_id": r["canonical"], "scope": r["scope"], "member": (r.get("member") or None) and str(r["member"])[:200],
                    "era": r.get("era"), "role": r.get("role"), "basis": r.get("basis"), "confidence": r["confidence"], "method": r["method"],
                    "reason": r["reason"][:600], "factor": r["factor"], "taxonomy_version": K.VERSION, "mapped_at": run_ts} for kid, r in m.items()]
        if rows_db:
            _try(sb, lambda: sb.upsert("kpi_std_map", rows_db, "ticker,kpi_id"), "kpi_std_map upsert", "kpi_std_map")
        _try(sb, lambda: sb.delete("kpi_std_map", f"ticker=eq.{tk}&mapped_at=lt.{urllib.parse.quote(run_ts)}"), "kpi_std_map stale delete", "kpi_std_map")
        if tk not in unanswered:
            state["sig"][tk] = sigs[tk]
        done.append(tk)
    # companies whose KPIs were withdrawn entirely
    gone = [tk for tk in ((old_map.get("primary") or {}).keys()) if tk not in by_t and not tickers]
    for tk in gone:
        touched |= set(old_map["primary"][tk].keys())
        state["sig"].pop(tk, None)
    _raw_put(sb, STATE_PATH, state)
    stats = publish(sb, uni, old_map, tfiles, touched, gone, run_ts, log=log)
    stats.update({"companies_mapped": len(done), "companies_left": len(todo) - len(done), "open_ids": len(pending), "unanswered_companies": len(unanswered),
                  "seconds": round(time.time() - t0, 1)})
    log("kpistd", stats)
    return stats


def publish(sb, uni, old_map, tfiles, touched, gone, run_ts, log=log_):
    """map.json, taxonomy.json and the series files of every canonical id whose members changed."""
    prim = dict(old_map.get("primary") or {})
    alts = dict(old_map.get("alts") or {})
    for tk in gone:
        prim.pop(tk, None)
        alts.pop(tk, None)
    for tk, tf in tfiles.items():
        prim[tk] = {cid: s["kpi_id"] for cid, s in tf["series"].items()}
        alts[tk] = {cid: [a["kpi_id"] for a in s["alts"]] for cid, s in tf["series"].items() if s["alts"]}
        if not prim[tk]:
            prim.pop(tk)
        if not alts[tk]:
            alts.pop(tk)
    seg_counts = dict(old_map.get("segment_counts") or {})
    for tk, tf in tfiles.items():
        sc = {}
        for r in tf["map"]:
            if r["scope"] != "total":
                sc[r["canonical"]] = sc.get(r["canonical"], 0) + 1
        seg_counts[tk] = sc
    for tk in gone:
        seg_counts.pop(tk, None)
    mp = {"generated": run_ts, "taxonomy_version": K.VERSION, "primary": prim, "alts": alts, "segment_counts": seg_counts}
    sb.put_json("kpistd/map.json", mp, cache="max-age=600")
    # series files: every company carrying the canonical id, read from its company file
    by_c = {}
    for tk, cs in prim.items():
        for cid in cs:
            by_c.setdefault(cid, []).append(tk)
    need = {tk for cid in touched for tk in by_c.get(cid, []) if tk not in tfiles}

    def get_tf(tk):
        return tk, _pub_get(sb, f"kpistd/t/{tk}.json")

    with ThreadPoolExecutor(8) as ex:
        for tk, tf in ex.map(get_tf, sorted(need)):
            if tf:
                tfiles[tk] = tf
    written = 0
    for cid in sorted(touched):
        t = K.BY_ID.get(cid)
        if not t:
            continue
        comps, docs = {}, {}
        for tk in sorted(by_c.get(cid, [])):
            tf = tfiles.get(tk)
            s = (tf or {}).get("series", {}).get(cid)
            if not s:
                continue
            comps[tk] = {**s, "sector": tf.get("sector"), "industry": tf.get("industry"), "fye": tf.get("fye"), "name": tf.get("name")}
            for pts in [s["Q"], s["A"]] + [a["Q"] + a["A"] for a in s["alts"]]:
                for p in pts:
                    if p[3] in (tf.get("docs") or {}):
                        docs[p[3]] = tf["docs"][p[3]]
        sb.put_json(f"kpistd/series/{cid}.json", {"id": cid, "name": t["name"], "unit": t["unit"], "family": t["family"], "how": t["how"],
                                                   "definition": t["definition"], "higher_is_better": t["higher_is_better"], "generated": run_ts,
                                                   "companies": comps, "docs": docs}, cache="max-age=600")
        written += 1
    # taxonomy with coverage
    cov = {}
    for cid, tks in by_c.items():
        inds = {}
        for tk in tks:
            c = uni.get(tk) or {}
            inds[c.get("industry") or "Other"] = inds.get(c.get("industry") or "Other", 0) + 1
        cov[cid] = {"companies": len(tks), "industries": inds, "segment_ids": sum((seg_counts.get(tk) or {}).get(cid, 0) for tk in seg_counts)}
    for cid in K.SEGMENT_ONLY:
        cov.setdefault(cid, {"companies": 0, "industries": {}, "segment_ids": sum((sc or {}).get(cid, 0) for sc in seg_counts.values()),
                             "segment_companies": sum(1 for sc in seg_counts.values() if (sc or {}).get(cid))})
    tax = [x | {"coverage": cov.get(x["id"], {"companies": 0, "industries": {}})} for x in K.public_taxonomy()]
    sb.put_json("kpistd/taxonomy.json", {"generated": run_ts, "version": K.VERSION, "kpis": tax,
                                         "method": "Rules on name, unit, category and parent, plus Fireworks model proposals kept only when they pass the rule review. "
                                                   "One company KPI id per series; ids from different reporting bases are never combined."}, cache="max-age=600")
    _try(sb, lambda: sb.upsert("kpi_canonical", [{"id": x["id"], "name": x["name"], "family": x["family"], "unit": x["unit"], "how": x["how"],
                                                  "definition": x["definition"], "higher_is_better": x["higher_is_better"], "match": x["match"],
                                                  "industries": x["industries"], "taxonomy_version": K.VERSION, "updated_at": run_ts} for x in K.public_taxonomy()], "id"),
         "kpi_canonical upsert", "kpi_canonical")
    return {"series_files": written, "companies_with_series": len(prim), "canonical_ids_covered": len(by_c)}


def primary_map(sb):
    """{ticker: {canonical: kpi_id}} for consumers (sectors, screener); empty when the mapping has not run."""
    m = _pub_get(sb, "kpistd/map.json") or {}
    return m.get("primary") or {}
