"""Stage narrowly reviewed KPI corrections; never execute database mutations.

``plan_repair`` reads live state and local hash-verified originals. It seals exact
database/public before and after states. ``render_guarded_sql`` emits a single
rollback-safe transaction for separate execution while the existing
``financial-aggregates`` lease is held. ``archive_stage`` and ``finalize_repair``
are explicit storage writes and require that same live lease owner.

Storage is not transactional with Postgres. An interrupted finalization can be
resumed from the sealed stage; rollback SQL and public restoration use the same
exact-state guards. No crawler, model, deletion or broad rebuild is invoked.
"""
import argparse
import base64
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote

from .common import Supabase, load_env, request


VALUE_FIELDS = frozenset(("ticker", "kpi_id", "period_kind", "period_end", "fiscal_label", "value",
                         "unit", "source_kind", "source_id", "anchor", "derived", "reconciled", "updated_at"))
DEFINITION_FIELDS = frozenset(("ticker", "kpi_id", "name", "category", "subcategory", "unit", "parent_id",
                              "sums_to_parent", "source_kind", "description", "sort", "updated_at"))
VALUE_CHANGES = frozenset(("value", "unit", "period_kind", "period_end", "fiscal_label", "anchor", "updated_at"))
DEFINITION_CHANGES = frozenset(("unit", "updated_at"))
KEY = ("ticker", "kpi_id", "period_kind", "period_end", "source_kind")
LEASE = "financial-aggregates"
MAX_CORRECTIONS = 1000


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=str).encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def _number(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a financial value")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid financial value") from exc
    if not number.is_finite():
        raise ValueError("Nonfinite financial value")
    return number


def _key(row):
    return tuple(row[k] for k in KEY)


def _canon(row):
    row = copy.deepcopy(row)
    if "value" in row:
        row["value"] = _number(row["value"]).normalize().to_eng_string()
    if row.get("updated_at"):
        row["updated_at"] = dt.datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")).astimezone(dt.timezone.utc).isoformat()
    return row


def _rows_equal(left, right, definitions=False):
    key = (lambda r: (r["ticker"], r["kpi_id"])) if definitions else _key
    a, b = {key(r): _canon(r) for r in left}, {key(r): _canon(r) for r in right}
    return len(a) == len(left) and len(b) == len(right) and a == b


def _query(ticker, ids):
    quoted = ",".join(json.dumps(k) for k in sorted(ids))
    return f"select=*&ticker=eq.{quote(ticker, safe='')}&kpi_id=in.({quote(quoted, safe='')})"


def _read_db(sb, ticker, ids):
    query = _query(ticker, ids)
    return {"kpi_definitions": sb.select("kpi_definitions", query + "&order=kpi_id"),
            "kpi_values": sb.select("kpi_values", query + "&order=kpi_id,period_kind,period_end,source_kind")}


def _same_db(left, right):
    return all(_rows_equal(left[t], right[t], t == "kpi_definitions") for t in left)


def _full_row(row, fields, ticker):
    if set(row) != fields or row.get("ticker") != ticker or row.get("source_kind") != "ir":
        raise ValueError("Expected full, exact IR row for the scoped company")
    if "value" in fields:
        _number(row["value"])
        if row["period_kind"] not in "QAIYT" or len(row["period_kind"]) != 1:
            raise ValueError("Invalid period kind")
        dt.date.fromisoformat(row["period_end"])


def _proofs(manifest, correction):
    ids = correction.get("proof_ids") or []
    proofs = manifest.get("source_proofs") or {}
    if not ids or any(pid not in proofs for pid in ids):
        raise ValueError("Every correction needs a source evidence manifest")
    return [proofs[pid] for pid in ids]


