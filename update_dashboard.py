"""
Dashboard updater — run after screener.py or standalone.
Fetches live position values + writes web/data.json + git pushes.
"""

import yfinance as yf
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

DATA_FILE = Path(__file__).parent / "web" / "data.json"


def load_data():
    with open(DATA_FILE) as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def update_positions(data):
    """Mark open share positions at the latest close (display only — the executor
    uses live Robinhood prices for exits)."""
    for pos in data.get("positions", []):
        if pos.get("status") != "open":
            continue
        try:
            hist = yf.Ticker(pos["ticker"]).history(period="5d")
            if hist.empty:
                continue
            last = round(float(hist["Close"].iloc[-1]), 2)
            value = round(last * float(pos["quantity"]), 2)
            pnl = round(value - pos["entry_cost"], 2)
            pos["last_price"] = last
            pos["current_value"] = value
            pos["pnl"] = pnl
            pos["pnl_pct"] = round(pnl / pos["entry_cost"] * 100, 1)
            print(f"  {pos['ticker']} x{pos['quantity']}: ${last} | P&L: ${pnl:+.2f} ({pos['pnl_pct']:+.1f}%)")
        except Exception as e:
            print(f"  Warning: could not update {pos.get('ticker')} — {e}")

    return data


def update_oil(data):
    """Refresh WTI crude price."""
    try:
        oil = yf.Ticker("CL=F")
        hist = oil.history(period="5d")
        wti = round(float(hist["Close"].iloc[-1]), 2)
        data["oil_price"] = wti
        data["oil_status"] = "ok" if wti >= 84 else "warn"
        print(f"  WTI Crude: ${wti}")
    except Exception as e:
        print(f"  Warning: could not fetch oil price — {e}")
    return data


def update_portfolio(data):
    """Recalculate committed capital. Leave current_balance and available unchanged
    — those are set manually to reflect actual Robinhood buying power."""
    open_positions = [p for p in data.get("positions", []) if p["status"] == "open"]
    committed = sum(p["entry_cost"] for p in open_positions)
    data["portfolio"]["committed"] = round(committed, 2)
    # Do NOT auto-calculate available — Robinhood settlement affects it
    return data


def compute_stats(data):
    """Compute win rate, avg return, and per-ticker breakdown from trade_history."""
    history = [t for t in data.get("trade_history", []) if t.get("status") == "closed"]
    if not history:
        data["stats"] = {"trades": 0, "note": "No closed trades yet."}
        return data

    wins   = [t for t in history if t.get("pnl", 0) > 0]
    losses = [t for t in history if t.get("pnl", 0) <= 0]

    by_ticker = {}
    for t in history:
        tk = t["ticker"]
        if tk not in by_ticker:
            by_ticker[tk] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
        by_ticker[tk]["trades"] += 1
        by_ticker[tk]["total_pnl"] = round(by_ticker[tk]["total_pnl"] + t.get("pnl", 0), 2)
        if t.get("pnl", 0) > 0:
            by_ticker[tk]["wins"] += 1

    data["stats"] = {
        "trades":       len(history),
        "wins":         len(wins),
        "losses":       len(losses),
        "win_rate_pct": round(len(wins) / len(history) * 100, 1),
        "avg_return_pct": round(
            sum(t.get("pnl_pct", 0) for t in history) / len(history), 1
        ),
        "avg_winner_pct": round(
            sum(t.get("pnl_pct", 0) for t in wins) / len(wins), 1
        ) if wins else None,
        "avg_loser_pct": round(
            sum(t.get("pnl_pct", 0) for t in losses) / len(losses), 1
        ) if losses else None,
        "total_realized_pnl": round(sum(t.get("pnl", 0) for t in history), 2),
        "by_ticker": by_ticker,
        "last_computed": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return data


def set_timestamp(data):
    data["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return data


def git_push():
    """Commit and push data.json to GitHub so Vercel redeploys."""
    if os.environ.get("GITHUB_ACTIONS"):
        print("  CI run — the workflow's commit step pushes (with rebase retry).")
        return
    try:
        subprocess.run(["git", "add", "web/data.json"], check=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if result.returncode == 0:
            print("  No changes to push.")
            return
        subprocess.run(["git", "commit", "-m", f"dashboard update {datetime.now().strftime('%Y-%m-%d %H:%M')}"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("  ✅ Pushed to GitHub — Vercel deploying now.")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠️  Git push failed: {e}")


def main():
    print("\n── Updating Dashboard ───────────────────────────────")
    data = load_data()
    print("\nPositions:")
    data = update_positions(data)
    print("\nMarket:")
    data = update_oil(data)
    data = update_portfolio(data)
    print("\nStats:")
    data = compute_stats(data)
    win_rate = data["stats"].get("win_rate_pct", "N/A")
    avg_ret  = data["stats"].get("avg_return_pct", "N/A")
    print(f"  {data['stats']['trades']} trades | Win rate: {win_rate}% | Avg return: {avg_ret}%")
    data = set_timestamp(data)
    save_data(data)
    print("\nPushing to GitHub...")
    git_push()
    print("\n✅ Dashboard updated.\n")


if __name__ == "__main__":
    main()
