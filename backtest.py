"""
Backtest for the 52-Week High Momentum Options Screener (screener.py)

Simulates the screener's actual trade: when a stock is within 5% of its
52-week high on >= average volume, buy a ~3-week ATM call, exit at
+90% (target), -40% (stop), or expiry.

Option prices are simulated with Black-Scholes using 20-day realized vol.
NOTE: real ATM calls usually cost MORE than realized-vol BS prices
(IV premium), so real-world results would likely be WORSE than this.
Earnings blackout is not simulated (no reliable historical earnings dates).

Usage: python3 backtest.py [years]   (default 3)
"""

import sys
import math
import numpy as np
import pandas as pd
import yfinance as yf

# ── Match screener.py config ───────────────────────────────────────────────
MAX_DIST_FROM_HIGH = 0.05
MIN_VOL_RATIO      = 0.8         # updated Aug 17 — screener uses 0.8x
HOLD_DAYS_CAL      = 21          # ~3 weeks to expiry
TARGET_MULT        = 1.80        # +80% premium target (updated Aug 17)
STOP_MULT          = 0.60        # -40% premium stop
RISK_FREE          = 0.04
PER_TRADE          = 150         # dollars per trade (updated Aug 17 — $500 account)

TICKERS = [
    # AI / Momentum
    "SOUN", "PLTR", "CRWD", "SNOW",
    # Mega-cap Tech
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    # Software / Cloud
    "AMD", "ORCL", "ADBE", "CRM", "NOW",
    # Communication
    "NFLX",
    # Financials
    "V", "MA", "JPM", "AXP", "GS", "MS", "BAC", "BLK", "SCHW",
    # Industrials
    "CAT", "DE", "HON", "GE", "ETN", "EMR", "UNP", "FDX", "MMM",
    # Defense
    "RTX", "LMT", "NOC", "BA", "GD",
    # Healthcare
    "LLY", "ABT", "DHR", "JNJ", "UNH", "MRK", "PFE",
    # Consumer
    "COST", "HD", "NKE", "MCD",
    # Energy
    "SLB", "MPC", "XOM", "CVX", "OXY", "HAL",
]


