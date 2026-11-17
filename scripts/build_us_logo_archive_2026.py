#!/usr/bin/env python3
"""Collect real US-listed company logo assets; never fabricate or use letter placeholders."""
from __future__ import annotations
import csv, hashlib, io, json, os, re, time, zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from PIL import Image
from xml.etree import ElementTree as ET

ROOT = Path("company-logo-assets")
ROOT.mkdir(exist_ok=True)
LOGOS = ROOT / "logos"
LOGOS.mkdir(exist_ok=True)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FiledUnderAssetLibrary/1.0; public research)"}
RANKING_SOURCE = "https://www.stocktitan.net/rankings/companies-market-cap"
BATCH_SIZE, TARGET = 500, 2500

def get(url, timeout=25):
    for n in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (404, 410):
                return None
            if resp.status_code not in (429, 500, 502, 503, 504):
                return None
        except requests.RequestException:
            pass
        time.sleep(.5 * (n+1))
    return None

def parse_rank_table(html):
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for table in soup.find_all("table"):
        heading = table.find("thead")
        hrow = heading.find("tr") if heading else table.find("tr")
        if not hrow:
            continue
        headers = [x.get_text(" ", strip=True).lower() for x in hrow.find_all(["th","td"], recursive=False)]
        if not all(any(s in h for h in headers) for s in ("rank","symbol","country")):
            continue
        def idx(k):
            return next(i for i,h in enumerate(headers) if k in h)
        ix = {k:idx(k) for k in ("rank","symbol","country")}
        ix["name"] = next((i for i,h in enumerate(headers) if "company" in h), 2)
        ix["market_cap"] = next((i for i,h in enumerate(headers) if "market cap" in h), 3)
        ix["sector"] = next((i for i,h in enumerate(headers) if "sector" in h), -1)
        body = table.find("tbody")
        trs = body.find_all("tr") if body else table.find_all("tr")[1:]
        for tr in trs:
            cells = [c.get_text(" ",strip=True) for c in tr.find_all(["td","th"], recursive=False)]
            try:
                rank = int(re.sub(r"[^0-9]", "", cells[ix["rank"]]))
                ticker = cells[ix["symbol"]].upper().strip()
                name = cells[ix["name"]].strip()
                country = cells[ix["country"]].strip()
                market_cap = cells[ix["market_cap"]].strip()
                sector = cells[ix["sector"]].strip() if ix["sector"]>=0 else ""
                if ticker and name and country:
                    result.append(dict(source_rank=rank,ticker=ticker,name=name,
                                       country=country,market_cap=market_cap,sector=sector))
            except (ValueError,IndexError,KeyError):
                continue
        if result:
            break
    return result

def one_page(n):
    url = RANKING_SOURCE + (f"?page={n}" if n>1 else "")
    r = get(url)
    if not r:
        return n, []
    parsed = parse_rank_table(r.text)
    if n in (1,2,40):
        print(f"Rankings page {n}: HTTP 200, parsed {len(parsed)} rows; "
              f"first={parsed[0]['ticker'] if parsed else 'NONE'}", flush=True)
    return n, parsed

def norm_name(name):
    n = re.sub(r"\b(class [a-z]|series [a-z]|common stock|ordinary shares|ads|adr)\b", "", name.lower())
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return re.sub(r"\b(incorporated|inc|corp|corporation|company|co|holdings|holding|plc|ltd|limited)\b", "", n).strip()

def build_universe():
    pages = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures=[pool.submit(one_page,p) for p in range(1,61)]
        for f in as_completed(futures):
            p, rows=f.result()
            pages[p]=rows
    rows=[r for p in sorted(pages) for r in pages[p]]
    print(f"Market cap ranking: {len(rows)} rows across {sum(bool(x) for x in pages.values())} pages",flush=True)
    rows.sort(key=lambda r:r["source_rank"])
    selected, names, symbols = [],set(),set()
    for row in rows:
        if row["country"]!="United States":
            continue
        t=row["ticker"]
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,12}",t) or t in symbols:
            continue
        n=row["name"]
        if re.search(r"\b(acquisition (corp|corporation|company)|spac|etf|etn|warrants?|municipal income fund|closed.end fund)\b",n,re.I):
            continue
        canon=norm_name(n)
        if len(canon)<2 or canon in names:
            continue
        selected.append(row)
        names.add(canon)
        symbols.add(t)
        if len(selected)==TARGET:
            break
    if len(selected)<TARGET:
        raise RuntimeError(f"Only {len(selected)} validated US company ranks; no fabricated 2500 list")
    for i,r in enumerate(selected,1):
        r["us_rank"]=i
    return selected

