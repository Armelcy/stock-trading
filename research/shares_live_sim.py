"""
Shares momentum, live-execution realism check.

Differences vs strategy_lab.py `shares_trail15` (which entered at the signal-day close):
  - signal computed on day i close; entry at day i+1 OPEN (executor buys at ~9:45 ET)
  - trailing stop: peak of closes since entry; trigger when close <= peak*(1-TRAIL)
      exit_mode "close": sell at that close (15:30 backstop sells near the close)
      exit_mode "next_open": sell at next day's open (only the 9:45 run catches it)
  - time exit after MAX_HOLD trading days, at that day's close
  - SHARE_SLIP each way
Usage: python3 research/shares_live_sim.py
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backtest as bt  # noqa: E402

SHARE_SLIP, TRAIL, MAX_HOLD = 0.0005, 0.15, 60

px = bt.load_prices(3)
tickers = [t for t in bt.TICKERS if t in px and len(px[t]) > 300]
idx = px["SPY"].index
f = lambda col: pd.DataFrame({t: px[t][col] for t in tickers}).reindex(idx)
close, high, opn, vol = f("Close"), f("High"), f("Open"), f("Volume").astype(float)
spy = px["SPY"]["Close"]
etf = {e: px[e]["Close"].reindex(idx) for e in set(bt.SECTOR_ETFS.values()) if e in px}

dist = (high.rolling(252).max() - close) / high.rolling(252).max()
vr = vol / vol.rolling(20).mean()
rs = close.pct_change(10).sub(spy.pct_change(10), axis=0)
etf_ok = pd.DataFrame({t: (etf[bt.SECTOR_ETFS[t]] > etf[bt.SECTOR_ETFS[t]].rolling(20).mean())
                       if bt.SECTOR_ETFS[t] in etf else False for t in tickers})
sig = ((dist <= 0.05) & (dist >= 0) & (vr >= 0.8) & (rs > 0) & etf_ok).mul(spy > spy.rolling(20).mean(), axis=0)
sig = sig.astype(bool)
sig.iloc[:252] = False
C, O, S, SCORE, N = close.values, opn.values, sig.values, (-dist).values, len(idx)


def trade(k, i, mode, end=None):
    """Signal at close i → buy open i+1. Returns (exit_day, ratio)."""
    e = i + 1
    last = min(e + MAX_HOLD, N - 1, end if end is not None else N - 1)
    if e >= N or not np.isfinite(O[e, k]):
        return None
    cost = O[e, k] * (1 + SHARE_SLIP)
    peak = max(O[e, k], C[e, k])
    for j in range(e, last + 1):
        c = C[j, k]
        if not np.isfinite(c):
            continue
        peak = max(peak, c)
        if c <= peak * (1 - TRAIL) and j < last:
            if mode == "close" or j + 1 >= N:
                return j, c * (1 - SHARE_SLIP) / cost
            return j + 1, O[j + 1, k] * (1 - SHARE_SLIP) / cost
        if j == last:
            return j, c * (1 - SHARE_SLIP) / cost
    return last, 1.0


def per_trade(mode):
    rows = []
    for k in range(len(tickers)):
        free = 252
        for i in range(252, N - 2):
            if i < free or not S[i, k]:
                continue
            r = trade(k, i, mode)
            if r is None:
                continue
            rows.append((idx[i].year, r[1] - 1, r[0] - i))
            free = r[0] + 1
    d = pd.DataFrame(rows, columns=["year", "r", "days"])
    ci = 1.96 * d.r.std() / math.sqrt(len(d)) * 100
    return (len(d), (d.r > 0).mean() * 100, d.r.mean() * 100, ci, d.days.mean(),
            d.groupby("year").r.mean().mul(100).round(1).to_dict())


def goal_sim(s, mode, max_pos=3, frac=0.30, horizon=54, start_cash=815.0,
             deposits=((12, 500.0), (33, 500.0))):
    e = s + horizon
    cash, dep, pos = start_cash, start_cash, []
    for d in range(s, e + 1):
        for p in [p for p in pos if p[2] == d]:
            cash += p[3] * p[4]
            pos.remove(p)
        for dd, amt in deposits:
            if d == s + dd:
                cash += amt
                dep += amt
        if d >= e - 1 or len(pos) >= max_pos:
            continue
        held = {p[0] for p in pos}
        eq = cash + sum(p[3] for p in pos)
        for k in sorted((k for k in range(len(tickers)) if S[d, k] and k not in held), key=lambda k: -SCORE[d, k]):
            if len(pos) >= max_pos:
                break
            r = trade(k, d, mode, end=e)
            alloc = min(eq * frac, cash)
            if r is None or alloc < 25:
                continue
            cash -= alloc
            pos.append((k, d, r[0], alloc, r[1]))
    return cash + sum(p[3] * p[4] for p in pos), dep


def main():
    print(f"{len(tickers)} tickers, {idx[252].date()} → {idx[-1].date()}, trail {TRAIL:.0%}, max hold {MAX_HOLD}d\n")
    for mode in ("close", "next_open"):
        n, win, exp, ci, days, yr = per_trade(mode)
        print(f"exit={mode:9s} n={n} win={win:.1f}% exp={exp:+.2f}% ±{ci:.2f} avg_hold={days:.0f}d by year {yr}")
    print()
    starts = list(range(252, N - 56, 5))
    for mode in ("close", "next_open"):
        res = [goal_sim(s, mode) for s in starts]
        fin = np.array([r[0] for r in res])
        dep = res[0][1]
        print(f"goal sim exit={mode:9s} 3x30%: median ${np.median(fin):,.0f} p10 ${np.percentile(fin, 10):,.0f} "
              f"p90 ${np.percentile(fin, 90):,.0f} P(>=3000) {(fin >= 3000).mean():.0%} "
              f"P(<deposits ${dep:,.0f}) {(fin < dep).mean():.0%}  windows={len(starts)}")


if __name__ == "__main__":
    main()
