"""Centralized configuration for the modular trading signal engine."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")
RUNTIME_DIR = ROOT_DIR / "bot_runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _parse_int_bounds(raw: str | None, default: tuple[int, int]) -> tuple[int, int]:
    if not raw:
        return default
    values = [part.strip() for part in raw.split(",") if part.strip()]
    if len(values) != 2:
        return default
    return int(values[0]), int(values[1])


def _parse_float_bounds(raw: str | None, default: tuple[float, float]) -> tuple[float, float]:
    if not raw:
        return default
    values = [part.strip() for part in raw.split(",") if part.strip()]
    if len(values) != 2:
        return default
    return float(values[0]), float(values[1])

SYMBOL = "XAUUSDT"
TIMEFRAMES = ["1m", "5m", "15m", "1h"]
CANDLE_LIMIT = 300
DATA_REFRESH_SECONDS = 30
EXCHANGE = "binance"

MAX_SIGNALS_PER_DAY = 3
COOLDOWN_CANDLES = 3
LOSS_STREAK_LIMIT = 2

PRIMARY_TIMEFRAME = "15m"
MINIMUM_CANDLES = 300
SIGNAL_SCORE_THRESHOLD = 70

SESSION_START_UTC = "00:00"
SESSION_END_UTC = "23:59"

SIGNAL_MODE = os.getenv("SIGNAL_MODE", "BINARY").strip().upper()
BINARY_EXPIRY = os.getenv("BINARY_EXPIRY", "30m").strip()
FOREX_STOP_LOSS_ATR_MULTIPLIER = float(os.getenv("FOREX_STOP_LOSS_ATR_MULTIPLIER", "1.5"))
FOREX_TAKE_PROFIT_ATR_MULTIPLIER = float(os.getenv("FOREX_TAKE_PROFIT_ATR_MULTIPLIER", "2.5"))
FOREX_MAX_HOLDING_MINUTES = int(os.getenv("FOREX_MAX_HOLDING_MINUTES", "240"))
LEARNING_WINDOW_TRADES = int(os.getenv("LEARNING_WINDOW_TRADES", "100"))
MIN_DYNAMIC_THRESHOLD = int(os.getenv("MIN_DYNAMIC_THRESHOLD", "60"))
MAX_DYNAMIC_THRESHOLD = int(os.getenv("MAX_DYNAMIC_THRESHOLD", "85"))

ENGINE_STATE_PATH = RUNTIME_DIR / "engine_state.json"
SIGNAL_HISTORY_PATH = RUNTIME_DIR / "signals_history.csv"
PERFORMANCE_PATH = RUNTIME_DIR / "performance.json"
PENDING_TRADES_PATH = RUNTIME_DIR / "pending_trades.json"
THRESHOLD_STATE_PATH = RUNTIME_DIR / "threshold_state.json"
TRADE_LOG_PATH = DATA_DIR / "trade_log.csv"
THRESHOLD_HISTORY_PATH = DATA_DIR / "threshold_history.csv"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BACKTEST_MODE = os.getenv("BACKTEST_MODE", "true").strip().lower() == "true"
BACKTEST_SYMBOL = os.getenv("BACKTEST_SYMBOL", "XAUUSDT").strip() or SYMBOL
BACKTEST_TIMEFRAME = os.getenv("BACKTEST_TIMEFRAME", PRIMARY_TIMEFRAME).strip() or PRIMARY_TIMEFRAME
BACKTEST_START_DATE = os.getenv(
    "BACKTEST_START_DATE",
    (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d"),
).strip()
BACKTEST_END_DATE = os.getenv(
    "BACKTEST_END_DATE",
    datetime.now(timezone.utc).strftime("%Y-%m-%d"),
).strip()
BACKTEST_CSV_PATH = os.getenv("BACKTEST_CSV_PATH", "").strip() or None
BACKTEST_INITIAL_CAPITAL = float(os.getenv("BACKTEST_INITIAL_CAPITAL", "10000"))
BACKTEST_RISK_PERCENTAGE = float(os.getenv("BACKTEST_RISK_PERCENTAGE", "1.0"))
BACKTEST_BINARY_PAYOUT = float(os.getenv("BACKTEST_BINARY_PAYOUT", "0.8"))
BACKTEST_OUTPUT_DIR = ROOT_DIR / "data" / "backtests"
BACKTEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

WFO_TRAINING_DAYS = int(os.getenv("WFO_TRAINING_DAYS", "90"))
WFO_TEST_DAYS = int(os.getenv("WFO_TEST_DAYS", "30"))
WFO_STEP_DAYS = int(os.getenv("WFO_STEP_DAYS", str(WFO_TEST_DAYS)))
PARAM_THRESHOLD_RANGE = _parse_int_bounds(os.getenv("PARAM_THRESHOLD_RANGE"), (60, 85))
PARAM_THRESHOLD_STEP = int(os.getenv("PARAM_THRESHOLD_STEP", "5"))
PARAM_ATR_RANGE = _parse_float_bounds(os.getenv("PARAM_ATR_RANGE"), (1.0, 2.5))
PARAM_ATR_STEP = float(os.getenv("PARAM_ATR_STEP", "0.5"))
WFO_OUTPUT_DIR = ROOT_DIR / "data" / "wfo"
WFO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
