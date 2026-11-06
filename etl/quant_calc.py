"""Quant statistics for the Technicals & Quant lab. Pure standard-library Python, no numpy.

Every function here is deterministic and documented with its exact formula; METHODS in etl/quant.py repeats the
formulas for the page tooltips. Conventions used throughout:
  * returns are simple daily returns of a total-return index (dividends reinvested at the ex-date close) unless a
    function says log returns;
  * sample standard deviation uses n - 1; skewness and excess kurtosis use population central moments
    (g1 = m3 / m2^1.5, g2 = m4 / m2^2 - 3), the same definitions as scipy.stats.skew/kurtosis with bias=True;
  * percentiles use linear interpolation between order statistics (numpy's default "linear", Hyndman-Fan type 7);
  * annualisation uses 252 trading days.
"""
import math

ANN = 252


# ---------------------------------------------------------------- basic moments
def mean(a):
    return sum(a) / len(a) if a else None


def sd(a):
    n = len(a)
    if n < 2:
        return None
    m = sum(a) / n
    return math.sqrt(sum((x - m) ** 2 for x in a) / (n - 1))


def moments(a):
    """n, mean, sample SD, skewness g1 and excess kurtosis g2 (population central moments)."""
    n = len(a)
    if n < 3:
        return None
    m = sum(a) / n
    m2 = sum((x - m) ** 2 for x in a) / n
    if m2 <= 0:
        return {"n": n, "mean": m, "sd": 0.0, "skew": None, "kurt": None}
    m3 = sum((x - m) ** 3 for x in a) / n
    m4 = sum((x - m) ** 4 for x in a) / n
    return {"n": n, "mean": m, "sd": math.sqrt(m2 * n / (n - 1)), "skew": m3 / m2 ** 1.5, "kurt": m4 / (m2 * m2) - 3}


def percentile(sorted_a, p):
    """Linear interpolation (type 7) on an already sorted list, p in [0, 100]."""
    n = len(sorted_a)
    if not n:
        return None
    if n == 1:
        return sorted_a[0]
    h = (n - 1) * p / 100.0
    lo = int(math.floor(h))
    hi = min(lo + 1, n - 1)
    return sorted_a[lo] + (h - lo) * (sorted_a[hi] - sorted_a[lo])


def pct_rank(values, x):
    """Share of values <= x (the current value counts itself when it is in the list)."""
    v = [y for y in values if y is not None]
    if not v or x is None:
        return None
    return sum(1 for y in v if y <= x) / len(v)


