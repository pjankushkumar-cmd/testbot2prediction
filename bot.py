import os
import json
import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Optional

import aiohttp
from aiohttp import web

from telegram import Update, MessageEntity
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8767998937"))
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", str(ADMIN_ID)))

API_URL_DEFAULT = (
    "https://draw.ar-lottery01.com/WinGo/WinGo_1M/"
    "GetHistoryIssuePage.json"
)

POLL_SECONDS = max(1, int(os.getenv("POLL_SECONDS", "2")))
NEXT_SEND_DELAY = max(1, int(os.getenv("NEXT_SEND_DELAY", "90")))
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "12")))
PORT = int(os.getenv("PORT", "10000"))

DATA_FILE = Path("data.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("bot1")

DEFAULT_HEADER = (
    "🚨 DM WIN GAME 🚨\n"
    "🔝 WINGO 1 MIN 🔝\n"
    "PERIOD NO ➡️ {period3}\n"
    "ONLY ALL WALLET BET 📊"
)

# =========================
# DATA
# =========================
def default_data():
    return {
        "messages": {str(i): {"text": "", "entities": []} for i in range(10)},
        "header": {"text": DEFAULT_HEADER, "entities": []},
        "api_url": API_URL_DEFAULT,
        "running": False,
    }


def load_data():
    d = default_data()
    try:
        if DATA_FILE.exists():
            saved = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                d.update(saved)
    except Exception:
        log.exception("Could not load data.json")

    if not isinstance(d.get("messages"), dict):
        d["messages"] = {}
    for i in range(10):
        d["messages"].setdefault(str(i), {"text": "", "entities": []})

    if not isinstance(d.get("header"), dict):
        d["header"] = {"text": DEFAULT_HEADER, "entities": []}

    if not d.get("api_url"):
        d["api_url"] = API_URL_DEFAULT

    return d


DATA = load_data()


def save_data():
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(DATA, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(DATA_FILE)


# =========================
# TELEGRAM ENTITY HELPERS
# =========================
ENTITY_FIELDS = {
    "url", "user", "language", "custom_emoji_id", "emoji",
    "type", "offset", "length"
}


def entity_to_dict(e: MessageEntity):
    out = {
        "type": e.type,
        "offset": e.offset,
        "length": e.length,
    }
    for key in ("url", "language", "custom_emoji_id"):
        value = getattr(e, key, None)
        if value is not None:
            out[key] = value
    return out


def message_to_record(message):
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    return {
        "text": text,
        "entities": [entity_to_dict(e) for e in entities],
    }


def record_to_entities(record):
    result = []
    for x in (record or {}).get("entities", []):
        if not isinstance(x, dict):
            continue
        kwargs = {
            "type": x.get("type"),
            "offset": int(x.get("offset", 0)),
            "length": int(x.get("length", 0)),
        }
        for key in ("url", "language", "custom_emoji_id"):
            if x.get(key) is not None:
                kwargs[key] = x[key]
        try:
            result.append(MessageEntity(**kwargs))
        except Exception:
            log.exception("Bad Telegram entity: %s", x)
    return result


def record_text(record):
    return (record or {}).get("text", "")


# =========================
# GLOBAL RUNTIME STATE
# =========================
runtime = {
    "sent_ids": [],          # [(message_id, number_or_none)]
    "cycle_target": None,    # target period whose messages are currently shown
    "confirmed_period": None,
    "confirmed_result": None,
    "confirmed_at": None,
    "phase": "idle",
    "api_ok": False,
    "api_error": "",
    "last_period": None,
    "last_result": None,
}

worker_task = None
capture_tasks = {}


# =========================
# PERIOD / API PARSING
# =========================
def normalize_period(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return s


def next_period(period: str):
    s = normalize_period(period)
    if not s:
        return None
    try:
        return str(int(s) + 1).zfill(len(s))
    except Exception:
        # Fallback for non-numeric period strings:
        return s


def rows_from_json(x: Any):
    """
    Recursively finds likely list containers in the API response.
    Supports:
      data.list
      data.records
      data.result
      list
      records
      result
      nested dictionaries/lists
    """
    if isinstance(x, list):
        return x

    if isinstance(x, dict):
        preferred = (
            "list", "records", "data", "result",
            "rows", "items", "history", "resultList"
        )

        for key in preferred:
            if key in x:
                v = x[key]
                if isinstance(v, list):
                    return v
                found = rows_from_json(v)
                if found:
                    return found

        for v in x.values():
            found = rows_from_json(v)
            if found:
                return found

    return []


def period_of(row):
    if not isinstance(row, dict):
        return None

    keys = (
        "issueNumber", "issue", "period", "periodNumber",
        "issueNo", "periodNo", "drawNumber", "drawNo"
    )
    for k in keys:
        if row.get(k) is not None:
            return normalize_period(row[k])

    # Case-insensitive fallback.
    for k, v in row.items():
        lk = str(k).lower()
        if any(
            token in lk
            for token in ("issuenumber", "periodnumber", "periodno", "drawnumber")
        ):
            p = normalize_period(v)
            if p:
                return p

    return None


def result_of(row):
    if not isinstance(row, dict):
        return None

    keys = (
        "number", "result", "openNumber", "winNumber",
        "winningNumber", "open_num", "win_num"
    )

    for k in keys:
        if row.get(k) is not None:
            try:
                value = str(row[k]).strip()
                # Some APIs may return "7,..." or "7".
                value = value.split(",")[0].strip()
                n = int(value)
                if 0 <= n <= 9:
                    return n
            except Exception:
                pass

    # Case-insensitive fallback.
    for k, v in row.items():
        lk = str(k).lower()
        if any(token in lk for token in ("number", "result", "winnumber", "opennumber")):
            try:
                n = int(str(v).strip().split(",")[0])
                if 0 <= n <= 9:
                    return n
            except Exception:
                pass

    return None


def find_period_result(payload):
    rows = rows_from_json(payload)

    # First pass: normal rows.
    for row in rows:
        if isinstance(row, dict):
            p = period_of(row)
            n = result_of(row)
            if p and n is not None:
                return p, n

    # Deep fallback: search every dict in the payload.
    def walk(x):
        if isinstance(x, dict):
            p = period_of(x)
            n = result_of(x)
            if p and n is not None:
                return p, n
            for v in x.values():
                got = walk(v)
                if got:
                    return got
        elif isinstance(x, list):
            for v in x:
                got = walk(v)
                if got:
                    return got
        return None

    return walk(payload) or (None, None)


async def fetch_latest(session):
    url = DATA["api_url"].strip()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10) "
            "AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://draw.ar-lottery01.com/",
        "Origin": "https://draw.ar-lottery01.com",
        "Connection": "keep-alive",
    }

    log.info("API request: %s", url)

    try:
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            allow_redirects=True,
        ) as response:
            status = response.status
            content_type = response.headers.get("Content-Type", "")

            body = await response.text(errors="replace")

            log.info(
                "API response: HTTP %s | content-type=%s | bytes=%s",
                status,
                content_type,
                len(body.encode("utf-8")),
            )

            response.raise_for_status()

            try:
                payload = json.loads(body)
            except Exception:
                log.error("API did not return valid JSON. First 500 chars: %s", body[:500])
                raise

            period, result = find_period_result(payload)

            if period is None or result is None:
                log.error(
                    "API JSON received but period/result not detected. "
                    "Top-level type=%s | keys=%s",
                    type(payload).__name__,
                    list(payload.keys())[:30] if isinstance(payload, dict) else "n/a",
                )
                log.error("API sample: %s", body[:1000])
                return None, None

            log.info("API latest: period=%s result=%s", period, result)
            runtime["api_ok"] = True
            runtime["api_error"] = ""
            runtime["last_period"] = period
            runtime["last_result"] = result
            return period, result

    except Exception as e:
        runtime["api_ok"] = False
        runtime["api_error"] = f"{type(e).__name__}: {e}"
        log.exception("API fetch failed: %s", e)
        return None, None


