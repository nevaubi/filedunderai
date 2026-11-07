"""One scheduler tick (api/cron/agents.py, every 10 minutes): due monitors, due tasks, weekly insights, email.

Idempotency: a task is claimed under the user's lease before it runs (its `running` marker and next run time are
written first), so an overlapping tick skips it; a claim older than STALE_MIN is treated as a crashed run.
Monitors are cheap and their dedup keys make a repeated evaluation harmless.
"""
import datetime as dt
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from .. import llm
from ..common import request
from . import interests as I
from . import monitors as M
from . import schedule as S
from .data import Data
from .runner import Run, docx_from_markdown
from .store import Busy, Store, new_id

STALE_MIN = 25
TASK_SECONDS = {False: 300, True: 480}


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def task_next(task, tz, after):
    sched = task.get("schedule") or {}
    if sched.get("kind") == "once":
        return None
    try:
        return S.iso(S.next_after(sched.get("cron") or "0 7 * * 1", after, sched.get("tz") or tz))
    except S.CronError:
        return None


def scope_note(data, scope, state):
    kind = (scope or {}).get("kind", "watchlist")
    tickers, universe = M.resolve_scope(data, {"scope": scope}, state)
    if universe:
        return "Scope: all covered companies (about 1,844). Use screen to find the relevant ones.", "all covered companies"
    if not tickers:
        what = {"watchlist": "the user's watchlist", "portfolio": "the user's portfolio"}.get(kind, "the selected companies")
        return f"Scope: {what}, which is empty. Say so briefly and suggest what to add, then cover the market backdrop.", what
    label = {"watchlist": "your watchlist", "portfolio": "your portfolio", "sector": f"the {(scope or {}).get('sector')} sector"}.get(kind, "the selected companies")
    return f"Scope: {label} ({len(tickers)} companies): {', '.join(tickers[:60])}" + (" and more" if len(tickers) > 60 else ""), label


# ---------------------------------------------------------------- phase 1: claim
def claim(store, uid, now, force_insights=False):
    """Under the lease: fill missing next-run times, pick due monitors and tasks, mark tasks running."""
    due_m, due_t, do_insights = [], [], False
    with store.edit(uid) as s:
        tz = s.get("tz") or "America/Chicago"
        for t in s["tasks"]:
            if not t.get("enabled", True):
                continue
            run = t.get("running")
            if run and _parse(run.get("started_at")) and (now - _parse(run["started_at"])).total_seconds() > STALE_MIN * 60:
                t.setdefault("runs", []).insert(0, {"id": run.get("run_id"), "started_at": run.get("started_at"), "status": "failed", "error": "run did not finish (timed out)"})
                t["running"] = None
            if t.get("running"):
                continue
            if not t.get("next_at"):
                t["next_at"] = task_next(t, tz, now - dt.timedelta(minutes=1)) if t.get("schedule", {}).get("kind") != "once" else _iso(now)
            due = t.get("run_now") or (t.get("next_at") and _parse(t["next_at"]) <= now)
            if due:
                rid = new_id()
                t["running"] = {"run_id": rid, "started_at": _iso(now)}
                t["run_now"] = False
                t["next_at"] = task_next(t, tz, now)
                if t.get("schedule", {}).get("kind") == "once":
                    t["enabled"] = False
                due_t.append((dict(t), rid))
        for m in s["monitors"]:
            if not m.get("enabled", True):
                continue
            nxt = _parse(m.get("next_at"))
            if m.get("check_now") or nxt is None or nxt <= now:
                every = int(m.get("every_minutes") or M.DEFAULT_EVERY.get(m.get("type"), 60))
                m["next_at"] = _iso(now + dt.timedelta(minutes=max(10, every)))
                m["check_now"] = False
                due_m.append(json.loads(json.dumps(m)))
        cad = s["settings"].get("insights", "weekly")
        nxt = _parse(s["settings"].get("insights_next_at"))
        if force_insights or (cad != "off" and (nxt is None or nxt <= now) and (s["interests"]["events"] or s.get("watchlist"))):
            do_insights = True
            days = 1 if cad == "daily" else 7
            s["settings"]["insights_next_at"] = _iso(now + dt.timedelta(days=days))
        snapshot = json.loads(json.dumps({k: s[k] for k in ("uid", "email", "tz", "settings", "watchlist", "portfolio", "interests")}))
    return due_m, due_t, do_insights, snapshot


# ---------------------------------------------------------------- phase 2: work
ESCALATE_SYSTEM = """Write a short alert note for an investor from the monitor hits given (JSON). Return {"title": "...", "body": "3-5 sentences in plain language: what happened, the key numbers with dates, and why it may matter; mention base rates when given"}. Use only facts in the hits. No advice."""


def escalate(mon, hits, post=None):
    try:
        obj, _, _ = llm.chat_json(["deepseek-v4p1-flash", "kimi-k3p1-pro"], [
            {"role": "system", "content": ESCALATE_SYSTEM},
            {"role": "user", "content": json.dumps({"monitor": mon.get("name"), "hits": [{k: h.get(k) for k in ("title", "summary", "facts")} for h in hits[:12]]}, default=str)[:30000]},
        ], max_tokens=2500, temperature=0.1, timeout=90, post=post)
        return str(obj.get("title") or "")[:140], str(obj.get("body") or "")[:1500]
    except Exception as e:
        print("monitor write-up failed:", str(e)[:160])
        return None, None


