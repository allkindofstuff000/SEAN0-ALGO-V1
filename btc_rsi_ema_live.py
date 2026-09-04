"""BTC RSI EMA live signal bot (24/7 crypto).

Polls BTCUSDT M5 candles from the Binance data mirror every 60s, evaluates the
LAST CLOSED bar with the EXACT XAU RSI EMA signal engine
(`backtests.backtest_forex_engine.evaluate_signal`) — the gold session filter is
bypassed because crypto trades 24/7 — and fires on a valid EMA50/200 M15 trend +
RSI 55/45 + M5 breakout + volatility/range/no-trade-zone setup.

Signals go to Telegram + Mongo (tagged strategy 'rsi-btc'; they show on the BTC
page's Signal History and /signals). Entry is re-anchored to the live price at
fire time (preserving SL/TP distances) exactly like the gold bot. Dedup by candle
timestamp; daily cap + cooldown risk guards.

Runs as systemd unit `btc-rsi-ema.service`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
import os
import signal as _signal
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from backtests import backtest_forex_engine as engine
from core.btc_fetcher import BtcFetcher
from core.indicator_engine import IndicatorEngine
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
STATE_PATH = ROOT / "state_btc_rsi_ema.txt"
POLL_SECS = 60
HISTORY_DAYS = 12   # M5 history per cycle — enough for the M15 EMA200 to converge

# RSI EMA strategy config: RR 1:1 (SL 1.5×ATR / TP 1.5×ATR). Set on the engine
# module in THIS process only — the bot is its own process, so this never
# touches the web server's engine globals.
SL_ATR = 1.5
TP_ATR = 1.5

# Risk guards (env-overridable)
MAX_SIGNALS_PER_DAY = int(os.getenv("BTC_MAX_SIGNALS_PER_DAY", "8"))
COOLDOWN_BARS = int(os.getenv("BTC_COOLDOWN_BARS", "3"))
_RISK: dict = {"day": None, "count": 0, "last_fired_ts": None}

LOG = logging.getLogger("btc-rsi-ema.live")


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


def _format_message(direction: str, entry: float, sl: float, tp: float, atr: float, candle_ts: str, risk_pct: int) -> str:
    arrow = "🟢 BUY" if direction == "BUY" else "🔴 SELL"
    return (
        f"📊 *RSI EMA — BTC/USD* — {arrow}\n"
        f"Symbol: BTCUSD  ·  M5  ·  Binance\n"
        f"Candle (UTC): `{candle_ts}`\n"
        f"Entry: `{entry:.2f}`\n"
        f"SL:    `{sl:.2f}`\n"
        f"TP:    `{tp:.2f}`\n"
        f"ATR:   {atr:.2f}  ·  RR 1:1  ·  Risk {risk_pct}%"
    )


def _fetch_btc_history(fetcher: BtcFetcher) -> pd.DataFrame:
    """~HISTORY_DAYS of *closed* M5 bars (drops the still-forming last candle)."""
    end = pd.Timestamp.now(tz="UTC")
    start = end - pd.Timedelta(days=HISTORY_DAYS)
    df = fetcher.fetch_range(start, end, "5m")
    if df is not None and len(df) > 1:
        df = df.iloc[:-1].reset_index(drop=True)  # last row is the forming candle
    return df


async def _cycle(fetcher: BtcFetcher, ind_engine: IndicatorEngine, tg, last_signal_ts: str | None, risk_pct: int) -> str | None:
    df = await asyncio.to_thread(_fetch_btc_history, fetcher)
    if df is None or len(df) < 250:
        LOG.warning("insufficient candles (%s)", 0 if df is None else len(df))
        return last_signal_ts

    entry_df = ind_engine.add_indicators(df)
    trend_df = ind_engine.add_indicators(engine.resample_to_15m(df))
    trend_df = engine.add_adx14(trend_df)
    trend_lookup = trend_df.set_index("timestamp", drop=False).sort_index()

    i = len(entry_df) - 1  # last CLOSED bar
    ts = pd.Timestamp(entry_df.iloc[i]["timestamp"])
    ts_str = str(ts)[:19]
    if ts_str == last_signal_ts:
        return last_signal_ts

    # Evaluate with the exact RSI EMA engine; crypto is 24/7 so bypass the gold
    # session filter for the duration of this call (single-threaded, process-local).
    _orig_session = engine.session_allowed
    engine.session_allowed = lambda *_a, **_k: True
    try:
        ev = engine.evaluate_signal(
            entry_df=entry_df,
            trend_lookup=trend_lookup,
            signal_index=i,
            start_utc=ts - pd.Timedelta(minutes=1),
            end_utc=ts + pd.Timedelta(minutes=1),
            trace_handle=io.StringIO(),
        )
    finally:
        engine.session_allowed = _orig_session

    sig = ev.get("signal")
    if sig is None:
        LOG.info("no signal @ %s (%s)", ts_str, ev.get("reason"))
        return ts_str

    direction = str(sig["direction"])
    atr_val = float(sig["atr_value"])
    risk_dist = float(sig["risk_distance"])                 # atr × SL_ATR
    tp_dist = atr_val * float(engine.TAKE_PROFIT_ATR_MULTIPLIER)

    # ── Risk guards ─────────────────────────────────────────────────────────
    utc_day = ts.date()
    if _RISK["day"] != utc_day:
        _RISK["day"] = utc_day
        _RISK["count"] = 0
    if _RISK["count"] >= MAX_SIGNALS_PER_DAY:
        LOG.info("risk: daily cap %d reached, skip %s @ %s", MAX_SIGNALS_PER_DAY, direction, ts_str)
        return ts_str
    if _RISK["last_fired_ts"] is not None:
        gap = (ts - _RISK["last_fired_ts"]) / pd.Timedelta(minutes=5)
        if gap < COOLDOWN_BARS:
            LOG.info("risk: cooldown %.0f/%d bars, skip %s @ %s", gap, COOLDOWN_BARS, direction, ts_str)
            return ts_str

    # ── Entry alignment: re-anchor to the live BTC price, keep SL/TP distances ─
    entry = float(entry_df.iloc[i]["close"])
    try:
        snap = await asyncio.to_thread(fetcher.fetch_live_price)
        if snap and snap.get("price"):
            entry = float(snap["price"])
    except Exception as e:
        LOG.warning("live price failed (%s); using signal-bar close", e)

    if direction == "BUY":
        sl = entry - risk_dist
        tp = entry + tp_dist
    else:
        sl = entry + risk_dist
        tp = entry - tp_dist

    # ── Sanity guard: never fire a malformed signal ─────────────────────────
    ok, why = check_signal(
        direction=direction, entry=entry, stop_loss=sl, take_profit=tp, atr=atr_val,
        ref_price=float(entry_df.iloc[i]["close"]),
        recent_high=float(entry_df.iloc[i]["high"]), recent_low=float(entry_df.iloc[i]["low"]),
    )
    if not ok:
        LOG.warning("signal REJECTED by sanity guard: %s (%s @ %s)", why, direction, ts_str)
        return ts_str

    LOG.info("SIGNAL %s @ %s entry=%.2f sl=%.2f tp=%.2f atr=%.2f", direction, ts_str, entry, sl, tp, atr_val)

    telegram_sent = False
    if tg is not None:
        try:
            msg = _format_message(direction, entry, sl, tp, atr_val, ts_str, risk_pct)
            ok = await tg.send_message(msg)   # send_message is a coroutine — await it
            telegram_sent = bool(ok)
            LOG.info("telegram: %s", "sent" if telegram_sent else "not_sent")
        except Exception as e:
            LOG.warning("telegram failed: %s", e)

    if _MONGO_OK:
        try:
            await asyncio.to_thread(
                _save_live_signal,
                symbol="BTCUSD",
                direction=direction,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                atr=atr_val,
                score=1,
                score_threshold=1,
                session="24/7",
                market_regime="trend",
                regime_confidence=1.0,
                trend_alignment=True,   # all filters passed (a signal only fires if they do)
                price_trigger=True,
                rsi_filter=True,
                atr_expansion=True,
                reason="rsi_ema_breakout",
                strategy="rsi-btc",
                strategyName="BTC RSI EMA",
                signal_kind="rsi_btc",
                telegram_sent=telegram_sent,
                candle_time_utc=ts_str,
                timestamp=int(dt.datetime.utcnow().timestamp()),
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
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv(ROOT / ".env")

    global MAX_SIGNALS_PER_DAY, COOLDOWN_BARS
    MAX_SIGNALS_PER_DAY = int(os.getenv("BTC_MAX_SIGNALS_PER_DAY", str(MAX_SIGNALS_PER_DAY)))
    COOLDOWN_BARS = int(os.getenv("BTC_COOLDOWN_BARS", str(COOLDOWN_BARS)))
    risk_pct = int(float(os.getenv("BTC_RISK_PCT", "2")))

    # RSI EMA strategy config on the engine module (this process only).
    engine.STOP_LOSS_ATR_MULTIPLIER = SL_ATR
    engine.TAKE_PROFIT_ATR_MULTIPLIER = TP_ATR

    fetcher = BtcFetcher()
    ind_engine = IndicatorEngine()

    tg = None
    if _TG_OK:
        try:
            tg = TelegramNotifier(
                token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
                chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            )
            LOG.info("telegram: ready")
        except Exception as e:
            LOG.warning("telegram init failed: %s -- running signal-silent", e)
    else:
        LOG.warning("telegram module unavailable -- running signal-silent")

    LOG.info(
        "started; BTCUSD 24/7; RSI EMA SL %.1fx / TP %.1fx ATR (RR 1:1); poll %ds; history %dd",
        SL_ATR, TP_ATR, POLL_SECS, HISTORY_DAYS,
    )

    last = _load_last_signal_ts()
    if last:
        LOG.info("resuming; last signal ts %s", last)

    stop = asyncio.Event()
    for s in (_signal.SIGINT, _signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(s, stop.set)
        except NotImplementedError:
            _signal.signal(s, lambda *_: stop.set())

    while not stop.is_set():
        try:
            last = await _cycle(fetcher, ind_engine, tg, last, risk_pct)
        except Exception as e:
            LOG.exception("cycle error: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECS)
        except asyncio.TimeoutError:
            pass

    LOG.info("stopped")


if __name__ == "__main__":
    asyncio.run(run())
