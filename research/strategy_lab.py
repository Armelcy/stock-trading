"""
Strategy lab: 52-week-high momentum as shares vs 30-90 DTE calls, realistic costs,
plus a goal simulation ($815 start, +$500 at ~Nov 1 and ~Dec 1, 54 trading days).

Pricing: Black-Scholes with IV = 20d realized vol x IV_MULT (vol held constant per trade).
Fills: options buy at mid+half-spread, sell at mid-half-spread (half = max($0.025, HALF_PCT*mid));
shares pay SHARE_SLIP each way. Stops/targets checked on daily closes and filled at that
day's price (gap-through). Oil gate, earnings blackout and IV filter are not simulated.

Usage: python3 research/strategy_lab.py
"""
import math
import os
import sys
from functools import lru_cache

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backtest as bt  # noqa: E402

IV_MULT, HALF_PCT, SHARE_SLIP, RF = 1.15, 0.025, 0.0005, 0.04
TP, SL, TRAIL_ARM = 0.80, -0.35, 0.40
TD_PER_YEAR = 252

# ── data + signals ──────────────────────────────────────────────────────────
px = bt.load_prices(3)
tickers = [t for t in bt.TICKERS if t in px and len(px[t]) > 300]
idx = px["SPY"].index
close = pd.DataFrame({t: px[t]["Close"] for t in tickers}).reindex(idx)
high = pd.DataFrame({t: px[t]["High"] for t in tickers}).reindex(idx)
vol = pd.DataFrame({t: px[t]["Volume"] for t in tickers}).reindex(idx).astype(float)
spy = px["SPY"]["Close"]
etf = {e: px[e]["Close"].reindex(idx) for e in set(bt.SECTOR_ETFS.values()) if e in px}

dist = (high.rolling(252).max() - close) / high.rolling(252).max()
vr = vol / vol.rolling(20).mean()
spy_ok = spy > spy.rolling(20).mean()
rs = close.pct_change(10).sub(spy.pct_change(10), axis=0)
etf_ok = pd.DataFrame({t: (etf[bt.SECTOR_ETFS[t]] > etf[bt.SECTOR_ETFS[t]].rolling(20).mean())
                       if bt.SECTOR_ETFS[t] in etf else False for t in tickers})
rv = np.log(close).diff().rolling(20).std() * math.sqrt(252)
signal = (dist <= 0.05) & (dist >= 0) & (vr >= 0.8) & (rs > 0) & etf_ok
signal = signal.mul(spy_ok, axis=0).astype(bool)
signal.iloc[:252] = False
C, RV, SIG, SCORE = close.values, rv.values, signal.values, (-dist).values
TI = {t: k for k, t in enumerate(tickers)}
N = len(idx)


def bs_call(S, K, T, s):
    if T <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (RF + 0.5 * s * s) * T) / (s * math.sqrt(T))
    nd = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return S * nd(d1) - K * math.exp(-RF * T) * nd(d1 - s * math.sqrt(T))


VARIANTS = {
    # name: (kind, dte_trading_days, strike_mult, max_hold_td, trail_pct for shares)
    "shares_trail10": ("shares", None, None, 60, 0.10),
    "shares_trail15": ("shares", None, None, 60, 0.15),
    "call30_atm": ("call", 21, 1.00, 16, None),
    "call60_atm": ("call", 42, 1.00, 32, None),
    "call60_itm": ("call", 42, 0.90, 32, None),
    "call90_atm": ("call", 63, 1.00, 53, None),
    "call90_itm": ("call", 63, 0.90, 53, None),
}


@lru_cache(maxsize=None)
def trade(var, k, i, end=None):
    """Enter ticker k at close of day i. Returns (exit_day, value_ratio, unit_cost)."""
    kind, dte, km, hold, trail = VARIANTS[var]
    last = min(i + hold, N - 1, end if end is not None else N - 1)
    S0 = C[i, k]
    if kind == "shares":
        cost, peak = S0 * (1 + SHARE_SLIP), S0
        for j in range(i + 1, last + 1):
            s = C[j, k]
            if not np.isfinite(s):
                continue
            peak = max(peak, s)
            if s <= peak * (1 - trail) or j == last:
                return j, s * (1 - SHARE_SLIP) / cost, cost
        return last, 1.0, cost
    iv = max(RV[i, k] * IV_MULT, 0.12)
    K = S0 * km
    mid = lambda j, s: bs_call(s, K, (dte - (j - i)) / TD_PER_YEAR, iv)
    half = lambda m: max(0.025, HALF_PCT * m)
    m0 = mid(i, S0)
    cost = m0 + half(m0)
    stop, armed = cost * (1 + SL), False
    for j in range(i + 1, last + 1):
        s = C[j, k]
        if not np.isfinite(s):
            continue
        m = mid(j, s)
        r = m / cost - 1
        if r >= TRAIL_ARM and not armed:
            armed, stop = True, cost
        if r >= TP or m <= stop or j == last:
            return j, max(m - half(m), 0.0) / cost, cost * 100
    return last, 1.0, cost * 100