def _validate_manifest(manifest):
    ticker = manifest.get("ticker")
    changes = manifest.get("row_corrections") or []
    if manifest.get("schema_version") != 1 or not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9.^-]{1,15}", ticker):
        raise ValueError("Unsupported evidence manifest")
    if not 1 <= len(changes) <= MAX_CORRECTIONS or manifest.get("expected_count") != len(changes):
        raise ValueError("Exact correction count required (1–1000)")
    prepared = manifest.get("prepared_at")
    dt.datetime.fromisoformat(prepared.replace("Z", "+00:00"))
    seen, destinations = set(), set()
    for change in changes:
        before, after = change["before"], change["after"]
        _full_row(before, VALUE_FIELDS, ticker); _full_row(after, VALUE_FIELDS, ticker)
        if _key(before) in seen or _key(after) in destinations:
            raise ValueError("Duplicate source key or destination collision")
        seen.add(_key(before)); destinations.add(_key(after))
        changed = {k for k in before if before[k] != after[k]}
        if not changed or changed - VALUE_CHANGES:
            raise ValueError("Correction changes a column outside the allowlist")
        old_anchor, new_anchor = before.get("anchor") or {}, after.get("anchor") or {}
        if {k: v for k, v in old_anchor.items() if k != "quote"} != {k: v for k, v in new_anchor.items() if k != "quote"}:
            raise ValueError("Source cell, sheet and page may not move")
        if "updated_at" in changed and after["updated_at"] != prepared:
            raise ValueError("Correction timestamp must equal the reviewed prepared_at")
        proved = False
        for proof in _proofs(manifest, change):
            period = proof.get("mapped_period") or {}
            period_supported = (period.get("kind") == after["period_kind"] and period.get("end") == after["period_end"])
            if (proof.get("retained_period_not_changed") is True
                    and (before["period_kind"], before["period_end"]) == (after["period_kind"], after["period_end"])):
                period_supported = True
            if (proof.get("source_id") == before["source_id"] and proof.get("unit") == after["unit"]
                    and period_supported
                    and "reported_value" in proof and "scale" in proof):
                scale = _number(proof["scale"])
                if scale > 0 and _number(proof["reported_value"]) * scale == _number(after["value"]):
                    proved = True
        if not proved:
            raise ValueError("Source evidence does not prove the after value/unit and any changed period")
    expected_defs = manifest.get("expected_definitions") or []
    for row in expected_defs:
        _full_row(row, DEFINITION_FIELDS, ticker)
    ids = {c["before"]["kpi_id"] for c in changes}
    if {d["kpi_id"] for d in expected_defs} != ids or len(expected_defs) != len(ids):
        raise ValueError("Full expected definitions required for every affected metric")
    definition_keys = set()
    for change in manifest.get("definition_corrections") or []:
        before, after = change["before"], change["after"]
        _full_row(before, DEFINITION_FIELDS, ticker); _full_row(after, DEFINITION_FIELDS, ticker)
        if before["kpi_id"] not in ids or before["kpi_id"] in definition_keys:
            raise ValueError("Unscoped or duplicate definition correction")
        definition_keys.add(before["kpi_id"])
        changed = {k for k in before if before[k] != after[k]}
        if not changed or changed - DEFINITION_CHANGES:
            raise ValueError("Definition change is outside the unit/timestamp allowlist")
        if "updated_at" in changed and after["updated_at"] != prepared:
            raise ValueError("Definition timestamp must equal prepared_at")
        _proofs(manifest, change)
    return ticker, ids


def _verify_sources(sb, manifest):
    proofs = manifest["source_proofs"]
    ids = {p["source_id"] for p in proofs.values()}
    quoted = quote(",".join(json.dumps(s) for s in sorted(ids)), safe="")
    docs = {d["id"]: d for d in sb.select("ir_documents", f"select=*&ticker=eq.{manifest['ticker']}&id=in.({quoted})")}
    checked = set()
    for proof in proofs.values():
        sid, sha = proof["source_id"], proof.get("sha256")
        doc = docs.get(sid)
        if (not doc or not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha)
                or sha != doc.get("sha256") or proof.get("bytes") != doc.get("bytes")
                or (proof.get("url") or proof.get("source_url")) != doc.get("url")):
            raise ValueError("Source metadata/hash differs from the evidence manifest")
        path = proof.get("local_original")
        if not path:
            raise ValueError("A local original is required to verify source bytes")
        if (sid, path) not in checked:
            body = Path(path).read_bytes()
            if len(body) != proof["bytes"] or digest(body) != sha:
                raise ValueError("Original source bytes/hash mismatch")
            checked.add((sid, path))
    return [{k: doc[k] for k in ("id", "ticker", "url", "bytes", "sha256")} for doc in docs.values()]


