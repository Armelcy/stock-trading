"""
52-Week High Momentum Options Screener
Budget: $500 | Max per trade: $250 | Target premium: $0.30–$0.80/share
Strategy: ATM or slightly ITM calls, 2–3 weeks out
"""

import json
import os
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

# ── Config ─────────────────────────────────────────────────────────────────
MAX_TRADE_BUDGET    = 100       # max dollars per trade (agentic account cap — update when funded)
TARGET_PREMIUM_LOW  = 0.20      # min premium per share
TARGET_PREMIUM_HIGH = 1.00      # max premium per share (gives room above $0.80)
MAX_DIST_FROM_HIGH  = 0.05      # within 5% of 52-week high (tightened from 8% — reduces weak setups)
MAX_OTM_PCT         = 0.03      # strike must be within 3% above current price (HAL lesson — no far OTM)
MIN_EXPIRY_DAYS     = 10        # at least 10 days out
MAX_EXPIRY_DAYS     = 25        # no more than 25 days out (2–3 weeks)
OIL_DANGER_LEVEL    = 84.0      # warn on energy trades if WTI crude below this
OIL_TREND_WARN      = 87.0      # soft warning if oil trending toward danger zone
MIN_OI              = 200       # minimum open interest (raised from 100 — better liquidity)
MIN_VOL_RATIO       = 0.8       # stock must trade at ≥80% of its 20-day avg volume
MAX_SPREAD_PCT      = 0.15      # skip if bid/ask spread > 15% of mid price (tightened from 20%)
EARNINGS_BLACKOUT   = 14        # skip if earnings within this many days
MIN_CONVICTION      = 3         # all 3 must pass: dist from high + vol ratio + OI — no partial entries

# Energy tickers — flag these if oil is weak
ENERGY_TICKERS = {"SLB", "MPC", "XOM", "CVX", "OXY", "HAL"}

# Watchlist — mix of your existing radar + sector leaders
TICKERS = [
    # Your radar
    "SOUN", "AMD", "AAPL", "CAT",
    # Industrials (sector leader YTD)
    "DE", "HON", "GE", "ETN", "EMR",
    # Energy  (XOM/CVX/OXY/SLB removed Jul 2026 — oil at $68, blocked until WTI > $84)
    # (HAL removed Jul 2026 — consistent loser in 3y backtest)
    # Financials — added Jul 2026 (V hit 52w high, sector leading)
    "V", "MA", "JPM", "AXP",
    # Defense — added Jul 2026 (sector surge: RTX/LMT/NOC all +3-5% Jul 2)
    "RTX", "LMT", "NOC",
    # Healthcare  (UNH, MDT removed Jul 2026 — consistent losers in 3y backtest)
    "LLY", "ABT", "DHR",
    # Communication  (DIS removed Jul 2026 — consistent loser in 3y backtest)
    "META", "GOOGL", "NFLX",
    # Momentum/AI + Consumer
    "NVDA", "MSFT", "PLTR", "CRWD", "AMZN",
]

# ── Oil price check ────────────────────────────────────────────────────────

def get_wti_price():
    """Fetch current WTI crude price via CL=F futures ticker."""
    try:
        oil = yf.Ticker("CL=F")
        hist = oil.history(period="5d")
        if hist.empty:
            return None
        return round(hist["Close"].iloc[-1], 2)
    except Exception:
        return None


def check_oil(candidates_df):
    """Print oil price status and flag energy tickers if oil is weak."""
    wti = get_wti_price()
    if wti is None:
        print("  WTI crude: unavailable (check manually before energy trades)")
        return

    status = "✅ OK" if wti >= OIL_DANGER_LEVEL else f"⚠️  BELOW ${OIL_DANGER_LEVEL} — SKIP ENERGY TRADES"
    print(f"  WTI Crude (CL=F): ${wti}  {status}\n")

    if wti < OIL_DANGER_LEVEL and not candidates_df.empty:
        flagged = candidates_df[candidates_df["ticker"].isin(ENERGY_TICKERS)]
        if not flagged.empty:
            print(f"  ⚠️  Oil danger: removing energy tickers from candidates: {list(flagged['ticker'])}\n")
            return candidates_df[~candidates_df["ticker"].isin(ENERGY_TICKERS)].reset_index(drop=True)

    return candidates_df


