"""Recent coverage via Tavily search."""
import os
import urllib.parse

from .common import request
import json


def tavily(query, **kw):
    body = {"api_key": os.environ["TAVILY_API_KEY"], "query": query, "max_results": kw.pop("max_results", 8), **kw}
    return json.loads(request("https://api.tavily.com/search", method="POST", data=body, timeout=60))


def domain(u):
    try:
        return urllib.parse.urlparse(u).hostname.replace("www.", "")
    except Exception:
        return ""


def company_news(name, ticker, days=14, n=10):
    r = tavily(f"{name} ({ticker}) stock news", topic="news", days=days, max_results=n)
    return [{"title": x["title"], "url": x["url"], "date": x.get("published_date"), "source": domain(x["url"]), "snippet": (x.get("content") or "")[:600]} for x in r.get("results", [])]


MACRO_QUERIES = ["Federal Reserve interest rate decision", "US inflation CPI report", "US jobs report payrolls", "Treasury yields bond market"]


def macro_news(days=14):
    out, seen = [], set()
    for q in MACRO_QUERIES:
        try:
            r = tavily(q, topic="news", days=days, max_results=5)
        except Exception as e:
            print("macro news failed", q, e)
            continue
        for x in r.get("results", []):
            if x["url"] in seen:
                continue
            seen.add(x["url"])
            out.append({"title": x["title"], "url": x["url"], "date": x.get("published_date"), "source": domain(x["url"]), "snippet": (x.get("content") or "")[:500]})
    return out
