from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from data_fetcher import DataFetcher
from indicator_engine import IndicatorEngine
from risk_manager import RiskManager
from signal_logic import SignalDecision, SignalLogic, TradeSignal
from telegram_bot import TelegramNotifier


ROOT_DIR = Path(__file__).resolve().parent
STATE_PATH = ROOT_DIR / "state.json"
DECISION_TRACE_PATH = ROOT_DIR / "logs" / "decision_trace.log"
SYMBOL = "XAUUSD"
TIMEFRAME = "1m"
CANDLE_LIMIT = 300
POLL_INTERVAL_SECONDS = 60
DEFAULT_SIGNAL_MODE = "BINARY"
DEFAULT_MAX_CYCLES = 0

LOGGER = logging.getLogger("xauusd.mvp")


@dataclass
class RuntimeState:
    status: str = "starting"
    symbol: str = SYMBOL
    timeframe: str = TIMEFRAME
    mode: str = DEFAULT_SIGNAL_MODE
    last_cycle_utc: str | None = None
    last_candle_utc: str | None = None
    last_signal_time_utc: str | None = None
    last_score: int = 0
    last_threshold: int = 70
    last_direction: str | None = None
    last_reason: str | None = None


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _write_state(state: RuntimeState) -> None:
    STATE_PATH.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")

async def _send_signal(notifier: TelegramNotifier, signal: TradeSignal, mode: str) -> bool:
    if mode == "FOREX":
        return await notifier.send_message(signal.forex_message())
    return await notifier.send_message(signal.binary_message())


def _state_from_decision(state: RuntimeState, decision: SignalDecision) -> RuntimeState:
    state.last_cycle_utc = datetime.now(timezone.utc).isoformat()
    state.last_candle_utc = decision.candle_time_utc.isoformat()
    state.last_score = decision.score
    state.last_threshold = decision.score_threshold
    state.last_direction = decision.direction
    state.last_reason = decision.reason
    return state


def _build_components() -> tuple[DataFetcher, IndicatorEngine, SignalLogic, RiskManager, TelegramNotifier, str]:
    load_dotenv(ROOT_DIR / ".env")
    signal_mode = os.getenv("SIGNAL_MODE", DEFAULT_SIGNAL_MODE).strip().upper() or DEFAULT_SIGNAL_MODE

    fetcher = DataFetcher(symbol=SYMBOL, default_timeframe=TIMEFRAME, min_candles=CANDLE_LIMIT)
    indicators = IndicatorEngine()
    signal_logic = SignalLogic(symbol=SYMBOL, threshold=70)
    risk_manager = RiskManager(
        max_signals_per_day=int(os.getenv("MAX_SIGNALS_PER_DAY", "3")),
        cooldown_minutes=int(os.getenv("COOLDOWN_MINUTES", "5")),
        max_loss_streak=int(os.getenv("MAX_LOSS_STREAK", "2")),
    )
    notifier = TelegramNotifier(
        token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
    )
    return fetcher, indicators, signal_logic, risk_manager, notifier, signal_mode


async def run_loop() -> None:
    fetcher, indicators, signal_logic, risk_manager, notifier, signal_mode = _build_components()
    runtime_state = RuntimeState()
    runtime_state.mode = signal_mode
    last_processed_candle: pd.Timestamp | None = None
    max_cycles = max(0, int(os.getenv("MAX_CYCLES", str(DEFAULT_MAX_CYCLES)) or str(DEFAULT_MAX_CYCLES)))
    cycles_completed = 0

    LOGGER.info(
        "[ENGINE] started symbol=%s timeframe=%s mode=%s interval=%ss decision_log=%s",
        SYMBOL,
        TIMEFRAME,
        signal_mode,
        POLL_INTERVAL_SECONDS,
        DECISION_TRACE_PATH,
    )

    while True:
        try:
            candles = await asyncio.to_thread(fetcher.fetch_candles, TIMEFRAME, CANDLE_LIMIT)
            enriched = indicators.add_indicators(candles)
            if enriched.empty:
                raise RuntimeError("No enriched candles available.")

            latest_candle_time = pd.Timestamp(enriched["timestamp"].iloc[-1])
            latest_row = enriched.iloc[-1]
            LOGGER.info(
                "[DATA] fetched candle time=%s close=%.4f",
                latest_candle_time.isoformat(),
                float(latest_row["close"]),
            )
            if last_processed_candle is not None and latest_candle_time <= last_processed_candle:
                runtime_state.status = "waiting"
                runtime_state.last_cycle_utc = datetime.now(timezone.utc).isoformat()
                runtime_state.last_reason = "waiting_for_new_closed_candle"
                _write_state(runtime_state)
                LOGGER.info("[DATA] waiting for new closed candle current=%s", latest_candle_time.isoformat())
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            last_processed_candle = latest_candle_time
            LOGGER.info(
                "[INDICATOR] EMA20=%.4f EMA50=%.4f ATR=%.4f",
                float(latest_row["ema20"]),
                float(latest_row["ema50"]),
                float(latest_row["atr14"]),
            )
            decision = signal_logic.evaluate(enriched)
            runtime_state = _state_from_decision(runtime_state, decision)
            LOGGER.info(
                "[SIGNAL] score=%s threshold=%s direction=%s reason=%s",
                decision.score,
                decision.score_threshold,
                decision.direction,
                decision.reason,
            )

            if decision.signal_generated and decision.signal is not None:
                allowed, risk_reason = risk_manager.can_emit_signal(decision.signal.timestamp_utc)
                if allowed:
                    LOGGER.info("[RISK] signal allowed")
                    sent = await _send_signal(notifier, decision.signal, signal_mode)
                    risk_manager.record_signal(decision.signal)
                    runtime_state.status = "signal_sent" if sent else "signal_logged"
                    runtime_state.last_signal_time_utc = decision.signal.timestamp_utc.isoformat()
                    runtime_state.last_reason = "signal_dispatched"
                    LOGGER.info(
                        "[TELEGRAM] payload=%s",
                        decision.signal.forex_message() if signal_mode == "FOREX" else decision.signal.binary_message(),
                    )
                else:
                    runtime_state.status = "blocked"
                    runtime_state.last_reason = risk_reason
                    LOGGER.info("[RISK] signal blocked reason=%s", risk_reason)
            else:
                runtime_state.status = "idle"
                LOGGER.info("[RISK] no signal to route")

            _write_state(runtime_state)
            cycles_completed += 1
            if max_cycles and cycles_completed >= max_cycles:
                LOGGER.info("[ENGINE] reached max cycles=%s; exiting", max_cycles)
                return
        except Exception:
            runtime_state.status = "error"
            runtime_state.last_cycle_utc = datetime.now(timezone.utc).isoformat()
            runtime_state.last_reason = "exception"
            _write_state(runtime_state)
            LOGGER.exception("[ENGINE] cycle failed")
            cycles_completed += 1
            if max_cycles and cycles_completed >= max_cycles:
                LOGGER.info("[ENGINE] reached max cycles=%s after error; exiting", max_cycles)
                return

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    configure_logging()
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