def corr(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sab = saa = sbb = 0.0
    for x, y in zip(a, b):
        dx, dy = x - ma, y - mb
        sab += dx * dy
        saa += dx * dx
        sbb += dy * dy
    return sab / math.sqrt(saa * sbb) if saa > 0 and sbb > 0 else None


def beta(a, b):
    """OLS slope of a on b: cov(a, b) / var(b)."""
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sab = sbb = 0.0
    for x, y in zip(a, b):
        sab += (x - ma) * (y - mb)
        sbb += (y - mb) ** 2
    return sab / sbb if sbb > 0 else None


# ---------------------------------------------------------------- special functions (p-values)
def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c
        c = c if abs(c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c
        c = c if abs(c) > 1e-300 else 1e-300
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-12:
            break
    return h


def betainc(a, b, x):
    """Regularized incomplete beta I_x(a, b) (continued fraction, Numerical Recipes 6.4)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbt) * _betacf(b, a, 1 - x) / b


def t_pvalue(t, df):
    """Two-sided p-value of a Student t statistic."""
    if t is None or df is None or df <= 0 or not math.isfinite(t):
        return None
    return betainc(df / 2.0, 0.5, df / (df + t * t))


def gammainc_q(s, x):
    """Upper regularized incomplete gamma Q(s, x) (series / continued fraction)."""
    if x <= 0:
        return 1.0
    if x < s + 1:
        term = total = 1.0 / s
        ap = s
        for _ in range(500):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-14:
                break
        return 1.0 - total * math.exp(-x + s * math.log(x) - math.lgamma(s))
    b = x + 1 - s
    c = 1e300
    d = 1 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - s)
        b += 2
        d = an * d + b
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = b + an / c
        c = c if abs(c) > 1e-300 else 1e-300
        de = d * c
        h *= de
        if abs(de - 1) < 1e-14:
            break
    return math.exp(-x + s * math.log(x) - math.lgamma(s)) * h


def chi2_sf(x, df):
    return gammainc_q(df / 2.0, x / 2.0)


def norm_sf2(z):
    """Two-sided normal p-value."""
    return math.erfc(abs(z) / math.sqrt(2))


def mean_test(a):
    """Mean, SD, n, t = mean / (SD / sqrt(n)) and its two-sided p-value (df = n - 1)."""
    n = len(a)
    if n < 2:
        return {"n": n, "mean": a[0] if a else None, "sd": None, "t": None, "p": None}
    m, s = sum(a) / n, sd(a)
    t = m / (s / math.sqrt(n)) if s else None
    return {"n": n, "mean": m, "sd": s, "t": t, "p": t_pvalue(t, n - 1)}


def welch(a, b):
    """Welch two-sample t test of mean(a) - mean(b), Welch-Satterthwaite degrees of freedom."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"diff": None, "t": None, "df": None, "p": None}
    ma, mb = sum(a) / na, sum(b) / nb
    va, vb = sd(a) ** 2, sd(b) ** 2
    se2 = va / na + vb / nb
    if se2 <= 0:
        return {"diff": ma - mb, "t": None, "df": None, "p": None}
    t = (ma - mb) / math.sqrt(se2)
    df = se2 ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)) if va or vb else None
    return {"diff": ma - mb, "t": t, "df": df, "p": t_pvalue(t, df)}


# ---------------------------------------------------------------- series helpers
def total_return_index(c, days, divs):
    """TR_0 = 1; TR_t = TR_{t-1} * (C_t + D_t) / C_{t-1} with D_t the cash dividends going ex on day t."""
    dv = {}
    for d, a in divs or []:
        dv[d] = dv.get(d, 0.0) + a
    out, acc = [], 1.0
    for i in range(len(c)):
        if i and c[i - 1] > 0 and c[i] > 0:   # a non-positive print (e.g. WTI on 2020-04-20) carries the index flat
            acc *= (c[i] + dv.get(days[i], 0.0)) / c[i - 1]
        out.append(acc)
    return out


def simple_returns(x):
    return [None] + [x[i] / x[i - 1] - 1 if x[i - 1] else None for i in range(1, len(x))]


def log_returns(x):
    return [None] + [math.log(x[i] / x[i - 1]) if x[i - 1] and x[i - 1] > 0 and x[i] > 0 else None for i in range(1, len(x))]


def rolling_sd(a, n, scale=1.0):
    """Rolling sample SD over windows of n values (None until n values are available; a None resets the window)."""
    out = [None] * len(a)
    s = s2 = 0.0
    k = 0
    for i, x in enumerate(a):
        if x is None:
            s = s2 = 0.0
            k = 0
            continue
        s += x
        s2 += x * x
        k += 1
        if k > n:
            y = a[i - n]
            s -= y
            s2 -= y * y
            k = n
        if k == n:
            var = (s2 - s * s / n) / (n - 1)
            out[i] = math.sqrt(max(var, 0.0)) * scale
    return out


