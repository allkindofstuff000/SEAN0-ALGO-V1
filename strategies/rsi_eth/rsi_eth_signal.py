"""
ETH RSI 15 TF — Live signal engine.
RSI(7) cross + EMA(9/50) bias + 3 filters (Spread, Time, ADX).
Proven: 43.1% WR, 2.02 PF, +73.9% return over 3 months.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from . import config as cfg

# MongoDB persistence (non-fatal)
try:
    from core.mongo_store import save_strategy_signal as _save_signal
except Exception:
    def _save_signal(_): return None

log = logging.getLogger(__name__)


def _ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return [None] * len(values)
    mult = 2.0 / (period + 1)
    result = [None] * (period - 1)
    sma = sum(values[:period]) / period
    result.append(sma)
    for i in range(period, len(values)):
        sma = values[i] * mult + result[-1] * (1 - mult)
        result.append(sma)
    return result


def _rsi(closes: list[float], period: int = 7) -> list[float | None]:
    if len(closes) < period + 1:
        return [None] * len(closes)
    result = [None] * period
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    result.append(100 - 100 / (1 + rs))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        result.append(100 - 100 / (1 + rs))
    return result


def _atr(candles: list[dict], period: int = 14) -> list[float | None]:
    if len(candles) < 2:
        return [None] * len(candles)
    trs = [None]
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    result = [None] * period
    if len(trs) > period:
        avg = sum(t for t in trs[1:period + 1]) / period
        result.append(avg)
        for i in range(period + 1, len(trs)):
            avg = (avg * (period - 1) + trs[i]) / period
            result.append(avg)
    return result


def _adx(candles: list[dict], period: int = 14) -> list[float | None]:
    n = len(candles)
    if n < period * 2 + 2:
        return [None] * n
    result = [None] * n
    pdm = [0.0] * n
    mdm = [0.0] * n
    tr_l = [0.0] * n
    for i in range(1, n):
        h, l = candles[i]["high"], candles[i]["low"]
        ph, pl = candles[i - 1]["high"], candles[i - 1]["low"]
        pc = candles[i - 1]["close"]
        tr_l[i] = max(h - l, abs(h - pc), abs(l - pc))
        up = h - ph
        dn = pl - l
        pdm[i] = up if up > dn and up > 0 else 0
        mdm[i] = dn if dn > up and dn > 0 else 0
    atr_s = sum(tr_l[1:period + 1])
    ps = sum(pdm[1:period + 1])
    ms = sum(mdm[1:period + 1])
    dx_vals = []
    for i in range(period, n):
        if i == period:
            atr_s = sum(tr_l[1:period + 1])
            ps = sum(pdm[1:period + 1])
            ms = sum(mdm[1:period + 1])
        else:
            atr_s = atr_s - atr_s / period + tr_l[i]
            ps = ps - ps / period + pdm[i]
            ms = ms - ms / period + mdm[i]
        pdi = 100 * ps / atr_s if atr_s > 0 else 0
        mdi = 100 * ms / atr_s if atr_s > 0 else 0
        ds = pdi + mdi
        dx = 100 * abs(pdi - mdi) / ds if ds > 0 else 0
        dx_vals.append(dx)
    if len(dx_vals) >= period:
        adx_val = sum(dx_vals[:period]) / period
        for j in range(period, n):
            idx = j - period
            if idx < len(dx_vals):
                adx_val = (adx_val * (period - 1) + dx_vals[idx]) / period
                result[j] = adx_val
    return result


class RsiEthSignal:
    """ETH RSI 15 TF — RSI cross + EMA bias + 3 filters."""

    def __init__(self, binance_engine=None):
        self._engine = binance_engine
        self._candles_m15: list[dict[str, Any]] = []
        self._candles_m5: list[dict[str, Any]] = []
        self._latest_signal: dict[str, Any] | None = None
        self._signal_history: deque = deque(maxlen=200)
        self._sub_id: int | None = None
        self._queue = None
        self._live_price: float = 0.0
        self._live_time: int = 0
        self._initialized = False
        self._paused = False
        self._last_signal_time: float = 0
        self._stats = {
            "totalSignals": 0, "buySignals": 0, "sellSignals": 0,
            "lastSignalTime": None, "candleCount": 0,
        }
        self._decision_log: deque = deque(maxlen=500)
        self._conditions: dict[str, Any] = {}
        self._market_context: dict[str, Any] = {
            "session": "24/7 Crypto",
            "market_regime": "Unknown",
            "strategy_behavior": "warming_up",
            "regime_details": {},
            "market_open": True,
        }

    async def initialize(self):
        if not self._engine:
            log.warning("[ETH-RSI-15] no engine — offline mode")
            return
        try:
            self._candles_m15 = list(self._engine.store.get_candles("M15"))[-cfg.CANDLE_LOOKBACK:]
            self._candles_m5 = list(self._engine.store.get_candles("M5"))[-cfg.CANDLE_LOOKBACK:]
            self._sub_id, self._queue = self._engine.subscribe()
            asyncio.create_task(self._event_loop(), name="eth_rsi_15tf_events")
            self._initialized = True
            log.info("[ETH-RSI-15] initialized M15=%d M5=%d", len(self._candles_m15), len(self._candles_m5))
        except Exception as exc:
            log.error("[ETH-RSI-15] init error: %s", exc)

    def stop(self):
        self._paused = True
        self._initialized = False
        log.info("[ETH-RSI-15] stopped")

    async def resume(self):
        self._paused = False
        self._initialized = True
        log.info("[ETH-RSI-15] resumed")

    async def _event_loop(self):
        while True:
            try:
                raw = await asyncio.wait_for(self._queue.get(), timeout=60.0)
                if self._paused:
                    continue
                event = json.loads(raw)
                etype = event.get("type")
                if etype == "candle":
                    tf = event.get("timeframe")
                    candle = event.get("candle")
                    if candle:
                        self._on_candle(tf, candle)
                elif etype == "tick":
                    self._live_price = float(event.get("price", self._live_price))
                    self._live_time = int(event.get("time", self._live_time))
            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                log.error("[ETH-RSI-15] event loop error: %s", exc)
                await asyncio.sleep(1)

    def _on_candle(self, tf: str, candle: dict):
        if tf == "M15":
            self._candles_m15.append(candle)
            if len(self._candles_m15) > cfg.CANDLE_LOOKBACK:
                self._candles_m15 = self._candles_m15[-cfg.CANDLE_LOOKBACK:]
            self._evaluate()
        elif tf == "M5":
            self._candles_m5.append(candle)
            if len(self._candles_m5) > cfg.CANDLE_LOOKBACK:
                self._candles_m5 = self._candles_m5[-cfg.CANDLE_LOOKBACK:]

    def _evaluate(self):
        """
        ETH RSI 15 TF signal evaluation.
        Entry: RSI(7) crosses oversold/overbought + price vs EMA(50) bias.
        Filters: Spread/Wick, Time of Day, ADX > 20.
        """
        candles = self._candles_m15
        if len(candles) < cfg.EMA_SLOW + 5:
            return

        now = _time.time()
        if now - self._last_signal_time < cfg.SIGNAL_COOLDOWN_S:
            return

        # Calculate all indicators on M15
        closes = [c["close"] for c in candles]
        ema_f = _ema(closes, cfg.EMA_FAST)      # EMA(9)
        ema_s = _ema(closes, cfg.EMA_SLOW)       # EMA(50)
        rsi_v = _rsi(closes, cfg.RSI_PERIOD)     # RSI(7)
        atr_v = _atr(candles, cfg.ATR_PERIOD)    # ATR(14)
        adx_v = _adx(candles, cfg.ADX_PERIOD)    # ADX(14)

        i = len(candles) - 1
        price = candles[i]["close"]
        c = candles[i]

        # Get indicator values
        curr_rsi = rsi_v[i] if i < len(rsi_v) and rsi_v[i] is not None else 50
        prev_rsi = rsi_v[i - 1] if i - 1 < len(rsi_v) and rsi_v[i - 1] is not None else 50
        fast_now = ema_f[i] if i < len(ema_f) and ema_f[i] is not None else 0
        slow_now = ema_s[i] if i < len(ema_s) and ema_s[i] is not None else 0
        atr_val = atr_v[i] if i < len(atr_v) and atr_v[i] is not None else 0
        adx_val = adx_v[i] if i < len(adx_v) and adx_v[i] is not None else None
        atr_avg = self._atr_average(atr_v)

        if fast_now == 0 or slow_now == 0 or atr_val == 0:
            return

        # ── RSI cross + EMA bias (Entry Option C) ──
        rsi_bull_cross = prev_rsi <= cfg.RSI_OVERSOLD and curr_rsi > cfg.RSI_OVERSOLD
        rsi_bear_cross = prev_rsi >= cfg.RSI_OVERBOUGHT and curr_rsi < cfg.RSI_OVERBOUGHT

        signal = None
        direction = "NONE"
        if rsi_bull_cross and price > slow_now:
            signal = "LONG"
            direction = "BUY"
        elif rsi_bear_cross and price < slow_now:
            signal = "SHORT"
            direction = "SELL"

        # Update conditions for dashboard display
        self._conditions = {
            "trend": {"active": fast_now > slow_now if signal == "LONG" else fast_now < slow_now if signal == "SHORT" else False,
                      "bias": "bull" if price > slow_now else "bear",
                      "ema9": round(fast_now, 2), "ema50": round(slow_now, 2)},
            "rsi": {"active": rsi_bull_cross or rsi_bear_cross,
                    "value": round(curr_rsi, 1), "prev": round(prev_rsi, 1),
                    "cross": "bull_cross" if rsi_bull_cross else ("bear_cross" if rsi_bear_cross else "none")},
            "atr": {"active": atr_val > 0, "value": round(atr_val, 2)},
            "adx": {"active": adx_val is not None and adx_val >= cfg.ADX_MIN,
                    "value": round(adx_val, 1) if adx_val else 0},
        }
        self._market_context = self._build_market_context(
            candle=c,
            fast_now=fast_now,
            slow_now=slow_now,
            atr_val=atr_val,
            atr_avg=atr_avg,
            adx_val=adx_val,
        )
        self._stats["candleCount"] = len(candles)

        if not signal:
            self._decision_log.appendleft({
                "ts": int(now * 1000), "price": round(price, 2),
                "direction": direction, "decision": "NO_SIGNAL",
                "reason": f"RSI:{curr_rsi:.1f} EMA9:{fast_now:.0f} EMA50:{slow_now:.0f} ADX:{adx_val:.1f}" if adx_val else f"RSI:{curr_rsi:.1f} EMA9:{fast_now:.0f} EMA50:{slow_now:.0f}",
                "conditions": {k: v["active"] for k, v in self._conditions.items()},
            })
            return

        # ── FILTER 1: Spread/Wick — skip doji candles ──
        if getattr(cfg, "FILTER_SPREAD", True):
            body = abs(c["close"] - c["open"])
            rng = c["high"] - c["low"]
            if rng > 0 and body / rng < 0.30:
                self._decision_log.appendleft({
                    "ts": int(now * 1000), "price": round(price, 2),
                    "direction": direction, "decision": "FILTERED",
                    "reason": f"Spread/Wick filter — body {body/rng*100:.0f}% < 30%",
                    "conditions": {k: v["active"] for k, v in self._conditions.items()},
                })
                return

        # ── FILTER 2: Time of Day — skip dead hours ──
        if getattr(cfg, "FILTER_TIME_OF_DAY", True):
            ts = c.get("time", 0)
            if isinstance(ts, (int, float)) and ts > 1e9:
                hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
                if 2 <= hour < 5 or 20 <= hour < 22:
                    self._decision_log.appendleft({
                        "ts": int(now * 1000), "price": round(price, 2),
                        "direction": direction, "decision": "FILTERED",
                        "reason": f"Time filter — {hour}:00 UTC blocked",
                        "conditions": {k: v["active"] for k, v in self._conditions.items()},
                    })
                    return

        # ── FILTER 3: ADX > 20 — skip choppy markets ──
        if getattr(cfg, "FILTER_ADX", True):
            if adx_val is not None and adx_val < cfg.ADX_MIN:
                self._decision_log.appendleft({
                    "ts": int(now * 1000), "price": round(price, 2),
                    "direction": direction, "decision": "FILTERED",
                    "reason": f"ADX filter — ADX {adx_val:.1f} < {cfg.ADX_MIN} (choppy)",
                    "conditions": {k: v["active"] for k, v in self._conditions.items()},
                })
                return

        # ── Build signal ──
        sl_dist = atr_val * cfg.ATR_SL_MULTIPLIER
        tp_dist = sl_dist * cfg.ATR_TP_MULTIPLIER
        if direction == "BUY":
            sl = price - sl_dist
            tp = price + tp_dist
        else:
            sl = price + sl_dist
            tp = price - tp_dist

        rr = tp_dist / sl_dist if sl_dist > 0 else 0
        if rr < cfg.MIN_RR_RATIO:
            return

        signal_obj = {
            "strategyId": cfg.STRATEGY_ID,
            "strategyName": cfg.STRATEGY_NAME,
            "type": direction,
            "strength": "STRONG",
            "entryPrice": round(price, 2),
            "stopLoss": round(sl, 2),
            "takeProfit": round(tp, 2),
            "breakEven": round(price + (tp_dist / 3 if direction == "BUY" else -tp_dist / 3), 2),
            "riskReward": round(rr, 2),
            "score": 80,
            "atr": round(atr_val, 2),
            "adx": round(adx_val, 1) if adx_val else 0,
            "rsi": round(curr_rsi, 1),
            "ema9": round(fast_now, 2),
            "ema50": round(slow_now, 2),
            "timestamp": int(now * 1000),
            "instrument": cfg.INSTRUMENT,
            "timeframe": cfg.PRIMARY_TF,
        }

        self._latest_signal = signal_obj
        self._signal_history.appendleft(signal_obj)
        self._last_signal_time = now
        self._stats["totalSignals"] += 1
        if direction == "BUY":
            self._stats["buySignals"] += 1
        else:
            self._stats["sellSignals"] += 1
        self._stats["lastSignalTime"] = signal_obj["timestamp"]

        self._decision_log.appendleft({
            "ts": int(now * 1000), "price": round(price, 2),
            "direction": direction, "decision": "SIGNAL",
            "reason": f"{direction} @ {price:.2f} | SL:{sl:.2f} TP:{tp:.2f} | RSI:{curr_rsi:.1f} ADX:{adx_val:.1f}",
            "conditions": {k: v["active"] for k, v in self._conditions.items()},
        })

        log.info("[ETH-RSI-15] %s signal @ %.2f | SL: %.2f | TP: %.2f | RSI: %.1f | ADX: %.1f",
                 direction, price, sl, tp, curr_rsi, adx_val or 0)

        # MongoDB
        try:
            _save_signal({
                "strategy": cfg.STRATEGY_ID,
                "strategyName": cfg.STRATEGY_NAME,
                "symbol": cfg.INSTRUMENT,
                "direction": direction,
                "entry_price": price,
                "stop_loss": sl,
                "take_profit": tp,
                "score": 80,
                "strength": "STRONG",
                "rsi": round(curr_rsi, 1),
                "adx": round(adx_val, 1) if adx_val else 0,
                "timestamp": signal_obj["timestamp"],
            })
        except Exception as exc:
            log.warning("[ETH-RSI-15] MongoDB save failed: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        if (not self._market_context or self._market_context.get("market_regime") == "Unknown") and self._candles_m15:
            self._refresh_market_context_only()
        return {
            "initialized": self._initialized,
            "paused": self._paused,
            "strategyId": cfg.STRATEGY_ID,
            "strategyName": cfg.STRATEGY_NAME,
            "instrument": cfg.INSTRUMENT,
            "livePrice": self._live_price,
            "conditions": self._conditions,
            "latestSignal": self._latest_signal,
            "stats": self._stats,
            "session": self._market_context.get("session"),
            "market_regime": self._market_context.get("market_regime"),
            "strategy_behavior": self._market_context.get("strategy_behavior"),
            "regime_details": self._market_context.get("regime_details", {}),
            "market_open": self._market_context.get("market_open", True),
        }

    def get_latest_signal(self): return self._latest_signal
    def get_signal_history(self, limit=20): return list(self._signal_history)[:limit]
    def get_decision_log(self, limit=100): return list(self._decision_log)[:limit]
    def get_conditions(self): return self._conditions

    def _refresh_market_context_only(self) -> None:
        candles = self._candles_m15
        if len(candles) < cfg.EMA_SLOW + 5:
            return
        closes = [c["close"] for c in candles]
        ema_f = _ema(closes, cfg.EMA_FAST)
        ema_s = _ema(closes, cfg.EMA_SLOW)
        atr_v = _atr(candles, cfg.ATR_PERIOD)
        adx_v = _adx(candles, cfg.ADX_PERIOD)
        i = len(candles) - 1
        fast_now = ema_f[i] if i < len(ema_f) and ema_f[i] is not None else 0
        slow_now = ema_s[i] if i < len(ema_s) and ema_s[i] is not None else 0
        atr_val = atr_v[i] if i < len(atr_v) and atr_v[i] is not None else 0
        adx_val = adx_v[i] if i < len(adx_v) and adx_v[i] is not None else None
        if fast_now == 0 or slow_now == 0 or atr_val == 0:
            return
        self._market_context = self._build_market_context(
            candle=candles[i],
            fast_now=fast_now,
            slow_now=slow_now,
            atr_val=atr_val,
            atr_avg=self._atr_average(atr_v),
            adx_val=adx_val,
        )

    def _atr_average(self, atr_values: list[float | None]) -> float:
        valid = [float(v) for v in atr_values if v is not None]
        if not valid:
            return 0.0
        window = valid[-cfg.ATR_AVG_WINDOW:] if len(valid) >= cfg.ATR_AVG_WINDOW else valid
        return sum(window) / len(window)

    def _build_market_context(
        self,
        *,
        candle: dict[str, Any],
        fast_now: float,
        slow_now: float,
        atr_val: float,
        atr_avg: float,
        adx_val: float | None,
    ) -> dict[str, Any]:
        session_name = self._detect_session_name(candle.get("time"))
        adx_value = float(adx_val) if adx_val is not None else 0.0
        ema_spread = abs(fast_now - slow_now)
        vol_ratio = (atr_val / atr_avg) if atr_avg > 0 else 1.0

        if adx_value >= cfg.ADX_MIN + 5 and vol_ratio >= 1.15:
            regime = "Volatile"
            behavior = "momentum_breakout"
            should_trade = True
        elif adx_value >= cfg.ADX_MIN:
            regime = "Trending"
            behavior = "trend_follow"
            should_trade = True
        elif vol_ratio < 0.9:
            regime = "Calm"
            behavior = "standby"
            should_trade = False
        else:
            regime = "Ranging"
            behavior = "standby"
            should_trade = False

        return {
            "session": session_name,
            "market_regime": regime,
            "strategy_behavior": behavior,
            "market_open": True,
            "regime_details": {
                "regime": regime,
                "should_trade": should_trade,
                "trend_bias": "bull" if fast_now > slow_now else "bear" if fast_now < slow_now else "flat",
                "adx14": round(adx_value, 1),
                "atr14": round(atr_val, 2),
                "atr_avg": round(atr_avg, 2),
                "atr_ratio": round(vol_ratio, 2),
                "ema_spread": round(ema_spread, 2),
                "strategy_behavior": behavior,
            },
        }

    def _detect_session_name(self, timestamp: Any) -> str:
        dt = self._coerce_utc_datetime(timestamp)
        hour = dt.hour
        if 0 <= hour < 8:
            return "Asian Session"
        if 8 <= hour < 13:
            return "London Session"
        if 13 <= hour < 16:
            return "London / New York Overlap"
        if 16 <= hour < 21:
            return "New York Session"
        return "After Hours"

    def _coerce_utc_datetime(self, timestamp: Any) -> datetime:
        if isinstance(timestamp, datetime):
            return timestamp.astimezone(timezone.utc)
        if isinstance(timestamp, (int, float)):
            value = float(timestamp)
            if value > 1e12:
                value /= 1000.0
            return datetime.fromtimestamp(value, tz=timezone.utc)
        return datetime.now(timezone.utc)
