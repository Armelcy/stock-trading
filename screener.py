"""
52-Week High Momentum Options Screener  v3.1
Budget: $85/trade | Target premium: $0.20-$1.00/share
Strategy: ATM or slightly OTM calls, 2-3 weeks out.
          Primary filter: 52-week high momentum (price + volume + RS + sector).
          Earnings bonus: if a passing candidate also has earnings in 15-25 days
          with a strong beat rate, its conviction score is boosted (not required).

Filters (in order):
  1. Market regime   -- SPY above 20-day MA (skip ALL trades if not)
  2. VIX gate        -- block if VIX > 35; tighten IV limit if VIX 25-35
  3. 52w high        -- stock within 5% of yearly peak
  4. Volume          -- yesterday's completed vol >= 0.8x 20-day average
  5. Relative strength -- stock outperforming SPY over last 10 days
  6. Sector ETF      -- sector ETF also above its 20-day MA
  7. IV ratio        -- skip options priced >1.5x realized vol (tighter in high-VIX)
  8. Earnings        -- skip if earnings within 14 days of expiry
  9. Oil             -- skip energy tickers if WTI < $84

Conviction score (v3.1):
  34% -- 52w proximity | 21% -- volume | 17% -- RS delta | 13% -- ATM proximity
  15% -- earnings bonus (0 if no earnings in 15-25d window or beat rate < 75%)
"""

import json
import math
import os

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

# -- Core config ---------------------------------------------------------------
MAX_TRADE_BUDGET    = 85       # raise to $150 once balance > $600
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
EARNINGS_BLACKOUT   = 14      # skip if earnings within this many days of expiry

# -- Earnings bonus (optional boost to conviction score) -----------------------
EARNINGS_BONUS_MIN  = 15      # earnings must be at least this far away
EARNINGS_BONUS_MAX  = 25      # earnings must be within this many days
MIN_BEAT_RATE       = 0.75    # minimum beat rate to earn any bonus
MIN_BONUS_QUARTERS  = 2       # minimum quarters of data needed

# -- Filter 1: Market regime ---------------------------------------------------
SPY_MA_PERIOD       = 20

# -- Filter 2: VIX gate --------------------------------------------------------
VIX_WARN            = 25
VIX_BLOCK           = 35
VIX_IV_RATIO_TIGHT  = 1.2

# -- Filter 5: Relative strength -----------------------------------------------
RS_LOOKBACK         = 10

# -- Filter 6: Sector ETF ------------------------------------------------------
SECTOR_MA_PERIOD    = 20

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


# -- Filter 1: Market regime ---------------------------------------------------

def check_market_regime():
    """Return (is_uptrend, spy_hist). Fails open -- data error should not block all trades."""
    try:
        spy  = yf.Ticker("SPY")
        hist = spy.history(period="60d")
        if hist.empty or len(hist) < SPY_MA_PERIOD + RS_LOOKBACK:
            print("  SPY data unavailable -- skipping regime filter (fail-open)")
            return True, None
        ma20    = hist["Close"].tail(SPY_MA_PERIOD).mean()
        price   = hist["Close"].iloc[-1]
        uptrend = price > ma20
        symbol  = "OK" if uptrend else "DOWNTREND"
        print(f"  SPY: ${price:.2f} | 20-day MA: ${ma20:.2f} | {symbol} {'-- entries allowed' if uptrend else '-- NO ENTRIES TODAY'}")
        return uptrend, hist
    except Exception as e:
        print(f"  SPY check failed ({e}) -- proceeding (fail-open)")
        return True, None


# -- Filter 2: VIX gate --------------------------------------------------------

