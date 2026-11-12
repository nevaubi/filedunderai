#!/usr/bin/env python3
"""Download original documents linked by JPMorganChase quarterly earnings."""
import argparse,csv,hashlib,json,os,re,shutil,threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from urllib.parse import urlparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

INVENTORY=Path(__file__).resolve().parent/"official_manifest.json"
THREAD=threading.local()
def session():
    if not hasattr(THREAD,"s"):
        s=requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0 (compatible; InstitutionalIRResearch/1.0)",
                          "Accept":"application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*"})
        retry=Retry(total=4,connect=4,read=3,status=4,backoff_factor=2,
                    status_forcelist=[408,425,429,500,502,503,504],
                    allowed_methods=frozenset(["GET"]),respect_retry_after_header=True)
        s.mount("https://",HTTPAdapter(max_retries=retry))
        THREAD.s=s
    return THREAD.s
def make_name(r):
    name=re.sub(r"^\dQ\d\d\s+","",r["title"]).strip()
    name=re.sub(r"^\d{4}\s+(?=10-K)","",name)
    name=re.sub(r"[^\w.-]+","_",name).strip("._")[:110]
    return r["period"]+"__"+name+r["extension"]
def digest(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for block in iter(lambda:f.read(1048576),b""):h.update(block)
    return h.hexdigest()
def fetch(rec,root,maxmb):
    r=dict(rec)
    target=root/str(r["year"])/r["period"]/make_name(r)
    target.parent.mkdir(parents=True,exist_ok=True)
    r.update(relative_path=str(target.relative_to(root)),status="failed",
             size_bytes=0,sha256="",http_status="",error="",content_type="")
    if target.is_file() and target.stat().st_size>0:
        r.update(status="existing",size_bytes=target.stat().st_size,sha256=digest(target))
        return r
    part=target.with_name(target.name+".part")
    try:
        if urlparse(r["url"]).hostname not in ("www.jpmorganchase.com","jpmorganchase.com"):
            raise ValueError("Source host not official JPMorganChase")
        with session().get(r["url"],timeout=(20,120),stream=True) as resp:
            r["http_status"]=resp.status_code
            resp.raise_for_status()
            if urlparse(resp.url).hostname not in ("www.jpmorganchase.com","jpmorganchase.com"):
                raise ValueError("Unexpected redirect host: "+resp.url)
            n=0;first=b"";h=hashlib.sha256()
            with open(part,"wb") as out:
                for block in resp.iter_content(chunk_size=262144):
                    if not block:continue
                    n+=len(block)
                    if n>maxmb*1048576:raise ValueError("Exceeded per-file byte limit")
                    if len(first)<8:first+=block[:8-len(first)]
                    h.update(block);out.write(block)
            if n==0:raise ValueError("Empty file")
            if r["extension"]==".pdf" and not first.startswith(b"%PDF-"):
                raise ValueError("File was not a PDF")
            if r["extension"] in (".xlsx",".pptx",".docx") and not first.startswith(b"PK"):
                raise ValueError("File was not an Office document")
            os.replace(part,target)
            r.update(status="downloaded",size_bytes=n,sha256=h.hexdigest(),
                     content_type=resp.headers.get("content-type",""))
    except Exception as e:
        r["error"]=str(e)[:1000]
        part.unlink(missing_ok=True)
    return r
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",default="JPM_Quarterly_Earnings")
    ap.add_argument("--workers",type=int,default=6)
    ap.add_argument("--max-file-mb",type=int,default=125)
    args=ap.parse_args()
    inv=json.loads(INVENTORY.read_text(encoding="utf-8"))
    entries=inv["files"]
    assert len(entries)==355 and len(set(r["period"] for r in entries))==87
    root=Path(args.output)
    root.mkdir(parents=True,exist_ok=True)
    (root/"official_manifest.json").write_text(json.dumps(inv,indent=2),encoding="utf-8")
    results=[]
    with ThreadPoolExecutor(max_workers=min(max(1,args.workers),12)) as pool:
        jobs=[pool.submit(fetch,r,root,args.max_file_mb) for r in entries]
        for i,f in enumerate(as_completed(jobs),1):
            r=f.result();results.append(r)
            if r["status"]=="failed" or i%20==0 or i==len(entries):
                print("[%s/%s] %s %s %s %s"%(i,len(entries),r["status"],r["period"],r["title"],r["error"]),flush=True)
    keys=["year","quarter","period","title","extension","url","relative_path",
          "status","http_status","size_bytes","sha256","content_type","error"]
    with open(root/"download_manifest.csv","w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=keys,extrasaction="ignore");w.writeheader()
        w.writerows(sorted(results,key=lambda r:(r["year"],r["quarter"],r["title"])))
    statuses=Counter(r["status"] for r in results)
    summary={"source":inv["index"],"periods":len(set(r["period"] for r in entries)),
             "source_direct_files":len(entries),"webcast_only_links":len(inv["webcasts"]),
             "successful":statuses["downloaded"]+statuses["existing"],"failed":statuses["failed"],
             "bytes_downloaded":sum(r["size_bytes"] for r in results),
             "formats":dict(Counter(r["extension"] for r in entries)),
             "failures":[{"period":r["period"],"title":r["title"],"url":r["url"],
                          "error":r["error"],"http_status":r["http_status"]}
                         for r in results if r["status"]=="failed"]}
    (root/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    (root/"README.md").write_text(
        "# JPMorganChase quarterly earnings archive\n\nOfficial source: "+inv["index"]+"\n\n"
        "Scope: all 355 directly linked files from Q1 2005 through Q3 2026.\n\n"
        "Original PDF and XLSX bytes are preserved, grouped in year/quarter directories.\n\n"
        "The 18 external webcast links are recorded in official_manifest.json, but streaming "
        "audio/video files are not downloaded.\n\n"
        "See download_manifest.csv and summary.json for checksums and failures.\n",encoding="utf-8")
    z=shutil.make_archive(str(root),"zip",root_dir=root.parent,base_dir=root.name)
    print("ARCHIVE",z,Path(z).stat().st_size,"bytes",flush=True)
    print("SUMMARY",json.dumps(summary),flush=True)
    return 0 if not statuses["failed"] else 1
if __name__=="__main__":
    raise SystemExit(main())
