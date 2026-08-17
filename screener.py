"""
52-Week High Momentum Options Screener
Budget: $150/trade | Target premium: $0.20–$1.00/share
Strategy: ATM or slightly OTM calls, 2–3 weeks out

Filters (in order):
  1. Market regime   — SPY above 20-day MA (skip ALL trades if not)
  2. 52w high        — stock within 5% of yearly peak
  3. Volume          — at least 0.8x 20-day average
  4. Relative strength — stock outperforming SPY over last 10 days
  5. Sector ETF      — sector ETF also above its 20-day MA
  6. IV ratio        — skip options priced >1.5x realized vol (too expensive)
  7. Earnings        — no earnings within 14 days of expiry
  8. Oil             — skip energy tickers if WTI < $84
"""

import json
import os
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

# ── Core config ─────────────────────────────────────────────────────────────
MAX_TRADE_BUDGET    = 150
TARGET_PREMIUM_LOW  = 0.20
TARGET_PREMIUM_HIGH = 1.00
MAX_DIST_FROM_HIGH  = 0.05
MAX_OTM_PCT         = 0.03
MIN_EXPIRY_DAYS     = 10
MAX_EXPIRY_DAYS     = 25
OIL_DANGER_LEVEL    = 84.0
OIL_TREND_WARN      = 87.0
MIN_OI              = 200
MIN_VOL_RATIO       = 0.8
MAX_SPREAD_PCT      = 0.15
EARNINGS_BLACKOUT   = 14

# ── Filter 1: Market regime ──────────────────────────────────────────────────
SPY_MA_PERIOD       = 20        # SPY must be above this MA to allow entries

# ── Filter 4: Relative strength ──────────────────────────────────────────────
RS_LOOKBACK         = 10        # stock must outperform SPY over this many days

# ── Filter 5: Sector ETF ─────────────────────────────────────────────────────
SECTOR_MA_PERIOD    = 20        # sector ETF must be above this MA

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

# ── Filter 6: IV ratio ───────────────────────────────────────────────────────
IV_RATIO_MAX        = 1.5       # skip options where IV > 1.5x 20-day realized vol

# ── Energy tickers ───────────────────────────────────────────────────────────
ENERGY_TICKERS = {"SLB", "MPC", "XOM", "CVX", "OXY", "HAL"}

# ── Watchlist ────────────────────────────────────────────────────────────────
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
    # Energy (blocked when WTI < $84)
    "SLB", "MPC", "XOM", "CVX", "OXY", "HAL",
]


# ── Filter 1: Market regime ──────────────────────────────────────────────────

def check_market_regime():
    """Return (is_uptrend, spy_hist). Fail open (return True) if data unavailable."""
    try:
        spy = yf.Ticker("SPY")
        hist = spy.history(period="60d")
        if hist.empty or len(hist) < SPY_MA_PERIOD + RS_LOOKBACK:
            print("  SPY data unavailable — skipping regime filter")
            return True, None
        ma20  = hist["Close"].tail(SPY_MA_PERIOD).mean()
        price = hist["Close"].iloc[-1]
        uptrend = price > ma20
        symbol = "✅" if uptrend else "🔴"
        print(f"  SPY: ${price:.2f} | 20-day MA: ${ma20:.2f} | {symbol} {'UPTREND — entries allowed' if uptrend else 'DOWNTREND — NO ENTRIES TODAY'}")
        return uptrend, hist
    except Exception as e:
        print(f"  SPY check failed ({e}) — proceeding")
        return True, None


# ── Filter 4: Relative strength ──────────────────────────────────────────────

def has_relative_strength(ticker_hist, spy_hist):
    """Return True if stock outperformed SPY over last RS_LOOKBACK days."""
    try:
        if spy_hist is None or len(ticker_hist) < RS_LOOKBACK or len(spy_hist) < RS_LOOKBACK:
            return True  # fail open
        stock_ret = ticker_hist["Close"].iloc[-1] / ticker_hist["Close"].iloc[-RS_LOOKBACK] - 1
        spy_ret   = spy_hist["Close"].iloc[-1]    / spy_hist["Close"].iloc[-RS_LOOKBACK]    - 1
        return stock_ret >= spy_ret
    except Exception:
        return True


