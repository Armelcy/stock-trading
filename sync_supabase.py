"""
Sync today's screener picks from web/data.json to Supabase screener_picks table.
Run after screener.py. Requires env vars:
  SUPABASE_URL  — e.g. https://zouyaaeincpprkdkofgf.supabase.co
  SUPABASE_KEY  — service role key (stored as GH secret SUPABASE_SERVICE_KEY)
"""

import json
import os
import requests
from datetime import date
from pathlib import Path

DATA_FILE = Path(__file__).parent / "web" / "data.json"

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

TABLE = "screener_picks"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


def rest(method, path, **kwargs):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    r = requests.request(method, url, headers=HEADERS, **kwargs)
    r.raise_for_status()
    return r


def main():
    today = date.today().isoformat()

    with open(DATA_FILE) as f:
        data = json.load(f)

    opps = data.get("opportunities", [])
    briefing = data.get("briefing", {})

    # Delete any existing rows for today (idempotent re-runs)
    rest("DELETE", f"{TABLE}?run_date=eq.{today}")

    if not opps:
        # Insert single "no setups" row
        rows = [{"run_date": today, "briefing": "No setups today — sit in cash."}]
    else:
        headline = briefing.get("headline", f"{len(opps)} setup(s) found today.")
        rows = []
        for opp in opps:
            rows.append({
                "run_date": today,
                "ticker":    opp["ticker"],
                "price":     opp["price"],
                "strike":    opp["strike"],
                "expiry":    opp["expiry"],
                "premium":   opp["premium"],
                "contracts": opp["contracts"],
                "cost":      opp["cost"],
                "target":    opp["target"],
                "stop":      opp["stop"],
                "briefing":  headline,
            })

    rest("POST", TABLE, json=rows)
    print(f"  Supabase sync: {len(rows)} row(s) written for {today}")


if __name__ == "__main__":
    main()
