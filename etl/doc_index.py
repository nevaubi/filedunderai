"""Fills public.doc_chunks (Postgres full-text search) from stored IR documents.

Sources per document: the extracted page text ({T}/text/...json in the "ir" bucket, {"kind": "pages", "pages": [...]})
and, when the Docling pass has parsed it, its tables (docling/{T}/{id}.json.gz) as separate 'table' chunks so a query
for a KPI label finds the table that carries it. Progress lives in public.doc_index_state; a document is re-indexed
only when Docling output appears for one indexed from text alone.

Scope (default): releases, supplements, presentations, transcripts, prepared remarks and other IR documents
published since 2021; annual reports are left to the SEC filing search. Each document is capped at MAX_CHARS of
text so a 200-page deck cannot dominate the table.

Sharded by crc32 of the document id (parallel runners never overlap). Requires migration
supabase/migrations/20261108060000_doc_search.sql."""
import argparse
import gzip
import json
import os
import re
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

from .common import HTTPError, Supabase, now_iso, request

CHUNK = 1800
MAX_CHARS = int(os.environ.get("DOC_INDEX_MAX_CHARS", "60000"))
MAX_TABLE_CHUNKS = 40
TYPES = ["release", "supplement", "presentation", "transcript", "remarks", "other"]
SINCE = "2021-01-01"


def shard_of(doc_id, shards):
    return zlib.crc32(doc_id.encode()) % max(1, shards)


def clean(s):
    s = re.sub(r"[ \t ]+", " ", s or "")
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def split_text(text, size=CHUNK):
    """Chunks of at most `size` characters, broken at paragraph, then sentence, then word boundaries."""
    text = clean(text)
    out = []
    while text:
        if len(text) <= size:
            out.append(text)
            break
        cut = text.rfind("\n\n", 0, size)
        if cut < size * 0.5:
            cut = max(text.rfind(". ", 0, size), text.rfind("\n", 0, size))
        if cut < size * 0.5:
            cut = text.rfind(" ", 0, size)
        if cut <= 0:
            cut = size
        out.append(text[:cut + 1].strip())
        text = text[cut + 1:].strip()
    return [c for c in out if c]


def text_chunks(pages, max_chars=MAX_CHARS):
    """[(page, chunk text)] across pages, stopping at max_chars."""
    out, used = [], 0
    for i, p in enumerate(pages):
        p = p if isinstance(p, str) else json.dumps(p, ensure_ascii=False)
        for c in split_text(p):
            if used + len(c) > max_chars:
                return out
            out.append((i + 1, c))
            used += len(c)
    return out


def table_chunks(tables, limit=MAX_TABLE_CHUNKS):
    """One chunk per table (rows joined with ' | '), trimmed to CHUNK characters."""
    out = []
    for t in tables or []:
        rows = [" | ".join(x for x in r if x) for r in (t.get("rows") or [])]
        body = "\n".join(r for r in rows if r.strip())
        if not body:
            continue
        head = (t.get("caption") or "").strip()
        out.append((t.get("page"), ((head + "\n") if head else "") + body[:CHUNK - len(head) - 1]))
        if len(out) >= limit:
            break
    return out


def candidates(sb, shard, shards, types=TYPES, since=SINCE, tickers=None):
    q = ("select=id,ticker,doc_type,title,url,published,period_end,fiscal_label,text_path,fetched_at&text_path=not.is.null"
         f"&doc_type=in.({','.join(types)})")
    if tickers:
        q += f"&ticker=in.({','.join(tickers)})"
    rows = sb.select("ir_documents", q)
    keep = []
    for r in rows:
        when = r.get("published") or (r.get("fetched_at") or "")[:10]
        if since and when and when < since:
            continue
        if shard_of(r["id"], shards) == shard:
            keep.append(r)
    keep.sort(key=lambda r: r.get("published") or "", reverse=True)
    return keep


def docling_ids(sb, ticker):
    """Document ids with Docling output for one company."""
    try:
        files = json.loads(request(f"{sb.url}/storage/v1/object/list/ir", method="POST",
                                   data={"prefix": f"docling/{ticker}/", "limit": 1000}, headers=sb._h({"Content-Type": "application/json"})))
    except Exception:
        return set()
    return {f["name"][:-8] for f in files if f.get("name", "").endswith(".json.gz")}