def svg_valid(data):
    if len(data)<90 or len(data)>3_000_000:
        return False, "invalid_size"
    lower=data[:1000].lower()
    if b"<html" in lower or b"<!doctype html" in lower:
        return False, "html_instead_of_svg"
    try:
        root=ET.fromstring(data)
        if root.tag.split("}")[-1].lower() != "svg":
            return False, "not_svg_root"
        elems=list(root.iter())
        t=[e.tag.split("}")[-1].lower() for e in elems]
        if "script" in t or "foreignobject" in t:
            return False, "unsafe_svg"
        visible={"path","rect","circle","ellipse","polygon","polyline","image","line","use"}
        if not any(k in visible for k in t):
            return False, "no_drawable_mark"
        if b"javascript:" in data.lower():
            return False, "active_content"
        return True, ""
    except ET.ParseError:
        return False, "invalid_xml"

def png_valid(data):
    if len(data)<250 or len(data)>6_000_000 or data[:8]!=b"\x89PNG\r\n\x1a\n":
        return False, "not_png"
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            w,h=im.size
            if min(w,h)<96 or max(w,h)>8000:
                return False, "png_resolution"
            extrema=im.convert("RGBA").getextrema()
            if extrema[3][1]==0:
                return False, "blank_image"
        return True, ""
    except Exception:
        return False, "png_corrupt"

def asset_sources(ticker):
    t=quote(ticker,safe="")
    plain=quote(ticker.replace(".","-"),safe="")
    # Prioritize original, scalable, color SVG from the larger file-based logo catalogue.
    yield "TickerLogo verified SVG",f"https://tickerlogo.com/logos/{t}.svg","svg"
    if plain!=t:
        yield "TickerLogo alternate ticker",f"https://tickerlogo.com/logos/{plain}.svg","svg"
    # LogoAPI is useful for missing vector originals; a name-tile fallback must never be accepted.
    yield "LogoAPI vector",f"https://logoapi.sh/ticker/{t}.svg?fallback=404","svg"
    # Fallback only to true raster logo files; do not generate placeholder tiles.
    yield "NVSTly transparent PNG",f"https://raw.githubusercontent.com/nvstly/icons/main/ticker_icons/{t}.png","png"
    if plain!=t:
        yield "NVSTly alternate PNG",f"https://raw.githubusercontent.com/nvstly/icons/main/ticker_icons/{plain}.png","png"
    yield "Financial Modeling Prep PNG",f"https://financialmodelingprep.com/image-stock/{t}.png","png"

def collect_one(company):
    ticker=company["ticker"]
    problem=[]
    for provider,url,extension in asset_sources(ticker):
        response=get(url,timeout=20)
        if not response:
            problem.append(f"{provider}:unavailable")
            continue
        if provider.startswith("LogoAPI"):
            fallback=response.headers.get("X-Logo-Fallback","").lower()
            if fallback in ("name","tile","letter","favicon","initials"):
                problem.append(f"LogoAPI:placeholder_{fallback}")
                continue
        raw=response.content
        valid,reason=svg_valid(raw) if extension=="svg" else png_valid(raw)
        if not valid:
            problem.append(f"{provider}:{reason}")
            continue
        path=LOGOS / (ticker+"."+extension)
        path.write_bytes(raw)
        return dict(company, status="downloaded", file="logos/"+path.name,
                    format=extension.upper(), source=provider, source_url=url,
                    bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),
                    note="")
    return dict(company,status="missing",file="",format="",source="",source_url="",
                bytes=0,sha256="",note="; ".join(problem)[-1200:])

def write_csv(path,records):
    fields=["us_rank","source_rank","ticker","name","country","market_cap","sector",
            "status","file","format","source","source_url","bytes","sha256","note"]
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        w.writeheader()
        w.writerows(records)

