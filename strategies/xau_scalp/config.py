"""
XAU SCALP STRATEGY — ICT / SMC configuration

The infrastructure around this strategy stays the same. This file only updates
the internal scalp logic parameters while keeping the existing public strategy
identity and risk-management knobs available to the rest of the app.
"""

STRATEGY_NAME         = "XAU SCALP STRATEGY"
STRATEGY_ID           = "xau_scalp"
INSTRUMENT            = "XAU_USD"
PRIMARY_TIMEFRAME     = "M1"
CONFIRMATION_TF       = "M5"
BIAS_TIMEFRAME        = "M15"
TIMEFRAME             = PRIMARY_TIMEFRAME

CANDLE_LOOKBACK_M1    = 100
CANDLE_LOOKBACK_M5    = 100
CANDLE_LOOKBACK_M15   = 50
CANDLE_LOOKBACK       = CANDLE_LOOKBACK_M1

# ── Market structure ──────────────────────────────────────────────────────
STRUCTURE_LOOKBACK     = 30
SWING_PIVOT_BARS       = 5
MIN_STRUCTURE_MOVE_PCT = 0.08
CHoCH_SENSITIVITY      = 3

# ── Order blocks ──────────────────────────────────────────────────────────
OB_LOOKBACK            = 50
OB_MIN_IMPULSE_PCT     = 0.10
OB_MAX_TESTS           = 2
OB_BUFFER_PRICE        = 0.50
OB_VALID_CANDLE_BODY   = 0.60

# ── Fair value gaps ───────────────────────────────────────────────────────
FVG_MIN_SIZE_PRICE     = 0.30
FVG_MAX_AGE_CANDLES    = 50
FVG_ENTRY_PCT          = 0.50
FVG_MITIGATION_PCT     = 0.75

# ── Liquidity ─────────────────────────────────────────────────────────────
SSL_LOOKBACK           = 20
BSL_LOOKBACK           = 20
SWEEP_MIN_PIPS         = 1
SWEEP_MAX_PIPS         = 15
EQUAL_LEVEL_TOLERANCE  = 0.20
MIN_LIQUIDITY_TESTS    = 2

# ── Displacement ──────────────────────────────────────────────────────────
DISPLACEMENT_BODY_PCT  = 0.58          # was 0.75 — relaxed for M1 Gold
DISPLACEMENT_CLOSE_PCT = 0.65          # was 0.85 — relaxed for M1 Gold
DISPLACEMENT_MIN_SIZE  = 0.40          # was 1.00 — min candle range in price
DISPLACEMENT_FVG_REQ   = True

# ── CISD ──────────────────────────────────────────────────────────────────
CISD_LOOKBACK          = 5
CISD_CONFIRMATION_BARS = 1

# ── Session filter ────────────────────────────────────────────────────────
TRADE_LONDON_SESSION   = True
TRADE_NEWYORK_SESSION  = True
TRADE_ASIAN_SESSION    = False
LONDON_OPEN_UTC        = 8
LONDON_CLOSE_UTC       = 16
NY_OPEN_UTC            = 13
NY_CLOSE_UTC           = 21
OVERLAP_BONUS          = True

# ── Risk management (existing knobs retained) ────────────────────────────
ATR_PERIOD             = 14
ATR_SL_MULTIPLIER      = 1.0
MIN_RR_RATIO           = 2.0
TARGET_RR_RATIO        = 3.0
BREAKEVEN_TRIGGER_R    = 1.0
SL_BELOW_SWEEP_BUFFER  = 0.50
TP1_AT_PREV_HIGH       = True
TP2_AT_BSL             = True
TP1_CLOSE_PCT          = 0.50
MAX_TRADES_PER_SESSION = 3
SIGNAL_COOLDOWN_MS     = 300_000
MAX_TRADES_PER_HOUR    = 3

# ── Legacy / compatibility knobs kept for existing callers ───────────────
STRONG_SIGNAL_MIN        = 9
MEDIUM_SIGNAL_MIN        = 6
TREND_LOOKBACK           = STRUCTURE_LOOKBACK
DEMAND_ZONE_LOOKBACK     = OB_LOOKBACK
MIN_ZONE_IMPULSE_PCT     = OB_MIN_IMPULSE_PCT
ZONE_BUFFER_PIPS         = int(OB_BUFFER_PRICE / 0.10)
SWING_LOW_LOOKBACK       = SSL_LOOKBACK
MAX_SWEEP_DEPTH_PIPS     = SWEEP_MAX_PIPS
MIN_REJECTION_BODY_PCT   = DISPLACEMENT_BODY_PCT
MIN_UPPER_WICK_PCT       = 0.10
MUST_CLOSE_ABOVE_ZONE    = True
VOLUME_SPIKE_MULTIPLIER  = 1.5
VOLUME_AVERAGE_PERIOD    = 20
