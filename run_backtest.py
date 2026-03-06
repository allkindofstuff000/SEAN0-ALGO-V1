from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from bot.backtest.backtest_runner import BacktestConfig, BacktestRunner
from bot.backtest.report_generator import ReportGenerator
from bot.config.config import (
    BACKTEST_CSV_PATH,
    BACKTEST_END_DATE,
    BACKTEST_OUTPUT_DIR,
    BACKTEST_START_DATE,
    BACKTEST_SYMBOL,
    BACKTEST_TIMEFRAME,
    BINARY_EXPIRY,
    SIGNAL_MODE,
)


LOGGER = logging.getLogger("bot.backtest.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the trading strategy backtester.")
    parser.add_argument("--csv", default=BACKTEST_CSV_PATH, help="Optional historical CSV path.")
    parser.add_argument("--symbol", default=BACKTEST_SYMBOL, help="Instrument symbol to backtest.")
    parser.add_argument("--timeframe", default=BACKTEST_TIMEFRAME, help="Backtest timeframe, e.g. 15m.")
    parser.add_argument("--start-date", default=BACKTEST_START_DATE, help="Historical window start date in UTC.")
    parser.add_argument("--end-date", default=BACKTEST_END_DATE, help="Historical window end date in UTC.")
    parser.add_argument("--mode", default=SIGNAL_MODE, choices=["BINARY", "FOREX"], help="Execution mode.")
    parser.add_argument("--binary-expiry", default=BINARY_EXPIRY, help="Binary expiry, e.g. 30m.")
    parser.add_argument("--output-dir", default="", help="Directory for CSV, JSON, and chart outputs.")
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
    config = BacktestConfig(
        symbol=args.symbol,
        timeframe=args.timeframe,
        start_date=args.start_date or None,
        end_date=args.end_date or None,
        csv_path=args.csv or None,
        mode=args.mode,
        binary_expiry=args.binary_expiry,
        output_dir=output_dir,
    )

    LOGGER.info(
        "starting_backtest symbol=%s timeframe=%s mode=%s start=%s end=%s csv=%s output=%s",
        config.symbol,
        config.timeframe,
        config.mode,
        config.start_date,
        config.end_date,
        config.csv_path,
        config.output_dir,
    )

    runner = BacktestRunner(config=config)
    result = runner.run()
    artifacts = ReportGenerator(output_dir=config.output_dir).generate(result=result)

    summary = result.summary()
    print("Backtest Completed")
    print()
    print(f"Trades: {summary['total_trades']}")
    print(f"Wins: {summary['wins']}")
    print(f"Losses: {summary['losses']}")
    print(f"Win Rate: {summary['win_rate']}%")
    print(f"Max Drawdown: {summary['max_drawdown']}%")
    print(f"Profit Factor: {summary['profit_factor']}")
    print(f"Average Score: {summary['average_score']}")
    print(f"Reports: {config.output_dir}")
    for name, path in artifacts.items():
        print(f"{name}: {path}")


def _default_output_dir(symbol: str, timeframe: str, mode: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    label = f"{stamp}_{symbol.lower()}_{timeframe}_{mode.lower()}"
    return BACKTEST_OUTPUT_DIR / label


if __name__ == "__main__":
    main()
