"""
Multi-timeframe candle engine for SEAN0-ALGO.

Architecture:
  1 OANDA pricing stream → tick parser → CandleBuilder (M1/M5/M15/H1)
  → CandleStore (in-memory, last 200 per TF) → SSE broadcaster

Usage (from web_server.py):
    engine = OandaStreamEngine.from_env()
    await engine.start(asyncio_loop)
    sub_id, queue = engine.subscribe()
    engine.unsubscribe(sub_id)
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import ssl
import threading
import time
import urllib.request
import urllib.error
from collections import deque
from typing import Any

import pandas as pd

LOGGER = logging.getLogger("xau.candle_engine")

TIMEFRAMES: dict[str, int] = {   # timeframe → period in seconds
    "M1":  60,
    "M5":  300,
    "M15": 900,
    "H1":  3600,
}
MAX_CANDLES = 200


# ── CandleStore ────────────────────────────────────────────────────────────────

class CandleStore:
    """Thread-safe in-memory store for completed + current candles."""

    def __init__(self) -> None:
        self._completed: dict[str, deque] = {tf: deque(maxlen=MAX_CANDLES) for tf in TIMEFRAMES}
        self._current:   dict[str, dict | None] = {tf: None for tf in TIMEFRAMES}
        self._lock = threading.Lock()

    def get_candles(self, timeframe: str) -> list[dict]:
        with self._lock:
            return list(self._completed[timeframe])

    def get_current(self, timeframe: str) -> dict | None:
        with self._lock:
            c = self._current[timeframe]
            return dict(c) if c else None

    def seed_historical(self, timeframe: str, candles: list[dict]) -> None:
        """Pre-fill store with historical candles (called on startup)."""
        with self._lock:
            self._completed[timeframe].clear()
            for c in candles[-MAX_CANDLES:]:
                self._completed[timeframe].append(c)

    def push_completed(self, timeframe: str, candle: dict) -> None:
        with self._lock:
            self._completed[timeframe].append(candle)

    def set_current(self, timeframe: str, candle: dict | None) -> None:
        with self._lock:
            self._current[timeframe] = candle

    def all_current(self) -> dict[str, dict | None]:
        with self._lock:
            return {tf: (dict(c) if c else None) for tf, c in self._current.items()}


# ── CandleBuilder ──────────────────────────────────────────────────────────────

class CandleBuilder:
    """Processes price ticks → builds candles for all 4 timeframes."""

    def __init__(self, store: CandleStore) -> None:
        self._store = store
        self._current: dict[str, dict | None] = {tf: None for tf in TIMEFRAMES}
        self._lock = threading.Lock()

    def process_tick(self, mid: float, tick_unix: float) -> list[dict]:
        """
        Feed one price tick. Returns list of newly COMPLETED candles
        (each has timeframe key set).
        """
        completed: list[dict] = []
        with self._lock:
            for tf, period in TIMEFRAMES.items():
                period_start = int(math.floor(tick_unix / period)) * period
                cur = self._current[tf]

                if cur is None:
                    # First tick
                    self._current[tf] = _new_candle(tf, period_start, mid)

                elif cur["time"] == period_start:
                    # Same period — update OHLC
                    cur["high"]   = max(cur["high"], mid)
                    cur["low"]    = min(cur["low"],  mid)
                    cur["close"]  = mid
                    cur["volume"] += 1

                else:
                    # New period — close the old candle
                    old = dict(cur)
                    old["complete"] = True
                    self._store.push_completed(tf, old)
                    completed.append(old)

                    # Fill any skipped periods with flat candles (stream gap / low liquidity)
                    last_close = old["close"]
                    next_start = old["time"] + period
                    while next_start < period_start:
                        gap = _new_candle(tf, next_start, last_close)
                        gap["complete"] = True
                        self._store.push_completed(tf, gap)
                        completed.append(gap)
                        next_start += period

                    self._current[tf] = _new_candle(tf, period_start, mid)

                # Always sync store's current candle
                self._store.set_current(tf, dict(self._current[tf]))

        return completed

    def snapshot_current(self) -> dict[str, dict | None]:
        """Return a copy of all current (live) candles."""
        with self._lock:
            return {tf: (dict(c) if c else None) for tf, c in self._current.items()}


def _new_candle(tf: str, period_start: int, price: float) -> dict:
    return {
        "time":      period_start,
        "open":      price,
        "high":      price,
        "low":       price,
        "close":     price,
        "volume":    1,
        "complete":  False,
        "timeframe": tf,
    }


# ── OandaStreamEngine ──────────────────────────────────────────────────────────

class OandaStreamEngine:
    """
    Singleton engine:
      - Fetches 200 historical candles per TF in parallel on startup
      - Opens ONE OANDA pricing stream → feeds tick builder
      - Broadcasts SSE events to all subscriber queues
    """

    def __init__(
        self,
        api_key:     str,
        account_id:  str,
        api_base:    str,   # https://api-fxpractice.oanda.com/v3
        stream_base: str,   # https://stream-fxpractice.oanda.com/v3
    ) -> None:
        self.api_key     = api_key
        self.account_id  = account_id
        self.api_base    = api_base.rstrip("/")
        self.stream_base = stream_base.rstrip("/")

        self.store   = CandleStore()
        self.builder = CandleBuilder(self.store)

        self._subscribers:      dict[int, asyncio.Queue] = {}
        self._sub_lock          = threading.Lock()
        self._sub_counter       = 0
        self._loop:             asyncio.AbstractEventLoop | None = None
        self._running           = False
        self._stream_thread:    threading.Thread | None = None
        self._history_loaded    = False
        self.stream_status      = "disconnected"   # for dashboard display

    # ── Public API ─────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "OandaStreamEngine":
        """Build engine from environment variables (must call load_dotenv first)."""
        import os
        api_key = os.getenv("OANDA_API_KEY", "").strip()
        env     = os.getenv("OANDA_ENV", "practice").strip().lower()
        raw_api = os.getenv("OANDA_API_URL", "").strip().rstrip("/")

        if raw_api:
            api_base = raw_api if "/v3" in raw_api else raw_api + "/v3"
        elif env == "live":
            api_base = "https://api-fxtrade.oanda.com/v3"
        else:
            api_base = "https://api-fxpractice.oanda.com/v3"

        # Stream URL is different from REST API URL
        stream_base_env = os.getenv("OANDA_STREAM_URL", "").strip().rstrip("/")
        if stream_base_env:
            stream_base = stream_base_env if "/v3" in stream_base_env else stream_base_env + "/v3"
        elif env == "live":
            stream_base = "https://stream-fxtrade.oanda.com/v3"
        else:
            stream_base = "https://stream-fxpractice.oanda.com/v3"

        # Auto-discover account ID if not set
        account_id = os.getenv("OANDA_ACCOUNT_ID", "").strip()
        if not account_id and api_key:
            try:
                account_id = _discover_account_id(api_key, api_base)
                LOGGER.info("[ENGINE] auto-discovered OANDA account_id=%s", account_id)
            except Exception as exc:
                LOGGER.warning("[ENGINE] could not auto-discover account_id: %s", exc)

        return cls(api_key=api_key, account_id=account_id,
                   api_base=api_base, stream_base=stream_base)

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called at FastAPI startup. Fetches history then starts stream thread."""
        self._loop   = loop
        self._running = True

        # Fetch all 4 timeframes in parallel
        LOGGER.info("[ENGINE] fetching historical candles for M1/M5/M15/H1 …")
        try:
            await asyncio.gather(
                *[asyncio.to_thread(self._fetch_and_seed, tf) for tf in TIMEFRAMES],
                return_exceptions=True,
            )
        except Exception as exc:
            LOGGER.warning("[ENGINE] historical fetch error: %s", exc)

        self._history_loaded = True
        LOGGER.info("[ENGINE] historical candles loaded")

        # Broadcast init to any early subscribers
        self._broadcast(_init_event(self.store))

        # Start stream thread (daemon — dies when server exits)
        self._stream_thread = threading.Thread(
            target=self._stream_loop, name="oanda-stream", daemon=True
        )
        self._stream_thread.start()
        LOGGER.info("[ENGINE] stream thread started")

    def subscribe(self) -> tuple[int, asyncio.Queue]:
        """Register a new SSE subscriber. Returns (sub_id, queue)."""
        q = asyncio.Queue(maxsize=500)
        with self._sub_lock:
            sid = self._sub_counter
            self._sub_counter += 1
            self._subscribers[sid] = q
        return sid, q

    def unsubscribe(self, sub_id: int) -> None:
        with self._sub_lock:
            self._subscribers.pop(sub_id, None)

    def stop(self) -> None:
        self._running = False

    # ── Historical fetch ────────────────────────────────────────────────────────

    def _fetch_and_seed(self, timeframe: str) -> None:
        """Fetch 200 historical candles for one TF and seed the store."""
        candles = _fetch_oanda_candles(
            api_key=self.api_key,
            base_url=self.api_base,
            granularity=timeframe,
            count=MAX_CANDLES + 5,   # fetch a few extra
        )
        # Keep only complete candles
        complete = [c for c in candles if c.get("complete", False)][-MAX_CANDLES:]
        self.store.seed_historical(timeframe, complete)
        LOGGER.info("[ENGINE] seeded %s  candles=%d", timeframe, len(complete))

    # ── Stream loop ─────────────────────────────────────────────────────────────

    def _stream_loop(self) -> None:
        """Runs forever in background thread. Reconnects on any failure."""
        while self._running:
            try:
                self._connect_and_read()
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    LOGGER.error("[STREAM] 401 Invalid API Key — stopping stream.")
                    self._broadcast({"type": "status", "status": "error", "detail": "Invalid API Key"})
                    self._running = False
                    return
                elif exc.code == 429:
                    LOGGER.warning("[STREAM] 429 rate limited — waiting 60s")
                    self._set_status("reconnecting")
                    time.sleep(60)
                else:
                    LOGGER.warning("[STREAM] HTTP %s — reconnecting in 5s", exc.code)
                    self._set_status("reconnecting")
                    time.sleep(5)
            except Exception as exc:
                LOGGER.warning("[STREAM] disconnected: %s — reconnecting in 2s", exc)
                self._set_status("reconnecting")
                time.sleep(2)

    def _connect_and_read(self) -> None:
        """Open OANDA pricing stream and read ticks until error or stop."""
        if not self.api_key or not self.account_id:
            LOGGER.error("[STREAM] api_key or account_id missing — cannot stream")
            time.sleep(10)
            return

        url = (
            f"{self.stream_base}/accounts/{self.account_id}"
            f"/pricing/stream?instruments=XAU_USD"
        )
        headers = {
            "Authorization":         f"Bearer {self.api_key}",
            "Accept-Datetime-Format": "RFC3339",
            "User-Agent":            "SEAN0-ALGO-V1/1.0",
        }
        ssl_ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers=headers, method="GET")

        LOGGER.info("[STREAM] connecting → %s", url)
        with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
            self._set_status("connected")
            LOGGER.info("[STREAM] connected — reading ticks …")

            for raw_line in resp:
                if not self._running:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "HEARTBEAT":
                    # Keep-alive — just log occasionally
                    continue

                if msg_type != "PRICE":
                    continue

                # Extract mid price
                bids = msg.get("bids", [])
                asks = msg.get("asks", [])
                if not bids or not asks:
                    continue
                bid = float(bids[0]["price"])
                ask = float(asks[0]["price"])
                mid = (bid + ask) / 2.0

                # Parse tick time
                tick_time_str = msg.get("time", "")
                try:
                    tick_unix = pd.Timestamp(tick_time_str).timestamp()
                except Exception:
                    tick_unix = time.time()

                # Build candles
                completed = self.builder.process_tick(mid, tick_unix)

                # Broadcast completed candles
                for candle in completed:
                    self._broadcast({
                        "type":      "candle",
                        "timeframe": candle["timeframe"],
                        "candle":    candle,
                    })

                # Broadcast live tick (all current TF states)
                self._broadcast({
                    "type":    "tick",
                    "price":   round(mid, 3),
                    "time":    tick_unix,
                    "candles": self.builder.snapshot_current(),
                })

    # ── Broadcasting ───────────────────────────────────────────────────────────

    def _broadcast(self, event: dict) -> None:
        """Thread-safe: push event to all subscriber queues via the asyncio loop."""
        if self._loop is None or self._loop.is_closed():
            return
        payload = json.dumps(event, default=str)
        with self._sub_lock:
            subs = list(self._subscribers.values())
        for q in subs:
            try:
                self._loop.call_soon_threadsafe(_safe_put, q, payload)
            except RuntimeError:
                pass

    def _set_status(self, status: str) -> None:
        self.stream_status = status
        self._broadcast({"type": "status", "status": status})


