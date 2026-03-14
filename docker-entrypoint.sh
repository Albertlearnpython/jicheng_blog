#!/bin/sh
set -eu

APP_DIR="/app/blogsite"
DATA_DIR="/app/data"
DB_PATH="${DJANGO_DB_PATH:-$DATA_DIR/db.sqlite3}"
SEED_DB="/app/blogsite/db.sqlite3"

mkdir -p "$DATA_DIR"

if [ ! -f "$DB_PATH" ] && [ -f "$SEED_DB" ]; then
  cp "$SEED_DB" "$DB_PATH"
fi

cd "$APP_DIR"
python manage.py migrate --noinput
python manage.py collectstatic --noinput

set -- gunicorn blogsite.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-120}"

if [ "${GUNICORN_RELOAD:-0}" = "1" ]; then
  set -- "$@" --reload
fi

exec "$@"
