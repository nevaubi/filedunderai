"""CFTC Commitments of Traders through the CFTC Public Reporting Environment (publicreporting.cftc.gov, Socrata).

Traders in Financial Futures (futures only, dataset gpe5-46if) for rates, equity index, currency and volatility contracts,
and the Disaggregated report (futures only, dataset 72hh-3qpy) for major commodities. Net positions are long minus short of
the published columns; the snapshot keeps both columns so the arithmetic can be checked against the source row.
"""
import json
import urllib.parse

from .common import request

BASE = "https://publicreporting.cftc.gov/resource/"
PAGE = "https://publicreporting.cftc.gov/stories/s/r4w3-av2u"
TFF, DIS = "gpe5-46if", "72hh-3qpy"

FIN = {
    "13874A": "E-mini S&P 500", "209742": "Nasdaq-100 E-mini", "239742": "Russell 2000 E-mini", "1170E1": "VIX futures",
    "042601": "2-year Treasury note", "044601": "5-year Treasury note", "043602": "10-year Treasury note", "020601": "Treasury bond",
    "134741": "3-month SOFR", "098662": "U.S. dollar index", "099741": "Euro FX", "097741": "Japanese yen", "133741": "Bitcoin (CME)",
}
COM = {
    "067651": "WTI crude oil (NYMEX)", "023651": "Natural gas (NYMEX)", "088691": "Gold (COMEX)", "084691": "Silver (COMEX)",
    "085692": "Copper (COMEX)", "002602": "Corn (CBOT)", "005602": "Soybeans (CBOT)", "001602": "Wheat, SRW (CBOT)",
}
# dataset: [(suffix, label, long column, short column)]
MEASURES = {
    TFF: [("LEV", "leveraged funds net position", "lev_money_positions_long", "lev_money_positions_short"),
          ("AM", "asset managers net position", "asset_mgr_positions_long", "asset_mgr_positions_short")],
    DIS: [("MM", "managed money net position", "m_money_positions_long_all", "m_money_positions_short_all")],
}


def query(dataset, codes, since):
    cols = ["report_date_as_yyyy_mm_dd", "cftc_contract_market_code", "market_and_exchange_names", "open_interest_all"]
    for _, _, lc, sc in MEASURES[dataset]:
        cols += [lc, sc]
    where = f"report_date_as_yyyy_mm_dd >= '{since}T00:00:00' AND cftc_contract_market_code in ({','.join(repr(c) for c in codes)})"
    out, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"$select": ",".join(cols), "$where": where, "$order": "report_date_as_yyyy_mm_dd,cftc_contract_market_code", "$limit": 50000, "$offset": offset})
        page = json.loads(request(f"{BASE}{dataset}.json?{q}", timeout=180, retries=4))
        out += page
        if len(page) < 50000:
            return out, cols, f"{BASE}{dataset}.json?" + urllib.parse.urlencode({"$where": where})
        offset += 50000


def collect(since="2008-01-01", log=print):
    out = []
    for dataset, markets, report in [(TFF, FIN, "Traders in Financial Futures, futures only"), (DIS, COM, "Disaggregated, futures only")]:
        rows, cols, url = query(dataset, list(markets), since)
        log(f"cftc {dataset}: {len(rows)} rows")
        for code, name in markets.items():
            rs = [r for r in rows if r["cftc_contract_market_code"] == code]
            if not rs:
                log("cftc: no rows for", code, name)
                continue
            for suf, label, lc, sc in MEASURES[dataset]:
                obs, snap = [], []
                for r in rs:
                    d = r["report_date_as_yyyy_mm_dd"][:10]
                    snap.append([d, r.get("market_and_exchange_names", ""), r.get("open_interest_all", ""), r.get(lc, ""), r.get(sc, "")])
                    if r.get(lc) not in (None, "") and r.get(sc) not in (None, ""):
                        obs.append((d, str(int(float(r[lc])) - int(float(r[sc])))))
                meta = {"id": f"CFTC:{code}:{suf}", "source": f"Commitments of Traders, {report}", "provider": "Commodity Futures Trading Commission",
                        "title": f"{name}: {label}", "units": "Contracts", "frequency": "W", "seasonal_adj": None, "geography": "United States",
                        "source_url": f"https://publicreporting.cftc.gov/d/{dataset}", "data_url": f"{BASE}{dataset}.json?cftc_contract_market_code={code}",
                        "source_key": f"{dataset} {code} {lc} - {sc}", "section": "positioning", "release": "CFTC:COT",
                        "group": "cot_fin" if dataset == TFF else "cot_com", "market": name, "measure": suf, "dp": 0, "short": f"{name}, {label.split(' net')[0]}",
                        "derived": f"{lc} minus {sc}", "notes": "Positions as of Tuesday, released the following Friday at 3:30 p.m. Eastern (later after federal holidays). Net = long minus short."}
                out.append({"meta": meta, "obs": obs, "snap": {"url": meta["data_url"], "series": f"{code} {lc}, {sc}", "cols": ["report_date", "market", "open_interest_all", lc, sc],
                                                              "rows": snap, "keys": [s[0] for s in snap], "hl": [3, 4]}})
    return out
