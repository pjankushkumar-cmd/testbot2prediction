# Bot 1 — Web Service FINAL4

Render Web Service only.

Build command:
`pip install -r requirements.txt`

Start command:
`python bot.py`

Environment variables:
- BOT_TOKEN
- ADMIN_ID=8767998937
- TARGET_CHAT_ID=8767998937
- API_URL=https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json
- POLL_SECONDS=2
- NEXT_SEND_DELAY=90
- REQUEST_TIMEOUT=15

## 403 fix

The bot now tries, in order:
1. Normal aiohttp request with browser/origin headers.
2. Chrome-impersonated request using `curl_cffi`.
3. urllib fallback.
4. Server-side raw-response proxy fallbacks.

The proxy target includes a cache-buster so an old cached result is not reused. JSON parsing also handles JSON wrapped by a reader/proxy.

`/status` reports the real HTTP status and the latest period/result. If every route is blocked, the bot reports the exact failure instead of showing fake data.
