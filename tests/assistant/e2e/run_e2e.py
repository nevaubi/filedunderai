"""Browser end-to-end scenarios for the assistant panel against the scripted mock API (no model or keys).

  npm ci && node scripts/build.mjs
  python3 tests/assistant/e2e/run_e2e.py [--port 8106] [--shots DIR]

Public data, fonts and the d3 CDN are fetched through Python (system proxy, verified TLS with the
configured CA bundle) and cached on disk, so Chrome needs no proxy or certificate changes.
Scenarios: dock + empty state, plan -> parallel tools -> ask_user -> cited answer, evidence and filing citations
opening the source drawer beside the panel, Deep self-review, Stop, float drag/resize persistence, handoff
prefill without submit, #research history (open, delete), mobile full screen.
"""
import argparse
import hashlib
import json
import os
import ssl
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).parent))
import mock_api  # noqa: E402

CACHE = Path(os.environ.get("FU_E2E_CACHE", Path(tempfile.gettempdir()) / "fu-e2e-cache"))
CA = next((p for p in [os.environ.get("SSL_CERT_FILE"), "/root/.ccr/ca-bundle.crt"] if p and Path(p).exists()), None)
CTX = ssl.create_default_context(cafile=CA) if CA else ssl.create_default_context()
CHROME = os.environ.get("CHROME", "/usr/bin/google-chrome")


def external(route):
    url = route.request.url
    key = CACHE / hashlib.sha256(url.encode()).hexdigest()
    try:
        if key.exists():
            meta = json.loads((key.with_suffix(".json")).read_text())
            return route.fulfill(status=meta["status"], headers={"content-type": meta["type"], "access-control-allow-origin": "*"}, body=key.read_bytes())
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 FiledUnder-e2e"})
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            body, status, ctype = r.read(), r.status, r.headers.get("content-type", "application/octet-stream")
        CACHE.mkdir(parents=True, exist_ok=True)
        key.write_bytes(body)
        key.with_suffix(".json").write_text(json.dumps({"status": status, "type": ctype}))
        return route.fulfill(status=status, headers={"content-type": ctype, "access-control-allow-origin": "*"}, body=body)
    except urllib.error.HTTPError as e:
        return route.fulfill(status=e.code, body=b"")
    except Exception:
        return route.abort()


def wire(page, base):
    page.route(lambda u: not u.startswith(base), external)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


