#!/bin/sh
set -eu

APP_DIR="/app/blogsite"
DATA_DIR="/app/data"
DB_PATH="${DJANGO_DB_PATH:-$DATA_DIR/db.sqlite3}"
SEED_DB="/app/blogsite/db.sqlite3"

mkdir -p "$DATA_DIR" "$(dirname "$DB_PATH")"

if [ ! -f "$DB_PATH" ] && [ -f "$SEED_DB" ]; then
  cp "$SEED_DB" "$DB_PATH"
fi

cd "$APP_DIR"
python manage.py migrate --noinput
python manage.py collectstatic --noinput

set -- uvicorn blogsite.asgi:application \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "${UVICORN_WORKERS:-${GUNICORN_WORKERS:-1}}" \
  --proxy-headers \
  --forwarded-allow-ips="*" \
  --timeout-keep-alive "${UVICORN_TIMEOUT_KEEP_ALIVE:-5}" \
  --ws-ping-interval "${UVICORN_WS_PING_INTERVAL:-20}" \
  --ws-ping-timeout "${UVICORN_WS_PING_TIMEOUT:-20}"

if [ "${UVICORN_RELOAD:-${GUNICORN_RELOAD:-0}}" = "1" ]; then
  set -- "$@" --reload
fi

exec "$@"
