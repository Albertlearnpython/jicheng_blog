# linuxclaw-web

`linuxclaw-web` is now a single-purpose Django service:

- Receive Feishu bot callbacks
- Convert Chinese user messages into stable pinyin prompt text when needed
- Forward each message to Codex CLI over SSH
- Reply back to Feishu with the Codex answer
- Persist one Codex thread per Feishu `chat_id`, so later messages continue the same session

## Request flow

1. Feishu sends an `im.message.receive_v1` callback to `/api/feishu/events/`
2. The service extracts the text message and normalizes mention prefixes
3. Chinese messages are normalized and rewritten into pinyin prompt text when needed
4. The service looks up the saved `codex_thread_id` for that Feishu chat
5. It runs either:
   - `codex exec ...` for a new conversation
   - `codex exec resume <thread_id> ...` for a continued conversation
6. The returned reply is sent back to Feishu
7. The latest `codex_thread_id` is stored in SQLite for the next turn

## Kept features

- Feishu webhook verification
- Group-message mention gating
- Duplicate callback suppression by `event_id`
- Persistent Codex conversation continuity
- Docker deployment

## Removed from the old project

- Blog pages
- AI Chat web UI
- Remote terminal
- Calendar integration
- Repository planning and remote code-change flows
- Static theme assets and front-end pages

## Environment variables

Required:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `CODEX_SSH_HOST`
- `CODEX_SSH_USER`
- `CODEX_SSH_PASSWORD` or `CODEX_SSH_IDENTITY_FILE`

Common runtime settings:

- `FEISHU_VERIFICATION_TOKEN`
- `FEISHU_REQUIRE_GROUP_MENTION`
- `CODEX_BIN`
- `CODEX_WORKDIR`
- `CODEX_MODEL`
- `CODEX_REASONING_EFFORT`
- `CODEX_TIMEOUT_SECONDS`

Reference values are in `.env.example`.

## Local run

```bash
cd blogsite
python manage.py migrate
python manage.py runserver
```

Health check:

- `http://127.0.0.1:8000/`

Feishu callback endpoint:

- `POST /api/feishu/events/`

## Docker run

```bash
docker compose up -d --build
```

The container starts Django with Gunicorn and stores SQLite data in `./data`.

## Tests

```bash
cd blogsite
python manage.py test
```
