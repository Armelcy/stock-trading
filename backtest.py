"""
Backtest for the 52-Week High Momentum Options Screener (screener.py)

Simulates the screener's actual trade: when a stock is within 5% of its
52-week high on >= average volume, buy a ~3-week ATM call, exit at
+80% (target), -40% (stop), Thursday of expiry week, or expiry.

Option prices are simulated with Black-Scholes using 20-day realized vol.
Earnings blackout is not simulated (no reliable historical earnings dates).
IV-ratio filter is not simulated (no historical IV); use --iv-mult instead.

Realistic mode (default) vs the original optimistic model:
  - fills: buy at ask, sell at bid (half-spread = max($0.025, HALF_SPREAD_PCT * mid)),
    or a flat round-trip haircut via --haircut 0.10 / 0.15
  - stops/targets fill at that day's modelled price (gap-through), not exactly at -40%/+80%
  - Thursday-of-expiry-week exit (live rule) instead of holding to expiry
  - live filters: SPY > 20d MA, 10d RS vs SPY, sector ETF > 20d MA
  - live premium window $0.20-$1.00/sh, contracts sized to PER_TRADE

Usage:
  python3 backtest.py [years] [--ideal] [--haircut 0.10] [--iv-mult 1.2]
                      [--no-live-filters] [--no-premium-window]
  --ideal reproduces the original model (mid fills, exact stop/target, hold to expiry,
  52w+volume filters only).
"""

import sys
import math
import argparse
import numpy as np
import pandas as pd
import yfinance as yf

# ── Match screener.py config ───────────────────────────────────────────────
MAX_DIST_FROM_HIGH = 0.05
MIN_VOL_RATIO      = 0.8
HOLD_DAYS_CAL      = 21          # ~3 weeks to expiry
TARGET_MULT        = 1.80        # +80% premium target
STOP_MULT          = 0.60        # -40% premium stop
RISK_FREE          = 0.04
PER_TRADE          = 150         # dollars per trade
PREMIUM_LOW, PREMIUM_HIGH = 0.20, 1.00
SPY_MA, SECTOR_MA, RS_LOOKBACK = 20, 20, 10
HALF_SPREAD_PCT    = 0.05        # half-spread as % of mid (full spread ~10%)
MIN_HALF_SPREAD    = 0.025       # $0.05 minimum full spread on cheap options
N_TD               = 15          # trading days to expiry
THURSDAY_TD        = 14          # Thursday of expiry week

SECTOR_ETFS = {
    "SOUN": "XLK", "PLTR": "XLK", "CRWD": "XLK", "SNOW": "XLK",
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AMZN": "XLY",
    "GOOGL": "XLC", "META": "XLC", "TSLA": "XLY",
    "AMD": "XLK", "ORCL": "XLK", "ADBE": "XLK", "CRM": "XLK", "NOW": "XLK",
    "NFLX": "XLC",
    "V": "XLF", "MA": "XLF", "JPM": "XLF", "AXP": "XLF", "GS": "XLF",
    "MS": "XLF", "BAC": "XLF", "BLK": "XLF", "SCHW": "XLF",
    "CAT": "XLI", "DE": "XLI", "HON": "XLI", "GE": "XLI", "ETN": "XLI",
    "EMR": "XLI", "UNP": "XLI", "FDX": "XLI", "MMM": "XLI",
    "RTX": "XLI", "LMT": "XLI", "NOC": "XLI", "BA": "XLI", "GD": "XLI",
    "LLY": "XLV", "ABT": "XLV", "DHR": "XLV", "JNJ": "XLV",
    "UNH": "XLV", "MRK": "XLV", "PFE": "XLV",
    "COST": "XLP", "HD": "XLY", "NKE": "XLY", "MCD": "XLP",
    "SLB": "XLE", "MPC": "XLE", "XOM": "XLE", "CVX": "XLE",
    "OXY": "XLE", "HAL": "XLE",
}
TICKERS = list(SECTOR_ETFS)


