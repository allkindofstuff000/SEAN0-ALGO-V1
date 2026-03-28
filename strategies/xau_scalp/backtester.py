from __future__ import annotations

import os
import time
from typing import Any

import pandas as pd
import requests

from . import config as cfg
from .cisd_scanner import CISDScanner
from .displacement_scanner import DisplacementScanner
from .liquidity_engine import LiquidityEngine
from .order_block_fvg import OrderBlockFVG
from .risk_manager import RiskManager
from .session_filter import SessionFilter
from .structure_analyzer import StructureAnalyzer
from .utils import normalize_candles, price_to_pips, resample_candles

OANDA_BASE_URLS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_SLEEP = 5
CHUNK_DAYS = 3
MIN_SIGNAL_GAP = 10          # bars between signals to avoid over-trading
LIMIT_FILL_LOOKFORWARD = 20  # bars to wait for limit-order fill


def _base_url() -> str:
    env = os.getenv("OANDA_ENV", "practice").strip().lower()
    return OANDA_BASE_URLS.get(env, OANDA_BASE_URLS["practice"])


def _headers() -> dict[str, str]:
    token = os.getenv("OANDA_API_KEY", "").strip()
    if not token:
        raise RuntimeError("OANDA_API_KEY env var not set — cannot run XAU backtest.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept-Datetime-Format": "RFC3339",
        "User-Agent": "SEAN0-ALGO-xau-ict-backtest/1.0",
    }


def _fetch_chunk(start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, Any]]:
    url = f"{_base_url()}/v3/instruments/{cfg.INSTRUMENT}/candles"
    params = {
        "granularity": "M1",
        "price": "M",
        "from": start.isoformat(),
        "to": end.isoformat(),
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, headers=_headers(), timeout=REQUEST_TIMEOUT)
            if response.status_code in (400, 401, 403):
                return []
            response.raise_for_status()
            payload = response.json().get("candles", [])
            candles: list[dict[str, Any]] = []
            for candle in payload:
                if not candle.get("complete"):
                    continue
                mid = candle.get("mid", {})
                candles.append(
                    {
                        "time": candle["time"][:19],
                        "open": float(mid.get("o", 0)),
                        "high": float(mid.get("h", 0)),
                        "low": float(mid.get("l", 0)),
                        "close": float(mid.get("c", 0)),
                        "volume": int(candle.get("volume", 0)),
                    }
                )
            return candles
        except Exception:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_SLEEP)
    return []


