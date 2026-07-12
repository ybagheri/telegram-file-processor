FROM python:3.11-slim

# ffmpeg is required by services/media.py (video/audio conversion,
# thumbnails). rarfile also shells out to an external unrar/unar binary.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        unrar-free \
        libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Actual command (bot.py vs worker.py) is set per-service in
# docker-compose.yml, since these are two independent long-running
# processes that only ever talk through the Telegram bridge group.
CMD ["python", "bot.py"]