def per_trade_stats(var, start):
    rows = []
    for k in range(len(tickers)):
        free = start
        for i in range(start, N - 1):
            if i < free or not SIG[i, k]:
                continue
            j, ratio, _ = trade(var, k, i)
            rows.append((idx[i].year, ratio - 1))
            free = j + 1
    df = pd.DataFrame(rows, columns=["year", "r"])
    by_year = df.groupby("year").r.mean().mul(100).round(1).to_dict()
    se = df.r.std() / math.sqrt(len(df))
    return len(df), (df.r > 0).mean() * 100, df.r.mean() * 100, 1.96 * se * 100, by_year


def goal_sim(var, s, max_pos, frac, horizon=54, start_cash=815.0, deposits=((12, 500.0), (33, 500.0))):
    e = s + horizon
    cash, deposited, open_pos = start_cash, start_cash, []  # (k, entry_day, exit_day, alloc, ratio)
    for d in range(s, e + 1):
        for p in [p for p in open_pos if p[2] == d]:
            cash += p[3] * p[4]
            open_pos.remove(p)
        for dd, amt in deposits:
            if d == s + dd:
                cash += amt
                deposited += amt
        if d == e or len(open_pos) >= max_pos:
            continue
        held = {p[0] for p in open_pos}
        equity = cash + sum(p[3] for p in open_pos)
        cands = sorted((k for k in range(len(tickers)) if SIG[d, k] and k not in held),
                       key=lambda k: -SCORE[d, k])
        for k in cands:
            if len(open_pos) >= max_pos:
                break
            budget = min(equity * frac, cash)
            j, ratio, unit = trade(var, k, d, end=e)
            if VARIANTS[var][0] == "shares":
                alloc = budget
            else:
                n = int(budget // unit)
                if n < 1:
                    continue
                alloc = n * unit
            if alloc < 25:
                continue
            cash -= alloc
            open_pos.append((k, d, j, alloc, ratio))
    final = cash + sum(p[3] * p[4] for p in open_pos)  # open ones are marked at e via end=e
    return final, deposited


def main():
    start = 252
    print(f"{len(tickers)} tickers, {idx[start].date()} → {idx[-1].date()}, IV x{IV_MULT}, "
          f"half-spread max($0.025, {HALF_PCT:.1%})\n")
    print("PER-TRADE (your exits: +80% / -35% / breakeven after +40%)")
    for v in VARIANTS:
        n, win, exp, ci, yr = per_trade_stats(v, start)
        print(f"  {v:15s} n={n:4d} win={win:4.1f}% exp={exp:+5.1f}% ±{ci:.1f}  by year {yr}")

    print("\nGOAL SIM: $815 start, +$500 day 12 and day 33, 54 trading days, weekly start dates")
    starts = list(range(start, N - 55, 5))
    sizing = [(2, 0.45), (3, 0.30), (4, 0.22)]
    out = []
    for v in VARIANTS:
        for mp, fr in sizing:
            res = [goal_sim(v, s, mp, fr) for s in starts]
            f = np.array([r[0] for r in res])
            dep = res[0][1]
            out.append((v, f"{mp}x{fr:.0%}", (f >= 3000).mean() * 100, np.median(f),
                        np.percentile(f, 10), (f < dep).mean() * 100))
    df = pd.DataFrame(out, columns=["variant", "sizing", "P(>=3000)%", "median$", "p10$", "P(<deposits)%"])
    print(df.round(1).to_string(index=False))
    print(f"\n  windows={len(starts)} | deposits total=${dep:,.0f} | deposits-only ending=${dep:,.0f}")


if __name__ == "__main__":
    main()


def hybrid(core="shares_trail15", sleeve="call60_atm", core_share=0.7, core_sz=(3, 0.30), sleeve_sz=(2, 0.45)):
    starts = list(range(252, N - 55, 5))
    deps = lambda w: ((12, 500.0 * w), (33, 500.0 * w))
    f = np.array([goal_sim(core, s, *core_sz, start_cash=815 * core_share, deposits=deps(core_share))[0]
                  + goal_sim(sleeve, s, *sleeve_sz, start_cash=815 * (1 - core_share), deposits=deps(1 - core_share))[0]
                  for s in starts])
    return (f >= 3000).mean() * 100, np.median(f), np.percentile(f, 10), (f < 1815).mean() * 100
