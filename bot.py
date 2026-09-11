import os
import json
import asyncio
import logging
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("bot1")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8767998937"))
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", str(ADMIN_ID)))
API_URL_DEFAULT = "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"
POLL_SECONDS = max(1, int(os.getenv("POLL_SECONDS", "2")))
NEXT_SEND_DELAY = max(1, int(os.getenv("NEXT_SEND_DELAY", "90")))
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "12")))
PORT = int(os.getenv("PORT", "10000"))

DATA_FILE = Path("data.json")
DEFAULT_MESSAGES = {str(i): f"BET ON ➡️ {i} NUMBER ALL WALLET" for i in range(10)}
DEFAULT_HEADER = (
    "🚨 <b>DM WIN GAME</b> 🚨\n\n"
    "🔝 <b>WINGO 1 MIN</b> 🔝\n\n"
    "PERIOD NO ➡️ <b>{period3}</b>\n\n"
    "ONLY ALL WALLET BET 📊"
)

def load_data():
    if DATA_FILE.exists():
        try:
            d = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    else:
        d = {}
    d.setdefault("messages", DEFAULT_MESSAGES.copy())
    d.setdefault("header", DEFAULT_HEADER)
    d.setdefault("api_url", API_URL_DEFAULT)
    d.setdefault("running", False)
    return d

DATA = load_data()

def save_data():
    DATA_FILE.write_text(json.dumps(DATA, ensure_ascii=False, indent=2), encoding="utf-8")

def is_admin(update: Update):
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)

async def deny(update):
    await update.effective_message.reply_text("❌ Admin only.")

async def start_cmd(update, context):
    if not is_admin(update): return await deny(update)
    await update.effective_message.reply_text(
        "✅ Bot 1 ready.\n\n"
        "/go - start\n/stop - stop\n/status - status\n"
        "/setmessage 0 - then send the message\n/setheader - then send header\n"
        "/updateapi <URL>\n/changename <name>\n/clearchat"
    )

async def go_cmd(update, context):
    if not is_admin(update): return await deny(update)
    DATA["running"] = True; save_data()
    await update.effective_message.reply_text("▶️ Bot 1 started.")

async def stop_cmd(update, context):
    if not is_admin(update): return await deny(update)
    DATA["running"] = False; save_data()
    await update.effective_message.reply_text("⏹ Bot 1 stopped.")

async def status_cmd(update, context):
    if not is_admin(update): return await deny(update)
    await update.effective_message.reply_text(
        f"Status: {'🟢 RUNNING' if DATA['running'] else '🔴 STOPPED'}\n"
        f"API: {DATA['api_url']}"
    )

async def changename_cmd(update, context):
    if not is_admin(update): return await deny(update)
    name = " ".join(context.args).strip()
    if not name: return await update.effective_message.reply_text("Usage: /changename New Name")
    try:
        await context.bot.set_my_name(name)
        await update.effective_message.reply_text("✅ Name changed.")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ {e}")

async def setmessage_cmd(update, context):
    if not is_admin(update): return await deny(update)
    if len(context.args) != 1 or context.args[0] not in [str(i) for i in range(10)]:
        return await update.effective_message.reply_text("Usage: /setmessage 0 (then send the message)")
    n = context.args[0]
    context.user_data["awaiting_message_number"] = n
    await update.effective_message.reply_text(
        f"📝 Send the message for number {n} in your next message.\n"
        "Telegram formatting/custom emoji will be preserved."
    )

async def setheader_cmd(update, context):
    if not is_admin(update): return await deny(update)
    context.user_data["awaiting_header"] = True
    await update.effective_message.reply_text(
        "📝 Send the header in your next message.\n"
        "Use {period3} where the last 3 period digits should appear."
    )

async def text_capture(update, context):
    if not is_admin(update) or not update.effective_message:
        return
    msg = update.effective_message
    # Telegram custom emoji entities can be lost if only plain text is stored.
    # Copy the message itself to the target chat when it is used as a template is
    # outside the scope of the text handler; here we store text/HTML-compatible text.
    if "awaiting_message_number" in context.user_data:
        n = context.user_data.pop("awaiting_message_number")
        DATA["messages"][n] = msg.text_html or msg.text or msg.caption_html or msg.caption or ""
        save_data()
        await msg.reply_text(f"✅ Message for {n} saved.")
        return
    if context.user_data.pop("awaiting_header", False):
        DATA["header"] = msg.text_html or msg.text or msg.caption_html or msg.caption or ""
        save_data()
        await msg.reply_text("✅ Header saved. Use {period3} for last 3 period digits.")

async def updateapi_cmd(update, context):
    if not is_admin(update): return await deny(update)
    url = " ".join(context.args).strip()
    if not url.startswith(("http://", "https://")):
        return await update.effective_message.reply_text("Usage: /updateapi https://example.com/api")
    DATA["api_url"] = url; save_data()
    await update.effective_message.reply_text("✅ API updated.")

