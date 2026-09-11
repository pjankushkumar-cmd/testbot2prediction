# Bot 1 - Render Web Service

## Build Command
pip install -r requirements.txt

## Start Command
python bot.py

## Environment Variables
BOT_TOKEN=YOUR_BOT_TOKEN
ADMIN_ID=8767998937
TARGET_CHAT_ID=8767998937
API_URL=https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json
POLL_SECONDS=2
NEXT_SEND_DELAY=90
REQUEST_TIMEOUT=12

Do not manually set PORT on Render. Render supplies it.

## Flow
1. /go starts the cycle worker.
2. Bot fetches the latest completed API period.
3. Bot sends messages for the next period.
4. When that target period appears in the API, its result is detected.
5. All other number messages are deleted; only the matching number message remains.
6. After 90 seconds, the next period cycle is sent.
7. API failures are logged and retried without crashing the bot.

## Important
If the API response changes, Render logs now show:
- API request
- HTTP status
- content type
- response size
- detected period/result
- a sample when parsing fails
