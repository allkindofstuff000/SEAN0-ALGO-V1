from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config.config import (
    BINARY_EXPIRY,
    CANDLE_LIMIT,
    COOLDOWN_CANDLES,
    DATA_REFRESH_SECONDS,
    ENGINE_STATE_PATH,
    EXCHANGE,
    FOREX_MAX_HOLDING_MINUTES,
    FOREX_STOP_LOSS_ATR_MULTIPLIER,
    FOREX_TAKE_PROFIT_ATR_MULTIPLIER,
    LEARNING_WINDOW_TRADES,
    LOSS_STREAK_LIMIT,
    MAX_DYNAMIC_THRESHOLD,
    MAX_SIGNALS_PER_DAY,
    MIN_DYNAMIC_THRESHOLD,
    MINIMUM_CANDLES,
    PENDING_TRADES_PATH,
    PERFORMANCE_PATH,
    PRIMARY_TIMEFRAME,
    SESSION_END_UTC,
    SESSION_START_UTC,
    SIGNAL_HISTORY_PATH,
    SIGNAL_MODE,
    SIGNAL_SCORE_THRESHOLD,
    SYMBOL,
    TELEGRAM_CHAT_ID,
    TELEGRAM_TOKEN,
    THRESHOLD_HISTORY_PATH,
    THRESHOLD_STATE_PATH,
    TIMEFRAMES,
    TRADE_LOG_PATH,
)
from bot.data.data_fetcher import DataFetcher
from bot.data.timeframe_manager import TimeframeManager
from bot.execution.signal_router import SignalRouter
from bot.indicators.indicator_engine import IndicatorEngine
from bot.learning.performance_tracker import PerformanceTracker
from bot.learning.strategy_adapter import StrategyAdapter
from bot.learning.threshold_optimizer import ThresholdOptimizer
from bot.market.liquidity_map import LiquidityMapEngine
from bot.market.regime_detector import RegimeDetector
from bot.market.session_engine import SessionEngine
from bot.output.signal_dispatcher import SignalDispatcher
from bot.output.telegram_bot import TelegramNotifier
from bot.risk.risk_manager import RiskManager
from bot.signals.signal_logic import SignalGenerator

try:
    from storage import load_config as legacy_load_config
    from storage import record_last_signal_time as legacy_record_last_signal_time
    from storage import update_state as legacy_update_state
except Exception:  # pragma: no cover - fallback when legacy storage module is absent
    legacy_load_config = None
    legacy_record_last_signal_time = None
    legacy_update_state = None


LOGGER = logging.getLogger("bot.engine")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


