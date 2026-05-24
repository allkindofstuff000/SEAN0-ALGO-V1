"""
MongoDB store for SEAN0-ALGO
Saves backtest reports, live trade signals, and bot events.
Collection: backtest_reports  (capped at 50 documents)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

LOGGER = logging.getLogger(__name__)

_client = None
_db     = None

MONGO_URI = os.getenv("MONGODB_URI", "")
MONGO_DB  = os.getenv("MONGODB_DB", "sean0_algo")

MAX_REPORTS = 50   # keep only the last 50 backtest reports


def _get_db():
    """Lazy singleton — connect once, reuse forever."""
    global _client, _db
    if _db is not None:
        return _db
    if not MONGO_URI:
        LOGGER.warning("[MONGO] MONGODB_URI not set — persistence disabled")
        return None
    try:
        import pymongo
        _client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=6000)
        _client.admin.command("ping")          # fast connection check
        _db = _client[MONGO_DB]
        LOGGER.info("[MONGO] connected  db=%s", MONGO_DB)
        return _db
    except Exception as exc:
        LOGGER.error("[MONGO] connection failed: %s", exc)
        _db = None
        return None


# ── Backtest reports ──────────────────────────────────────────────────────────
def save_backtest_report(
    *,
    metrics:      dict[str, Any],
    trades:       list[dict[str, Any]],
    equity_curve: list[dict[str, Any]],
    params:       dict[str, Any] | None = None,
) -> str | None:
    """
    Insert one backtest report and prune oldest so only 50 remain.
    Returns the inserted document _id (str) or None on failure.
    """
    db = _get_db()
    if db is None:
        return None

    col = db["backtest_reports"]
    doc = {
        "saved_at":    datetime.now(timezone.utc).isoformat(),
        "params":      params or {},
        "metrics":     metrics,
        "trade_count": len(trades),
        "trades":      trades,
        "equity_curve": equity_curve,
    }

    try:
        result = col.insert_one(doc)
        inserted_id = str(result.inserted_id)
        LOGGER.info("[MONGO] backtest saved  id=%s  trades=%s", inserted_id, len(trades))

        # ── Prune: keep only newest MAX_REPORTS ────────────────────────────
        total = col.count_documents({})
        if total > MAX_REPORTS:
            # Find the _id of the (total - MAX_REPORTS)-th oldest document
            oldest_cursor = (
                col.find({}, {"_id": 1})
                   .sort("saved_at", 1)
                   .limit(total - MAX_REPORTS)
            )
            ids_to_delete = [d["_id"] for d in oldest_cursor]
            if ids_to_delete:
                deleted = col.delete_many({"_id": {"$in": ids_to_delete}})
                LOGGER.info("[MONGO] pruned %s old reports", deleted.deleted_count)

        return inserted_id

    except Exception as exc:
        LOGGER.error("[MONGO] save_backtest_report failed: %s", exc)
        return None


def load_backtest_history(limit: int = 50) -> list[dict[str, Any]]:
    """
    Return last *limit* backtest reports, newest first.
    Each doc has: saved_at, params, metrics, trade_count (no full trades list).
    """
    db = _get_db()
    if db is None:
        return []
    try:
        col   = db["backtest_reports"]
        cursor = (
            col.find({}, {"trades": 0, "equity_curve": 0})   # exclude heavy fields
               .sort("saved_at", -1)
               .limit(limit)
        )
        docs = []
        for d in cursor:
            d["_id"] = str(d["_id"])
            docs.append(d)
        return docs
    except Exception as exc:
        LOGGER.error("[MONGO] load_backtest_history failed: %s", exc)
        return []


def load_backtest_report(report_id: str) -> dict[str, Any] | None:
    """Load a single full backtest report (including trades) by _id."""
    db = _get_db()
    if db is None:
        return None
    try:
        from bson import ObjectId
        col = db["backtest_reports"]
        doc = col.find_one({"_id": ObjectId(report_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc
    except Exception as exc:
        LOGGER.error("[MONGO] load_backtest_report failed: %s", exc)
        return None


# ── Live signals ──────────────────────────────────────────────────────────────
MAX_SIGNALS = 500   # keep only the last 500 live signals

def save_live_signal(
    *,
    symbol:          str,
    direction:       str,
    entry_price:     float,
    stop_loss:       float | None,
    take_profit:     float | None,
    atr:             float,
    score:           int,
    score_threshold: int,
    session:         str,
    market_regime:   str,
    regime_confidence: float,
    trend_alignment: bool,
    price_trigger:   bool,
    rsi_filter:      bool,
    atr_expansion:   bool,
    reason:          str,
    signal_kind:     str = "forex",
    telegram_sent:   bool = False,
    candle_time_utc: str | None = None,
) -> str | None:
    """
    Insert one live signal into 'live_signals' collection.
    Returns inserted _id (str) or None on failure.
    Keeps only the last MAX_SIGNALS documents.
    """
    db = _get_db()
    if db is None:
        return None
    try:
        col = db["live_signals"]
        doc = {
            "sent_at":           datetime.now(timezone.utc).isoformat(),
            "candle_time_utc":   candle_time_utc,
            "symbol":            symbol,
            "direction":         direction,
            "entry_price":       entry_price,
            "stop_loss":         stop_loss,
            "take_profit":       take_profit,
            "atr":               atr,
            "score":             score,
            "score_threshold":   score_threshold,
            "session":           session,
            "market_regime":     market_regime,
            "regime_confidence": round(regime_confidence, 4),
            "trend_alignment":   trend_alignment,
            "price_trigger":     price_trigger,
            "rsi_filter":        rsi_filter,
            "atr_expansion":     atr_expansion,
            "reason":            reason,
            "signal_kind":       signal_kind,
            "telegram_sent":     telegram_sent,
            # outcome filled in later via update_signal_outcome()
            "outcome":           None,   # "WIN" | "LOSS" | "BREAKEVEN" | None
            "exit_price":        None,
            "outcome_note":      None,
        }
        result = col.insert_one(doc)
        inserted_id = str(result.inserted_id)
        LOGGER.info("[MONGO] live_signal saved id=%s dir=%s entry=%.2f", inserted_id, direction, entry_price)

        # prune oldest beyond MAX_SIGNALS
        total = col.count_documents({})
        if total > MAX_SIGNALS:
            oldest = (
                col.find({}, {"_id": 1})
                   .sort("sent_at", 1)
                   .limit(total - MAX_SIGNALS)
            )
            ids = [d["_id"] for d in oldest]
            if ids:
                col.delete_many({"_id": {"$in": ids}})

        return inserted_id
    except Exception as exc:
        LOGGER.error("[MONGO] save_live_signal failed: %s", exc)
        return None


def load_live_signals(limit: int = 100) -> list[dict[str, Any]]:
    """Return last *limit* live signals, newest first."""
    db = _get_db()
    if db is None:
        return []
    try:
        col = db["live_signals"]
        cursor = col.find({}).sort("sent_at", -1).limit(limit)
        docs = []
        for d in cursor:
            d["_id"] = str(d["_id"])
            docs.append(d)
        return docs
    except Exception as exc:
        LOGGER.error("[MONGO] load_live_signals failed: %s", exc)
        return []


def update_signal_outcome(
    signal_id: str,
    outcome: str,
    exit_price: float | None = None,
    note: str | None = None,
) -> bool:
    """
    Mark a signal WIN / LOSS / BREAKEVEN.
    outcome: 'WIN' | 'LOSS' | 'BREAKEVEN'
    Returns True on success.
    """
    db = _get_db()
    if db is None:
        return False
    try:
        from bson import ObjectId
        col = db["live_signals"]
        col.update_one(
            {"_id": ObjectId(signal_id)},
            {"$set": {
                "outcome":      outcome.upper(),
                "exit_price":   exit_price,
                "outcome_note": note,
                "marked_at":    datetime.now(timezone.utc).isoformat(),
            }},
        )
        LOGGER.info("[MONGO] signal %s marked %s", signal_id, outcome)
        return True
    except Exception as exc:
        LOGGER.error("[MONGO] update_signal_outcome failed: %s", exc)
        return False


# ── Bot state ─────────────────────────────────────────────────────────────────
_BOT_STATE_DOC_ID = "singleton"

def save_bot_state(intent: str) -> bool:
    """
    Persist bot on/off intent so web server restarts can auto-resume.
    intent: 'running' | 'stopped'
    Returns True on success.
    """
    db = _get_db()
    if db is None:
        return False
    try:
        col = db["bot_state"]
        col.update_one(
            {"_id": _BOT_STATE_DOC_ID},
            {"$set": {
                "_id":        _BOT_STATE_DOC_ID,
                "intent":     intent,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        LOGGER.info("[MONGO] bot_state saved  intent=%s", intent)
        return True
    except Exception as exc:
        LOGGER.error("[MONGO] save_bot_state failed: %s", exc)
        return False


def load_bot_state() -> str | None:
    """
    Return the last persisted bot intent ('running' | 'stopped') or None if unknown.
    """
    db = _get_db()
    if db is None:
        return None
    try:
        col = db["bot_state"]
        doc = col.find_one({"_id": _BOT_STATE_DOC_ID})
        return doc["intent"] if doc else None
    except Exception as exc:
        LOGGER.error("[MONGO] load_bot_state failed: %s", exc)
        return None
