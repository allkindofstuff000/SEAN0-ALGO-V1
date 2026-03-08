from __future__ import annotations

import logging
from dataclasses import dataclass

from telegram import Bot


LOGGER = logging.getLogger(__name__)


@dataclass
class TelegramNotifier:
    """
    Minimal Telegram notifier for the backend-only MVP.
    """

    token: str
    chat_id: str

    def __post_init__(self) -> None:
        self.enabled = bool(self.token and self.chat_id)
        self._bot = Bot(self.token) if self.enabled else None
        if not self.enabled:
            LOGGER.warning("[TELEGRAM] notifier disabled; missing token/chat_id")

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
