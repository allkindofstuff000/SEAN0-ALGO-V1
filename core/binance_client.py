"""
Bybit PUBLIC API client — REST + WebSocket.
No API key needed (public endpoints only — data only, no trading).
Switched from Binance to Bybit for cleaner candle data (deeper liquidity, no flash wicks).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from typing import Any, Callable

LOGGER = logging.getLogger("bybit")

BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com").rstrip("/")
WS_URL = os.getenv("BYBIT_WS_URL", "wss://stream.bybit.com/v5/public/linear").rstrip("/")


# ── REST API ──────────────────────────────────────────────────────────────────

def _get_json(url: str, timeout: int = 15) -> Any:
    """Simple GET → JSON helper."""
    req = urllib.request.Request(url, headers={"User-Agent": "SEAN-ALGO/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def fetch_klines(
    symbol: str = "ETHUSDT",
    interval: str = "1",
    limit: int = 200,
    start_time: int | None = None,
    end_time: int | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch OHLCV candles from Bybit REST API.
    Bybit intervals: 1, 5, 15, 60 (minutes)
    Returns list of {time, open, high, low, close, volume} sorted chronologically.
    """
    params = f"category=linear&symbol={symbol}&interval={interval}&limit={min(limit, 1000)}"
    if start_time is not None:
        params += f"&start={start_time}"
    if end_time is not None:
        params += f"&end={end_time}"

    url = f"{BASE_URL}/v5/market/kline?{params}"
    data = _get_json(url)

    if data.get("retCode") != 0:
        LOGGER.warning("[BYBIT] API error: %s", data.get("retMsg", "unknown"))
        return []

    raw_list = data.get("result", {}).get("list", [])

    candles = []
    for k in raw_list:
        # Bybit kline format: [startTime, open, high, low, close, volume, turnover]
        candles.append({
            "time": int(k[0]) // 1000,   # ms → seconds
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })

    # Bybit returns newest first — reverse to chronological order
    candles.sort(key=lambda c: c["time"])
    return candles


# Bybit interval mapping (minutes → Bybit interval string)
_INTERVAL_MAP = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "1h": "60",
}


def fetch_klines_paged(
    symbol: str = "ETHUSDT",
    interval: str = "1m",
    total: int = 4320,
) -> list[dict[str, Any]]:
    """
    Fetch more than 1000 candles by paginating backwards from now.
    interval param accepts Binance-style strings (1m, 5m, 15m, 1h) for compatibility.
    """
    bybit_interval = _INTERVAL_MAP.get(interval, interval)

    if total <= 200:
        return fetch_klines(symbol=symbol, interval=bybit_interval, limit=total)

    all_candles: list[dict[str, Any]] = []
    end_time = int(time.time() * 1000)
    remaining = total

    while remaining > 0:
        limit = min(remaining, 200)  # Bybit max is 200 per request
        batch = fetch_klines(
            symbol=symbol,
            interval=bybit_interval,
            limit=limit,
            end_time=end_time,
        )
        if not batch:
            break
        all_candles = batch + all_candles  # prepend for chronological order
        remaining -= len(batch)
        end_time = batch[0]["time"] * 1000 - 1  # before oldest candle
        if len(batch) < limit:
            break
        time.sleep(0.1)  # rate limit courtesy

    # Deduplicate by time
    seen: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for c in all_candles:
        if c["time"] not in seen:
            seen.add(c["time"])
            deduped.append(c)
    return deduped


def fetch_price(symbol: str = "ETHUSDT") -> float:
    """Get current price for a symbol."""
    url = f"{BASE_URL}/v5/market/tickers?category=linear&symbol={symbol}"
    data = _get_json(url)
    tickers = data.get("result", {}).get("list", [])
    if tickers:
        return float(tickers[0].get("lastPrice", 0))
    return 0.0


def fetch_24hr(symbol: str = "ETHUSDT") -> dict[str, float]:
    """Get 24hr ticker stats."""
    url = f"{BASE_URL}/v5/market/tickers?category=linear&symbol={symbol}"
    data = _get_json(url)
    tickers = data.get("result", {}).get("list", [])
    if not tickers:
        return {"priceChange": 0, "priceChangePct": 0, "high": 0, "low": 0, "volume": 0}

    t = tickers[0]
    last = float(t.get("lastPrice", 0))
    prev = float(t.get("prevPrice24h", 0))
    change = last - prev
    change_pct = (change / prev * 100) if prev > 0 else 0

    return {
        "priceChange": round(change, 2),
        "priceChangePct": round(change_pct, 2),
        "high": float(t.get("highPrice24h", 0)),
        "low": float(t.get("lowPrice24h", 0)),
        "volume": float(t.get("volume24h", 0)),
    }


