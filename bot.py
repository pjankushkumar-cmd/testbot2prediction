import os
import json
import asyncio
import logging
from pathlib import Path
from typing import Any

import aiohttp
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("bot1")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8767998937"))
DEFAULT_API_URL = "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"
API_URL = os.getenv("API_URL", DEFAULT_API_URL).strip()
POLL_SECONDS = max(1, int(os.getenv("POLL_SECONDS", "2")))
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", str(ADMIN_ID)))

DATA_FILE = Path("data.json")
DEFAULT_MESSAGES = {
    str(i): f"BET ON ➡️ {i} NUMBER ALL WALLET" for i in range(10)
}

def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            data.setdefault("messages", DEFAULT_MESSAGES.copy())
            data.setdefault("api_url", API_URL)
            data.setdefault("running", False)
            return data
        except Exception:
            pass
    return {
        "messages": DEFAULT_MESSAGES.copy(),
        "api_url": API_URL,
        "running": False,
    }

def save_data(data: dict) -> None:
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

DATA = load_data()

def admin_only(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)

async def deny(update: Update) -> None:
    if update.effective_message:
        await update.effective_message.reply_text("❌ Admin only.")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return await deny(update)
    await update.effective_message.reply_text(
        "✅ Bot 1 ready.\n\n"
        "/go - start\n"
        "/stop - stop\n"
        "/status - status\n"
        "/changename <name> - bot name\n"
        "/clearchat - delete bot messages\n"
        "/setmessage <0-9> - set message\n"
        "/updateapi <URL> - update API\n"
        "/setheader <text> - period header"
    )

async def go_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return await deny(update)
    DATA["running"] = True
    save_data(DATA)
    await update.effective_message.reply_text("▶️ Bot 1 started.")

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return await deny(update)
    DATA["running"] = False
    save_data(DATA)
    await update.effective_message.reply_text("⏹ Bot 1 stopped.")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return await deny(update)
    await update.effective_message.reply_text(
        f"Status: {'🟢 RUNNING' if DATA.get('running') else '🔴 STOPPED'}\n"
        f"API: {DATA.get('api_url')}"
    )

async def changename_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return await deny(update)
    name = " ".join(context.args).strip()
    if not name:
        return await update.effective_message.reply_text(
            "Usage: /changename Your Name"
        )
    try:
        await context.bot.set_my_name(name)
        await update.effective_message.reply_text(f"✅ Name changed to: {name}")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Name change failed: {e}")

async def setmessage_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return await deny(update)
    if len(context.args) < 2:
        return await update.effective_message.reply_text(
            "Usage:\n/setmessage 0 Your message here\n\n"
            "Use Telegram HTML formatting if required."
        )
    number = context.args[0]
    if number not in [str(i) for i in range(10)]:
        return await update.effective_message.reply_text("❌ Number must be 0-9.")
    message = " ".join(context.args[1:]).strip()
    DATA["messages"][number] = message
    save_data(DATA)
    await update.effective_message.reply_text(
        f"✅ Message for {number} saved."
    )

async def setheader_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return await deny(update)
    header = " ".join(context.args).strip()
    if not header:
        return await update.effective_message.reply_text(
            "Usage: /setheader PERIOD NO ➡️ <b>{period3}</b>"
        )
    DATA["header"] = header
    save_data(DATA)
    await update.effective_message.reply_text(
        "✅ Header saved. Use {period3} for the last 3 period digits."
    )

async def updateapi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return await deny(update)
    new_url = " ".join(context.args).strip()
    if not new_url.startswith(("http://", "https://")):
        return await update.effective_message.reply_text(
            "Usage: /updateapi https://example.com/api"
        )
    DATA["api_url"] = new_url
    save_data(DATA)
    await update.effective_message.reply_text("✅ API updated.")

async def clearchat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return await deny(update)
    # Telegram bots cannot arbitrarily clear an entire chat history.
    # This command removes messages recorded by this bot during this run.
    ids = context.application.bot_data.get("sent_message_ids", [])
    deleted = 0
    for mid in list(ids):
        try:
            await context.bot.delete_message(TARGET_CHAT_ID, mid)
            deleted += 1
        except Exception:
            pass
    context.application.bot_data["sent_message_ids"] = []
    await update.effective_message.reply_text(
        f"🧹 Cleared {deleted} bot message(s)."
    )

