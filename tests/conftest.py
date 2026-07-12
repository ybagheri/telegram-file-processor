import os

# config.py validates these at import time. Tests never touch the real
# Telegram/network layer, so dummy values are enough to let every module
# import cleanly in CI or on a fresh machine with no .env file yet.
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "test_hash")
os.environ.setdefault("BOT_TOKEN", "123:test-token")
os.environ.setdefault("GROUP_ID", "-100123456789")
os.environ.setdefault("SESSION_NAME", "test_session")
