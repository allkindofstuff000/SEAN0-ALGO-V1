from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from data_fetcher import DataFetcher
from indicator_engine import IndicatorEngine
from main import RuntimeState, get_env, run_single_cycle
from risk_manager import RiskManager
from signal_logic import SignalLogic


TEST_MODE = True
TOTAL_CYCLES = 200


async def run_backend_validation() -> None:
    load_dotenv()

    fetcher = DataFetcher(
        symbol=get_env("SYMBOL", "XAUUSDT"),
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
    state = RuntimeState()

    total_cycles = 0
    valid_signals = 0
    skipped_cycles = 0
    errors_handled = 0
    nan_detected = 0

    for cycle in range(1, TOTAL_CYCLES + 1):
        print(f"----- Dry Run Cycle {cycle}/{TOTAL_CYCLES} -----")
        report = await run_single_cycle(
            fetcher=fetcher,
            indicators=indicators,
            signal_logic=signal_logic,
            risk_manager=risk_manager,
            state=state,
            telegram_bot=None,
            test_mode=TEST_MODE,
            enforce_new_candle=False,
        )
        total_cycles += 1
        if report.signal_generated:
            valid_signals += 1
        if report.skipped:
            skipped_cycles += 1
        if report.nan_detected:
            nan_detected += 1
        if report.error_handled:
            errors_handled += 1

    print("======== BACKEND TEST SUMMARY ========")
    print(f"Total Cycles: {total_cycles}")
    print(f"Valid Signals: {valid_signals}")
    print(f"Skipped (filters): {skipped_cycles}")
    print(f"Errors handled: {errors_handled}")
    print(f"NaN detected: {nan_detected}")
    print("=======================================")


if __name__ == "__main__":
    asyncio.run(run_backend_validation())