def check_vix():
    """Return (vix_level, entries_allowed, iv_limit). Fails open on data error."""
    try:
        hist = yf.Ticker("^VIX").history(period="5d")
        if hist.empty:
            print("  VIX data unavailable -- skipping VIX gate")
            return None, True, IV_RATIO_MAX
        vix = round(float(hist["Close"].iloc[-1]), 1)
        if vix > VIX_BLOCK:
            print(f"  VIX: {vix} -- BLOCKED (>{VIX_BLOCK}). Options too expensive. Sitting in cash.")
            return vix, False, IV_RATIO_MAX
        elif vix > VIX_WARN:
            print(f"  VIX: {vix} -- ELEVATED (>{VIX_WARN}). Tightening IV limit to {VIX_IV_RATIO_TIGHT}x.")
            return vix, True, VIX_IV_RATIO_TIGHT
        else:
            print(f"  VIX: {vix} -- normal. IV limit: {IV_RATIO_MAX}x.")
            return vix, True, IV_RATIO_MAX
    except Exception as e:
        print(f"  VIX check failed ({e}) -- skipping (fail-open)")
        return None, True, IV_RATIO_MAX


# -- Filter 5: Relative strength -----------------------------------------------

def relative_strength_delta(ticker_hist, spy_hist):
    """Return (passes, rs_delta). Fail-closed per ticker, fail-open if spy_hist missing."""
    try:
        if spy_hist is None:
            return None, 0.0
        if len(ticker_hist) < RS_LOOKBACK or len(spy_hist) < RS_LOOKBACK:
            return False, 0.0
        stock_ret = ticker_hist["Close"].iloc[-1] / ticker_hist["Close"].iloc[-RS_LOOKBACK] - 1
        spy_ret   = spy_hist["Close"].iloc[-1]    / spy_hist["Close"].iloc[-RS_LOOKBACK]    - 1
        delta     = round(float(stock_ret - spy_ret), 4)
        return stock_ret >= spy_ret, delta
    except Exception:
        return False, 0.0


# -- Filter 6: Sector ETF ------------------------------------------------------

def get_sector_trend(ticker, etf_cache):
    """Return True if the ticker's sector ETF is above its 20-day MA. Fail-closed."""
    etf = SECTOR_ETFS.get(ticker)
    if not etf:
        return True
    if etf in etf_cache:
        return etf_cache[etf]
    try:
        hist = yf.Ticker(etf).history(period="60d")
        if hist.empty or len(hist) < SECTOR_MA_PERIOD:
            etf_cache[etf] = False
            return False
        ma20   = hist["Close"].tail(SECTOR_MA_PERIOD).mean()
        price  = hist["Close"].iloc[-1]
        result = price > ma20
        etf_cache[etf] = result
        return result
    except Exception:
        etf_cache[etf] = False
        return False


# -- Earnings bonus (optional -- never blocks a trade) -------------------------

def get_next_earnings(ticker):
    """Return (earnings_date as datetime, days_until) or (None, None)."""
    stk = yf.Ticker(ticker)
    try:
        ed = stk.earnings_dates
        if ed is not None and not ed.empty:
            now_utc = pd.Timestamp.now(tz="UTC")
            future  = ed[ed.index > now_utc]
            if not future.empty:
                next_earn  = future.index[0]
                dt         = next_earn.tz_localize(None).to_pydatetime()
                days_until = (dt - datetime.now()).days
                return dt, days_until
    except Exception:
        pass
    try:
        cal = stk.calendar
        if cal:
            dates = cal.get("Earnings Date", [])
            if dates:
                dt         = pd.Timestamp(dates[0]).to_pydatetime()
                days_until = (dt - datetime.now()).days
                return dt, days_until
    except Exception:
        pass
    return None, None


def get_earnings_beat_rate(ticker):
    """Return (beat_rate 0.0-1.0, quarters_checked) or (None, 0) if data unavailable."""
    stk = yf.Ticker(ticker)
    try:
        hist = stk.earnings_history
        if hist is None or hist.empty:
            return None, 0
        cols       = {c.lower().replace(" ", "_"): c for c in hist.columns}
        est_col    = cols.get("epsestimate") or cols.get("eps_estimate")
        actual_col = cols.get("epsactual")   or cols.get("eps_actual") or cols.get("reported_eps")
        if not est_col or not actual_col:
            return None, 0
        recent    = hist[[est_col, actual_col]].dropna().tail(4)
        if len(recent) < MIN_BONUS_QUARTERS:
            return None, len(recent)
        beats     = (recent[actual_col] > recent[est_col]).sum()
        return round(float(beats / len(recent)), 3), len(recent)
    except Exception:
        return None, 0