async def clearchat_cmd(update, context):
    if not is_admin(update): return await deny(update)
    ids = context.application.bot_data.get("sent_ids", [])
    count = 0
    for mid in list(ids):
        try:
            await context.bot.delete_message(TARGET_CHAT_ID, mid); count += 1
        except Exception: pass
    context.application.bot_data["sent_ids"] = []
    context.application.bot_data["number_ids"] = {}
    await update.effective_message.reply_text(f"🧹 Cleared {count} bot message(s).")

def rows_from_json(x: Any):
    if isinstance(x, list): return x
    if isinstance(x, dict):
        for k in ("data", "list", "records", "result"):
            v = x.get(k)
            if isinstance(v, list): return v
            if isinstance(v, dict):
                r = rows_from_json(v)
                if r: return r
        for v in x.values():
            r = rows_from_json(v)
            if r: return r
    return []

def period_of(row):
    for k in ("issueNumber", "issue", "period", "periodNumber"):
        if row.get(k) is not None: return str(row[k])
    return None

def result_of(row):
    for k in ("number", "result", "openNumber", "winNumber"):
        if row.get(k) is not None:
            try:
                n = int(str(row[k]).strip())
                if 0 <= n <= 9: return n
            except Exception: pass
    return None

async def fetch_latest(session):
    async with session.get(DATA["api_url"], timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                          headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}) as r:
        r.raise_for_status()
        payload = await r.json(content_type=None)
    for row in rows_from_json(payload):
        if isinstance(row, dict):
            p, n = period_of(row), result_of(row)
            if p and n is not None: return p, n
    return None, None

async def delete_all_except(app, keep_number):
    number_ids = app.bot_data.setdefault("number_ids", {})
    for n, mid in list(number_ids.items()):
        if int(n) == keep_number: continue
        try: await app.bot.delete_message(TARGET_CHAT_ID, mid)
        except Exception: pass
        number_ids.pop(n, None)
        if mid in app.bot_data.setdefault("sent_ids", []):
            app.bot_data["sent_ids"].remove(mid)

async def send_cycle(app, period):
    p3 = period[-3:]
    header = DATA["header"].replace("{period3}", p3)
    try:
        m = await app.bot.send_message(TARGET_CHAT_ID, header, parse_mode=ParseMode.HTML)
        app.bot_data.setdefault("sent_ids", []).append(m.message_id)
    except Exception as e: log.warning("Header send: %s", e)

    ids = app.bot_data.setdefault("number_ids", {})
    for n in range(10):
        text = DATA["messages"].get(str(n), "")
        if not text: continue
        try:
            m = await app.bot.send_message(TARGET_CHAT_ID, text, parse_mode=ParseMode.HTML)
            ids[str(n)] = m.message_id
            app.bot_data.setdefault("sent_ids", []).append(m.message_id)
        except Exception as e: log.warning("Number %s send: %s", n, e)

async def cycle_loop(app):
    last_period = None
    last_cycle_at = 0.0
    loop = asyncio.get_running_loop()
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                if DATA["running"]:
                    period, result = await fetch_latest(session)
                    if period:
                        # On a new API period, remove all number messages except the actual result.
                        if last_period is not None and period != last_period and result is not None:
                            await delete_all_except(app, result)
                        # First run or new period: send the NEXT period's messages.
                        if period != last_period:
                            try:
                                next_period = str(int(period) + 1).zfill(len(period))
                            except Exception:
                                next_period = period
                            await send_cycle(app, next_period)
                            last_period = period
                            last_cycle_at = loop.time()
                        elif loop.time() - last_cycle_at >= NEXT_SEND_DELAY:
                            # Re-send next period after the configured 90 seconds.
                            try:
                                next_period = str(int(period) + 1).zfill(len(period))
                            except Exception:
                                next_period = period
                            await send_cycle(app, next_period)
                            last_cycle_at = loop.time()
                await asyncio.sleep(POLL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("Cycle error: %s", e)
                await asyncio.sleep(POLL_SECONDS)

async def health(request):
    return web.json_response({"status": "ok", "bot": "bot1", "running": DATA["running"]})

async def start_web_server(app):
    runner = web.AppRunner(web.Application())
    runner.app.router.add_get("/", health)
    runner.app.router.add_get("/health", health)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    app.bot_data["web_runner"] = runner
    log.info("Health server listening on port %s", PORT)

async def post_init(app):
    await start_web_server(app)
    app.bot_data["cycle_task"] = asyncio.create_task(cycle_loop(app))

async def post_shutdown(app):
    t = app.bot_data.get("cycle_task")
    if t:
        t.cancel()
        try: await t
        except asyncio.CancelledError: pass
    runner = app.bot_data.get("web_runner")
    if runner: await runner.cleanup()

def main():
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN environment variable is missing.")
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("go", go_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("changename", changename_cmd))
    app.add_handler(CommandHandler("setmessage", setmessage_cmd))
    app.add_handler(CommandHandler("setheader", setheader_cmd))
    app.add_handler(CommandHandler("updateapi", updateapi_cmd))
    app.add_handler(CommandHandler("clearchat", clearchat_cmd))
    from telegram.ext import MessageHandler, filters
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_capture))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
