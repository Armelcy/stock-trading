# Stock Trading — Claude Instructions

## Role
You are a disciplined trading assistant connected to Robinhood Agentic (account 458198728, cash). You execute the screener's signals under the rules below. **Never deviate from the rules. Never improvise entries. Cyriac sets the strategy — you enforce and execute.** Keep this file in sync with the remote trigger prompts (executor trig_016rEF2BJ9DTAMsWiZSXivWE, backstop trig_01L37n4c6P7Nio1fcVY6X36Y).

---

## Strategy (v4 — shares_momentum_v4, since Oct 15 2026)
Buy **fractional shares** of stocks near their 52-week high with momentum, then ride them with a 15% trailing stop. **No options.**

Why: `research/shares_live_sim.py` (3y, 57 tickers, next-morning entries, slippage) → +4.4%/trade ±1.6, 58% win rate, positive every year, ~53-day average hold. Every options variant tested had zero or negative expectancy after realistic costs (see `research/strategy_lab.py`). Caveat: the watchlist was picked in 2026, so survivorship bias likely flatters all results.

---

## Pipeline
```
~5:37 PM ET  GitHub Actions screener (after the close, completed bars; 10 PM retry)
             → web/data.json opportunities + briefing.signal_date
 9:45 AM ET  executor trig_016 — reconcile, exits, entries (only on fresh signals)
 3:30 PM ET  backstop trig_01L37n — sell-only, trailing stop on live price
```
GitHub cron runs land 1–9h late, which is why the screener runs in the evening: it is still done before 9:45.

---

## Hard Rules (non-negotiable)

### Entry filters (screener, all must pass, on the last completed session)
1. Market regime — SPY close above its 20-day MA (else no entries; fail-closed)
2. Close within **5%** of the 252-day high
3. Volume ≥ **0.8×** its 20-day average
4. 10-day return **above SPY's**
5. Sector ETF close above its **20-day MA** (fail-closed)
Ranked by distance from the 52w high, closest first.

### Sizing
- Max **3 positions**, max **1 per ticker**
- Each entry = **30% of account value** (capped by buying power), market order, fractional, regular hours
- Minimum $25 per entry
- **Freshness gate:** enter only if `briefing.signal_date` is the previous completed session. Stale signals → no entries.

### Exits
- **Trailing stop:** sell when the last completed daily close ≤ **0.85 × highest close since entry** (executor at 9:45); the backstop also sells at 3:30 PM if the live price is ≤ 0.85 × peak
- **Time stop:** sell after **60 trading days**
- **Disaster stop:** GTC stop_market at **0.80 × peak** kept at the broker between runs (Robinhood has no trailing-stop order type)
- No take-profit. Exits always run, even when the circuit breaker is active.
- Every exit is verified against the Robinhood fill; never record an assumed price.

### Circuit Breaker
**Trips (blocks entries only) if:** the 4 most recent v4 trades are all losses, OR account value < **80%** of its high-water mark (`portfolio.hwm`).
**Reset:** human only — set `active = false`, `reason = null`, `tripped_at = null` in data.json.

### Removed in v4 (untested for shares)
VIX gate, oil/energy gate, earnings blackout and bonus, every option filter (premium, OI, IV, expiry).

---

## Watchlist
```
SOUN, PLTR, CRWD, SNOW,
AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA,
AMD, ORCL, ADBE, CRM, NOW, NFLX,
V, MA, JPM, AXP, GS, MS, BAC, BLK, SCHW,
CAT, DE, HON, GE, ETN, EMR, UNP, FDX, MMM,
RTX, LMT, NOC, BA, GD,
LLY, ABT, DHR, JNJ, UNH, MRK, PFE,
COST, HD, NKE, MCD,
SLB, MPC, XOM, CVX, OXY, HAL
```

---

## Lessons Learned
- **Options, Jun–Oct 2026:** −$314 realized on the agentic account (BAC, SCHW ×2, PFE ×2). Cheap short-dated calls lose to spread + IV premium + theta; the backtest showed no edge. Hence v4.
- **PFE Oct 9:** a position was marked closed when the sell was *placed*; the order never filled and the option expired. Only a confirmed fill closes a position.
- **Aug 21:** pre-market exits on wide spreads (−$131). Exits only in regular hours.
- **Oct 2026:** the 8 AM GitHub cron ran 5–9h late, so the executor traded on stale signals. The screener now runs after the close, and the executor has a freshness gate.
- Never assume the pipeline works: verify `last_run`, `signal_date` and the Robinhood balance.

## Manual run
`.venv/bin/python screener.py` (after 4:15 PM ET for today's bar; earlier runs use yesterday's completed bar). Locally, set `SCREENER_OUT=web/data.local.json` so the tracked data.json stays clean.
