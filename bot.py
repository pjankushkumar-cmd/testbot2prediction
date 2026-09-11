import os
import json
import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
from aiohttp import web
from telegram import Update, MessageEntity
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8767998937"))
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", str(ADMIN_ID)))
API_URL_DEFAULT = "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"
POLL_SECONDS = max(1, int(os.getenv("POLL_SECONDS", "2")))
NEXT_SEND_DELAY = max(1, int(os.getenv("NEXT_SEND_DELAY", "90")))
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "15")))
# Only used when the origin returns HTTP 403/blocked to Render.
# The origin URL itself remains the primary source.
API_PROXY_URLS = [
    "https://r.jina.ai/",
    "https://api.allorigins.win/raw?url=",
    "https://corsproxy.io/?url=",
]
PORT = int(os.getenv("PORT", "10000"))
DATA_FILE = Path("data.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bot1")

DEFAULT_HEADER = "🚨 DM WIN GAME 🚨\n🔝 WINGO 1 MIN 🔝\nPERIOD NO ➡️ {period3}\nONLY ALL WALLET BET 📊"


def empty_record():
    return {"text": "", "entities": []}


def default_data():
    return {
        "messages": {str(i): empty_record() for i in range(10)},
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
        log.exception("data.json load failed")
    if not isinstance(d.get("messages"), dict):
        d["messages"] = {}
    for i in range(10):
        old = d["messages"].get(str(i), {})
        if isinstance(old, str):
            old = {"text": old, "entities": []}
        d["messages"][str(i)] = old
    if not isinstance(d.get("header"), dict):
        d["header"] = {"text": DEFAULT_HEADER, "entities": []}
    if not d.get("api_url"):
        d["api_url"] = API_URL_DEFAULT
    return d


DATA = load_data()


def save_data():
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(DATA, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)


# ---------- Telegram formatting ----------
def entity_to_dict(e: MessageEntity):
    out = {"type": e.type, "offset": e.offset, "length": e.length}
    for k in ("url", "language", "custom_emoji_id"):
        v = getattr(e, k, None)
        if v is not None:
            out[k] = v
    return out


def message_to_record(message):
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    return {"text": text, "entities": [entity_to_dict(e) for e in entities]}


def record_to_entities(record):
    result = []
    for x in (record or {}).get("entities", []):
        if not isinstance(x, dict):
            continue
        try:
            kwargs = {
                "type": x.get("type"),
                "offset": int(x.get("offset", 0)),
                "length": int(x.get("length", 0)),
            }
            for k in ("url", "language", "custom_emoji_id"):
                if x.get(k) is not None:
                    kwargs[k] = x[k]
            result.append(MessageEntity(**kwargs))
        except Exception:
            log.exception("Invalid entity: %r", x)
    return result


def record_text(record):
    return str((record or {}).get("text", ""))


# ---------- Runtime ----------
runtime = {
    "sent_ids": [],
    "cycle_target": None,
    "phase": "idle",
    "api_ok": False,
    "api_error": "",
    "last_period": None,
    "last_result": None,
    "confirmed_period": None,
    "confirmed_result": None,
    "confirmed_at": None,
    "api_http": None,
}

capture_tasks = {}
worker_task = None
health_runner = None


def normalize_period(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def next_period(p):
    p = normalize_period(p)
    if not p:
        return None
    try:
        return str(int(p) + 1).zfill(len(p))
    except Exception:
        return p


# ---------- API parser ----------
def find_rows(payload: Any):
    # This API is normally: {"data": {"list": [{"issueNumber":..., "number":...}]}}
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            lst = data.get("list")
            if isinstance(lst, list):
                return lst
        if isinstance(data, list):
            return data
        for key in ("list", "records", "result", "rows", "items", "history", "resultList"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    if isinstance(payload, list):
        return payload

    # Deep fallback for changed API wrappers.
    def walk(x):
        if isinstance(x, list):
            if any(isinstance(v, dict) for v in x):
                return x
            for v in x:
                got = walk(v)
                if got:
                    return got
        elif isinstance(x, dict):
            for v in x.values():
                got = walk(v)
                if got:
                    return got
        return []
    return walk(payload)


def get_field(row, names):
    if not isinstance(row, dict):
        return None
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    lower = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in lower and lower[name.lower()] is not None:
            return lower[name.lower()]
    return None


def parse_result_value(v):
    if v is None:
        return None
    s = str(v).strip()
    # number can sometimes arrive as "7,green" or "7|..."
    for sep in (",", "|", " "):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
    try:
        n = int(s)
        return n if 0 <= n <= 9 else None
    except Exception:
        return None


def parse_latest(payload):
    rows = find_rows(payload)
    # Prefer first API row. The endpoint returns newest first.
    for row in rows:
        if not isinstance(row, dict):
            continue
        p = get_field(row, ["issueNumber", "issue", "period", "periodNumber", "issueNo"])
        n = get_field(row, ["number", "result", "openNumber", "winNumber", "winningNumber"])
        p = normalize_period(p)
        n = parse_result_value(n)
        if p and n is not None:
            return p, n

    # Deep search as final fallback.
    def walk(x):
        if isinstance(x, dict):
            p = normalize_period(get_field(x, ["issueNumber", "issue", "period", "periodNumber", "issueNo"]))
            n = parse_result_value(get_field(x, ["number", "result", "openNumber", "winNumber", "winningNumber"]))
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


async def _decode_api_bytes(raw: bytes):
    """Decode the API even when it advertises application/octet-stream."""
    if not raw:
        raise ValueError("API returned an empty body")

    # Normal UTF-8 JSON (including UTF-8 BOM).
    text = raw.decode("utf-8-sig", errors="replace").strip()

    # Some gateways return JSON as a quoted JSON string, so allow a second decode.
    candidates = [text]
    if text.startswith('"') and text.endswith('"'):
        try:
            inner = json.loads(text)
            if isinstance(inner, str):
                candidates.append(inner)
        except Exception:
            pass

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # Extract a JSON object/array if a gateway prepended/appended text.
            starts = [candidate.find("{"), candidate.find("[")]
            starts = [x for x in starts if x >= 0]
            if not starts:
                continue
            start = min(starts)
            end_obj = candidate.rfind("}")
            end_arr = candidate.rfind("]")
            end = max(end_obj, end_arr)
            if end > start:
                try:
                    return json.loads(candidate[start:end + 1])
                except json.JSONDecodeError:
                    pass
    # Reader/proxy responses may wrap JSON in a markdown code fence.
    fenced = text.replace("```json", "").replace("```", "").strip()
    if fenced != text:
        try:
            return json.loads(fenced)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Invalid JSON body: {text[:500]}")


def _requests_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://draw.ar-lottery01.com/",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


async def _fetch_with_urllib(url):
    """Fallback HTTP client. It is deliberately independent of aiohttp parsing."""
    from urllib.request import Request, urlopen

    def do_request():
        req = Request(url, headers=_requests_headers(), method="GET")
        with urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            return int(getattr(r, "status", 200)), dict(r.headers.items()), r.read()

    return await asyncio.to_thread(do_request)


async def fetch_latest(session):
    base = DATA.get("api_url", API_URL_DEFAULT).strip() or API_URL_DEFAULT
    sep = "&" if "?" in base else "?"
    url = f"{base}{sep}t={int(time.time() * 1000)}"
    headers = _requests_headers()

    log.info("API GET: %s", url)

    # Primary: aiohttp. We read raw bytes and decode ourselves because this endpoint
    # is known to return JSON while advertising application/octet-stream.
    try:
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            allow_redirects=True,
        ) as resp:
            runtime["api_http"] = resp.status
            raw = await resp.read()
            ctype = resp.headers.get("Content-Type", "")
            cenc = resp.headers.get("Content-Encoding", "")
            log.info("API HTTP %s | content-type=%s | encoding=%s | bytes=%d", resp.status, ctype, cenc, len(raw))
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}")
            payload = await _decode_api_bytes(raw)
            period, result = parse_latest(payload)
            if period and result is not None:
                runtime.update({"api_ok": True, "api_error": "", "last_period": period, "last_result": result})
                log.info("API LATEST -> period=%s result=%s", period, result)
                return period, result
            raise ValueError("JSON received but data.list[0].issueNumber/number not found")
    except Exception as first_error:
        log.warning("AIOHTTP API attempt failed: %s", first_error)

    # Fallback: urllib. This handles unusual proxy/content-type behavior independently.
    second_error = None
    try:
        status, hdrs, raw = await _fetch_with_urllib(url)
        runtime["api_http"] = status
        log.info("API FALLBACK HTTP %s | content-type=%s | bytes=%d", status, hdrs.get("Content-Type", ""), len(raw))
        if status >= 400:
            raise RuntimeError(f"HTTP {status}")
        payload = await _decode_api_bytes(raw)
        period, result = parse_latest(payload)
        if period and result is not None:
            runtime.update({"api_ok": True, "api_error": "", "last_period": period, "last_result": result})
            log.info("API FALLBACK LATEST -> period=%s result=%s", period, result)
            return period, result
        raise ValueError("Fallback JSON received but data.list[0].issueNumber/number not found")
    except Exception as e:
        second_error = e
        log.warning("DIRECT API still failed: %s", e)

    # Render/cloud IPs can receive HTTP 403 even though the endpoint is public.
    # If that happens, fetch the SAME endpoint through a simple raw-response proxy.
    # No local API file/folder is required.
    # Render/cloud IPs can receive HTTP 403 even though the endpoint is publicly
    # reachable from normal clients. Try a server-side reader as a fallback.
    # Jina Reader accepts an absolute target URL directly after r.jina.ai/.
    proxy_jobs = []
    for proxy_base in API_PROXY_URLS:
        if proxy_base == "https://r.jina.ai/":
            proxy_jobs.append((proxy_base + base, True))
        else:
            proxy_jobs.append((proxy_base + quote(base, safe=""), False))

    for proxy_url, is_jina in proxy_jobs:
        try:
            if is_jina:
                proxy_headers = {
                    "User-Agent": headers["User-Agent"],
                    "Accept": "application/json, text/plain, */*",
                    "X-Engine": "direct",
                    "X-No-Cache": "true",
                    "X-Respond-With": "text",
                }
                safe_log_url = "https://r.jina.ai/<target-url>"
            else:
                proxy_headers = {"User-Agent": headers["User-Agent"], "Accept": "application/json, text/plain, */*"}
                safe_log_url = proxy_url.split("?url=")[0] + "?url=<encoded>"

            log.info("API PROXY GET: %s", safe_log_url)
            async with session.get(
                proxy_url,
                headers=proxy_headers,
                timeout=aiohttp.ClientTimeout(total=max(REQUEST_TIMEOUT, 20)),
                allow_redirects=True,
            ) as resp:
                raw = await resp.read()
                log.info("API PROXY HTTP %s | content-type=%s | bytes=%d", resp.status, resp.headers.get("Content-Type", ""), len(raw))
                if resp.status >= 400:
                    raise RuntimeError(f"PROXY HTTP {resp.status}")
                payload = await _decode_api_bytes(raw)
                period, result = parse_latest(payload)
                if period and result is not None:
                    runtime.update({"api_ok": True, "api_error": "", "last_period": period, "last_result": result})
                    runtime["api_http"] = 200
                    log.info("API PROXY LATEST -> period=%s result=%s", period, result)
                    return period, result
                raise ValueError("Proxy returned data but issueNumber/number was not found")
        except Exception as proxy_error:
            log.warning("API proxy failed: %s", proxy_error)

    runtime["api_ok"] = False
    runtime["api_error"] = (
        f"Direct: {type(first_error).__name__}: {first_error} | "
        f"Fallback: {type(second_error).__name__}: {second_error} | "
        "Proxy attempts failed"
    )
    log.error("API ERROR -> %s", runtime["api_error"])
    return None, None


# ---------- Telegram output ----------
async def send_record(app, record, period3=None):
    text = record_text(record)
    if not text:
        return None
    entities = record_to_entities(record)
    if period3 is not None and "{period3}" in text:
        text = text.replace("{period3}", str(period3))
        # Replacement changes offsets; safest fallback is plain text for header.
        entities = []
    try:
        return await app.bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text=text,
            entities=entities or None,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.warning("Formatted send failed: %s", e)
        try:
            return await app.bot.send_message(chat_id=TARGET_CHAT_ID, text=text, disable_web_page_preview=True)
        except Exception as e2:
            log.error("Plain send failed: %s", e2)
            return None


async def delete_tracked(app, keep_numbers=None):
    keep = set(keep_numbers or [])
    remaining = []
    for item in list(runtime["sent_ids"]):
        mid = item.get("id")
        num = item.get("number")
        if num in keep:
            remaining.append(item)
            continue
        try:
            await app.bot.delete_message(TARGET_CHAT_ID, int(mid))
        except Exception as e:
            log.debug("Delete %s skipped: %s", mid, e)
    runtime["sent_ids"] = remaining


async def send_cycle(app, target):
    await delete_tracked(app)
    p3 = str(target)[-3:].zfill(3)
    h = await send_record(app, DATA["header"], p3)
    if h:
        runtime["sent_ids"].append({"id": h.message_id, "number": None})
    count = 0
    for n in range(10):
        rec = DATA["messages"].get(str(n), {})
        if not record_text(rec):
            continue
        msg = await send_record(app, rec)
        if msg:
            runtime["sent_ids"].append({"id": msg.message_id, "number": n})
            count += 1
    runtime["cycle_target"] = str(target)
    runtime["phase"] = "waiting_result"
    log.info("CYCLE SENT -> target=%s configured_messages=%d", target, count)


async def cycle_worker(app):
    log.info("CYCLE WORKER STARTED")
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    connector = aiohttp.TCPConnector(limit=5, ttl_dns_cache=60, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        while True:
            try:
                if not DATA.get("running"):
                    runtime["phase"] = "stopped"
                    await asyncio.sleep(POLL_SECONDS)
                    continue

                period, result = await fetch_latest(session)
                if not period:
                    runtime["phase"] = "api_error"
                    await asyncio.sleep(POLL_SECONDS)
                    continue

                now = time.monotonic()

                if runtime["cycle_target"] is None:
                    target = next_period(period)
                    log.info("FIRST CYCLE -> latest=%s result=%s target=%s", period, result, target)
                    if target:
                        await send_cycle(app, target)
                    await asyncio.sleep(POLL_SECONDS)
                    continue

                target = str(runtime["cycle_target"])

                if runtime["phase"] == "waiting_result":
                    if str(period) == target:
                        log.info("RESULT CONFIRMED -> period=%s result=%s", target, result)
                        await delete_tracked(app, {int(result)})
                        runtime["confirmed_period"] = target
                        runtime["confirmed_result"] = result
                        runtime["confirmed_at"] = now
                        runtime["phase"] = "waiting_90_seconds"

                elif runtime["phase"] == "waiting_90_seconds":
                    at = runtime.get("confirmed_at")
                    if at is not None and now - float(at) >= NEXT_SEND_DELAY:
                        new_target = next_period(target)
                        log.info("90 SEC COMPLETE -> target=%s next=%s", target, new_target)
                        await send_cycle(app, new_target)

                await asyncio.sleep(POLL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("WORKER ERROR")
                await asyncio.sleep(POLL_SECONDS)


# ---------- Commands ----------
def is_admin(update):
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)

async def deny(update):
    await update.effective_message.reply_text("❌ Admin only.")

async def start(update, context):
    if not is_admin(update): return await deny(update)
    await update.effective_message.reply_text(
        "✅ Bot 1 ready.\n\n/go - start\n/stop - stop\n/status - status\n"
        "/setmessage 0 - then send the message\n/setheader - then send header\n"
        "/updateapi <URL>\n/changename <name>\n/clearchat"
    )

async def go(update, context):
    if not is_admin(update): return await deny(update)
    DATA["running"] = True
    save_data()
    runtime["cycle_target"] = None
    runtime["phase"] = "idle"
    runtime["confirmed_at"] = None
    runtime["confirmed_period"] = None
    runtime["confirmed_result"] = None
    runtime["api_error"] = ""
    runtime["api_ok"] = False
    runtime["api_http"] = None
    await update.effective_message.reply_text("▶️ Bot 1 started.")

async def stop(update, context):
    if not is_admin(update): return await deny(update)
    DATA["running"] = False
    save_data()
    runtime["phase"] = "stopped"
    await update.effective_message.reply_text("⏹ Bot 1 stopped.")

async def status(update, context):
    if not is_admin(update): return await deny(update)
    err = runtime.get("api_error") or "-"
    if len(err) > 500: err = err[:500]
    await update.effective_message.reply_text(
        f"Status: {'🟢 RUNNING' if DATA.get('running') else '🔴 STOPPED'}\n"
        f"API: {DATA['api_url']}\n"
        f"API state: {'🟢 OK' if runtime['api_ok'] else '🔴 WAIT/ERROR'}\n"
        f"HTTP: {runtime.get('api_http') or '-'}\n"
        f"Latest period: {runtime.get('last_period') or '-'}\n"
        f"Latest result: {runtime.get('last_result') if runtime.get('last_result') is not None else '-'}\n"
        f"Cycle target: {runtime.get('cycle_target') or '-'}\n"
        f"Phase: {runtime.get('phase') or '-'}\n"
        f"Error: {err}"
    )

async def changename(update, context):
    if not is_admin(update): return await deny(update)
    name = " ".join(context.args).strip()
    if not name: return await update.effective_message.reply_text("Use: /changename Your Bot Name")
    try:
        await context.bot.set_my_name(name=name)
        await update.effective_message.reply_text(f"✅ Bot name changed to: {name}")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ {e}")

async def updateapi(update, context):
    if not is_admin(update): return await deny(update)
    url = " ".join(context.args).strip()
    if not url: return await update.effective_message.reply_text("Use: /updateapi <URL>")
    DATA["api_url"] = url
    save_data()
    runtime["api_ok"] = False
    runtime["api_error"] = ""
    await update.effective_message.reply_text(f"✅ API updated.\n{url}")

async def setmessage(update, context):
    if not is_admin(update): return await deny(update)
    if not context.args: return await update.effective_message.reply_text("Use: /setmessage 0")
    try:
        n = int(context.args[0])
        if not 0 <= n <= 9: raise ValueError
    except Exception:
        return await update.effective_message.reply_text("❌ Number must be 0 to 9.")
    capture_tasks[update.effective_user.id] = {"kind": "message", "number": n}
    await update.effective_message.reply_text(f"✏️ Send the message for number {n}.")

async def setheader(update, context):
    if not is_admin(update): return await deny(update)
    capture_tasks[update.effective_user.id] = {"kind": "header"}
    await update.effective_message.reply_text("✏️ Send the header. Use {period3} for last 3 digits.")

async def clearchat(update, context):
    if not is_admin(update): return await deny(update)
    count = 0
    for item in list(runtime["sent_ids"]):
        try:
            await context.bot.delete_message(TARGET_CHAT_ID, int(item["id"]))
            count += 1
        except Exception:
            pass
    runtime["sent_ids"] = []
    await update.effective_message.reply_text(f"🧹 Cleared {count} tracked bot messages.")

async def capture_message(update, context):
    if not is_admin(update): return
    uid = update.effective_user.id
    task = capture_tasks.get(uid)
    if not task: return
    msg = update.effective_message
    if not msg or not (msg.text or msg.caption):
        return await msg.reply_text("❌ Please send text/message content.")
    rec = message_to_record(msg)
    if task["kind"] == "message":
        DATA["messages"][str(task["number"])] = rec
        save_data(); del capture_tasks[uid]
        return await msg.reply_text(f"✅ Message for {task['number']} saved.")
    DATA["header"] = rec
    save_data(); del capture_tasks[uid]
    await msg.reply_text("✅ Header saved.")


# ---------- Render Web Service ----------
async def index(request):
    return web.Response(text="Bot 1 is running.", content_type="text/plain")

async def health(request):
    return web.json_response({
        "ok": True,
        "running": bool(DATA.get("running")),
        "phase": runtime.get("phase"),
        "api_ok": runtime.get("api_ok"),
        "http": runtime.get("api_http"),
        "period": runtime.get("last_period"),
        "result": runtime.get("last_result"),
        "error": runtime.get("api_error", ""),
    })

async def start_health():
    global health_runner
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    health_runner = web.AppRunner(app)
    await health_runner.setup()
    site = web.TCPSite(health_runner, "0.0.0.0", PORT)
    await site.start()
    log.info("WEB SERVICE HEALTH LISTENING -> 0.0.0.0:%s", PORT)


async def post_init(application: Application):
    global worker_task
    await start_health()
    worker_task = application.create_task(cycle_worker(application), name="bot1-cycle-worker")
    log.info("API/CYCLE WORKER SCHEDULED")

async def post_shutdown(application: Application):
    global worker_task, health_runner
    if worker_task:
        worker_task.cancel()
        try: await worker_task
        except asyncio.CancelledError: pass
        worker_task = None
    if health_runner:
        await health_runner.cleanup()
        health_runner = None


def build_application():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")
    app = (Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("go", go))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("changename", changename))
    app.add_handler(CommandHandler("setmessage", setmessage))
    app.add_handler(CommandHandler("setheader", setheader))
    app.add_handler(CommandHandler("updateapi", updateapi))
    app.add_handler(CommandHandler("clearchat", clearchat))
    app.add_handler(MessageHandler((filters.TEXT | filters.Caption()) & ~filters.COMMAND, capture_message))
    return app


def main():
    app = build_application()
    log.info("STARTING BOT 1 WEB SERVICE")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False, close_loop=True)

if __name__ == "__main__":
    main()
