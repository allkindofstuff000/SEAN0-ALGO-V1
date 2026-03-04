from __future__ import annotations

from dataclasses import dataclass

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from risk_manager import RiskManager
from signal_logic import TradeSignal


@dataclass
class TelegramSignalBot:
    """
    Telegram transport + command handlers for manual performance control.
    Uses python-telegram-bot v20 async style.
    """

    token: str
    chat_id: str
    risk_manager: RiskManager
    pair_name: str = "XAUUSD OTC"

    def __post_init__(self) -> None:
        self.application = Application.builder().token(self.token).build()
        self.application.add_handler(CommandHandler("start", self._start_command))
        self.application.add_handler(CommandHandler("setloss", self._set_loss_command))
        self.application.add_handler(CommandHandler("markwin", self._mark_win_command))
        self.application.add_handler(CommandHandler("markloss", self._mark_loss_command))

    async def send_signal(self, signal: TradeSignal) -> None:
        text = self._format_signal_message(signal)
        await self.application.bot.send_message(chat_id=self.chat_id, text=text)

    def _format_signal_message(self, signal: TradeSignal) -> str:
        entry_time = signal.timestamp_utc.tz_convert("Asia/Dhaka").strftime("%H:%M")
        reason_block = "\n".join(f"• {line}" for line in signal.reason_lines)
        return (
            "-----------------------\n"
            "🚀 HIGH WIN-RATE SIGNAL\n"
            f"Signal: {signal.signal_type}\n"
            f"Pair: {self.pair_name}\n"
            "Timeframe: 15M (1H confirmed)\n"
            "Expiry: 30 Minutes\n"
            f"Entry Candle Close: {entry_time} BDT\n"
            "Reason:\n"
            f"{reason_block}\n"
            "-----------------------"
        )

    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized_chat(update):
            return
        if update.message is None:
            return
        streak = self.risk_manager.get_consecutive_losses()
        message = (
            "Bot is running.\n"
            f"Current consecutive losses: {streak}\n\n"
            "Commands:\n"
            "/setloss <0-5>\n"
            "/markwin\n"
            "/markloss"
        )
        await update.message.reply_text(message)

    async def _set_loss_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized_chat(update):
            return
        if update.message is None:
            return
        if not context.args:
            await update.message.reply_text("Usage: /setloss <0-5>")
            return

        raw_value = context.args[0].strip()
        if not raw_value.isdigit():
            await update.message.reply_text("Invalid value. Use integer between 0 and 5.")
            return

        value = int(raw_value)
        if value < 0 or value > 5:
            await update.message.reply_text("Invalid value. Use integer between 0 and 5.")
            return

        self.risk_manager.set_consecutive_losses(value)
        await update.message.reply_text(f"Consecutive losses updated to {value}.")

    async def _mark_win_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized_chat(update):
            return
        if update.message is None:
            return
        success, message = self.risk_manager.mark_last_signal("WIN")
        await update.message.reply_text(message)
        if success:
            await update.message.reply_text("Loss streak reset to 0.")

    async def _mark_loss_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized_chat(update):
            return
        if update.message is None:
            return
        success, message = self.risk_manager.mark_last_signal("LOSS")
        await update.message.reply_text(message)
        if success:
            streak = self.risk_manager.get_consecutive_losses()
            await update.message.reply_text(f"Consecutive losses is now {streak}.")

    def _is_authorized_chat(self, update: Update) -> bool:
        if update.effective_chat is None:
            return False
        current_chat_id = str(update.effective_chat.id)
        if current_chat_id != str(self.chat_id):
            return False
        return True
