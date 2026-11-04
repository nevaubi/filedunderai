"""Python mirror of the signal definitions in public/pages/technicals-lib.js, used by the market-wide job.
Kept operation-for-operation identical so both sides find the same events; tests/technicals/test_parity.py checks it."""
import math
from collections import deque

COOLDOWN, BREAK_GAP, DIV_K, DIV_MIN, DIV_MAX = 10, 20, 5, 10, 60


def sma(a, n):
    o = [None] * len(a)
    s, k = 0.0, 0
    for i, x in enumerate(a):
        if x is None:
            s, k = 0.0, 0
            continue
        s += x
        k += 1
        if k > n:
            s -= a[i - n]
            k = n
        if k == n:
            o[i] = s / n
    return o


def _seeded(a, n, step):
    o = [None] * len(a)
    start = 0
    while start < len(a) and a[start] is None:
        start += 1
    if len(a) - start < n:
        return o
    e = 0.0
    for i in range(start, start + n):
        e += a[i]
    e /= n
    o[start + n - 1] = e
    for i in range(start + n, len(a)):
        e = step(e, a[i])
        o[i] = e
    return o


def ema(a, n):
    k = 2 / (n + 1)
    return _seeded(a, n, lambda e, x: e if x is None else x * k + e * (1 - k))


def rma(a, n):
    return _seeded(a, n, lambda e, x: (e * (n - 1) + (e if x is None else x)) / n)


def stdev(a, n):
    o = [None] * len(a)
    for i in range(n - 1, len(a)):
        w = a[i - n + 1:i + 1]
        if any(x is None for x in w):
            continue
        s = 0.0
        for x in w:
            s += x
        m = s / n
        v = 0.0
        for x in w:
            v += (x - m) ** 2
        o[i] = math.sqrt(v / n)
    return o


def true_range(h, l, c):
    return [None] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]


def atr(h, l, c, n=14):
    return rma(true_range(h, l, c), n)


def rsi(c, n=14):
    o = [None] * len(c)
    if len(c) <= n:
        return o
    g = ls = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        if d > 0:
            g += d
        else:
            ls -= d
    g /= n
    ls /= n

    def val(g, ls):
        return (50 if g == 0 else 100) if ls == 0 else 100 - 100 / (1 + g / ls)
    o[n] = val(g, ls)
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        ls = (ls * (n - 1) + max(-d, 0)) / n
        o[i] = val(g, ls)
    return o


def macd_hist(c, fast=12, slow=26, sig=9):
    ef, es = ema(c, fast), ema(c, slow)
    line = [None if ef[i] is None or es[i] is None else ef[i] - es[i] for i in range(len(c))]
    s = ema(line, sig)
    return [None if line[i] is None or s[i] is None else line[i] - s[i] for i in range(len(c))]


def supertrend_dir(h, l, c, n=10, mult=3):
    a = atr(h, l, c, n)
    d_out = [None] * len(c)
    fu = fl = None
    d = 1
    for i in range(len(c)):
        if a[i] is None:
            continue
        hl2 = (h[i] + l[i]) / 2
        bu, bl = hl2 + mult * a[i], hl2 - mult * a[i]
        if fu is None:
            fu, fl = bu, bl
            d = 1 if c[i] >= hl2 else -1
        else:
            pu, pl = fu, fl
            fu = bu if (bu < pu or c[i - 1] > pu) else pu
            fl = bl if (bl > pl or c[i - 1] < pl) else pl
            if d == 1 and c[i] < pl:
                d = -1
            elif d == -1 and c[i] > pu:
                d = 1
        d_out[i] = d
    return d_out


def squeeze_flags(h, l, c):
    mid, sd = sma(c, 20), stdev(c, 20)
    km, ka = ema(c, 20), atr(h, l, c, 20)
    out = []
    for i in range(len(c)):
        if mid[i] is None or km[i] is None or ka[i] is None:
            out.append(False)
            continue
        bu, bl = mid[i] + 2 * sd[i], mid[i] - 2 * sd[i]
        ku, kl = km[i] + 1.5 * ka[i], km[i] - 1.5 * ka[i]
        out.append(bu < ku and bl > kl)
    return out, mid


def swings(h, l, k=5):
    highs, lows = [], []
    for i in range(k, len(h) - k):
        hi = lo = True
        for j in range(i - k, i + k + 1):
            if j == i:
                continue
            if h[j] >= h[i]:
                hi = False
            if l[j] <= l[i]:
                lo = False
            if not hi and not lo:
                break
        if hi:
            highs.append(i)
        if lo:
            lows.append(i)
    return highs, lows