# ── Helpers ────────────────────────────────────────────────────────────────

def pct(val):
    return f"{val:.1f}%"

def money(val):
    return f"${val:.2f}"


def get_expiry_window():
    now = datetime.now()
    low  = now + timedelta(days=MIN_EXPIRY_DAYS)
    high = now + timedelta(days=MAX_EXPIRY_DAYS)
    return low, high


def screen_stocks(tickers):
    """Return stocks within MAX_DIST_FROM_HIGH of their 52-week high."""
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
            dist     = (high_52w - price) / high_52w   # 0 = AT the high

            if dist < 0 or dist > MAX_DIST_FROM_HIGH:
                continue

            vol_20d = hist["Volume"].tail(20).mean()
            vol_now = hist["Volume"].iloc[-1]
            vol_ratio = vol_now / vol_20d if vol_20d > 0 else 0

            # Filter: stock volume must be at least MIN_VOL_RATIO of average
            if vol_ratio < MIN_VOL_RATIO:
                continue

            candidates.append({
                "ticker":       ticker,
                "price":        round(price, 2),
                "52w_high":     round(high_52w, 2),
                "dist_pct":     round(dist * 100, 2),
                "vol_ratio":    round(vol_ratio, 2),
            })
        except Exception:
            continue

    df = pd.DataFrame(candidates)
    if df.empty:
        return df
    return df.sort_values("dist_pct").reset_index(drop=True)


def has_upcoming_earnings(ticker):
    """Return True if earnings within EARNINGS_BLACKOUT days, None if unknown.
    Uses earnings_dates (more reliable) with calendar as fallback.
    Returns None when date cannot be determined — callers treat this as skip."""
    stk = yf.Ticker(ticker)

    # Method 1: earnings_dates DataFrame (most reliable)
    try:
        ed = stk.earnings_dates
        if ed is not None and not ed.empty:
            now_utc = pd.Timestamp.now(tz="UTC")
            future = ed[ed.index > now_utc]
            if not future.empty:
                next_earn = future.index[0]
                days_until = (next_earn.tz_localize(None) - pd.Timestamp.now()).days
                return 0 <= days_until <= EARNINGS_BLACKOUT
            return False  # No future dates found — assume safe
    except Exception:
        pass

    # Method 2: calendar dict fallback
    try:
        cal = stk.calendar
        if cal:
            dates = cal.get("Earnings Date", [])
            if dates:
                next_earn = pd.Timestamp(dates[0])
                days_until = (next_earn - pd.Timestamp.now()).days
                return 0 <= days_until <= EARNINGS_BLACKOUT
    except Exception:
        pass

    # Unknown — cannot confirm safety; treat as blocked
    return None