def bs_call(S, K, T, sigma, r=RISK_FREE):
    """Black-Scholes call price. T in years."""
    if T <= 0:
        return max(S - K, 0.0)
    if sigma <= 0:
        return max(S - K * math.exp(-r * T), 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return S * N(d1) - K * math.exp(-r * T) * N(d2)


def load_prices(years):
    syms = TICKERS + ["SPY"] + sorted(set(SECTOR_ETFS.values()))
    df = yf.download(syms, period=f"{years + 1}y", auto_adjust=True,
                     group_by="ticker", progress=False, threads=True)
    return {s: df[s].dropna() for s in syms if s in df.columns.get_level_values(0)}


def make_fill(args):
    """Return (buy_px(mid), sell_px(mid)) functions for the chosen fill model."""
    if args.ideal:
        return (lambda m: m), (lambda m: m)
    if args.haircut is not None:
        h = args.haircut / 2
        return (lambda m: m * (1 + h)), (lambda m: max(m * (1 - h), 0.0))
    hs = lambda m: max(MIN_HALF_SPREAD, HALF_SPREAD_PCT * m)
    return (lambda m: m + hs(m)), (lambda m: max(m - hs(m), 0.0))


def backtest_ticker(ticker, px, args, buy_px, sell_px):
    hist = px.get(ticker)
    if hist is None or len(hist) < 300:
        return []
    spy = px["SPY"]["Close"]
    etf = px.get(SECTOR_ETFS[ticker])
    hist = hist.join(spy.rename("SPY"), how="inner")
    if etf is not None:
        hist = hist.join(etf["Close"].rename("ETF"), how="inner")

    close, high = hist["Close"].values, hist["High"].values
    vol = hist["Volume"].values.astype(float)
    spy_c = hist["SPY"].values
    etf_c = hist["ETF"].values if "ETF" in hist else None
    dates = hist.index
    logret = np.diff(np.log(close), prepend=np.nan)
    live = not (args.ideal or args.no_live_filters)
    window = not (args.ideal or args.no_premium_window)

    trades, open_until = [], -1
    for i in range(252, len(close) - 1):
        if i <= open_until:
            continue
        high_52w = high[i - 251:i + 1].max()
        if not (0 <= (high_52w - close[i]) / high_52w <= MAX_DIST_FROM_HIGH):
            continue
        v20 = vol[i - 19:i + 1].mean()
        if v20 <= 0 or vol[i] / v20 < MIN_VOL_RATIO:
            continue
        if live:
            if spy_c[i] <= spy_c[i - SPY_MA + 1:i + 1].mean():
                continue
            rs = close[i] / close[i - RS_LOOKBACK] - spy_c[i] / spy_c[i - RS_LOOKBACK]
            if rs <= 0:
                continue
            if etf_c is None or etf_c[i] <= etf_c[i - SECTOR_MA + 1:i + 1].mean():
                continue
        sig = np.nanstd(logret[i - 19:i + 1]) * math.sqrt(252)
        if not np.isfinite(sig) or sig <= 0:
            continue
        iv = sig * args.iv_mult

        S0 = K = close[i]
        T0 = HOLD_DAYS_CAL / 365.0
        mid0 = bs_call(S0, K, T0, iv)
        if mid0 <= 0.01:
            continue
        if window and not (PREMIUM_LOW <= mid0 <= PREMIUM_HIGH):
            continue
        cost = buy_px(mid0)

        last_j = THURSDAY_TD if not args.ideal else N_TD
        exit_px, exit_reason, exit_j = None, "expiry", None
        for j in range(1, last_j + 1):
            if i + j >= len(close):
                break
            T = max(T0 - j * (HOLD_DAYS_CAL / N_TD) / 365.0, 0.0)
            m = bs_call(close[i + j], K, T, iv)
            # Live rules trigger on mid vs entry
            if m >= cost * TARGET_MULT:
                exit_px = cost * TARGET_MULT if args.ideal else sell_px(m)
                exit_reason, exit_j = "target", j
                break
            if m <= cost * STOP_MULT:
                exit_px = cost * STOP_MULT if args.ideal else sell_px(m)
                exit_reason, exit_j = "stop", j
                break
        if exit_px is None:
            j = min(last_j, len(close) - 1 - i)
            if args.ideal:
                exit_px = max(close[i + j] - K, 0.0)
            else:
                T = max(T0 - j * (HOLD_DAYS_CAL / N_TD) / 365.0, 0.0)
                exit_px = sell_px(bs_call(close[i + j], K, T, iv))
                exit_reason = "thursday"
            exit_j = j

        pnl_pct = (exit_px - cost) / cost
        if window:
            contracts = int(PER_TRADE // (cost * 100))
            if contracts < 1:
                continue
            pnl_usd = (exit_px - cost) * 100 * contracts
        else:
            pnl_usd = pnl_pct * PER_TRADE
        trades.append({
            "ticker": ticker, "date": dates[i].date(), "year": dates[i].year,
            "sigma": round(sig, 3), "entry_prem": round(cost, 2),
            "exit_prem": round(exit_px, 2), "exit": exit_reason, "days_held": exit_j,
            "pnl_pct": round(pnl_pct * 100, 1), "pnl_usd": round(pnl_usd, 2),
        })
        open_until = i + (exit_j or N_TD)
    return trades


def summarize(df, label):
    wins, losses = df[df.pnl_pct > 0], df[df.pnl_pct <= 0]
    gl = abs(losses.pnl_usd.sum())
    se = df.pnl_pct.std() / math.sqrt(len(df)) if len(df) > 1 else float("nan")
    print(f"\n=== {label} ===")
    print(f"  Trades {len(df)} | win {len(wins)/len(df)*100:.1f}% | "
          f"avg win {wins.pnl_pct.mean():.1f}% / loss {losses.pnl_pct.mean():.1f}%")
    print(f"  Expectancy {df.pnl_pct.mean():+.1f}%/trade (±{1.96*se:.1f}% 95% CI) | "
          f"${df.pnl_usd.mean():+.2f}/trade | PF {wins.pnl_usd.sum()/gl:.2f}" if gl else "  PF inf")
    print(f"  Exits: {df['exit'].value_counts().to_dict()}")
    yr = df.groupby("year").agg(n=("pnl_pct", "size"), exp_pct=("pnl_pct", "mean"),
                                win=("pnl_pct", lambda s: (s > 0).mean() * 100)).round(1)
    print("  By year: " + "; ".join(f"{y}: n={r.n} exp={r.exp_pct:+.1f}% win={r.win:.0f}%"
                                     for y, r in yr.iterrows()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("years", nargs="?", type=int, default=3)
    ap.add_argument("--ideal", action="store_true")
    ap.add_argument("--haircut", type=float)
    ap.add_argument("--iv-mult", type=float, default=1.0)
    ap.add_argument("--no-live-filters", action="store_true")
    ap.add_argument("--no-premium-window", action="store_true")
    ap.add_argument("--out", default="backtest_trades.csv")
    args = ap.parse_args()

    px = load_prices(args.years)
    buy_px, sell_px = make_fill(args)
    all_trades = []
    for t in TICKERS:
        try:
            all_trades += backtest_ticker(t, px, args, buy_px, sell_px)
        except Exception as e:
            print(f"  {t:6s} error: {e}")
    if not all_trades:
        print("No trades generated.")
        return
    df = pd.DataFrame(all_trades)
    df.to_csv(args.out, index=False)
    summarize(df, " ".join(sys.argv[1:]) or "realistic (default)")
    tk = df.groupby("ticker").agg(n=("pnl_pct", "size"), usd=("pnl_usd", "sum")).sort_values("n")
    print("  Most-traded tickers:", tk.tail(6).n.to_dict())


if __name__ == "__main__":
    main()
