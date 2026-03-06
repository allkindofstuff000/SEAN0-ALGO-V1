from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from bot.signals.signal_logic import TradingSignal


@dataclass
class SimulatedTrade:
    pair: str
    mode: str
    timeframe: str
    session: str
    regime: str
    signal_direction: str
    execution_direction: str
    score: int
    score_threshold: int
    entry_index: int
    entry_timestamp: datetime
    entry_price: float
    risk_amount: float
    stop_loss: float | None = None
    take_profit: float | None = None
    expiry_candles: int | None = None
    expiry_index: int | None = None
    exit_index: int | None = None
    exit_timestamp: datetime | None = None
    exit_price: float | None = None
    result: str | None = None
    pnl: float = 0.0
    pnl_r: float = 0.0
    equity_before: float = 0.0
    equity_after: float = 0.0
    duration_candles: int = 0
    exit_reason: str | None = None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["entry_timestamp"] = self.entry_timestamp.isoformat()
        record["exit_timestamp"] = self.exit_timestamp.isoformat() if self.exit_timestamp else None
        record["entry_price"] = round(float(self.entry_price), 6)
        record["exit_price"] = round(float(self.exit_price), 6) if self.exit_price is not None else None
        record["stop_loss"] = round(float(self.stop_loss), 6) if self.stop_loss is not None else None
        record["take_profit"] = round(float(self.take_profit), 6) if self.take_profit is not None else None
        record["risk_amount"] = round(float(self.risk_amount), 4)
        record["pnl"] = round(float(self.pnl), 4)
        record["pnl_r"] = round(float(self.pnl_r), 4)
        record["equity_before"] = round(float(self.equity_before), 4)
        record["equity_after"] = round(float(self.equity_after), 4)
        return record


