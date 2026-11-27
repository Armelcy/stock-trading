"""
Paper options tracker -- PRETEND trades only, never sent to Robinhood.

Question it answers: would a ~90-DTE, ~10%-ITM call on a screener pick under $60
have made money from Nov 2026 on, at real option quotes? research/cheap_calls.py
backtested +23%/trade +/-19 (n=41, 2025 negative), not enough to risk money on.

Runs after screener.py (GitHub Actions, after the close). One paper slot:
  Entry: first screener opportunity priced under $60, call with 70-130 DTE and
         strike nearest 0.90 x spot, spread <= 10% of mid. Filled at the ASK
         (or last trade + penalty spread when Yahoo has no after-hours quote).
  Exits (marked at the BID on the evening quote): +80% take profit; -35% stop,
         moved to breakeven once up +40%; or 75 calendar days held / 21 DTE left.
Writes data.json["paper_options"]. The executor must ignore this block.

Usage: python3 paper_options.py
"""

import json
import os
from datetime import datetime

import yfinance as yf

from screener import ET, call_reference, load_data, write_data

PRICE_CAP       = 60.0
MAX_SPREAD_PCT  = 10.0
TAKE_PROFIT     = 0.80
STOP_LOSS       = -0.35
BREAKEVEN_ARM   = 0.40
MAX_HELD_DAYS   = 75
MIN_DTE_LEFT    = 21
LAST_HALF_PCT   = 0.05
LAST_MIN_HALF   = 0.05


def quote(ticker, expiry, strike):
    """(bid, ask) for one call contract, or None.
    Yahoo zeroes bid/ask outside market hours, and this runs after the close, so
    fall back to the last trade (if within 3 days) with a penalty spread of
    max($0.05, 5%) each side -- twice the backtest's half-spread, to stay conservative."""
    try:
        calls = yf.Ticker(ticker).option_chain(expiry).calls
        row = calls[calls["strike"] == strike]
        if row.empty:
            return None
        c = row.iloc[0]
        bid, ask = float(c.get("bid") or 0), float(c.get("ask") or 0)
        if bid > 0 and ask > 0:
            return bid, ask
        last, traded = float(c.get("lastPrice") or 0), c.get("lastTradeDate")
        if last <= 0 or traded is None or (datetime.now(ET) - traded.tz_convert(ET)).days > 3:
            return None
        half = max(LAST_MIN_HALF, LAST_HALF_PCT * last)
        return max(last - half, 0.0), last + half
    except Exception as e:
        print(f"  {ticker} {expiry} {strike}C: quote unavailable ({e})")
        return None


def mark(pos, today):
    q = quote(pos["ticker"], pos["expiry"], pos["strike"])
    if q is None or q[0] <= 0:
        return None
    bid = q[0]
    ret = bid / pos["entry"] - 1
    pos.update(last_bid=round(bid, 2), last_marked=today.isoformat(), ret_pct=round(ret * 100, 1))
    if ret >= BREAKEVEN_ARM:
        pos["breakeven_armed"] = True
    stop = 0.0 if pos.get("breakeven_armed") else STOP_LOSS
    held = (today - datetime.strptime(pos["opened"], "%Y-%m-%d").date()).days
    dte = (datetime.strptime(pos["expiry"], "%Y-%m-%d").date() - today).days
    if ret >= TAKE_PROFIT:
        return "take profit +80%"
    if ret <= stop:
        return "breakeven stop" if pos.get("breakeven_armed") else "stop -35%"
    if held >= MAX_HELD_DAYS or dte <= MIN_DTE_LEFT:
        return "time exit"
    return None


def try_entry(data, today):
    for o in data.get("opportunities") or []:
        price = o.get("price")
        if not price or price >= PRICE_CAP:
            continue
        ref = call_reference(o["ticker"], price)
        if not ref:
            continue
        q = quote(o["ticker"], ref["expiry"], ref["strike"])
        if q is None or q[0] <= 0 or q[1] <= 0:
            continue
        bid, ask = q
        spread = (ask - bid) / ((ask + bid) / 2) * 100
        if spread > MAX_SPREAD_PCT + 0.5:  # penalty-spread fallback sits at ~10%
            print(f"  {o['ticker']}: spread {spread:.0f}% > {MAX_SPREAD_PCT:.0f}%, skipped")
            continue
        return {
            "ticker": o["ticker"], "stock_price": price, "strike": ref["strike"],
            "expiry": ref["expiry"], "opened": today.isoformat(),
            "entry": round(ask, 2), "cost": round(ask * 100, 2), "spread_pct": round(spread, 1),
            "last_bid": round(bid, 2), "last_marked": today.isoformat(),
            "ret_pct": round((bid / ask - 1) * 100, 1), "breakeven_armed": False,
        }
    return None


def main():
    data = load_data()
    book = data.setdefault("paper_options", {
        "note": "PAPER ONLY - pretend trades, never placed. Executor must ignore.",
        "started": datetime.now(ET).date().isoformat(), "open": None, "closed": [],
    })
    today = datetime.now(ET).date()
    signal_date = (data.get("briefing") or {}).get("signal_date")

    pos = book.get("open")
    if pos:
        reason = mark(pos, today)
        if reason:
            exit_px = pos["last_bid"]
            pos.update(closed=today.isoformat(), exit=exit_px, reason=reason,
                       pnl=round((exit_px - pos["entry"]) * 100, 2))
            book["closed"].append(pos)
            book["open"] = None
            print(f"  PAPER EXIT {pos['ticker']} {pos['strike']}C: {reason}, {pos['pnl']:+.2f}")
        else:
            print(f"  paper {pos['ticker']} {pos['strike']}C: {pos.get('ret_pct')}%")

    if book["open"] is None and signal_date and book.get("last_entry_signal") != signal_date:
        book["last_entry_signal"] = signal_date
        new = try_entry(data, today)
        if new:
            book["open"] = new
            print(f"  PAPER ENTRY {new['ticker']} {new['strike']}C {new['expiry']} @ {new['entry']} (${new['cost']})")

    closed = book["closed"]
    book["summary"] = {
        "trades": len(closed),
        "wins": sum(1 for t in closed if t["pnl"] > 0),
        "total_pnl": round(sum(t["pnl"] for t in closed), 2),
        "avg_ret_pct": round(sum(t["exit"] / t["entry"] - 1 for t in closed) / len(closed) * 100, 1)
                       if closed else None,
    }
    write_data(data)


if __name__ == "__main__":
    main()
