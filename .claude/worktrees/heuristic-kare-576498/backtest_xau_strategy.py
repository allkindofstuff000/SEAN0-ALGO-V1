from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest_forex_engine import BACKTEST_SUMMARY_PATH
from backtest_forex_engine import DECISION_TRACE_PATH
from backtest_forex_engine import DEFAULT_LOOKBACK_DAYS
from backtest_forex_engine import DEFAULT_MAX_HOLD
from backtest_forex_engine import DEFAULT_RISK_PER_TRADE
from backtest_forex_engine import DEFAULT_STARTING_BALANCE
from backtest_forex_engine import EQUITY_CURVE_PATH
from backtest_forex_engine import LOG_DIR
from backtest_forex_engine import ROOT
from backtest_forex_engine import TIMESTAMP_FILENAME_FORMAT
from backtest_forex_engine import TRADES_CSV_PATH
from backtest_forex_engine import load_local_env
from backtest_forex_engine import main as forex_main
from backtest_forex_engine import parse_args
from backtest_forex_engine import parse_date_utc
from backtest_forex_engine import save_backtest_outputs
from backtest_forex_engine import save_equity_curve
from backtest_forex_engine import run_backtest as run_forex_backtest


def run_backtest(
    *,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    mode: str = "forex",
    max_hold_bars: int = DEFAULT_MAX_HOLD,
) -> tuple[pd.DataFrame, dict[str, float]]:
    normalized_mode = str(mode).strip().lower()
    if normalized_mode != "forex":
        raise ValueError("Only forex mode is supported by the current backtester.")
    return run_forex_backtest(
        start_utc=start_utc,
        end_utc=end_utc,
        max_hold_bars=max_hold_bars,
    )


def main() -> int:
    return forex_main()


if __name__ == "__main__":
    raise SystemExit(main())
