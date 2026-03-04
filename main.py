from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ContextTypes

from data_fetcher import DataFetcher
from indicator_engine import IndicatorEngine
from risk_manager import RiskManager
from signal_logic import SignalLogic, TradeSignal
from telegram_bot import TelegramSignalBot


@dataclass
class RuntimeState:
    last_15m_close: pd.Timestamp | None = None
    last_1h_close: pd.Timestamp | None = None


@dataclass
class CycleReport:
    cycle_ok: bool = False
    skipped: bool = False
    signal_generated: bool = False
    nan_detected: bool = False
    error_handled: bool = False


REQUIRED_BASE_COLUMNS = {"open", "high", "low", "close", "volume"}
REQUIRED_INDICATOR_COLUMNS = {
    "close",
    "high",
    "low",
    "session_vwap",
    "supertrend_direction",
    "macd_line",
    "macd_signal",
    "macd_hist",
    "atr_10",
    "atr_rolling_mean_20",
}


async def send_signal_with_retry(
    telegram_bot: TelegramSignalBot,
    signal: TradeSignal,
    max_retries: int = 3,
) -> None:
    for attempt in range(1, max_retries + 1):
        try:
            await telegram_bot.send_signal(signal)
            return
        except Exception as error:
            print(f"[Telegram] send attempt {attempt}/{max_retries} failed: {error}")
            if attempt >= max_retries:
                raise
            await asyncio.sleep(2 * attempt)


def sanitize_candles(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    cleaned = df.copy()
    had_nan_or_inf = cleaned.replace([np.inf, -np.inf], np.nan).isna().any().any()
    cleaned = cleaned.replace([np.inf, -np.inf], np.nan)
    cleaned = cleaned.dropna()
    return cleaned, bool(had_nan_or_inf)


def validate_indicator_frame(df: pd.DataFrame) -> tuple[bool, str, bool]:
    missing = REQUIRED_INDICATOR_COLUMNS - set(df.columns)
    if missing:
        return False, f"Missing indicator columns: {sorted(missing)}", False
    if len(df) < 100:
        return False, "Not enough rows after indicator computation (<100).", False

    latest = df[list(REQUIRED_INDICATOR_COLUMNS)].tail(5)
    has_nan = bool(latest.isna().any().any())
    if has_nan:
        return False, "NaN detected in last 5 indicator rows.", True
    return True, "", False


def map_skip_label(reason: str) -> str:
    text = reason.lower()
    if "midpoint" in text:
        return "[Skipped: midpoint filter]"
    if "atr" in text:
        return "[Skipped: insufficient ATR]"
    return f"[Skipped: {reason}]"


async def run_single_cycle(
    *,
    fetcher: DataFetcher,
    indicators: IndicatorEngine,
    signal_logic: SignalLogic,
    risk_manager: RiskManager,
    state: RuntimeState,
    telegram_bot: TelegramSignalBot | None,
    test_mode: bool,
    enforce_new_candle: bool,
) -> CycleReport:
    report = CycleReport()
    try:
        candles_15m_raw, candles_1h_raw = await fetcher.fetch_dual_timeframes()

        if not REQUIRED_BASE_COLUMNS.issubset(candles_15m_raw.columns):
            print("[Skipped: missing OHLCV columns on 15M]")
            report.skipped = True
            return report
        if not REQUIRED_BASE_COLUMNS.issubset(candles_1h_raw.columns):
            print("[Skipped: missing OHLCV columns on 1H]")
            report.skipped = True
            return report

        candles_15m, had_nan_15m = sanitize_candles(candles_15m_raw)
        candles_1h, had_nan_1h = sanitize_candles(candles_1h_raw)
        if had_nan_15m or had_nan_1h:
            report.nan_detected = True

        if len(candles_15m) < 100 or len(candles_1h) < 100:
            print("[Skipped: insufficient cleaned candles]")
            report.skipped = True
            return report

        candles_15m = indicators.add_indicators(candles_15m)
        candles_1h = indicators.add_indicators(candles_1h)

        valid_15m, reason_15m, nan_15m = validate_indicator_frame(candles_15m)
        valid_1h, reason_1h, nan_1h = validate_indicator_frame(candles_1h)
        if nan_15m or nan_1h:
            report.nan_detected = True
        if not valid_15m:
            print(map_skip_label(reason_15m))
            report.skipped = True
            return report
        if not valid_1h:
            print(map_skip_label(reason_1h))
            report.skipped = True
            return report

        latest_15m = candles_15m.index[-1]
        latest_1h = candles_1h.index[-1]

        if state.last_1h_close is None or latest_1h > state.last_1h_close:
            print(f"[Cycle] New 1H closed candle detected at {latest_1h}.")
            state.last_1h_close = latest_1h

        if enforce_new_candle and state.last_15m_close is not None and latest_15m <= state.last_15m_close:
            print("[Skipped: no new closed 15M candle yet]")
            report.skipped = True
            return report

        state.last_15m_close = latest_15m

        signal, evaluation_message = signal_logic.evaluate(candles_15m, candles_1h)
        if signal is None:
            print(map_skip_label(evaluation_message))
            report.skipped = True
            report.cycle_ok = True
            print("[Cycle OK]")
            return report

        atr_now = float(candles_15m["atr_10"].iloc[-1])
        atr_previous = float(candles_15m["atr_10"].iloc[-2])
        allowed, risk_message = risk_manager.can_send_signal(
            signal_time_utc=signal.timestamp_utc,
            atr_now=atr_now,
            atr_previous=atr_previous,
        )
        if not allowed:
            print(map_skip_label(risk_message))
            report.skipped = True
            report.cycle_ok = True
            print("[Cycle OK]")
            return report

        report.signal_generated = True
        print("[Signal generated]")

        if not test_mode and telegram_bot is not None:
            await send_signal_with_retry(telegram_bot, signal)
            risk_manager.log_signal(signal)

        report.cycle_ok = True
        print("[Cycle OK]")
        return report
    except Exception as error:
        report.error_handled = True
        print(f"[Error handled safely] {error}")
        return report


async def market_scan_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    fetcher: DataFetcher = context.application.bot_data["fetcher"]
    indicators: IndicatorEngine = context.application.bot_data["indicators"]
    signal_logic: SignalLogic = context.application.bot_data["signal_logic"]
    risk_manager: RiskManager = context.application.bot_data["risk_manager"]
    telegram_bot: TelegramSignalBot = context.application.bot_data["telegram_bot"]
    state: RuntimeState = context.application.bot_data["state"]
    await run_single_cycle(
        fetcher=fetcher,
        indicators=indicators,
        signal_logic=signal_logic,
        risk_manager=risk_manager,
        state=state,
        telegram_bot=telegram_bot,
        test_mode=False,
        enforce_new_candle=True,
    )


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"[TelegramError] Update={update} Error={context.error}")


def get_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value


def main() -> None:
    load_dotenv()
    test_mode = str(get_env("TEST_MODE", "False")).strip().lower() == "true"

    token = get_env("TELEGRAM_BOT_TOKEN")
    chat_id = get_env("TELEGRAM_CHAT_ID")
    symbol = get_env("SYMBOL", "XAUUSDT")
    pair_name = get_env("PAIR_NAME", "XAUUSD OTC")

    fetcher = DataFetcher(
        symbol=symbol,
        min_candles=int(get_env("MIN_CANDLES", "300")),
        request_limit=int(get_env("REQUEST_LIMIT", "500")),
    )
    indicators = IndicatorEngine(
        atr_length=int(get_env("ATR_LENGTH", "10")),
        supertrend_multiplier=float(get_env("SUPERTREND_MULTIPLIER", "3.0")),
        macd_fast=int(get_env("MACD_FAST", "12")),
        macd_slow=int(get_env("MACD_SLOW", "26")),
        macd_signal=int(get_env("MACD_SIGNAL", "9")),
        atr_rolling_window=int(get_env("ATR_ROLLING_WINDOW", "20")),
    )
    signal_logic = SignalLogic()
    risk_manager = RiskManager(
        max_consecutive_losses=int(get_env("MAX_CONSECUTIVE_LOSSES", "2")),
        max_signals_per_day=int(get_env("MAX_SIGNALS_PER_DAY", "2")),
        cooldown_candles=int(get_env("COOLDOWN_CANDLES", "5")),
        candle_minutes=int(get_env("CANDLE_MINUTES", "15")),
        loss_streak_path=get_env("LOSS_STREAK_PATH", "loss_streak.json"),
        signals_path=get_env("SIGNALS_PATH", "signals.csv"),
        performance_path=get_env("PERFORMANCE_PATH", "performance.csv"),
    )
    telegram_bot = TelegramSignalBot(
        token=token,
        chat_id=chat_id,
        risk_manager=risk_manager,
        pair_name=pair_name,
    )
    if test_mode:
        print("[Main] TEST_MODE=True. Use backend_tester.py for 200-cycle dry-run validation.")

    app = telegram_bot.application
    app.add_error_handler(global_error_handler)
    app.bot_data["fetcher"] = fetcher
    app.bot_data["indicators"] = indicators
    app.bot_data["signal_logic"] = signal_logic
    app.bot_data["risk_manager"] = risk_manager
    app.bot_data["telegram_bot"] = telegram_bot
    app.bot_data["state"] = RuntimeState()

    app.job_queue.run_repeating(
        market_scan_job,
        interval=30,
        first=5,
        name="market-scan-30s",
    )

    print("[Main] Bot started. Scan interval: 30 seconds.")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
