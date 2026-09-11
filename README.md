Bot 1 Web Service FINAL API 403 fix.

Build: pip install -r requirements.txt
Start: python bot.py

Environment:
BOT_TOKEN=your_bot_token
ADMIN_ID=8767998937
TARGET_CHAT_ID=8767998937
API_URL=https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json
POLL_SECONDS=2
NEXT_SEND_DELAY=90
REQUEST_TIMEOUT=15

Do not set PORT manually. Render supplies PORT.

The bot first requests the API directly. If Render receives HTTP 403 from the API, it automatically tries raw-response proxy fallbacks. No API folder/file is required.
