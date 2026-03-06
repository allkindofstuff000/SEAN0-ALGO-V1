from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import ccxt
import pandas as pd


LOGGER = logging.getLogger(__name__)


@dataclass
class DataFetcher:
    """
    Robust OHLCV fetcher for Binance (futures by default).
    Handles retries, symbol resolution, and closed-candle filtering.
    """

    exchange_name: str = "binance"
    default_limit: int = 300
    max_retries: int = 5
    retry_base_seconds: float = 1.5
    exchange_options: dict[str, Any] = field(
        default_factory=lambda: {
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        }
    )

    def __post_init__(self) -> None:
        if self.exchange_name.lower() != "binance":
            raise ValueError("This phase supports EXCHANGE='binance' only.")
        self.exchange = ccxt.binance(self.exchange_options)
        self._markets_loaded = False
        self._symbol_cache: dict[str, str] = {}

    def fetch_ohlcv(self, symbol: str = "XAUUSDT", timeframe: str = "15m", limit: int = 300) -> pd.DataFrame:
        """
        Fetch OHLCV candles and return a DataFrame with:
        timestamp, open, high, low, close, volume
        Only closed candles are returned.
        """
        effective_limit = max(50, int(limit or self.default_limit))
        request_limit = effective_limit + 50
        resolved_symbol = self._resolve_symbol(symbol)
        raw = self._fetch_with_retry(resolved_symbol=resolved_symbol, timeframe=timeframe, limit=request_limit)
        frame = self._to_dataframe(raw)
        frame = self._drop_unfinished_candle(frame, timeframe=timeframe)
        if len(frame) > effective_limit:
            frame = frame.tail(effective_limit).reset_index(drop=True)
        return frame

    def _ensure_markets(self) -> None:
        if self._markets_loaded:
            return
        self.exchange.load_markets()
        self._markets_loaded = True

    def _resolve_symbol(self, raw_symbol: str) -> str:
        if raw_symbol in self._symbol_cache:
            return self._symbol_cache[raw_symbol]

        self._ensure_markets()
        markets = self.exchange.markets or {}
        if raw_symbol in markets:
            self._symbol_cache[raw_symbol] = raw_symbol
            return raw_symbol

        target = raw_symbol.replace("/", "").replace(":", "").upper()
        for market in markets.values():
            market_id = str(market.get("id", "")).replace("/", "").replace(":", "").upper()
            market_symbol = str(market.get("symbol", "")).replace("/", "").replace(":", "").upper()
            if target in {market_id, market_symbol}:
                resolved = str(market["symbol"])
                self._symbol_cache[raw_symbol] = resolved
                LOGGER.info("resolved_symbol raw=%s resolved=%s", raw_symbol, resolved)
                return resolved

        raise ValueError(f"Could not resolve symbol '{raw_symbol}' on Binance.")

    def _fetch_with_retry(self, resolved_symbol: str, timeframe: str, limit: int) -> list[list[float]]:
        for attempt in range(1, self.max_retries + 1):
            try:
                rows = self.exchange.fetch_ohlcv(resolved_symbol, timeframe=timeframe, limit=limit)
                if not rows:
                    raise RuntimeError("empty_ohlcv_response")
                return rows
            except (ccxt.NetworkError, ccxt.ExchangeError, RuntimeError) as error:
                wait = min(20.0, self.retry_base_seconds * (2 ** (attempt - 1)))
                LOGGER.warning(
                    "fetch_ohlcv_failed symbol=%s timeframe=%s attempt=%s/%s error=%s retry_in=%.2fs",
                    resolved_symbol,
                    timeframe,
                    attempt,
                    self.max_retries,
                    error,
                    wait,
                )
                if attempt >= self.max_retries:
                    raise
                time.sleep(wait)

        raise RuntimeError("unexpected_retry_exit")

    @staticmethod
    def _to_dataframe(raw_rows: list[list[float]]) -> pd.DataFrame:
        frame = pd.DataFrame(raw_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        return frame

    def _drop_unfinished_candle(self, frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        if frame.empty:
            return frame
        timeframe_ms = int(self.exchange.parse_timeframe(timeframe) * 1000)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        open_ms = frame["timestamp"].apply(lambda ts: int(ts.timestamp() * 1000))
        close_ms = open_ms + timeframe_ms
        closed = frame[close_ms <= now_ms].copy()
        return closed.reset_index(drop=True)
