from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

import ccxt
import pandas as pd

from indicator_engine import IndicatorEngine
from signal_logic import BTC_SYMBOLS, TradeSignal, XAU_SYMBOLS, SignalLogic


LOGGER = logging.getLogger("simple_backtest")

STRATEGY_CONFIG = {
    "XAUUSDT": {
        "trend_tf": "15m",
        "entry_tf": "5m",
    },
    "BTCUSDT": {
        "trend_tf": "1h",
        "entry_tf": "15m",
    },
}


@dataclass
class SimulatedTrade:
    entry_time_utc: str
    exit_time_utc: str
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    stop_loss: float | None
    take_profit: float | None
    score: int
    outcome: str
    r_multiple: float
    hold_candles: int


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple multi-timeframe backtest for the simplified signal engine.")
    parser.add_argument("--symbol", default="XAUUSDT", help="Raw exchange symbol to backtest.")
    parser.add_argument("--limit", type=int, default=1500, help="Number of historical entry candles to fetch.")
    parser.add_argument(
        "--max-hold-candles",
        type=int,
        default=30,
        help="Maximum number of entry candles to hold a forex trade before closing at market.",
    )
    return parser.parse_args()


def normalize_symbol(value: str) -> str:
    return value.replace("/", "").replace(":", "").replace("-", "").replace(" ", "").upper()


def resolve_symbol(exchange: ccxt.Exchange, raw_symbol: str) -> str:
    exchange.load_markets()
    markets = exchange.markets or {}
    if raw_symbol in markets:
        return raw_symbol

    target = normalize_symbol(raw_symbol)
    matches: list[dict] = []
    for market in markets.values():
        market_id = normalize_symbol(str(market.get("id", "")))
        market_symbol = normalize_symbol(str(market.get("symbol", "")))
        if target in {market_id, market_symbol}:
            matches.append(market)

    if not matches:
        raise ValueError(f"Could not resolve symbol '{raw_symbol}' on Binance.")

    best_match = max(matches, key=lambda market: (1 if market.get("contract") else 0, 1 if market.get("linear") else 0, 1 if ":" in str(market.get("symbol", "")) else 0))
    resolved = str(best_match["symbol"])
    LOGGER.info("resolved_symbol raw=%s resolved=%s", raw_symbol, resolved)
    return resolved


