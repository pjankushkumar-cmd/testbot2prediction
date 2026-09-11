# Bot 1 — Web Service FINAL3

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
- REQUEST_TIMEOUT=12

Do not set PORT manually. Render provides PORT.

The bot first requests the configured API directly. If Render receives HTTP 403 from the origin, it automatically tries a server-side reader fallback, then other raw-response proxies. It parses the same JSON and looks for data.list[0].issueNumber and data.list[0].number (with defensive fallbacks). No local API file or folder is required.