def _object(sb, path):
    raw = sb.get_object("public-data", path)
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.encode()
    return {"path": path, "before": json.loads(raw), "before_sha256": digest(raw),
            "before_bytes": base64.b64encode(raw).decode()}


def _public_point_matches(point, item, row):
    anchor = row.get("anchor") or {}
    return (_number(point["v"]) == _number(row["value"]) and item.get("unit") == row["unit"]
            and point.get("doc") == row["source_id"] and point.get("label") == row.get("fiscal_label")
            and all((point.get(k) or None) == (anchor.get(k) or None) for k in ("page", "sheet", "cell", "quote")))


def _patch_raw(obj, manifest):
    before, after = obj["before"], copy.deepcopy(obj["before"])
    if before.get("ticker") != manifest["ticker"] or not isinstance(before.get("groups"), list):
        raise ValueError("Invalid public KPI bundle")
    old_items = {it["id"]: it for g in before["groups"] for it in g["items"]}
    items = {it["id"]: it for g in after["groups"] for it in g["items"]}
    if len(old_items) != sum(len(g["items"]) for g in before["groups"]):
        raise ValueError("Duplicate public KPI item")
    for change in manifest["row_corrections"]:
        old, new = change["before"], change["after"]
        kid, kind, end = old["kpi_id"], old["period_kind"], old["period_end"]
        old_item = old_items.get(kid)
        point = ((old_item or {}).get("values", {}).get(kind) or {}).get(end)
        if point is None or not _public_point_matches(point, old_item, old):
            raise ValueError(f"Exact public before point missing or different: {kid} {kind} {end}")
        if change.get("public_before") is not None and change["public_before"] != point:
            raise ValueError("Reviewed public before point differs")
        values = items[kid]["values"]
        if (kind, end) != (new["period_kind"], new["period_end"]) and new["period_end"] in values.get(new["period_kind"], {}):
            raise ValueError("Public destination collision")
        updated = copy.deepcopy(point)
        updated["v"] = float(_number(new["value"]))
        updated["label"] = new.get("fiscal_label")
        if old.get("anchor") != new.get("anchor"):
            updated["quote"] = (new.get("anchor") or {}).get("quote")
        if change.get("public_printed") is not None:
            updated["printed"] = change["public_printed"]
        if change.get("public_after") is not None:
            proposed = copy.deepcopy(change["public_after"])
            # A separately reviewed printed representation may change with its quote.
            if "printed" in proposed:
                updated["printed"] = proposed["printed"]
            if updated != proposed:
                raise ValueError("Public after changes unrelated point metadata")
        del values[kind][end]
        values.setdefault(new["period_kind"], {})[new["period_end"]] = updated
    for change in manifest.get("definition_corrections") or []:
        kid = change["before"]["kpi_id"]
        if old_items[kid].get("unit") != change["before"]["unit"]:
            raise ValueError("Public definition unit differs")
        items[kid]["unit"] = change["after"]["unit"]
    after["generated"] = manifest["prepared_at"]
    reviewed = manifest.get("public_bundle")
    if reviewed:
        old_bytes = Path(reviewed["local_before"]).read_bytes()
        new_bytes = Path(reviewed["local_proposed"]).read_bytes()
        if (reviewed["path"] != obj["path"] or digest(old_bytes) != reviewed["before_sha256"]
                or digest(new_bytes) != reviewed["proposed_sha256"]
                or json.loads(old_bytes) != before or json.loads(new_bytes) != after):
            raise ValueError("Reviewed public bundle does not match the exact scoped patch")
    obj.update(after=after, after_sha256=digest(encoded(after)))
    return obj


def _contains_metric(value, ids):
    if isinstance(value, dict):
        return value.get("kpi_id") in ids or any(_contains_metric(v, ids) for v in value.values())
    return isinstance(value, list) and any(_contains_metric(v, ids) for v in value)


