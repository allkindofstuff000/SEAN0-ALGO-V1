from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _trade_sharpe(r_values: pd.Series) -> float:
    cleaned = pd.to_numeric(r_values, errors="coerce").dropna()
    if cleaned.empty:
        return 0.0
    std_dev = float(cleaned.std(ddof=0))
    if std_dev == 0.0:
        return 0.0
    return float((cleaned.mean() / std_dev) * math.sqrt(len(cleaned)))


def _risk_adjusted_return(profit_factor: float, avg_r: float, max_drawdown: float) -> float:
    drawdown_penalty = abs(float(max_drawdown)) if float(max_drawdown) != 0 else 1.0
    profit_factor_score = float(profit_factor) if math.isfinite(float(profit_factor)) else 5.0
    return (profit_factor_score * max(float(avg_r), -2.0)) / drawdown_penalty


def _detect_weaknesses(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    trades = result.get("trades_frame", pd.DataFrame())
    if trades.empty:
        return {
            "low_volatility_periods": {"flagged": False, "severity": "low", "evidence": "No trades generated."},
            "trend_reversals": {"flagged": False, "severity": "low", "evidence": "No trades generated."},
            "range_markets": {"flagged": False, "severity": "low", "evidence": "No trades generated."},
        }

    losses = trades[pd.to_numeric(trades.get("pnl"), errors="coerce").fillna(0.0) < 0]
    total_losses = max(1, len(losses))
    exit_reasons = losses["exit_reason"].fillna("unknown") if "exit_reason" in losses.columns else pd.Series(dtype="object")
    median_atr = float(pd.to_numeric(trades.get("atr"), errors="coerce").dropna().median()) if "atr" in trades.columns else 0.0

    low_vol_losses = 0
    if "atr" in losses.columns and median_atr > 0:
        low_vol_losses = int((pd.to_numeric(losses["atr"], errors="coerce").fillna(0.0) < (median_atr * 0.85)).sum())
    stop_loss_losses = int((exit_reasons == "stop_loss_hit").sum())
    max_hold_losses = int(exit_reasons.astype(str).str.startswith("max_hold").sum())

    low_vol_ratio = low_vol_losses / total_losses
    stop_loss_ratio = stop_loss_losses / total_losses
    max_hold_ratio = max_hold_losses / total_losses

    return {
        "low_volatility_periods": {
            "flagged": low_vol_ratio >= 0.3,
            "severity": "high" if low_vol_ratio >= 0.5 else "medium" if low_vol_ratio >= 0.3 else "low",
            "evidence": f"{low_vol_losses}/{total_losses} losing trades printed below 85% of median ATR ({median_atr:.2f}).",
        },
        "trend_reversals": {
            "flagged": stop_loss_ratio >= 0.35,
            "severity": "high" if stop_loss_ratio >= 0.55 else "medium" if stop_loss_ratio >= 0.35 else "low",
            "evidence": f"{stop_loss_losses}/{total_losses} losing trades exited via stop loss after breakout entry.",
        },
        "range_markets": {
            "flagged": max_hold_ratio >= 0.3,
            "severity": "high" if max_hold_ratio >= 0.5 else "medium" if max_hold_ratio >= 0.3 else "low",
            "evidence": f"{max_hold_losses}/{total_losses} losing trades stalled until max-hold exit, suggesting chop/range conditions.",
        },
    }


def _aggregate_weaknesses(variant_analyses: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    categories = ("low_volatility_periods", "trend_reversals", "range_markets")
    aggregate: dict[str, dict[str, Any]] = {}

    for category in categories:
        flagged = [item for item in variant_analyses if item["weaknesses"][category]["flagged"]]
        aggregate[category] = {
            "flagged_variants": len(flagged),
            "total_variants": len(variant_analyses),
            "severity": (
                "high"
                if len(flagged) >= max(1, math.ceil(len(variant_analyses) * 0.5))
                else "medium"
                if len(flagged) >= max(1, math.ceil(len(variant_analyses) * 0.25))
                else "low"
            ),
            "sample_evidence": [item["weaknesses"][category]["evidence"] for item in flagged[:3]],
        }
    return aggregate


def _build_improvement_suggestions(aggregate_weaknesses: dict[str, dict[str, Any]]) -> list[str]:
    suggestions: list[str] = []

    if aggregate_weaknesses["low_volatility_periods"]["flagged_variants"] > 0:
        suggestions.append(
            "Tighten the ATR expansion gate or skip sessions where ATR stays below the rolling average for extended periods."
        )
    if aggregate_weaknesses["trend_reversals"]["flagged_variants"] > 0:
        suggestions.append(
            "Increase breakout confirmation strength or require a stronger RSI bias to reduce reversal stop-outs."
        )
    if aggregate_weaknesses["range_markets"]["flagged_variants"] > 0:
        suggestions.append(
            "Add a range filter or shorter max-hold rule to avoid choppy breakouts that never follow through."
        )
    if not suggestions:
        suggestions.append("The tested variants were relatively stable; iterate on finer parameter steps rather than new hard filters.")
    return suggestions


def analyze_variant_performance(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate advanced metrics and diagnose recurring weaknesses across variants."""

    variant_analyses: list[dict[str, Any]] = []
    for result in results:
        sharpe_ratio = round(_trade_sharpe(result.get("r_multiple_series", pd.Series(dtype="float64"))), 4)
        risk_adjusted_return = round(
            _risk_adjusted_return(
                profit_factor=float(result["profit_factor"]),
                avg_r=float(result["avg_R"]),
                max_drawdown=float(result["max_drawdown"]),
            ),
            4,
        )
        weaknesses = _detect_weaknesses(result)

        variant_analyses.append(
            {
                **result,
                "sharpe_ratio": sharpe_ratio,
                "risk_adjusted_return": risk_adjusted_return,
                "weaknesses": weaknesses,
            }
        )

    aggregate_weaknesses = _aggregate_weaknesses(variant_analyses)
    return {
        "variants": variant_analyses,
        "weakness_summary": aggregate_weaknesses,
        "improvement_suggestions": _build_improvement_suggestions(aggregate_weaknesses),
    }