def fetch_m1_candles(start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> list[dict[str, Any]]:
    all_candles: list[dict[str, Any]] = []
    cursor = start_utc
    step = pd.Timedelta(days=CHUNK_DAYS)
    while cursor < end_utc:
        chunk_end = min(cursor + step, end_utc)
        all_candles.extend(_fetch_chunk(cursor, chunk_end))
        cursor = chunk_end
    return normalize_candles(all_candles)


def _available_higher_tf(candles: list[dict[str, Any]], upto_unix: int, tf_minutes: int) -> list[dict[str, Any]]:
    cutoff = upto_unix - ((tf_minutes * 60) - 60)
    idx = 0
    while idx < len(candles) and candles[idx]["time"] <= cutoff:
        idx += 1
    return candles[:idx]


def _zone_bounds(zone: dict[str, Any]) -> tuple[float, float]:
    if zone.get("kind") == "ob":
        return float(zone["zoneLow"]), float(zone["zoneHigh"])
    return float(zone["fvgLow"]), float(zone["fvgHigh"])


def _find_best_sweep(m1: list[dict], bias: str, le: LiquidityEngine) -> dict[str, Any]:
    pools = le.findSSLPools(m1) if bias == "bullish" else le.findBSLPools(m1)
    detector = le.detectSSLSweep if bias == "bullish" else le.detectBSLSweep
    candidates: list[dict[str, Any]] = []
    for pool in pools[:8]:
        sw = detector(m1, pool)
        if sw.get("swept"):
            candidates.append(sw)
    if not candidates:
        return {"swept": False}
    candidates.sort(key=lambda item: (item["candleIndex"], {"high": 2, "medium": 1, "low": 0}.get(item.get("quality"), 0)), reverse=True)
    return candidates[0]


def _build_signal(
    bias: str,
    entry_price: float,
    fvg: dict[str, Any],
    sweep: dict[str, Any],
    displacement: dict[str, Any],
    cisd: dict[str, Any],
    m1: list[dict],
    session: dict[str, Any],
    bias_state: dict[str, Any],
    zone: dict[str, Any],
    rm: RiskManager,
    le: LiquidityEngine,
    score: int,
    strength: str,
) -> dict[str, Any] | None:
    stop_loss = (
        round(float(sweep["sweepCandle"]["low"]) - cfg.SL_BELOW_SWEEP_BUFFER, 2)
        if bias == "bullish"
        else round(float(sweep["sweepCandle"]["high"]) + cfg.SL_BELOW_SWEEP_BUFFER, 2)
    )
    risk_abs = abs(entry_price - stop_loss)
    if risk_abs <= 0:
        return None

    atr_value = rm.calculate_atr(m1)
    default_tp1 = round(entry_price + risk_abs * cfg.TARGET_RR_RATIO, 2) if bias == "bullish" else round(entry_price - risk_abs * cfg.TARGET_RR_RATIO, 2)

    # TP1 — nearest liquidity pool that offers at least MIN_RR_RATIO
    tp1_pool = le.getNearestBSL(m1, entry_price) if bias == "bullish" else le.getNearestSSL(m1, entry_price)
    if tp1_pool:
        candidate = float(tp1_pool["price"])
        reward = (candidate - entry_price) if bias == "bullish" else (entry_price - candidate)
        tp1 = round(candidate, 2) if (risk_abs > 0 and reward / risk_abs >= cfg.MIN_RR_RATIO) else default_tp1
    else:
        tp1 = default_tp1

    # TP2 — extended target at TARGET_RR_RATIO × risk
    tp2 = round(entry_price + risk_abs * cfg.TARGET_RR_RATIO, 2) if bias == "bullish" else round(entry_price - risk_abs * cfg.TARGET_RR_RATIO, 2)

    def rr(ep: float, sl: float, tp: float) -> float:
        r = abs(ep - sl)
        reward = (tp - ep) if bias == "bullish" else (ep - tp)
        return reward / r if r > 0 else 0.0

    rr1 = rr(entry_price, stop_loss, tp1)
    rr2 = rr(entry_price, stop_loss, tp2)
    if max(rr1, rr2) < cfg.MIN_RR_RATIO:
        return None

    return {
        "strategyId": cfg.STRATEGY_ID,
        "strategyName": cfg.STRATEGY_NAME,
        "type": "LONG" if bias == "bullish" else "SHORT",
        "direction": "BUY" if bias == "bullish" else "SELL",
        "timestamp": int(m1[-1]["time"]) * 1000,
        "timeframe": cfg.PRIMARY_TIMEFRAME,
        "instrument": cfg.INSTRUMENT,
        "entryPrice": round(entry_price, 2),
        "entryZone": {"high": fvg["fvgHigh"], "low": fvg["fvgLow"]},
        "stopLoss": stop_loss,
        "takeProfit": round(tp1, 2),
        "takeProfit1": round(tp1, 2),
        "takeProfit2": round(tp2, 2),
        "riskPips": round(price_to_pips(risk_abs), 1),
        "rewardPips1": round(price_to_pips(abs(tp1 - entry_price)), 1),
        "riskReward": round(rr1, 2),
        "riskReward1": round(rr1, 2),
        "riskReward2": round(rr2, 2),
        "atrValue": round(atr_value, 3),
        "sessionName": session.get("zoneName"),
        "sessionPriority": session.get("priority"),
        "trendStrength": bias_state.get("strength"),
        "zone": zone,
        "sweepDepth": sweep.get("sweepDepth"),
        "strength": strength,
        "ictScore": score,
        "outcome": "PENDING",
    }


def _score_signal(
    session: dict[str, Any],
    bias_state: dict[str, Any],
    zone: dict[str, Any],
    sweep: dict[str, Any],
    displacement: dict[str, Any],
    cisd: dict[str, Any],
    pda_good: bool,
) -> tuple[str, int]:
    score = 0
    if session.get("priority") == "HIGH":
        score += 2
    elif session.get("priority") == "MEDIUM":
        score += 1
    if bias_state.get("strength") == "strong":
        score += 2
    elif bias_state.get("strength") == "moderate":
        score += 1
    if zone.get("fresh"):
        score += 2
    elif zone.get("kind") == "fvg":
        score += 1
    if pda_good:
        score += 1
    if sweep.get("quality") == "high":
        score += 1
    if displacement.get("bodyPct", 0) >= 0.80:
        score += 2
    elif displacement.get("bodyPct", 0) >= cfg.DISPLACEMENT_BODY_PCT:
        score += 1
    if int(cisd.get("barsAfterSweep", 99)) <= 2:
        score += 1

    if score >= cfg.STRONG_SIGNAL_MIN:
        return "STRONG", score
    if score >= cfg.MEDIUM_SIGNAL_MIN:
        return "MEDIUM", score
    return "WEAK", score


def _simulate_trade_from_fill(
    signal: dict[str, Any],
    candles: list[dict[str, Any]],
    fill_bar: int,
    max_hold_bars: int,
) -> tuple[str, float, int]:
    """Simulate outcome starting from the bar the limit order was filled."""
    stop = signal["stopLoss"]
    target = signal["takeProfit1"]
    direction = signal["type"]
    total_bars = 0

    for i in range(fill_bar + 1, min(len(candles), fill_bar + 1 + max_hold_bars)):
        candle = candles[i]
        total_bars = i - fill_bar
        if direction == "LONG":
            if candle["low"] <= stop:
                return "LOSS", stop, total_bars
            if candle["high"] >= target:
                return "WIN", target, total_bars
        else:
            if candle["high"] >= stop:
                return "LOSS", stop, total_bars
            if candle["low"] <= target:
                return "WIN", target, total_bars

    last_idx = min(len(candles) - 1, fill_bar + max_hold_bars)
    return "TIMEOUT", candles[last_idx]["close"], min(max_hold_bars, len(candles) - fill_bar - 1)


def run_backtest(
    *,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    starting_balance: float = 10_000.0,
    risk_per_trade: float = 0.01,
    max_hold_bars: int = 200,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    m1_candles = fetch_m1_candles(start_utc, end_utc)
    if len(m1_candles) < cfg.CANDLE_LOOKBACK_M1:
        return [], {"error": "Insufficient M1 history for backtest.", "totalCandles": len(m1_candles)}, []

    m5_candles = resample_candles(m1_candles, "M5")
    m15_candles = resample_candles(m1_candles, "M15")

    # Instantiate sub-modules directly so we can bypass the strict live-price checks
    sf = SessionFilter(cfg)
    sa = StructureAnalyzer(cfg)
    ob = OrderBlockFVG(cfg)
    le = LiquidityEngine(cfg)
    ds = DisplacementScanner(cfg)
    cs = CISDScanner(cfg)
    rm = RiskManager(cfg)

    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = [{"trade": 0, "equity": round(starting_balance, 2), "ts": m1_candles[0]["time"]}]
    balance = starting_balance
    last_signal_index = -MIN_SIGNAL_GAP

    LB = cfg.CANDLE_LOOKBACK_M1

    for i in range(LB, len(m1_candles) - LIMIT_FILL_LOOKFORWARD - 1):
        if i - last_signal_index < MIN_SIGNAL_GAP:
            continue

        m1s = m1_candles[max(0, i - LB + 1):i + 1]
        now_unix = m1s[-1]["time"]
        m5s = _available_higher_tf(m5_candles, now_unix, 5)[-cfg.CANDLE_LOOKBACK_M5:]
        m15s = _available_higher_tf(m15_candles, now_unix, 15)[-cfg.CANDLE_LOOKBACK_M15:]

        if len(m5s) < 20 or len(m15s) < 15:
            continue

        # ── 1. Session gate ────────────────────────────────────────────────
        sess = sf.canTrade(now_unix)
        if not sess["allowed"]:
            continue

        # ── 2. 15M bias gate ──────────────────────────────────────────────
        bias_s = sa.analyzeBias(m15s)
        bias = bias_s["bias"]
        if bias not in {"bullish", "bearish"}:
            continue

        # ── 3. 5M zone — use candle RANGE not just close ──────────────────
        current_candle = m1s[-1]
        zone = ob.getBestZone(m5s, current_candle["close"], bias)
        if not zone:
            continue
        z_low, z_high = _zone_bounds(zone)
        buf = cfg.OB_BUFFER_PRICE
        # Accept if the M1 candle's high/low range overlaps the zone
        candle_touches_zone = (current_candle["low"] <= z_high + buf) and (current_candle["high"] >= z_low - buf)
        if not candle_touches_zone:
            continue

        # ── 4. Liquidity sweep ────────────────────────────────────────────
        sweep = _find_best_sweep(m1s, bias, le)
        if not sweep.get("swept"):
            continue

        # ── 5. Displacement candle ────────────────────────────────────────
        disp = ds.findDisplacement(m1s, bias, start_index=sweep["candleIndex"])
        if not disp.get("found") or disp["candleIndex"] <= sweep["candleIndex"]:
            continue

        # Reject stale setups — displacement must be recent (within 15 bars)
        bars_since_disp = (len(m1s) - 1) - disp["candleIndex"]
        if bars_since_disp > 15:
            continue

        # ── 6. CISD confirmation ──────────────────────────────────────────
        cisd = cs.detectCISD(m1s, sweep, bias)
        cisd_ok = cs.isCISDValid(cisd) and cisd.get("candleIndex", -1) >= disp["candleIndex"]
        if not cisd_ok:
            continue

        # ── 7. Entry FVG — look FORWARD for limit-order fill ──────────────
        fvg = disp.get("fvg")
        if not fvg:
            continue

        entry_price = fvg["fvgMid"]
        fill_bar: int | None = None

        for j in range(i, min(len(m1_candles), i + LIMIT_FILL_LOOKFORWARD)):
            c = m1_candles[j]
            if bias == "bullish" and c["low"] <= entry_price:
                fill_bar = j
                break
            if bias == "bearish" and c["high"] >= entry_price:
                fill_bar = j
                break

        if fill_bar is None:
            continue  # Limit order never filled within look-forward window

        # ── 8. Score and strength ─────────────────────────────────────────
        pda = sf.isPDA(m1s)
        pda_good = (bias == "bullish" and pda["zone"] == "discount") or (bias == "bearish" and pda["zone"] == "premium")
        strength, score = _score_signal(sess, bias_s, zone, sweep, disp, cisd, pda_good)
        if strength == "WEAK":
            continue

        # ── Build signal ──────────────────────────────────────────────────
        signal = _build_signal(
            bias, entry_price, fvg, sweep, disp, cisd, m1s, sess, bias_s, zone, rm, le, score, strength
        )
        if not signal:
            continue

        # ── Simulate trade from the fill bar ─────────────────────────────
        outcome, exit_price, bars_held = _simulate_trade_from_fill(signal, m1_candles, fill_bar, max_hold_bars)
        risk_amount = balance * risk_per_trade
        rr = signal["riskReward1"]
        pnl = risk_amount * rr if outcome == "WIN" else (-risk_amount if outcome == "LOSS" else 0.0)
        balance += pnl
        last_signal_index = i

        trades.append(
            {
                "entry_timestamp": m1_candles[fill_bar]["time"],
                "direction": signal["type"],
                "entry": round(entry_price, 2),
                "stop_loss": round(signal["stopLoss"], 2),
                "take_profit": round(signal["takeProfit1"], 2),
                "take_profit_2": round(signal["takeProfit2"], 2),
                "risk_reward": round(rr, 2),
                "strength": strength,
                "score": score,
                "session": sess.get("zoneName"),
                "bias": bias.upper(),
                "outcome": outcome,
                "exit_price": round(exit_price, 2),
                "bars_held": bars_held,
                "pnl": round(pnl, 2),
                "equity_after": round(balance, 2),
            }
        )
        equity_curve.append({"trade": len(trades), "equity": round(balance, 2), "ts": m1_candles[fill_bar]["time"]})

    total = len(trades)
    wins = sum(1 for t in trades if t["outcome"] == "WIN")
    losses = sum(1 for t in trades if t["outcome"] == "LOSS")
    gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))

    metrics = {
        "totalTrades": total,
        "wins": wins,
        "losses": losses,
        "timeouts": total - wins - losses,
        "winRate": round((wins / total) * 100, 1) if total else 0.0,
        "totalPnl": round(balance - starting_balance, 2),
        "totalPnlPct": round(((balance - starting_balance) / starting_balance) * 100, 2),
        "finalBalance": round(balance, 2),
        "profitFactor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "avgRR": round(sum(t["risk_reward"] for t in trades) / total, 2) if total else 0.0,
        "totalCandles": len(m1_candles),
        "startDate": start_utc.strftime("%Y-%m-%d"),
        "endDate": end_utc.strftime("%Y-%m-%d"),
    }
    return trades, metrics, equity_curve
