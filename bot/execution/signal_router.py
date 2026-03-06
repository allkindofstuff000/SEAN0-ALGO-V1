from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.signals.signal_logic import TradingSignal


@dataclass
class SignalRouter:
    """
    Convert an approved signal into the payload required by the execution venue.
    """

    mode: str = "BINARY"
    binary_expiry: str = "30m"
    forex_stop_loss_atr_multiplier: float = 1.5
    forex_take_profit_atr_multiplier: float = 2.5
    pair_aliases: dict[str, str] = field(default_factory=lambda: {"XAUUSDT": "XAUUSD"})

    def route(self, signal: TradingSignal) -> dict[str, Any]:
        mode = self.mode.strip().upper()
        if mode == "BINARY":
            return self._route_binary(signal)
        if mode == "FOREX":
            return self._route_forex(signal)
        raise ValueError(f"Unsupported signal routing mode: {self.mode}")

    def _route_binary(self, signal: TradingSignal) -> dict[str, Any]:
        return {
            "mode": "binary",
            "pair": self._normalize_pair(signal.pair),
            "direction": signal.direction,
            "expiry": self.binary_expiry,
            "score": int(signal.score),
        }

    def _route_forex(self, signal: TradingSignal) -> dict[str, Any]:
        entry = float(signal.price)
        atr = float(signal.atr) if float(signal.atr) > 0 else max(entry * 0.0015, 0.01)
        if signal.direction == "CALL":
            direction = "BUY"
            stop_loss = entry - (atr * self.forex_stop_loss_atr_multiplier)
            take_profit = entry + (atr * self.forex_take_profit_atr_multiplier)
        else:
            direction = "SELL"
            stop_loss = entry + (atr * self.forex_stop_loss_atr_multiplier)
            take_profit = entry - (atr * self.forex_take_profit_atr_multiplier)

        return {
            "mode": "forex",
            "pair": self._normalize_pair(signal.pair),
            "direction": direction,
            "entry": round(entry, 4),
            "stop_loss": round(stop_loss, 4),
            "take_profit": round(take_profit, 4),
            "score": int(signal.score),
        }

    def _normalize_pair(self, raw_pair: str) -> str:
        if raw_pair in self.pair_aliases:
            return self.pair_aliases[raw_pair]
        if raw_pair.endswith("USDT"):
            return f"{raw_pair[:-4]}USD"
        return raw_pair
