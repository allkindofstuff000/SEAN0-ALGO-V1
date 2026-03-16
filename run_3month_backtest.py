"""Run 3-month backtest with $3,000 balance and 5% risk per trade."""
import pandas as pd
from backtest_forex_engine import (
    load_local_env,
    parse_date_utc,
    run_backtest,
    save_backtest_outputs,
)

load_local_env()

start_utc = parse_date_utc("2025-12-13")
end_utc = parse_date_utc("2026-03-13", inclusive_end=True)

print(f"Running 3-month backtest: {start_utc.date()} -> 2026-03-13")
print("Balance: $3,000  |  Risk: 5%  |  R:R 2:1")
print("-" * 50)

trades_df, metrics = run_backtest(
    start_utc=start_utc,
    end_utc=end_utc,
    max_hold_bars=12,
    starting_balance=3_000.0,
    risk_per_trade=0.05,
)

save_backtest_outputs(trades_df, metrics, start_label="2025-12-13", end_label="2026-03-13")

print("\n3-MONTH BACKTEST RESULTS ($3,000 / 5% Risk)")
print("=" * 50)
print(f"Window       : 2025-12-13 -> 2026-03-13")
print(f"Start Balance: $3,000.00")
print(f"End Balance  : ${metrics['ending_balance']:,.2f}")
net = metrics['ending_balance'] - 3000
print(f"Net P&L      : ${net:+,.2f}  ({net/3000*100:+.2f}%)")
print(f"Total Trades : {int(metrics['total_trades'])}")
print(f"Wins         : {int(metrics['wins'])}")
print(f"Losses       : {int(metrics['losses'])}")
print(f"Win Rate     : {metrics['win_rate']:.2f}%")
print(f"Avg R        : {metrics['average_r']:.4f}")
print(f"Profit Factor: {metrics['profit_factor']:.2f}")
print(f"Max Drawdown : {metrics['max_drawdown_r']:.2f} R")
