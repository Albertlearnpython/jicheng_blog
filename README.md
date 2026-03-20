# linuxclaw-web

`linuxclaw-web` is now a single-purpose Django service:

- Receive Feishu bot callbacks
- Convert Chinese user messages into stable pinyin prompt text when needed
- Forward each message to Codex CLI over SSH
- Reply back to Feishu with the Codex answer
- Persist one Codex thread per Feishu `chat_id`, so later messages continue the same session
- Resolve Codex execution policy per Feishu chat, so selected private chats can run with broader host access while other chats stay restricted

## Request flow

1. Feishu sends an `im.message.receive_v1` callback to `/api/feishu/events/`
2. The service extracts the text message and normalizes mention prefixes
3. Chinese messages are normalized and rewritten into pinyin prompt text when needed
4. The service looks up the saved `codex_thread_id` for that Feishu chat
5. It runs either:
   - `codex exec ...` for a new conversation, using the execution policy resolved for that Feishu chat
   - `codex exec resume <thread_id> ...` for a continued conversation
6. The returned reply is sent back to Feishu
7. The latest `codex_thread_id` is stored in SQLite for the next turn

## Kept features

- Feishu webhook verification
- Group-message mention gating
- Duplicate callback suppression by `event_id`
- Persistent Codex conversation continuity
- Per-chat Codex execution policy routing
- Docker deployment
- Daily QQ Mail credit-card spending report command
- Daily credit-card snapshots stored in SQLite
- Daily, weekly, and monthly Feishu push reports

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
- `CODEX_RESTRICTED_WORKDIR`
- `CODEX_MODEL`
- `CODEX_REASONING_EFFORT`
- `CODEX_SANDBOX`
- `CODEX_RESTRICTED_SANDBOX`
- `CODEX_PRIVILEGED_CHAT_IDS`
- `CODEX_PRIVILEGED_OPEN_IDS`
- `CODEX_TIMEOUT_SECONDS`
- `QQ_EMAIL_ADDRESS`
- `QQ_EMAIL_APP_PASSWORD`
- `QQ_IMAP_HOST`
- `QQ_IMAP_PORT`
- `CREDIT_CARD_REPORT_OUTPUT_DIR`
- `CREDIT_CARD_REPORT_DAILY_LAG_DAYS`
- `CREDIT_CARD_REPORT_WEEKLY_PUSH_WEEKDAY`
- `CREDIT_CARD_REPORT_MONTHLY_PUSH_DAY`
- `CREDIT_CARD_REPORT_FEISHU_RECEIVE_ID`
- `CREDIT_CARD_REPORT_FEISHU_RECEIVE_ID_TYPE`
- `CREDIT_CARD_REPORT_FEISHU_USE_LATEST_SESSION`

Reference values are in `.env.example`.

## Daily credit-card report

The repo now includes a Django management command that reads QQ Mail over IMAP, identifies daily credit-card spending emails, writes JSON/text summaries, and stores the daily snapshot plus transaction details in SQLite.

Manual run:

```bash
cd blogsite
python manage.py credit_card_report
```

Optional date override:

```bash
cd blogsite
python manage.py credit_card_report --date 2026-03-19
```

Output files default to:

- `/app/data/credit_card_reports/YYYY-MM-DD.json`
- `/app/data/credit_card_reports/YYYY-MM-DD.txt`

Stored database tables:

- `blog_creditcarddailysnapshot`
- `blog_creditcardtransactionrecord`

## Scheduled Feishu push

Use this command to sync the daily snapshot into SQLite and push reports to Feishu:

```bash
cd blogsite
python manage.py credit_card_push_reports
```

Behavior:

- Every day at `23:30`, it pushes the daily report for the previous day.
- Every Monday at `23:30`, it also pushes a weekly report for the previous 7 days.
- On day `1` of each month at `23:30`, it also pushes a monthly report for the previous month.
- If `CREDIT_CARD_REPORT_FEISHU_RECEIVE_ID` is empty, the service reuses the latest Feishu bot chat session automatically.

`deploy/linuxclaw-credit-card-report.service` and `deploy/linuxclaw-credit-card-report.timer` provide a host-side `systemd` template for this scheduler.

The bundled timer uses the previous day by default because the current mailbox pattern is a next-day digest mail (`每日信用管家`) that summarizes the previous day's credit-card spending.

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