def run_monitor(data, mon, snap, now, post=None):
    try:
        hits = M.evaluate(data, mon, snap, now)
    except Exception as e:
        return mon, None, f"{type(e).__name__}: {str(e)[:200]}"
    if not hits:
        return mon, None, None
    rank = {"high": 0, "notable": 1, "info": 2}
    hits.sort(key=lambda h: rank.get(h.get("severity"), 3))
    title, body = (escalate(mon, hits, post) if mon.get("escalate") else (None, None))
    n = len(hits)
    item = {
        "kind": "alert", "title": title or (hits[0]["title"] if n == 1 else f"{mon.get('name')}: {n} new"),
        "summary": body or "; ".join(h["title"] for h in hits[:4]) + (f"; and {n - 4} more" if n > 4 else ""),
        "severity": hits[0].get("severity", "info"), "tickers": sorted({t for h in hits for t in h.get("tickers") or []})[:20],
        "source": {"monitor_id": mon["id"], "monitor": mon.get("name"), "type": mon.get("type")},
        "hits": hits[:50], "evidence": [e for h in hits[:10] for e in (h.get("evidence") or [])[:2]],
        "report_md": body or "",
    }
    return mon, item, None


def run_task(sb, data, task, rid, snap, now, post=None, web_fetch=None):
    note, label = scope_note(data, task.get("scope"), snap)
    prompt = (task.get("prompt") or "").replace("{scope}", label)
    if task.get("notes"):
        prompt += f"\nTask notes from the user: {task['notes']}"
    r = Run(sb, data, post=post, web_fetch=web_fetch)
    deep = bool(task.get("deep"))
    out = r.run(prompt, now.date().isoformat(), note, task.get("deliverable", "report"), deep,
                budget_seconds=TASK_SECONDS[deep], budget_usd=float(task.get("budget_usd") or (1.0 if deep else 0.4)))
    return out


