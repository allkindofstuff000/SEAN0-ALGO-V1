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

# python-telegram-bot's default connect timeout is 5 s. The resolver's daily
# heartbeat timed out on 3 of 3 natural 00:00 UTC runs (a busy moment on the
# shared box), so every client built here gets a 20 s budget.
TELEGRAM_CONNECT_TIMEOUT = 20.0
TELEGRAM_RW_TIMEOUT = 20.0


def make_bot(token: str) -> Bot:
    """A Bot with the longer timeouts (falls back to defaults on old PTB versions)."""
    try:
        from telegram.request import HTTPXRequest  # PTB >= 20

        req = HTTPXRequest(
            connect_timeout=TELEGRAM_CONNECT_TIMEOUT,
            read_timeout=TELEGRAM_RW_TIMEOUT,
            write_timeout=TELEGRAM_RW_TIMEOUT,
            pool_timeout=TELEGRAM_CONNECT_TIMEOUT,
        )
        return Bot(token, request=req)
    except Exception:  # noqa: BLE001
        return Bot(token)


@dataclass
class TelegramNotifier:
    """
    Minimal Telegram notifier for the XAU-only backend MVP.
    """

    token: str
    chat_id: str

    def __post_init__(self) -> None:
        self.enabled = bool(self.token and self.chat_id)
        self._bot = make_bot(self.token) if self.enabled else None
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
