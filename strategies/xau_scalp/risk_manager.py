"""
RiskManager — Step 7: SL / TP / Break-Even Calculator
Uses Wilder's ATR for dynamic stop placement on XAU/USD M1.
"""
from typing import Dict, List, Optional


class RiskManager:
    def __init__(self, cfg):
        self.cfg = cfg

    # ── Public API ────────────────────────────────────────────────────────

    def calculate_atr(self, candles: List[Dict]) -> float:
        """Wilder's RMA-smoothed ATR over cfg.ATR_PERIOD bars."""
        period = self.cfg.ATR_PERIOD
        if len(candles) < period + 1:
            return 0.0

        trs = []
        for i in range(1, len(candles)):
            h, lo, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
            trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))

        if not trs:
            return 0.0

        # Seed with simple average, then Wilder's smoothing
        rma = sum(trs[:period]) / period
        for tr in trs[period:]:
            rma = (rma * (period - 1) + tr) / period

        return round(rma, 3)

    def calculate_stop_loss(self, entry: float, atr: float,
                            zone: Optional[Dict] = None) -> float:
        atr_sl   = entry - atr * self.cfg.ATR_SL_MULTIPLIER
        if zone:
            zone_sl  = zone["zoneLow"] - self.cfg.ZONE_BUFFER_PIPS * 0.10
            # Take the more conservative (lower) stop
            return round(min(atr_sl, zone_sl), 2)
        return round(atr_sl, 2)

    def calculate_take_profit(self, candles: List[Dict],
                              entry: float, stop_loss: float) -> Optional[float]:
        risk = entry - stop_loss
        if risk <= 0:
            return None

        # Primary: most recent 20-bar swing high
        recent_high = max(c["high"] for c in candles[-20:])
        rr_swing    = (recent_high - entry) / risk

        if rr_swing >= self.cfg.MIN_RR_RATIO:
            return round(recent_high, 2)

        # Synthesise TP at TARGET_RR_RATIO if swing high is too close
        synthetic = entry + risk * self.cfg.TARGET_RR_RATIO
        if (synthetic - entry) / risk >= self.cfg.MIN_RR_RATIO:
            return round(synthetic, 2)

        return None  # No viable TP → skip trade

    def calculate_break_even(self, entry: float, stop_loss: float) -> float:
        risk = entry - stop_loss
        return round(entry + risk * self.cfg.BREAKEVEN_TRIGGER_R, 2)

    def calculate_position_size(self, account_balance: float,
                                risk_pct: float,
                                entry: float,
                                stop_loss: float) -> float:
        risk_amount = account_balance * risk_pct / 100
        risk_price  = entry - stop_loss
        if risk_price <= 0:
            return 0.01
        # 1 lot XAU/USD ≈ 100 oz; pip value ≈ $1 per 0.1-price per lot
        lot_size = risk_amount / (risk_price * 100)
        return round(min(lot_size, 0.10), 2)  # safety cap

    def get_risk_report(self, entry: float, stop_loss: float,
                        take_profit: float, atr: float,
                        qty: float = 0.01) -> Dict:
        risk   = entry - stop_loss
        reward = take_profit - entry
        rr     = reward / risk if risk > 0 else 0
        be     = self.calculate_break_even(entry, stop_loss)
        return {
            "entry":         round(entry,      2),
            "sl":            round(stop_loss,  2),
            "tp":            round(take_profit, 2),
            "be":            round(be,          2),
            "rr":            round(rr,          2),
            "pips_risk":     round(risk   / 0.10, 1),
            "pips_reward":   round(reward / 0.10, 1),
            "atr":           atr,
            "position_size": qty,
        }