# =========================
# TELEGRAM SEND / DELETE
# =========================
async def send_record(app, chat_id, record, period3=None):
    text = record_text(record)

    if not text:
        return None

    # Header placeholder replacement without changing user message entities
    # is safest only when placeholder is outside entities. For the default
    # header this is true. If replacement changes offsets, send plain text.
    if period3 is not None and "{period3}" in text:
        text = text.replace("{period3}", str(period3))
        entities = record_to_entities(record)
        if entities:
            # Offsets can become invalid after replacement; use HTML/text fallback.
            entities = []
    else:
        entities = record_to_entities(record)

    try:
        return await app.bot.send_message(
            chat_id=chat_id,
            text=text,
            entities=entities or None,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.error("Telegram send failed: %s", e)

        # Last-resort plain-text send. This prevents one malformed entity
        # from stopping the whole cycle.
        try:
            return await app.bot.send_message(
                chat_id=chat_id,
                text=text,
                disable_web_page_preview=True,
            )
        except Exception:
            log.exception("Telegram plain-text fallback also failed")
            return None


async def delete_tracked(app, keep_numbers=None):
    keep_numbers = set(keep_numbers or [])
    remaining = []

    for item in list(runtime["sent_ids"]):
        if isinstance(item, dict):
            mid = item.get("id")
            number = item.get("number")
        else:
            mid = item[0] if item else None
            number = item[1] if len(item) > 1 else None

        if mid is None:
            continue

        if number in keep_numbers:
            remaining.append({"id": mid, "number": number})
            continue

        try:
            await app.bot.delete_message(
                chat_id=TARGET_CHAT_ID,
                message_id=int(mid),
            )
        except Exception as e:
            # Already deleted / too old / not found is harmless.
            log.warning("Delete message %s failed: %s", mid, e)

    runtime["sent_ids"] = remaining


async def send_cycle(app, target_period):
    # Remove anything still visible from the previous cycle.
    await delete_tracked(app, keep_numbers=set())

    p3 = str(target_period)[-3:].zfill(3)

    header_msg = await send_record(
        app,
        TARGET_CHAT_ID,
        DATA["header"],
        period3=p3,
    )
    if header_msg:
        runtime["sent_ids"].append({
            "id": header_msg.message_id,
            "number": None,
        })

    sent_count = 0

    for n in range(10):
        record = DATA["messages"].get(str(n), {})
        if not record_text(record):
            continue

        msg = await send_record(app, TARGET_CHAT_ID, record)
        if msg:
            runtime["sent_ids"].append({
                "id": msg.message_id,
                "number": n,
            })
            sent_count += 1

    runtime["cycle_target"] = str(target_period)
    runtime["phase"] = "waiting_result"

    log.info(
        "Cycle sent: target_period=%s | messages=%s",
        target_period,
        sent_count,
    )


async def keep_only_result(app, result_number):
    # Keep only the configured message for the winning number.
    await delete_tracked(app, keep_numbers={int(result_number)})


# =========================
# MAIN CYCLE WORKER
# =========================
async def cycle_worker(app):
    log.info("Cycle worker started")

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)

    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
    ) as session:

        # A fresh state after deploy/restart.
        runtime["cycle_target"] = None
        runtime["phase"] = "idle"
        runtime["confirmed_period"] = None
        runtime["confirmed_result"] = None
        runtime["confirmed_at"] = None

        while True:
            try:
                if not DATA.get("running"):
                    runtime["phase"] = "stopped"
                    await asyncio.sleep(POLL_SECONDS)
                    continue

                period, result = await fetch_latest(session)

                if not period:
                    await asyncio.sleep(POLL_SECONDS)
                    continue

                now = time.monotonic()

                # -------------------------
                # FIRST START
                # -------------------------
                if runtime["cycle_target"] is None:
                    target = next_period(period)
                    if target is None:
                        await asyncio.sleep(POLL_SECONDS)
                        continue

                    log.info(
                        "Starting first cycle: API latest=%s result=%s -> target=%s",
                        period, result, target
                    )
                    await send_cycle(app, target)
                    await asyncio.sleep(POLL_SECONDS)
                    continue

                target = str(runtime["cycle_target"])

                # -------------------------
                # RESULT CONFIRMATION
                # -------------------------
                if runtime["phase"] == "waiting_result":
                    if str(period) == target and result is not None:
                        log.info(
                            "Target period completed: %s | result=%s",
                            target,
                            result,
                        )

                        await keep_only_result(app, result)

                        runtime["confirmed_period"] = target
                        runtime["confirmed_result"] = result
                        runtime["confirmed_at"] = now
                        runtime["phase"] = "waiting_90_seconds"

                        log.info(
                            "Keeping result %s for period %s; next cycle in %ss",
                            result,
                            target,
                            NEXT_SEND_DELAY,
                        )

                # -------------------------
                # 90-SECOND DELAY
                # -------------------------
                elif runtime["phase"] == "waiting_90_seconds":
                    confirmed_at = runtime.get("confirmed_at")

                    if (
                        confirmed_at is not None
                        and now - float(confirmed_at) >= NEXT_SEND_DELAY
                    ):
                        new_target = next_period(target)

                        if new_target is None:
                            log.error("Could not calculate next period from %s", target)
                        else:
                            log.info(
                                "90 seconds complete: %s -> next target=%s",
                                target,
                                new_target,
                            )
                            await send_cycle(app, new_target)

                await asyncio.sleep(POLL_SECONDS)

            except asyncio.CancelledError:
                log.info("Cycle worker cancelled")
                raise
            except Exception:
                log.exception("Cycle worker error")
                await asyncio.sleep(POLL_SECONDS)