def zip_bundle(path, records, root_metadata):
    logos_found=[r for r in records if r["status"]=="downloaded"]
    with zipfile.ZipFile(path, "w",compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
        for r in logos_found:
            z.write(ROOT/r["file"], r["file"])
        csvbuff=io.StringIO()
        fields=["us_rank","source_rank","ticker","name","country","market_cap","sector",
                "status","file","format","source","source_url","bytes","sha256","note"]
        cw=csv.DictWriter(csvbuff, fieldnames=fields, extrasaction="ignore")
        cw.writeheader()
        cw.writerows(records)
        z.writestr("manifest.csv",csvbuff.getvalue())
        z.writestr("quality_report.json",json.dumps(dict(root_metadata,
            requested=len(records), downloaded=len(logos_found),
            missing=len(records)-len(logos_found),
            format_counts=dict(Counter(r["format"] for r in logos_found)),
            source_counts=dict(Counter(r["source"] for r in logos_found))),indent=2))
        z.writestr("README.txt",
            "Filed Under / US company logo library\n"
            "Only actual downloaded image assets. No synthesized letter tiles.\n"
            "Universe: US-domiciled public companies, sorted by the source's market cap ranking.\n"
            "Ranking source: "+RANKING_SOURCE+"\n"
            "Assets: SVG where valid, PNG fallback. Source and SHA256 in manifest.csv.\n"
            "Logo recency is not individually certified; verify brand usage before publication.\n"
            "These marks may be trademarks. See each issuer's branding and terms.\n")

def validate_archive(path):
    with zipfile.ZipFile(path) as z:
        corrupt=z.testzip()
        if corrupt: raise RuntimeError(f"Corrupt archive {path}: {corrupt}")
        names=z.namelist()
        count=len([x for x in names if x.startswith("logos/")])
        if not count: raise RuntimeError(f"No visual assets in {path}")
        if "manifest.csv" not in names or "quality_report.json" not in names:
            raise RuntimeError(f"No manifest in {path}")
        return count

def main():
    timestamp=datetime.now(timezone.utc).isoformat()
    entries=build_universe()
    print(f"Selected {len(entries)} companies. Top: {[(x['ticker'],x['name']) for x in entries[:10]]}",flush=True)
    records=[]
    with ThreadPoolExecutor(max_workers=22) as pool:
        futures={pool.submit(collect_one,c):c for c in entries}
        for f in as_completed(futures):
            record=f.result()
            records.append(record)
            if len(records)%100==0:
                ok=sum(r["status"]=="downloaded" for r in records)
                print(f"Processed {len(records)} / {len(entries)} — downloaded {ok}, missing {len(records)-ok}",flush=True)
    records.sort(key=lambda r:r["us_rank"])
    write_csv(ROOT/"master_manifest.csv",records)
    missing=[r for r in records if r["status"]!="downloaded"]
    write_csv(ROOT/"missing_logos.csv",missing)
    metadata={"target":TARGET,"retrieved_utc":timestamp,"universe":RANKING_SOURCE,
              "no_placeholder_images":True}
    for start in range(0,len(records),BATCH_SIZE):
        batch=records[start:start+BATCH_SIZE]
        path=ROOT/f"US_Company_Logos_{start+1:04d}-{start+len(batch):04d}.zip"
        zip_bundle(path,batch,metadata)
        count=validate_archive(path)
        print(f"VERIFIED ZIP {path}: {count} actual image files; {path.stat().st_size:,} bytes",flush=True)
    combined=ROOT/"US_Company_Logos_Top_2500_ALL.zip"
    zip_bundle(combined,records,metadata)
    count=validate_archive(combined)
    print(f"VERIFIED MASTER ZIP {combined}: {count} actual image files; {combined.stat().st_size:,} bytes",flush=True)
    report=dict(metadata,downloaded=count,missing=len(missing),
                missing_tickers=[r["ticker"] for r in missing],
                format_counts=dict(Counter(r["format"] for r in records if r["status"]=="downloaded")),
                source_counts=dict(Counter(r["source"] for r in records if r["status"]=="downloaded")))
    (ROOT/"SUMMARY.json").write_text(json.dumps(report,indent=2))
    if count<1800:
        raise RuntimeError(f"Logo coverage {count}/2500 below minimum publication threshold 1800")
    print("SUCCESS: READY TO RELEASE",flush=True)

if __name__=="__main__":
    main()