def _standard_objects(sb, manifest, ids):
    path = f"kpistd/t/{manifest['ticker']}.json"
    company = _object(sb, path)
    checks = [{"path": path, "sha256": company["before_sha256"] if company else None}]
    if manifest.get("standardized_objects"):
        raise ValueError("Reviewed standardized edits require a separate numeric-series repair")
    changed_units = {c["before"]["kpi_id"] for c in manifest.get("definition_corrections", [])
                     if c["before"]["unit"] != c["after"]["unit"]}
    if company and changed_units and _contains_metric(company["before"], changed_units):
        raise ValueError("Mapped standardized unit change requires a separate mapping repair")
    # Segment metadata is not a numeric series. Its unchanged contents remain
    # checksum guarded; this narrow utility never rebuilds canonical values.
    if company and _contains_metric(company["before"].get("series", {}), ids):
        raise ValueError("Affected standardized numeric series require a separate reviewed repair")
    return [], checks


def plan_repair(sb, manifest):
    """Read-only remote planning. Local originals are required, never fetched."""
    manifest = copy.deepcopy(manifest)
    ticker, ids = _validate_manifest(manifest)
    sources = _verify_sources(sb, manifest)
    before = _read_db(sb, ticker, ids)
    if not _rows_equal(before["kpi_definitions"], manifest["expected_definitions"], True):
        raise ValueError("Live database definitions differ from expected before")
    rows = {_key(r): r for r in before["kpi_values"]}
    if len(rows) != len(before["kpi_values"]):
        raise ValueError("Duplicate live database key")
    for change in manifest["row_corrections"]:
        old, new = change["before"], change["after"]
        if _key(old) not in rows or _canon(rows[_key(old)]) != _canon(old):
            raise ValueError("Live database row differs from expected before")
        if _key(new) != _key(old) and _key(new) in rows:
            raise ValueError("Database destination collision")
    complete = {s["kpi_id"]: s for s in manifest.get("expected_metric_row_sets") or []}
    for change in manifest.get("definition_corrections") or []:
        kid = change["before"]["kpi_id"]
        live_def = next(d for d in before["kpi_definitions"] if d["kpi_id"] == kid)
        if _canon(live_def) != _canon(change["before"]):
            raise ValueError("Definition correction before state differs")
        if change["before"]["unit"] != change["after"]["unit"]:
            scope = complete.get(kid)
            live = [r for r in before["kpi_values"] if r["kpi_id"] == kid]
            proved = [c for c in manifest["row_corrections"] if c["before"]["kpi_id"] == kid]
            if (not scope or scope.get("expected_count") != len(live)
                    or not _rows_equal(scope.get("rows") or [], live)
                    or {_key(c["before"]) for c in proved} != {_key(r) for r in live}
                    or any(c["after"]["unit"] != change["after"]["unit"] for c in proved)):
                raise ValueError("Definition unit change lacks complete historical coverage/count")
    after = copy.deepcopy(before)
    replacements = {_key(c["before"]): c["after"] for c in manifest["row_corrections"]}
    after["kpi_values"] = [copy.deepcopy(replacements.get(_key(r), r)) for r in before["kpi_values"]]
    defs = {c["before"]["kpi_id"]: c["after"] for c in manifest.get("definition_corrections") or []}
    after["kpi_definitions"] = [copy.deepcopy(defs.get(d["kpi_id"], d)) for d in before["kpi_definitions"]]
    units = {d["kpi_id"]: d["unit"] for d in after["kpi_definitions"]}
    if any(r["unit"] != units[r["kpi_id"]] for r in after["kpi_values"]):
        raise ValueError("Resulting row units do not match their complete metric definitions")
    raw = _object(sb, f"kpis/{ticker}.json")
    if raw is None:
        raise ValueError("Existing public KPI bundle required")
    objects = [_patch_raw(raw, manifest)]
    standard, checks = _standard_objects(sb, manifest, ids)
    objects += standard
    stage = {"schema_version": 1, "ticker": ticker, "metric_ids": sorted(ids), "manifest": manifest,
             "database_before": before, "database_after": after, "verified_sources": sources,
             "public_objects": objects, "publication_checks": checks}
    stage["stage_sha256"] = digest(encoded(stage))
    return stage