@dataclass
class TradeSimulator:
    """
    Simulate pending and settled trades using candle-by-candle execution.
    """

    initial_capital: float = 10_000.0
    risk_per_trade_pct: float = 1.0
    binary_payout: float = 0.8
    forex_max_holding_candles: int = 16
    active_trades: list[SimulatedTrade] = field(default_factory=list, init=False)
    closed_trades: list[dict[str, Any]] = field(default_factory=list, init=False)
    equity: float = field(init=False)

    def __post_init__(self) -> None:
        self.equity = float(self.initial_capital)

    def open_trade(
        self,
        *,
        signal: TradingSignal,
        routed_signal: dict[str, Any],
        timeframe: str,
        entry_index: int,
        entry_timestamp: datetime,
    ) -> SimulatedTrade:
        mode = str(routed_signal.get("mode", "binary")).upper()
        risk_amount = max(self.equity * (float(self.risk_per_trade_pct) / 100.0), 0.0)
        trade = SimulatedTrade(
            pair=str(routed_signal.get("pair", signal.pair)),
            mode=mode,
            timeframe=timeframe,
            session=signal.session,
            regime=signal.regime,
            signal_direction=signal.direction,
            execution_direction=str(routed_signal.get("direction", signal.direction)),
            score=int(signal.score),
            score_threshold=int(signal.score_threshold),
            entry_index=int(entry_index),
            entry_timestamp=entry_timestamp,
            entry_price=float(signal.price),
            risk_amount=risk_amount,
            equity_before=float(self.equity),
        )

        if mode == "BINARY":
            expiry_candles = self._duration_to_candles(str(routed_signal.get("expiry", "30m")), timeframe)
            trade.expiry_candles = expiry_candles
            trade.expiry_index = entry_index + expiry_candles
        elif mode == "FOREX":
            trade.stop_loss = float(routed_signal["stop_loss"])
            trade.take_profit = float(routed_signal["take_profit"])
        else:
            raise ValueError(f"Unsupported trade mode: {mode}")

        self.active_trades.append(trade)
        return trade

    def settle_pending_trades(self, *, df: pd.DataFrame, current_index: int) -> list[dict[str, Any]]:
        settled: list[dict[str, Any]] = []
        remaining: list[SimulatedTrade] = []
        candle = df.iloc[current_index]

        for trade in self.active_trades:
            if trade.mode == "BINARY":
                if trade.expiry_index is None or current_index < trade.expiry_index:
                    remaining.append(trade)
                    continue
                settled.append(self._settle_binary(trade=trade, candle=df.iloc[trade.expiry_index], exit_index=trade.expiry_index, exit_reason="expiry"))
                continue

            settlement = self._check_forex_exit(trade=trade, candle=candle, current_index=current_index)
            if settlement is None:
                remaining.append(trade)
                continue
            settled.append(settlement)

        self.active_trades = remaining
        return settled

    def force_close_open_trades(self, *, df: pd.DataFrame) -> list[dict[str, Any]]:
        if df.empty:
            return []
        final_index = len(df) - 1
        final_candle = df.iloc[final_index]
        forced: list[dict[str, Any]] = []

        for trade in self.active_trades:
            if trade.mode == "BINARY":
                forced.append(
                    self._settle_binary(
                        trade=trade,
                        candle=final_candle,
                        exit_index=final_index,
                        exit_reason="end_of_data",
                    )
                )
            else:
                forced.append(
                    self._close_trade(
                        trade=trade,
                        exit_index=final_index,
                        exit_timestamp=self._coerce_timestamp(final_candle["timestamp"]),
                        exit_price=float(final_candle["close"]),
                        result=self._result_from_pnl(
                            self._forex_r_multiple(trade=trade, exit_price=float(final_candle["close"]))
                        ),
                        pnl_r=self._forex_r_multiple(trade=trade, exit_price=float(final_candle["close"])),
                        exit_reason="end_of_data",
                    )
                )

        self.active_trades = []
        return forced

    def _settle_binary(
        self,
        *,
        trade: SimulatedTrade,
        candle: pd.Series,
        exit_index: int,
        exit_reason: str,
    ) -> dict[str, Any]:
        exit_price = float(candle["close"])
        is_win = self._binary_is_win(trade=trade, exit_price=exit_price)
        pnl_r = float(self.binary_payout) if is_win else -1.0
        return self._close_trade(
            trade=trade,
            exit_index=exit_index,
            exit_timestamp=self._coerce_timestamp(candle["timestamp"]),
            exit_price=exit_price,
            result="WIN" if is_win else "LOSS",
            pnl_r=pnl_r,
            exit_reason=exit_reason,
        )

    def _check_forex_exit(
        self,
        *,
        trade: SimulatedTrade,
        candle: pd.Series,
        current_index: int,
    ) -> dict[str, Any] | None:
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        holding_candles = current_index - trade.entry_index
        exit_timestamp = self._coerce_timestamp(candle["timestamp"])

        if trade.execution_direction == "BUY":
            hit_stop = trade.stop_loss is not None and low <= float(trade.stop_loss)
            hit_target = trade.take_profit is not None and high >= float(trade.take_profit)
            if hit_stop and hit_target:
                return self._close_trade(
                    trade=trade,
                    exit_index=current_index,
                    exit_timestamp=exit_timestamp,
                    exit_price=float(trade.stop_loss),
                    result="LOSS",
                    pnl_r=-1.0,
                    exit_reason="stop_loss_intrabar_conflict",
                )
            if hit_stop:
                return self._close_trade(
                    trade=trade,
                    exit_index=current_index,
                    exit_timestamp=exit_timestamp,
                    exit_price=float(trade.stop_loss),
                    result="LOSS",
                    pnl_r=-1.0,
                    exit_reason="stop_loss",
                )
            if hit_target:
                return self._close_trade(
                    trade=trade,
                    exit_index=current_index,
                    exit_timestamp=exit_timestamp,
                    exit_price=float(trade.take_profit),
                    result="WIN",
                    pnl_r=self._target_r_multiple(trade),
                    exit_reason="take_profit",
                )
        else:
            hit_stop = trade.stop_loss is not None and high >= float(trade.stop_loss)
            hit_target = trade.take_profit is not None and low <= float(trade.take_profit)
            if hit_stop and hit_target:
                return self._close_trade(
                    trade=trade,
                    exit_index=current_index,
                    exit_timestamp=exit_timestamp,
                    exit_price=float(trade.stop_loss),
                    result="LOSS",
                    pnl_r=-1.0,
                    exit_reason="stop_loss_intrabar_conflict",
                )
            if hit_stop:
                return self._close_trade(
                    trade=trade,
                    exit_index=current_index,
                    exit_timestamp=exit_timestamp,
                    exit_price=float(trade.stop_loss),
                    result="LOSS",
                    pnl_r=-1.0,
                    exit_reason="stop_loss",
                )
            if hit_target:
                return self._close_trade(
                    trade=trade,
                    exit_index=current_index,
                    exit_timestamp=exit_timestamp,
                    exit_price=float(trade.take_profit),
                    result="WIN",
                    pnl_r=self._target_r_multiple(trade),
                    exit_reason="take_profit",
                )

        if holding_candles >= int(self.forex_max_holding_candles):
            pnl_r = self._forex_r_multiple(trade=trade, exit_price=close)
            return self._close_trade(
                trade=trade,
                exit_index=current_index,
                exit_timestamp=exit_timestamp,
                exit_price=close,
                result=self._result_from_pnl(pnl_r),
                pnl_r=pnl_r,
                exit_reason="max_holding_time",
            )

        return None

    def _close_trade(
        self,
        *,
        trade: SimulatedTrade,
        exit_index: int,
        exit_timestamp: datetime,
        exit_price: float,
        result: str,
        pnl_r: float,
        exit_reason: str,
    ) -> dict[str, Any]:
        trade.exit_index = int(exit_index)
        trade.exit_timestamp = exit_timestamp
        trade.exit_price = float(exit_price)
        trade.result = result
        trade.pnl_r = float(pnl_r)
        trade.pnl = float(trade.risk_amount) * float(pnl_r)
        self.equity += trade.pnl
        trade.equity_after = float(self.equity)
        trade.duration_candles = max(0, trade.exit_index - trade.entry_index)
        trade.exit_reason = exit_reason

        record = trade.to_record()
        self.closed_trades.append(record)
        return record

    @staticmethod
    def _binary_is_win(*, trade: SimulatedTrade, exit_price: float) -> bool:
        if trade.signal_direction == "CALL":
            return exit_price > trade.entry_price
        return exit_price < trade.entry_price

    @staticmethod
    def _result_from_pnl(pnl_r: float) -> str:
        if pnl_r > 0:
            return "WIN"
        if pnl_r < 0:
            return "LOSS"
        return "BREAKEVEN"

    @staticmethod
    def _target_r_multiple(trade: SimulatedTrade) -> float:
        if trade.stop_loss is None or trade.take_profit is None:
            return 0.0
        risk_distance = abs(float(trade.entry_price) - float(trade.stop_loss))
        reward_distance = abs(float(trade.take_profit) - float(trade.entry_price))
        if risk_distance <= 0:
            return 0.0
        return reward_distance / risk_distance

    @staticmethod
    def _forex_r_multiple(*, trade: SimulatedTrade, exit_price: float) -> float:
        if trade.stop_loss is None:
            return 0.0
        risk_distance = abs(float(trade.entry_price) - float(trade.stop_loss))
        if risk_distance <= 0:
            return 0.0
        if trade.execution_direction == "BUY":
            return (float(exit_price) - float(trade.entry_price)) / risk_distance
        return (float(trade.entry_price) - float(exit_price)) / risk_distance

    @staticmethod
    def _duration_to_candles(duration: str, timeframe: str) -> int:
        duration_minutes = TradeSimulator._duration_to_minutes(duration)
        timeframe_minutes = TradeSimulator._duration_to_minutes(timeframe)
        if timeframe_minutes <= 0:
            raise ValueError(f"Invalid timeframe: {timeframe}")
        return max(1, int(math.ceil(duration_minutes / timeframe_minutes)))

    @staticmethod
    def _duration_to_minutes(raw: str) -> int:
        value = str(raw).strip().lower()
        if value.endswith("m"):
            return int(value[:-1])
        if value.endswith("h"):
            return int(value[:-1]) * 60
        if value.endswith("d"):
            return int(value[:-1]) * 1440
        raise ValueError(f"Unsupported duration format: {raw}")

    @staticmethod
    def _coerce_timestamp(value: Any) -> datetime:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        return timestamp.to_pydatetime()
