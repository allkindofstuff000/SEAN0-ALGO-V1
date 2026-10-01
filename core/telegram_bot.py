from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from telegram import Bot

if TYPE_CHECKING:
    from .signal_logic import TradeSignal


LOGGER = logging.getLogger(__name__)

# httpx logs every request URL at INFO — for Telegram that URL embeds the bot
# token (…/bot<TOKEN>/sendMessage) and would land in /var/log + journald.
# Silenced here so every importer (all live bots + the resolver) is covered.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@dataclass
class TelegramNotifier:
    """
    Minimal Telegram notifier for the XAU-only backend MVP.
    """

    token: str
    chat_id: str

    def __post_init__(self) -> None:
        self.enabled = bool(self.token and self.chat_id)
        self._bot = Bot(self.token) if self.enabled else None
        if not self.enabled:
            LOGGER.warning("[TELEGRAM] notifier disabled; missing token/chat_id")

    def format_signal(self, signal: "TradeSignal") -> str:
        return signal.forex_message()

    async def send_signal(self, signal: "TradeSignal") -> bool:
        return await self.send_message(self.format_signal(signal))

    async def send_message(self, text: str) -> bool:
        if not self.enabled or self._bot is None:
            LOGGER.info("[TELEGRAM] send skipped; notifier disabled")
            return False
        try:
            await self._bot.send_message(chat_id=self.chat_id, text=text)
            LOGGER.info("[TELEGRAM] signal sent")
            return True
        except Exception:
            LOGGER.exception("[TELEGRAM] send failed")
            return False