def _seal(stage):
    value = {k: v for k, v in stage.items() if k != "stage_sha256"}
    if digest(encoded(value)) != stage.get("stage_sha256"):
        raise ValueError("Evidence stage seal mismatch")
    return stage["stage_sha256"]


def archive_path(stage):
    return f"repairs/kpi-evidence/{stage['ticker']}/{_seal(stage)}/stage.json"


def _lease(sb, owner):
    rows = sb.select("etl_leases", f"select=*&lock_name=eq.{LEASE}")
    if (len(rows) != 1 or rows[0].get("owner") != owner or
            dt.datetime.fromisoformat(rows[0]["expires_at"].replace("Z", "+00:00")) <= dt.datetime.now(dt.timezone.utc)):
        raise ValueError("Existing financial-aggregates lease is not held by this owner")


class EvidenceSupabase(Supabase):
    def put_immutable_object(self, bucket, path, body):
        existing = self.get_object(bucket, path)
        if existing is not None:
            if existing != body:
                raise ValueError("Immutable evidence archive collision")
            return
        request(f"{self.url}/storage/v1/object/{bucket}/{quote(path)}", method="POST", data=body,
                headers=self._h({"Content-Type": "application/json", "x-upsert": "false", "cache-control": "no-store"}),
                timeout=30, retries=1)


def archive_stage(sb, stage, lease_owner):
    _lease(sb, lease_owner)
    body, path = encoded(stage), archive_path(stage)
    sb.put_immutable_object("raw", path, body)
    if sb.get_object("raw", path) != body:
        raise ValueError("Evidence archive readback failed")
    return {"path": path, "sha256": digest(body)}


def _sql_literal(value):
    return "'" + value.replace("'", "''") + "'"