def find_history_list(obj: Any):
    if isinstance(obj, dict):
        for key in ("data", "list", "records", "result"):
            value = obj.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                found = find_history_list(value)
                if found is not None:
                    return found
        for value in obj.values():
            found = find_history_list(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        return obj
    return None

def extract_period(item: dict) -> str | None:
    for key in ("issueNumber", "issue", "period", "periodNumber", "number"):
        value = item.get(key)
        if value is not None:
            return str(value)
    return None

def extract_result(item: dict) -> int | None:
    for key in ("number", "result", "openNumber", "winNumber"):
        value = item.get(key)
        if value is None:
            continue
        try:
            n = int(str(value).strip())
            if 0 <= n <= 9:
                return n
        except Exception:
            pass
    return None

async def fetch_latest(session: aiohttp.ClientSession):
    async with session.get(
        DATA.get("api_url", API_URL),
        timeout=aiohttp.ClientTimeout(total=10),
        headers={"User-Agent": "Mozilla/5.0"},
    ) as response:
        response.raise_for_status()
        payload = await response.json(content_type=None)

    rows = find_history_list(payload) or []
    for row in rows:
        if isinstance(row, dict):
            period = extract_period(row)
            result = extract_result(row)
            if period and result is not None:
                return period, result
    return None, None

async def send_prediction_messages(app: Application, period: str):
    # Send the header first. {period3} is replaced by the last 3 digits.
    period3 = period[-3:]
    header = DATA.get(
        "header",
        "🚨 <b>DM WIN GAME</b> 🚨\n\n"
        "🔝 <b>WINGO 1 MIN</b> 🔝\n\n"
        "PERIOD NO ➡️ <b>{period3}</b>\n\n"
        "ONLY ALL WALLET BET",
    ).replace("{period3}", period3)

    try:
        header_msg = await app.bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text=header,
            parse_mode=ParseMode.HTML,
        )
        app.bot_data.setdefault("sent_message_ids", []).append(header_msg.message_id)
    except Exception as e:
        log.warning("Could not send header: %s", e)

    number_ids = app.bot_data.setdefault("number_message_ids", {})
    for n in range(10):
        text = DATA["messages"].get(str(n), DEFAULT_MESSAGES[str(n)])
        try:
            msg = await app.bot.send_message(
                chat_id=TARGET_CHAT_ID,
                text=text,
                parse_mode=ParseMode.HTML,
            )
            number_ids[str(n)] = msg.message_id
            app.bot_data.setdefault("sent_message_ids", []).append(msg.message_id)
        except Exception as e:
            log.warning("Could not send message %s: %s", n, e)

async def delete_number_message(app: Application, number: int):
    # Delete only the message belonging to the API result number.
    ids = app.bot_data.setdefault("number_message_ids", {})
    mid = ids.get(str(number))
    if mid:
        try:
            await app.bot.delete_message(TARGET_CHAT_ID, mid)
        except Exception:
            pass
        ids.pop(str(number), None)

async def poll_loop(app: Application):
    last_period = None
    session = aiohttp.ClientSession()
    try:
        while True:
            if DATA.get("running"):
                try:
                    period, result = await fetch_latest(session)
                    if period and period != last_period:
                        last_period = period
                        log.info("Latest period=%s result=%s", period, result)
                        await send_prediction_messages(app, period)
                        # Keep a short-lived mapping for a subsequent result.
                        app.bot_data["latest_period"] = period
                        app.bot_data["latest_result"] = result
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning("API error: %s", e)
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await session.close()

async def post_init(app: Application):
    app.bot_data["poll_task"] = asyncio.create_task(poll_loop(app))

async def post_shutdown(app: Application):
    task = app.bot_data.get("poll_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("go", go_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("changename", changename_cmd))
    app.add_handler(CommandHandler("setmessage", setmessage_cmd))
    app.add_handler(CommandHandler("updateapi", updateapi_cmd))
    app.add_handler(CommandHandler("setheader", setheader_cmd))
    app.add_handler(CommandHandler("clearchat", clearchat_cmd))

    log.info("Bot 1 starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
