# Bot 1 — Render Web Service

Use Render **Web Service** (not Background Worker).

Build Command:
`pip install -r requirements.txt`

Start Command:
`python bot.py`

Environment variables:
`BOT_TOKEN` = your BotFather token
`ADMIN_ID` = 8767998937
`TARGET_CHAT_ID` = 8767998937
`API_URL` = https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json
`POLL_SECONDS` = 2
`NEXT_SEND_DELAY` = 90
`REQUEST_TIMEOUT` = 12

Do not set PORT manually; Render supplies PORT automatically.

Commands:
`/start`
`/go`
`/stop`
`/status`
`/setmessage 0` then send the message
`/setheader` then send the header; use `{period3}`
`/updateapi <URL>`
`/changename <name>`
`/clearchat`

The service exposes `/health` for Render's web-service port check.
