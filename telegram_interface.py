import logging
import subprocess
import os
from pathlib import Path
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Telegram bot token and chat/group ID
BOT_TOKEN = "8285509725:AAEIcqU3lHSQO3EKdwJ-rqPe0PliVKAP5r0"
CHAT_ID = "-5113832568"

# Paths
ROOT_DIR = Path(__file__).parent.resolve()
DECISION_LOG_PATH = ROOT_DIR / "logs" / "decision_trace.log"
BACKTEST_SCRIPT = ROOT_DIR / "backtest_xau_strategy.py"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to SEAN0-ALGO-V1 Bot!\n"
        "Commands:\n"
    "/backtest - run forex mode backtest\n"
        "/status - show paper trading status\n"
        "/logs - show last 20 decision logs"
    )

async def run_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Running forex mode backtest... please wait.")
    try:
        result = subprocess.run(
            ["python", str(BACKTEST_SCRIPT), "backtest", "--mode", "forex", "--months", "6", "--validation-samples", "5"],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT_DIR)
        )
        output = result.stdout or "No output."
    except Exception as e:
        output = f"Error running backtest: {e}"
    await update.message.reply_text(f"Backtest result:\n{output[:4000]}")

async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Adapt this command to your system's paper_status command or API
    try:
        # Example: run paper-status CLI tool
        result = subprocess.run(
            ["python", str(BACKTEST_SCRIPT), "paper-status", "--log-limit", "20"],
            capture_output=True, text=True, timeout=30, cwd=str(ROOT_DIR)
        )
        output = result.stdout or "No output."
    except Exception as e:
        output = f"Error fetching status: {e}"
    await update.message.reply_text(f"Paper trading status:\n{output[:4000]}")

async def show_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if DECISION_LOG_PATH.exists():
            lines = DECISION_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
            last_lines = "\n".join(lines[-20:])
        else:
            last_lines = "Decision log file not found."
    except Exception as e:
        last_lines = f"Error reading logs: {e}"
    await update.message.reply_text(f"Last 20 decision log lines:\n{last_lines}")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if "backtest" in text:
        await run_backtest(update, context)
    elif "status" in text:
        await show_status(update, context)
    elif "log" in text:
        await show_logs(update, context)
    else:
        await update.message.reply_text("Unrecognized command or message. Use /start for help.")

async def send_signal(bot: Bot, message: str):
    await bot.send_message(chat_id=CHAT_ID, text=message)

def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("backtest", run_backtest))
    application.add_handler(CommandHandler("status", show_status))
    application.add_handler(CommandHandler("logs", show_logs))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), echo))

    logger.info("Bot started. Listening for updates...")
    application.run_polling()

if __name__ == "__main__":
    main()
