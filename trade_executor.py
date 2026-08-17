"""
Autonomous trade executor — runs after screener.py.
Handles both EXIT (stop/target/expiry-week) and ENTRY logic.
Reads web/data.json for signals. Updates data.json after trading.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

DATA_FILE = Path(__file__).parent / "web" / "data.json"

# ── Hard rules (mirror CLAUDE.md — update both places) ──────────────────────
MAX_TRADE_BUDGET    = 150       # max dollars per trade
MAX_POSITIONS       = 2         # max simultaneous open positions
MAX_BP_PCT          = 0.75      # never commit more than 75% of buying power
OIL_DANGER_LEVEL    = 84.0
ENERGY_TICKERS      = {"SLB", "MPC", "XOM", "CVX", "OXY", "HAL"}
MIN_PREMIUM         = 0.20
MAX_PREMIUM         = 1.00
MIN_OI              = 200
TAKE_PROFIT_PCT     = 0.80      # exit at +80%
STOP_LOSS_PCT       = -0.40     # exit at -40%


# ── Data helpers ─────────────────────────────────────────────────────────────

def load_data():
    with open(DATA_FILE) as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


# ── Robinhood login ──────────────────────────────────────────────────────────

def rh_login():
    import pyotp
    import robin_stocks.robinhood as r
    totp_key = os.environ.get("RH_TOTP_KEY", "")
    mfa_code = pyotp.TOTP(totp_key).now() if totp_key else None
    login_result = r.login(
        username=os.environ["RH_USERNAME"],
        password=os.environ["RH_PASSWORD"],
        mfa_code=mfa_code,
        store_session=False,
        expiresIn=600,
    )
    if not login_result:
        raise RuntimeError("Robinhood login failed")
    log("Logged in to Robinhood")
    return r


def rh_logout(r):
    try:
        r.logout()
    except Exception:
        pass


# ── Option quote ─────────────────────────────────────────────────────────────

def get_live_quote(r, ticker, strike, expiry):
    """Return dict with mid, bid, ask, oi — or None on failure."""
    try:
        chain = r.options.find_options_by_expiration_and_strike(
            inputSymbols=ticker,
            expirationDate=expiry,
            strikePrice=str(strike),
            optionType="call",
        )
        if not chain:
            return None
        opt = chain[0]
        bid = float(opt.get("bid_price") or 0)
        ask = float(opt.get("ask_price") or 0)
        oi  = int(float(opt.get("open_interest") or 0))
        mid = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else float(opt.get("last_trade_price") or 0)
        return {"mid": mid, "bid": bid, "ask": ask, "oi": oi}
    except Exception as e:
        log(f"  Quote error for {ticker} ${strike}C {expiry}: {e}")
        return None


# ── Orders ───────────────────────────────────────────────────────────────────

ACCOUNT = os.environ.get("RH_ACCOUNT", "")

def place_buy(r, ticker, strike, expiry, contracts, price):
    log(f"  BUY {ticker} ${strike}C {expiry} x{contracts} @ ${price}/sh")
    result = r.orders.order_buy_option_limit(
        positionEffect="open",
        creditOrDebit="debit",
        price=str(round(price, 2)),
        symbol=ticker,
        quantity=contracts,
        expirationDate=expiry,
        strike=str(strike),
        optionType="call",
        timeInForce="gfd",
        account_number=ACCOUNT,
        jsonify=True,
    )
    order_id = (result or {}).get("id", "unknown")
    log(f"  ✅ Buy order placed — ID: {order_id}")
    return order_id


def place_sell(r, ticker, strike, expiry, contracts, price):
    log(f"  SELL {ticker} ${strike}C {expiry} x{contracts} @ ${price}/sh")
    result = r.orders.order_sell_option_limit(
        positionEffect="close",
        creditOrDebit="credit",
        price=str(round(price, 2)),
        symbol=ticker,
        quantity=contracts,
        expirationDate=expiry,
        strike=str(strike),
        optionType="call",
        timeInForce="gfd",
        account_number=ACCOUNT,
        jsonify=True,
    )
    order_id = (result or {}).get("id", "unknown")
    log(f"  ✅ Sell order placed — ID: {order_id}")
    return order_id


# ── Exit logic ───────────────────────────────────────────────────────────────

def is_expiry_week(expiry_str):
    """Return True if today is Monday–Thursday of expiry week."""
    expiry = datetime.strptime(expiry_str, "%Y-%m-%d")
    today = datetime.now()
    # Monday of expiry week
    monday = expiry - timedelta(days=expiry.weekday())
    return monday <= today <= expiry - timedelta(days=1)  # Mon–Thu before expiry


def check_exits(r, data):
    """Check open positions for exit signals. Returns updated data."""
    positions = data.get("positions", [])
    updated = False

    for pos in positions:
        if pos["status"] != "open":
            continue

        ticker  = pos["ticker"]
        strike  = pos["strike"]
        expiry  = pos["expiry"]
        entry   = pos["entry"]
        contracts = pos["contracts"]

        log(f"\n── Checking exit: {ticker} ${strike}C {expiry} ──")

        quote = get_live_quote(r, ticker, strike, expiry)
        if not quote:
            log("  Could not fetch quote — skipping exit check")
            continue

        mid = quote["mid"]
        if mid <= 0:
            log("  Mid price is 0 — option may be worthless, skipping")
            continue

        pnl_pct = (mid - entry) / entry

        # Update live P&L in data
        pos["current_premium"] = mid
        pos["current_value"]   = round(mid * 100 * contracts, 2)
        pos["pnl"]             = round((mid - entry) * 100 * contracts, 2)
        pos["pnl_pct"]         = round(pnl_pct * 100, 1)
        updated = True

        log(f"  Live: ${mid}/sh | Entry: ${entry}/sh | P&L: {pnl_pct*100:+.1f}%")

        reason = None
        if pnl_pct >= TAKE_PROFIT_PCT:
            reason = f"TAKE PROFIT (+{pnl_pct*100:.0f}%)"
        elif pnl_pct <= STOP_LOSS_PCT:
            reason = f"STOP LOSS ({pnl_pct*100:.0f}%)"
        elif is_expiry_week(expiry):
            reason = "EXPIRY WEEK — exit by Thursday rule"

        if reason:
            log(f"  ⚡ EXIT signal: {reason}")
            order_id = place_sell(r, ticker, strike, expiry, contracts, mid)
            pos["status"]     = "closed"
            pos["exit"]       = mid
            pos["exit_value"] = round(mid * 100 * contracts, 2)
            pos["exit_reason"]= reason
            pos["closed_at"]  = datetime.now().strftime("%Y-%m-%d %H:%M")
            pos["exit_order_id"] = order_id

            # Move to trade_history
            data.setdefault("trade_history", []).append({
                "date":      pos.get("opened_at", ""),
                "ticker":    ticker,
                "direction": "CALL",
                "strike":    strike,
                "expiry":    expiry,
                "contracts": contracts,
                "entry":     entry,
                "entry_cost":pos["entry_cost"],
                "exit":      mid,
                "exit_value":pos["exit_value"],
                "pnl":       pos["pnl"],
                "pnl_pct":   pos["pnl_pct"],
                "status":    "closed",
                "reason":    reason,
            })
        else:
            log(f"  Hold — no exit signal")

    # Recalculate portfolio
    open_positions = [p for p in positions if p["status"] == "open"]
    committed = sum(p["entry_cost"] for p in open_positions)
    data["portfolio"]["committed"] = round(committed, 2)

    if updated:
        save_data(data)

    return data


# ── Entry logic ──────────────────────────────────────────────────────────────

def check_entries(r, data):
    """Evaluate screener opportunities and enter if rules pass."""
    action       = data.get("briefing", {}).get("action", "HOLD")
    opportunities = data.get("opportunities", [])

    if action != "BUY" or not opportunities:
        log(f"\n📊 Screener action: {action} — no entries today")
        return data

    log(f"\n📊 Screener action: BUY — {len(opportunities)} opportunity(ies)")

    open_positions = [p for p in data.get("positions", []) if p["status"] == "open"]
    slots = MAX_POSITIONS - len(open_positions)

    if slots <= 0:
        log(f"⛔ Already at max positions ({len(open_positions)}/{MAX_POSITIONS})")
        return data

    # Fetch buying power from data.json (synced after each trade)
    buying_power = float(data["portfolio"].get("available", 0))
    log(f"💰 Available buying power: ${buying_power:.2f} | Open slots: {slots}")

    if buying_power < MIN_PREMIUM * 100:
        log("⛔ Insufficient buying power")
        return data

    wti = data.get("oil_price")
    trades_placed = 0

    for opp in opportunities:
        if trades_placed >= slots:
            break

        ticker    = opp["ticker"]
        strike    = opp["strike"]
        expiry    = opp["expiry"]
        contracts = opp.get("contracts", 1)

        log(f"\n── Evaluating entry: {ticker} ${strike}C {expiry} ──")

        # Energy check
        if ticker in ENERGY_TICKERS and wti is not None and wti < OIL_DANGER_LEVEL:
            log(f"  ⛔ Energy ticker with WTI ${wti} < ${OIL_DANGER_LEVEL} — skip")
            continue

        # Re-fetch live quote to confirm setup still valid
        quote = get_live_quote(r, ticker, strike, expiry)
        if not quote:
            log("  ⛔ Could not fetch live quote — skip")
            continue

        mid = quote["mid"]
        oi  = quote["oi"]

        log(f"  Live: ${mid}/sh | OI: {oi}")

        # Validate all rules with live data
        if mid < MIN_PREMIUM or mid > MAX_PREMIUM:
            log(f"  ⛔ Premium ${mid} outside [${MIN_PREMIUM}–${MAX_PREMIUM}] — skip")
            continue
        if oi < MIN_OI:
            log(f"  ⛔ OI {oi} < {MIN_OI} — skip")
            continue

        # Fit to budget
        max_contracts = max(1, int(MAX_TRADE_BUDGET // (mid * 100)))
        contracts     = min(contracts, max_contracts)
        total_cost    = round(mid * 100 * contracts, 2)

        if total_cost > MAX_TRADE_BUDGET:
            log(f"  ⛔ Cost ${total_cost} > budget ${MAX_TRADE_BUDGET} — skip")
            continue
        if total_cost > buying_power * MAX_BP_PCT:
            log(f"  ⛔ Cost ${total_cost} > 75% of ${buying_power:.2f} — skip")
            continue

        # Place order
        order_id = place_buy(r, ticker, strike, expiry, contracts, mid)

        # Record position
        position = {
            "ticker":          ticker,
            "strike":          strike,
            "expiry":          expiry,
            "contracts":       contracts,
            "entry":           mid,
            "entry_cost":      total_cost,
            "target":          round(mid * (1 + TAKE_PROFIT_PCT), 2),
            "stop":            round(mid * (1 + STOP_LOSS_PCT), 2),
            "status":          "open",
            "order_id":        order_id,
            "opened_at":       datetime.now().strftime("%Y-%m-%d %H:%M"),
            "current_premium": mid,
            "current_value":   total_cost,
            "pnl":             0,
            "pnl_pct":         0,
        }
        data.setdefault("positions", []).append(position)

        buying_power -= total_cost
        trades_placed += 1
        log(f"  ✅ Position recorded — remaining BP: ${buying_power:.2f}")

    # Recalculate portfolio
    open_positions = [p for p in data.get("positions", []) if p["status"] == "open"]
    committed = sum(p["entry_cost"] for p in open_positions)
    data["portfolio"]["committed"] = round(committed, 2)
    data["portfolio"]["available"] = round(data["portfolio"]["current_balance"] - committed, 2)

    if trades_placed:
        save_data(data)
        log(f"\n✅ {trades_placed} trade(s) placed")
    else:
        log("\n📊 No entries placed — all opportunities failed live validation")

    return data


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  AUTONOMOUS TRADE EXECUTOR")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print("=" * 60)

    data = load_data()

    r = rh_login()
    try:
        data = check_exits(r, data)
        data = check_entries(r, data)
    finally:
        rh_logout(r)

    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    main()
