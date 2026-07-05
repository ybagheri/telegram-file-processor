# Telegram File Processor

سیستم پردازش فایل تلگرام (Video, Audio, PDF, Archive)

## معماری
- `bot.py` → ارتباط با کاربر
- `worker.py` → پردازش (UserBot)
- ارتباط فقط از طریق Bridge Group با Protocol JSON

## راه‌اندازی
1. `.env` را از `.env.example` بسازید
2. `pip install -r requirements.txt`
3. `python bot.py` و `python worker.py`

## توسعه
بعد از هر تغییر: `git commit` و `git push`