def get_earnings_bonus(ticker):
    """Return 0.0-1.0 conviction bonus. Non-zero only if earnings in window + beat rate >= MIN_BEAT_RATE.
    Always returns 0.0 on any error -- never blocks or penalizes a trade."""
    try:
        _, days = get_next_earnings(ticker)
        if days is None or not (EARNINGS_BONUS_MIN <= days <= EARNINGS_BONUS_MAX):
            return 0.0
        beat_rate, quarters = get_earnings_beat_rate(ticker)
        if beat_rate is None or quarters < MIN_BONUS_QUARTERS or beat_rate < MIN_BEAT_RATE:
            return 0.0
        return round(float(beat_rate), 3)
    except Exception:
        return 0.0


# -- Conviction score (v3.1) ---------------------------------------------------

def conviction_score(dist_pct, vol_ratio, rs_delta, otm_pct, earnings_bonus=0.0):
    """Score a setup from 0.0 to 1.0. Higher = better quality entry.

    Weights (v3.1):
      34% -- 52w proximity (0% from high = 1.0, 5% = 0.0)
      21% -- volume strength (0.8x = 0.0, 3x+ = 1.0)
      17% -- RS delta vs SPY (capped at +-10%)
      13% -- strike closeness to ATM (0% OTM = 1.0, 3% OTM = 0.0)
      15% -- earnings bonus (0 if no earnings in 15-25d or beat rate < 75%)
    """
    dist_score  = max(0.0, 1.0 - dist_pct / (MAX_DIST_FROM_HIGH * 100))
    vol_score   = min(1.0, max(0.0, (vol_ratio - MIN_VOL_RATIO) / (3.0 - MIN_VOL_RATIO)))
    rs_score    = min(1.0, max(0.0, (rs_delta + 0.10) / 0.20))
    atm_score   = max(0.0, 1.0 - otm_pct / MAX_OTM_PCT)

    return round(
        0.34 * dist_score   +
        0.21 * vol_score    +
        0.17 * rs_score     +
        0.13 * atm_score    +
        0.15 * earnings_bonus,
        3,
    )


# -- Oil price -----------------------------------------------------------------

def get_wti_price():
    try:
        hist = yf.Ticker("CL=F").history(period="5d")
        if hist.empty:
            return None
        return round(float(hist["Close"].iloc[-1]), 2)
    except Exception:
        return None


# -- Helpers -------------------------------------------------------------------

def get_expiry_window():
    now  = datetime.now()
    low  = now + timedelta(days=MIN_EXPIRY_DAYS)
    high = now + timedelta(days=MAX_EXPIRY_DAYS)
    return low, high


# -- Filters 3+4+5: 52w high + volume + RS ------------------------------------

def screen_stocks(tickers, spy_hist):
    """Return candidates passing 52w high, volume, and RS filters."""
    print(f"\nScanning {len(tickers)} tickers for 52-week high proximity...\n")
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

            vol_20d   = hist["Volume"].tail(21).iloc[:-1].mean()
            vol_prev  = hist["Volume"].iloc[-2]
            vol_ratio = vol_prev / vol_20d if vol_20d > 0 else 0.0

            if vol_ratio < MIN_VOL_RATIO:
                continue

            rs_passes, rs_delta = relative_strength_delta(hist, spy_hist)
            if rs_passes is False:
                continue

            log_rets = hist["Close"].pct_change().dropna().tail(20)
            sigma    = float(log_rets.std() * math.sqrt(252)) if len(log_rets) >= 10 else 0.0

            candidates.append({
                "ticker":    ticker,
                "price":     round(price, 2),
                "52w_high":  round(high_52w, 2),
                "dist_pct":  round(dist * 100, 2),
                "vol_ratio": round(vol_ratio, 2),
                "sigma":     round(sigma, 3),
                "rs_delta":  rs_delta if rs_delta is not None else 0.0,
            })
        except Exception:
            continue

    if not candidates:
        return pd.DataFrame()

    return pd.DataFrame(candidates).sort_values("dist_pct").reset_index(drop=True)


