"""
VWAP + Supertrend strategy — default parameters.

Chosen from walk-forward backtest (2 x 60-day XAUUSD windows, $5k, 2% risk):
  ST(10, 3.0) @ 1:2 RR  → recent +75.0% (PF 1.73) · older +57.2% (PF 1.72)
  ST(14, 3.0) @ 1:2 RR  → recent +63.0% (PF 1.70) · older +47.7% (PF 1.60)

We default to ST(10, 3.0) 1:2 — highest expectancy and consistent across regimes.
"""
from __future__ import annotations

# ── Signal / indicator ────────────────────────────────────────────────────
ENTRY_GRANULARITY = "M5"
SUPERTREND_PERIOD = 10
SUPERTREND_MULTIPLIER = 3.0
VWAP_ANCHOR = "day"           # daily-anchored VWAP (resets 00:00 UTC)

# ── Risk / exits (ATR multipliers on M5 ATR14) ────────────────────────────
STOP_LOSS_ATR_MULTIPLIER = 1.5
TAKE_PROFIT_ATR_MULTIPLIER = 3.0   # 1:2 RR — the winning config
DEFAULT_MAX_HOLD_BARS = 12         # ~1 hour on M5

# ── Session gate (UTC) ────────────────────────────────────────────────────
SESSION_START_HOUR = 12
SESSION_END_HOUR = 21          # London-NY overlap + NY (same as RSI EMA)

# ── Backtest / execution cost model ───────────────────────────────────────
DEFAULT_STARTING_BALANCE = 5_000.0
DEFAULT_RISK_PER_TRADE = 0.02
SLIPPAGE_POINTS = 0.05
COMMISSION_RATE = 0.000001

# ── Data-fetch (mirrors backtest_forex_engine) ────────────────────────────
DEFAULT_WARMUP_DAYS = 30
DEFAULT_LOOKBACK_DAYS = 60
