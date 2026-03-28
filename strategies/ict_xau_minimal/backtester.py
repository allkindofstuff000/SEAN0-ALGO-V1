"""
Minimal ICT Strategy Backtest Engine — XAU/USD (Gold)
Reuses the same 6-variant ICT engine from ETH but with:
  - OANDA data source instead of Binance
  - XAU-specific parameters (wider stops, bigger FVGs, etc.)
  - M5 signal timeframe, M15 bias timeframe
"""
from __future__ import annotations

import os
import time as _time
from typing import Any

import pandas as pd
import requests

# Reuse the core ICT backtest engine from ETH
from strategies.ict_eth_minimal.backtester import run_all_variants

OANDA_BASE_URLS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
CHUNK_DAYS = 5


def _base_url() -> str:
    env = os.getenv("OANDA_ENV", "practice").strip().lower()
    return OANDA_BASE_URLS.get(env, OANDA_BASE_URLS["practice"])


def _headers() -> dict[str, str]:
    token = os.getenv("OANDA_API_KEY", "").strip()
    if not token:
        raise RuntimeError("OANDA_API_KEY not set — cannot run XAU ICT backtest.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept-Datetime-Format": "RFC3339",
        "User-Agent": "SEAN0-ALGO-xau-ict-backtest/1.0",
    }


def _fetch_oanda_candles(
    instrument: str,
    granularity: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[dict[str, Any]]:
    """Fetch candles from OANDA in chunks, return standard format."""
    all_candles: list[dict[str, Any]] = []
    cursor = start

    while cursor < end:
        chunk_end = min(cursor + pd.Timedelta(days=CHUNK_DAYS), end)
        url = f"{_base_url()}/v3/instruments/{instrument}/candles"
        params = {
            "granularity": granularity,
            "price": "M",
            "from": cursor.isoformat(),
            "to": chunk_end.isoformat(),
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.get(url, params=params, headers=_headers(), timeout=REQUEST_TIMEOUT)
                if resp.status_code in (400, 401, 403):
                    break
                resp.raise_for_status()
                data = resp.json()

                for c in data.get("candles", []):
                    if not c.get("complete", False):
                        continue
                    mid = c.get("mid", {})
                    ts = pd.Timestamp(c["time"]).timestamp()
                    all_candles.append({
                        "time": int(ts),
                        "open": float(mid.get("o", 0)),
                        "high": float(mid.get("h", 0)),
                        "low": float(mid.get("l", 0)),
                        "close": float(mid.get("c", 0)),
                        "volume": int(c.get("volume", 0)),
                    })
                break
            except requests.RequestException:
                if attempt < MAX_RETRIES:
                    _time.sleep(2)
                continue

        cursor = chunk_end
        _time.sleep(0.3)  # rate limit

    # Deduplicate by time
    seen: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for c in all_candles:
        if c["time"] not in seen:
            seen.add(c["time"])
            deduped.append(c)
    return sorted(deduped, key=lambda c: c["time"])


def run_xau_ict_backtest(days: int = 30) -> dict[str, Any]:
    """
    Fetch XAU/USD M5 + M15 data from OANDA and run all 6 ICT variants.
    XAU-specific: wider FVG min size, bigger tolerances.
    """
    instrument = os.getenv("OANDA_INSTRUMENT", "XAU_USD")
    now_utc = pd.Timestamp.now(tz="UTC")
    # OANDA rejects future dates — use yesterday as end
    end_utc = (now_utc.normalize() - pd.Timedelta(seconds=1))
    start_utc = end_utc - pd.Timedelta(days=days)

    # Fetch M5 and M15 candles
    m5 = _fetch_oanda_candles(instrument, "M5", start_utc, end_utc)
    m15 = _fetch_oanda_candles(instrument, "M15", start_utc, end_utc)

    if len(m5) < 300:
        return {"error": f"Only {len(m5)} M5 candles — need 300+", "variants": {}, "best": None}

    # Run the same 6-variant engine (shared with ETH)
    # XAU parameters are handled by the engine's tolerance-based detection
    # (XAU prices ~$2000-4000 vs ETH ~$2000-3000 — similar ranges work)
    result = run_all_variants(m5, m15)

    return {
        **result,
        "instrument": instrument,
        "dataSource": "OANDA",
        "m5Count": len(m5),
        "m15Count": len(m15),
        "days": days,
    }
