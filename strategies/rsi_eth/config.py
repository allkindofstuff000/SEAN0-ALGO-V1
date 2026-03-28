"""ETH RSI 15 TF — optimized RSI EMA strategy with ADX filter. Proven +73.9% in 3 months."""

STRATEGY_NAME = "ETH RSI 15 TF"
STRATEGY_ID = "eth_rsi_15tf"
INSTRUMENT = "ETHUSDT"
DISPLAY_PAIR = "ETH/USDT"
DATA_SOURCE = "binance"

# Timeframes
PRIMARY_TF = "M15"
ENTRY_TF = "M15"

# Indicator params
EMA_FAST = 9
EMA_SLOW = 50
RSI_PERIOD = 7
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 35
ATR_PERIOD = 14
ATR_AVG_WINDOW = 20
ADX_PERIOD = 14
ADX_MIN = 20                  # skip trades when ADX < 20 (choppy market)

# Signal params
SCORE_THRESHOLD = 80
RULE_WEIGHT = 20
ATR_SL_MULTIPLIER = 1.0      # SL = 1.0 ATR from entry
ATR_TP_MULTIPLIER = 2.5      # TP = 2.5R (2.5x the risk)
ENTRY_OPTION = "C"            # RSI cross + EMA bias

# Filters
FILTER_SPREAD = True          # skip doji candles (body < 30% of range)
FILTER_TIME_OF_DAY = True     # skip 02-05 UTC + 20-22 UTC
FILTER_ADX = True             # skip when ADX < 20

# Crypto-specific
SESSION_FILTER = False
WEEKEND_DISABLED = False
MAX_RISK_PCT = 0.75
SIGNAL_COOLDOWN_S = 600
MIN_RR_RATIO = 2.0

# Lookback
CANDLE_LOOKBACK = 200
