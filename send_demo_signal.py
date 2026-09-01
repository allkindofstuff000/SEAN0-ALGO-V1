"""One-off: fire a DEMO signal to the Telegram group in the new template
(with the strategy name header), so we can preview the format before wiring
it into the live signal path. Clearly marked as a demo — not a live trade."""
import asyncio
import os
import sys

sys.path.insert(0, "/opt/sean0algo")
from dotenv import load_dotenv

load_dotenv("/opt/sean0algo/.env")
from core.telegram_bot import TelegramNotifier

MSG = (
    "🧪 DEMO — new template preview (not a live trade)\n"
    "━━━━━━━━━━━━━━\n"
    "📊 RSI EMA STRATEGY\n"
    "XAUUSD SELL\n"
    "Entry: 4362.58\n"
    "SL: 4371.78\n"
    "TP: 4353.39"
)


async def main() -> None:
    tg = TelegramNotifier(
        token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
    )
    ok = await tg.send_message(MSG)
    print("DEMO_SENT:", ok)


asyncio.run(main())