def find_calls(ticker, stock_price):
    """Find call options matching our budget and expiry window."""
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

        # Only ATM or slightly ITM — no far OTM (HAL lesson)
        # Strike must be between 5% below and MAX_OTM_PCT above current price
        calls = calls[calls["strike"] >= stock_price * 0.95]
        calls = calls[calls["strike"] <= stock_price * (1 + MAX_OTM_PCT)]

        # Use bid/ask mid as the live price; fall back to lastPrice if market closed
        has_quote = (calls["bid"] > 0) & (calls["ask"] > 0)
        mid = (calls["bid"] + calls["ask"]) / 2
        calls = calls.copy()
        calls["mid_price"] = mid.where(has_quote, calls["lastPrice"])

        # Filter by premium budget (using mid price — not stale lastPrice)
        calls = calls[calls["mid_price"] >= TARGET_PREMIUM_LOW]
        calls = calls[calls["mid_price"] <= TARGET_PREMIUM_HIGH]

        # Must fit trade budget (1 contract = 100 shares)
        calls = calls[calls["mid_price"] * 100 <= MAX_TRADE_BUDGET]

        # Filter: minimum open interest for liquidity
        calls = calls[calls["openInterest"].fillna(0) >= MIN_OI]

        # Filter: bid/ask spread must be ≤ MAX_SPREAD_PCT of mid price
        # Skip spread check when market is closed (bid/ask = 0)
        spread_pct = (calls["ask"] - calls["bid"]) / mid.replace(0, float("nan"))
        calls = calls[~has_quote | (spread_pct.fillna(1) <= MAX_SPREAD_PCT)]

        for _, row in calls.iterrows():
            contracts = int(MAX_TRADE_BUDGET // (row["mid_price"] * 100))
            results.append({
                "expiry":    exp_str,
                "strike":    row["strike"],
                "premium":   round(row["mid_price"], 2),
                "cost_1x":   round(row["mid_price"] * 100, 2),
                "contracts": max(1, contracts),
                "IV":        round(row.get("impliedVolatility", 0) * 100, 1),
                "OI":        int(row.get("openInterest", 0) or 0),
                "volume":    int(row.get("volume", 0) or 0),
            })

    return sorted(results, key=lambda x: x["premium"])


# ── Watchlist Monitor ──────────────────────────────────────────────────────

DATA_JSON = os.path.join(os.path.dirname(__file__), "web", "data.json")

def fetch_quote(ticker, timeout=8):
    """Fetch price/volume for a ticker via Yahoo Finance v8 API. Returns dict or None."""
    import requests
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"interval": "1d", "range": "5d"}
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        r.raise_for_status()
        j = r.json()
        result = j["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        volumes = result["indicators"]["quote"][0]["volume"]
        # Filter None values
        closes  = [c for c in closes  if c is not None]
        volumes = [v for v in volumes if v is not None]
        if len(closes) < 2:
            return None, "not enough data"
        return {"closes": closes, "volumes": volumes}, None
    except Exception as e:
        return None, str(e)


def validate_plan(plan, ticker, price):
    """Sanity-check a hand-written watchlist plan against live data and the
    screener's own rules. Returns a list of warnings (empty = plan looks OK).
    Added after the SLB $58C incident: plans bypassed all safety filters."""
    import re
    warnings = []

    # 1. Expired or near-expiry date in plan text (e.g. "6/26" or "06/26")
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})\b", plan):
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            now = datetime.now()
            year = now.year
            plan_date = datetime(year, month, day)
            # if date already passed by >6 months, it probably means next year
            if (now - plan_date).days > 180:
                plan_date = datetime(year + 1, month, day)
            if plan_date < now:
                warnings.append(f"references {month}/{day} which has passed — plan is stale")
            break

    # 2. Strike too far OTM vs current price (screener rule: ≤3% above spot)
    strike_m = re.search(r"\$(\d+(?:\.\d+)?)\s*C\b", plan, re.IGNORECASE)
    if strike_m and price:
        strike = float(strike_m.group(1))
        otm = (strike - price) / price
        if otm > MAX_OTM_PCT:
            warnings.append(
                f"strike ${strike:g} is {otm*100:.0f}% above ${price} — violates ≤{MAX_OTM_PCT*100:.0f}% OTM rule")

    # 3. Energy ticker while oil is in the danger zone
    if ticker in ENERGY_TICKERS:
        wti = get_wti_price()
        if wti is not None and wti < OIL_DANGER_LEVEL:
            warnings.append(f"energy ticker with WTI ${wti} < ${OIL_DANGER_LEVEL} — oil rule says avoid")

    return warnings