@dataclass
class EngineStateStore:
    """
    Persist lightweight engine state for dashboard/API consumption.
    """

    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.write(self.default_state())

    @staticmethod
    def default_state() -> dict[str, Any]:
        return {
            "status": "starting",
            "enabled": True,
            "regime": "UNKNOWN",
            "session": "UNKNOWN",
            "volatility_state": "UNKNOWN",
            "liquidity_state": "UNKNOWN",
            "signal_score": 0,
            "dynamic_threshold": SIGNAL_SCORE_THRESHOLD,
            "signal_confidence": "LOW",
            "completed_trades": 0,
            "learning_enabled": True,
            "last_threshold_update_utc": None,
            "last_cycle_utc": None,
            "last_signal_time_utc": None,
            "last_direction": None,
            "last_reason": None,
            "route_mode": SIGNAL_MODE,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.default_state()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return self.default_state()
            state = self.default_state()
            state.update(payload)
            return state
        except Exception:
            LOGGER.exception("engine_state_read_failed")
            return self.default_state()

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        state = self.read()
        state.update(patch)
        state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        self.write(state)
        return state

    def write(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _is_bot_enabled() -> bool:
    if callable(legacy_load_config):
        try:
            return bool(legacy_load_config().get("enabled", True))
        except Exception:
            LOGGER.exception("legacy_load_config_failed")
    return True


def _runtime_config() -> dict[str, Any]:
    if callable(legacy_load_config):
        try:
            return dict(legacy_load_config())
        except Exception:
            LOGGER.exception("legacy_runtime_config_failed")
    return {}


def _sync_legacy_state(state: dict[str, Any]) -> None:
    if not callable(legacy_update_state):
        return
    try:
        legacy_update_state(
            {
                "status": state.get("status"),
                "last_cycle": state.get("last_cycle_utc"),
                "last_signal_time": state.get("last_signal_time_utc"),
            }
        )
    except Exception:
        LOGGER.exception("legacy_state_sync_failed")


def _apply_runtime_config(*, config: dict[str, Any], risk_manager: RiskManager, strategy_adapter: StrategyAdapter) -> bool:
    risk_manager.max_signals_per_day = max(1, int(config.get("max_signals_per_day", risk_manager.max_signals_per_day)))
    risk_manager.cooldown_candles = max(1, int(config.get("cooldown_candles", risk_manager.cooldown_candles)))
    risk_manager.max_loss_streak = max(1, int(config.get("max_loss_streak", risk_manager.max_loss_streak)))

    learning_enabled = bool(config.get("self_learning_enabled", strategy_adapter.is_enabled()))
    if learning_enabled != strategy_adapter.is_enabled():
        strategy_adapter.set_enabled(learning_enabled)
    return learning_enabled


def _resolve_volatility_state(regime: dict[str, Any]) -> str:
    return "EXPANDING" if bool(regime.get("atr_expansion", False)) else "CONTRACTING"


def _summarize_liquidity(liquidity_map: dict[str, Any]) -> str:
    if bool(liquidity_map.get("bullish_sweep")):
        return "Bullish Sweep"
    if bool(liquidity_map.get("bearish_sweep")):
        return "Bearish Sweep"
    if liquidity_map.get("equal_highs"):
        return "Equal Highs"
    if liquidity_map.get("equal_lows"):
        return "Equal Lows"
    if liquidity_map.get("swing_highs") or liquidity_map.get("swing_lows"):
        return "Swing Zones"
    return "No Active Zone"


def _resolve_confidence(score: int, threshold: int) -> str:
    if score >= threshold + 12:
        return "HIGH"
    if score >= threshold:
        return "MEDIUM"
    return "LOW"


async def run_loop() -> None:
    fetcher = DataFetcher(exchange_name=EXCHANGE, default_limit=CANDLE_LIMIT)
    manager = TimeframeManager(fetcher=fetcher, minimum_candles=MINIMUM_CANDLES)
    indicators = IndicatorEngine()
    liquidity_engine = LiquidityMapEngine()
    regime_detector = RegimeDetector()
    session_engine = SessionEngine()
    performance_tracker = PerformanceTracker(
        trade_log_path=TRADE_LOG_PATH,
        pending_trades_path=PENDING_TRADES_PATH,
        forex_max_holding_minutes=FOREX_MAX_HOLDING_MINUTES,
    )
    threshold_optimizer = ThresholdOptimizer(lookback_trades=LEARNING_WINDOW_TRADES)
    strategy_adapter = StrategyAdapter(
        state_path=THRESHOLD_STATE_PATH,
        history_path=THRESHOLD_HISTORY_PATH,
        default_threshold=SIGNAL_SCORE_THRESHOLD,
        min_threshold=MIN_DYNAMIC_THRESHOLD,
        max_threshold=MAX_DYNAMIC_THRESHOLD,
        optimization_frequency=LEARNING_WINDOW_TRADES,
        optimizer=threshold_optimizer,
    )
    signal_generator = SignalGenerator(
        score_threshold=SIGNAL_SCORE_THRESHOLD,
        threshold_provider=strategy_adapter,
    )
    risk_manager = RiskManager(
        max_signals_per_day=MAX_SIGNALS_PER_DAY,
        cooldown_candles=COOLDOWN_CANDLES,
        max_loss_streak=LOSS_STREAK_LIMIT,
        candle_minutes=15,
        session_start_utc=SESSION_START_UTC,
        session_end_utc=SESSION_END_UTC,
    )
    signal_router = SignalRouter(
        mode=SIGNAL_MODE,
        binary_expiry=BINARY_EXPIRY,
        forex_stop_loss_atr_multiplier=FOREX_STOP_LOSS_ATR_MULTIPLIER,
        forex_take_profit_atr_multiplier=FOREX_TAKE_PROFIT_ATR_MULTIPLIER,
    )
    notifier = TelegramNotifier(token=TELEGRAM_TOKEN, chat_id=TELEGRAM_CHAT_ID)
    dispatcher = SignalDispatcher(
        notifier=notifier,
        signal_history_path=SIGNAL_HISTORY_PATH,
        performance_path=PERFORMANCE_PATH,
    )
    state_store = EngineStateStore(path=ENGINE_STATE_PATH)

    LOGGER.info(
        "engine_started exchange=%s symbol=%s timeframes=%s limit=%s refresh=%ss mode=%s threshold=%s learning_enabled=%s",
        EXCHANGE,
        SYMBOL,
        TIMEFRAMES,
        CANDLE_LIMIT,
        DATA_REFRESH_SECONDS,
        SIGNAL_MODE,
        strategy_adapter.get_threshold(),
        strategy_adapter.is_enabled(),
    )

    while True:
        cycle_time = datetime.now(timezone.utc)
        try:
            runtime_config = _runtime_config()
            enabled = bool(runtime_config.get("enabled", _is_bot_enabled()))
            if not enabled:
                state = state_store.update(
                    {
                        "status": "stopped",
                        "enabled": False,
                        "learning_enabled": strategy_adapter.is_enabled(),
                        "last_cycle_utc": cycle_time.isoformat(),
                        "last_reason": "disabled_by_config",
                    }
                )
                _sync_legacy_state(state)
                await asyncio.sleep(DATA_REFRESH_SECONDS)
                continue

            learning_enabled = _apply_runtime_config(
                config=runtime_config,
                risk_manager=risk_manager,
                strategy_adapter=strategy_adapter,
            )
            risk_manager.reset_daily_if_needed(cycle_time)

            datasets = await asyncio.to_thread(
                manager.fetch_timeframes,
                symbol=SYMBOL,
                timeframes=TIMEFRAMES,
                limit=CANDLE_LIMIT,
            )
            enriched = {timeframe: indicators.add_indicators(frame) for timeframe, frame in datasets.items()}
            timeframe = PRIMARY_TIMEFRAME if PRIMARY_TIMEFRAME in enriched else TIMEFRAMES[0]
            frame = enriched[timeframe]
            latest = frame.iloc[-1]
            latest_candle = {
                "timestamp": latest["timestamp"] if "timestamp" in frame.columns else cycle_time,
                "close": float(latest["close"]),
                "high": float(latest["high"]),
                "low": float(latest["low"]),
            }

            completed_trades = await asyncio.to_thread(
                performance_tracker.settle_due_trades,
                current_time_utc=cycle_time,
                latest_candle=latest_candle,
            )
            for trade in completed_trades:
                result = str(trade.get("result", "")).upper()
                if result == "WIN":
                    risk_manager.record_outcome(True)
                elif result == "LOSS":
                    risk_manager.record_outcome(False)

            if completed_trades and learning_enabled:
                await asyncio.to_thread(strategy_adapter.maybe_optimize, performance_tracker)

            liquidity_map = liquidity_engine.build(frame)
            regime = regime_detector.detect(frame)
            session = session_engine.detect(frame.iloc[-1]["timestamp"] if "timestamp" in frame.columns else cycle_time)
            evaluation = signal_generator.evaluate(
                pair=SYMBOL,
                timeframe=timeframe,
                df=frame,
                liquidity_map=liquidity_map,
                regime=regime,
                session=session,
            )

            status = "running"
            reason = evaluation.reason
            score = int(evaluation.score)
            direction = evaluation.direction
            last_signal_iso = state_store.read().get("last_signal_time_utc")

            if evaluation.signal_generated and evaluation.signal is not None:
                allowed, risk_reason = risk_manager.can_emit_signal(cycle_time)
                if allowed:
                    routed_signal = signal_router.route(evaluation.signal)
                    await dispatcher.dispatch(evaluation.signal, routed_signal)
                    risk_manager.record_signal(evaluation.signal.timestamp_utc)
                    await asyncio.to_thread(performance_tracker.register_signal, evaluation.signal, routed_signal)
                    last_signal_iso = evaluation.signal.timestamp_utc.isoformat()
                    reason = "signal_dispatched"
                    if callable(legacy_record_last_signal_time):
                        try:
                            legacy_record_last_signal_time(evaluation.signal.timestamp_utc.isoformat())
                        except Exception:
                            LOGGER.exception("legacy_signal_time_sync_failed")
                else:
                    status = "blocked"
                    reason = risk_reason
            else:
                status = "idle"

            total_completed_trades = await asyncio.to_thread(performance_tracker.completed_trade_count)
            volatility_state = _resolve_volatility_state(regime)
            liquidity_state = _summarize_liquidity(liquidity_map)
            confidence = _resolve_confidence(score, evaluation.score_threshold)
            state = state_store.update(
                {
                    "status": status,
                    "enabled": True,
                    "regime": evaluation.regime,
                    "session": evaluation.session,
                    "volatility_state": volatility_state,
                    "liquidity_state": liquidity_state,
                    "signal_score": score,
                    "dynamic_threshold": evaluation.score_threshold,
                    "signal_confidence": confidence,
                    "completed_trades": total_completed_trades,
                    "learning_enabled": learning_enabled,
                    "last_threshold_update_utc": strategy_adapter.last_update_utc(),
                    "last_direction": direction,
                    "last_reason": reason,
                    "last_cycle_utc": cycle_time.isoformat(),
                    "last_signal_time_utc": last_signal_iso,
                    "route_mode": signal_router.mode,
                }
            )
            _sync_legacy_state(state)

            LOGGER.info(
                "cycle_complete status=%s regime=%s session=%s timeframe=%s close=%.4f score=%s threshold=%s direction=%s reason=%s completed_trades=%s learning_enabled=%s",
                status,
                evaluation.regime,
                evaluation.session,
                timeframe,
                float(latest["close"]),
                score,
                evaluation.score_threshold,
                direction,
                reason,
                total_completed_trades,
                learning_enabled,
            )
        except Exception:
            LOGGER.exception("cycle_failed")
            state = state_store.update(
                {
                    "status": "error",
                    "enabled": _is_bot_enabled(),
                    "last_cycle_utc": cycle_time.isoformat(),
                    "last_reason": "exception",
                }
            )
            _sync_legacy_state(state)

        await asyncio.sleep(DATA_REFRESH_SECONDS)


def main() -> None:
    configure_logging()
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
