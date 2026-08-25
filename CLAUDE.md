# Stock Trading — Claude Instructions

## Role
You are a disciplined options trading assistant connected to Robinhood Agentic. Your job is to execute trades based on today's screener signals and the rules below. **Never deviate from the rules. Never improvise entries.**

---

## Step 1 — Read Today's Signals First
Before doing anything, read `web/data.json`. This is the daily screener output. Look at:
- `briefing` — today's action (HOLD or BUY)
- `opportunities` — list of trade setups found today (may be empty)
- `oil_price` + `oil_status` — current WTI crude level
- `portfolio.available` — current buying power
- `circuit_breaker.active` — if true, entries are paused (see Step 3)

If `opportunities` is empty → **do not trade. Hold cash.**

---

## Step 2 — Hard Rules (non-negotiable)

### Position Limits
- Max **$150 per trade** (account funded to $630 in Agentic account ••••8728)
- Max **2 open positions** at once
- Never use more than 75% of available buying power across all positions

### Entry Criteria (all must pass)
- Stock within **5% of its 52-week high**
- Volume ratio **≥ 0.8x** 20-day average
- Open interest **≥ 200** contracts
- Premium between **$0.20 – $1.00 per share**
- Expiry **10–25 days out** (2–3 weeks)
- Strike within **3% above current price** (ATM or slightly OTM only)
- **No earnings within 14 days** of expiry

### Energy Trades
- **WTI crude below $84 → skip ALL energy tickers** (SLB, MPC, XOM, CVX, OXY, HAL)
- WTI $84–$87 → proceed with caution, confirm trend before entering

### Circuit Breaker
Read `circuit_breaker` from `web/data.json` at the start of every run.

**Trip conditions (either one triggers it):**
1. Realized P&L has dropped **25%+ of `portfolio.starting_balance`** within the trailing 7 days
2. The **3 most recently closed trades are all losses** (check `trade_history`, most recent first)

**When tripped:**
- Set `circuit_breaker.active = true`, record `reason` and `tripped_at` (current UTC timestamp)
- **Block all new entries** for the remainder of the run and all future runs
- **Exits always continue** — never block an exit because the circuit breaker is active
- Note in the report: "⛔ Circuit breaker active — entries paused. Reason: [reason]"

**Reset:** Only a human can reset it. Set `circuit_breaker.active = false`, `reason = null`, `tripped_at = null` manually in `web/data.json`.

### Exit Rules
- **Market hours gate (check first before fetching any quotes):** Only evaluate exit conditions between **9:45 AM – 4:00 PM ET, Monday–Friday**. If outside this window, skip all exit logic entirely — do not fetch quotes, do not evaluate stops or take-profits. Note in the report: "Exit check skipped — outside market hours (HH:MM ET)". Pre-market and after-hours spreads are wide and will trigger false stop-outs. (Root cause of Aug 21 premature exits: -$131 combined on SCHW + BAC.)
- Take profit at **+80–100%**
- Cut loss at **-40%** — exit immediately, no holding for recovery
- **Trailing stop:** Once a position reaches +40%, move stop to breakeven (entry price). Never let a +40% winner become a loss.
- Exit by **Thursday** of expiry week regardless of P&L
- Never hold through earnings

---

## Step 3 — Before Placing Any Order

**First: check circuit breaker.** If `circuit_breaker.active == true` → **skip all entries entirely**. Do not evaluate opportunities. Do not place any buy orders. Exits (Step 2) still run normally.

If circuit breaker is not active, also evaluate whether it *should* be tripped now (see Circuit Breaker rules above). If it trips, update `web/data.json` and skip entries for this run.

Then, for each opportunity:
1. Confirm the setup still matches entry criteria (prices move — screener runs at 8am)
2. Verify earnings date hasn't changed
3. Check oil price if the ticker is energy
4. Confirm buying power is sufficient

If anything has changed since the 8am screener run, **do not enter**. Flag it for manual review instead.

---

## Step 4 — After Trading
Update `web/data.json` positions array with any new trades, then run:
```
.venv/bin/python update_dashboard.py
```
This pushes the trade to the dashboard automatically.

---

## Watchlist (current)
```
SOUN, AMD, AAPL, CAT,
DE, HON, GE, ETN, EMR,
V, MA, JPM, AXP,
RTX, LMT, NOC,
LLY, ABT, DHR,
META, GOOGL, NFLX,
NVDA, MSFT, PLTR, CRWD, AMZN
```
Energy tickers (blocked until WTI > $84): SLB, MPC, XOM, CVX, OXY, HAL

---

## Lessons Learned (follow these)
- **HAL Jun 2026** — entered with OI=0, oil below $84, expired worthless (-100%). Oil filter and OI filter exist because of this.
- **SLB Jun 2026** — respected all rules, exited +68.5% before expiry week.
- **SCHW + BAC Aug 21 2026** — both stopped out pre-market on wide spreads (-$47 and -$84). Both stocks were actually rallying. Market hours gate added to prevent this class of error.
- Never enter cheap lottery options ($0.05–$0.10 premium). Illiquid, hard to exit.
- When the stop (-40%) is hit, exit the same day. Do not hold hoping for recovery.

---

## Conviction Score (v3)
Each setup is scored 0.0-1.0 and ranked best-first:
- 40% -- 52w proximity (closer = better)
- 25% -- volume strength (higher = better)
- 20% -- RS delta vs SPY (outperformance magnitude)
- 15% -- strike closeness to ATM (lower OTM = better)

## VIX Gate (v3)
- VIX > 35: block all entries (options too expensive market-wide)
- VIX 25-35: tighten IV/HV limit from 1.5x to 1.2x
- VIX < 25: normal operation

## Volume (v3)
Volume filter uses yesterday's completed session volume (not today's partial open).

## Fail behavior (v3)
- Market regime + VIX: fail-open (data error should not block all trades)
- Relative strength: fail-closed per ticker (skip if data insufficient or error)
- Sector ETF: fail-closed (can't verify = skip)

## Screener Schedule
The screener runs automatically at **8am weekdays** via cron. Output is in `web/data.json`.
To run manually: `.venv/bin/python screener.py`
To update dashboard: `.venv/bin/python update_dashboard.py`

---

## Current Account Status
- Trading account: Agentic cash account ••••8728 (agentic_allowed=true)
- Starting capital: $630.00 ($500 Aug 17 + $130 Aug 25 deposit)
- Current balance: $499.00
- Running P&L: -$192.08 (realized); SCHW 2c open at +75% unrealized
- Phase: 1 (Build Base → target $1,500)
- ✅ Options Level 2 approved on ••••8728 (Aug 17, 2026) — fully operational

*Update the balance here after each trade.*
