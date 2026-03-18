#!/bin/sh
set -eu

APP_DIR="/app/blogsite"
DATA_DIR="/app/data"
DB_PATH="${DJANGO_DB_PATH:-$DATA_DIR/db.sqlite3}"

mkdir -p "$DATA_DIR" "$(dirname "$DB_PATH")"

cd "$APP_DIR"
python manage.py migrate --noinput

exec gunicorn blogsite.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-1}" \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout "${GUNICORN_TIMEOUT:-180}" \
  --access-logfile - \
  --error-logfile -
