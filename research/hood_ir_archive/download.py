#!/usr/bin/env python3
"""Mirror all Robinhood quarterly earnings originals plus quarter-matched SEC formats.

The official Nasdaq/GlobeNewswire IR index is the source of truth for earnings quarters.
Quarterly supplements, filings and source data are preserved without conversion.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

ROOT_URL = "https://robinhood.gcs-web.com"
RESULTS = ROOT_URL + "/financials/quarterly-results"
FILINGS = ROOT_URL + "/financials/sec-filings"
LOCAL = threading.local()
FILES = []
OTHER_LINKS = []
DISCOVERY_ERRORS = []
VALID_HOSTS = {"robinhood.gcs-web.com", "investors.robinhood.com"}
TYPES = {
    "application/pdf":".pdf",
    "application/rtf":".rtf",
    "application/msword":".doc",
    "application/vnd.ms-excel":".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation":".pptx",
    "application/xml":".xml",
    "text/xml":".xml",
    "text/html":".html",
    "image/png":".png",
    "image/jpeg":".jpg",
    "text/csv":".csv",
    "application/json":".json",
    "application/zip":".zip",
}
EXTS = set(TYPES.values()) | {".xsd",".xbrl",".txt",".gif",".svg",".ppt",".mp3",".mp4",".webp",".htm"}

def session():
    if not hasattr(LOCAL, "session"):
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        retry = Retry(total=4, connect=4, read=4, status=4, backoff_factor=1.3,
                      status_forcelist=(408,425,429,500,502,503,504),
                      allowed_methods=frozenset(("GET",)), respect_retry_after_header=True)
        s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=12, pool_maxsize=12))
        LOCAL.session=s
    return LOCAL.session

def get_page(url, params=None):
    r=session().get(url, params=params, timeout=(25,90))
    r.raise_for_status()
    return BeautifulSoup(r.content,"html.parser")

def ext_for(title,mime):
    title=title.strip().split("?")[0]
    m=re.search(r"(\.[A-Za-z0-9]{2,5})$",title)
    if m and m.group(1).lower() in EXTS:
        return m.group(1).lower()
    return TYPES.get(mime.lower().split(";")[0].strip(),"") or ".bin"

def make_record(*,year,quarter,category,title,url,mime="",source_page="",accession="",form="",filing_date="",document_date=""):
    extension=ext_for(title,mime)
    result={"year":year,"quarter":quarter,"period":str(year)+"-Q"+str(quarter),
        "category":category,"title":title,"url":url,"mime_type":mime,
        "extension":extension,"source_page":source_page,"accession":accession,
        "form":form,"filing_date":filing_date,"document_date":document_date}
    return result

def discover_main():
    soup=get_page(RESULTS)
    year_cards=soup.select(".results-slider .slick-slide")
    if len(year_cards)<6:
        raise RuntimeError("Earnings archive HTML did not provide 2021–2026 year cards")
    raw=[]
    for card in year_cards:
        heading=card.select_one("h2.h4")
        if not heading:continue
        yr_text=heading.get_text(" ",strip=True)
        if not re.fullmatch(r"20\d{2}",yr_text):continue
        year=int(yr_text)
        for row in card.select("li.views-row"):
            category_tag=row.select_one("h3.result-title")
            if not category_tag:continue
            category=category_tag.get_text(" ",strip=True)
            for item in row.select("li[data-order]"):
                try:quarter=int(item.get("data-order"))
                except (TypeError,ValueError):continue
                if quarter not in (1,2,3,4):continue
                for a in item.select("a[href]"):
                    url=urljoin(ROOT_URL,a.get("href",""))
                    title=a.get("title") or a.get("aria-label") or a.get_text(" ",strip=True)
                    mime=a.get("type") or ""
                    if "/static-files/" in url:
                        raw.append(make_record(year=year,quarter=quarter,category=category,
                            title=title,url=url,mime=mime,source_page=RESULTS))
                    else:
                        OTHER_LINKS.append({"period":str(year)+"-Q"+str(quarter),
                            "category":category,"url":url,"title":title,"source_page":RESULTS})
    reported={r["period"] for r in raw if r["category"]=="Press Release"}
    # A Q4 2026 general investor presentation is available, but Q4 earnings have not occurred.
    quarter_files=[r for r in raw if r["period"] in reported]
    extra=[r for r in raw if r["period"] not in reported]
    for r in extra:
        r["period"]="Supplemental-"+str(r["year"])
        r["category"]="Standalone Investor Presentation"
    if len(reported)!=22 or "2021-Q2" not in reported or "2026-Q3" not in reported:
        raise RuntimeError("Expected 22 published Robinhood quarters, 2021-Q2 through 2026-Q3, got "+str(sorted(reported)))
    if len(quarter_files)<90:
        raise RuntimeError("Unexpectedly few downloadable earnings items: "+str(len(quarter_files)))
    FILES.extend(quarter_files)
    FILES.extend(extra)
    print("PRIMARY_INDEX",len(quarter_files),"quarter files",len(extra),"other presentations",
          len(reported),"quarters",len(OTHER_LINKS),"external webcast links",flush=True)
    return reported

def collect_sec_listing():
    seen=set()
    listings=[]
    for filing_year in range(2021,2027):
        for group in ("496","471"):
            for page in range(4):
                params={"field_nir_sec_form_group_target_id":group,
                        "field_nir_sec_date_filed_value":str(filing_year),
                        "items_per_page":"100","page":str(page)}
                try:
                    soup=get_page(FILINGS,params=params)
                except Exception as exc:
                    DISCOVERY_ERRORS.append({"stage":"filing_index","year":filing_year,
                        "group":group,"page":page,"error":str(exc)})
                    break
                trs=soup.select("table.nirtable tbody tr")
                if not trs:
                    break
                new=0
                for tr in trs:
                    cells=tr.find_all("td",recursive=False)
                    if len(cells)<2:continue
                    a=cells[1].select_one("a[href]")
                    if not a:continue
                    form=a.get_text(" ",strip=True).upper()
                    if form not in ("10-Q","10-K","10-Q/A","10-K/A"):continue
                    link=urljoin(ROOT_URL,a.get("href",""))
                    if link in seen:continue
                    seen.add(link);new+=1
                    t=cells[0].select_one("time")
                    listings.append({"form":form,"url":link,
                                     "filing_date":t.get_text(" ",strip=True) if t else ""})
                # If there is no next page, do not retry a duplicate first page.
                if not soup.select("li.pager__item--next a, a[rel='next']") or new==0:
                    break
    print("SEC_FILING_INDEX",len(listings),"filing details discovered",flush=True)
    return listings

def detail_one(entry, reported):
    data=[]
    try:
        soup=get_page(entry["url"])
        date_field=soup.select_one(".field--name-field-nir-sec-doc-date .field__item")
        if not date_field:
            raise ValueError("Document Date was not available")
        date=date_field.get_text(" ",strip=True)
        d=datetime.strptime(date,"%b %d, %Y")
        year=d.year
        quarter=(d.month-1)//3+1
        period=str(year)+"-Q"+str(quarter)
        if period not in reported:
            return [],None
        accession=(entry["url"].rstrip("/").split("/")[-1])
        for section in soup.select(".doc-group,.xbrl-group"):
            for a in section.select("a[href]"):
                url=urljoin(ROOT_URL,a.get("href",""))
                title=a.get("title") or a.get_text(" ",strip=True)
                mime=a.get("type") or ""
                if "/static-files/" in url:
                    role="SEC Filing - "+entry["form"]+(" XBRL" if "xbrl-group" in section.get("class",[]) else " Formats")
                    data.append(make_record(year=year,quarter=quarter,category=role,title=title,
                        url=url,mime=mime,source_page=entry["url"],accession=accession,
                        form=entry["form"],filing_date=entry["filing_date"],document_date=date))
                elif (a.get_text(" ",strip=True).lower()=="view html" and
                      "/html" in url and urlparse(url).hostname in VALID_HOSTS):
                    data.append(make_record(year=year,quarter=quarter,
                        category="SEC Filing - "+entry["form"]+" HTML",
                        title=accession+".html",url=url,mime="text/html",
                        source_page=entry["url"],accession=accession,
                        form=entry["form"],filing_date=entry["filing_date"],document_date=date))
        if not data:
            raise ValueError("Detail page exposed no downloadable formats")
    except Exception as ex:
        return [],{"stage":"filing_detail","url":entry["url"],"error":str(ex)}
    return data,None

def discover_sec(reported):
    filings=collect_sec_listing()
    all_data=[]
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs=[pool.submit(detail_one,f,reported) for f in filings]
        for f in as_completed(jobs):
            arr,err=f.result()
            all_data.extend(arr)
            if err:DISCOVERY_ERRORS.append(err)
    FILES.extend(all_data)
    print("SEC_SOURCE_ATTACHMENTS",len(all_data),"from",
          len(set(x["source_page"] for x in all_data)),"filing pages",
          "discovery_errors",len(DISCOVERY_ERRORS),flush=True)

def unique_records(entries):
    seen=set()
    out=[]
    for r in entries:
        key=(r["period"],r["url"])
        if key in seen:continue
        seen.add(key)
        out.append(r)
    return sorted(out,key=lambda r:(r["year"],r["quarter"],r["category"],r["title"],r["url"]))

def safe_name(r):
    ext=r["extension"]
    title=r["title"]
    if title.lower().endswith(ext):
        title=title[:-len(ext)]
    cat=re.sub(r"[^A-Za-z0-9._-]+","_",r["category"]).strip("_")
    name=re.sub(r"[^A-Za-z0-9._-]+","_",title).strip("_")
    slug=(cat+"__"+name)[:165].rstrip("._-")
    # UUID source filenames are not human-readable; suffix makes names collision-proof.
    unique=hashlib.sha256(r["url"].encode("utf-8")).hexdigest()[:8]
    return slug+"__"+unique+ext

def target_path(root,r):
    if r["period"].startswith("Supplemental-"):
        folder=root/"Supplemental_Investor_Presentations"/str(r["year"])
    else:
        folder=root/str(r["year"])/r["period"]
        folder=folder/("SEC_Filings_and_XBRL" if r["category"].startswith("SEC Filing") else "Earnings_and_Supplements")
    return folder/safe_name(r)

def detect_valid(ext,first):
    if not first:return False
    if ext==".pdf":return first.startswith(b"%PDF-")
    if ext in (".xlsx",".pptx",".docx",".zip"):return first.startswith(b"PK")
    if ext==".rtf":return first.lstrip().startswith(b"{\\rtf")
    if ext==".xls":
        f=first.lstrip().lower()
        return first.startswith(bytes.fromhex("D0CF11E0A1B11AE1")) or first.startswith(b"PK") or f.startswith((b"<html",b"<?xml",b"<!doctype",b"<table"))
    if ext in (".xml",".xsd",".xbrl"):
        return first.lstrip().startswith(b"<")
    if ext in (".htm",".html"):
        return first.lstrip().lower().startswith((b"<!doctype",b"<html",b"<?xml",b"<head",b"<body"))
    if ext in (".jpeg",".jpg"):return first.startswith(b"\xff\xd8")
    if ext==".png":return first.startswith(b"\x89PNG")
    return True

def sha_file(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for part in iter(lambda:f.read(1024*1024),b""):
            h.update(part)
    return h.hexdigest()

def fetch_one(rec,root,maxmb):
    r=dict(rec)
    path=target_path(root,r)
    path.parent.mkdir(exist_ok=True,parents=True)
    r.update(relative_path=str(path.relative_to(root)),status="failed",
             size_bytes=0,sha256="",http_status="",content_type="",error="")
    if path.exists() and path.stat().st_size:
        r.update(status="existing",size_bytes=path.stat().st_size,sha256=sha_file(path))
        return r
    temp=path.with_name(path.name+".part")
    try:
        host=urlparse(r["url"]).hostname
        if host not in VALID_HOSTS:raise ValueError("Unexpected source domain: "+str(host))
        with session().get(r["url"],timeout=(25,120),stream=True) as response:
            r["http_status"]=response.status_code
            response.raise_for_status()
            if urlparse(response.url).hostname not in VALID_HOSTS:
                raise ValueError("Unexpected final redirect domain "+response.url)
            head=b""
            total=0
            digest=hashlib.sha256()
            with temp.open("wb") as out:
                for chunk in response.iter_content(chunk_size=262144):
                    if not chunk:continue
                    total+=len(chunk)
                    if total>maxmb*1048576:raise ValueError("File exceeds max-file-mb")
                    if len(head)<128:head+=chunk[:128-len(head)]
                    digest.update(chunk)
                    out.write(chunk)
            if not detect_valid(r["extension"],head):
                raise ValueError("File signature did not match "+r["extension"])
            os.replace(temp,path)
            r.update(status="downloaded",size_bytes=total,
                     sha256=digest.hexdigest(),content_type=response.headers.get("content-type",""))
    except Exception as e:
        r["error"]=str(e)[:900]
        temp.unlink(missing_ok=True)
    return r

def write_outputs(root,reported,rows):
    inv={"company":"Robinhood Markets, Inc.","ticker":"HOOD","retrieved_date_utc":datetime.now(timezone.utc).isoformat(),
         "official_quarterly_results_url":"https://investors.robinhood.com/financials/quarterly-results",
         "official_sec_filings_url":"https://investors.robinhood.com/financials/sec-filings",
         "reported_quarters":sorted(reported),"published_original_urls":FILES,
         "non_document_links":OTHER_LINKS,"discovery_warnings":DISCOVERY_ERRORS}
    (root/"official_source_inventory.json").write_text(json.dumps(inv,indent=2),encoding="utf-8")
    cols=["year","quarter","period","category","title","url","mime_type","extension",
          "source_page","accession","form","filing_date","document_date","relative_path",
          "status","http_status","size_bytes","sha256","content_type","error"]
    with open(root/"download_manifest.csv","w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols,extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows,key=lambda x:(x["year"],x["quarter"],x["category"],x["title"])))
    statuses=Counter(r["status"] for r in rows)
    ext=Counter(r["extension"] for r in rows)
    cats=Counter(r["category"] for r in rows)
    perq=Counter(r["period"] for r in rows if not r["period"].startswith("Supplemental-"))
    summary={
        "ticker":"HOOD",
        "reported_quarter_count":len(reported),
        "quarter_range":"2021-Q2 through 2026-Q3",
        "expected_total_documents":len(rows),
        "files_downloaded":statuses["downloaded"]+statuses["existing"],
        "failed_downloads":statuses["failed"],
        "file_bytes":sum(r["size_bytes"] for r in rows),
        "extensions":dict(sorted(ext.items())),
        "categories":dict(sorted(cats.items())),
        "files_by_quarter":dict(sorted(perq.items())),
        "non_document_links":len(OTHER_LINKS),
        "discovery_warnings":DISCOVERY_ERRORS,
        "failures":[{"period":r["period"],"title":r["title"],"url":r["url"],
                     "error":r["error"],"http_status":r["http_status"]}
                    for r in rows if r["status"]=="failed"],
    }
    (root/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    (root/"README.md").write_text(
        "# Robinhood Markets (HOOD) quarterly investor-relations archive\n\n"
        "Official quarterly source: https://investors.robinhood.com/financials/quarterly-results\n\n"
        "Scope: all available publicly reported quarterly earnings (Q2 2021 to Q3 2026), "
        "including original PDF releases and presentations, transcript PDFs, "
        "Excel earnings supplements, published periodic-report PDFs, supplemental investor presentations, "
        "and the PDF, RTF, XLS, XBRL schema/XML and HTML formats separately supplied "
        "on corresponding official SEC filing detail pages.\n\n"
        "A standalone Q4 2026 investor presentation is included in Supplemental_Investor_Presentations, "
        "but is not represented as Q4 earnings (Q4 2026 is not yet reported).\n\n"
        "Original-source URLs, SHA-256 checksums, response types, sizes and failures are included "
        "in download_manifest.csv and summary.json. External earnings webcast/media links are "
        "catalogued in official_source_inventory.json but streaming recordings are not downloaded.\n\n"
        "Duplicates may be present when the IR quarterly page and the SEC filing page publish "
        "the same document under different source URLs.\n",encoding="utf-8")
    print("SUMMARY",json.dumps({k:v for k,v in summary.items()
          if k not in ("files_by_quarter","categories","failures","discovery_warnings")}),flush=True)
    print("FAILURES",json.dumps(summary["failures"][:20]),flush=True)
    print("DISCOVERY_WARNINGS",json.dumps(DISCOVERY_ERRORS[:20]),flush=True)
    return summary

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default="HOOD_Quarterly_Earnings")
    parser.add_argument("--workers",type=int,default=7)
    parser.add_argument("--max-file-mb",type=int,default=160)
    args=parser.parse_args()
    root=Path(args.output)
    root.mkdir(parents=True,exist_ok=True)
    reported=discover_main()
    discover_sec(reported)
    records=unique_records(FILES)
    print("ARCHIVE_INVENTORY",len(records),"original asset URLs",
          dict(Counter(r["extension"] for r in records)),flush=True)
    rows=[]
    with ThreadPoolExecutor(max_workers=min(12,max(1,args.workers))) as pool:
        jobs=[pool.submit(fetch_one,r,root,args.max_file_mb) for r in records]
        for i,f in enumerate(as_completed(jobs),1):
            r=f.result()
            rows.append(r)
            if r["status"]=="failed" or i%25==0 or i==len(jobs):
                print("[%s/%s]"%(i,len(jobs)),r["status"],r["period"],r["category"],r["title"],r["error"],flush=True)
    s=write_outputs(root,reported,rows)
    print("FINISHED",s["files_downloaded"],"/",s["expected_total_documents"],"files.",flush=True)
    return 0 if s["failed_downloads"]==0 and not DISCOVERY_ERRORS else 1
if __name__=="__main__":
    sys.exit(main())
