"""
52-Week High Momentum SHARES Screener  v4.0
Runs AFTER the close (GitHub Actions ~5:37 PM ET). Signals are computed on the
completed session; the executor buys at the next morning's 9:45 ET run.

Filters (identical to research/shares_live_sim.py, which tested +4.4%/trade, 3y):
  1. Market regime     -- SPY close above its 20-day MA (skip ALL entries if not)
  2. 52w high          -- close within 5% of the 252-day high
  3. Volume            -- session volume >= 0.8x its 20-day average
  4. Relative strength -- 10-day return above SPY's
  5. Sector ETF        -- sector ETF close above its 20-day MA
Ranked by distance from the 52w high (closest first).

Exits (enforced by the executor, not here): 15% trailing stop off the highest
close since entry, or 60 trading days, whichever comes first.
Sizing: max 3 positions x 30% of equity, fractional shares.

Not tested, so removed from v3.1: VIX gate, oil gate, earnings blackout/bonus,
all option filters (premium, OI, IV, expiry).
"""

import json
import math
import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

# -- Core config ---------------------------------------------------------------
STRATEGY            = "shares_momentum_v4"
MAX_DIST_FROM_HIGH  = 0.05
MIN_VOL_RATIO       = 0.8
SPY_MA_PERIOD       = 20
RS_LOOKBACK         = 10
SECTOR_MA_PERIOD    = 20
HIGH_LOOKBACK       = 252
MAX_OPPORTUNITIES   = 6      # executor needs spares when a top pick is already held
# Rules the executor enforces (published in data.json so both sides agree)
MAX_POSITIONS       = 3
POSITION_FRAC       = 0.30
TRAIL_PCT           = 0.15
MAX_HOLD_DAYS       = 60
DISASTER_STOP_PCT   = 0.20   # broker-side GTC stop, below the 15% software trail

ET = ZoneInfo("America/New_York")

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

# -- Filter 7: IV ratio --------------------------------------------------------
IV_RATIO_MAX        = 1.5

# -- Energy tickers ------------------------------------------------------------
ENERGY_TICKERS = {"SLB", "MPC", "XOM", "CVX", "OXY", "HAL"}

# -- Watchlist -----------------------------------------------------------------
TICKERS = [
    "SOUN", "PLTR", "CRWD", "SNOW",
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "AMD", "ORCL", "ADBE", "CRM", "NOW",
    "NFLX",
    "V", "MA", "JPM", "AXP", "GS", "MS", "BAC", "BLK", "SCHW",
    "CAT", "DE", "HON", "GE", "ETN", "EMR", "UNP", "FDX", "MMM",
    "RTX", "LMT", "NOC", "BA", "GD",
    "LLY", "ABT", "DHR", "JNJ", "UNH", "MRK", "PFE",
    "COST", "HD", "NKE", "MCD",
    "SLB", "MPC", "XOM", "CVX", "OXY", "HAL",
]



# -- Daily bars (completed sessions only) --------------------------------------

def daily(symbol, period="2y"):
    """Daily bars with today's row dropped if the session isn't finished yet,
    so a late or manual run never screens on a partial intraday bar."""
    hist = yf.Ticker(symbol).history(period=period, auto_adjust=True)
    if hist.empty:
        return hist
    now = datetime.now(ET)
    if hist.index[-1].date() == now.date() and now.time() < time(16, 15):
        hist = hist.iloc[:-1]
    return hist


# -- Filter 1: Market regime ---------------------------------------------------

def check_market_regime():
    """Return (is_uptrend, spy_hist). Fails CLOSED -- no SPY data, no entries."""
    try:
        hist = daily("SPY", "1y")
        if len(hist) < SPY_MA_PERIOD + RS_LOOKBACK + 1:
            print("  SPY data unavailable -- no entries (fail-closed)")
            return False, None
        ma20    = hist["Close"].tail(SPY_MA_PERIOD).mean()
        price   = hist["Close"].iloc[-1]
        uptrend = price > ma20
        print(f"  SPY: ${price:.2f} | 20-day MA: ${ma20:.2f} | {'OK' if uptrend else 'DOWNTREND -- NO ENTRIES'}")
        return uptrend, hist
    except Exception as e:
        print(f"  SPY check failed ({e}) -- no entries (fail-closed)")
        return False, None


# -- Filter 4: Relative strength -----------------------------------------------

def rs_delta(ticker_hist, spy_hist):
    """10-day return minus SPY's (same as pct_change(10) in the backtest)."""
    n = RS_LOOKBACK
    stock = ticker_hist["Close"].iloc[-1] / ticker_hist["Close"].iloc[-n - 1] - 1
    spy   = spy_hist["Close"].iloc[-1] / spy_hist["Close"].iloc[-n - 1] - 1
    return round(float(stock - spy), 4)