# ── Filter 5: Sector ETF ─────────────────────────────────────────────────────

def get_sector_trend(ticker, etf_cache):
    """Return True if the ticker's sector ETF is above its 20-day MA."""
    etf = SECTOR_ETFS.get(ticker)
    if not etf:
        return True  # no mapping — fail open
    if etf in etf_cache:
        return etf_cache[etf]
    try:
        hist = yf.Ticker(etf).history(period="60d")
        if hist.empty or len(hist) < SECTOR_MA_PERIOD:
            etf_cache[etf] = True
            return True
        ma20  = hist["Close"].tail(SECTOR_MA_PERIOD).mean()
        price = hist["Close"].iloc[-1]
        result = price > ma20
        etf_cache[etf] = result
        return result
    except Exception:
        etf_cache[etf] = True
        return True


# ── Oil price ────────────────────────────────────────────────────────────────

def get_wti_price():
    try:
        oil  = yf.Ticker("CL=F")
        hist = oil.history(period="5d")
        if hist.empty:
            return None
        return round(hist["Close"].iloc[-1], 2)
    except Exception:
        return None


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_expiry_window():
    now  = datetime.now()
    low  = now + timedelta(days=MIN_EXPIRY_DAYS)
    high = now + timedelta(days=MAX_EXPIRY_DAYS)
    return low, high


# ── Filter 2+3: 52w high + volume (+ sigma for IV filter) ────────────────────

def screen_stocks(tickers, spy_hist):
    """Return stocks within MAX_DIST_FROM_HIGH of 52w high, with RS check."""
    print(f"\n🔍 Scanning {len(tickers)} tickers for 52-week high proximity...\n")
    candidates = []

    for ticker in tickers:
        try:
            stk  = yf.Ticker(ticker)
            hist = stk.history(period="1y")
            if hist.empty or len(hist) < 50:
                continue

            price    = hist["Close"].iloc[-1]
            high_52w = hist["High"].max()
            dist     = (high_52w - price) / high_52w

            if dist < 0 or dist > MAX_DIST_FROM_HIGH:
                continue

            vol_20d   = hist["Volume"].tail(20).mean()
            vol_now   = hist["Volume"].iloc[-1]
            vol_ratio = vol_now / vol_20d if vol_20d > 0 else 0

            if vol_ratio < MIN_VOL_RATIO:
                continue

            # Filter 4: Relative strength vs SPY
            if not has_relative_strength(hist, spy_hist):
                continue

            # 20-day realized vol for IV ratio filter later
            import math
            import numpy as np
            log_rets = hist["Close"].pct_change().dropna().tail(20)
            sigma    = float(log_rets.std() * math.sqrt(252)) if len(log_rets) >= 10 else 0.0

            candidates.append({
                "ticker":    ticker,
                "price":     round(price, 2),
                "52w_high":  round(high_52w, 2),
                "dist_pct":  round(dist * 100, 2),
                "vol_ratio": round(vol_ratio, 2),
                "sigma":     round(sigma, 3),
            })
        except Exception:
            continue

    df = pd.DataFrame(candidates)
    if df.empty:
        return df
    return df.sort_values("dist_pct").reset_index(drop=True)


# ── Earnings check ───────────────────────────────────────────────────────────

def has_upcoming_earnings(ticker):
    stk = yf.Ticker(ticker)
    try:
        ed = stk.earnings_dates
        if ed is not None and not ed.empty:
            now_utc = pd.Timestamp.now(tz="UTC")
            future  = ed[ed.index > now_utc]
            if not future.empty:
                next_earn  = future.index[0]
                days_until = (next_earn.tz_localize(None) - pd.Timestamp.now()).days
                return 0 <= days_until <= EARNINGS_BLACKOUT
            return False
    except Exception:
        pass
    try:
        cal   = stk.calendar
        if cal:
            dates = cal.get("Earnings Date", [])
            if dates:
                next_earn  = pd.Timestamp(dates[0])
                days_until = (next_earn - pd.Timestamp.now()).days
                return 0 <= days_until <= EARNINGS_BLACKOUT
    except Exception:
        pass
    return None


