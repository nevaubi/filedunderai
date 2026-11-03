"""Company logos: fetched once per ticker from public logo CDNs and stored in the public bucket."""
from concurrent.futures import ThreadPoolExecutor

from .common import HTTPError, now_iso, request
from .universe import UNIVERSE

SOURCES = [
    ("parqet", "https://assets.parqet.com/logos/symbol/{t}?format=png&size=256"),
    ("fmp", "https://financialmodelingprep.com/image-stock/{t}.png"),
    ("nvstly", "https://raw.githubusercontent.com/nvstly/icons/main/ticker_icons/{t}.png"),
]
PNG = b"\x89PNG\r\n\x1a\n"


def fetch_logo(ticker):
    """Returns (source, png_bytes) from the first source that has a real PNG for the ticker."""
    for name, url in SOURCES:
        try:
            body = request(url.format(t=ticker), timeout=20, retries=2, raw=True)
        except HTTPError:
            continue
        except Exception:
            continue
        if body and body[:8] == PNG and len(body) > 300:
            return name, body
    return None, None


def logos_job(sb, tickers=None, force=False, workers=6):
    index = {}
    try:
        index = sb.get_json("meta/logos.json") or {}
    except Exception:
        index = {}
    have = index.get("tickers", {})
    todo = [t for t, *_ in UNIVERSE if (tickers is None or t in tickers) and (force or t not in have)]

    def one(t):
        src, body = fetch_logo(t)
        if not body:
            return t, None
        sb.put_object("public-data", f"logos/{t}.png", body, "image/png", "public, max-age=604800")
        return t, src

    found, missing = 0, []
    with ThreadPoolExecutor(workers) as ex:
        for t, src in ex.map(one, todo):
            if src:
                have[t] = src
                found += 1
            else:
                missing.append(t)
    index = {"generated": now_iso(), "tickers": have, "missing": sorted(set(missing) | (set(index.get("missing", [])) - set(have)))}
    sb.put_json("meta/logos.json", index, cache="max-age=600")
    return found, missing