# =========================
# COMMANDS
# =========================
def is_admin(update: Update):
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)


async def deny(update: Update):
    if update.effective_message:
        await update.effective_message.reply_text("❌ Admin only.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)

    await update.effective_message.reply_text(
        "✅ Bot 1 ready.\n\n"
        "/go - start\n"
        "/stop - stop\n"
        "/status - status\n"
        "/setmessage 0 - then send the message\n"
        "/setheader - then send header\n"
        "/updateapi <URL>\n"
        "/changename <name>\n"
        "/clearchat"
    )


async def go(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)

    DATA["running"] = True
    save_data()

    runtime["phase"] = "idle"
    runtime["cycle_target"] = None
    runtime["confirmed_at"] = None

    await update.effective_message.reply_text("▶️ Bot 1 started.")


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)

    DATA["running"] = False
    save_data()
    runtime["phase"] = "stopped"

    await update.effective_message.reply_text("⏹ Bot 1 stopped.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)

    api_state = "🟢 OK" if runtime["api_ok"] else "🔴 WAIT/ERROR"

    await update.effective_message.reply_text(
        f"Status: {'🟢 RUNNING' if DATA.get('running') else '🔴 STOPPED'}\n"
        f"API: {DATA['api_url']}\n"
        f"API state: {api_state}\n"
        f"Latest period: {runtime.get('last_period') or '-'}\n"
        f"Latest result: {runtime.get('last_result') if runtime.get('last_result') is not None else '-'}\n"
        f"Cycle target: {runtime.get('cycle_target') or '-'}\n"
        f"Phase: {runtime.get('phase') or '-'}"
    )


async def changename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)

    name = " ".join(context.args).strip()
    if not name:
        return await update.effective_message.reply_text(
            "Use: /changename Your Bot Name"
        )

    try:
        await context.bot.set_my_name(name=name)
        await update.effective_message.reply_text(
            f"✅ Bot name changed to: {name}"
        )
    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Could not change name: {e}"
        )


