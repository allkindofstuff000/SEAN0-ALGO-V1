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
_last_connect_fail = 0.0   # monotonic time of the last failed connect (negative cache)
MONGO_RETRY_AFTER_S = 30.0  # while Mongo is down, retry a connect at most every 30 s

MONGO_URI = os.getenv("MONGODB_URI", "")
MONGO_DB  = os.getenv("MONGODB_DB", "sean0_algo")

MAX_REPORTS = 150  # keep only the last 150 backtest reports (at 50, new crypto/nyorb runs evicted the gold history)


def _get_db():
    """Lazy singleton — connect once, reuse forever."""
    global _client, _db
    if _db is not None:
        return _db
    if not MONGO_URI:
        LOGGER.warning("[MONGO] MONGODB_URI not set — persistence disabled")
        return None
    import time as _time
    global _last_connect_fail
    if _last_connect_fail and _time.monotonic() - _last_connect_fail < MONGO_RETRY_AFTER_S:
        return None   # recently failed: callers degrade immediately instead of blocking 6 s each
    try:
        import pymongo
        _client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=6000)
        _client.admin.command("ping")          # fast connection check
        _db = _client[MONGO_DB]
        LOGGER.info("[MONGO] connected  db=%s", MONGO_DB)
        _ensure_indexes(_db)
        return _db
    except Exception as exc:
        LOGGER.error("[MONGO] connection failed: %s (next attempt in %.0fs)", exc, MONGO_RETRY_AFTER_S)
        _db = None
        _last_connect_fail = _time.monotonic()
        return None


def _ensure_indexes(db) -> None:
    """Idempotent. live_signals had only _id: the resolver's {outcome:null} + sort
    sent_at was a COLLSCAN+SORT every 2 min and /signals sorted the whole collection."""
    try:
        col = db["live_signals"]
        col.create_index([("outcome", 1), ("sent_at", -1)], name="outcome_sent_at")
        col.create_index([("sent_at", -1)], name="sent_at_desc")
    except Exception as exc:  # noqa: BLE001 — never block startup on an index
        LOGGER.warning("[MONGO] ensure_indexes failed: %s", exc)


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
    score:           int | None = None,
    score_threshold: int | None = None,
    session:         str = "",
    market_regime:   str = "",
    regime_confidence: float | None = None,
    trend_alignment: bool | None = None,
    price_trigger:   bool | None = None,
    rsi_filter:      bool | None = None,
    atr_expansion:   bool | None = None,
    reason:          str = "",
    signal_kind:     str = "forex",
    telegram_sent:   bool = False,
    candle_time_utc: str | None = None,
    sent_at:         str | None = None,
    **extra,
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
            # a replay from the retry queue passes the ORIGINAL sent_at so history
            # keeps its true order (a queued signal is not "sent" when Mongo recovers)
            "sent_at":           sent_at or datetime.now(timezone.utc).isoformat(),
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
            "regime_confidence": round(regime_confidence, 4) if regime_confidence is not None else None,
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
        # forward-compatible per-strategy fields (e.g. strategy, strategyName,
        # timestamp). Stored without clobbering the core keys above so a new
        # strategy can tag its signals without changing this signature.
        for _k, _v in extra.items():
            doc.setdefault(_k, _v)
        result = col.insert_one(doc)
        inserted_id = str(result.inserted_id)
        LOGGER.info("[MONGO] live_signal saved id=%s dir=%s entry=%.2f", inserted_id, direction, entry_price)

        # prune oldest beyond MAX_SIGNALS — best effort: the signal IS saved at this
        # point, so a prune hiccup must not report a failure (the caller would
        # re-queue the signal and insert it twice).
        try:
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
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("[MONGO] live_signals prune skipped: %s", exc)

        return inserted_id
    except Exception as exc:
        LOGGER.error("[MONGO] save_live_signal failed: %s", exc)
        return None


def save_strategy_signal(signal_doc: dict[str, Any]) -> str | None:
    """Generic signal save — works for any strategy (XAU Scalp, etc.)."""
    db = _get_db()
    if db is None:
        return None
    try:
        col = db["live_signals"]
        doc = {
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "outcome": None,
            "exit_price": None,
            **signal_doc,
        }
        result = col.insert_one(doc)
        inserted_id = str(result.inserted_id)
        LOGGER.info("[MONGO] strategy_signal saved id=%s strategy=%s", inserted_id, signal_doc.get("strategy", "?"))

        total = col.count_documents({})
        if total > MAX_SIGNALS:
            oldest = col.find({}, {"_id": 1}).sort("sent_at", 1).limit(total - MAX_SIGNALS)
            ids = [d["_id"] for d in oldest]
            if ids:
                col.delete_many({"_id": {"$in": ids}})

        return inserted_id
    except Exception as exc:
        LOGGER.error("[MONGO] save_strategy_signal failed: %s", exc)
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


def load_open_signals(limit: int = 500) -> list[dict[str, Any]]:
    """Signals with no outcome yet (the resolver's work queue), newest first.
    Queried directly so an old open signal can never fall off the end of a
    size-limited 'latest N' list and sit unresolved forever."""
    db = _get_db()
    if db is None:
        return []
    try:
        col = db["live_signals"]
        cursor = col.find({"outcome": None}).sort("sent_at", -1).limit(limit)
        docs = []
        for d in cursor:
            d["_id"] = str(d["_id"])
            docs.append(d)
        return docs
    except Exception as exc:
        LOGGER.error("[MONGO] load_open_signals failed: %s", exc)
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


