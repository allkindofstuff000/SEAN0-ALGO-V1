from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import certifi
import pandas as pd


LOGGER = logging.getLogger(__name__)
XAU_SYMBOLS = {"XAUUSD", "XAUUSDT"}
OANDA_INSTRUMENT = "XAU_USD"
OANDA_GRANULARITY = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "30m": "M30",
    "1h": "H1",
    "4h": "H4",
}
OANDA_BASE_URLS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


@dataclass
class DataFetcher:
    """
    XAU market data fetcher for the simplified MVP engine.

    Uses OANDA `XAU_USD` candles so the bot consumes real intraday spot-gold
    data instead of crypto proxy markets.
    """

    default_timeframe: str = "5m"
    min_candles: int = 300
    request_limit: int = 350
    max_retries: int = 5
    retry_base_seconds: float = 12.0
    oanda_api_key: str = field(default_factory=lambda: os.getenv("OANDA_API_KEY", "").strip())
    oanda_environment: str = field(default_factory=lambda: os.getenv("OANDA_ENV", "practice").strip().lower())
    oanda_price_component: str = field(default_factory=lambda: os.getenv("OANDA_PRICE_COMPONENT", "M").strip().upper())

    def __post_init__(self) -> None:
        if not self.oanda_api_key:
            raise RuntimeError("OANDA_API_KEY is required for true XAUUSD intraday data.")
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())
        self._oanda_base_url = OANDA_BASE_URLS.get(self.oanda_environment, OANDA_BASE_URLS["practice"])

    def fetch_market_data(
        self,
        symbol: str,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        normalized_symbol = self._normalize_symbol(symbol)
        if normalized_symbol not in XAU_SYMBOLS:
            raise ValueError(f"Unsupported symbol for the simplified XAU engine: {symbol}")

        selected_timeframe = timeframe or self.default_timeframe
        selected_limit = max(self.min_candles, int(limit or self.request_limit))
        return self.fetch_oanda(timeframe=selected_timeframe, limit=selected_limit)

    def fetch_oanda(self, timeframe: str = "5m", limit: int | None = None) -> pd.DataFrame:
        selected_limit = max(self.min_candles, int(limit or self.request_limit))
        granularity = self._oanda_granularity(timeframe)
        params = urllib.parse.urlencode(
            {
                "price": self.oanda_price_component,
                "granularity": granularity,
                "count": selected_limit + 50,
            }
        )
        url = f"{self._oanda_base_url}/v3/instruments/{OANDA_INSTRUMENT}/candles?{params}"
        payload = self._request_with_retry(url)
        candles = payload.get("candles", [])
        if not candles:
            raise RuntimeError("empty_oanda_candle_response")

        rows: list[dict[str, Any]] = []
        for candle in candles:
            if not candle.get("complete", False):
                continue
            price_bucket = candle.get("mid") or candle.get("bid") or candle.get("ask")
            if not isinstance(price_bucket, dict):
                continue
            rows.append(
                {
                    "timestamp": pd.to_datetime(candle["time"], utc=True),
                    "open": float(price_bucket["o"]),
                    "high": float(price_bucket["h"]),
                    "low": float(price_bucket["l"]),
                    "close": float(price_bucket["c"]),
                    "volume": float(candle.get("volume", 0.0)),
                }
            )

        frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        if frame.empty:
            raise RuntimeError("no_complete_oanda_candles")

        frame = frame.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
        if len(frame) < self.min_candles:
            raise RuntimeError(
                f"Not enough OANDA candles for XAUUSD on {timeframe}. "
                f"Expected at least {self.min_candles}, got {len(frame)}."
            )

        LOGGER.info(
            "resolved_symbol requested=%s resolved=%s provider=oanda environment=%s component=%s",
            "XAUUSDT",
            OANDA_INSTRUMENT,
            self.oanda_environment,
            self.oanda_price_component,
        )
        return frame.tail(selected_limit).reset_index(drop=True)

    def fetch_multi_timeframe_data(self, limit: int | None = None) -> dict[str, pd.DataFrame]:
        selected_limit = max(self.min_candles, int(limit or self.request_limit))
        return {
            "primary": self.fetch_oanda(timeframe="5m", limit=selected_limit),
            "confirmation": self.fetch_oanda(timeframe="15m", limit=selected_limit),
        }

    def provider_summary(self) -> dict[str, Any]:
        return {
            "provider": "oanda",
            "instrument": OANDA_INSTRUMENT,
            "environment": self.oanda_environment,
            "price_component": self.oanda_price_component,
            "mode": "true_xauusd_intraday",
            "note": "Using OANDA XAU_USD intraday candles for real spot-gold data",
        }

    def startup_check(self) -> dict[str, Any]:
        params = urllib.parse.urlencode(
            {
                "price": self.oanda_price_component,
                "granularity": "M5",
                "count": 5,
            }
        )
        url = f"{self._oanda_base_url}/v3/instruments/{OANDA_INSTRUMENT}/candles?{params}"
        payload = self._request_with_retry(url)
        candles = payload.get("candles", [])
        if not candles:
            raise RuntimeError("OANDA startup check failed: missing candle data.")
        return self.provider_summary()

    def _request_with_retry(self, url: str) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.oanda_api_key}",
            "Accept-Datetime-Format": "RFC3339",
            "User-Agent": "SEAN0-ALGO-V1/1.0",
        }

        for attempt in range(1, self.max_retries + 1):
            try:
                request = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(request, timeout=30, context=self._ssl_context) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="ignore")
                wait_seconds = self.retry_base_seconds
                if error.code in {401, 403}:
                    raise RuntimeError("OANDA authorization failed. Set a valid OANDA_API_KEY and OANDA_ENV.") from error
                LOGGER.warning(
                    "oanda_fetch_failed status=%s attempt=%s/%s error=%s retry_in=%.2fs",
                    error.code,
                    attempt,
                    self.max_retries,
                    body or error.reason,
                    wait_seconds,
                )
                if attempt >= self.max_retries:
                    raise RuntimeError(f"OANDA request failed: {body or error.reason}") from error
                time.sleep(wait_seconds)
            except (urllib.error.URLError, TimeoutError, ssl.SSLError, json.JSONDecodeError) as error:
                wait_seconds = self.retry_base_seconds
                LOGGER.warning(
                    "oanda_fetch_failed attempt=%s/%s error=%s retry_in=%.2fs",
                    attempt,
                    self.max_retries,
                    error,
                    wait_seconds,
                )
                if attempt >= self.max_retries:
                    raise RuntimeError(f"OANDA request failed: {error}") from error
                time.sleep(wait_seconds)

        raise RuntimeError("unexpected_oanda_retry_exit")

    @staticmethod
    def _normalize_symbol(value: str) -> str:
        return value.replace("/", "").replace(":", "").replace("-", "").replace(" ", "").upper()

    @staticmethod
    def _oanda_granularity(timeframe: str) -> str:
        normalized = timeframe.strip().lower()
        if normalized not in OANDA_GRANULARITY:
            raise ValueError(f"Unsupported OANDA timeframe: {timeframe}")
        return OANDA_GRANULARITY[normalized]