def render_guarded_sql(stage, lease_owner, rollback=False):
    """Emit, never execute, a transaction with complete before/after comparisons."""
    sha = _seal(stage)
    if not isinstance(lease_owner, str) or not lease_owner or len(lease_owner) > 160:
        raise ValueError("A specific existing lease owner is required")
    old, new = (stage["database_after"], stage["database_before"]) if rollback else (stage["database_before"], stage["database_after"])
    changes = [{"before": c["after"], "after": c["before"]} if rollback else c
               for c in stage["manifest"]["row_corrections"]]
    defchanges = [{"before": c["after"], "after": c["before"]} if rollback else c
                  for c in stage["manifest"].get("definition_corrections") or []]
    payload = {"ticker": stage["ticker"], "ids": stage["metric_ids"], "before": old, "after": new,
               "changes": changes, "definition_changes": defchanges, "sources": stage["verified_sources"],
               "lease_owner": lease_owner, "archive_path": archive_path(stage), "expected_count": len(changes)}
    literal = _sql_literal(encoded(payload).decode())
    tag = "$repair_" + digest(literal.encode())[:24] + "$"
    if tag in literal:
        raise ValueError("SQL delimiter collision")
    checks = []
    for state in ("before", "after"):
        body = []
        for table in ("kpi_definitions", "kpi_values"):
            keys = ("ticker", "kpi_id") if table == "kpi_definitions" else KEY
            match = " AND ".join(f"t.{k}=e.{k}" for k in keys)
            body.append(f"""
  SELECT count(*) INTO actual_count FROM public.{table} t
   WHERE t.ticker=p->>'ticker' AND t.kpi_id IN (SELECT jsonb_array_elements_text(p->'ids'));
  IF actual_count <> jsonb_array_length(p->'{state}'->'{table}') THEN
    RAISE EXCEPTION '{state} {table} count differs';
  END IF;
  FOR r IN SELECT * FROM jsonb_array_elements(p->'{state}'->'{table}') LOOP
    IF NOT EXISTS (SELECT 1 FROM public.{table} t
       CROSS JOIN jsonb_populate_record(NULL::public.{table}, r) e
       WHERE {match} AND to_jsonb(t) IS NOT DISTINCT FROM to_jsonb(e)) THEN
      RAISE EXCEPTION '{state} {table} exact row differs';
    END IF;
  END LOOP;""")
        checks.append("\n".join(body))
    match_before = " AND ".join(f"t.{k}=b.{k}" for k in KEY)
    match_after = " AND ".join(f"t.{k}=a.{k}" for k in KEY)
    return f"""BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '45s';
SET LOCAL standard_conforming_strings = on;
DO {tag}
DECLARE
  p jsonb := {literal}::jsonb;
  r jsonb; c jsonb; actual_count bigint; changed bigint;
  b public.kpi_values%ROWTYPE; a public.kpi_values%ROWTYPE;
  bd public.kpi_definitions%ROWTYPE; ad public.kpi_definitions%ROWTYPE;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.etl_leases WHERE lock_name='financial-aggregates'
      AND owner=p->>'lease_owner' AND expires_at>clock_timestamp()) THEN
    RAISE EXCEPTION 'financial-aggregates lease not held';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM storage.objects WHERE bucket_id='raw' AND name=p->>'archive_path') THEN
    RAISE EXCEPTION 'immutable evidence archive missing';
  END IF;
  -- Short table locks prevent uncooperative phantom inserts; row locks document
  -- the exact company/metric scope. Lock timeout aborts instead of waiting.
  LOCK TABLE public.kpi_definitions, public.kpi_values IN SHARE ROW EXCLUSIVE MODE;
  PERFORM 1 FROM public.kpi_definitions WHERE ticker=p->>'ticker'
    AND kpi_id IN (SELECT jsonb_array_elements_text(p->'ids')) FOR UPDATE;
  PERFORM 1 FROM public.kpi_values WHERE ticker=p->>'ticker'
    AND kpi_id IN (SELECT jsonb_array_elements_text(p->'ids')) FOR UPDATE;
  IF jsonb_array_length(p->'changes') <> (p->>'expected_count')::integer THEN
    RAISE EXCEPTION 'correction count differs';
  END IF;
  FOR r IN SELECT * FROM jsonb_array_elements(p->'sources') LOOP
    IF NOT EXISTS (SELECT 1 FROM public.ir_documents d WHERE d.id=r->>'id' AND d.ticker=r->>'ticker'
        AND d.sha256=r->>'sha256' AND d.url=r->>'url' AND d.bytes=(r->>'bytes')::bigint) THEN
      RAISE EXCEPTION 'source evidence metadata changed';
    END IF;
  END LOOP;
{checks[0]}
  FOR c IN SELECT * FROM jsonb_array_elements(p->'changes') LOOP
    SELECT * INTO b FROM jsonb_populate_record(NULL::public.kpi_values,c->'before');
    SELECT * INTO a FROM jsonb_populate_record(NULL::public.kpi_values,c->'after');
    IF (to_jsonb(b)-ARRAY['value','unit','period_kind','period_end','fiscal_label','anchor','updated_at'])
       IS DISTINCT FROM (to_jsonb(a)-ARRAY['value','unit','period_kind','period_end','fiscal_label','anchor','updated_at'])
       OR (coalesce(b.anchor,'{{}}'::jsonb)-'quote') IS DISTINCT FROM (coalesce(a.anchor,'{{}}'::jsonb)-'quote') THEN
      RAISE EXCEPTION 'row correction outside allowlist';
    END IF;
    IF ROW(b.period_kind,b.period_end) IS DISTINCT FROM ROW(a.period_kind,a.period_end)
       AND EXISTS (SELECT 1 FROM public.kpi_values t WHERE {match_after}) THEN
      RAISE EXCEPTION 'destination collision';
    END IF;
    UPDATE public.kpi_values t SET value=a.value, unit=a.unit, period_kind=a.period_kind,
      period_end=a.period_end, fiscal_label=a.fiscal_label, anchor=a.anchor, updated_at=a.updated_at
      WHERE {match_before};
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed<>1 THEN RAISE EXCEPTION 'expected one corrected row'; END IF;
  END LOOP;
  FOR c IN SELECT * FROM jsonb_array_elements(p->'definition_changes') LOOP
    SELECT * INTO bd FROM jsonb_populate_record(NULL::public.kpi_definitions,c->'before');
    SELECT * INTO ad FROM jsonb_populate_record(NULL::public.kpi_definitions,c->'after');
    IF (to_jsonb(bd)-ARRAY['unit','updated_at']) IS DISTINCT FROM (to_jsonb(ad)-ARRAY['unit','updated_at']) THEN
      RAISE EXCEPTION 'definition correction outside allowlist';
    END IF;
    UPDATE public.kpi_definitions t SET unit=ad.unit, updated_at=ad.updated_at
      WHERE t.ticker=bd.ticker AND t.kpi_id=bd.kpi_id;
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed<>1 THEN RAISE EXCEPTION 'expected one corrected definition'; END IF;
  END LOOP;
{checks[1]}
  IF NOT EXISTS (SELECT 1 FROM public.etl_leases WHERE lock_name='financial-aggregates'
      AND owner=p->>'lease_owner' AND expires_at>clock_timestamp()) THEN
    RAISE EXCEPTION 'lease expired during correction';
  END IF;
END;
{tag};
COMMIT;
"""


