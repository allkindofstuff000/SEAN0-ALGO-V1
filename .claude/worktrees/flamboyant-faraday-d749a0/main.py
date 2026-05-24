from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
import pytz
from dotenv import load_dotenv

from data_fetcher import DataFetcher
from indicator_engine import IndicatorEngine
from risk_manager import RiskManager
from signal_logic import SignalDecision, SignalLogic
from telegram_bot import TelegramNotifier


ROOT_DIR = Path(__file__).resolve().parent
STATE_PATH = ROOT_DIR / "state.json"
DECISION_TRACE_PATH = ROOT_DIR / "logs" / "decision_trace.log"

SYMBOL = "XAUUSDT"
TREND_TIMEFRAME = "15m"
ENTRY_TIMEFRAME = "5m"
HTF_TIMEFRAME = "1h"
SIGNAL_MODES = ("forex",)
CANDLE_LIMIT = 300
HTF_CANDLE_LIMIT = 300
POLL_INTERVAL_SECONDS = 60
DEFAULT_MAX_CYCLES = 0
ENABLE_HTF_FILTER = True

MARKET_HOURS = {
    "always_open": False,
    "close_time": (4, 22, 0),
    "open_time": (6, 22, 0),
}

SKIP_LOG_INTERVAL = datetime.timedelta(hours=1)

LOGGER = logging.getLogger("xau.mvp")


@dataclass
class RuntimeState:
    status: str = "starting"
    symbol: str = SYMBOL
    trend_timeframe: str = TREND_TIMEFRAME
    entry_timeframe: str = ENTRY_TIMEFRAME
    output_modes: tuple[str, ...] = SIGNAL_MODES
    last_cycle_utc: str | None = None
    last_trend_candle_utc: str | None = None
    last_entry_candle_utc: str | None = None
    last_signal_time_utc: str | None = None
    last_score: int = 0
    last_threshold: int = 80
    last_direction: str | None = None
    last_reason: str | None = None
    last_signal_modes: list[str] | None = None


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _write_state(state: RuntimeState) -> None:
    STATE_PATH.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def _state_from_decision(
    state: RuntimeState,
    decision: SignalDecision,
    trend_candle_time: pd.Timestamp,
    entry_candle_time: pd.Timestamp,
) -> RuntimeState:
    state.last_trend_candle_utc = trend_candle_time.isoformat()
    state.last_entry_candle_utc = entry_candle_time.isoformat()
    state.last_score = decision.score
    state.last_threshold = decision.score_threshold
    state.last_direction = decision.direction
    state.last_reason = decision.reason
    return state


def _build_components() -> tuple[DataFetcher, IndicatorEngine, SignalLogic, RiskManager, TelegramNotifier]:
    load_dotenv(ROOT_DIR / ".env")

    fetcher = DataFetcher(min_candles=CANDLE_LIMIT)
    fetcher.startup_check()
    indicators = IndicatorEngine()
    signal_engine = SignalLogic(symbol=SYMBOL, signal_modes=SIGNAL_MODES, enable_htf_filter=ENABLE_HTF_FILTER)
    risk_manager = RiskManager(
        max_signals_per_day=int(os.getenv("MAX_SIGNALS_PER_DAY", "3")),
        cooldown_candles=int(os.getenv("COOLDOWN_CANDLES", "1")),
        max_loss_streak=int(os.getenv("MAX_LOSS_STREAK", os.getenv("MAX_CONSECUTIVE_LOSSES", "2"))),
    )
    notifier = TelegramNotifier(
        token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
    )
    return fetcher, indicators, signal_engine, risk_manager, notifier


def _startup_feed_summary() -> dict[str, str]:
    return {
        "provider": "oanda",
        "instrument": "XAU_USD",
        "environment": os.getenv("OANDA_ENV", "practice").strip().lower(),
        "price_component": os.getenv("OANDA_PRICE_COMPONENT", "M").strip().upper(),
        "mode": "true_xauusd_intraday",
        "note": "Configured for OANDA XAU_USD 5min/15min spot-gold candles",
    }


def _build_week_boundary(
    now_utc: datetime.datetime,
    weekday: int,
    hour: int,
    minute: int,
) -> datetime.datetime:
    week_start = now_utc - datetime.timedelta(
        days=now_utc.weekday(),
        hours=now_utc.hour,
        minutes=now_utc.minute,
        seconds=now_utc.second,
        microseconds=now_utc.microsecond,
    )
    return week_start + datetime.timedelta(days=weekday, hours=hour, minutes=minute)