def fetch_history(
    symbol: str = "ETHUSDT",
    interval: str = "15m",
    from_ms: int = 0,
    to_ms: int = 0,
) -> list[dict[str, Any]]:
    """
    Fetch ALL candles in a date range (paginated, 200/call).
    from_ms / to_ms = Unix milliseconds.
    """
    bybit_interval = _INTERVAL_MAP.get(interval, interval)
    all_candles: list[dict[str, Any]] = []
    cursor = from_ms

    while cursor < to_ms:
        batch = fetch_klines(
            symbol=symbol,
            interval=bybit_interval,
            limit=200,
            start_time=cursor,
            end_time=to_ms,
        )
        if not batch:
            break
        all_candles.extend(batch)
        # Move cursor past the last candle
        cursor = batch[-1]["time"] * 1000 + 1  # +1ms to avoid duplicate
        if len(batch) < 200:
            break  # No more data
        time.sleep(0.1)  # Rate limit courtesy

    return all_candles


# ── WebSocket Stream ──────────────────────────────────────────────────────────

# Bybit WS interval mapping
_WS_INTERVAL_MAP = {
    "kline_1m": "1",
    "kline_5m": "5",
    "kline_15m": "15",
    "kline_1h": "60",
}


class BinanceStreamThread(threading.Thread):
    """
    Bybit WebSocket for kline streams (kept as BinanceStreamThread for compatibility).
    Auto-reconnects on disconnect.
    """

    def __init__(
        self,
        streams: list[str],
        on_message: Callable[[str, dict], None],
        symbol: str = "ETHUSDT",
    ):
        super().__init__(daemon=True, name="bybit-ws")
        self.streams = streams  # e.g. ["ethusdt@kline_1m", "ethusdt@kline_5m"]
        self.on_message = on_message
        self.symbol = symbol
        self._running = True
        self._ws = None

    def _build_subscribe_msg(self) -> str:
        """Convert Binance-style stream names to Bybit subscribe message."""
        topics = []
        for stream in self.streams:
            # "ethusdt@kline_1m" → "kline.1.ETHUSDT"
            parts = stream.split("@")
            if len(parts) == 2:
                kline_part = parts[1]  # "kline_1m"
                bybit_interval = _WS_INTERVAL_MAP.get(kline_part, "1")
                topics.append(f"kline.{bybit_interval}.{self.symbol}")

        return json.dumps({
            "op": "subscribe",
            "args": topics,
        })

    def run(self) -> None:
        import websocket  # websocket-client

        while self._running:
            try:
                LOGGER.info("[BYBIT] connecting → %s", WS_URL[:60])
                ws = websocket.WebSocketApp(
                    WS_URL,
                    on_message=self._on_ws_message,
                    on_error=self._on_ws_error,
                    on_close=self._on_ws_close,
                    on_open=self._on_ws_open,
                )
                self._ws = ws
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                LOGGER.warning("[BYBIT] WebSocket error: %s", exc)

            if self._running:
                LOGGER.info("[BYBIT] reconnecting in 3s…")
                time.sleep(3)

    def _on_ws_open(self, ws: Any) -> None:
        LOGGER.info("[BYBIT] WebSocket connected — subscribing %s", self.symbol)
        sub_msg = self._build_subscribe_msg()
        ws.send(sub_msg)
        LOGGER.info("[BYBIT] subscribed to %d kline topics", len(self.streams))

    def _on_ws_message(self, ws: Any, message: str) -> None:
        try:
            data = json.loads(message)

            # Bybit kline message format:
            # {"topic": "kline.5.ETHUSDT", "data": [{"start":..., "end":..., "interval":"5", ...}], "type": "snapshot"}
            topic = data.get("topic", "")
            if not topic.startswith("kline."):
                return

            kline_data = data.get("data", [])
            if not kline_data:
                return

            k = kline_data[0]

            # Map Bybit interval back to Binance-style stream name for compatibility
            interval = k.get("interval", "")
            interval_to_binance = {"1": "1m", "5": "5m", "15": "15m", "60": "1h"}
            binance_interval = interval_to_binance.get(interval, interval)

            # Build Binance-compatible kline payload so engine code doesn't change
            compat_payload = {
                "e": "kline",
                "k": {
                    "t": int(k["start"]),           # open time ms
                    "T": int(k["end"]),             # close time ms
                    "i": binance_interval,
                    "o": k["open"],
                    "h": k["high"],
                    "l": k["low"],
                    "c": k["close"],
                    "v": k["volume"],
                    "x": k.get("confirm", False),   # is candle closed
                },
            }

            # Use Binance-style stream name for compatibility
            stream_name = f"{self.symbol.lower()}@kline_{binance_interval}"
            self.on_message(stream_name, compat_payload)

        except Exception as exc:
            LOGGER.debug("[BYBIT] message parse error: %s", exc)

    def _on_ws_error(self, ws: Any, error: Any) -> None:
        LOGGER.warning("[BYBIT] WebSocket error: %s", error)

    def _on_ws_close(self, ws: Any, close_code: Any, close_msg: Any) -> None:
        LOGGER.info("[BYBIT] WebSocket closed (code=%s)", close_code)

    def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
