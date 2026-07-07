FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    BROWSER_STATE_PATH=/data/browser_state.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium-headless-shell \
    && rm -rf /var/lib/apt/lists/*

COPY app.py charger.py emailer.py ./

RUN mkdir -p /data
VOLUME /data

EXPOSE 80

# Single worker + threads: the app serializes charging runs with a lock,
# which only works within one process.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${WEBHOOK_PORT:-80} --workers 1 --threads 4 --timeout 600 app:app"]
