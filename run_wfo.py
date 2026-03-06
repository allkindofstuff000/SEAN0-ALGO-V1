from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

import json

from bot.config.config import (
    BACKTEST_CSV_PATH,
    BACKTEST_END_DATE,
    BACKTEST_START_DATE,
    BACKTEST_SYMBOL,
    BACKTEST_TIMEFRAME,
    PARAM_ATR_RANGE,
    PARAM_ATR_STEP,
    PARAM_THRESHOLD_RANGE,
    PARAM_THRESHOLD_STEP,
    SIGNAL_MODE,
    WFO_OUTPUT_DIR,
    WFO_STEP_DAYS,
    WFO_TEST_DAYS,
    WFO_TRAINING_DAYS,
)
from bot.wfo.wfo_report import WFOReport
from bot.wfo.wfo_runner import WalkForwardConfig, WalkForwardRunner


LOGGER = logging.getLogger("bot.wfo.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run walk-forward optimization for the trading engine.")
    parser.add_argument("--csv", default=BACKTEST_CSV_PATH, help="Optional historical CSV path.")
    parser.add_argument("--symbol", default=BACKTEST_SYMBOL, help="Instrument symbol to validate.")
    parser.add_argument("--timeframe", default=BACKTEST_TIMEFRAME, help="Validation timeframe, e.g. 1h.")
    parser.add_argument("--start-date", default=BACKTEST_START_DATE, help="Historical window start date in UTC.")
    parser.add_argument("--end-date", default=BACKTEST_END_DATE, help="Historical window end date in UTC.")
    parser.add_argument("--mode", default=SIGNAL_MODE, choices=["BINARY", "FOREX"], help="Execution mode.")
    parser.add_argument("--training-days", type=int, default=WFO_TRAINING_DAYS, help="Training window length in days.")
    parser.add_argument("--testing-days", type=int, default=WFO_TEST_DAYS, help="Testing window length in days.")
    parser.add_argument("--step-days", type=int, default=WFO_STEP_DAYS, help="Rolling step size in days.")
    parser.add_argument("--threshold-min", type=int, default=PARAM_THRESHOLD_RANGE[0], help="Minimum threshold value.")
    parser.add_argument("--threshold-max", type=int, default=PARAM_THRESHOLD_RANGE[1], help="Maximum threshold value.")
    parser.add_argument("--threshold-step", type=int, default=PARAM_THRESHOLD_STEP, help="Threshold increment.")
    parser.add_argument("--atr-min", type=float, default=PARAM_ATR_RANGE[0], help="Minimum ATR multiplier.")
    parser.add_argument("--atr-max", type=float, default=PARAM_ATR_RANGE[1], help="Maximum ATR multiplier.")
    parser.add_argument("--atr-step", type=float, default=PARAM_ATR_STEP, help="ATR multiplier increment.")
    parser.add_argument("--cooldowns", default="2,4,6", help="Comma-separated cooldown candidates.")
    parser.add_argument("--max-signals", default="2,3,4", help="Comma-separated max signals/day candidates.")
    parser.add_argument("--output-dir", default="", help="Directory for WFO artifacts.")
    parser.add_argument("--log-level", default="INFO", help="Logging level.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir(args.symbol, args.timeframe, args.mode)
    config = WalkForwardConfig(
        symbol=args.symbol,
        timeframe=args.timeframe,
        start_date=args.start_date or None,
        end_date=args.end_date or None,
        csv_path=args.csv or None,
        mode=args.mode,
        training_days=int(args.training_days),
        testing_days=int(args.testing_days),
        step_days=int(args.step_days),
        threshold_bounds=(int(args.threshold_min), int(args.threshold_max)),
        threshold_step=int(args.threshold_step),
        atr_bounds=(float(args.atr_min), float(args.atr_max)),
        atr_step=float(args.atr_step),
        cooldown_values=_parse_int_list(args.cooldowns),
        max_signals_values=_parse_int_list(args.max_signals),
        output_dir=output_dir,
    )

    LOGGER.info(
        "starting_wfo symbol=%s timeframe=%s mode=%s start=%s end=%s output=%s",
        config.symbol,
        config.timeframe,
        config.mode,
        config.start_date,
        config.end_date,
        config.output_dir,
    )

    result = WalkForwardRunner(config=config).run()
    artifacts = WFOReport(output_dir=config.output_dir).generate(result=result)

    summary_path = artifacts["wfo_summary"]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    overview = summary["overview"]

    print("Walk Forward Completed")
    print()
    print(f"Windows tested: {overview['windows_tested']}")
    print(f"Average Win Rate: {summary['overall_trade_metrics']['win_rate']}%")
    print(f"Profit Factor: {overview['average_profit_factor']}")
    print(f"Max Drawdown: {overview['max_drawdown']}%")
    print(f"Reports: {config.output_dir}")
    for name, path in artifacts.items():
        print(f"{name}: {path}")


def _default_output_dir(symbol: str, timeframe: str, mode: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return WFO_OUTPUT_DIR / f"{stamp}_{symbol.lower()}_{timeframe}_{mode.lower()}"


def _parse_int_list(raw: str) -> tuple[int, ...]:
    values = [int(part.strip()) for part in str(raw).split(",") if part.strip()]
    if not values:
        raise ValueError("Expected at least one integer candidate value.")
    return tuple(values)


if __name__ == "__main__":
    main()