def bs_call(S, K, T, sigma, r=RISK_FREE):
    """Black-Scholes call price. T in years."""
    if T <= 0:
        return max(S - K, 0.0)
    if sigma <= 0:
        return max(S - K * math.exp(-r * T), 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    from math import erf
    N = lambda x: 0.5 * (1 + erf(x / math.sqrt(2)))
    return S * N(d1) - K * math.exp(-r * T) * N(d2)


def backtest_ticker(ticker, years):
    period = f"{years + 1}y"  # +1y for the 52w-high lookback
    hist = yf.Ticker(ticker).history(period=period, auto_adjust=True)
    if hist.empty or len(hist) < 300:
        return []

    close = hist["Close"].values
    high  = hist["High"].values
    vol   = hist["Volume"].values.astype(float)
    dates = hist.index

    logret = np.diff(np.log(close), prepend=np.nan)
    trades = []
    open_until = -1  # index until which we're in a trade (no overlapping entries per ticker)

    for i in range(252, len(close) - 1):
        if i <= open_until:
            continue
        high_52w = high[i - 251:i + 1].max()
        dist = (high_52w - close[i]) / high_52w
        if not (0 <= dist <= MAX_DIST_FROM_HIGH):
            continue
        v20 = vol[i - 19:i + 1].mean()
        if v20 <= 0 or vol[i] / v20 < MIN_VOL_RATIO:
            continue
        sig = np.nanstd(logret[i - 19:i + 1]) * math.sqrt(252)
        if not np.isfinite(sig) or sig <= 0:
            continue

        # Enter: ATM call, HOLD_DAYS_CAL calendar days ≈ 15 trading days
        S0, K = close[i], close[i]
        T0 = HOLD_DAYS_CAL / 365.0
        entry = bs_call(S0, K, T0, sig)
        if entry <= 0.01:
            continue

        exit_px, exit_reason, exit_j = None, "expiry", None
        n_td = 15  # trading days to expiry
        for j in range(1, n_td + 1):
            if i + j >= len(close):
                break
            T = max(T0 - j * (HOLD_DAYS_CAL / n_td) / 365.0, 0.0)
            px = bs_call(close[i + j], K, T, sig)
            if px >= entry * TARGET_MULT:
                exit_px, exit_reason, exit_j = entry * TARGET_MULT, "target", j
                break
            if px <= entry * STOP_MULT:
                exit_px, exit_reason, exit_j = entry * STOP_MULT, "stop", j
                break
        if exit_px is None:
            j = min(n_td, len(close) - 1 - i)
            exit_px, exit_j = max(close[i + j] - K, 0.0), j

        pnl_pct = (exit_px - entry) / entry
        trades.append({
            "ticker": ticker,
            "date": dates[i].date(),
            "year": dates[i].year,
            "sigma": round(sig, 3),
            "entry_prem": round(entry, 2),
            "exit_prem": round(exit_px, 2),
            "exit": exit_reason,
            "days_held": exit_j,
            "pnl_pct": round(pnl_pct * 100, 1),
            "pnl_usd": round(pnl_pct * PER_TRADE, 2),
        })
        open_until = i + (exit_j or n_td)

    return trades


def main():
    years = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"Backtesting {len(TICKERS)} tickers over ~{years} years "
          f"(entry: ≤{MAX_DIST_FROM_HIGH:.0%} from 52w high, vol≥{MIN_VOL_RATIO}x; "
          f"ATM call {HOLD_DAYS_CAL}d, +90%/-40% exits)\n")

    all_trades = []
    for t in TICKERS:
        try:
            tr = backtest_ticker(t, years)
            all_trades += tr
            print(f"  {t:6s} {len(tr):3d} trades")
        except Exception as e:
            print(f"  {t:6s} error: {e}")

    if not all_trades:
        print("No trades generated.")
        return

    df = pd.DataFrame(all_trades)
    df.to_csv("backtest_trades.csv", index=False)

    wins = df[df.pnl_pct > 0]
    losses = df[df.pnl_pct <= 0]
    total_pnl = df.pnl_usd.sum()
    gross_win = wins.pnl_usd.sum()
    gross_loss = abs(losses.pnl_usd.sum())

    print("\n" + "=" * 62)
    print("  RESULTS  (simulated, $%d risked per trade)" % PER_TRADE)
    print("=" * 62)
    print(f"  Trades:          {len(df)}")
    print(f"  Win rate:        {len(wins)/len(df)*100:.1f}%   (need ~31% at +90/-40 to break even)")
    print(f"  Avg win:         {wins.pnl_pct.mean():.1f}%   Avg loss: {losses.pnl_pct.mean():.1f}%")
    print(f"  Expectancy:      {df.pnl_pct.mean():+.1f}% per trade  (${df.pnl_usd.mean():+.2f} on $250)")
    print(f"  Total P&L:       ${total_pnl:+,.0f}  over {len(df)} trades")
    print(f"  Profit factor:   {gross_win/gross_loss:.2f}" if gross_loss else "  Profit factor:   inf")
    print(f"\n  Exits: {df['exit'].value_counts().to_dict()}")
    print("\n  By year:")
    yr = df.groupby("year").agg(trades=("pnl_usd", "size"), pnl_usd=("pnl_usd", "sum"),
                                win_rate=("pnl_pct", lambda s: (s > 0).mean() * 100)).round(1)
    print(yr.to_string())
    print("\n  Best/worst tickers by total P&L:")
    tk = df.groupby("ticker").pnl_usd.sum().sort_values()
    print("   worst:", tk.head(4).round(0).to_dict())
    print("   best: ", tk.tail(4).round(0).to_dict())
    print("\n  ⚠️  Caveats: BS/realized-vol pricing understates real premiums (IV),")
    print("      no bid/ask spread or commissions, no earnings blackout.")
    print("      Real-world results would likely be somewhat worse.")
    print("      Trades saved to backtest_trades.csv")


if __name__ == "__main__":
    main()