def is_market_open(now_utc: datetime.datetime) -> bool:
    close_weekday, close_hour, close_minute = MARKET_HOURS["close_time"]
    open_weekday, open_hour, open_minute = MARKET_HOURS["open_time"]
    weekend_close = _build_week_boundary(now_utc, close_weekday, close_hour, close_minute)
    weekend_open = _build_week_boundary(now_utc, open_weekday, open_hour, open_minute)
    return not (weekend_close <= now_utc < weekend_open)


def next_market_open(now_utc: datetime.datetime) -> datetime.datetime:
    open_weekday, open_hour, open_minute = MARKET_HOURS["open_time"]
    next_open = _build_week_boundary(now_utc, open_weekday, open_hour, open_minute)
    if now_utc >= next_open:
        next_open += datetime.timedelta(days=7)
    return next_open


def should_log_skip(now_utc: datetime.datetime, last_skip_log: datetime.datetime | None) -> bool:
    if last_skip_log is None:
        return True
    return (now_utc - last_skip_log) >= SKIP_LOG_INTERVAL


async def run_loop() -> None:
    startup_summary = _startup_feed_summary()
    LOGGER.info(
        "[FEED] provider=%s instrument=%s environment=%s component=%s mode=%s",
        startup_summary["provider"],
        startup_summary["instrument"],
        startup_summary["environment"],
        startup_summary["price_component"],
        startup_summary["mode"],
    )
    LOGGER.info("[FEED] %s", startup_summary["note"])

    fetcher, indicators, signal_engine, risk_manager, notifier = _build_components()
    provider_summary = fetcher.provider_summary()
    runtime_state = RuntimeState()
    last_processed_entry_candle: pd.Timestamp | None = None
    # CLEANER LOGS: only log DATA/INDICATOR on new candles
    last_logged_entry_candle: pd.Timestamp | None = None
    last_logged_trend_candle: pd.Timestamp | None = None
    last_skip_log: datetime.datetime | None = None
    max_cycles = max(0, int(os.getenv("MAX_CYCLES", str(DEFAULT_MAX_CYCLES)) or str(DEFAULT_MAX_CYCLES)))
    cycles_completed = 0

    LOGGER.info(
        "[ENGINE] started symbol=%s trend_tf=%s entry_tf=%s htf_tf=%s htf_filter=%s modes=%s interval=%ss decision_log=%s",
        SYMBOL,
        TREND_TIMEFRAME,
        ENTRY_TIMEFRAME,
        HTF_TIMEFRAME,
        ENABLE_HTF_FILTER,
        ",".join(SIGNAL_MODES),
        POLL_INTERVAL_SECONDS,
        DECISION_TRACE_PATH,
    )
    if provider_summary != startup_summary:
        LOGGER.info(
            "[FEED] resolved provider=%s instrument=%s environment=%s component=%s mode=%s",
            provider_summary["provider"],
            provider_summary["instrument"],
            provider_summary["environment"],
            provider_summary["price_component"],
            provider_summary["mode"],
        )
        LOGGER.info("[FEED] %s", provider_summary["note"])

    while True:
        now_utc = datetime.datetime.now(pytz.UTC)
        runtime_state.last_cycle_utc = now_utc.isoformat()
        runtime_state.status = "running"

        if not is_market_open(now_utc):
            runtime_state.status = "market_closed"
            runtime_state.last_reason = "market_closed"
            if should_log_skip(now_utc, last_skip_log):
                LOGGER.info(
                    "[MARKET] %s closed in UTC - skipping until %s",
                    SYMBOL,
                    next_market_open(now_utc).isoformat(),
                )
                last_skip_log = now_utc
            _write_state(runtime_state)
            cycles_completed += 1
            if max_cycles and cycles_completed >= max_cycles:
                LOGGER.info("[ENGINE] reached max cycles=%s; exiting", max_cycles)
                return
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue

        last_skip_log = None

        try:
            trend_candles = await asyncio.to_thread(fetcher.fetch_market_data, SYMBOL, TREND_TIMEFRAME, CANDLE_LIMIT)
            entry_candles = await asyncio.to_thread(fetcher.fetch_market_data, SYMBOL, ENTRY_TIMEFRAME, CANDLE_LIMIT)
            trend_indicators = indicators.add_indicators(trend_candles)
            entry_indicators = indicators.add_indicators(entry_candles)

            # HTF (1H) candles for structural bias filter
            htf_indicators = None
            if ENABLE_HTF_FILTER:
                try:
                    htf_candles = await asyncio.to_thread(
                        fetcher.fetch_market_data, SYMBOL, HTF_TIMEFRAME, HTF_CANDLE_LIMIT
                    )
                    htf_indicators = indicators.add_indicators(htf_candles)
                    if htf_indicators.empty:
                        LOGGER.warning("[HTF] empty indicators for %s %s — HTF filter skipped", SYMBOL, HTF_TIMEFRAME)
                        htf_indicators = None
                except Exception:
                    LOGGER.warning("[HTF] failed to fetch %s %s candles — HTF filter skipped", SYMBOL, HTF_TIMEFRAME)
                    htf_indicators = None

            if trend_indicators.empty or entry_indicators.empty:
                raise RuntimeError(f"Missing indicator data for {SYMBOL}.")

            trend_candle_time = pd.Timestamp(trend_indicators["timestamp"].iloc[-1])
            entry_candle_time = pd.Timestamp(entry_indicators["timestamp"].iloc[-1])
            trend_last = trend_indicators.iloc[-1]
            entry_last = entry_indicators.iloc[-1]
            should_log_market_snapshot = bool(
                trend_candle_time != last_logged_trend_candle
                or entry_candle_time != last_logged_entry_candle
            )

            if last_processed_entry_candle is not None and entry_candle_time <= last_processed_entry_candle:
                runtime_state.status = "waiting"
                runtime_state.last_reason = "waiting_for_new_closed_entry_candle"
                _write_state(runtime_state)
                cycles_completed += 1
                if max_cycles and cycles_completed >= max_cycles:
                    LOGGER.info("[ENGINE] reached max cycles=%s; exiting", max_cycles)
                    return
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            last_processed_entry_candle = entry_candle_time
            if should_log_market_snapshot:
                LOGGER.info(
                    "[DATA] symbol=%s trend_tf=%s trend_candle=%s entry_tf=%s entry_candle=%s",
                    SYMBOL,
                    TREND_TIMEFRAME,
                    trend_candle_time.isoformat(),
                    ENTRY_TIMEFRAME,
                    entry_candle_time.isoformat(),
                )
                LOGGER.info(
                    "[INDICATOR] symbol=%s trend_ema50=%.4f trend_ema200=%.4f entry_rsi=%.2f entry_atr=%.4f",
                    SYMBOL,
                    float(trend_last["ema50"]),
                    float(trend_last["ema200"]),
                    float(entry_last["rsi14"]),
                    float(entry_last["atr14"]),
                )
                last_logged_trend_candle = trend_candle_time
                last_logged_entry_candle = entry_candle_time

            decision = signal_engine.evaluate(
                trend_indicators, entry_indicators, now_utc=now_utc, htf_candles=htf_indicators
            )
            runtime_state = _state_from_decision(
                runtime_state,
                decision,
                trend_candle_time=trend_candle_time,
                entry_candle_time=entry_candle_time,
            )
            LOGGER.info(
                "[SIGNAL] symbol=%s score=%s threshold=%s direction=%s reason=%s",
                SYMBOL,
                decision.score,
                decision.score_threshold,
                decision.direction,
                decision.reason,
            )

            if decision.signal_generated and decision.signals:
                primary_signal = decision.signal or decision.signals[0]
                allowed, risk_reason = risk_manager.can_emit_signal(primary_signal)
                if allowed:
                    LOGGER.info("[RISK] symbol=%s signal allowed", SYMBOL)
                    sent_any = False
                    for signal in decision.signals:
                        sent = await notifier.send_signal(signal)
                        sent_any = sent_any or sent
                        LOGGER.info(
                            "[TELEGRAM] symbol=%s mode=%s payload=%s",
                            SYMBOL,
                            signal.signal_kind,
                            signal.message().replace("\n", " | "),
                        )
                    risk_manager.record_signal(primary_signal)
                    runtime_state.status = "signal_sent" if sent_any else "signal_logged"
                    runtime_state.last_signal_time_utc = primary_signal.timestamp_utc.isoformat()
                    runtime_state.last_reason = "signal_dispatched"
                    runtime_state.last_signal_modes = [signal.signal_kind for signal in decision.signals]
                else:
                    runtime_state.status = "blocked"
                    runtime_state.last_reason = risk_reason
                    LOGGER.info("[RISK] symbol=%s signal blocked reason=%s", SYMBOL, risk_reason)
            else:
                runtime_state.status = "idle"
                runtime_state.last_signal_modes = []
                LOGGER.info("[RISK] symbol=%s no signal to route", SYMBOL)
        except Exception:
            runtime_state.status = "error"
            runtime_state.last_reason = "exception"
            LOGGER.exception("[ENGINE] symbol=%s cycle failed", SYMBOL)

        _write_state(runtime_state)
        cycles_completed += 1
        if max_cycles and cycles_completed >= max_cycles:
            LOGGER.info("[ENGINE] reached max cycles=%s; exiting", max_cycles)
            return
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    configure_logging()
    try:
        asyncio.run(run_loop())
    except RuntimeError as error:
        LOGGER.error("[ENGINE] startup failed: %s", error)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
