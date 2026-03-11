from __future__ import annotations

import datetime as dt
import importlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_trading_main():
    return importlib.import_module("main")


def _select_preferred_signal(decision: Any) -> Any | None:
    for signal in getattr(decision, "signals", []):
        if getattr(signal, "signal_kind", "") == "forex":
            return signal
    return getattr(decision, "signal", None)


def _run_live_strategy_impl() -> dict[str, Any]:
    trading_main = _load_trading_main()
    now_utc = dt.datetime.now(dt.timezone.utc)

    if not trading_main.is_market_open(now_utc):
        return {
            "symbol": "XAUUSD",
            "signal": "NO_SIGNAL",
            "entry": None,
            "sl": None,
            "tp": None,
            "status": "market_closed",
            "reason": "market_closed",
        }

    fetcher, indicators, signal_engine, risk_manager, _ = trading_main._build_components()
    trend_candles = fetcher.fetch_market_data(
        trading_main.SYMBOL,
        trading_main.TREND_TIMEFRAME,
        trading_main.CANDLE_LIMIT,
    )
    entry_candles = fetcher.fetch_market_data(
        trading_main.SYMBOL,
        trading_main.ENTRY_TIMEFRAME,
        trading_main.CANDLE_LIMIT,
    )

    trend_indicators = indicators.add_indicators(trend_candles)
    entry_indicators = indicators.add_indicators(entry_candles)
    decision = signal_engine.evaluate(trend_indicators, entry_indicators, now_utc=now_utc)
    selected_signal = _select_preferred_signal(decision)

    if selected_signal is None:
        return {
            "symbol": "XAUUSD",
            "signal": "NO_SIGNAL",
            "entry": None,
            "sl": None,
            "tp": None,
            "status": "evaluated",
            "reason": decision.reason,
            "score": int(decision.score),
            "threshold": int(decision.score_threshold),
            "session": str(decision.session),
            "market_regime": str(decision.market_regime),
            "volatility_regime": str(decision.volatility_regime),
            "strategy_behavior": str(decision.strategy_behavior),
            "regime_confidence": float(decision.regime_confidence),
            "breakdown": dict(decision.breakdown),
        }

    risk_allowed, risk_reason = risk_manager.can_emit_signal(selected_signal)
    return {
        "symbol": getattr(selected_signal, "display_symbol", "XAUUSD"),
        "signal": str(selected_signal.direction),
        "entry": round(float(selected_signal.entry_price), 4),
        "sl": None if selected_signal.stop_loss is None else round(float(selected_signal.stop_loss), 4),
        "tp": None if selected_signal.take_profit is None else round(float(selected_signal.take_profit), 4),
        "status": "signal_ready",
        "reason": decision.reason,
        "score": int(decision.score),
        "threshold": int(decision.score_threshold),
        "session": str(decision.session),
        "market_regime": str(decision.market_regime),
        "volatility_regime": str(decision.volatility_regime),
        "strategy_behavior": str(decision.strategy_behavior),
        "regime_confidence": float(decision.regime_confidence),
        "risk_allowed": bool(risk_allowed),
        "risk_reason": str(risk_reason),
        "signal_kind": str(selected_signal.signal_kind),
        "timestamp_utc": pd.Timestamp(selected_signal.timestamp_utc).isoformat(),
        "breakdown": dict(decision.breakdown),
    }


def run_live_strategy(timeout_seconds: int = 90) -> dict[str, Any]:
    """Run one live strategy evaluation without entering the infinite loop."""

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run_live_strategy_impl)
        try:
            return future.result(timeout=max(1, int(timeout_seconds)))
        except FuturesTimeout as error:
            raise TimeoutError(
                f"Live strategy evaluation exceeded timeout after {timeout_seconds}s."
            ) from error


def start_strategy_execution(
    *,
    python_executable: str | None = None,
    create_window: bool = False,
) -> dict[str, Any]:
    """Optional helper for starting the main strategy process from OpenClaw."""

    creation_flags = 0
    if not create_window and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creation_flags |= subprocess.CREATE_NO_WINDOW
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP

    process = subprocess.Popen(
        [python_executable or sys.executable, str(PROJECT_ROOT / "main.py")],
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    return {
        "status": "started",
        "pid": process.pid,
        "command": [python_executable or sys.executable, str(PROJECT_ROOT / "main.py")],
    }


def stop_strategy_execution(pid: int) -> dict[str, Any]:
    """Optional helper for stopping the main strategy process from OpenClaw."""

    completed = subprocess.run(
        ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "status": "stopped" if completed.returncode == 0 else "not_running",
        "pid": int(pid),
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }
