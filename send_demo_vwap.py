"""One-off demo sender for the VWAP + Supertrend Telegram alert.

Sends a clearly-marked DEMO signal using the *real* live-bot message format
(`vwap_st_live._format_message`), so the group can see exactly what a VWAP+ST
signal looks like now that delivery is fixed. Telegram only — no Mongo write,
no orders.

Run on the VPS:  /opt/sean0algo/.venv/bin/python send_demo_vwap.py
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from core.telegram_bot import TelegramNotifier
from vwap_st_live import _format_message

ROOT = Path(__file__).resolve().parent


async def main() -> None:
    load_dotenv(ROOT / ".env")
    tg = TelegramNotifier(
        token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
    )
    # Mirror the real Sep-1 setup the delivery bug dropped, so this doubles as
    # "here is what you missed" — but banner it clearly as a test.
    body = _format_message(
        direction="SELL",
        entry=4351.19,
        sl=4360.11,
        tp=4333.34,
        atr=5.9492,
        candle_ts="2026-09-01 16:25:00",
    )
    msg = (
        "\U0001F9EA *DEMO / TEST SIGNAL - do not trade*\n"
        "_(replay of the Sep-1 22:25 Dhaka setup; live delivery is now fixed)_\n\n"
        + body
    )
    ok = await tg.send_message(msg)
    print("sent:", ok)


if __name__ == "__main__":
    asyncio.run(main())
