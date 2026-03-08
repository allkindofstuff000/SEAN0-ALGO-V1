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
    Minimal XAUUSD-only market data fetcher.

    The trading engine treats XAUUSD as the public-facing symbol but resolves it
    to the exchange symbol available on Binance futures.
    """

    symbol: str = "XAUUSD"
    default_timeframe: str = "1m"
    min_candles: int = 300
    request_limit: int = 350
    max_retries: int = 5
    retry_base_seconds: float = 1.5
    exchange_options: dict[str, Any] = field(
        default_factory=lambda: {
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        }
    )

    def __post_init__(self) -> None:
        normalized_symbol = self.symbol.strip().upper()
        if normalized_symbol != "XAUUSD":
            raise ValueError("This MVP supports XAUUSD only.")

        self.exchange = ccxt.binance(self.exchange_options)
        self._markets_loaded = False
        self._resolved_symbol: str | None = None

    def fetch_candles(self, timeframe: str | None = None, limit: int | None = None) -> pd.DataFrame:
        selected_timeframe = (timeframe or self.default_timeframe).strip()
        selected_limit = max(self.min_candles, int(limit or self.request_limit))

        resolved_symbol = self._resolve_symbol()
        rows = self._fetch_with_retry(
            resolved_symbol=resolved_symbol,
            timeframe=selected_timeframe,
            limit=selected_limit + 50,
        )
        frame = self._to_dataframe(rows)
        closed = self._drop_unfinished_candle(frame=frame, timeframe=selected_timeframe)
        if len(closed) < self.min_candles:
            raise RuntimeError(
                f"Not enough closed candles for XAUUSD on {selected_timeframe}. "
                f"Expected at least {self.min_candles}, got {len(closed)}."
            )
        return closed.tail(selected_limit).reset_index(drop=True)

    def fetch_closed_candles(self, timeframe: str | None = None, limit: int | None = None) -> pd.DataFrame:
        return self.fetch_candles(timeframe=timeframe, limit=limit)

    def _resolve_symbol(self) -> str:
        if self._resolved_symbol is not None:
            return self._resolved_symbol

        self._ensure_markets()
        markets = self.exchange.markets or {}
        candidates = {
            "XAUUSD",
            "XAUUSDT",
            "XAU/USD",
            "XAU/USD:USDT",
            "XAUUSDTM",
        }
        normalized_candidates = {self._normalize_candidate(value) for value in candidates}

        for market in markets.values():
            market_id = self._normalize_candidate(str(market.get("id", "")))
            market_symbol = self._normalize_candidate(str(market.get("symbol", "")))
            if market_id in normalized_candidates or market_symbol in normalized_candidates:
                self._resolved_symbol = str(market["symbol"])
                LOGGER.info("resolved_xauusd_symbol resolved=%s", self._resolved_symbol)
                return self._resolved_symbol

        raise ValueError("Could not resolve an exchange symbol for XAUUSD on Binance futures.")

    def _ensure_markets(self) -> None:
        if self._markets_loaded:
            return
        self.exchange.load_markets()
        self._markets_loaded = True

    def _fetch_with_retry(self, *, resolved_symbol: str, timeframe: str, limit: int) -> list[list[float]]:
        for attempt in range(1, self.max_retries + 1):
            try:
                rows = self.exchange.fetch_ohlcv(resolved_symbol, timeframe=timeframe, limit=limit)
                if not rows:
                    raise RuntimeError("empty_ohlcv_response")
                return rows
            except (ccxt.NetworkError, ccxt.ExchangeError, RuntimeError) as error:
                wait_seconds = min(20.0, self.retry_base_seconds * (2 ** (attempt - 1)))
                LOGGER.warning(
                    "xauusd_fetch_failed timeframe=%s attempt=%s/%s error=%s retry_in=%.2fs",
                    timeframe,
                    attempt,
                    self.max_retries,
                    error,
                    wait_seconds,
                )
                if attempt >= self.max_retries:
                    raise
                time.sleep(wait_seconds)

        raise RuntimeError("unexpected_fetch_retry_exit")

    @staticmethod
    def _to_dataframe(rows: list[list[float]]) -> pd.DataFrame:
        frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        numeric_columns = ["open", "high", "low", "close", "volume"]
        frame[numeric_columns] = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
        frame = frame.dropna(subset=numeric_columns)
        frame = frame.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
        return frame.reset_index(drop=True)

    def _drop_unfinished_candle(self, *, frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        if frame.empty:
            return frame

        timeframe_ms = int(self.exchange.parse_timeframe(timeframe) * 1000)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        open_ms = frame["timestamp"].astype("int64") // 10**6
        close_ms = open_ms + timeframe_ms
        closed = frame.loc[close_ms <= now_ms].copy()
        return closed.reset_index(drop=True)

    @staticmethod
    def _normalize_candidate(value: str) -> str:
        return value.replace("/", "").replace(":", "").replace("-", "").replace(" ", "").upper()
