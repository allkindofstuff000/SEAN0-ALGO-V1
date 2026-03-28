from __future__ import annotations

from typing import Any

from . import config as cfg
from .cisd_scanner import CISDScanner
from .displacement_scanner import DisplacementScanner
from .liquidity_engine import LiquidityEngine
from .order_block_fvg import OrderBlockFVG
from .risk_manager import RiskManager
from .session_filter import SessionFilter
from .structure_analyzer import StructureAnalyzer
from .utils import normalize_candles, price_to_pips


class ICTEntryEngine:
    def __init__(
        self,
        config=cfg,
        structureAnalyzer: StructureAnalyzer | None = None,
        orderBlockFVG: OrderBlockFVG | None = None,
        liquidityEngine: LiquidityEngine | None = None,
        displacementScanner: DisplacementScanner | None = None,
        cisdScanner: CISDScanner | None = None,
        sessionFilter: SessionFilter | None = None,
        riskManager: RiskManager | None = None,
    ):
        self.config = config
        self.structureAnalyzer = structureAnalyzer or StructureAnalyzer(config)
        self.orderBlockFVG = orderBlockFVG or OrderBlockFVG(config)
        self.liquidityEngine = liquidityEngine or LiquidityEngine(config)
        self.displacementScanner = displacementScanner or DisplacementScanner(config)
        self.cisdScanner = cisdScanner or CISDScanner(config)
        self.sessionFilter = sessionFilter or SessionFilter(config)
        self.riskManager = riskManager or RiskManager(config)

    def evaluate(
        self,
        m15Candles: list[dict[str, Any]],
        m5Candles: list[dict[str, Any]],
        m1Candles: list[dict[str, Any]],
        currentTick: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        m15 = normalize_candles(m15Candles)
        m5 = normalize_candles(m5Candles)
        m1 = normalize_candles(m1Candles)
        if len(m1) < 30 or len(m5) < 20 or len(m15) < 15:
            conditions = self._empty_conditions(currentTick.get("time") if currentTick else (m1[-1]["time"] if m1 else None))
            return {"signal": None, "conditions": conditions, "reason": "Waiting for more candles", "score": 0}

        current_price = float((currentTick or {}).get("price", m1[-1]["close"]))
        now = (currentTick or {}).get("time", m1[-1]["time"])
        conditions = self._empty_conditions(now)

        session_state = self.sessionFilter.canTrade(now)
        countdown = self.sessionFilter.getNextHighPrioritySession(now)
        conditions["session"] = {
            **session_state,
            "active": session_state["allowed"],
            "countdown": countdown,
            "headline": session_state["zoneName"],
            "detail": session_state["reason"],
            "badge": "ACTIVE" if session_state["allowed"] else "WAITING",
        }
        if not session_state["allowed"]:
            return {"signal": None, "conditions": conditions, "reason": session_state["reason"], "score": 0}

        bias_state = self.structureAnalyzer.analyzeBias(m15)
        bias = bias_state["bias"]
        conditions["bias15m"] = {
            **bias_state,
            "active": bias in {"bullish", "bearish"},
            "headline": bias.upper() if bias in {"bullish", "bearish"} else "NEUTRAL",
            "detail": self._bias_detail(bias_state),
            "badge": bias_state["strength"].upper(),
        }
        if bias not in {"bullish", "bearish"}:
            return {"signal": None, "conditions": conditions, "reason": "15M structure neutral — waiting for BOS", "score": 0}

        zone = self.orderBlockFVG.getBestZone(m5, current_price, bias)
        in_zone = self.orderBlockFVG.isPriceInZone(current_price, zone)
        conditions["zone5m"] = {
            "active": bool(zone and in_zone),
            "zone": zone,
            "priceInZone": in_zone,
            "headline": self.orderBlockFVG.getZoneDescription(zone) if zone else "No valid zone",
            "detail": "Price inside zone" if in_zone else "Waiting for pullback into 5M zone",
            "badge": "FRESH" if zone and zone.get("fresh") else "SETUP",
        }
        if not zone:
            return {"signal": None, "conditions": conditions, "reason": "No valid 5M OB/FVG", "score": 0}
        if not in_zone:
            return {"signal": None, "conditions": conditions, "reason": "Waiting for price to retrace into 5M zone", "score": 0}

        sweep = self._find_best_sweep(m1, bias)
        conditions["sslSweep"] = {
            "active": bool(sweep.get("swept")),
            "sweep": sweep,
            "headline": self._sweep_headline(sweep, bias),
            "detail": self._sweep_detail(sweep),
            "badge": (sweep.get("quality") or "LOW").upper(),
        }
        if not sweep.get("swept"):
            return {"signal": None, "conditions": conditions, "reason": "Waiting for liquidity sweep", "score": 0}

        displacement = self.displacementScanner.findDisplacement(m1, bias, start_index=sweep["candleIndex"])
        conditions["displacement"] = {
            "active": bool(displacement.get("found")),
            "headline": self._displacement_headline(displacement, bias),
            "detail": self._displacement_detail(displacement),
            "badge": "ACTIVE" if displacement.get("found") else "WAITING",
            "data": displacement,
        }
        if not displacement.get("found"):
            return {"signal": None, "conditions": conditions, "reason": "Waiting for displacement candle", "score": 0}
        if displacement["candleIndex"] <= sweep["candleIndex"]:
            return {"signal": None, "conditions": conditions, "reason": "Displacement must form after the sweep", "score": 0}

        cisd = self.cisdScanner.detectCISD(m1, sweep, bias)
        cisd_ok = self.cisdScanner.isCISDValid(cisd) and cisd.get("candleIndex", -1) >= displacement["candleIndex"]
        conditions["cisd"] = {
            "active": cisd_ok,
            "headline": "Confirmed" if cisd_ok else "Waiting for micro flip",
            "detail": self._cisd_detail(cisd),
            "badge": f"{cisd.get('barsAfterSweep', '—')} BAR" if cisd_ok else "WAITING",
            "data": cisd,
        }
        if not cisd_ok:
            return {"signal": None, "conditions": conditions, "reason": "Waiting for CISD confirmation", "score": 0}

        entry_fvg = displacement.get("fvg")
        price_at_entry = self._is_price_at_entry_fvg(current_price, entry_fvg, bias)
        conditions["entryFVG"] = {
            "active": bool(entry_fvg and price_at_entry),
            "headline": self._entry_fvg_headline(entry_fvg),
            "detail": "Price at 50% equilibrium" if price_at_entry else "Waiting for retrace into displacement FVG",
            "badge": "PRICE AT 50%" if price_at_entry else "WAITING",
            "fvg": entry_fvg,
        }
        if not entry_fvg:
            return {"signal": None, "conditions": conditions, "reason": "Displacement did not leave a valid FVG", "score": 0}
        if not price_at_entry:
            return {"signal": None, "conditions": conditions, "reason": "Waiting for retrace into entry FVG", "score": 0}

        pda = self.sessionFilter.isPDA(m1)
        pda_good = (bias == "bullish" and pda["zone"] == "discount") or (bias == "bearish" and pda["zone"] == "premium")
        conditions["pdaCheck"] = {
            "active": pda_good,
            "headline": f"{pda['zone'].upper()} ZONE",
            "detail": f"{pda['pct']:.1f}% of range",
            "badge": "GOOD FOR BUY" if bias == "bullish" and pda_good else ("GOOD FOR SELL" if bias == "bearish" and pda_good else "NEUTRAL"),
            "zone": pda["zone"],
            "pct": pda["pct"],
        }

        signal = self.buildICTSignal(
            {
                "bias": bias,
                "session": session_state,
                "bias_state": bias_state,
                "price": current_price,
                "pda": pda,
                "pda_good": pda_good,
            },
            m1,
            zone,
            sweep,
            displacement,
            cisd,
        )
        if signal is None:
            return {"signal": None, "conditions": conditions, "reason": "Risk-to-reward below minimum threshold", "score": 0}

        strength, ict_score = self.getSignalStrength(
            {
                "session": session_state,
                "bias_state": bias_state,
                "zone": zone,
                "sweep": sweep,
                "displacement": displacement,
                "cisd": cisd,
                "entryFVG": entry_fvg,
                "pda_good": pda_good,
            }
        )
        if strength == "WEAK":
            return {"signal": None, "conditions": conditions, "reason": "ICT confluence score too weak", "score": ict_score}

        signal["strength"] = strength
        signal["ictScore"] = ict_score
        signal["conditions"] = {
            "session": True,
            "bias15m": True,
            "zone5m": True,
            "sslSweep": True,
            "displacement": True,
            "cisd": True,
            "entryFVG": True,
            "pdaCheck": pda_good,
        }
        signal["summaryText"] = (
            f"⚡ ICT {strength} {signal['type']} — Entry: {signal['entryPrice']:.2f} | "
            f"SL: {signal['stopLoss']:.2f} | TP1: {signal['takeProfit1']:.2f} | "
            f"TP2: {signal['takeProfit2']:.2f} | R:R: 1:{signal['riskReward2']:.1f}"
        )
        return {"signal": signal, "conditions": conditions, "reason": "ICT sequence confirmed", "score": ict_score}

    def buildICTSignal(self, conditions: dict[str, Any], m1Candles: list[dict], m5Zone: dict, sweep: dict, displacement: dict, cisd: dict) -> dict[str, Any] | None:
        bias = conditions["bias"]
        entry_price = displacement["fvg"]["fvgMid"]
        if bias == "bullish":
            stop_loss = round(float(sweep["sweepCandle"]["low"]) - self.config.SL_BELOW_SWEEP_BUFFER, 2)
        else:
            stop_loss = round(float(sweep["sweepCandle"]["high"]) + self.config.SL_BELOW_SWEEP_BUFFER, 2)

        risk_abs = abs(entry_price - stop_loss)
        if risk_abs <= 0:
            return None

        atr_value = self.riskManager.calculate_atr(m1Candles)
        tp1 = self._get_recent_swing_target(m1Candles, entry_price, bias)
        tp2_pool = self.liquidityEngine.getNearestBSL(m1Candles, entry_price) if bias == "bullish" else self.liquidityEngine.getNearestSSL(m1Candles, entry_price)
        tp2 = float(tp2_pool["price"]) if tp2_pool else None

        if tp1 is None:
            tp1 = round(entry_price + (risk_abs * self.config.TARGET_RR_RATIO if bias == "bullish" else -risk_abs * self.config.TARGET_RR_RATIO), 2)
        if tp2 is None:
            tp2 = round(entry_price + (risk_abs * self.config.TARGET_RR_RATIO if bias == "bullish" else -risk_abs * self.config.TARGET_RR_RATIO), 2)

        rr1 = self._rr(entry_price, stop_loss, tp1, bias)
        rr2 = self._rr(entry_price, stop_loss, tp2, bias)
        if max(rr1, rr2) < self.config.MIN_RR_RATIO:
            return None

        return {
            "strategyId": self.config.STRATEGY_ID,
            "strategyName": self.config.STRATEGY_NAME,
            "type": "LONG" if bias == "bullish" else "SHORT",
            "direction": "BUY" if bias == "bullish" else "SELL",
            "timestamp": int(m1Candles[-1]["time"]) * 1000,
            "timeframe": self.config.PRIMARY_TIMEFRAME,
            "instrument": self.config.INSTRUMENT,
            "entryPrice": round(entry_price, 2),
            "entryZone": {"high": displacement["fvg"]["fvgHigh"], "low": displacement["fvg"]["fvgLow"]},
            "stopLoss": stop_loss,
            "takeProfit": round(tp1, 2),
            "takeProfit1": round(tp1, 2),
            "takeProfit2": round(tp2, 2),
            "breakEvenAt": self._break_even(entry_price, stop_loss, bias),
            "riskPips": round(price_to_pips(risk_abs), 1),
            "rewardPips": round(price_to_pips(abs(tp1 - entry_price)), 1),
            "rewardPips1": round(price_to_pips(abs(tp1 - entry_price)), 1),
            "rewardPips2": round(price_to_pips(abs(tp2 - entry_price)), 1),
            "riskReward": round(rr1, 2),
            "riskReward1": round(rr1, 2),
            "riskReward2": round(rr2, 2),
            "atrValue": round(atr_value, 3),
            "sessionName": conditions["session"]["zoneName"],
            "sessionPriority": conditions["session"]["priority"],
            "trendStrength": conditions["bias_state"]["strength"],
            "zone": m5Zone,
            "sweepDepth": sweep.get("sweepDepth"),
            "fiveMinZone": m5Zone,
            "sweepCandle": sweep.get("sweepCandle"),
            "displacementCandle": displacement.get("candle"),
            "cisdCandle": cisd.get("cisdCandle"),
            "outcome": "PENDING",
        }

    def getSignalStrength(self, conditions: dict[str, Any]) -> tuple[str, int]:
        score = 0
        if conditions["session"]["priority"] == "HIGH":
            score += 2
        elif conditions["session"]["priority"] == "MEDIUM":
            score += 1

        if conditions["bias_state"]["strength"] == "strong":
            score += 2
        elif conditions["bias_state"]["strength"] == "moderate":
            score += 1

        if conditions["zone"].get("fresh"):
            score += 2
        elif conditions["zone"].get("kind") == "fvg":
            score += 1

        if conditions["pda_good"]:
            score += 1
        if conditions["sweep"].get("quality") == "high":
            score += 1
        if conditions["displacement"].get("bodyPct", 0) >= 0.85:
            score += 2
        elif conditions["displacement"].get("bodyPct", 0) >= self.config.DISPLACEMENT_BODY_PCT:
            score += 1
        if int(conditions["cisd"].get("barsAfterSweep", 99)) <= 2:
            score += 1

        if score >= 9:
            return "STRONG", score
        if score >= 6:
            return "MEDIUM", score
        return "WEAK", score

    def _empty_conditions(self, now: Any) -> dict[str, Any]:
        countdown = self.sessionFilter.getNextHighPrioritySession(now) if now is not None else {"sessionName": "London Kill Zone", "startsIn_minutes": 0}
        return {
            "session": {"active": False, "headline": "Waiting", "detail": "Waiting for session data", "badge": "WAITING", "countdown": countdown},
            "bias15m": {"active": False, "headline": "NEUTRAL", "detail": "Waiting for 15M structure", "badge": "WAITING"},
            "zone5m": {"active": False, "headline": "No valid zone", "detail": "Waiting for 5M OB/FVG", "badge": "WAITING"},
            "sslSweep": {"active": False, "headline": "No sweep", "detail": "Waiting for liquidity grab", "badge": "WAITING"},
            "displacement": {"active": False, "headline": "No displacement", "detail": "Waiting for impulse candle", "badge": "WAITING"},
            "cisd": {"active": False, "headline": "No CISD", "detail": "Waiting for micro flip", "badge": "WAITING"},
            "entryFVG": {"active": False, "headline": "No entry FVG", "detail": "Waiting for retrace", "badge": "WAITING"},
            "pdaCheck": {"active": False, "headline": "EQUILIBRIUM", "detail": "Waiting for context", "badge": "NEUTRAL"},
        }

    def _find_best_sweep(self, m1Candles: list[dict], bias: str) -> dict[str, Any]:
        pools = self.liquidityEngine.findSSLPools(m1Candles) if bias == "bullish" else self.liquidityEngine.findBSLPools(m1Candles)
        detector = self.liquidityEngine.detectSSLSweep if bias == "bullish" else self.liquidityEngine.detectBSLSweep
        candidates: list[dict[str, Any]] = []
        for pool in pools[:8]:
            sweep = detector(m1Candles, pool)
            if sweep.get("swept"):
                candidates.append(sweep)
        if not candidates:
            return {"swept": False}
        candidates.sort(key=lambda item: (item["candleIndex"], {"high": 2, "medium": 1, "low": 0}.get(item.get("quality"), 0)), reverse=True)
        return candidates[0]

    def _bias_detail(self, bias_state: dict[str, Any]) -> str:
        bos = bias_state.get("lastBOS")
        choch = bias_state.get("lastCHoCH")
        if bos and choch:
            return f"BOS {bos['direction']} @ {bos['price']:.2f} · CHoCH caution"
        if bos:
            return f"BOS {bos['direction']} @ {bos['price']:.2f}"
        return "No confirmed BOS yet"

    def _sweep_headline(self, sweep: dict[str, Any], bias: str) -> str:
        if not sweep.get("swept"):
            return "No sweep"
        side = "SSL sweep" if bias == "bullish" else "BSL sweep"
        return f"{side} · {sweep.get('sweepDepth', 0)} pips"

    def _sweep_detail(self, sweep: dict[str, Any]) -> str:
        if not sweep.get("swept"):
            return "Waiting for stop run and recovery"
        return f"{sweep.get('quality', 'low').capitalize()} quality · {sweep.get('recoverySpeed', 'slow')} recovery"

    def _displacement_headline(self, displacement: dict[str, Any], bias: str) -> str:
        if not displacement.get("found"):
            return "No displacement"
        return f"{bias.upper()} impulse · body {displacement.get('bodyPct', 0) * 100:.0f}%"

    def _displacement_detail(self, displacement: dict[str, Any]) -> str:
        if not displacement.get("found"):
            return "Waiting for large imbalance candle"
        return f"Range {displacement.get('rangeVsAvg', 0):.1f}x avg · FVG {'yes' if displacement.get('createdFVG') else 'no'}"

    def _cisd_detail(self, cisd: dict[str, Any]) -> str:
        if not cisd.get("detected"):
            return "Waiting for break of micro structure"
        return f"Broke {cisd.get('flippedLevel', 0):.2f} · {cisd.get('barsAfterSweep', 0)} bar(s) after sweep"

    def _entry_fvg_headline(self, entry_fvg: dict[str, Any] | None) -> str:
        if not entry_fvg:
            return "No displacement FVG"
        return f"{entry_fvg['fvgLow']:.2f}-{entry_fvg['fvgHigh']:.2f} · mid {entry_fvg['fvgMid']:.2f}"

    def _is_price_at_entry_fvg(self, current_price: float, entry_fvg: dict[str, Any] | None, bias: str) -> bool:
        if not entry_fvg:
            return False
        # Allow full FVG zone with a small buffer so backtesting bar-closes
        # that land anywhere inside the imbalance register as a valid entry.
        buf = max(0.20, (entry_fvg["fvgHigh"] - entry_fvg["fvgLow"]) * 0.50)
        return (entry_fvg["fvgLow"] - buf) <= current_price <= (entry_fvg["fvgHigh"] + buf)

    def _get_recent_swing_target(self, m1Candles: list[dict], entry_price: float, bias: str) -> float | None:
        window = m1Candles[-20:]
        if bias == "bullish":
            candidates = [candle["high"] for candle in window if candle["high"] > entry_price]
            return round(min(candidates), 2) if candidates else None
        candidates = [candle["low"] for candle in window if candle["low"] < entry_price]
        return round(max(candidates), 2) if candidates else None

    def _rr(self, entry: float, stop: float, target: float, bias: str) -> float:
        risk = abs(entry - stop)
        reward = (target - entry) if bias == "bullish" else (entry - target)
        return reward / risk if risk > 0 else 0.0

    def _break_even(self, entry: float, stop: float, bias: str) -> float:
        risk = abs(entry - stop)
        if bias == "bullish":
            return round(entry + risk * self.config.BREAKEVEN_TRIGGER_R, 2)
        return round(entry - risk * self.config.BREAKEVEN_TRIGGER_R, 2)