def fetch_historical_ohlcv(exchange: ccxt.Exchange, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    resolved_symbol = resolve_symbol(exchange, symbol)

    rows: list[list[float]] = []
    remaining = max(1, int(limit))
    since = None
    max_batch = 1500

    while remaining > 0:
        batch_limit = min(remaining, max_batch)
        batch = exchange.fetch_ohlcv(resolved_symbol, timeframe=timeframe, since=since, limit=batch_limit)
        if not batch:
            break
        rows.extend(batch)
        remaining -= len(batch)
        if len(batch) < batch_limit:
            break
        since = int(batch[-1][0]) + 1

    if not rows:
        raise RuntimeError(f"No OHLCV rows returned from Binance for {symbol} {timeframe}.")

    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    numeric_columns = ["open", "high", "low", "close", "volume"]
    frame[numeric_columns] = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=numeric_columns)
    frame = frame.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)

    timeframe_ms = int(exchange.parse_timeframe(timeframe) * 1000)
    now_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)
    close_ms = (frame["timestamp"].astype("int64") // 10**6) + timeframe_ms
    frame = frame.loc[close_ms <= now_ms].reset_index(drop=True)
    return frame.tail(limit).reset_index(drop=True)


def fetch_strategy_candles(symbol: str, limit: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    normalized_symbol = normalize_symbol(symbol)
    if normalized_symbol not in STRATEGY_CONFIG:
        raise ValueError(f"Unsupported symbol for backtest: {symbol}")

    strategy = STRATEGY_CONFIG[normalized_symbol]
    exchange = ccxt.binance(
        {
            "enableRateLimit": True,
            "timeout": 30000,
            "options": {"defaultType": "future"},
        }
    )
    trend_limit = max(limit, 400)
    trend_candles = fetch_historical_ohlcv(exchange, normalized_symbol, strategy["trend_tf"], trend_limit)
    entry_candles = fetch_historical_ohlcv(exchange, normalized_symbol, strategy["entry_tf"], limit)
    return trend_candles, entry_candles


def simulate_binary_trade(signal: TradeSignal, future_candles: pd.DataFrame) -> tuple[SimulatedTrade, int]:
    expiry_candles = max(1, signal.expiry_minutes // signal.entry_timeframe_minutes) if signal.expiry_minutes else 1
    horizon = future_candles.head(expiry_candles).reset_index(drop=True)
    if len(horizon) < expiry_candles:
        raise ValueError("Not enough future candles available to settle binary trade.")

    expiry_candle = horizon.iloc[-1]
    expiry_close = float(expiry_candle["close"])
    is_win = expiry_close > signal.entry_price if signal.direction == "BUY" else expiry_close < signal.entry_price
    return (
        SimulatedTrade(
            entry_time_utc=signal.timestamp_utc.isoformat(),
            exit_time_utc=pd.Timestamp(expiry_candle["timestamp"]).isoformat(),
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=float(signal.entry_price),
            exit_price=expiry_close,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            score=signal.score,
            outcome="WIN" if is_win else "LOSS",
            r_multiple=1.0 if is_win else -1.0,
            hold_candles=expiry_candles,
        ),
        expiry_candles - 1,
    )


def simulate_forex_trade(
    signal: TradeSignal,
    future_candles: pd.DataFrame,
    max_hold_candles: int,
) -> tuple[SimulatedTrade, int]:
    if signal.stop_loss is None or signal.take_profit is None:
        raise ValueError("Signal is missing stop loss / take profit values.")

    entry = float(signal.entry_price)
    stop_loss = float(signal.stop_loss)
    take_profit = float(signal.take_profit)
    risk_distance = abs(entry - stop_loss)
    reward_distance = abs(take_profit - entry)
    reward_risk = reward_distance / risk_distance if risk_distance > 0 else 0.0

    horizon = future_candles.head(max_hold_candles).reset_index(drop=True)
    if horizon.empty:
        raise ValueError("No future candles available to simulate forex trade outcome.")

    for offset, candle in horizon.iterrows():
        candle_high = float(candle["high"])
        candle_low = float(candle["low"])
        candle_time = pd.Timestamp(candle["timestamp"]).isoformat()

        if signal.direction == "BUY":
            stop_hit = candle_low <= stop_loss
            target_hit = candle_high >= take_profit
        else:
            stop_hit = candle_high >= stop_loss
            target_hit = candle_low <= take_profit

        if stop_hit:
            return (
                SimulatedTrade(
                    entry_time_utc=signal.timestamp_utc.isoformat(),
                    exit_time_utc=candle_time,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    entry_price=entry,
                    exit_price=stop_loss,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    score=signal.score,
                    outcome="LOSS",
                    r_multiple=-1.0,
                    hold_candles=offset + 1,
                ),
                offset,
            )

        if target_hit:
            return (
                SimulatedTrade(
                    entry_time_utc=signal.timestamp_utc.isoformat(),
                    exit_time_utc=candle_time,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    entry_price=entry,
                    exit_price=take_profit,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    score=signal.score,
                    outcome="WIN",
                    r_multiple=round(reward_risk, 4),
                    hold_candles=offset + 1,
                ),
                offset,
            )

    final_candle = horizon.iloc[-1]
    final_close = float(final_candle["close"])
    if signal.direction == "BUY":
        r_multiple = (final_close - entry) / risk_distance if risk_distance > 0 else 0.0
    else:
        r_multiple = (entry - final_close) / risk_distance if risk_distance > 0 else 0.0

    outcome = "WIN" if r_multiple > 0 else "LOSS"
    return (
        SimulatedTrade(
            entry_time_utc=signal.timestamp_utc.isoformat(),
            exit_time_utc=pd.Timestamp(final_candle["timestamp"]).isoformat(),
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=entry,
            exit_price=final_close,
            stop_loss=stop_loss,
            take_profit=take_profit,
            score=signal.score,
            outcome=outcome,
            r_multiple=round(r_multiple, 4),
            hold_candles=len(horizon),
        ),
        len(horizon) - 1,
    )


def simulate_trade(
    signal: TradeSignal,
    future_candles: pd.DataFrame,
    max_hold_candles: int,
) -> tuple[SimulatedTrade, int]:
    if signal.signal_kind == "binary":
        return simulate_binary_trade(signal, future_candles)
    return simulate_forex_trade(signal, future_candles, max_hold_candles)


def run_backtest(
    trend_candles: pd.DataFrame,
    entry_candles: pd.DataFrame,
    symbol: str,
    max_hold_candles: int,
) -> list[SimulatedTrade]:
    normalized_symbol = normalize_symbol(symbol)
    indicators = IndicatorEngine()
    signal_logic = SignalLogic(symbol=normalized_symbol)
    trend_indicators = indicators.add_indicators(trend_candles)
    entry_indicators = indicators.add_indicators(entry_candles)

    trades: list[SimulatedTrade] = []
    index = 220
    while index < len(entry_indicators) - 1:
        entry_window = entry_indicators.iloc[: index + 1].copy()
        entry_time = pd.Timestamp(entry_window["timestamp"].iloc[-1])
        trend_window = trend_indicators.loc[trend_indicators["timestamp"] <= entry_time].copy()
        if len(trend_window) < 220:
            index += 1
            continue

        decision = signal_logic.evaluate(
            trend_candles=trend_window,
            entry_candles=entry_window,
            now_utc=entry_time.to_pydatetime(),
        )

        if not decision.signal_generated or decision.signal is None:
            index += 1
            continue

        future_candles = entry_indicators.iloc[index + 1 :].copy()
        trade, exit_offset = simulate_trade(
            signal=decision.signal,
            future_candles=future_candles,
            max_hold_candles=max_hold_candles,
        )
        trades.append(trade)
        index += exit_offset + 2

    return trades


def summarize_trades(trades: list[SimulatedTrade]) -> dict[str, float | int]:
    wins = sum(1 for trade in trades if trade.outcome == "WIN")
    losses = sum(1 for trade in trades if trade.outcome == "LOSS")
    total = len(trades)
    win_rate = round((wins / total) * 100.0, 2) if total else 0.0
    avg_r = round(sum(trade.r_multiple for trade in trades) / total, 4) if total else 0.0
    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_r": avg_r,
    }


def print_summary(summary: dict[str, float | int]) -> None:
    print(f"Trades: {summary['trades']}")
    print(f"Wins: {summary['wins']}")
    print(f"Losses: {summary['losses']}")
    print(f"Win Rate: {summary['win_rate']}%")
    print(f"Avg R: {summary['avg_r']}")


def main() -> None:
    configure_logging()
    args = parse_args()
    normalized_symbol = normalize_symbol(args.symbol)
    if normalized_symbol not in XAU_SYMBOLS | BTC_SYMBOLS:
        raise ValueError(f"Unsupported symbol: {args.symbol}")

    trend_candles, entry_candles = fetch_strategy_candles(normalized_symbol, args.limit)
    trades = run_backtest(
        trend_candles=trend_candles,
        entry_candles=entry_candles,
        symbol=normalized_symbol,
        max_hold_candles=args.max_hold_candles,
    )
    summary = summarize_trades(trades)
    print_summary(summary)


if __name__ == "__main__":
    main()
