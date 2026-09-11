# Bot 1

## Render
Service: Background Worker

Build:
`pip install -r requirements.txt`

Start:
`python bot.py`

Environment:
- `BOT_TOKEN` = Telegram bot token
- `ADMIN_ID` = `8767998937`
- `API_URL` = `https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json`
- `POLL_SECONDS` = `2`
- `TARGET_CHAT_ID` = `8767998937`

## Commands
`/start`
`/go`
`/stop`
`/status`
`/changename <name>`
`/clearchat`
`/setmessage <0-9> <message>`
`/setheader <text>`
`/updateapi <URL>`

For the period header, use `{period3}` where the last 3 digits of the API period should appear.

Only `ADMIN_ID` can use admin commands.

Telegram bots cannot delete arbitrary old chat history. `/clearchat` deletes messages that this bot has recorded as sent.
