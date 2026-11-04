"""Writes reference.json: a fixed OHLCV series and the TA-Lib 0.6+ outputs the JS indicators are tested against.
Run: pip install TA-Lib numpy && python3 tests/technicals/make_reference.py"""
import json
import os
import random

import numpy as np
import talib

random.seed(7)
n = 600
o, h, l, c, v = [], [], [], [], []
p = 100.0
for i in range(n):
    op = p * (1 + random.gauss(0, 0.006))
    cl = op * (1 + random.gauss(0.0004, 0.017))
    hi = max(op, cl) * (1 + abs(random.gauss(0, 0.007)))
    lo = min(op, cl) * (1 - abs(random.gauss(0, 0.007)))
    o.append(round(op, 4)); h.append(round(hi, 4)); l.append(round(lo, 4)); c.append(round(cl, 4)); v.append(float(random.randint(800_000, 5_000_000)))
    p = cl
O, H, L, C, V = (np.array(x, dtype=float) for x in (o, h, l, c, v))


def arr(x):
    return [None if np.isnan(y) else float(y) for y in x]


up, mid, lo_ = talib.BBANDS(C, 20, 2, 2, 0)
m, s, hist = talib.MACD(C, 12, 26, 9)
sk, sd = talib.STOCH(H, L, C, 14, 3, 0, 3, 0)
ref = {
    "source": f"TA-Lib {talib.__version__} (python wrapper) on a seeded random OHLCV series",
    "o": o, "h": h, "l": l, "c": c, "v": v,
    "sma20": arr(talib.SMA(C, 20)), "ema20": arr(talib.EMA(C, 20)), "wma10": arr(talib.WMA(C, 10)),
    "bb_upper": arr(up), "bb_mid": arr(mid), "bb_lower": arr(lo_),
    "atr14": arr(talib.ATR(H, L, C, 14)), "rsi14": arr(talib.RSI(C, 14)),
    "stoch_k": arr(sk), "stoch_d": arr(sd),
    "macd": arr(m), "macd_signal": arr(s), "macd_hist": arr(hist),
    "adx14": arr(talib.ADX(H, L, C, 14)), "pdi14": arr(talib.PLUS_DI(H, L, C, 14)), "mdi14": arr(talib.MINUS_DI(H, L, C, 14)),
    "obv": arr(talib.OBV(C, V)), "mfi14": arr(talib.MFI(H, L, C, V, 14)),
}
json.dump(ref, open(os.path.join(os.path.dirname(__file__), "reference.json"), "w"))
print("wrote", len(ref), "series")
