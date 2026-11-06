"""One-off pilot backfill for the supply-chain map, run from a workstation without the production LLM key.

  python3 -m etl.supply_seed fetch  --only=NVDA,AAPL      # filings + 8-K Item 1.01 docs into SUPPLY_CACHE
  python3 -m etl.supply_seed candidates --only=NVDA       # rule-based proposals for human review (JSON lines)
  python3 -m etl.supply_seed publish --reviewed=FILE      # verify every reviewed quote verbatim, then write supply/

Reviewed links come from a JSON file written by the reviewer ({ticker: [{accn, cp, cpt, rel, ...quote}]}).
Nothing is written unless every quote is found verbatim in its filing text; failures are dropped and reported."""
import concurrent.futures as cf
import json
import os
import sys

from .common import sec_json
from .supply_core import norm, rule_candidates
from .supply_sources import agreement_filings, fetch_text, filing_rows, older_rows, pick_filings
from .universe import BY_TICKER

PILOT = ("NVDA AAPL MSFT AMZN GOOGL META AVGO AMD INTC QCOM MU AMAT LRCX KLAC TXN ORCL CSCO DELL HPQ TSLA F GM BA LMT RTX GE CAT DE "
         "WMT COST TGT HD PG KO PEP PFE LLY MRK JNJ ABBV UNH XOM CVX NKE SBUX DIS NFLX MCK COR CAH CMCSA CHTR MRVL ADI JBL FDX UPS "
         "LOW CVS CI").split()


def arg(name, default=None):
    for a in sys.argv[2:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


def plan(ticker):
    cik = BY_TICKER[ticker]["cik"]
    sub = sec_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    rows = filing_rows(sub)
    if len([r for r in rows if r["form"] == "10-K"]) < 3:
        rows += older_rows(sub)
    return {"ticker": ticker, "cik": cik, "name": BY_TICKER[ticker]["name"], "legal": sub.get("name"), "filings": pick_filings(rows),
            "agreements": agreement_filings(rows, since="2023-11-01")}


def fetch(ticker):
    p = plan(ticker)
    for f in p["filings"] + p["agreements"]:
        try:
            f["chars"] = len(fetch_text(p["cik"], f["accn"], f["doc"]))
        except Exception as e:
            f["error"] = str(e)[:160]
    return p


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    only = (arg("only") or ",".join(PILOT)).upper().split(",")
    out_dir = arg("out", os.environ.get("SUPPLY_WORK", "."))
    if cmd == "fetch":
        with cf.ThreadPoolExecutor(6) as ex:
            plans = list(ex.map(lambda t: (t, fetch(t)), only))
        path = os.path.join(out_dir, "plans.json")
        old = json.load(open(path)) if os.path.exists(path) else {}
        old.update({t: p for t, p in plans})
        json.dump(old, open(path, "w"), indent=1)
        for t, p in plans:
            print(t, [(f["form"], f["filed"], f.get("chars"), f.get("error")) for f in p["filings"]], len(p["agreements"]), "8-K 1.01")
    elif cmd == "candidates":
        plans = json.load(open(os.path.join(out_dir, "plans.json")))
        covered = {t: [v["name"]] for t, v in BY_TICKER.items()}
        for t in only:
            p = plans[t]
            self_names = [p["name"], p.get("legal")]
            for f in p["filings"]:
                if f.get("error"):
                    continue
                text = fetch_text(p["cik"], f["accn"], f["doc"])
                for c in rule_candidates(text, t, covered, self_names):
                    c.update(t=t, accn=f["accn"], form=f["form"], filed=f["filed"], doc=f["doc"], role=f["role"])
                    print(json.dumps(c))
    elif cmd == "publish":
        publish(only, out_dir, write=arg("dry") != "1")
    elif cmd == "intel":
        from .common import Supabase
        from .supply import intel_job
        print(json.dumps(intel_job(Supabase(), time_budget=int(arg("budget", "3000")), per_run=200, force_regions=arg("regions") == "1"), indent=1)[:3000])
    elif cmd == "regraph":
        from .common import Supabase
        from .supply import merge_graph, public_get
        sb = Supabase()
        cos = {t: public_get(sb, f"supply/co/{t}.json") for t in only}
        print(merge_graph(sb, {t: (co["links"], co) for t, co in cos.items() if co}))
    else:
        print(__doc__)


def publish(only, out_dir, write=True):
    """Re-reads every pilot filing, applies the reviewer decisions, keeps only quotes verbatim in the filing text, writes
    supply/co/{T}.json and text copies, merges the coverage graph and records rotation state (method rules+review)."""
    from .common import Supabase
    from .supply import merge_graph, process_company, public_get, public_put
    sb = Supabase()
    plans = json.load(open(os.path.join(out_dir, "plans.json")))
    updates, summary = {}, {}

    def one(t):
        links, co, s = process_company(sb, t, mode="rules", review=True, p=plans[t], write=write)
        return t, links, co, s
    with cf.ThreadPoolExecutor(4) as ex:
        for t, links, co, s in ex.map(one, only):
            updates[t] = (links, co)
            summary[t] = s
            print(t, s)
    if not write:
        return
    print("graph", merge_graph(sb, updates))
    state = public_get(sb, "supply/state.json") or {"tickers": {}, "cursor": 0}
    from .common import now_iso
    for t, (links, co) in updates.items():
        p = plans[t]
        k = next((f["accn"] for f in p["filings"] if f["form"] == "10-K"), None)
        q = next((f["accn"] for f in p["filings"] if f["form"] == "10-Q"), None)
        state["tickers"][t] = {"k": k, "q": q, "at": co["generated"], "m": co["method"], "links": len(links), "intel": None}
    state["generated"] = now_iso()
    public_put(sb, "supply/state.json", state, cache="max-age=60")
    json.dump(summary, open(os.path.join(out_dir, "publish_summary.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
