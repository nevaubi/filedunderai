"""Structured parsing of stored IR documents with Docling (layout, reading order, table structure).

Runs on servers only, never in a browser. Two engines:
  local  the docling Python library in this process (GitHub Actions CPU matrix for the backfill)
  serve  a docling-serve deployment via its async API (POST /v1/convert/source/async, GET /v1/status/poll/{id},
         GET /v1/result/{id}); set DOCLING_SERVE_URL and DOCLING_SERVE_API_KEY. See docling/render.yaml.

Input: ir_documents rows (originals in the "ir" bucket at storage_path). Output per document, in the "ir" bucket:
  docling/{ticker}/{id}.json.gz  {"id", "ticker", "url", "engine", "pages", "seconds", "markdown", "tables": [...]}
Each table is {"page", "caption", "n_rows", "n_cols", "header", "rows"} with cell text, so KPI extraction and search
can read real table structure instead of flattened text.

Sharding is by crc32 of the document id, so parallel runners never take the same document."""
import argparse
import base64
import datetime as dt
import gzip
import io
import json
import os
import sys
import time
import zlib

from .common import Supabase, now_iso, request

BUCKET = "ir"
OUT = "docling/{ticker}/{id}.json.gz"
EXTS = (".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm")
MAX_PAGES = int(os.environ.get("DOCLING_MAX_PAGES", "300"))
MAX_BYTES = 60 * 1024 * 1024
# Table-dense documents first: financial supplements and earnings releases carry the KPI tables; decks next.
# Transcripts and prepared remarks are prose (the existing text extraction is enough) and annual reports duplicate
# XBRL-tagged filings, so they are parsed last and only when asked for.
PRIORITY = {"supplement": 0, "release": 1, "presentation": 2, "other": 3, "annual_report": 4, "transcript": 5, "remarks": 6}
DEFAULT_TYPES = ["supplement", "release", "presentation", "other"]


def shard_of(doc_id, shards):
    return zlib.crc32(doc_id.encode()) % max(1, shards)


def candidates(sb, shard=0, shards=1, exts=(".pdf",), doc_types=None, tickers=None):
    """ir_documents rows in this shard with a stored original, largest companies' newest documents first."""
    q = "select=id,ticker,url,title,doc_type,ext,pages,bytes,storage_path,published,fetched_at&storage_path=not.is.null"
    q += "&ext=in.(" + ",".join(exts) + ")"
    if doc_types:
        q += "&doc_type=in.(" + ",".join(doc_types) + ")"
    if tickers:
        q += "&ticker=in.(" + ",".join(tickers) + ")"
    q += "&order=published.desc.nullslast,fetched_at.desc"
    rows = sb.select("ir_documents", q)
    rows = [r for r in rows if shard_of(r["id"], shards) == shard and (r.get("bytes") or 0) <= MAX_BYTES
            and (r.get("pages") or 0) <= MAX_PAGES]
    # stable sort keeps newest-first inside each type
    rows.sort(key=lambda r: PRIORITY.get(r.get("doc_type") or "other", 3))
    return rows


def out_path(r):
    return OUT.format(ticker=r["ticker"], id=r["id"])


def exists(sb, path):
    url, headers = sb._storage_read_request(BUCKET, path)
    try:
        request(url, method="HEAD", headers=headers, raw=True, retries=1, timeout=20)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- table and document shaping

def table_rows(grid):
    """Docling table grid (list of rows of cells with .text) -> list of lists of strings, trimmed."""
    out = []
    for row in grid:
        cells = [(getattr(c, "text", None) if not isinstance(c, dict) else c.get("text")) or "" for c in row]
        out.append([" ".join(str(x).split()) for x in cells])
    while out and not any(out[-1]):
        out.pop()
    return out


