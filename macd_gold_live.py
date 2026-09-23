"""Gold MACD-Momentum DAY-TRADE live signal bot (XAUUSD, H1).

The engine's 4th strategy — a slower, day-trade complement to the M5 RSI EMA /
VWAP+ST bots. Polls OANDA XAUUSD, resamples M5 -> H1 (to match the validated
backtest exactly), evaluates the LAST CLOSED H1 bar, and fires on a MACD(12,26,9)
signal-line cross that agrees with the H1 EMA200 trend:

  BUY  : close > EMA200  AND  MACD crosses ABOVE signal
  SELL : close < EMA200  AND  MACD crosses BELOW signal

SL 1.5xATR / TP 3.0xATR (RR 1:2), hold up to 24h. Validated on 240d, 3
non-overlapping windows (PF ~1.37, positive in every window; robust across
MACD/RR/trend params). Signals -> Telegram + Mongo (strategy 'macd-gold').

Runs as systemd unit `macd-gold.service`. Dedup by H1 candle ts; daily cap +
cooldown risk guards.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import signal as _signal
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from core.data_fetcher import DataFetcher
from core.signal_guard import check_signal

try:
    from core.telegram_bot import TelegramNotifier
    _TG_OK = True
except Exception:  # pragma: no cover
    _TG_OK = False
    TelegramNotifier = None  # type: ignore

try:
    from core.mongo_store import save_live_signal as _save_live_signal
    _MONGO_OK = True
except Exception:  # pragma: no cover
    _MONGO_OK = False

    def _save_live_signal(**_):
        return None


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state_macd_gold.txt"
POLL_SECS = 120          # H1 bars close hourly; poll every 2 min to catch the close promptly
H1_FETCH = 400           # H1 bars to fetch (fetch_oanda drops the forming bar); > EMA200 warmup

# Strategy config (baseline, validated cluster)
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
TREND_EMA = 200
ATR_LEN = 14
SL_ATR, TP_ATR = 1.5, 3.0   # RR 1:2

# Risk guards (env-overridable)
MAX_SIGNALS_PER_DAY = int(os.getenv("MACD_MAX_SIGNALS_PER_DAY", "4"))
COOLDOWN_H1_BARS = int(os.getenv("MACD_COOLDOWN_BARS", "2"))
_RISK: dict = {"day": None, "count": 0, "last_fired_ts": None}

LOG = logging.getLogger("macd-gold.live")


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def _load_last_signal_ts() -> str | None:
    if not STATE_PATH.exists():
        return None
    try:
        return STATE_PATH.read_text().strip() or None
    except Exception:
        return None


def _save_last_signal_ts(ts: str) -> None:
    try:
        STATE_PATH.write_text(ts)
    except Exception as e:
        LOG.warning("state save failed: %s", e)


def _format_message(direction: str, entry: float, sl: float, tp: float, atr: float, ts) -> str:
    arrow = "🟢 BUY" if direction == "BUY" else "🔴 SELL"
    # ts = H1 candle OPEN (UTC); it closes 1h later — that close, in Dhaka (UTC+6),
    # is the signal time. Show that so the timing reads right on the user's clock.
    bd = (pd.Timestamp(ts) + pd.Timedelta(hours=1 + 6)).strftime("%Y-%m-%d %H:%M")
    return (
        f"📈 *Gold MACD (day-trade)* — {arrow}\n"
        f"Symbol: XAUUSD  ·  H1\n"
        f"🕒 Signal (BD): `{bd}`  ·  enter now\n"
        f"Entry: `{entry:.2f}`\n"
        f"SL:    `{sl:.2f}`\n"
        f"TP:    `{tp:.2f}`\n"
        f"ATR:   {atr:.2f}  ·  RR 1:2  ·  hold ≤24h"
    )


def _fetch_h1(fetcher: DataFetcher) -> pd.DataFrame | None:
    """Recent CLOSED H1 bars with MACD / EMA200 / ATR. fetch_oanda already drops
    the still-forming candle, so df[-1] is the most recent COMPLETED H1 bar."""
    h1 = fetcher.fetch_oanda("1h", H1_FETCH)
    if h1 is None or len(h1) < 250:
        return None
    h1 = h1.reset_index(drop=True)
    h1["ema200"] = _ema(h1["close"], TREND_EMA)
    macd = _ema(h1["close"], MACD_FAST) - _ema(h1["close"], MACD_SLOW)
    h1["macd"] = macd
    h1["signal"] = _ema(macd, MACD_SIGNAL)
    h1["atr"] = _atr(h1, ATR_LEN)
    return h1


async def _cycle(fetcher: DataFetcher, tg, last_signal_ts: str | None) -> str | None:
    df = await asyncio.to_thread(_fetch_h1, fetcher)
    if df is None or len(df) < 250:
        LOG.warning("insufficient H1 candles (%s)", 0 if df is None else len(df))
        return last_signal_ts

    i = len(df) - 1
    row = df.iloc[i]
    prev = df.iloc[i - 1]
    ts = pd.Timestamp(row["timestamp"])
    ts_str = str(ts)[:19]
    if ts_str == last_signal_ts:
        return last_signal_ts

    e200, atr, close = row.get("ema200"), row.get("atr"), float(row["close"])
    m, s, pm, ps = row.get("macd"), row.get("signal"), prev.get("macd"), prev.get("signal")
    if any(pd.isna(x) for x in (e200, atr, m, s, pm, ps)) or float(atr) <= 0:
        LOG.info("indicators not ready @ %s", ts_str)
        return last_signal_ts

    cross_up = (pm <= ps) and (m > s)
    cross_dn = (pm >= ps) and (m < s)
    direction: str | None = None
    if close > float(e200) and cross_up:
        direction = "BUY"
    elif close < float(e200) and cross_dn:
        direction = "SELL"

    if direction is None:
        LOG.info("no signal @ %s (close=%.2f ema200=%.2f macd=%.3f sig=%.3f)", ts_str, close, float(e200), float(m), float(s))
        return ts_str

    # Risk guards: daily cap + cooldown (in H1 bars)
    utc_day = ts.date()
    if _RISK["day"] != utc_day:
        _RISK["day"] = utc_day
        _RISK["count"] = 0
    if _RISK["count"] >= MAX_SIGNALS_PER_DAY:
        LOG.info("risk: daily cap %d reached, skip %s @ %s", MAX_SIGNALS_PER_DAY, direction, ts_str)
        return ts_str
    if _RISK["last_fired_ts"] is not None:
        gap = (ts - _RISK["last_fired_ts"]) / pd.Timedelta(hours=1)
        if gap < COOLDOWN_H1_BARS:
            LOG.info("risk: cooldown %.0f/%d bars, skip %s @ %s", gap, COOLDOWN_H1_BARS, direction, ts_str)
            return ts_str

    # Entry alignment: re-anchor to the live price at fire time, keep SL/TP distances.
    atr_val = float(atr)
    entry = close
    try:
        snap = await asyncio.to_thread(fetcher.fetch_live_market_snapshot, "1m")
        if snap and snap.get("close"):
            entry = float(snap["close"])
    except Exception as e:
        LOG.warning("live snapshot failed (%s); using H1 close as entry", e)

    sl_dist = atr_val * SL_ATR
    tp_dist = atr_val * TP_ATR
    if direction == "BUY":
        sl, tp = entry - sl_dist, entry + tp_dist
    else:
        sl, tp = entry + sl_dist, entry - tp_dist

    ok, why = check_signal(
        direction=direction, entry=entry, stop_loss=sl, take_profit=tp, atr=atr_val,
        ref_price=close, recent_high=float(row["high"]), recent_low=float(row["low"]),
    )
    if not ok:
        LOG.warning("signal REJECTED by sanity guard: %s (%s @ %s)", why, direction, ts_str)
        return ts_str

    LOG.info("SIGNAL %s @ %s entry=%.2f sl=%.2f tp=%.2f atr=%.2f", direction, ts_str, entry, sl, tp, atr_val)

    telegram_sent = False
    if tg is not None:
        try:
            ok2 = await tg.send_message(_format_message(direction, entry, sl, tp, atr_val, ts))
            telegram_sent = bool(ok2)
            LOG.info("telegram: %s", "sent" if telegram_sent else "not_sent")
        except Exception as e:
            LOG.warning("telegram failed: %s", e)

    if _MONGO_OK:
        try:
            await asyncio.to_thread(
                _save_live_signal,
                symbol="XAUUSD",
                direction=direction,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                atr=atr_val,
                session="H1",
                market_regime="trend",
                trend_alignment=True,
                price_trigger=True,
                reason="macd_cross_trend",
                strategy="macd-gold",
                strategyName="Gold MACD",
                signal_kind="macd_gold",
                telegram_sent=telegram_sent,
                candle_time_utc=ts_str,
                # Entry is at the H1 CLOSE (ts is the H1 open); the resolver must
                # score outcomes from here, not from the pre-entry hour.
                entry_time_utc=str(ts + pd.Timedelta(hours=1))[:19],
                timestamp=int(dt.datetime.now(dt.timezone.utc).timestamp()),
            )
            LOG.info("mongo: saved")
        except Exception as e:
            LOG.warning("mongo save failed: %s", e)

    _RISK["count"] += 1
    _RISK["last_fired_ts"] = ts
    LOG.info("risk: fired %d/%d today", _RISK["count"], MAX_SIGNALS_PER_DAY)
    _save_last_signal_ts(ts_str)
    return ts_str


async def run() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_dotenv(ROOT / ".env")

    fetcher = DataFetcher(min_candles=H1_FETCH)
    try:
        fetcher.startup_check()
    except Exception as e:
        LOG.warning("fetcher startup_check failed: %s", e)

    tg = None
    if _TG_OK:
        try:
            tg = TelegramNotifier(token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
                                  chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip())
            LOG.info("telegram: ready")
        except Exception as e:
            LOG.warning("telegram init failed: %s -- running signal-silent", e)
    else:
        LOG.warning("telegram module unavailable -- running signal-silent")

    LOG.info("started; XAUUSD H1 MACD(%d,%d,%d)/EMA%d; SL %.1fx / TP %.1fx ATR (RR 1:2); poll %ds",
             MACD_FAST, MACD_SLOW, MACD_SIGNAL, TREND_EMA, SL_ATR, TP_ATR, POLL_SECS)

    last = _load_last_signal_ts()
    if last:
        LOG.info("resuming; last signal ts %s", last)

    stop = asyncio.Event()
    for sg in (_signal.SIGINT, _signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(sg, stop.set)
        except NotImplementedError:
            _signal.signal(sg, lambda *_: stop.set())

    while not stop.is_set():
        try:
            last = await _cycle(fetcher, tg, last)
        except Exception as e:
            LOG.exception("cycle error: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECS)
        except asyncio.TimeoutError:
            pass

    LOG.info("stopped")


if __name__ == "__main__":
    asyncio.run(run())
