"""
Signal outcome resolver.

Every ~2 minutes, scans open live signals (outcome is null) and checks the
subsequent XAUUSD M5 price action: whichever level price touched first — the
take-profit or the stop-loss — decides WIN or LOSS. Updates the Mongo record
(outcome, exit_price, note) so the dashboard's Signal History shows whether
each fired signal was profitable.

Rules (match the strategies' fill convention):
  • SELL: SL hit when a later bar's HIGH >= stop_loss; TP when LOW <= take_profit
  • BUY:  SL hit when a later bar's LOW  <= stop_loss; TP when HIGH >= take_profit
  • If one bar spans BOTH levels, count it a LOSS (stop-first — conservative,
    same honest assumption the backtester makes without tick data).
  • A signal that hasn't hit either level yet stays OPEN and is retried next run.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal as _signal
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from core.data_fetcher import DataFetcher

try:
    from core.indicator_engine import IndicatorEngine
    _IND_OK = True
except Exception:  # pragma: no cover
    _IND_OK = False
    IndicatorEngine = None  # type: ignore

try:
    from core.btc_fetcher import BtcFetcher
    _BTC_OK = True
except Exception:  # pragma: no cover
    _BTC_OK = False
    BtcFetcher = None  # type: ignore

try:
    from core.mongo_store import load_live_signals, update_signal_outcome
    _MONGO_OK = True
except Exception as _exc:  # pragma: no cover
    _MONGO_OK = False

try:
    from core.telegram_bot import TelegramNotifier
    _TG_OK = True
except Exception:
    _TG_OK = False
    TelegramNotifier = None  # type: ignore

ROOT = Path(__file__).resolve().parent
POLL_SECS = 120          # re-check open signals every 2 min
MAX_SIGNAL_AGE_H = 48    # don't chase signals older than 2 days
CANDLE_COUNT = 600       # ~50h+ of M5 — MUST exceed MAX_SIGNAL_AGE_H so the fetch
                         # window always reaches back past the signal candle
                         # (otherwise an early SL/TP touch falls off the front and
                         # the outcome is mis-marked). Weekends only widen coverage.

# ── Health watchdog ─────────────────────────────────────────────────────────
STALE_FEED_MIN = 20         # alert if no fresh OANDA bar in this many minutes
HEALTH_COOLDOWN_MIN = 30    # min minutes between repeat stale-feed alerts
_HEALTH: dict = {"last_alert": None, "heartbeat_day": None}

LOG = logging.getLogger("signal-resolver")


def _forex_open(now) -> bool:
    """Rough OANDA XAUUSD availability: closed Fri 21:00 -> Sun 22:00 UTC, plus
    the nightly 21:00-22:00 settlement break. Used to suppress false stale
    alerts when the feed is *expected* to be quiet."""
    wd = now.weekday()  # Mon=0 .. Sun=6
    h = now.hour
    if wd == 5:                     # Saturday
        return False
    if wd == 4 and h >= 21:        # Friday after 21:00 UTC
        return False
    if wd == 6 and h < 22:         # Sunday before 22:00 UTC
        return False
    if h == 21:                    # nightly settlement break
        return False
    return True


def _send_tg_sync(tg, text: str) -> None:
    """send_message is a coroutine, but health_check runs in a worker thread
    (via asyncio.to_thread) that has no running event loop — so we must actually
    run the coroutine to completion here. Calling tg.send_message(...) bare just
    builds a coroutine object that is never awaited and the alert never sends."""
    if tg is None:
        return
    try:
        asyncio.run(tg.send_message(text))
    except Exception:
        pass


def _daily_heartbeat_msg(fetcher: DataFetcher, now: "pd.Timestamp") -> str:
    """Daily market-state summary so a quiet day never looks like a dead bot:
    resolver alive + gold volatility regime (why signals are/aren't firing) +
    today's signal count + time since the last signal."""
    lines = [
        f"✅ *SEAN ALGO — daily check*  ({now:%Y-%m-%d %H:%M} UTC)",
        "Resolver alive · all 3 bots evaluating.",
    ]
    # Gold volatility regime — the usual reason signals go quiet.
    try:
        gdf = fetcher.fetch_oanda("5m", 60)
        if _IND_OK and gdf is not None and len(gdf) > 20:
            gdf = IndicatorEngine().add_indicators(gdf)
            last = gdf.iloc[-1]
            atr = float(last["atr14"])
            avg = float(last.get("atr20_avg") or atr)
            level = "🔴 high-vol" if atr >= 15 else ("⚪ normal" if atr >= 8 else "🟡 low / quiet")
            trend = "↑ rising" if atr >= avg * 1.1 else ("↓ falling" if atr <= avg * 0.9 else "→ flat")
            lines.append(f"Gold ATR: {atr:.1f} — {level} ({trend} vs {avg:.1f} avg)")
    except Exception:  # noqa: BLE001 — advisory line only
        lines.append("Gold ATR: n/a (feed closed / unavailable)")
    # Signal activity (today + gap since last).
    try:
        sigs = load_live_signals(limit=200) if _MONGO_OK else []
        dated = [s for s in sigs if (s.get("sent_at") or s.get("candle_time_utc"))]
        today_n = sum(1 for s in dated if str(s.get("sent_at") or s.get("candle_time_utc"))[:10] == f"{now.date()}")
        if dated:
            def _t(s):
                t = pd.Timestamp(s.get("sent_at") or s.get("candle_time_utc"))
                return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
            ah = (now - max(_t(s) for s in dated)) / pd.Timedelta(hours=1)
            ago = f"{int(ah) // 24}d {int(ah) % 24}h" if ah >= 24 else f"{ah:.1f}h"
            lines.append(f"Signals today: {today_n}  ·  last signal {ago} ago")
        else:
            lines.append(f"Signals today: {today_n}")
    except Exception:  # noqa: BLE001
        pass
    lines.append("_A quiet day = no valid setup, not a fault._")
    return "\n".join(lines)


def health_check(fetcher: DataFetcher, tg) -> None:
    """Daily heartbeat + stale-feed watchdog, both via Telegram."""
    now = pd.Timestamp.now(tz="UTC")

    # Once-a-day heartbeat so silence never means "is it even running?"
    if _HEALTH["heartbeat_day"] != now.date():
        _HEALTH["heartbeat_day"] = now.date()
        _send_tg_sync(tg, _daily_heartbeat_msg(fetcher, now))

    if not _forex_open(now):
        return
    try:
        snap = fetcher.fetch_live_market_snapshot("1m")
        bar_ts = pd.Timestamp(snap["timestamp"])
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.tz_localize("UTC")
        age = (now - bar_ts) / pd.Timedelta(minutes=1)
    except Exception as e:  # noqa: BLE001
        LOG.warning("health: snapshot failed: %s", e)
        return

    if age > STALE_FEED_MIN:
        last = _HEALTH["last_alert"]
        if last is None or (now - last) / pd.Timedelta(minutes=1) >= HEALTH_COOLDOWN_MIN:
            _HEALTH["last_alert"] = now
            LOG.warning("health: STALE FEED %.0f min (last bar %s)", age, bar_ts)
            _send_tg_sync(
                tg,
                f"⚠️ SEAN ALGO: OANDA feed looks stale — last bar "
                f"{bar_ts:%H:%M} UTC is {age:.0f} min old. Check the bots/VPS.",
            )


def _resolve_one(sig: dict, df: pd.DataFrame) -> tuple[str, float, str] | None:
    """Return (outcome, exit_price, note) or None if still open / unresolvable."""
    try:
        direction = str(sig["direction"]).upper()
        sl = float(sig["stop_loss"])
        tp = float(sig["take_profit"])
    except (KeyError, TypeError, ValueError):
        return None

    # Entry reference: an explicit entry_time_utc wins — the MACD bot enters at
    # the H1 CLOSE, so its signal-bar OPEN (candle_time_utc) would otherwise make
    # us scan a full pre-entry hour and mis-mark WIN/LOSS. Otherwise use the M5
    # convention: signal-bar open, entry on the NEXT bar.
    et_raw = sig.get("entry_time_utc")
    ref_raw = et_raw or sig.get("candle_time_utc") or sig.get("sent_at")
    if not ref_raw:
        return None
    ref = pd.Timestamp(ref_raw)
    if ref.tzinfo is None:
        ref = ref.tz_localize("UTC")
    inclusive = et_raw is not None  # entry is AT entry_time → include that bar

    # Coverage guard: the fetched window must reach back to (or before) the entry
    # reference. If the earliest fetched bar is AFTER it, an early SL/TP touch
    # could have happened off the front of the window — so resolving now risks a
    # wrong verdict. Stay OPEN and retry next cycle instead of guessing.
    if not df.empty:
        earliest = pd.Timestamp(df["timestamp"].min())
        if earliest.tzinfo is None:
            earliest = earliest.tz_localize("UTC")
        if earliest > ref:
            return None

    # Bars from the entry onward (>= when entry is AT the reference, else strictly after).
    fut = df[df["timestamp"] >= ref] if inclusive else df[df["timestamp"] > ref]
    for _, bar in fut.iterrows():
        hi = float(bar["high"])
        lo = float(bar["low"])
        when = str(bar["timestamp"])[:16]
        if direction == "SELL":
            hit_sl = hi >= sl
            hit_tp = lo <= tp
        else:  # BUY
            hit_sl = lo <= sl
            hit_tp = hi >= tp

        if hit_sl and hit_tp:
            return "LOSS", sl, f"SL+TP same bar {when} (stop-first)"
        if hit_sl:
            return "LOSS", sl, f"SL hit {when}"
        if hit_tp:
            return "WIN", tp, f"TP hit {when}"
    return None  # still open


def _resolve_batch(open_sigs: list[dict], df: "pd.DataFrame", label: str) -> int:
    """Resolve a batch of open signals against a candle frame (symbol-agnostic)."""
    resolved = 0
    for s in open_sigs:
        verdict = _resolve_one(s, df)
        if verdict is None:
            continue
        outcome, exit_price, note = verdict
        if update_signal_outcome(str(s["_id"]), outcome, exit_price, note):
            resolved += 1
            LOG.info(
                "resolved %s %s @ %.2f -> %s (%s)",
                s.get("direction"), s.get("symbol"), float(s.get("entry_price", 0)),
                outcome, note,
            )
    LOG.info("[%s] resolved %d/%d open signals", label, resolved, len(open_sigs))
    return resolved


def resolve_once(fetcher: DataFetcher) -> int:
    if not _MONGO_OK:
        LOG.warning("mongo unavailable; nothing to do")
        return 0

    signals = load_live_signals(limit=200)
    now = pd.Timestamp.now(tz="UTC")

    def _open_within_age(s: dict) -> bool:
        if s.get("outcome"):
            return False
        if s.get("stop_loss") is None or s.get("take_profit") is None:
            return False
        ct_raw = s.get("candle_time_utc") or s.get("sent_at")
        if not ct_raw:
            return False
        ct = pd.Timestamp(ct_raw)
        if ct.tzinfo is None:
            ct = ct.tz_localize("UTC")
        return (now - ct) <= pd.Timedelta(hours=MAX_SIGNAL_AGE_H)

    def _sym(s: dict) -> str:
        return str(s.get("symbol", "")).upper()

    open_xau = [s for s in signals if _open_within_age(s)
                and not any(x in _sym(s) for x in ("BTC", "ETH", "CRYPTO"))]
    open_btc = [s for s in signals if _open_within_age(s) and "BTC" in _sym(s)]

    if not open_xau and not open_btc:
        LOG.info("no open signals to resolve")
        return 0

    total = 0

    # XAU / gold — OANDA M5
    if open_xau:
        try:
            xdf = fetcher.fetch_oanda("5m", CANDLE_COUNT).sort_values("timestamp").reset_index(drop=True)
            total += _resolve_batch(open_xau, xdf, "XAU")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("XAU candle fetch failed: %s", exc)

    # BTC — Binance mirror M5 (24/7); needs its own price feed
    if open_btc:
        if not _BTC_OK:
            LOG.warning("BTC fetcher unavailable; %d BTC signals left open", len(open_btc))
        else:
            try:
                bdf = BtcFetcher().fetch_klines("5m", 600, closed_only=True).sort_values("timestamp").reset_index(drop=True)
                total += _resolve_batch(open_btc, bdf, "BTC")
            except Exception as exc:  # noqa: BLE001
                LOG.warning("BTC candle fetch failed: %s", exc)

    return total


async def run() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv(ROOT / ".env")

    fetcher = DataFetcher(min_candles=CANDLE_COUNT)
    try:
        fetcher.startup_check()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("startup_check failed: %s", exc)

    tg = None
    if _TG_OK:
        try:
            tg = TelegramNotifier(
                token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
                chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            )
        except Exception as exc:  # noqa: BLE001
            LOG.warning("telegram init failed: %s", exc)

    LOG.info("signal-resolver started; poll %ds; stale-feed alert >%dmin", POLL_SECS, STALE_FEED_MIN)

    stop = asyncio.Event()
    for s in (_signal.SIGINT, _signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(s, stop.set)
        except NotImplementedError:
            _signal.signal(s, lambda *_: stop.set())

    while not stop.is_set():
        try:
            await asyncio.to_thread(resolve_once, fetcher)
        except Exception as exc:  # noqa: BLE001
            LOG.exception("resolve cycle error: %s", exc)
        try:
            await asyncio.to_thread(health_check, fetcher, tg)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("health cycle error: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECS)
        except asyncio.TimeoutError:
            pass

    LOG.info("signal-resolver stopped")


if __name__ == "__main__":
    asyncio.run(run())