async def updateapi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)

    url = " ".join(context.args).strip()
    if not url:
        return await update.effective_message.reply_text(
            "Use: /updateapi <URL>"
        )

    DATA["api_url"] = url
    save_data()
    runtime["api_ok"] = False
    runtime["api_error"] = ""

    await update.effective_message.reply_text(
        f"✅ API updated.\n{url}"
    )


async def setmessage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)

    if not context.args:
        return await update.effective_message.reply_text(
            "Use: /setmessage 0"
        )

    try:
        n = int(context.args[0])
        if n < 0 or n > 9:
            raise ValueError
    except Exception:
        return await update.effective_message.reply_text(
            "❌ Number must be 0 to 9."
        )

    capture_tasks[update.effective_user.id] = {
        "kind": "message",
        "number": n,
    }

    await update.effective_message.reply_text(
        f"✏️ Send the message for number {n}."
    )


async def setheader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)

    capture_tasks[update.effective_user.id] = {
        "kind": "header",
    }

    await update.effective_message.reply_text(
        "✏️ Send the header.\n"
        "Use {period3} where you want the last 3 digits of the period."
    )


async def clearchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)

    count = 0

    for item in list(runtime["sent_ids"]):
        mid = item.get("id") if isinstance(item, dict) else item[0]
        try:
            await context.bot.delete_message(
                chat_id=TARGET_CHAT_ID,
                message_id=int(mid),
            )
            count += 1
        except Exception:
            pass

    runtime["sent_ids"] = []

    await update.effective_message.reply_text(
        f"🧹 Cleared {count} tracked bot messages."
    )


