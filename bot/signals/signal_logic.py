from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from bot.debug.decision_logger import DecisionLogger, get_decision_logger
from bot.signals.scoring_engine import SignalScoringEngine


@dataclass
class TradingSignal:
    pair: str
    direction: str
    score: int
    score_threshold: int
    regime: str
    session: str
    timeframe: str
    timestamp_utc: datetime
    reason: str
    breakdown: dict[str, int]
    price: float
    atr: float


@dataclass
class SignalEvaluation:
    pair: str
    timeframe: str
    timestamp_utc: datetime
    price: float
    direction: str
    score: int
    score_threshold: int
    regime: str
    session: str
    breakdown: dict[str, int]
    call_score: int
    put_score: int
    atr_ratio: float
    atr: float
    signal_generated: bool
    reason: str
    rejected_checks: list[str]
    signal: TradingSignal | None = None


@dataclass
class SignalGenerator:
    """
    Convert scoring output into a transparent signal evaluation.
    """

    score_threshold: int = 70
    scorer: SignalScoringEngine = field(default_factory=SignalScoringEngine)
    decision_logger: DecisionLogger = field(default_factory=get_decision_logger)
    threshold_provider: Any | None = None

    def evaluate(
        self,
        *,
        pair: str,
        timeframe: str,
        df: pd.DataFrame,
        liquidity_map: dict[str, Any],
        regime: dict[str, Any],
        session: dict[str, Any],
    ) -> SignalEvaluation:
        last = df.iloc[-1]
        evaluated_at = datetime.now(timezone.utc)
        dynamic_threshold = self._resolve_threshold()
        price = float(last["close"])

        try:
            score_payload = self.scorer.score(
                df=df,
                liquidity_map=liquidity_map,
                regime=regime,
                session=session,
            )
        except Exception as exc:
            self.decision_logger.log_decision(
                {
                    "timestamp": evaluated_at,
                    "price": price,
                    "session": str(session.get("session", "ASIAN")),
                    "trend_alignment": None,
                    "liquidity_sweep": None,
                    "atr_expansion": None,
                    "regime": str(regime.get("regime", "UNKNOWN")),
                    "market_regime": str(regime.get("regime", "UNKNOWN")),
                    "score": 0,
                    "signal_score": 0,
                    "score_threshold": int(dynamic_threshold),
                    "signal_generated": False,
                    "reason": f"scoring_error:{exc}",
                    "pair": pair,
                    "timeframe": timeframe,
                }
            )
            raise

        score = int(score_payload["score"])
        direction = str(score_payload["direction"])
        breakdown = dict(score_payload["breakdown"])
        signal_generated = score >= dynamic_threshold
        rejected_checks = [name for name, points in breakdown.items() if int(points) <= 0]
        reason = "score_threshold_met"
        if not signal_generated:
            reason = "score_below_threshold"
            if rejected_checks:
                reason = f"{reason}:{','.join(rejected_checks)}"

        signal: TradingSignal | None = None
        if signal_generated:
            signal = TradingSignal(
                pair=pair,
                direction=direction,
                score=score,
                score_threshold=int(dynamic_threshold),
                regime=str(regime.get("regime", "UNKNOWN")),
                session=str(session.get("session", "ASIAN")),
                timeframe=timeframe,
                timestamp_utc=evaluated_at,
                reason=f"score={score} breakdown={breakdown}",
                breakdown=breakdown,
                price=price,
                atr=float(score_payload.get("atr", 0.0)),
            )

        evaluation = SignalEvaluation(
            pair=pair,
            timeframe=timeframe,
            timestamp_utc=evaluated_at,
            price=price,
            direction=direction,
            score=score,
            score_threshold=int(dynamic_threshold),
            regime=str(regime.get("regime", "UNKNOWN")),
            session=str(session.get("session", "ASIAN")),
            breakdown=breakdown,
            call_score=int(score_payload.get("call_score", 0)),
            put_score=int(score_payload.get("put_score", 0)),
            atr_ratio=float(score_payload.get("atr_ratio", 0.0)),
            atr=float(score_payload.get("atr", 0.0)),
            signal_generated=signal_generated,
            reason=reason,
            rejected_checks=rejected_checks if not signal_generated else [],
            signal=signal,
        )
        self._log_evaluation(evaluation=evaluation)
        return evaluation

    def generate(
        self,
        *,
        pair: str,
        timeframe: str,
        df: pd.DataFrame,
        liquidity_map: dict[str, Any],
        regime: dict[str, Any],
        session: dict[str, Any],
    ) -> SignalEvaluation:
        return self.evaluate(
            pair=pair,
            timeframe=timeframe,
            df=df,
            liquidity_map=liquidity_map,
            regime=regime,
            session=session,
        )

    def _log_evaluation(self, *, evaluation: SignalEvaluation) -> None:
        self.decision_logger.log_decision(
            {
                "timestamp": evaluation.timestamp_utc,
                "price": evaluation.price,
                "session": evaluation.session,
                "trend_alignment": int(evaluation.breakdown.get("trend_alignment", 0)) > 0,
                "liquidity_sweep": int(evaluation.breakdown.get("liquidity_sweep", 0)) > 0,
                "atr_expansion": int(evaluation.breakdown.get("atr_expansion", 0)) > 0,
                "regime": evaluation.regime,
                "market_regime": evaluation.regime,
                "score": evaluation.score,
                "signal_score": evaluation.score,
                "score_threshold": evaluation.score_threshold,
                "signal_generated": evaluation.signal_generated,
                "reason": evaluation.reason,
                "pair": evaluation.pair,
                "timeframe": evaluation.timeframe,
                "direction": evaluation.direction,
                "call_score": evaluation.call_score,
                "put_score": evaluation.put_score,
                "atr_ratio": evaluation.atr_ratio,
                "breakdown": evaluation.breakdown,
                "rejected_checks": evaluation.rejected_checks,
            }
        )

    def _resolve_threshold(self) -> int:
        provider = self.threshold_provider
        if provider is None:
            return int(self.score_threshold)
        get_threshold = getattr(provider, "get_threshold", None)
        if callable(get_threshold):
            try:
                return int(get_threshold())
            except Exception:
                return int(self.score_threshold)
        return int(self.score_threshold)