def save_strategy_state(strategy_id: str, intent: str) -> bool:
    """Persist per-strategy on/off intent + started_at timestamp."""
    db = _get_db()
    if db is None:
        return False
    try:
        col = db["bot_state"]
        update_doc = {
            "_id":        f"strategy:{strategy_id}",
            "strategy":   strategy_id,
            "intent":     intent,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Save started_at when starting, clear when stopping
        if intent == "running":
            update_doc["started_at"] = datetime.now(timezone.utc).isoformat()
        elif intent == "stopped":
            update_doc["started_at"] = None
        col.update_one(
            {"_id": f"strategy:{strategy_id}"},
            {"$set": update_doc},
            upsert=True,
        )
        LOGGER.info("[MONGO] strategy_state saved  %s=%s", strategy_id, intent)
        return True
    except Exception as exc:
        LOGGER.error("[MONGO] save_strategy_state failed: %s", exc)
        return False


def load_strategy_state(strategy_id: str) -> str | None:
    """Return per-strategy intent ('running' | 'stopped') or None."""
    db = _get_db()
    if db is None:
        return None
    try:
        col = db["bot_state"]
        doc = col.find_one({"_id": f"strategy:{strategy_id}"})
        return doc["intent"] if doc else None
    except Exception as exc:
        LOGGER.error("[MONGO] load_strategy_state failed: %s", exc)
        return None


def load_strategy_started_at(strategy_id: str) -> str | None:
    """Return per-strategy started_at ISO timestamp or None."""
    db = _get_db()
    if db is None:
        return None
    try:
        col = db["bot_state"]
        doc = col.find_one({"_id": f"strategy:{strategy_id}"})
        return doc.get("started_at") if doc else None
    except Exception as exc:
        LOGGER.error("[MONGO] load_strategy_started_at failed: %s", exc)
        return None

# ── Durable retry for failed signal saves ────────────────────────────────────
# A bot that has already SENT a signal to Telegram must never lose it from the
# track record because Mongo hiccupped at that instant. On failure the kwargs
# are appended to a local JSONL queue and retried every bot cycle.
import json as _json
import re as _re
import threading as _threading
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
_PENDING_TAG: str | None = None
# Legacy shared file name. Before 2026-10-09 all six bot processes appended to and
# rewrote this ONE file under a per-process threading.Lock — a cross-process race
# that could drop a queued signal or replay it twice when Mongo came back. Each
# bot now calls configure_pending_queue(<strategy tag>) and owns its own file.
PENDING_SIGNALS_PATH = _ROOT / "pending_signals.jsonl"
_PENDING_LOCK = _threading.Lock()


def _safe_tag(tag: str) -> str:
    return _re.sub(r"[^A-Za-z0-9_.-]+", "_", str(tag or "").strip()) or "shared"


def configure_pending_queue(tag: str) -> _Path:
    """Give this process its own retry-queue file (pending_signals_<tag>.jsonl)."""
    global _PENDING_TAG, PENDING_SIGNALS_PATH
    _PENDING_TAG = _safe_tag(tag)
    PENDING_SIGNALS_PATH = _ROOT / f"pending_signals_{_PENDING_TAG}.jsonl"
    return PENDING_SIGNALS_PATH


def _queue_path_for(kwargs: dict[str, Any]) -> _Path:
    if _PENDING_TAG is not None:
        return PENDING_SIGNALS_PATH
    tag = kwargs.get("strategy")
    if tag:
        return _ROOT / f"pending_signals_{_safe_tag(str(tag))}.jsonl"
    return PENDING_SIGNALS_PATH


def save_live_signal_queued(**kwargs: Any) -> str | None:
    """save_live_signal(), but queue the document for retry when it fails."""
    sid = save_live_signal(**kwargs)
    if sid is not None:
        return sid
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        kwargs.setdefault("sent_at", now_iso)   # keep the true send time on replay
        kwargs["_queued_at"] = now_iso
        path = _queue_path_for(kwargs)
        with _PENDING_LOCK:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(_json.dumps(kwargs, default=str) + "\n")
        LOGGER.warning("[MONGO] signal save failed — queued for retry (%s)", path.name)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("[MONGO] could not queue failed signal: %s", exc)
    return None


def _queue_files() -> list[_Path]:
    """This process's queue file(s). Unconfigured callers (web server, scripts) only
    touch the legacy shared file so they never race a bot on its own file."""
    if _PENDING_TAG is not None:
        return [PENDING_SIGNALS_PATH]
    return [_ROOT / "pending_signals.jsonl"]


def flush_pending_signals(max_items: int = 100) -> int:
    """Retry queued signal saves. Returns how many were persisted this call.
    Stops at the first failed replay (Mongo is still down — do not burn a
    6 s server-selection timeout per line every cycle) and rewrites the file
    atomically (tmp + rename)."""
    saved = 0
    for path in _queue_files():
        if not path.exists():
            continue
        kept: list[str] = []
        with _PENDING_LOCK:
            try:
                lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("[MONGO] pending queue unreadable (%s): %s", path.name, exc)
                continue
            if not lines:
                path.unlink(missing_ok=True)
                continue
            stop = False
            for n, ln in enumerate(lines):
                if stop or n >= max_items:
                    kept.append(ln)
                    continue
                try:
                    kw = _json.loads(ln)
                    kw.pop("_queued_at", None)
                    if save_live_signal(**kw) is not None:
                        saved += 1
                        continue
                except Exception as exc:  # noqa: BLE001
                    LOGGER.error("[MONGO] pending signal replay failed: %s", exc)
                kept.append(ln)
                stop = True   # Mongo still unavailable — keep the rest for next cycle
            try:
                if kept:
                    tmp = path.with_name(path.name + ".tmp")
                    tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
                    os.replace(tmp, path)
                else:
                    path.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("[MONGO] pending queue rewrite failed (%s): %s", path.name, exc)
        if saved:
            LOGGER.info("[MONGO] replayed %d queued signal(s) from %s; %d still pending", saved, path.name, len(kept))
    return saved
