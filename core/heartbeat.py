"""Per-bot liveness heartbeat.

A systemd unit can be `active` while the bot inside is blind (mirror blocked,
hung loop, "insufficient candles" forever). Each bot calls `beat(name)` after every
*successful* evaluation cycle; the file's timestamp is the proof of life.
`/api/health` reports the ages (`bot_heartbeats` / `bot_stalled`) and
`ops/healthcheck.sh` alerts on Telegram when one goes stale.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HB_DIR = Path(os.getenv("SEAN_HEARTBEAT_DIR", str(ROOT / "logs" / "heartbeat")))
LOGGER = logging.getLogger(__name__)


def beat(name: str, note: str = "") -> None:
    """Record that `name` just completed a good cycle (atomic write, never raises)."""
    try:
        HB_DIR.mkdir(parents=True, exist_ok=True)
        tmp = HB_DIR / f".{name}.tmp"
        tmp.write_text(json.dumps({"name": name, "ts": time.time(), "note": note}), encoding="utf-8")
        os.replace(tmp, HB_DIR / name)
    except Exception as exc:  # noqa: BLE001 — a heartbeat must never take a bot down
        LOGGER.debug("heartbeat write failed for %s: %s", name, exc)


def read_all() -> dict[str, dict[str, Any]]:
    """{name: {ts, age_s, note}} for every heartbeat file present."""
    out: dict[str, dict[str, Any]] = {}
    now = time.time()
    try:
        entries = list(HB_DIR.iterdir())
    except FileNotFoundError:
        return out
    except Exception:  # noqa: BLE001
        return out
    for p in entries:
        if p.name.startswith(".") or not p.is_file():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            ts = float(d.get("ts") or 0)
            out[p.name] = {"ts": ts, "age_s": round(now - ts, 1), "note": str(d.get("note") or "")}
        except Exception:  # noqa: BLE001
            try:
                m = p.stat().st_mtime
                out[p.name] = {"ts": m, "age_s": round(now - m, 1), "note": "unreadable"}
            except Exception:  # noqa: BLE001
                pass
    return out
