"""Score published filing insights against the hand-labelled set (tests/eval/filing_insights_labels.json).

For each labelled filing, read insights/{T}.json from production public data and check whether its top five
insights cover each label. A label is a hit when a top-five insight is in the same section and either
  - its quote shares at least half of the label quote's words, or
  - it has the same type and its title/text is similar to the label summary (token Jaccard >= 0.25).
Insights drawn from a different filing than the labelled one are reported as stale and count as misses.

Usage:  python3 tests/eval/score_filing_insights.py [--base URL] [--json]
The base defaults to $SUPABASE_URL/storage/v1/object/public/public-data/. No model keys are needed.
"""
import argparse
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from etl.supply_core import norm  # noqa: E402

TARGET = 0.80
STOP = set("the a an of and or to in on for with by as at from our its is are was were be been this that which it we".split())


def words(s):
    return [w for w in re.findall(r"[a-z0-9$%.]+", norm(s)) if w not in STOP]


def overlap(label_quote, quote):
    a, b = words(label_quote), set(words(quote))
    return sum(1 for w in a if w in b) / len(a) if a else 0.0


def jaccard(a, b):
    a, b = set(words(a)), set(words(b))
    return len(a & b) / len(a | b) if a | b else 0.0


def same_section(a, b):
    return norm(a).split(".")[0] == norm(b).split(".")[0] or norm(a) == norm(b)


def hit(label, insight):
    if not same_section(label["section"], insight.get("section", "")):
        return False
    if overlap(label["quote"], insight.get("quote", "")) >= 0.5:
        return True
    return label["type"] == insight.get("type") and jaccard(label["summary"], f"{insight.get('title', '')} {insight.get('text', '')}") >= 0.25


def fetch(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=30) as r:
            return json.loads(r.read())
    except Exception:
        return None


def score(labels, load):
    rows, hits, total, scored = [], 0, 0, 0
    for f in labels["filings"]:
        doc = load(f"insights/{f['ticker']}.json")
        status = "missing" if not doc else "stale" if doc["filing"]["accn"] != f["accn"] else "ok"
        top = (doc or {}).get("insights", [])[:5] if status == "ok" else []
        got = [any(hit(m, i) for i in top) for m in f["material"]]
        n = sum(got)
        total += len(got)
        hits += n
        scored += status == "ok"
        rows.append({"ticker": f["ticker"], "form": f["form"], "filed": f["filed"], "status": status, "labels": len(got), "hits": n,
                     "recall": n / len(got) if got else None,
                     "missed": [m["summary"] for m, g in zip(f["material"], got) if not g]})
    return {"filings": len(rows), "scored": scored, "labels": total, "hits": hits, "recall": hits / total if total else 0.0,
            "recall_scored": (sum(r["hits"] for r in rows if r["status"] == "ok") / max(1, sum(r["labels"] for r in rows if r["status"] == "ok"))),
            "target": TARGET, "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=(os.environ.get("SUPABASE_URL", "").rstrip("/") + "/storage/v1/object/public/public-data/"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not a.base.startswith("http"):
        sys.exit("Set SUPABASE_URL or pass --base")
    labels = json.load(open(os.path.join(HERE, "filing_insights_labels.json")))
    res = score(labels, lambda p: fetch(a.base, p))
    if a.json:
        print(json.dumps(res, indent=1))
        return
    for r in res["rows"]:
        rec = "-" if r["recall"] is None else f"{r['recall']:.0%}"
        print(f"{r['ticker']:<6} {r['form']:<5} {r['filed']}  {r['status']:<7} {r['hits']}/{r['labels']}  {rec}")
        for m in r["missed"] if r["status"] == "ok" else []:
            print(f"         missed: {m}")
    print(f"\nTop-5 recall over all {res['labels']} labels: {res['recall']:.1%} (target {TARGET:.0%})")
    print(f"Filings with insights for the labelled filing: {res['scored']}/{res['filings']}; recall on those: {res['recall_scored']:.1%}")


if __name__ == "__main__":
    main()