# ── Filter 6: Options chain + IV ratio ───────────────────────────────────────

def find_calls(ticker, stock_price, realized_vol):
    """Find call options matching budget/expiry. Skips if IV > IV_RATIO_MAX × realized_vol."""
    low_date, high_date = get_expiry_window()
    stk = yf.Ticker(ticker)

    try:
        expirations = stk.options
    except Exception:
        return []

    results = []
    for exp_str in expirations:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d")
        if not (low_date <= exp_date <= high_date):
            continue
        try:
            chain = stk.option_chain(exp_str)
            calls = chain.calls.copy()
        except Exception:
            continue

        calls = calls[calls["strike"] >= stock_price * 0.95]
        calls = calls[calls["strike"] <= stock_price * (1 + MAX_OTM_PCT)]

        has_quote  = (calls["bid"] > 0) & (calls["ask"] > 0)
        mid        = (calls["bid"] + calls["ask"]) / 2
        calls      = calls.copy()
        calls["mid_price"] = mid.where(has_quote, calls["lastPrice"])

        calls = calls[calls["mid_price"] >= TARGET_PREMIUM_LOW]
        calls = calls[calls["mid_price"] <= TARGET_PREMIUM_HIGH]
        calls = calls[calls["mid_price"] * 100 <= MAX_TRADE_BUDGET]
        calls = calls[calls["openInterest"].fillna(0) >= MIN_OI]

        live       = (calls["bid"] > 0) & (calls["ask"] > 0)
        spread_pct = ((calls["ask"] - calls["bid"]) / calls["mid_price"]).where(live, 0)
        calls      = calls[~live | (spread_pct <= MAX_SPREAD_PCT)]

        for _, row in calls.iterrows():
            # Filter 6: IV ratio — skip if options are overpriced vs realized vol
            iv = float(row.get("impliedVolatility") or 0)
            if iv > 0 and realized_vol > 0 and iv > realized_vol * IV_RATIO_MAX:
                continue  # options too expensive relative to historical vol

            contracts = int(MAX_TRADE_BUDGET // (row["mid_price"] * 100))
            results.append({
                "expiry":    exp_str,
                "strike":    row["strike"],
                "premium":   round(row["mid_price"], 2),
                "cost_1x":   round(row["mid_price"] * 100, 2),
                "contracts": max(1, contracts),
                "IV":        round(iv * 100, 1),
                "OI":        int(row.get("openInterest", 0) or 0),
                "volume":    int(row.get("volume", 0) or 0),
                "iv_ratio":  round(iv / realized_vol, 2) if realized_vol > 0 else None,
            })

    return sorted(results, key=lambda x: x["premium"])


# ── Watchlist monitor ────────────────────────────────────────────────────────

DATA_JSON = os.path.join(os.path.dirname(__file__), "web", "data.json")

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
        with open(DATA_JSON) as f:
            data = json.load(f)
    except Exception:
        return

    watchlist = data.get("watchlist", [])
    if not watchlist:
        return

    print("\n── Watchlist Alert ──────────────────────────────────────")
    updated = False

    for item in watchlist:
        ticker      = item["ticker"]
        quote, err  = fetch_quote(ticker)

        if err or quote is None:
            item["signal"]       = "⚠️ No data — check manually"
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

        if   chg_pct <= -5 and vol_ratio >= 1.5:  signal = "🟢 BUY DIP — down 5%+ with high volume."
        elif chg_pct <= -3:                        signal = "🟡 WATCH — mild dip. Wait for volume confirmation."
        elif chg_pct >= 8 and vol_ratio >= 2.0:   signal = "🔥 MOMENTUM — up 8%+ on 2x volume."
        elif chg_pct >= 5:                         signal = "📈 RISING — up 5%+. Watch for pullback entry."
        elif vol_ratio >= 2.0:                     signal = "⚡ VOLUME SPIKE — unusual volume. Monitor."
        else:                                      signal = "😴 QUIET — no signal today."

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
        with open(DATA_JSON, "w") as f:
            json.dump(data, f, indent=2)
    print()


# ── Dashboard writer ─────────────────────────────────────────────────────────

def save_dashboard(opportunities, wti, regime_blocked=False):
    try:
        with open(DATA_JSON) as f:
            data = json.load(f)
    except Exception:
        data = {}

    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if wti is not None:
        data["oil_price"]  = wti
        data["oil_status"] = ("ok" if wti >= OIL_TREND_WARN else "warning" if wti >= OIL_DANGER_LEVEL else "danger")

    data["opportunities"] = opportunities
    today_str = datetime.now().strftime("%b %d")

    if regime_blocked:
        data["briefing"] = {
            "date":          today_str,
            "headline":      "Market downtrend — no entries today.",
            "body":          "SPY is below its 20-day MA. Sitting in cash until market recovers.",
            "action":        "HOLD",
            "action_detail": "Market regime filter active",
        }
    elif opportunities:
        data["briefing"] = {
            "date":          today_str,
            "headline":      f"{len(opportunities)} setup(s) found today.",
            "body":          "Verify live prices before entering any position.",
            "action":        "BUY",
            "action_detail": f"{len(opportunities)} trade idea(s) below",
        }
    else:
        data["briefing"] = {
            "date":          today_str,
            "headline":      "No setups today.",
            "body":          "Sitting in cash is the right move — do not force a trade.",
            "action":        "HOLD",
            "action_detail": None,
        }

    with open(DATA_JSON, "w") as f:
        json.dump(data, f, indent=2)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  52-WEEK HIGH MOMENTUM OPTIONS SCREENER  (v2 — 5 filters)")
    print(f"  Max/trade: ${MAX_TRADE_BUDGET} | Premium: ${TARGET_PREMIUM_LOW}–${TARGET_PREMIUM_HIGH}/sh")
    print(f"  Expiry: {MIN_EXPIRY_DAYS}–{MAX_EXPIRY_DAYS}d | Vol ≥{MIN_VOL_RATIO}x | OI ≥{MIN_OI} | IV ≤{IV_RATIO_MAX}x realized")
    print("=" * 60)

    # ── Filter 1: Market regime ───────────────────────────────────────────────
    print("\n── Filter 1: Market Regime (SPY 20-day MA) ─────────────")
    uptrend, spy_hist = check_market_regime()
    if not uptrend:
        print("\n🔴 HOLD — market in downtrend. No entries today.")
        wti = get_wti_price()
        save_dashboard([], wti, regime_blocked=True)
        check_watchlist()
        return

    # ── Oil check ─────────────────────────────────────────────────────────────
    print("\n── Oil Price Check ──────────────────────────────────────")
    wti = get_wti_price()
    if wti is None:
        print("  WTI crude: unavailable")
    else:
        if   wti >= OIL_TREND_WARN:    status = "✅ OK"
        elif wti >= OIL_DANGER_LEVEL:  status = f"🟡 TRENDING WEAK — caution on energy"
        else:                          status = f"⚠️  BELOW ${OIL_DANGER_LEVEL} — AVOID ENERGY"
        print(f"  WTI Crude: ${wti}  {status}")

    check_watchlist()

    # ── Filters 2+3+4: 52w high + volume + relative strength ─────────────────
    candidates = screen_stocks(TICKERS, spy_hist)

    if candidates.empty:
        print("No candidates after 52w high + volume + RS filters.")
        save_dashboard([], wti)
        return

    # Remove energy tickers if oil weak
    if wti is not None and wti < OIL_DANGER_LEVEL:
        flagged = candidates[candidates["ticker"].isin(ENERGY_TICKERS)]["ticker"].tolist()
        if flagged:
            print(f"⚠️  Dropping energy tickers (WTI ${wti}): {flagged}")
            candidates = candidates[~candidates["ticker"].isin(ENERGY_TICKERS)].reset_index(drop=True)

    print(f"✅ {len(candidates)} stocks passed 52w high + volume + RS filters:\n")
    print(candidates[["ticker", "price", "52w_high", "dist_pct", "vol_ratio"]].to_string(index=False))

    # ── Filters 5+6+7: Sector ETF + IV ratio + Earnings + Options ────────────
    print("\n" + "=" * 60)
    print("  SCANNING OPTIONS (sector ETF + IV ratio + earnings)...")
    print("=" * 60)

    etf_cache = {}
    tradeable = []

    for _, row in candidates.iterrows():
        ticker       = row["ticker"]
        price        = row["price"]
        realized_vol = row["sigma"]

        # Filter 5: Sector ETF trend
        if not get_sector_trend(ticker, etf_cache):
            etf = SECTOR_ETFS.get(ticker, "?")
            print(f"  ⚠️  {ticker}: sector ETF {etf} below 20-day MA — skipping")
            continue

        # Filter 7: Earnings blackout
        earnings_status = has_upcoming_earnings(ticker)
        if earnings_status is True:
            print(f"  ⚠️  {ticker}: earnings within {EARNINGS_BLACKOUT} days — skipping")
            continue
        if earnings_status is None:
            print(f"  ⚠️  {ticker}: earnings date unknown — skipping")
            continue

        # Filter 6: IV ratio applied inside find_calls()
        calls = find_calls(ticker, price, realized_vol)
        if not calls:
            continue

        for c in calls:
            tradeable.append({
                "ticker":    ticker,
                "price":     price,
                "dist%":     row["dist_pct"],
                "vol_ratio": row["vol_ratio"],
                "expiry":    c["expiry"],
                "strike":    c["strike"],
                "premium":   c["premium"],
                "cost_1x":   c["cost_1x"],
                "contracts": c["contracts"],
                "IV%":       c["IV"],
                "OI":        c["OI"],
                "iv_ratio":  c.get("iv_ratio"),
            })

    if not tradeable:
        print("\n❌ No options passed all filters today. Holding cash.")
        save_dashboard([], wti)
        return

    df  = pd.DataFrame(tradeable)
    df  = df.sort_values(["dist%", "premium"]).reset_index(drop=True)
    top3 = df.head(3)

    print(f"\n🎯 {len(df)} tradeable calls found. Top 3:\n")
    for i, row in top3.iterrows():
        contracts  = row["contracts"]
        total_cost = round(row["premium"] * 100 * contracts, 2)
        target     = round(row["premium"] * 1.8, 2)
        stop       = round(row["premium"] * 0.60, 2)
        iv_tag     = f" | IV/HV: {row['iv_ratio']}x" if row.get("iv_ratio") else ""
        print(f"""
#{i+1}  {row['ticker']}  |  ${row['price']} stock  |  {row['dist%']}% from 52w high
    Strike: ${row['strike']}  |  Expiry: {row['expiry']}  |  IV: {row['IV%']}%{iv_tag}
    Entry:  ${row['premium']}/sh  →  {contracts} contract(s) = ${total_cost}
    Target: ${target}/sh  (+80%)    Stop: ${stop}/sh  (-40%)
""")

    opps = []
    for i, row in top3.iterrows():
        opps.append({
            "ticker":    row["ticker"],
            "price":     row["price"],
            "dist_pct":  row["dist%"],
            "strike":    row["strike"],
            "expiry":    row["expiry"],
            "premium":   row["premium"],
            "contracts": int(row["contracts"]),
            "cost":      round(row["premium"] * 100 * int(row["contracts"]), 2),
            "target":    round(row["premium"] * 1.8, 2),
            "stop":      round(row["premium"] * 0.60, 2),
            "iv_pct":    row["IV%"],
        })
    save_dashboard(opps, wti)
    print("⚠️  Not financial advice. Verify prices live on Robinhood before entering.\n")


if __name__ == "__main__":
    main()
