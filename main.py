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

from core.data_fetcher import DataFetcher
from core.indicator_engine import IndicatorEngine
from core.risk_manager import RiskManager
from core.signal_logic import SignalDecision, SignalLogic, TradeSignal
from core.telegram_bot import TelegramNotifier

# MongoDB signal persistence (non-fatal if unavailable)
try:
    from core.mongo_store import save_live_signal as _save_live_signal
    _MONGO_SIGNALS = True
except Exception:
    _MONGO_SIGNALS = False
    def _save_live_signal(**_): return None


ROOT_DIR = Path(__file__).resolve().parent
STATE_PATH = ROOT_DIR / "state.json"
DECISION_TRACE_PATH = ROOT_DIR / "logs" / "decision_trace.log"

SYMBOL = "XAUUSDT"
TREND_TIMEFRAME = "15m"
ENTRY_TIMEFRAME = "5m"
SIGNAL_MODES = ("forex",)
CANDLE_LIMIT = 300
POLL_INTERVAL_SECONDS = 60
DEFAULT_MAX_CYCLES = 0

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
    signal_engine = SignalLogic(symbol=SYMBOL, signal_modes=SIGNAL_MODES)
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


def _timeframe_delta(timeframe: str) -> pd.Timedelta:
    normalized = timeframe.strip().lower()
    mapping = {
        "1m": pd.Timedelta(minutes=1),
        "5m": pd.Timedelta(minutes=5),
        "15m": pd.Timedelta(minutes=15),
        "30m": pd.Timedelta(minutes=30),
        "1h": pd.Timedelta(hours=1),
        "4h": pd.Timedelta(hours=4),
    }
    if normalized not in mapping:
        raise ValueError(f"Unsupported timeframe delta: {timeframe}")
    return mapping[normalized]


def _market_feed_status(
    now_utc: datetime.datetime,
    entry_close_time: pd.Timestamp,
    live_candle_time: pd.Timestamp | None,
    live_candle_complete: bool,
) -> str:
    now_ts = pd.Timestamp(now_utc)
    entry_delay_seconds = max(0.0, float((now_ts - entry_close_time).total_seconds()))
    if live_candle_time is None:
        return "snapshot_unavailable"

    live_age_seconds = max(0.0, float((now_ts - live_candle_time).total_seconds()))
    if live_age_seconds > 600 or entry_delay_seconds > 900:
        return "stale_or_market_pause"
    if live_candle_complete:
        return "live_complete"
    return "live_in_progress"


def _build_market_snapshot_payload(
    *,
    now_utc: datetime.datetime,
    trend_candle_time: pd.Timestamp,
    entry_candle_time: pd.Timestamp,
    trend_last: pd.Series,
    entry_last: pd.Series,
    live_snapshot: dict[str, object] | None,
    snapshot_error: str | None = None,
) -> dict[str, object]:
    trend_close_time = trend_candle_time + _timeframe_delta(TREND_TIMEFRAME)
    entry_close_time = entry_candle_time + _timeframe_delta(ENTRY_TIMEFRAME)

    live_candle_time = None
    live_candle_close_time = None
    live_candle_complete = False
    live_price = None
    live_open = None
    live_high = None
    live_low = None
    live_volume = None
    live_granularity = None
    live_instrument = None

    if live_snapshot is not None:
        live_candle_time = pd.Timestamp(live_snapshot["timestamp"])
        live_candle_close_time = live_candle_time + _timeframe_delta("1m")
        live_candle_complete = bool(live_snapshot.get("complete", False))
        live_price = float(live_snapshot["close"])
        live_open = float(live_snapshot["open"])
        live_high = float(live_snapshot["high"])
        live_low = float(live_snapshot["low"])
        live_volume = float(live_snapshot["volume"])
        live_granularity = str(live_snapshot.get("granularity", "M1"))
        live_instrument = str(live_snapshot.get("instrument", "XAU_USD"))

    now_ts = pd.Timestamp(now_utc)
    entry_delay_seconds = max(0.0, float((now_ts - entry_close_time).total_seconds()))
    trend_delay_seconds = max(0.0, float((now_ts - trend_close_time).total_seconds()))
    feed_status = _market_feed_status(
        now_utc=now_utc,
        entry_close_time=entry_close_time,
        live_candle_time=live_candle_time,
        live_candle_complete=live_candle_complete,
    )

    return {
        "timestamp": now_utc,
        "event_type": "market_snapshot",
        "symbol": SYMBOL,
        "fetched_at_utc": now_utc,
        "latest_closed_trend_open_utc": trend_candle_time,
        "latest_closed_trend_close_utc": trend_close_time,
        "latest_closed_trend_close_price": float(trend_last["close"]),
        "latest_closed_entry_open_utc": entry_candle_time,
        "latest_closed_entry_close_utc": entry_close_time,
        "latest_closed_entry_close_price": float(entry_last["close"]),
        "trend_delay_seconds": round(trend_delay_seconds, 2),
        "entry_delay_seconds": round(entry_delay_seconds, 2),
        "live_candle_open_utc": live_candle_time,
        "live_candle_close_utc": live_candle_close_time,
        "live_candle_complete": live_candle_complete,
        "live_price": live_price,
        "live_open": live_open,
        "live_high": live_high,
        "live_low": live_low,
        "live_volume": live_volume,
        "live_granularity": live_granularity,
        "live_instrument": live_instrument,
        "feed_status": feed_status,
        "snapshot_error": snapshot_error,
    }


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
        "[ENGINE] started symbol=%s trend_tf=%s entry_tf=%s modes=%s interval=%ss decision_log=%s",
        SYMBOL,
        TREND_TIMEFRAME,
        ENTRY_TIMEFRAME,
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

            live_snapshot: dict[str, object] | None = None
            snapshot_error: str | None = None
            try:
                live_snapshot = await asyncio.to_thread(fetcher.fetch_live_market_snapshot, "1m")
            except Exception as error:
                snapshot_error = str(error)
                LOGGER.warning("[MARKET] live snapshot fetch failed: %s", error)

            signal_engine.decision_logger.log_market_snapshot(
                _build_market_snapshot_payload(
                    now_utc=now_utc,
                    trend_candle_time=trend_candle_time,
                    entry_candle_time=entry_candle_time,
                    trend_last=trend_last,
                    entry_last=entry_last,
                    live_snapshot=live_snapshot,
                    snapshot_error=snapshot_error,
                )
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
                trend_indicators, entry_indicators, now_utc=now_utc
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
                    # ── Persist signal to MongoDB ─────────────────────────────
                    try:
                        _save_live_signal(
                            symbol=primary_signal.symbol,
                            direction=primary_signal.direction,
                            entry_price=primary_signal.entry_price,
                            stop_loss=primary_signal.stop_loss,
                            take_profit=primary_signal.take_profit,
                            atr=primary_signal.atr,
                            score=primary_signal.score,
                            score_threshold=primary_signal.score_threshold,
                            session=primary_signal.session,
                            market_regime=decision.market_regime,
                            regime_confidence=decision.regime_confidence,
                            trend_alignment=decision.trend_alignment,
                            price_trigger=decision.price_trigger,
                            rsi_filter=decision.rsi_filter,
                            atr_expansion=decision.atr_expansion,
                            reason=decision.reason,
                            signal_kind=primary_signal.signal_kind,
                            telegram_sent=sent_any,
                            candle_time_utc=primary_signal.timestamp_utc.isoformat(),
                        )
                    except Exception:
                        LOGGER.warning("[MONGO] failed to save live signal — continuing")
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
