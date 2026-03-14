# Feishu Bot MVP

This project now includes a Django endpoint for a Feishu bot callback:

- `POST /blog/api/feishu/events/`

The bot flow is:

1. Receive a Feishu text message.
2. Build a remote repository inspection plan with OpenAI.
3. Read a small set of files from the Linux server over SSH.
4. Draft a safe change plan.
5. Wait for `/approve <token>` before applying edits.
6. Apply changes, run allowed tests, and send the result back to Feishu.

## Required environment variables

OpenAI:

- `OPENAI_API_KEY`
- `OPENAI_API_URL`
- `OPENAI_MODEL`

Feishu:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_VERIFICATION_TOKEN`
- `FEISHU_BASE_URL` (optional, default `https://open.feishu.cn`)
- `FEISHU_REQUIRE_GROUP_MENTION` (optional, default `true`)
- `FEISHU_CALENDAR_ID` (optional, use a specific calendar instead of the app primary calendar)
- `FEISHU_CALENDAR_TIMEZONE` (optional, default `Asia/Shanghai`)
- `FEISHU_CALENDAR_AUTO_INVITE_SENDER` (optional, default `true`)

Remote SSH:

- `REMOTE_SSH_HOST`
- `REMOTE_SSH_PORT` (optional, default `22`)
- `REMOTE_SSH_USER`
- `REMOTE_SSH_PASSWORD` or `REMOTE_SSH_IDENTITY_FILE`
- `REMOTE_SSH_IDENTITY_FILE` (optional)
- `REMOTE_PROJECT_ROOT`
- `REMOTE_REQUIRE_CLEAN_WORKTREE` (optional, default `true`)
- `REMOTE_ALLOWED_TEST_PREFIXES` (comma-separated, optional)

Docker deployment:

- `docker compose up -d --build`
- Default host port is `80`

## Feishu app setup

Suggested permissions for the app:

- Event subscription for `im.message.receive_v1`
- Permission to send messages

Set the event callback URL to:

- `https://<your-domain>/blog/api/feishu/events/`

For this MVP, keep the callback payload unencrypted. The endpoint currently validates the verification token when `FEISHU_VERIFICATION_TOKEN` is configured and does not decrypt encrypted payloads.

## Message commands

- `/help`
- `/chat <question>`
- `/repo <request>`
- `/calendar create 2026-03-15 14:00 15:00 标题 | 可选描述`
- `/calendar list [YYYY-MM-DD]`
- `/calendar delete <event_id>`
- `/approve <token>`
- `/reject <token>`
- `/status <token>`

Any other text message is auto-routed:

- General Q&A is answered directly
- Repo or server work generates a plan first and waits for approval
- Calendar commands are handled explicitly and do not trigger repo inspection

## Calendar notes

- The current implementation uses the app calendar identified by `FEISHU_CALENDAR_ID`.
- If `FEISHU_CALENDAR_ID` is empty, the bot resolves the app primary calendar automatically.
- When `FEISHU_CALENDAR_AUTO_INVITE_SENDER=true`, the message sender is automatically added as an attendee, so the created event can surface in the sender's Feishu calendar.
- Calendar list and delete operations apply to that same bot/app calendar.

## Operational notes

- The bot ignores non-text messages.
- In group chats, the bot ignores messages without mentions when `FEISHU_REQUIRE_GROUP_MENTION=true`.
- Existing remote worktree changes block execution by default.
- File edits are rolled back if an allowed test command fails.
