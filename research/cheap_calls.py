"""
90-DTE calls restricted to stocks under $60 at entry, vs shares on the same names,
plus a 6-month account sim at the real size ($775 start, +$450 every ~21 trading days).

Reuses strategy_lab's signals, pricing and costs (Black-Scholes, IV = 20d RV x 1.15,
half-spread max($0.025, 2.5%), option exits +80% / -35% / breakeven after +40%).

Usage: python3 research/cheap_calls.py
"""
import numpy as np
import pandas as pd

import strategy_lab as L

PRICE_CAP = 60.0
START = 252


def per_trade(var, cap=PRICE_CAP):
    rows = []
    for k in range(len(L.tickers)):
        free = START
        for i in range(START, L.N - 1):
            if i < free or not L.SIG[i, k] or not (L.C[i, k] < cap):
                continue
            j, ratio, unit = L.trade(var, k, i)
            rows.append((L.tickers[k], L.idx[i].year, ratio - 1, unit, j - i))
            free = j + 1
    return pd.DataFrame(rows, columns=["t", "year", "r", "unit", "days"])


def summary(name, df):
    se = df.r.std() / np.sqrt(len(df))
    yr = df.groupby("year").r.mean().mul(100).round(1).to_dict()
    return (f"  {name:22s} n={len(df):3d} win={(df.r > 0).mean() * 100:4.1f}% "
            f"avg={df.r.mean() * 100:+5.1f}% ±{1.96 * se * 100:4.1f} "
            f"worst={df.r.min() * 100:+5.0f}% hold={df.days.mean():4.0f}d  by year {yr}")


def account_sim(s, call_slot, horizon=126, start_cash=775.0, dep=450.0, every=21,
                share_slots=5, share_frac=0.20, call_cap=0.35):
    """share_slots x share_frac in shares; if call_slot, one slot is a 90d ITM call on a <$60 name
    (whole contracts, cost <= call_cap of equity) and share_slots drops by one."""
    e = s + horizon
    cash, deposited, pos = start_cash, start_cash, []  # (k, exit_day, alloc, ratio, kind)
    n_share = share_slots - (1 if call_slot else 0)
    for d in range(s, e + 1):
        for p in [p for p in pos if p[1] == d]:
            cash += p[2] * p[3]
            pos.remove(p)
        if d > s and (d - s) % every == 0:
            cash += dep
            deposited += dep
        if d == e:
            continue
        held = {p[0] for p in pos}
        equity = cash + sum(p[2] for p in pos)
        cands = sorted((k for k in range(len(L.tickers)) if L.SIG[d, k] and k not in held),
                       key=lambda k: -L.SCORE[d, k])
        for k in cands:
            nc = sum(p[4] == "call" for p in pos)
            ns = sum(p[4] == "shares" for p in pos)
            if call_slot and nc == 0 and L.C[d, k] < PRICE_CAP:
                j, ratio, unit = L.trade("call90_itm", k, d, end=e)
                n = int(min(equity * call_cap, cash) // unit)
                if n >= 1:
                    cash -= n * unit
                    pos.append((k, j, n * unit, ratio, "call"))
                    continue
            if ns < n_share:
                alloc = min(equity * share_frac, cash)
                if alloc < 25:
                    continue
                j, ratio, _ = L.trade("shares_trail15", k, d, end=e)
                cash -= alloc
                pos.append((k, j, alloc, ratio, "shares"))
    final = cash + sum(p[2] * p[3] for p in pos)
    return final, deposited


def main():
    print(f"Universe: {len(L.tickers)} tickers, {L.idx[START].date()} -> {L.idx[-1].date()}")
    cheap = sorted({L.tickers[k] for k in range(len(L.tickers))
                    if np.nanmin(L.C[START:, k]) < PRICE_CAP})
    print(f"Ever under ${PRICE_CAP:.0f}: {', '.join(cheap)}\n")

    print(f"PER TRADE, signals on stocks under ${PRICE_CAP:.0f} at entry")
    dfs = {v: per_trade(v) for v in ["shares_trail15", "call90_itm", "call90_atm", "call60_itm"]}
    for v, df in dfs.items():
        print(summary(v, df))
    c = dfs["call90_itm"]
    print(f"\n  call90_itm contract cost: median ${c.unit.median():.0f}, "
          f"p90 ${c.unit.quantile(.9):.0f}, max ${c.unit.max():.0f}")
    print(f"  losses worse than -30%: {(c.r < -0.30).mean() * 100:.0f}% of trades")
    print("  by ticker (avg %, n):",
          c.groupby("t").r.agg(["mean", "count"]).assign(mean=lambda x: (x["mean"] * 100).round(1))
          .sort_values("count", ascending=False).head(8).to_dict("index"))

    print("\nACCOUNT SIM: $775 start, +$450 every 21 trading days, 126 trading days (~6 months)")
    starts = list(range(START, L.N - 127, 5))
    rows = []
    for label, cs in [("5 x 20% shares (current)", False), ("4 x 20% shares + 1 call slot", True)]:
        res = [account_sim(s, cs) for s in starts]
        f = np.array([r[0] for r in res])
        dep = res[0][1]
        gain = f - dep
        rows.append((label, np.median(f), np.median(gain), np.percentile(gain, 10),
                     np.percentile(gain, 90), (gain < 0).mean() * 100))
    out = pd.DataFrame(rows, columns=["setup", "median end $", "median gain $", "p10 gain $",
                                      "p90 gain $", "P(lose money)%"])
    print(out.round(0).to_string(index=False))
    print(f"  windows={len(starts)} | deposited=${dep:,.0f}")


if __name__ == "__main__":
    main()