def _safe_put(q: asyncio.Queue, payload: str) -> None:
    """Called inside the event loop — put payload without blocking."""
    try:
        q.put_nowait(payload)
    except asyncio.QueueFull:
        pass  # Slow consumer — drop oldest if needed


def _init_event(store: CandleStore) -> dict:
    return {
        "type":    "init",
        "candles": {tf: store.get_candles(tf) for tf in TIMEFRAMES},
    }


# ── OANDA helpers ──────────────────────────────────────────────────────────────

def _fetch_oanda_candles(
    api_key: str,
    base_url: str,
    granularity: str,
    count: int,
) -> list[dict]:
    """Fetch candles from OANDA REST API. Returns list including incomplete candle."""
    url = (
        f"{base_url}/instruments/XAU_USD/candles"
        f"?price=M&granularity={granularity}&count={count}"
    )
    headers = {
        "Authorization":         f"Bearer {api_key}",
        "Accept-Datetime-Format": "RFC3339",
        "User-Agent":            "SEAN0-ALGO-V1/1.0",
    }
    ssl_ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=20, context=ssl_ctx) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    candles: list[dict] = []
    for c in payload.get("candles", []):
        mid = c.get("mid") or c.get("bid") or c.get("ask") or {}
        if not mid:
            continue
        ts_unix = int(pd.Timestamp(c["time"]).timestamp())
        candles.append({
            "time":     ts_unix,
            "open":     float(mid.get("o", 0)),
            "high":     float(mid.get("h", 0)),
            "low":      float(mid.get("l", 0)),
            "close":    float(mid.get("c", 0)),
            "volume":   int(c.get("volume", 0)),
            "complete": bool(c.get("complete", False)),
        })
    return candles


def _discover_account_id(api_key: str, base_url: str) -> str:
    """Auto-fetch first OANDA account ID for this API key."""
    url = f"{base_url}/accounts"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent":    "SEAN0-ALGO-V1/1.0",
    }
    ssl_ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    accounts = data.get("accounts", [])
    if not accounts:
        raise RuntimeError("No OANDA accounts found.")
    return accounts[0]["id"]