async def capture_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    user = update.effective_user
    if not user:
        return

    task = capture_tasks.get(user.id)
    if not task:
        return

    message = update.effective_message
    if not message:
        return

    if not (message.text or message.caption):
        await message.reply_text("❌ Please send text/message content.")
        return

    record = message_to_record(message)

    if task["kind"] == "message":
        n = int(task["number"])
        DATA["messages"][str(n)] = record
        save_data()
        del capture_tasks[user.id]

        await message.reply_text(f"✅ Message for {n} saved.")
        return

    if task["kind"] == "header":
        DATA["header"] = record
        save_data()
        del capture_tasks[user.id]

        await message.reply_text("✅ Header saved.")


# =========================
# RENDER HEALTH SERVER
# =========================
async def health(request):
    return web.json_response({
        "ok": True,
        "running": bool(DATA.get("running")),
        "phase": runtime.get("phase"),
        "period": runtime.get("last_period"),
        "result": runtime.get("last_result"),
    })


async def index(request):
    return web.Response(
        text="Bot 1 is running.",
        content_type="text/plain",
    )


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    log.info("Health server listening on 0.0.0.0:%s", PORT)
    return runner


# =========================
# APP STARTUP
# =========================
async def post_init(application: Application):
    global worker_task
    worker_task = asyncio.create_task(cycle_worker(application))
    log.info("Cycle worker task created.")


async def post_shutdown(application: Application):
    global worker_task
    if worker_task:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


def build_application():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("go", go))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("changename", changename))
    app.add_handler(CommandHandler("setmessage", setmessage))
    app.add_handler(CommandHandler("setheader", setheader))
    app.add_handler(CommandHandler("updateapi", updateapi))
    app.add_handler(CommandHandler("clearchat", clearchat))

    # Text/caption capture is deliberately after commands.
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.Caption()) & ~filters.COMMAND,
            capture_message,
        )
    )

    return app


def main():
    health_runner = asyncio.run(start_health_server())

    try:
        app = build_application()
        log.info("Starting Telegram polling...")
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
        )
    finally:
        try:
            asyncio.run(health_runner.cleanup())
        except Exception:
            pass


if __name__ == "__main__":
    main()
