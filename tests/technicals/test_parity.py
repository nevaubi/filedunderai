"""Checks that etl/technicals_calc.detect finds exactly the events TA.detect (public/pages/technicals-lib.js) finds.
Run from the repo root: python3 tests/technicals/test_parity.py"""
import json
import os
import random
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from etl.technicals_calc import detect  # noqa: E402

JS = r"""
const fs = require("fs"); const m = { exports: {} };
new Function("module", fs.readFileSync(process.argv[2], "utf8"))(m);
const s = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = s.map(x => m.exports.detect(x.o, x.h, x.l, x.c).events.map(e => [e.type, e.i, e.bars]));
fs.writeFileSync(process.argv[4], JSON.stringify(out));
"""


def series(n, seed, vol=0.018, jumpy=False):
    rnd = random.Random(seed)
    o, h, l, c = [], [], [], []
    p = 50.0
    for i in range(n):
        gap = rnd.gauss(0, 0.03) if jumpy and rnd.random() < 0.03 else rnd.gauss(0, 0.005)
        op = p * (1 + gap)
        drift = 0.002 * math.sin(i / 180)
        cl = op * (1 + rnd.gauss(drift, vol))
        hi = max(op, cl) * (1 + abs(rnd.gauss(0, 0.006)))
        lo = min(op, cl) * (1 - abs(rnd.gauss(0, 0.006)))
        o.append(round(op, 4)); h.append(round(hi, 4)); l.append(round(lo, 4)); c.append(round(cl, 4))
        p = cl
    return {"o": o, "h": h, "l": l, "c": c}


import math  # noqa: E402


def main():
    ref = json.load(open(os.path.join(os.path.dirname(__file__), "reference.json")))
    sets = [{k: ref[k] for k in "ohlc"}, series(3000, 1), series(4700, 2, 0.025, True), series(2500, 3, 0.012)]
    with tempfile.TemporaryDirectory() as td:
        inp, out, js = os.path.join(td, "in.json"), os.path.join(td, "out.json"), os.path.join(td, "p.cjs")
        json.dump(sets, open(inp, "w"))
        open(js, "w").write(JS)
        subprocess.run(["node", js, os.path.join(ROOT, "public/pages/technicals-lib.js"), inp, out], check=True)
        js_ev = json.load(open(out))
    total = 0
    for k, s in enumerate(sets):
        py_ev = [[t, i, b] for t, i, b in detect(s["o"], s["h"], s["l"], s["c"])[0]]
        if py_ev != js_ev[k]:
            a, b = {tuple([e[0], e[1]]) for e in py_ev}, {tuple([e[0], e[1]]) for e in js_ev[k]}
            raise SystemExit(f"series {k}: mismatch; python only {sorted(a - b)[:10]}, js only {sorted(b - a)[:10]}")
        total += len(py_ev)
        types = sorted({e[0] for e in py_ev})
        print(f"series {k}: {len(s['c'])} bars, {len(py_ev)} events identical ({len(types)} types)")
    print("parity ok,", total, "events")


if __name__ == "__main__":
    main()
