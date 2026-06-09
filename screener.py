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
MAX_TRADE_BUDGET    = 250       # max dollars per trade
TARGET_PREMIUM_LOW  = 0.20      # min premium per share
TARGET_PREMIUM_HIGH = 1.00      # max premium per share (gives room above $0.80)
MAX_DIST_FROM_HIGH  = 0.08      # within 8% of 52-week high
MIN_EXPIRY_DAYS     = 10        # at least 10 days out
MAX_EXPIRY_DAYS     = 25        # no more than 25 days out (2–3 weeks)
OIL_DANGER_LEVEL    = 84.0      # warn on energy trades if WTI crude below this
MIN_OI              = 100       # minimum open interest — below this = illiquid
MIN_VOL_RATIO       = 0.8       # stock must trade at ≥80% of its 20-day avg volume
MAX_SPREAD_PCT      = 0.20      # skip if bid/ask spread > 20% of mid price
EARNINGS_BLACKOUT   = 14        # skip if earnings within this many days

# Energy tickers — flag these if oil is weak
ENERGY_TICKERS = {"SLB", "MPC", "XOM", "CVX", "OXY", "HAL"}

# Watchlist — mix of your existing radar + sector leaders
TICKERS = [
    # Your radar
    "SOUN", "AMD", "MPC", "AAPL", "CAT",
    # Industrials (sector leader YTD)
    "DE", "HON", "GE", "ETN", "EMR",
    # Energy
    "XOM", "CVX", "OXY", "SLB", "HAL",
    # Healthcare
    "LLY", "UNH", "ABT", "MDT", "DHR",
    # Communication
    "META", "GOOGL", "NFLX", "DIS",
    # Momentum/AI
    "NVDA", "MSFT", "PLTR", "CRWD",
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
    """Return True if earnings are within EARNINGS_BLACKOUT days."""
    try:
        stk = yf.Ticker(ticker)
        cal = stk.calendar
        if not cal:
            return False
        dates = cal.get("Earnings Date", [])
        if not dates:
            return False
        next_earn = pd.Timestamp(dates[0])
        days_until = (next_earn - pd.Timestamp.now()).days
        return 0 <= days_until <= EARNINGS_BLACKOUT
    except Exception:
        return False


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

        # Only ATM / slightly ITM (within 5% of current price)
        calls = calls[calls["strike"] >= stock_price * 0.95]
        calls = calls[calls["strike"] <= stock_price * 1.05]

        # Filter by premium budget
        calls = calls[calls["lastPrice"] >= TARGET_PREMIUM_LOW]
        calls = calls[calls["lastPrice"] <= TARGET_PREMIUM_HIGH]

        # Must fit trade budget (1 contract = 100 shares)
        calls = calls[calls["lastPrice"] * 100 <= MAX_TRADE_BUDGET]

        # Filter: minimum open interest for liquidity
        calls = calls[calls["openInterest"].fillna(0) >= MIN_OI]

        # Filter: bid/ask spread must be ≤ MAX_SPREAD_PCT of mid price
        # Skip when bid/ask are 0 (market closed — no live quote available)
        has_quote = (calls["bid"] > 0) & (calls["ask"] > 0)
        mid = (calls["bid"] + calls["ask"]) / 2
        spread_pct = (calls["ask"] - calls["bid"]) / mid.replace(0, float("nan"))
        calls = calls[~has_quote | (spread_pct.fillna(1) <= MAX_SPREAD_PCT)]

        for _, row in calls.iterrows():
            contracts = int(MAX_TRADE_BUDGET // (row["lastPrice"] * 100))
            results.append({
                "expiry":    exp_str,
                "strike":    row["strike"],
                "premium":   row["lastPrice"],
                "cost_1x":   round(row["lastPrice"] * 100, 2),
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
            print(f"    Plan: {item['day_trade_plan']}")

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


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  52-WEEK HIGH MOMENTUM OPTIONS SCREENER")
    print(f"  Budget: $500 | Max/trade: ${MAX_TRADE_BUDGET} | Target: $0.30–$0.80")
    print(f"  Expiry window: {MIN_EXPIRY_DAYS}–{MAX_EXPIRY_DAYS} days out")
    print("=" * 60)

    # Oil price check first
    print("\n── Oil Price Check ──────────────────────────────────────")
    wti = get_wti_price()
    if wti is None:
        print("  WTI crude: unavailable (check manually before energy trades)")
    else:
        status = "✅ OK" if wti >= OIL_DANGER_LEVEL else f"⚠️  BELOW ${OIL_DANGER_LEVEL} — AVOID ENERGY TRADES"
        print(f"  WTI Crude (CL=F): ${wti}  {status}")
    print()

    check_watchlist()

    candidates = screen_stocks(TICKERS)

    if candidates.empty:
        print("No candidates found today. Try widening MAX_DIST_FROM_HIGH.")
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

        # Skip if earnings within blackout window
        if has_upcoming_earnings(ticker):
            print(f"  ⚠️  {ticker}: earnings within {EARNINGS_BLACKOUT} days — skipping")
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


if __name__ == "__main__":
    main()