def index_doc(sb, r, with_tables):
    meta = {k: r.get(k) for k in ("ticker", "doc_type", "title", "url", "published", "period_end", "fiscal_label")}
    meta["title"] = (meta["title"] or "").split("||")[0].replace(", opens in new window", "").strip()[:300] or None
    d = json.loads(sb.get_object("ir", r["text_path"]) or b"{}")
    pages = d.get("pages") or []
    rows = [{"doc_id": r["id"], **meta, "page": p, "kind": "text", "chunk": i, "content": c} for i, (p, c) in enumerate(text_chunks(pages))]
    source = "text"
    if with_tables:
        try:
            rec = json.loads(gzip.decompress(sb.get_object("ir", f"docling/{r['ticker']}/{r['id']}.json.gz")))
            rows += [{"doc_id": r["id"], **meta, "page": p, "kind": "table", "chunk": i, "content": c}
                     for i, (p, c) in enumerate(table_chunks(rec.get("tables")))]
            source = "text+docling"
        except Exception:
            pass
    # replace this document's chunks
    sb.delete("doc_chunks", f"doc_id=eq.{r['id']}")
    for i in range(0, len(rows), 400):
        sb.upsert("doc_chunks", rows[i:i + 400], on_conflict="doc_id,kind,chunk")
    sb.upsert("doc_index_state", [{"doc_id": r["id"], "ticker": r["ticker"], "source": source, "chunks": len(rows),
                                   "chars": sum(len(x["content"]) for x in rows), "indexed_at": now_iso()}], on_conflict="doc_id")
    return len(rows)


def run(sb, shard=0, shards=1, hours=5.5, workers=6, types=TYPES, since=SINCE, tickers=None, log=print):
    deadline = time.time() + hours * 3600
    try:
        sb.select("doc_index_state", "select=doc_id&limit=1")
    except HTTPError as e:
        raise SystemExit(f"doc_index_state is missing; apply supabase/migrations/20261108060000_doc_search.sql first ({str(e)[:120]})")
    rows = candidates(sb, shard, shards, types, since, tickers)
    state = {}
    for s in sb.select("doc_index_state", "select=doc_id,source"):
        state[s["doc_id"]] = s["source"]
    by_t = {}
    for r in rows:
        by_t.setdefault(r["ticker"], []).append(r)
    with ThreadPoolExecutor(8) as ex:
        dl = dict(zip(by_t, ex.map(lambda t: docling_ids(sb, t), by_t)))
    todo = []
    for r in rows:
        has_tables = r["id"] in dl.get(r["ticker"], set())
        st = state.get(r["id"])
        if st is None or (has_tables and st == "text"):
            todo.append((r, has_tables))
    log(f"doc index shard {shard}/{shards}: {len(rows)} in scope, {len(todo)} to index")
    done = chunks = failed = 0

    def one(item):
        r, tables = item
        if time.time() > deadline:
            return None
        try:
            return index_doc(sb, r, tables)
        except Exception as e:
            log(f"  failed {r['ticker']} {r['id']}: {str(e)[:160]}")
            return -1
    with ThreadPoolExecutor(workers) as ex:
        for n in ex.map(one, todo):
            if n is None:
                continue
            if n < 0:
                failed += 1
            else:
                done += 1
                chunks += n
                if done % 500 == 0:
                    log(f"  {done} documents, {chunks} chunks")
    return {"shard": shard, "in_scope": len(rows), "indexed": done, "chunks": chunks, "failed": failed, "remaining": len(todo) - done - failed}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=int(os.environ.get("SHARD", 0)))
    ap.add_argument("--shards", type=int, default=int(os.environ.get("SHARDS", 1)))
    ap.add_argument("--hours", type=float, default=5.5)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--since", default=SINCE)
    ap.add_argument("--types", default=",".join(TYPES))
    ap.add_argument("--tickers", default="")
    a = ap.parse_args(argv)
    out = run(Supabase(), a.shard, a.shards, a.hours, a.workers, [t for t in a.types.split(",") if t], a.since or None,
              [t for t in a.tickers.split(",") if t] or None)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