# -- Filter 8: Earnings blackout -----------------------------------------------

def has_upcoming_earnings(ticker):
    """Return True if earnings within EARNINGS_BLACKOUT days, False if not, None if unknown."""
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
        cal = stk.calendar
        if cal:
            dates = cal.get("Earnings Date", [])
            if dates:
                next_earn  = pd.Timestamp(dates[0])
                days_until = (next_earn - pd.Timestamp.now()).days
                return 0 <= days_until <= EARNINGS_BLACKOUT
    except Exception:
        pass
    return None


# -- Filter 7: Options chain + IV ratio ----------------------------------------

def find_calls(ticker, stock_price, realized_vol, rs_delta, iv_limit):
    """Find call options matching budget/expiry/IV constraints."""
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

        calls = calls[calls["strike"] >= stock_price * 0.97]
        calls = calls[calls["strike"] <= stock_price * (1 + MAX_OTM_PCT)]

        has_quote          = (calls["bid"] > 0) & (calls["ask"] > 0)
        mid                = (calls["bid"] + calls["ask"]) / 2
        calls              = calls.copy()
        calls["mid_price"] = mid.where(has_quote, calls["lastPrice"])

        calls = calls[calls["mid_price"] >= TARGET_PREMIUM_LOW]
        calls = calls[calls["mid_price"] <= TARGET_PREMIUM_HIGH]
        calls = calls[calls["mid_price"] * 100 <= MAX_TRADE_BUDGET]
        calls = calls[calls["openInterest"].fillna(0) >= MIN_OI]

        live       = (calls["bid"] > 0) & (calls["ask"] > 0)
        spread_pct = ((calls["ask"] - calls["bid"]) / calls["mid_price"]).where(live, 0)
        calls      = calls[~live | (spread_pct <= MAX_SPREAD_PCT)]

        for _, row in calls.iterrows():
            iv = float(row.get("impliedVolatility") or 0)
            if iv > 0 and realized_vol > 0 and iv > realized_vol * iv_limit:
                continue

            otm_pct   = max(0.0, (row["strike"] - stock_price) / stock_price)
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
                "otm_pct":   round(otm_pct * 100, 2),
            })

    return sorted(results, key=lambda x: x["premium"])


# -- Watchlist monitor ---------------------------------------------------------

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
        with open(DATA_JSON, "w") as f:
            json.dump(data, f, indent=2)
    print()


# -- Dashboard writer ----------------------------------------------------------

def save_dashboard(opportunities, wti, vix=None, regime_blocked=False, vix_blocked=False):
    try:
        with open(DATA_JSON) as f:
            data = json.load(f)
    except Exception:
        data = {}

    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if wti is not None:
        data["oil_price"]  = wti
        data["oil_status"] = ("ok" if wti >= OIL_TREND_WARN else "warning" if wti >= OIL_DANGER_LEVEL else "danger")
    if vix is not None:
        data["vix"] = vix

    data["opportunities"] = opportunities
    today_str = datetime.now().strftime("%b %d")

    if regime_blocked:
        data["briefing"] = {"date": today_str, "headline": "Market downtrend -- no entries today.",
                            "body": "SPY is below its 20-day MA. Sitting in cash until market recovers.",
                            "action": "HOLD", "action_detail": "Market regime filter"}
    elif vix_blocked:
        data["briefing"] = {"date": today_str, "headline": f"VIX {vix} -- too elevated. No entries today.",
                            "body": f"VIX above {VIX_BLOCK}. Options overpriced. Sitting in cash.",
                            "action": "HOLD", "action_detail": "VIX gate"}
    elif opportunities:
        top = opportunities[0]
        earn_tag = f" | earnings bonus" if top.get("earnings_bonus", 0) > 0 else ""
        data["briefing"] = {"date": today_str,
                            "headline": f"{len(opportunities)} setup(s) found. Best: {top['ticker']} (score {top.get('score', '?')}){earn_tag}",
                            "body": "Setups ranked by conviction score. Verify live prices before entering.",
                            "action": "BUY", "action_detail": f"{len(opportunities)} trade idea(s) below"}
    else:
        data["briefing"] = {"date": today_str, "headline": "No setups today.",
                            "body": "Sitting in cash is the right move -- do not force a trade.",
                            "action": "HOLD", "action_detail": None}

    with open(DATA_JSON, "w") as f:
        json.dump(data, f, indent=2)