def detect(o, h, l, c):
    """Same events as TA.detect in technicals-lib.js: list of (type, i, bars)."""
    L = len(c)
    ev = []
    s50, s200, r, mh, st = sma(c, 50), sma(c, 200), rsi(c, 14), macd_hist(c), supertrend_dir(h, l, c, 10, 3)
    squeeze, bbmid = squeeze_flags(h, l, c)
    last_at = {}

    def push(t, i, bars):
        if t in last_at and i - last_at[t] < COOLDOWN:
            return
        last_at[t] = i
        ev.append((t, i, bars))
    last_hi = last_lo = -10 ** 9
    sq_run = 1 if (L and squeeze[0]) else 0
    dqh, dql = deque(), deque()  # windows [i-252, i-1]: front holds the earliest index of the max / min
    for i in range(1, L):
        j = i - 1
        while dqh and h[dqh[-1]] < h[j]:
            dqh.pop()
        dqh.append(j)
        while dql and l[dql[-1]] > l[j]:
            dql.pop()
        dql.append(j)
        while dqh[0] < i - 252:
            dqh.popleft()
        while dql[0] < i - 252:
            dql.popleft()
        if s50[i - 1] is not None and s200[i - 1] is not None:
            if s50[i - 1] <= s200[i - 1] and s50[i] > s200[i]:
                push("golden_cross", i, [i])
            if s50[i - 1] >= s200[i - 1] and s50[i] < s200[i]:
                push("death_cross", i, [i])
        if i >= 252:
            ph, pl = h[dqh[0]], l[dql[0]]
            if c[i] > ph:
                if i - last_hi > BREAK_GAP:
                    ev.append(("high_52w", i, [dqh[0], i]))
                last_hi = i
            if c[i] < pl:
                if i - last_lo > BREAK_GAP:
                    ev.append(("low_52w", i, [dql[0], i]))
                last_lo = i
        if o[i] > h[i - 1] and o[i] >= c[i - 1] * 1.02:
            push("gap_up", i, [i - 1, i])
        if o[i] < l[i - 1] and o[i] <= c[i - 1] * 0.98:
            push("gap_down", i, [i - 1, i])
        if r[i - 1] is not None:
            if r[i - 1] >= 30 and r[i] < 30:
                push("rsi_oversold", i, [i])
            if r[i - 1] <= 70 and r[i] > 70:
                push("rsi_overbought", i, [i])
        if mh[i - 1] is not None:
            if mh[i - 1] <= 0 and mh[i] > 0:
                push("macd_bull", i, [i])
            if mh[i - 1] >= 0 and mh[i] < 0:
                push("macd_bear", i, [i])
        if st[i - 1] is not None and st[i] != st[i - 1]:
            push("st_up" if st[i] == 1 else "st_down", i, [i])
        if squeeze[i]:
            sq_run += 1
        else:
            if squeeze[i - 1] and sq_run >= 6 and bbmid[i] is not None:
                push("squeeze_bull" if c[i] > bbmid[i] else "squeeze_bear", i, [i - sq_run, i - 1, i])
            sq_run = 0
    highs, lows = swings(h, l, DIV_K)
    for x in range(1, len(lows)):
        a, b = lows[x - 1], lows[x]
        i = b + DIV_K
        if i >= L or b - a < DIV_MIN or b - a > DIV_MAX or r[a] is None:
            continue
        if l[b] < l[a] and r[b] > r[a] and r[a] < 40:
            push("rsi_bull_div", i, [a, b])
    for x in range(1, len(highs)):
        a, b = highs[x - 1], highs[x]
        i = b + DIV_K
        if i >= L or b - a < DIV_MIN or b - a > DIV_MAX or r[a] is None:
            continue
        if h[b] > h[a] and r[b] < r[a] and r[a] > 60:
            push("rsi_bear_div", i, [a, b])
    ev.sort(key=lambda e: (e[1], e[0]))
    return ev, {"sma50": s50, "sma200": s200, "rsi": r, "squeeze": squeeze, "macd_hist": mh, "st": st}


def adx(h, l, c, n=14):
    """Wilder +DI, -DI, ADX (same as TA.adx)."""
    L = len(c)
    pdm, mdm = [None] * L, [None] * L
    for i in range(1, L):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = up if (up > dn and up > 0) else 0
        mdm[i] = dn if (dn > up and dn > 0) else 0
    s_tr, s_p, s_m = rma(true_range(h, l, c), n), rma(pdm, n), rma(mdm, n)
    pdi = [None if t is None else (0 if t == 0 else 100 * s_p[i] / t) for i, t in enumerate(s_tr)]
    mdi = [None if t is None else (0 if t == 0 else 100 * s_m[i] / t) for i, t in enumerate(s_tr)]
    dx = [None if p is None else (0 if p + mdi[i] == 0 else 100 * abs(p - mdi[i]) / (p + mdi[i])) for i, p in enumerate(pdi)]
    return pdi, mdi, rma(dx, n)


def realized_vol(c, n):
    """Annualized close-to-close volatility of log returns over n bars (as TA.realizedVol)."""
    r = [None] + [math.log(c[i] / c[i - 1]) if c[i] > 0 and c[i - 1] > 0 else None for i in range(1, len(c))]
    o = [None] * len(c)
    for i in range(n, len(c)):
        w = r[i - n + 1:i + 1]
        if any(x is None for x in w):
            continue
        s = sum(w)
        s2 = sum(x * x for x in w)
        m = s / n
        o[i] = math.sqrt(max(0.0, (s2 - n * m * m) / (n - 1)) * 252)
    return o