# -- Filter 5: Sector ETF ------------------------------------------------------

def get_sector_trend(ticker, etf_cache):
    """True if the sector ETF closes above its 20-day MA. Fail-closed."""
    etf = SECTOR_ETFS.get(ticker)
    if not etf:
        return False
    if etf not in etf_cache:
        try:
            hist = daily(etf, "6mo")
            etf_cache[etf] = (len(hist) >= SECTOR_MA_PERIOD and
                              hist["Close"].iloc[-1] > hist["Close"].tail(SECTOR_MA_PERIOD).mean())
        except Exception:
            etf_cache[etf] = False
    return bool(etf_cache[etf])


# -- Filters 2-5 ---------------------------------------------------------------

def screen_stocks(tickers, spy_hist):
    print(f"\nScanning {len(tickers)} tickers...\n")
    etf_cache, rows = {}, []
    signal_date = spy_hist.index[-1].date()
    for ticker in tickers:
        try:
            hist = daily(ticker)
            if len(hist) < HIGH_LOOKBACK or hist.index[-1].date() != signal_date:
                continue
            price    = float(hist["Close"].iloc[-1])
            high_52w = float(hist["High"].tail(HIGH_LOOKBACK).max())
            dist     = (high_52w - price) / high_52w
            if dist < 0 or dist > MAX_DIST_FROM_HIGH:
                continue
            vol_ratio = float(hist["Volume"].iloc[-1] / hist["Volume"].tail(20).mean())
            if vol_ratio < MIN_VOL_RATIO:
                continue
            rs = rs_delta(hist, spy_hist)
            if rs <= 0:
                continue
            if not get_sector_trend(ticker, etf_cache):
                print(f"  {ticker}: sector ETF {SECTOR_ETFS.get(ticker)} below 20-day MA -- skipping")
                continue
            rows.append({
                "ticker":    ticker,
                "price":     round(price, 2),
                "52w_high":  round(high_52w, 2),
                "dist_pct":  round(dist * 100, 2),
                "vol_ratio": round(vol_ratio, 2),
                "rs_delta":  rs,
                "score":     round(100 * (1 - dist / MAX_DIST_FROM_HIGH), 1),
            })
        except Exception as e:
            print(f"  {ticker}: data error ({e}) -- skipping")
    return pd.DataFrame(rows).sort_values("dist_pct").reset_index(drop=True) if rows else pd.DataFrame()

# -- Watchlist monitor ---------------------------------------------------------

DATA_JSON = os.path.join(os.path.dirname(__file__), "web", "data.json")
# SCREENER_OUT redirects output (e.g. the desktop routine writes web/data.local.json)
# so local runs never dirty the git-tracked data.json. Input is still read from
# data.json until this run has written its own output.
OUT_JSON = os.environ.get("SCREENER_OUT") or DATA_JSON
_out_written = False


def load_data():
    with open(OUT_JSON if _out_written else DATA_JSON) as f:
        return json.load(f)


def write_data(data):
    global _out_written
    with open(OUT_JSON, "w") as f:
        json.dump(data, f, indent=2)
    _out_written = True


def fetch_quote(ticker, timeout=8):
    import requests
    try:
        url    = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"interval": "1d", "range": "5d"}
        r      = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        r.raise_for_status()
        j       = r.json()
        result  = j["chart"]["result"][0]
        closes  = [c for c in result["indicators"]["quote"][0]["close"]  if c is not None]
        volumes = [v for v in result["indicators"]["quote"][0]["volume"] if v is not None]
        if len(closes) < 2:
            return None, "not enough data"
        return {"closes": closes, "volumes": volumes}, None
    except Exception as e:
        return None, str(e)