def ask(page, text):
    ta = page.locator("#fu-asst textarea")
    ta.fill(text)
    ta.press("Enter")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8106)
    ap.add_argument("--shots", default=None)
    a = ap.parse_args()
    shots = Path(a.shots) if a.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{a.port}"
    httpd = mock_api.serve(a.port)
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))

    def shot(page, name):
        if shots:
            page.screenshot(path=str(shots / f"{name}.jpg"), type="jpeg", quality=80)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors = wire(page, base)
        page.goto(base + "/#company-nvda")
        page.wait_for_selector(".co-tabs, .company-page", timeout=60000)
        page.wait_for_timeout(800)
        page.keyboard.press("Control+j")
        page.wait_for_selector("#fu-asst.open")
        check("shortcut opens docked panel", page.evaluate("document.documentElement.classList.contains('asst-docked')"))
        pad = page.evaluate("getComputedStyle(document.querySelector('.app')).paddingRight")
        check("page content reflows beside docked panel", pad not in ("0px", ""), pad)
        sugg = page.locator(".asst-sugg button").all_inner_texts()
        check("empty state offers page-aware suggestions", any("NVDA" in s for s in sugg), "; ".join(sugg))
        shot(page, "01-docked-empty-company")

        # Scenario 1: plan -> parallel tools -> ask_user -> cited answer
        ask(page, "How is NVDA's revenue trending, are insiders selling, and what does the 10-Q say about Data Center?")
        page.wait_for_selector(".asst-plan", timeout=30000)
        page.wait_for_selector(".asst-ask", timeout=60000)
        check("plan card renders", page.locator(".asst-plan summary").first.inner_text().startswith("Research plan") or "plan" in page.locator(".asst-plan summary").first.inner_text().lower())
        names = page.locator(".asst-step .asst-slab").all_inner_texts()
        check("parallel tool steps shown", sum(1 for n in names if n in ("Pulled financials", "Pulled insider trades", "Researched the web")) == 3, ", ".join(names))
        check("reasoning summary shown", page.locator(".asst-step.think").count() >= 1)
        check("needs-input card with options", page.locator(".asst-opts button").count() == 2)
        shot(page, "02-needs-input")
        page.locator(".asst-opts button", has_text="Last 8 quarters").click()
        page.wait_for_selector(".asst-mfoot", timeout=90000)
        page.wait_for_timeout(400)
        md = page.locator(".asst-m.a").last
        check("answer cites evidence", md.locator(".cite[data-evidence-id]").count() >= 1)
        check("answer cites filing passage", md.locator(".asst-md .asst-fcite[data-q]").count() == 1)
        check("chart drawn", md.locator(".asst-chart svg rect").count() >= 2)
        check("table rendered", md.locator(".asst-md table").count() == 1)
        foot = md.locator(".asst-mfoot").inner_text()
        check("AI label and verified check", "ai-generated" in foot.lower() and "1 cited figure checked" in foot, foot)
        check("no contradiction banner", md.locator(".asst-alert").count() == 0)
        check("deduplicated source list", md.locator(".asst-all li").count() >= 1)
        check("user answer reached the model", any("User answered: Last 8 quarters" in json.dumps(e["body"]) for e in mock_api.LOG if e["path"] == "/api/ai"))
        shot(page, "03-answer")

        md.locator(".asst-md .cite[data-evidence-id]").first.click()
        page.wait_for_selector("#drawer.open", timeout=20000)
        page.wait_for_timeout(600)
        dr = page.locator("#drawer").bounding_box()
        pn = page.locator("#fu-asst").bounding_box()
        check("source drawer opens beside docked panel", dr["x"] + dr["width"] <= pn["x"] + 2, f"drawer {dr} panel {pn}")
        shot(page, "04-evidence-drawer")
        page.keyboard.press("Escape")
        md.locator(".asst-md .asst-fcite[data-q]").click()
        page.wait_for_selector("#drawer.open iframe", timeout=30000)
        frame = page.frame_locator("#drawer iframe")
        try:
            frame.locator(".fu-hit").first.wait_for(timeout=30000)
            hit = True
        except Exception:
            hit = False
        check("filing quote highlighted in drawer", hit, page.locator("#drawer .viewer-bar").inner_text())
        page.wait_for_timeout(900)
        shot(page, "05-filing-quote")
        page.keyboard.press("Escape")

        # Scenario 2: Deep mode with a self-review pass
        page.locator('#fu-asst [data-act="new"]').click()
        page.locator('#fu-asst [data-depth="deep"]').click()
        ask(page, "Is NVDA's momentum strong versus semiconductors? Check it twice.")
        page.wait_for_selector(".asst-mfoot", timeout=90000)
        page.locator(".asst-stoggle").last.click()
        text = page.locator(".asst-m.a").last.inner_text()
        sent_review = any("[Self-review" in json.dumps(e["body"]["messages"]) for e in mock_api.LOG if e["path"] == "/api/ai")
        check("deep mode ran a self-review and revised", "Revised answer" in text and "Reviewed the draft" in text and sent_review, text[:300])
        reqs = [e["body"] for e in mock_api.LOG if e["path"] == "/api/ai"]
        check("deep mode uses the deep profile", reqs[-1]["profile"] == "agent_deep")
        shot(page, "06-deep-review")

        # Scenario 3: Stop
        page.locator('#fu-asst [data-act="new"]').click()
        page.locator('#fu-asst [data-depth="fast"]').click()
        ask(page, "Run a long backtest (slow)")
        page.wait_for_selector(".asst-send.stop")
        page.wait_for_timeout(700)
        page.locator(".asst-send.stop").click()
        page.wait_for_selector(".asst-send:not(.stop)", timeout=15000)
        check("stop ends the turn", "Stopped" in page.locator(".asst-m.a").last.inner_text())

        # Handoff prefill never submits
        before = page.locator(".asst-m.u").count()
        page.evaluate("window.dispatchEvent(new CustomEvent('fu:research-request', { detail: 'Prefilled from a page' }))")
        page.wait_for_timeout(200)
        check("handoff prefills without submitting", page.locator("#fu-asst textarea").input_value() == "Prefilled from a page" and page.locator(".asst-m.u").count() == before)

        # Float, drag, resize, persistence
        page.locator('#fu-asst [data-act="dock"]').click()
        check("pop out to floating window", page.locator("#fu-asst.float").count() == 1 and not page.evaluate("document.documentElement.classList.contains('asst-docked')"))
        b0 = page.locator("#fu-asst").bounding_box()
        page.mouse.move(b0["x"] + 120, b0["y"] + 20)
        page.mouse.down()
        page.mouse.move(b0["x"] - 300, b0["y"] + 80, steps=8)
        page.mouse.up()
        rz = page.locator("#fu-asst .asst-resize").bounding_box()
        page.mouse.move(rz["x"] + 8, rz["y"] + 8)
        page.mouse.down()
        page.mouse.move(rz["x"] + 60, rz["y"] - 40, steps=6)
        page.mouse.up()
        b1 = page.locator("#fu-asst").bounding_box()
        check("drag moves the window", b1["x"] < b0["x"] - 250, f"{b0} -> {b1}")
        check("resize changes the window", abs(b1["width"] - b0["width"]) > 30, f"{b0} -> {b1}")
        shot(page, "07-floating")
        page.reload()
        page.wait_for_selector("#fu-asst.float.open", timeout=30000)
        b2 = page.locator("#fu-asst").bounding_box()
        check("float state and box persist across reload", abs(b2["x"] - b1["x"]) < 3 and abs(b2["width"] - b1["width"]) < 3, f"{b1} -> {b2}")
        page.locator('#fu-asst [data-act="dock"]').click()

        # Every built-in executor against the real public data bundles (web tools hit the mock routes)
        smoke = page.evaluate("""async () => {
          const tools = Object.fromEntries(FU_ASSISTANT_TOOLKIT.toolsFor('company').map(t => [t.name, t]));
          const calls = [
            ['get_coverage', { query: 'nvidia' }], ['screen_companies', { sort_by: 'fcf_yield', limit: 3, index: 'sp500' }],
            ['get_financials', { ticker: 'AAPL', statement: 'cashflow', frequency: 'annual', periods: 3 }], ['get_financials', { ticker: 'NVDA', statement: 'ratios', periods: 4 }],
            ['get_segments', { ticker: 'NVDA', frequency: 'annual', periods: 2 }], ['list_filings', { ticker: 'NVDA', forms: ['10-Q'], limit: 3 }],
            ['read_filing', { ticker: 'NVDA', accession: '0001045810-26-000075', query: 'gross margin' }], ['search_filings', { query: '"data center"', ticker: 'NVDA' }],
            ['get_price_history', { symbol: 'NVDA', range: '6mo' }], ['get_technicals', { symbol: 'SPY' }], ['get_quotes', { symbols: ['NVDA', 'QQQ'] }],
            ['get_insider_trades', { ticker: 'NVDA', limit: 3 }], ['get_macro', { name: 'CPI', n: 3 }], ['get_macro', { name: 'CURVE' }], ['get_news', { ticker: 'NVDA' }], ['get_news', {}],
            ['get_earnings_reactions', { ticker: 'NVDA' }], ['web_research', { query: 'x' }], ['read_webpage', { url: 'https://example.com' }], ['web_map', { url: 'https://example.com' }],
            ['web_extract', { url: 'https://example.com', prompt: 'x' }], ['get_page_context', {}],
            ['run_code', { code: 'console.log(data.universe.length); return { revenueMean: stats.mean(data.companies.NVDA.annual.rows.Revenue.filter(Number.isFinite).slice(-3)) > 0, spyBars: data.prices.SPY.length > 100, network: typeof fetch }', tickers: ['NVDA'], price_symbols: ['SPY'] }],
            ['run_code', { code: 'return [typeof fetch, typeof WorkerGlobalScope.prototype.fetch, typeof Object.getPrototypeOf(self).fetch, typeof XMLHttpRequest, typeof WebSocket, typeof indexedDB, typeof importScripts].join(",")' }],
            ['run_code', { code: 'while (true) {}' }],
          ];
          const out = [];
          for (const [name, args] of calls) {
            try { const r = await tools[name].run(args, {}); const text = typeof r === 'string' ? r : r.output; out.push([name, true, (r.summary || '') + (text.includes('[ev_') ? ' [evidence ids]' : '') + ' | ' + text.slice(0, 160).replace(/\\n/g, ' ')]); }
            catch (e) { out.push([name, false, String(e.message || e)]); }
          }
          return out;
        }""")
        for name, ok, detail in smoke:
            print(f"   tool {name}: {'ok' if ok else 'ERROR'} {detail[:150]}")
        check("all built-in tools execute on real data", all(ok for n, ok, d in smoke[:-1]), "; ".join(d for _, ok, d in smoke[:-1] if not ok))
        check("segments tool returns evidence-linked values", all("[evidence ids]" in d for n, ok, d in smoke if n in ("get_segments", "get_financials")))
        code = [d for n, ok, d in smoke if n == "run_code"]
        check("run_code sandbox has data and no network", '"revenueMean": true' in code[0] and '"spyBars": true' in code[0] and '"network": "undefined"' in code[0], code[0])
        check("run_code sandbox strips network APIs from the prototype chain", ",".join(["undefined"] * 7) in code[1], code[1])
        check("run_code sandbox enforces its time limit", "Timed out" in code[2], code[2])

        # History view
        page.goto(base + "/#research")
        page.wait_for_selector(".asst-hrow", timeout=30000)
        n = page.locator(".asst-hrow").count()
        check("history lists conversations", n >= 3, str(n))
        shot(page, "08-history")
        page.locator(".asst-hrow .asst-hopen").nth(n - 1).click()
        page.wait_for_timeout(300)
        check("history reopens a conversation in the panel", "revenue" in page.locator("#fu-asst .asst-m.u").first.inner_text().lower())
        page.locator(".asst-hrow .asst-hdel").first.click()
        page.locator(".asst-hrow .asst-hdel").first.click()
        page.wait_for_timeout(200)
        check("history deletes a conversation", page.locator(".asst-hrow").count() == n - 1)
        check("no page errors (desktop)", not errors, "; ".join(errors[:3]))

        # Mobile
        m = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        mp = m.new_page()
        merr = wire(mp, base)
        mp.goto(base + "/#company-nvda")
        mp.wait_for_selector(".co-tabs, .company-page", timeout=60000)
        mp.locator("#asst-toggle").click()
        mp.wait_for_selector("#fu-asst.open")
        mp.wait_for_timeout(400)
        bb = mp.locator("#fu-asst").bounding_box()
        check("mobile panel is full screen", bb["width"] >= 389 and bb["height"] >= 800, str(bb))
        check("no horizontal scroll on mobile", mp.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
        shot(mp, "09-mobile-empty")
        ask(mp, "How is NVDA's revenue trending?")
        mp.wait_for_selector(".asst-ask", timeout=60000)
        mp.locator(".asst-opts button").first.click()
        mp.wait_for_selector(".asst-mfoot", timeout=90000)
        shot(mp, "10-mobile-answer")
        mp.locator(".asst-md .cite[data-evidence-id]").first.click()
        mp.wait_for_selector("#drawer.open", timeout=20000)
        mp.wait_for_timeout(500)
        top = mp.evaluate("(() => { const r = document.querySelector('#drawer').getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + 80); return !!e && !!e.closest('#drawer'); })()")
        check("mobile source drawer sits above the panel", top)
        shot(mp, "11-mobile-drawer")
        check("no page errors (mobile)", not merr, "; ".join(merr[:3]))
        browser.close()
    httpd.shutdown()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
