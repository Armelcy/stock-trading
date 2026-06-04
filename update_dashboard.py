"""
Dashboard updater — run after screener.py or standalone.
Fetches live position values + writes web/data.json + git pushes.
"""

import yfinance as yf
import json
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
    """Refresh current_premium and P&L for all open positions."""
    for pos in data.get("positions", []):
        if pos["status"] != "open":
            continue
        try:
            ticker = yf.Ticker(pos["ticker"])
            exp = pos["expiry"]
            strike = float(pos["strike"])
            chain = ticker.option_chain(exp)
            calls = chain.calls
            row = calls[calls["strike"] == strike]
            if row.empty:
                continue
            current = float(row.iloc[0]["lastPrice"])
            contracts = pos["contracts"]
            entry_cost = pos["entry_cost"]
            current_value = round(current * contracts * 100, 2)
            pnl = round(current_value - entry_cost, 2)
            pnl_pct = round((pnl / entry_cost) * 100, 1)

            pos["current_premium"] = current
            pos["current_value"] = current_value
            pos["pnl"] = pnl
            pos["pnl_pct"] = pnl_pct
            print(f"  {pos['ticker']} ${pos['strike']}C: ${current:.2f}/sh | P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)")
        except Exception as e:
            print(f"  Warning: could not update {pos['ticker']} — {e}")

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
    """Recalculate committed and available capital."""
    open_positions = [p for p in data.get("positions", []) if p["status"] == "open"]
    committed = sum(p["entry_cost"] for p in open_positions)
    data["portfolio"]["committed"] = round(committed, 2)
    data["portfolio"]["available"] = round(data["portfolio"]["current_balance"] - committed, 2)
    return data


def set_timestamp(data):
    data["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return data


def git_push():
    """Commit and push data.json to GitHub so Vercel redeploys."""
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
    data = set_timestamp(data)
    save_data(data)
    print("\nPushing to GitHub...")
    git_push()
    print("\n✅ Dashboard updated.\n")


if __name__ == "__main__":
    main()