def check_watchlist():
    """Check watchlist tickers and print day trade signals. Updates data.json with live prices."""
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
        ticker = item["ticker"]
        quote, err = fetch_quote(ticker)

        if err or quote is None:
            msg = err or "no data"
            print(f"\n  {ticker} ({item.get('name', '')})")
            print(f"    ⚠️  Could not fetch data ({msg}) — check manually on Robinhood")
            item["signal"] = "⚠️ No data — check manually"
            item["last_checked"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            updated = True
            continue

        closes    = quote["closes"]
        volumes   = quote["volumes"]
        price     = round(closes[-1], 2)
        prev_close = round(closes[-2], 2)
        chg_pct   = round((price - prev_close) / prev_close * 100, 2)
        vol_today = volumes[-1] if volumes else 0
        vol_avg   = sum(volumes) / len(volumes) if volumes else 0
        vol_ratio = round(vol_today / vol_avg, 2) if vol_avg > 0 else 0

        chg_str = f"+{chg_pct}%" if chg_pct >= 0 else f"{chg_pct}%"

        # Day trade signal logic
        if chg_pct <= -5 and vol_ratio >= 1.5:
            signal = "🟢 BUY DIP — down 5%+ with high volume. Long entry window."
        elif chg_pct <= -3:
            signal = "🟡 WATCH — mild dip. Wait for volume confirmation before entering."
        elif chg_pct >= 8 and vol_ratio >= 2.0:
            signal = "🔥 MOMENTUM — up 8%+ on 2x volume. Ride or wait for pullback."
        elif chg_pct >= 5:
            signal = "📈 RISING — up 5%+. Watch first 30 min for pullback entry."
        elif vol_ratio >= 2.0:
            signal = "⚡ VOLUME SPIKE — unusual volume. Monitor closely."
        else:
            signal = "😴 QUIET — no signal today."

        print(f"\n  {ticker} ({item.get('name', '')})")
        print(f"    Price: ${price}  |  Change: {chg_str}  |  Vol ratio: {vol_ratio}x")
        print(f"    Signal: {signal}")
        if item.get("day_trade_plan"):
            plan = item["day_trade_plan"]
            warnings = validate_plan(plan, ticker, price)
            if warnings:
                print(f"    Plan: ❌ INVALID — {'; '.join(warnings)}")
                print(f"          (was: {plan})")
            else:
                print(f"    Plan: {plan}")

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


def save_dashboard(opportunities, wti):
    """Write today's screener results into web/data.json so the Vercel
    dashboard shows them. opportunities = list of dicts (may be empty)."""
    try:
        with open(DATA_JSON) as f:
            data = json.load(f)
    except Exception:
        data = {}
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if wti is not None:
        data["oil_price"] = wti
        data["oil_status"] = ("ok" if wti >= OIL_TREND_WARN
                              else "warning" if wti >= OIL_DANGER_LEVEL
                              else "danger")
    data["opportunities"] = opportunities
    data["briefing"] = (
        f"{len(opportunities)} setup(s) found — verify live prices before entering."
        if opportunities else
        "No setups today. Sitting in cash is the right move — do not force a trade."
    )
    with open(DATA_JSON, "w") as f:
        json.dump(data, f, indent=2)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  52-WEEK HIGH MOMENTUM OPTIONS SCREENER")
    print(f"  Max/trade: ${MAX_TRADE_BUDGET} | Target premium: ${TARGET_PREMIUM_LOW}–${TARGET_PREMIUM_HIGH}/sh")
    print(f"  Expiry window: {MIN_EXPIRY_DAYS}–{MAX_EXPIRY_DAYS} days | Vol ratio ≥{MIN_VOL_RATIO}x | OI ≥{MIN_OI}")
    print("=" * 60)

    # Oil price check first
    print("\n── Oil Price Check ──────────────────────────────────────")
    wti = get_wti_price()
    if wti is None:
        print("  WTI crude: unavailable (check manually before energy trades)")
    else:
        if wti >= OIL_TREND_WARN:
            status = "✅ OK"
        elif wti >= OIL_DANGER_LEVEL:
            status = f"🟡 TRENDING WEAK (${OIL_DANGER_LEVEL}–${OIL_TREND_WARN}) — caution on energy trades"
        else:
            status = f"⚠️  BELOW ${OIL_DANGER_LEVEL} — AVOID ENERGY TRADES"
        print(f"  WTI Crude (CL=F): ${wti}  {status}")
    print()

    check_watchlist()

    candidates = screen_stocks(TICKERS)

    if candidates.empty:
        print("No candidates found today. Sitting in cash is the right move — do not force a trade.")
        save_dashboard([], wti)
        return

    # Remove energy tickers if oil is in danger zone
    if wti is not None and wti < OIL_DANGER_LEVEL:
        flagged = candidates[candidates["ticker"].isin(ENERGY_TICKERS)]["ticker"].tolist()
        if flagged:
            print(f"⚠️  Dropping energy tickers due to weak oil: {flagged}\n")
            candidates = candidates[~candidates["ticker"].isin(ENERGY_TICKERS)].reset_index(drop=True)

    print(f"✅ {len(candidates)} stocks within {MAX_DIST_FROM_HIGH*100:.0f}% of 52-week high:\n")
    print(candidates.to_string(index=False))

    print("\n" + "=" * 60)
    print("  SCANNING OPTIONS CHAINS...")
    print("=" * 60)

    tradeable = []

    for _, row in candidates.iterrows():
        ticker = row["ticker"]
        price  = row["price"]

        # Skip if earnings within blackout window (or unknown — conservative)
        earnings_status = has_upcoming_earnings(ticker)
        if earnings_status is True:
            print(f"  ⚠️  {ticker}: earnings within {EARNINGS_BLACKOUT} days — skipping")
            continue
        if earnings_status is None:
            print(f"  ⚠️  {ticker}: earnings date unknown — skipping (verify manually before trading)")
            continue

        calls  = find_calls(ticker, price)

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
            })

    if not tradeable:
        print("\n❌ No options found matching your budget/expiry criteria.")
        print("   Try widening TARGET_PREMIUM_HIGH or MAX_EXPIRY_DAYS.\n")
        save_dashboard([], wti)
        return

    df = pd.DataFrame(tradeable)
    df = df.sort_values(["dist%", "premium"]).reset_index(drop=True)

    print(f"\n🎯 {len(df)} tradeable call options found:\n")
    print(df.to_string(index=False))

    print("\n" + "=" * 60)
    print("  TOP 3 PICKS (closest to high + affordable)")
    print("=" * 60)
    top3 = df.head(3)
    for i, row in top3.iterrows():
        contracts = row["contracts"]
        total_cost = round(row["premium"] * 100 * contracts, 2)
        target_exit = round(row["premium"] * 1.9, 2)  # ~90% gain target
        stop_loss   = round(row["premium"] * 0.60, 2)  # -40% stop
        print(f"""
#{i+1}  {row['ticker']}  |  ${row['price']} stock  |  {row['dist%']}% from 52w high
    Strike: ${row['strike']}  |  Expiry: {row['expiry']}  |  IV: {row['IV%']}%
    Entry:  ${row['premium']}/share  →  {contracts} contract(s) = ${total_cost}
    Target: ${target_exit}/share  (~90% gain, ${round((target_exit - row['premium'])*100*contracts, 2)} profit)
    Stop:   ${stop_loss}/share    (-40% loss, -${round((row['premium'] - stop_loss)*100*contracts, 2)})
""")

    print("⚠️  Not financial advice. Verify prices live on Robinhood before entering.\n")

    # Push today's top picks to the dashboard
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
            "target":    round(row["premium"] * 1.9, 2),
            "stop":      round(row["premium"] * 0.60, 2),
            "iv_pct":    row["IV%"],
        })
    save_dashboard(opps, wti)


if __name__ == "__main__":
    main()