def shape_tables_from_doc(doc):
    """Tables from a DoclingDocument (local engine)."""
    tables = []
    for t in getattr(doc, "tables", []) or []:
        grid = getattr(getattr(t, "data", None), "grid", None) or []
        rows = table_rows(grid)
        if not rows:
            continue
        page = None
        prov = getattr(t, "prov", None) or []
        if prov:
            page = getattr(prov[0], "page_no", None)
        cap = None
        try:
            cap = t.caption_text(doc) or None
        except Exception:
            cap = None
        n_header = 0
        for row in grid:
            if row and all(getattr(c, "column_header", False) for c in row if getattr(c, "text", "")):
                n_header += 1
            else:
                break
        tables.append({"page": page, "caption": cap, "n_rows": len(rows), "n_cols": max(len(r) for r in rows),
                       "header": rows[:n_header] if n_header else rows[:1], "rows": rows})
    return tables


def shape_tables_from_json(doc_json):
    """Tables from docling-serve's JSON output (export of a DoclingDocument)."""
    tables = []
    for t in (doc_json or {}).get("tables") or []:
        data = t.get("data") or {}
        grid = data.get("grid") or []
        rows = table_rows(grid)
        if not rows:
            continue
        prov = t.get("prov") or []
        n_header = 0
        for row in grid:
            if row and all(c.get("column_header") for c in row if c.get("text")):
                n_header += 1
            else:
                break
        tables.append({"page": prov[0].get("page_no") if prov else None, "caption": None, "n_rows": len(rows),
                       "n_cols": max(len(r) for r in rows), "header": rows[:n_header] if n_header else rows[:1], "rows": rows})
    return tables


# ---------------------------------------------------------------- engines

_CONVERTERS = {}


def table_mode(doc_type):
    """ACCURATE TableFormer for financial supplements (dense, multi-level tables); FAST elsewhere, which is about
    twice as quick on CPU. DOCLING_TABLES=accurate|fast forces one mode for every document."""
    forced = os.environ.get("DOCLING_TABLES", "").lower()
    if forced in ("accurate", "fast"):
        return forced
    return "accurate" if doc_type == "supplement" else "fast"


def local_converter(mode="accurate"):
    if mode not in _CONVERTERS:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.document_converter import DocumentConverter, PdfFormatOption
        opts = PdfPipelineOptions()
        opts.do_ocr = os.environ.get("DOCLING_OCR", "0") == "1"   # IR documents are born digital; OCR only on request
        opts.do_table_structure = True
        opts.table_structure_options.mode = TableFormerMode.ACCURATE if mode == "accurate" else TableFormerMode.FAST
        opts.generate_page_images = False
        _CONVERTERS[mode] = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return _CONVERTERS[mode]


def convert_local(body, name, mode="accurate"):
    from docling.datamodel.base_models import DocumentStream
    res = local_converter(mode).convert(DocumentStream(name=name, stream=io.BytesIO(body)), max_num_pages=MAX_PAGES, raises_on_error=True)
    doc = res.document
    return {"markdown": doc.export_to_markdown(), "tables": shape_tables_from_doc(doc), "pages": len(getattr(doc, "pages", {}) or {})}


def convert_serve(body, name, base=None, key=None, timeout=1800, poll=5):
    """docling-serve async API with the document sent inline (base64), so the server needs no storage access."""
    base = (base or os.environ["DOCLING_SERVE_URL"]).rstrip("/")
    key = key or os.environ.get("DOCLING_SERVE_API_KEY")
    h = {"Content-Type": "application/json", **({"X-Api-Key": key} if key else {})}
    payload = {"options": {"to_formats": ["md", "json"], "do_ocr": False, "table_mode": "accurate", "image_export_mode": "placeholder",
                           "abort_on_error": False},
               "file_sources": [{"base64_string": base64.b64encode(body).decode(), "filename": name}]}
    task = json.loads(request(f"{base}/v1/convert/source/async", method="POST", data=payload, headers=h, timeout=120, retries=3))
    tid = task["task_id"]
    t0 = time.time()
    while True:
        st = json.loads(request(f"{base}/v1/status/poll/{tid}", headers=h, timeout=60, retries=3))
        s = st.get("task_status")
        if s == "success":
            break
        if s in ("failure", "revoked"):
            raise RuntimeError(f"docling-serve task {tid} {s}: {st.get('task_meta')}")
        if time.time() - t0 > timeout:
            raise TimeoutError(f"docling-serve task {tid} still {s} after {timeout}s")
        time.sleep(poll)
    out = json.loads(request(f"{base}/v1/result/{tid}", headers=h, timeout=300, retries=2))
    d = out.get("document") or {}
    js = d.get("json_content") or {}
    return {"markdown": d.get("md_content") or "", "tables": shape_tables_from_json(js), "pages": len(js.get("pages") or {})}


