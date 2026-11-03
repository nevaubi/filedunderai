"""Original documents via Firecrawl: statement tables with inline XBRL anchors, and earnings releases."""
import json
import os
import re

from bs4 import BeautifulSoup

from .common import request

try:
    import lxml  # noqa: F401
    PARSER = "lxml"
except ImportError:
    PARSER = "html.parser"

KEYS = {
    "income": [["us-gaap:NetIncomeLoss", "us-gaap:ProfitLoss"], ["us-gaap:EarningsPerShareDiluted", "us-gaap:EarningsPerShareBasic", "us-gaap:EarningsPerShareBasicAndDiluted"]],
    "balance": [["us-gaap:LiabilitiesAndStockholdersEquity"], ["us-gaap:Assets"]],
    "cashflow": [["us-gaap:NetCashProvidedByUsedInOperatingActivities", "us-gaap:NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"], ["us-gaap:NetCashProvidedByUsedInInvestingActivities", "us-gaap:NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"]],
}
KEEP_STYLE = re.compile(r"(font-weight:\s*(bold|700|600)|text-align:\s*(right|center|left)|padding-left:\s*[\d.]+(pt|px|em|in)|text-indent:\s*-?[\d.]+(pt|px|em|in)|border-bottom:\s*[^;]+|border-top:\s*[^;]+)", re.I)


def firecrawl(url, fmt="rawHtml", timeout_ms=90000):
    key = os.environ["FIRECRAWL_API_KEY"]
    out = request("https://api.firecrawl.dev/v2/scrape", method="POST",
                  data={"url": url, "formats": [fmt], "onlyMainContent": False, "timeout": timeout_ms},
                  headers={"Authorization": f"Bearer {key}"}, timeout=timeout_ms / 1000 + 60, retries=2)
    d = json.loads(out)
    if not d.get("success"):
        raise RuntimeError(f"firecrawl failed for {url}: {str(d)[:200]}")
    return d["data"]


def _clean_table(t, ctx, soup):
    for ix in t.find_all(re.compile(r"^ix:nonfraction$", re.I)):
        span = soup.new_tag("span")
        p = ctx.get(ix.get("contextref"))
        if p:
            span["data-ix"] = ix.get("name", "")
            span["data-p"] = p
        span.string = ix.get_text()
        ix.replace_with(span)
    for tag in t.find_all(True):
        if re.match(r"^ix:", tag.name or "", re.I):
            tag.unwrap()
            continue
        keep = {}
        if tag.name in ("td", "th") and tag.get("colspan"):
            keep["colspan"] = tag["colspan"]
        if tag.name == "span" and tag.get("data-ix"):
            keep["data-ix"] = tag["data-ix"]
            keep["data-p"] = tag["data-p"]
        ks = ";".join(m.group(0) for m in KEEP_STYLE.finditer(tag.get("style", "") or ""))
        if ks and tag.name in ("td", "th", "span", "div", "p"):
            keep["style"] = ks
        tag.attrs = keep
    t.attrs = {}
    for tr in t.find_all("tr"):
        if not tr.get_text(strip=True):
            tr.decompose()
    for sp in t.find_all(["span", "div", "p", "font"]):
        if not sp.attrs:
            sp.unwrap()
    return re.sub(r"\s+", " ", str(t)).replace("\xa0", " ")


def ix_tables(html):
    soup = BeautifulSoup(html, PARSER)
    ctx = {}
    for c in soup.find_all(re.compile(r"^xbrli:context$", re.I)):
        if c.find(re.compile(r"segment$", re.I)):
            continue
        st, en, ins = c.find(re.compile(r"startdate$", re.I)), c.find(re.compile(r"enddate$", re.I)), c.find(re.compile(r"instant$", re.I))
        if st and en:
            ctx[c.get("id")] = f"{st.text.strip()}|{en.text.strip()}"
        elif ins:
            ctx[c.get("id")] = ins.text.strip()
    out = {}
    tables = soup.find_all("table")
    for stmt, groups in KEYS.items():
        for t in tables:
            names = {x.get("name") for x in t.find_all(re.compile(r"^ix:nonfraction$", re.I))}
            if len(names) >= 6 and all(any(k in names for k in g) for g in groups):
                out[stmt] = _clean_table(t, ctx, soup)
                break
    return out


def release_markdown(index_url, primary_doc):
    links = firecrawl(index_url, "links").get("links", [])
    acc = index_url.rstrip("/").rsplit("/", 1)[-1]
    htm = [l for l in links if l.lower().endswith((".htm", ".html")) and acc in l.replace("-", "")]
    pick = [l for l in htm if re.search(r"(?i)ex-?99|ex99|exhibit99|991|pr", l.rsplit("/", 1)[-1]) and not l.endswith(primary_doc)]
    if not pick:
        pick = [l for l in htm if not l.endswith(primary_doc) and "index" not in l.lower()]
    if not pick:
        return None, None
    url = pick[0]
    md = firecrawl(url, "markdown").get("markdown", "")
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    lines = md.split("\n")
    start = next((i for i, l in enumerate(lines) if len(l.split()) >= 4 and ".htm" not in l), 0)
    return url, "\n".join(lines[start:])[:16000]