def rolling_corr_beta(a, b, n, min_frac=0.8):
    """Rolling Pearson correlation and OLS beta of a on b over the last n positions. Positions where either value is
    None are skipped; a window is published only when at least min_frac * n pairs are present."""
    L = len(a)
    rc, rb = [None] * L, [None] * L
    sx = sy = sxx = syy = sxy = 0.0
    k = 0
    need = int(math.ceil(n * min_frac))
    for i in range(L):
        x, y = a[i], b[i]
        if x is not None and y is not None:
            sx += x; sy += y; sxx += x * x; syy += y * y; sxy += x * y; k += 1
        j = i - n
        if j >= 0:
            x0, y0 = a[j], b[j]
            if x0 is not None and y0 is not None:
                sx -= x0; sy -= y0; sxx -= x0 * x0; syy -= y0 * y0; sxy -= x0 * y0; k -= 1
        if k >= need and k >= 3:
            cxy = sxy - sx * sy / k
            cxx = sxx - sx * sx / k
            cyy = syy - sy * sy / k
            if cyy > 1e-18:
                rb[i] = cxy / cyy
                if cxx > 1e-18:
                    rc[i] = max(-1.0, min(1.0, cxy / math.sqrt(cxx * cyy)))
    return rc, rb


def sma(a, n):
    out = [None] * len(a)
    s = 0.0
    for i, x in enumerate(a):
        s += x
        if i >= n:
            s -= a[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def rsi_wilder(c, n=14):
    """RSI with Wilder smoothing seeded by the simple average of the first n changes (matches etl/technicals_calc.rsi)."""
    L = len(c)
    out = [None] * L
    if L <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        g += max(d, 0.0)
        l += max(-d, 0.0)
    ag, al = g / n, l / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, L):
        d = c[i] - c[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


# ---------------------------------------------------------------- distribution, risk
PCTS = (1, 5, 10, 25, 50, 75, 90, 95, 99)


def distribution(r):
    """Moments plus percentiles, worst and best of a return sample."""
    r = [x for x in r if x is not None]
    mo = moments(r)
    if not mo:
        return None
    s = sorted(r)
    mo["pct"] = {str(p): percentile(s, p) for p in PCTS}
    mo["min"], mo["max"] = s[0], s[-1]
    mo["up"] = sum(1 for x in r if x > 0) / len(r)
    if mo["sd"]:
        m, v = mo["mean"], mo["sd"]
        mo["beyond3"] = sum(1 for x in r if abs(x - m) > 3 * v)
        mo["normal3"] = len(r) * 0.0026997961
    return mo


def histogram(r, lo, hi, nb):
    w = (hi - lo) / nb
    h = [0] * nb
    under = over = 0
    for x in r:
        if x is None:
            continue
        j = int((x - lo) // w)
        if j < 0:
            under += 1
        elif j >= nb:
            over += 1
        else:
            h[j] += 1
    return {"lo": lo, "hi": hi, "w": w, "n": h, "under": under, "over": over}


def var_cvar(r, level):
    """Historical VaR and CVaR (expected shortfall) at confidence `level` (0.95, 0.99), as positive loss fractions:
    VaR = -Q(1 - level) with type-7 interpolation; CVaR = -mean of returns <= -VaR."""
    s = sorted(x for x in r if x is not None)
    if len(s) < 20:
        return None, None, len(s)
    q = percentile(s, (1 - level) * 100)
    tail = [x for x in s if x <= q]
    return -q, -sum(tail) / len(tail), len(s)


def drawdowns(tr, days, top=5):
    """Drawdown series dd_t = TR_t / max(TR_0..t) - 1 and the deepest `top` episodes (peak, trough, recovery = first
    day the index regains the peak, durations in trading days)."""
    dd = []
    peak, pi = tr[0], 0
    eps = []
    cur = None
    for i, x in enumerate(tr):
        if x >= peak:
            if cur:
                cur["recovered"] = i
                eps.append(cur)
                cur = None
            peak, pi = x, i
            dd.append(0.0)
            continue
        v = x / peak - 1
        dd.append(v)
        if cur is None:
            cur = {"peak": pi, "trough": i, "depth": v, "recovered": None}
        elif v < cur["depth"]:
            cur["trough"], cur["depth"] = i, v
    if cur:
        eps.append(cur)
    eps.sort(key=lambda e: e["depth"])
    out = []
    for e in eps[:top]:
        out.append({"peak": days[e["peak"]], "trough": days[e["trough"]], "recovered": days[e["recovered"]] if e["recovered"] is not None else None,
                    "depth": e["depth"], "to_trough": e["trough"] - e["peak"], "to_recover": (e["recovered"] - e["trough"]) if e["recovered"] is not None else None,
                    "length": ((e["recovered"] if e["recovered"] is not None else len(tr) - 1) - e["peak"])})
    cur_dd = dd[-1]
    since = None
    if cur_dd < 0:
        j = len(tr) - 1
        while j > 0 and dd[j] < 0:
            j -= 1
        since = days[j]
    return dd, out, {"current": cur_dd, "peak_date": since}


def month_end_returns(tr, days, include_partial=False):
    """[('YYYY-MM', return)] from month-end to month-end of the total-return index. The first (partial) month is
    dropped; the current month to date is included only with include_partial."""
    ends = []
    for i in range(len(days) - 1):
        if days[i + 1][:7] != days[i][:7]:
            ends.append(i)
    if include_partial and days and (not ends or ends[-1] != len(days) - 1):
        ends.append(len(days) - 1)
    out = []
    for a, b in zip(ends, ends[1:]):
        out.append((days[b][:7], tr[b] / tr[a] - 1))
    return out


def week_returns(tr, days):
    """Friday-to-Friday (last trading day of each ISO week) total returns."""
    import datetime as dt
    ends = []
    for i in range(len(days)):
        if i == len(days) - 1:
            ends.append(i)
            break
        a = dt.date.fromisoformat(days[i]).isocalendar()[:2]
        b = dt.date.fromisoformat(days[i + 1]).isocalendar()[:2]
        if a != b:
            ends.append(i)
    return [tr[b] / tr[a] - 1 for a, b in zip(ends, ends[1:])]


def capture(rs, rb):
    """Up/down capture from paired period returns: geometric average of the asset over the periods where the benchmark
    rose (fell), divided by the benchmark's geometric average over the same periods (Morningstar definition)."""
    def geo(x):
        p = 1.0
        for v in x:
            p *= 1 + v
        return p ** (1 / len(x)) - 1
    up = [(a, b) for a, b in zip(rs, rb) if b > 0]
    dn = [(a, b) for a, b in zip(rs, rb) if b < 0]
    out = {"n_up": len(up), "n_down": len(dn), "up": None, "down": None}
    if up:
        gb = geo([b for _, b in up])
        out["up"] = geo([a for a, _ in up]) / gb if gb else None
    if dn:
        gb = geo([b for _, b in dn])
        out["down"] = geo([a for a, _ in dn]) / gb if gb else None
    if out["up"] is not None and out["down"]:
        out["ratio"] = out["up"] / out["down"]
    return out


def sharpe_sortino(r, rf):
    """Annualised Sharpe = mean(r - rf) / SD(r - rf) * sqrt(252); Sortino = mean(r - rf) * 252 / (sqrt(mean(min(r - rf, 0)^2)) * sqrt(252)).
    r and rf are paired daily simple returns; rf is the daily T-bill return."""
    ex = [a - b for a, b in zip(r, rf)]
    n = len(ex)
    if n < 20:
        return None
    m = sum(ex) / n
    s = sd(ex)
    dd = math.sqrt(sum(min(x, 0.0) ** 2 for x in ex) / n)
    geo = 1.0
    for x in r:
        geo *= 1 + x
    return {"n": n, "sharpe": m / s * math.sqrt(ANN) if s else None, "sortino": m * ANN / (dd * math.sqrt(ANN)) if dd else None,
            "ann_return": geo ** (ANN / n) - 1, "ann_vol": sd(r) * math.sqrt(ANN), "ann_excess": m * ANN, "rf_ann": sum(rf) / n * ANN}


# ---------------------------------------------------------------- dependence: autocorrelation, variance ratio, Hurst
def acf(r, lags=10):
    """rho_k = sum_{t>k} (r_t - m)(r_{t-k} - m) / sum_t (r_t - m)^2; Ljung-Box Q = n(n+2) sum rho_k^2 / (n - k), chi-square(lags)."""
    n = len(r)
    if n < lags + 10:
        return None
    m = sum(r) / n
    d = [x - m for x in r]
    den = sum(x * x for x in d)
    if den <= 0:
        return None
    rho = []
    for k in range(1, lags + 1):
        rho.append(sum(d[t] * d[t - k] for t in range(k, n)) / den)
    q = n * (n + 2) * sum(p * p / (n - k) for k, p in enumerate(rho, 1))
    return {"n": n, "rho": rho, "band": 1.96 / math.sqrt(n), "lb_q": q, "lb_p": chi2_sf(q, lags)}


def variance_ratio(x, q):
    """Lo-MacKinlay (1988) variance ratio of log returns x for holding period q with overlapping q-sums and the
    heteroskedasticity-robust z*(q):
      mu = mean(x); s2a = sum (x_t - mu)^2 / (n - 1)
      s2c = (1/m) sum_{t=q..n} (x_t + ... + x_{t-q+1} - q mu)^2, m = q (n - q + 1)(1 - q/n)
      VR = s2c / s2a;  delta_j = n sum_{t>j} (x_t - mu)^2 (x_{t-j} - mu)^2 / [sum (x_t - mu)^2]^2
      theta = sum_{j=1..q-1} [2 (q - j) / q]^2 delta_j;  z* = sqrt(n) (VR - 1) / sqrt(theta)."""
    n = len(x)
    if n < 4 * q:
        return None
    mu = sum(x) / n
    d = [v - mu for v in x]
    ss = sum(v * v for v in d)
    s2a = ss / (n - 1)
    m = q * (n - q + 1) * (1 - q / n)
    acc = 0.0
    run = sum(d[:q])
    acc += run * run
    for t in range(q, n):
        run += d[t] - d[t - q]
        acc += run * run
    s2c = acc / m
    vr = s2c / s2a
    theta = 0.0
    d2 = [v * v for v in d]
    for j in range(1, q):
        delta = n * sum(d2[t] * d2[t - j] for t in range(j, n)) / (ss * ss)
        theta += (2 * (q - j) / q) ** 2 * delta
    z = math.sqrt(n) * (vr - 1) / math.sqrt(theta) if theta > 0 else None
    return {"q": q, "vr": vr, "z": z, "p": norm_sf2(z) if z is not None else None, "n": n}


def hurst_rs(x, min_n=10, max_frac=0.25, sizes=12):
    """Classical rescaled-range Hurst exponent: for log-spaced window sizes w from min_n to max_frac * N, split x into
    floor(N / w) non-overlapping blocks; per block Y = cumulative sum of (x - block mean), R = max(Y) - min(Y),
    S = population SD; average R/S over blocks. H = OLS slope of log(R/S) on log(w). No Anis-Lloyd small-sample correction."""
    N = len(x)
    hi = int(N * max_frac)
    if hi < min_n * 2:
        return None
    ws = sorted({int(round(math.exp(math.log(min_n) + k * (math.log(hi) - math.log(min_n)) / (sizes - 1)))) for k in range(sizes)})
    pts = []
    for w in ws:
        nb = N // w
        vals = []
        for b in range(nb):
            seg = x[b * w:(b + 1) * w]
            m = sum(seg) / w
            y = mx = mn = 0.0
            ss = 0.0
            for v in seg:
                y += v - m
                mx = max(mx, y)
                mn = min(mn, y)
                ss += (v - m) ** 2
            s = math.sqrt(ss / w)
            if s > 0:
                vals.append((mx - mn) / s)
        if vals:
            pts.append((w, sum(vals) / len(vals), len(vals)))
    if len(pts) < 4:
        return None
    lx = [math.log(p[0]) for p in pts]
    ly = [math.log(p[1]) for p in pts]
    h = beta(ly, lx)
    c = corr(ly, lx)
    return {"h": h, "r2": c * c if c is not None else None, "points": [[p[0], p[1], p[2]] for p in pts], "n": N}


# ---------------------------------------------------------------- regression
def solve(A, b):
    """Solves A x = b by Gauss-Jordan elimination with partial pivoting. Returns None for a singular matrix."""
    n = len(A)
    M = [list(map(float, A[i])) + [float(b[i])] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-14:
            return None
        M[col], M[piv] = M[piv], M[col]
        p = M[col][col]
        M[col] = [v / p for v in M[col]]
        for r in range(n):
            if r != col and M[r][col]:
                f = M[r][col]
                M[r] = [v - f * w for v, w in zip(M[r], M[col])]
    return [M[i][n] for i in range(n)]


def inverse(A):
    n = len(A)
    cols = []
    for j in range(n):
        e = [0.0] * n
        e[j] = 1.0
        x = solve(A, e)
        if x is None:
            return None
        cols.append(x)
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def ols(y, X, nw_lags=None):
    """OLS of y on [1, X] (X rows are regressor tuples). Returns coefficients (intercept first), classical standard
    errors, Newey-West HAC standard errors with Bartlett weights for nw_lags lags (if given), t-stats, R^2, adjusted R^2."""
    n = len(y)
    k = len(X[0]) + 1 if X else 1
    if n <= k + 2:
        return None
    Z = [[1.0] + list(r) for r in X]
    XtX = [[0.0] * k for _ in range(k)]
    Xty = [0.0] * k
    for zi, yi in zip(Z, y):
        for a in range(k):
            za = zi[a]
            Xty[a] += za * yi
            row = XtX[a]
            for b_ in range(a, k):
                row[b_] += za * zi[b_]
    for a in range(k):
        for b_ in range(a):
            XtX[a][b_] = XtX[b_][a]
    inv = inverse(XtX)
    if inv is None:
        return None
    coef = [sum(inv[a][b_] * Xty[b_] for b_ in range(k)) for a in range(k)]
    res = [yi - sum(c * z for c, z in zip(coef, zi)) for zi, yi in zip(Z, y)]
    sse = sum(e * e for e in res)
    my = sum(y) / n
    sst = sum((v - my) ** 2 for v in y)
    s2 = sse / (n - k)
    se = [math.sqrt(max(inv[a][a] * s2, 0.0)) for a in range(k)]
    out = {"n": n, "k": k, "coef": coef, "se": se, "t": [c / s if s else None for c, s in zip(coef, se)],
           "r2": 1 - sse / sst if sst else None, "adj_r2": 1 - (sse / (n - k)) / (sst / (n - 1)) if sst else None, "resid_sd": math.sqrt(s2)}
    if nw_lags:
        S = [[0.0] * k for _ in range(k)]
        for zi, e in zip(Z, res):
            for a in range(k):
                for b_ in range(k):
                    S[a][b_] += e * e * zi[a] * zi[b_]
        for l in range(1, nw_lags + 1):
            w = 1 - l / (nw_lags + 1)
            for t in range(l, n):
                ee = res[t] * res[t - l]
                zt, zl = Z[t], Z[t - l]
                for a in range(k):
                    for b_ in range(k):
                        S[a][b_] += w * ee * (zt[a] * zl[b_] + zl[a] * zt[b_])
        # V = inv S inv
        tmp = [[sum(inv[a][c] * S[c][b_] for c in range(k)) for b_ in range(k)] for a in range(k)]
        V = [[sum(tmp[a][c] * inv[c][b_] for c in range(k)) for b_ in range(k)] for a in range(k)]
        nse = [math.sqrt(max(V[a][a], 0.0)) for a in range(k)]
        out["nw_lags"] = nw_lags
        out["nw_se"] = nse
        out["nw_t"] = [c / s if s else None for c, s in zip(coef, nse)]
        out["nw_p"] = [t_pvalue(t, n - k) if t is not None else None for t in out["nw_t"]]
    return out


def rolling_ols(y, X, window, step, ends=None):
    """Coefficients of OLS y ~ [1, X] over trailing windows of `window` rows, evaluated every `step` rows (or at the
    given end positions), with running cross-product sums so each evaluation is a k x k solve."""
    n = len(y)
    k = len(X[0]) + 1
    XtX = [[0.0] * k for _ in range(k)]
    Xty = [0.0] * k
    yy = sy = 0.0

    def add(i, sgn):
        nonlocal yy, sy
        z = [1.0] + list(X[i])
        for a in range(k):
            Xty[a] += sgn * z[a] * y[i]
            for b_ in range(k):
                XtX[a][b_] += sgn * z[a] * z[b_]
        yy += sgn * y[i] * y[i]
        sy += sgn * y[i]
    want = set(ends) if ends is not None else None
    out = []
    for i in range(n):
        add(i, 1)
        if i >= window:
            add(i - window, -1)
        if i >= window - 1 and ((want is not None and i in want) or (want is None and (n - 1 - i) % step == 0)):
            c = solve(XtX, Xty)
            if c is None:
                continue
            sse = yy - sum(c[a] * Xty[a] for a in range(k))
            sst = yy - sy * sy / window
            out.append((i, c, 1 - sse / sst if sst > 0 else None))
    return out


# ---------------------------------------------------------------- calendars
def us_eastern_offset(utc_dt):
    """Hours to add to UTC for US Eastern time (EDT -4 from the second Sunday of March 02:00 local to the first Sunday
    of November 02:00 local, EST -5 otherwise; the 2007+ rule)."""
    import datetime as dt
    y = utc_dt.year

    def nth_sunday(month, nth):
        d = dt.datetime(y, month, 1)
        d += dt.timedelta(days=(6 - d.weekday()) % 7)
        return d + dt.timedelta(weeks=nth - 1)
    start = nth_sunday(3, 2) + dt.timedelta(hours=7)   # 02:00 EST = 07:00 UTC
    end = nth_sunday(11, 1) + dt.timedelta(hours=6)    # 02:00 EDT = 06:00 UTC
    naive = utc_dt.replace(tzinfo=None)
    return -4 if start <= naive < end else -5


def to_eastern(iso_utc):
    """'2026-11-02T21:30:23.000Z' -> (date 'YYYY-MM-DD', minutes after midnight) in US Eastern time."""
    import datetime as dt
    s = iso_utc.replace("Z", "").split(".")[0]
    u = dt.datetime.fromisoformat(s)
    e = u + dt.timedelta(hours=us_eastern_offset(u))
    return e.date().isoformat(), e.hour * 60 + e.minute


def holidays_from_calendar(days):
    """Weekdays between the first and last trading day that are missing from the trading calendar `days` (exchange
    holidays and unscheduled closures). Returns (pre, post): indexes of trading days immediately before / after one."""
    import datetime as dt
    have = set(days)
    pre, post = set(), set()
    for i in range(len(days) - 1):
        a = dt.date.fromisoformat(days[i])
        b = dt.date.fromisoformat(days[i + 1])
        cur = a + dt.timedelta(days=1)
        gap = False
        while cur < b:
            if cur.weekday() < 5 and cur.isoformat() not in have:
                gap = True
                break
            cur += dt.timedelta(days=1)
        if gap:
            pre.add(i)
            post.add(i + 1)
    return pre, post
