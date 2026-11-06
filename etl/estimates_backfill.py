"""One-off backfill of estimates/ from Alexandria responses collected outside the cron (e.g. through the
Firecrawl MCP tools), saved as JSON files with the standard scrape envelope {data: {alexandria: [...]}}.

    python3 -m etl.estimates_backfill --raw DIR [--only AAPL,NVDA] [--dry-run] [--upload-raw]

Each provider record keeps its retrieval time and the raw file it came from; --upload-raw stores the raw
responses (gzipped) under estraw/backfill/{date}/ in the private raw bucket.
"""
import argparse
import datetime as dt
import glob
import json
import os
from concurrent.futures import ThreadPoolExecutor

from . import estimates as E
from .common import Supabase, now_iso
from .universe import UNIVERSE, BY_TICKER


def load_raw(raw_dir):
    """Parse every saved response into per-ticker Yahoo payloads, Benzinga ratings, rosters and removals."""
    yahoo, ratings, analysts, firms, removed = {}, {}, {}, {}, set()
    files = sorted(glob.glob(os.path.join(raw_dir, "*.json")))
    for f in files:
        try:
            d = json.load(open(f))
        except Exception:
            continue
        base = os.path.basename(f)
        mtime = dt.datetime.fromtimestamp(os.path.getmtime(f), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for a in ((d.get("data") or {}).get("alexandria") or []):
            cap, x = a.get("capability"), a.get("data")
            if a.get("error") or x is None:
                continue
            meta = {"raw": base, "provider_request_id": a.get("providerRequestId"), "retrieved_at": mtime}
            if cap == "equities/earnings" and isinstance(x, dict) and x.get("symbol"):
                tk = E.norm_ticker(x["symbol"])
                prev = yahoo.get(tk)
                if not prev or (x.get("observed_at_ms") or 0) >= (prev[0].get("observed_at_ms") or 0):
                    yahoo[tk] = (x, meta)
            elif cap == "calendar/ratings" and isinstance(x, dict):
                for r in x.get("ratings") or []:
                    if r.get("id"):
                        ratings[r["id"]] = (r, meta)
            elif cap == "calendar/ratings-analysts" and isinstance(x, dict):
                for r in x.get("analyst_ratings_analyst") or []:
                    if r.get("id"):
                        analysts[r["id"]] = r
            elif cap == "calendar/ratings-firms" and isinstance(x, dict):
                for r in x.get("analyst_ratings_firm") or []:
                    if r.get("id"):
                        firms[r["id"]] = r
            elif cap == "calendar/removed" and isinstance(x, dict):
                removed |= {r["id"] for r in x.get("removed") or [] if r.get("type") == "ratings" and r.get("id")}
    by_ticker = {}
    for rid, (r, meta) in ratings.items():
        a = E.parse_rating(r)
        by_ticker.setdefault(a["ticker"], []).append(a)
    return {"files": files, "yahoo": yahoo, "actions": by_ticker, "analysts": list(analysts.values()), "firms": list(firms.values()),
            "removed": removed}


def fetch_inputs(sb, tk):
    out = {}
    for key, path in (("company", f"companies/{tk}.json"), ("prices", f"prices/{tk}.json"), ("guidance", f"guidance/{tk}.json"),
                      ("prev", f"estimates/{tk}.json")):
        try:
            out[key] = sb.get_json(path)
        except Exception as e:
            out[key] = None
            out.setdefault("errors", []).append(f"{path}: {str(e)[:120]}")
    c = out.get("company")
    if c:  # keep only what the bundle needs; company bundles are several MB
        inc = (c.get("statements") or {}).get("income") or []
        out["company"] = {"cik": c.get("cik"), "earnings8k": c.get("earnings8k") or [],
                          "statements": {"income": [r for r in inc if r.get("label") in ("EPS diluted", "Revenue")]}}
    return out


def build_one(sb, tk, raw, spy, today, sources_base, dry=False):
    inp = fetch_inputs(sb, tk)
    y = raw["yahoo"].get(tk)
    yahoo = E.parse_yahoo_earnings(y[0], y[1]["retrieved_at"]) if y else None
    if yahoo:
        yahoo["raw"] = y[1]["raw"]
        yahoo["provider_request_id"] = y[1]["provider_request_id"]
    src = dict(sources_base)
    if y:
        src["yahoo"] = {**sources_base["yahoo"], "url": yahoo and yahoo.get("url"), "retrieved_at": y[1]["retrieved_at"], "raw": f"estraw/backfill/{today}/{y[1]['raw']}.gz"}
    b = E.build_bundle(tk, name=BY_TICKER.get(tk, {}).get("name"), yahoo=yahoo, prev=inp.get("prev"), actions=raw["actions"].get(tk, []),
                       company=inp.get("company"), prices=inp.get("prices"), spy=spy, guidance=inp.get("guidance"), sources=src, today=today,
                       removed_ids=raw["removed"])
    if not dry:
        sb.put_json(f"estimates/{tk}.json", b, cache="max-age=600")
    return tk, b, inp.get("errors")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--only")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--upload-raw", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    sb = Supabase()
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    raw = load_raw(args.raw)
    print(f"raw files {len(raw['files'])}; yahoo {len(raw['yahoo'])}; tickers with actions {len(raw['actions'])}; "
          f"actions {sum(len(v) for v in raw['actions'].values())}; analysts {len(raw['analysts'])}; firms {len(raw['firms'])}; removed {len(raw['removed'])}")
    if args.upload_raw and not args.dry_run:
        def up(f):
            import gzip
            with open(f, "rb") as fh:
                data = fh.read()
            sb.put_object("raw", f"estraw/backfill/{today}/{os.path.basename(f)}.gz", gzip.compress(data), "application/gzip", cache="no-store")
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(up, raw["files"]))
        print("raw uploaded")
    tickers = [t.strip().upper() for t in args.only.split(",")] if args.only else [t[0] for t in UNIVERSE]
    spy = sb.get_json("prices/SPY.json")
    sources_base = {
        "yahoo": {"provider": E.YAHOO_LABEL, "via": "Firecrawl Alexandria finance-yahoo-com/equities/earnings", "method": "backfill"},
        "benzinga": {"provider": "Benzinga", "attribution": E.BENZINGA_ATTRIBUTION, "via": "Firecrawl Alexandria benzinga/calendar/ratings",
                     "method": "backfill", "retrieved_at": now_iso(), "history_from": "2025-07-02", "raw": f"estraw/backfill/{today}/"},
        "sec": {"provider": "SEC EDGAR via Filed Under company bundle", "path": "companies/{T}.json"},
        "guidance": {"provider": "Filed Under guidance extraction from IR press releases", "path": "guidance/{T}.json"},
    }
    results, errs = {}, {}
    with ThreadPoolExecutor(args.workers) as ex:
        for tk, b, e in ex.map(lambda t: build_one(sb, t, raw, spy, today, sources_base, args.dry_run), tickers):
            results[tk] = b
            if e:
                errs[tk] = e
    logos = {}
    try:
        reg = sb.get_json("estimates/firm-logos/registry.json") or {}
        logos = {k: v.get("path") for k, v in (reg.get("firms") or {}).items() if v.get("path")}
    except Exception:
        pass
    if not args.only:
        analysts, firms = E.build_rosters({t: b["actions"] for t, b in results.items()}, raw["analysts"], raw["firms"], logos, today)
        if not args.dry_run:
            sb.put_json("estimates/analysts.json", analysts, cache="max-age=600")
            sb.put_json("estimates/firms.json", firms, cache="max-age=600")
        print(f"analysts {len(analysts['analysts'])}; firms {len(firms['firms'])}")
    cov = {"consensus": sum(1 for b in results.values() if b["coverage"]["consensus"]), "actions": sum(1 for b in results.values() if b["coverage"]["actions"]),
           "guidance": sum(1 for b in results.values() if b["guidance"].get("available")), "surprises": sum(1 for b in results.values() if b["coverage"]["surprises"])}
    print("coverage", cov, "errors", len(errs))
    if errs:
        print(list(errs.items())[:5])
    return results


if __name__ == "__main__":
    main()
