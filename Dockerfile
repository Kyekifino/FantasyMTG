FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FANTASYMTG_DB_PATH=/data/fantasymtg.db

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 1000 bot \
    && mkdir -p /data && chown bot:bot /data

USER bot
VOLUME ["/data"]
CMD ["fantasymtg-discord"]
