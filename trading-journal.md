# Stock Trading Journal

## Goal
Grow $500 → $1,500 in 6 weeks (by ~June 20, 2026)
- Target: 3x return (200% gain)
- Platform: Robinhood
- Strategy: Short-dated options on high-momentum stocks near 52-week high

## Milestones
| Week | Target Balance | Status |
|------|---------------|--------|
| Week 1-2 | $850 | Pending |
| Week 3-4 | $1,250 | Pending |
| Week 5-6 | $1,500 | Pending |

## Trading Rules
- Max 2 trades open at once
- Never put more than $250 in one trade
- Cut losses at -40%
- Take profits at +80-100%
- Buy ATM or slightly OTM calls, 2-3 weeks out
- Enter early in week, exit by Thursday before expiry week
- Avoid 0DTE and cheap lottery options ($0.05-0.10)
- Check WTI crude before any energy trade — skip if below $84

## Tools
- `screener.py` — runs every Monday morning, scans 28 tickers for 52w high proximity + affordable calls
- Trigger: `.venv/bin/python screener.py`

---

## Active Trades

### Trade #1 — SLB $57 Call 6/12
| Field | Value |
|-------|-------|
| Status | **ACTIVE ✅** |
| Entry date | June 2, 2026 |
| Ticker | SLB (Schlumberger) |
| Direction | CALL |
| Strike | $57.00 |
| Expiry | June 12, 2026 |
| Contracts | 2 |
| Entry premium | $0.89/share |
| Total cost | $178.08 |
| Breakeven | $57.89 (+3.84% from entry stock price ~$55.75) |
| Target exit | $1.69/share (~90% gain, +$160 profit) |
| Stop loss | $0.53/share (-40%, -$71 loss) |
| Remaining budget | $321.92 (1 trade slot still open) |

**Key dates:**
- June 3 — Ex-dividend date (expect minor dip ~$0.30, hold through it)
- June 9 — Exit by EOD regardless (theta accelerates final week)
- June 12 — Expiry

**Robinhood alerts to set:**
- SLB stock > $57.00 → consider taking profit
- SLB stock < $53.00 → review stop loss

---

## Trade Log
| Date | Ticker | Direction | Strike | Expiry | Entry | Exit | P&L | Notes |
|------|--------|-----------|--------|--------|-------|------|-----|-------|
| Jun 2 | SLB | CALL | $57 | Jun 12 | $0.89 | — | +$46 (open) | 2 contracts, $178.08 total. EOD: $1.12/share, stock $56.51, +25.84% day 1 |

---

## Weekly Market Notes

### Week 1 (May 10, 2026)
- Starting balance: $500
- Market conditions: [see research below]
- Watchlist: TBD

### Week 3 (May 30, 2026)
- Balance: $500 (no trades filled yet)
- Oil: WTI below $88, down 16.2% in May — US/Iran ceasefire easing supply pressure
- Energy sector still +22% YTD despite recent oil pullback
- 12 stocks found within 8% of 52-week high via screener
- Only SLB fit budget + expiry criteria with affordable options

---

## Research & Signals

### May 30, 2026 — SLB Due Diligence

**Setup:**
- SLB at $54.60, 52-week high $58.82 (7.26% away)
- Volume: 2.46x 20-day average on Friday (institutional interest)
- Sector: Energy (oilfield services)

**Analyst Targets (Bullish):**
| Bank | Target | Date |
|------|--------|------|
| Bernstein | $71 | May 9 |
| BofA | $60 | May 12 |

**Earnings:** Q2 report July 24, 2026 — AFTER expiry. No earnings risk. ✅

**Recent news:**
- Raised $2B in senior notes (May 7) — financial strength
- Won 2 OTC technology awards (May 1)
- Var Energi digital partnership (May 19)
- Director sold 2,000 shares May 7 — minor, not alarming

**Risks:**
- WTI crude down 16% in May — main headwind for oilfield services
- Ex-dividend June 3 (minor stock dip expected)
- Needs +5.7% move to breakeven by June 12

**Verdict:** Sound trade with clear analyst support and no earnings overhang. Main risk is continued oil weakness.

---

### May 10, 2026 — Week 1 Scan

**Macro Picture:**
- S&P 500 target: 8,100 (15% upside from end of 2025)
- Sector rotation underway: Industrials (+16% YTD), Energy (+22% YTD) leading
- Tech faltering broadly but select names still strong
- Rate cuts pushed to 2027 (inflation/oil impact) — no Fed tailwind
- Favorable sectors: Industrials, Healthcare, Communication Services

**Top Momentum Tickers on Radar:**
| Ticker | Sector | Why Interesting |
|--------|--------|----------------|
| SOUN | AI/Tech | High momentum, breakout signals, cheaper options |
| AMD | Semiconductor | Strong volume, EMA alignment |
| MPC | Energy | Momentum play, energy sector +22% YTD |
| AAPL | Tech | Steady momentum, liquid options |
| CAT | Industrials | Sector leader, strong earnings tailwind |

---
*Started: May 10, 2026 | Last updated: May 30, 2026*