def deliver_task(store, uid, task, rid, out, now):
    item_id = new_id()
    files = []
    for f in out.get("files") or []:
        files.append(store.put_file(uid, item_id, f["name"], f["data"], f["content_type"]))
    if task.get("deliverable") == "memo" and out.get("report_md"):
        try:
            files.append(store.put_file(uid, item_id, (task.get("name") or "memo")[:40] + ".docx", docx_from_markdown(out["title"], out["report_md"]),
                                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
        except Exception as e:
            print("docx failed:", str(e)[:120])
    if out.get("report_md"):
        files.append(store.put_file(uid, item_id, (task.get("name") or "report")[:40] + ".md", out["report_md"].encode(), "text/markdown"))
    return {
        "id": item_id, "kind": "report", "title": out["title"], "summary": out["summary"], "report_md": out["report_md"], "views": out.get("views") or [],
        "files": files, "evidence": out.get("sources") or [], "tickers": out.get("tickers") or [], "severity": "info",
        "source": {"task_id": task["id"], "task": task.get("name"), "run_id": rid}, "steps": out.get("steps"), "model": out.get("model"),
        "seconds": out.get("seconds"), "cost_usd_est": out.get("cost_usd_est"), "created_at": _iso(now),
    }


# ---------------------------------------------------------------- email
def send_email(snap, cards, app_url=None):
    key, sender = os.environ.get("RESEND_API_KEY"), os.environ.get("AGENTS_FROM_EMAIL") or os.environ.get("MONITORING_FROM_EMAIL")
    to = snap.get("email")
    if not (key and sender and to and snap["settings"].get("email") and cards):
        return False
    app_url = app_url or os.environ.get("APP_PUBLIC_URL") or "https://filedunder.ai"
    lines = [f"- {c['title']}\n  {c.get('summary') or ''}"[:600] for c in cards[:12]]
    body = "Your Filed Under agents found:\n\n" + "\n\n".join(lines) + f"\n\nOpen your inbox: {app_url}/app/agents?tab=inbox\n\nTurn these emails off in Agents → Settings."
    try:
        request("https://api.resend.com/emails", method="POST", data={"from": sender, "to": [to], "subject": f"Filed Under: {cards[0]['title'][:80]}" + (f" (+{len(cards) - 1} more)" if len(cards) > 1 else ""), "text": body},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Idempotency-Key": f"agents/{snap['uid']}/{cards[0]['id']}"}, timeout=20, retries=1)
        return True
    except Exception as e:
        print("agent email failed:", str(e)[:160])
        return False


# ---------------------------------------------------------------- tick
def tick(sb, time_budget=700, now=None, only_uid=None, post=None, web_fetch=None, force_insights=False, log=print):
    t0 = time.time()
    now = now or _now()
    store = Store(sb)
    data = Data(sb)
    users = [only_uid] if only_uid else store.users()
    stats = {"users": len(users), "monitors": 0, "alerts": 0, "tasks": 0, "reports": 0, "insights": 0, "errors": [], "busy": 0, "emails": 0}
    work = []
    for uid in users:
        try:
            dm, dt_, di, snap = claim(store, uid, now, force_insights)
        except Busy:
            stats["busy"] += 1
            continue
        except Exception as e:
            stats["errors"].append(f"{uid[:8]} claim: {str(e)[:120]}")
            continue
        if dm or dt_ or di:
            work.append((uid, dm, dt_, di, snap))

    def do_user(w):
        uid, dm, dts, di, snap = w
        res = {"uid": uid, "monitors": [], "items": [], "task_runs": [], "insights": None, "errors": []}
        for m in dm:
            mon, item, err = run_monitor(data, m, snap, now, post)
            res["monitors"].append((mon, err))
            if item:
                res["items"].append(item)
        for task, rid in dts:
            if time.time() - t0 > time_budget - TASK_SECONDS[bool(task.get("deep"))] - 30:
                res["task_runs"].append((task, rid, None, "deferred: tick time budget"))
                continue
            try:
                out = run_task(sb, data, task, rid, snap, now, post, web_fetch)
                item = deliver_task(store, uid, task, rid, out, now)
                res["items"].append(item)
                res["task_runs"].append((task, rid, out, None))
            except Exception as e:
                res["task_runs"].append((task, rid, None, f"{type(e).__name__}: {str(e)[:200]}"))
        if di and time.time() - t0 < time_budget - 120:
            try:
                st = json.loads(json.dumps(snap))
                ins = I.insights(data, st, post=post, now=now)
                res["weights"] = st["interests"]["weights"]
                if ins and ins["insights"]:
                    md = "\n\n".join(f"### {x['title']}\n{x['body']}" + (f"\n\n_{x['why_you']}_" if x.get("why_you") else "") for x in ins["insights"])
                    res["insights"] = ins
                    res["items"].append({"kind": "insight", "title": f"Insights for you: {ins['insights'][0]['title']}"[:140],
                                         "summary": f"{len(ins['insights'])} findings picked from {ins['candidates']} candidate facts about the companies and topics you follow.",
                                         "report_md": md, "insights": ins["insights"], "suggested_monitors": ins["monitors"], "tickers": sorted({t for x in ins["insights"] for t in x["tickers"]}),
                                         "evidence": [e for x in ins["insights"] for e in x["evidence"]][:12], "source": {"insights": True}, "severity": "info"})
            except Exception as e:
                res["errors"].append(f"insights: {str(e)[:160]}")
        return res

    with ThreadPoolExecutor(3) as ex:
        results = list(ex.map(do_user, work))

    for res in results:
        uid = res["uid"]
        try:
            with store.edit(uid, wait=90) as s:
                cards = []
                for item in res["items"]:
                    cards.append(store.add_inbox(s, item))
                by_m = {m["id"]: m for m in s["monitors"]}
                for mon, err in res["monitors"]:
                    cur = by_m.get(mon["id"])
                    if not cur:
                        continue
                    for k in ("seen", "baseline", "last_checked", "last_hits"):
                        if k in mon:
                            cur[k] = mon[k]
                    cur["last_error"] = err
                by_t = {t["id"]: t for t in s["tasks"]}
                for task, rid, out, err in res["task_runs"]:
                    cur = by_t.get(task["id"])
                    if not cur:
                        continue
                    item = next((c for c in cards if (c.get("source") or {}).get("run_id") == rid), None)
                    rec = {"id": rid, "started_at": (cur.get("running") or {}).get("started_at"), "finished_at": _iso(_now()),
                           "status": "ok" if out else ("deferred" if err and err.startswith("deferred") else "failed"), "error": err,
                           "seconds": (out or {}).get("seconds"), "cost_usd_est": (out or {}).get("cost_usd_est"), "inbox_id": item["id"] if item else None,
                           "steps": len((out or {}).get("steps") or [])}
                    cur.setdefault("runs", []).insert(0, rec)
                    cur["running"] = None
                    if rec["status"] == "deferred":
                        cur["run_now"] = True
                    cur["last_run"] = rec
                if res.get("weights"):
                    s["interests"]["weights"] = res["weights"]
                if res.get("insights"):
                    s["suggestions"] = (res["insights"].get("monitors") or [])[:6]
                if send_email(s, cards):
                    stats["emails"] += 1
            stats["alerts"] += sum(1 for i in res["items"] if i["kind"] == "alert")
            stats["reports"] += sum(1 for i in res["items"] if i["kind"] == "report")
            stats["insights"] += sum(1 for i in res["items"] if i["kind"] == "insight")
            stats["monitors"] += len(res["monitors"])
            stats["tasks"] += len(res["task_runs"])
            stats["errors"] += [f"{uid[:8]} {e}" for e in res["errors"]] + [f"{uid[:8]} {t.get('name')}: {e}" for t, _, _, e in res["task_runs"] if e]
        except Exception as e:
            stats["errors"].append(f"{uid[:8]} write: {str(e)[:160]}")
    stats["seconds"] = round(time.time() - t0, 1)
    stats["errors"] = stats["errors"][:20]
    return stats