def finalize_repair(sb, stage, lease_owner, rollback=False):
    """Mirror only sealed reviewed objects after exact DB readback; resumable."""
    sha = _seal(stage)
    _lease(sb, lease_owner)
    if sb.get_object("raw", archive_path(stage)) != encoded(stage):
        raise ValueError("Immutable evidence archive missing or different")
    expected = stage["database_before"] if rollback else stage["database_after"]
    if not _same_db(_read_db(sb, stage["ticker"], stage["metric_ids"]), expected):
        raise ValueError("Committed database state differs; public bundles preserved")
    for check in stage["publication_checks"]:
        raw = sb.get_object("public-data", check["path"])
        if (digest(raw) if raw is not None else None) != check["sha256"]:
            raise ValueError("Standardized publication changed since review")
    pending = []
    for obj in stage["public_objects"]:
        before, after = base64.b64decode(obj["before_bytes"]), encoded(obj["after"])
        current = sb.get_object("public-data", obj["path"])
        if current not in (before, after):
            raise ValueError("Unreviewed public state; refusing to overwrite")
        desired = before if rollback else after
        if current != desired:
            pending.append((obj["path"], desired))
    for path, desired in pending:
        _lease(sb, lease_owner)
        sb.put_object("public-data", path, desired, content_type="application/json", cache="max-age=600")
        if sb.get_object("public-data", path) != desired:
            raise ValueError("Public readback failed; retain stage and resume or explicitly roll back")
    _lease(sb, lease_owner)
    if not _same_db(_read_db(sb, stage["ticker"], stage["metric_ids"]), expected):
        raise ValueError("Database changed during public finalization")
    receipt = {"status": "verified", "stage_sha256": sha, "ticker": stage["ticker"],
               "direction": "rollback" if rollback else "apply", "rows": stage["manifest"]["expected_count"],
               "objects": [{"path": o["path"], "sha256": o["before_sha256"] if rollback else o["after_sha256"]}
                           for o in stage["public_objects"]]}
    path = archive_path(stage).rsplit("/", 1)[0] + "/receipt-" + digest(encoded(receipt)) + ".json"
    sb.put_immutable_object("raw", path, encoded(receipt))
    if sb.get_object("raw", path) != encoded(receipt):
        raise ValueError("Final evidence receipt readback failed")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("stage", "sql", "rollback-sql", "archive", "finalize", "rollback-public"))
    parser.add_argument("file")
    parser.add_argument("--out")
    parser.add_argument("--lease-owner")
    args = parser.parse_args()
    value = json.loads(Path(args.file).read_text())
    if args.command in ("sql", "rollback-sql"):
        result = render_guarded_sql(value, args.lease_owner, rollback=args.command == "rollback-sql")
    else:
        load_env()
        sb = EvidenceSupabase()
        if args.command == "stage":
            result = encoded(plan_repair(sb, value)).decode()
        elif args.command == "archive":
            result = encoded(archive_stage(sb, value, args.lease_owner)).decode()
        else:
            result = encoded(finalize_repair(sb, value, args.lease_owner, rollback=args.command == "rollback-public")).decode()
    if args.out:
        target = Path(args.out)
        if target.exists() and target.read_text() != result:
            raise ValueError("Refusing to overwrite a different local stage or SQL artifact")
        target.write_text(result)
    else:
        print(result)


if __name__ == "__main__":
    main()