# ---------------------------------------------------------------- run

def process(sb, r, engine="local"):
    body = sb.get_object(BUCKET, r["storage_path"])
    if not body:
        raise ValueError("stored original missing")
    name = r["storage_path"].split("/")[-1]
    t0 = time.time()
    mode = table_mode(r.get("doc_type"))
    out = convert_local(body, name, mode) if engine == "local" else convert_serve(body, name)
    rec = {"id": r["id"], "ticker": r["ticker"], "url": r.get("url"), "title": r.get("title"), "doc_type": r.get("doc_type"),
           "engine": engine, "table_mode": mode, "generated": now_iso(), "seconds": round(time.time() - t0, 1), "pages": out["pages"] or r.get("pages"),
           "n_tables": len(out["tables"]), "markdown": out["markdown"], "tables": out["tables"]}
    gz = gzip.compress(json.dumps(rec, separators=(",", ":"), ensure_ascii=False).encode(), 6)
    sb.put_object(BUCKET, out_path(r), gz, "application/gzip", "public, max-age=31536000, immutable")
    return rec


def run(sb, shard=0, shards=1, limit=50, hours=5.5, engine="local", doc_types=None, tickers=None, redo=False, log=print):
    deadline = time.time() + hours * 3600
    rows = candidates(sb, shard, shards, doc_types=doc_types, tickers=tickers)
    log(f"docling shard {shard}/{shards}: {len(rows)} candidate documents")
    done = failed = skipped = pages = tables = 0
    secs = 0.0
    errors = []
    for r in rows:
        if done >= limit or time.time() > deadline:
            break
        if not redo and exists(sb, out_path(r)):
            skipped += 1
            continue
        try:
            rec = process(sb, r, engine)
            done += 1
            pages += rec["pages"] or 0
            tables += rec["n_tables"]
            secs += rec["seconds"]
            log(f"  {r['ticker']} {r['id']} {rec['pages']}p {rec['n_tables']} tables {rec['seconds']}s")
        except Exception as e:
            failed += 1
            errors.append({"id": r["id"], "ticker": r["ticker"], "error": str(e)[:200]})
            log(f"  failed {r['ticker']} {r['id']}: {str(e)[:160]}")
    stats = {"shard": shard, "shards": shards, "engine": engine, "done": done, "failed": failed, "skipped_existing": skipped,
             "pages": pages, "tables": tables, "sec_per_page": round(secs / pages, 2) if pages else None, "errors": errors[:40],
             "finished": now_iso()}
    sb.put_object("raw", f"docling/runs/{dt.date.today().isoformat()}-s{shard}of{shards}-{int(time.time())}.json",
                  json.dumps(stats), "application/json", "no-store")
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=int(os.environ.get("SHARD", 0)))
    ap.add_argument("--shards", type=int, default=int(os.environ.get("SHARDS", 1)))
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--hours", type=float, default=5.5)
    ap.add_argument("--engine", choices=["local", "serve"], default="local")
    ap.add_argument("--doc-types", default="")
    ap.add_argument("--tickers", default="")
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args(argv)
    stats = run(Supabase(), a.shard, a.shards, a.limit, a.hours, a.engine,
                doc_types=[x for x in a.doc_types.split(",") if x] or DEFAULT_TYPES, tickers=[x for x in a.tickers.split(",") if x] or None, redo=a.redo)
    print(json.dumps(stats))
    return 0 if stats["done"] or not stats["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