# -- Main ----------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  52-WEEK HIGH MOMENTUM OPTIONS SCREENER  (v3.1)")
    print(f"  Max/trade: ${MAX_TRADE_BUDGET} | Premium: ${TARGET_PREMIUM_LOW}-${TARGET_PREMIUM_HIGH}/sh")
    print(f"  Expiry: {MIN_EXPIRY_DAYS}-{MAX_EXPIRY_DAYS}d | Vol >={MIN_VOL_RATIO}x | OI >={MIN_OI}")
    print(f"  Earnings bonus: active (15-25d window, >={MIN_BEAT_RATE:.0%} beat rate)")
    print("=" * 60)

    print("\n-- Filter 1: Market Regime (SPY 20-day MA) -----------------")
    uptrend, spy_hist = check_market_regime()
    if not uptrend:
        print("\nHOLD -- market in downtrend. No entries today.")
        wti = get_wti_price()
        save_dashboard([], wti, regime_blocked=True)
        check_watchlist()
        return

    print("\n-- Filter 2: VIX Gate --------------------------------------")
    vix, entries_ok, iv_limit = check_vix()
    if not entries_ok:
        wti = get_wti_price()
        save_dashboard([], wti, vix=vix, vix_blocked=True)
        check_watchlist()
        return

    print("\n-- Oil Price Check ------------------------------------------")
    wti = get_wti_price()
    if wti is None:
        print("  WTI crude: unavailable")
    else:
        if   wti >= OIL_TREND_WARN:   status = "OK"
        elif wti >= OIL_DANGER_LEVEL: status = "TRENDING WEAK -- caution on energy"
        else:                         status = f"BELOW ${OIL_DANGER_LEVEL} -- AVOID ENERGY"
        print(f"  WTI Crude: ${wti}  [{status}]")

    check_watchlist()

    candidates = screen_stocks(TICKERS, spy_hist)

    if candidates.empty:
        print("No candidates after 52w high + volume + RS filters.")
        save_dashboard([], wti, vix=vix)
        return

    if wti is not None and wti < OIL_DANGER_LEVEL:
        flagged = candidates[candidates["ticker"].isin(ENERGY_TICKERS)]["ticker"].tolist()
        if flagged:
            print(f"  Dropping energy tickers (WTI ${wti}): {flagged}")
            candidates = candidates[~candidates["ticker"].isin(ENERGY_TICKERS)].reset_index(drop=True)

    print(f"\n{len(candidates)} stocks passed 52w high + volume + RS filters:\n")
    print(candidates[["ticker", "price", "52w_high", "dist_pct", "vol_ratio", "rs_delta"]].to_string(index=False))

    print("\n" + "=" * 60)
    print(f"  SCANNING OPTIONS (sector ETF + IV<={iv_limit}x + earnings check)...")
    print("=" * 60)

    etf_cache = {}
    tradeable = []

    for _, row in candidates.iterrows():
        ticker       = row["ticker"]
        price        = row["price"]
        realized_vol = row["sigma"]
        rs_delta     = row["rs_delta"]
        dist_pct     = row["dist_pct"]
        vol_ratio    = row["vol_ratio"]

        if not get_sector_trend(ticker, etf_cache):
            etf = SECTOR_ETFS.get(ticker, "?")
            print(f"  {ticker}: sector ETF {etf} below 20-day MA -- skipping")
            continue

        earnings_status = has_upcoming_earnings(ticker)
        if earnings_status is True:
            print(f"  {ticker}: earnings within {EARNINGS_BLACKOUT} days -- skipping")
            continue
        if earnings_status is None:
            print(f"  {ticker}: earnings date unknown -- skipping")
            continue

        # Earnings bonus (optional -- never blocks)
        earn_bonus = get_earnings_bonus(ticker)

        calls = find_calls(ticker, price, realized_vol, rs_delta, iv_limit)
        if not calls:
            continue

        for c in calls:
            otm_pct = c["otm_pct"] / 100
            score   = conviction_score(dist_pct, vol_ratio, rs_delta, otm_pct, earn_bonus)
            tradeable.append({
                "ticker":         ticker,
                "price":          price,
                "dist_pct":       dist_pct,
                "vol_ratio":      vol_ratio,
                "rs_delta":       rs_delta,
                "expiry":         c["expiry"],
                "strike":         c["strike"],
                "premium":        c["premium"],
                "cost_1x":        c["cost_1x"],
                "contracts":      c["contracts"],
                "IV_pct":         c["IV"],
                "OI":             c["OI"],
                "iv_ratio":       c.get("iv_ratio"),
                "otm_pct":        c["otm_pct"],
                "earnings_bonus": earn_bonus,
                "score":          score,
            })

    if not tradeable:
        print("\nNo options passed all filters today. Holding cash.")
        save_dashboard([], wti, vix=vix)
        return

    df  = pd.DataFrame(tradeable).sort_values(["score", "premium"], ascending=[False, True]).reset_index(drop=True)
    top = df.head(3)

    print(f"\n{len(df)} tradeable calls found. Top 3 by conviction score:\n")
    opps = []
    for i, row in top.iterrows():
        contracts  = row["contracts"]
        total_cost = round(row["premium"] * 100 * contracts, 2)
        target     = round(row["premium"] * 1.80, 2)
        stop       = round(row["premium"] * 0.65, 2)   # -35%
        iv_tag     = f" | IV/HV: {row['iv_ratio']}x" if row.get("iv_ratio") else ""
        earn_tag   = f" | EARN BONUS +{row['earnings_bonus']:.0%}" if row["earnings_bonus"] > 0 else ""

        print(f"""
#{i+1}  {row['ticker']}  |  ${row['price']} stock  |  Score: {row['score']}{earn_tag}
    {row['dist_pct']}% from 52w high | vol {row['vol_ratio']}x | RS: {row['rs_delta']:+.1%} vs SPY
    Strike: ${row['strike']} (+{row['otm_pct']}% OTM) | Expiry: {row['expiry']} | IV: {row['IV_pct']}%{iv_tag}
    Entry:  ${row['premium']}/sh -> {contracts} contract(s) = ${total_cost}
    Target: ${target}/sh (+80%) | Stop: ${stop}/sh (-35%)
""")

        opps.append({
            "ticker":         row["ticker"],
            "price":          row["price"],
            "dist_pct":       row["dist_pct"],
            "strike":         row["strike"],
            "expiry":         row["expiry"],
            "premium":        row["premium"],
            "contracts":      int(row["contracts"]),
            "cost":           total_cost,
            "target":         target,
            "stop":           stop,
            "iv_pct":         row["IV_pct"],
            "score":          row["score"],
            "rs_delta":       row["rs_delta"],
            "earnings_bonus": row["earnings_bonus"],
        })

    save_dashboard(opps, wti, vix=vix)
    print("Not financial advice. Verify prices live on Robinhood before entering.\n")


if __name__ == "__main__":
    main()