def check_watchlist():
    try:
        data = load_data()
    except Exception:
        return

    watchlist = data.get("watchlist", [])
    if not watchlist:
        return

    print("\n-- Watchlist Alert ------------------------------------------")
    updated = False

    for item in watchlist:
        ticker     = item["ticker"]
        quote, err = fetch_quote(ticker)

        if err or quote is None:
            item["signal"]       = "No data -- check manually"
            item["last_checked"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            updated = True
            continue

        closes     = quote["closes"]
        volumes    = quote["volumes"]
        price      = round(closes[-1], 2)
        prev_close = round(closes[-2], 2)
        chg_pct    = round((price - prev_close) / prev_close * 100, 2)
        vol_today  = volumes[-1] if volumes else 0
        vol_avg    = sum(volumes) / len(volumes) if volumes else 0
        vol_ratio  = round(vol_today / vol_avg, 2) if vol_avg > 0 else 0
        chg_str    = f"+{chg_pct}%" if chg_pct >= 0 else f"{chg_pct}%"

        if   chg_pct <= -5 and vol_ratio >= 1.5: signal = "BUY DIP -- down 5%+ with high volume."
        elif chg_pct <= -3:                       signal = "WATCH -- mild dip. Wait for volume confirmation."
        elif chg_pct >= 8 and vol_ratio >= 2.0:  signal = "MOMENTUM -- up 8%+ on 2x volume."
        elif chg_pct >= 5:                        signal = "RISING -- up 5%+. Watch for pullback entry."
        elif vol_ratio >= 2.0:                    signal = "VOLUME SPIKE -- unusual volume. Monitor."
        else:                                     signal = "QUIET -- no signal today."

        print(f"\n  {ticker} ({item.get('name', '')})")
        print(f"    Price: ${price}  |  Change: {chg_str}  |  Vol ratio: {vol_ratio}x")
        print(f"    Signal: {signal}")

        item["current_price"]    = price
        item["price_change_pct"] = chg_pct
        item["vol_ratio_today"]  = vol_ratio
        item["last_checked"]     = datetime.now().strftime("%Y-%m-%d %H:%M")
        item["signal"]           = signal
        updated = True

    if updated:
        data["watchlist"] = watchlist
        write_data(data)
    print()



# -- Dashboard writer ----------------------------------------------------------

def save_dashboard(opportunities, signal_date, regime_blocked=False):
    try:
        data = load_data()
    except Exception:
        data = {}

    data["last_updated"] = datetime.now(ET).strftime("%Y-%m-%dT%H:%M%z")
    data["strategy"] = {
        "name": STRATEGY, "max_positions": MAX_POSITIONS, "position_frac": POSITION_FRAC,
        "trail_pct": TRAIL_PCT, "max_hold_days": MAX_HOLD_DAYS, "disaster_stop_pct": DISASTER_STOP_PCT,
    }
    data["opportunities"] = opportunities
    sd = signal_date.isoformat() if signal_date else None
    today_str = signal_date.strftime("%b %d") if signal_date else datetime.now(ET).strftime("%b %d")

    if regime_blocked:
        data["briefing"] = {"date": today_str, "signal_date": sd,
                            "headline": "Market downtrend -- no entries.",
                            "body": "SPY closed below its 20-day MA. Open positions keep their trailing stops.",
                            "action": "HOLD", "action_detail": "Market regime filter"}
    elif opportunities:
        top = opportunities[0]
        data["briefing"] = {"date": today_str, "signal_date": sd,
                            "headline": f"{len(opportunities)} setup(s). Best: {top['ticker']} ({top['dist_pct']}% from 52w high)",
                            "body": "Executor buys at the next 9:45 ET run: fractional shares, 30% of equity each, max 3.",
                            "action": "BUY", "action_detail": f"{len(opportunities)} share setup(s)"}
    else:
        data["briefing"] = {"date": today_str, "signal_date": sd,
                            "headline": "No setups today.",
                            "body": "Sitting in cash is the right move -- do not force a trade.",
                            "action": "HOLD", "action_detail": None}

    write_data(data)


# -- Main ----------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  52-WEEK HIGH MOMENTUM SHARES SCREENER  (v4.0)")
    print(f"  Max {MAX_POSITIONS} x {POSITION_FRAC:.0%} | trail {TRAIL_PCT:.0%} | max hold {MAX_HOLD_DAYS}d")
    print("=" * 60)

    print("\n-- Filter 1: Market Regime (SPY 20-day MA) -----------------")
    uptrend, spy_hist = check_market_regime()
    signal_date = spy_hist.index[-1].date() if spy_hist is not None else None
    print(f"  Signal date (last completed session): {signal_date}")
    check_watchlist()
    if not uptrend:
        save_dashboard([], signal_date, regime_blocked=True)
        return

    candidates = screen_stocks(TICKERS, spy_hist)
    if candidates.empty:
        print("No candidates passed all filters.")
        save_dashboard([], signal_date)
        return

    print(f"\n{len(candidates)} stocks passed all filters:\n")
    print(candidates.to_string(index=False))

    opps = []
    for _, row in candidates.head(MAX_OPPORTUNITIES).iterrows():
        opps.append({
            "ticker":    row["ticker"],
            "price":     row["price"],
            "52w_high":  row["52w_high"],
            "dist_pct":  row["dist_pct"],
            "vol_ratio": row["vol_ratio"],
            "rs_delta":  row["rs_delta"],
            "score":     row["score"],
            "stop":      round(row["price"] * (1 - TRAIL_PCT), 2),  # initial trail level
        })
    save_dashboard(opps, signal_date)
    print("Not financial advice.\n")


if __name__ == "__main__":
    main()
