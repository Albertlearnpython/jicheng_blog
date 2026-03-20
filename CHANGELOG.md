# Changelog

## 2026-03-19

### Add per-chat Codex execution policy routing
- Added chat-specific Codex execution policy resolution so selected Feishu private chats can start new sessions with broader host access while other chats remain on restricted settings.
- Passed resolved sandbox and workdir settings into new `codex exec` sessions without changing resume command compatibility.
- Updated webhook tests and environment documentation for privileged chat allowlists and restricted fallback settings.

### Add database-backed credit-card report pushes
- Added SQLite persistence for daily credit-card snapshots and per-transaction records.
- Added a scheduled Feishu push command that can send daily, weekly, and monthly summaries from stored data.
- Switched the Linux `systemd` timer template to `23:30` and pointed it at the Feishu push workflow.
- Added Feishu push target settings so the scheduler can reuse the latest bot chat or an explicit receive id.

### Add daily QQ Mail credit-card spending report
- Added a QQ Mail IMAP credit-card report module that extracts same-day spending notifications, total spend, merchant data, and category summaries.
- Added a `manage.py credit_card_report` command that writes both JSON and text reports into the persistent `data/` directory.
- Added deployment templates for a daily Linux `systemd` timer that can run the report at `22:00`.
- Extended environment-variable documentation for QQ Mail and report output settings.

### Simplify linuxclaw-web into a Feishu-to-Codex bot
- Removed the previous blog, AI web chat, terminal, calendar, and repository-planning feature set.
- Rebuilt the service as a minimal Django webhook that only handles Feishu callbacks and health checks.
- Added persistent Codex thread storage per Feishu `chat_id`, so follow-up messages resume the same Codex session.
- Added a dedicated SSH-based Codex client that runs `codex exec` for new chats and `codex exec resume` for continued chats.
- Added a lightweight pinyin adapter for Chinese incoming messages before they are handed to Codex, to work around custom-provider CLI input encoding issues.
- Switched container startup to a smaller Gunicorn-based deployment path and trimmed Python dependencies to the minimum needed runtime set.
- Updated environment-variable docs, migration history, and README to match the new single-purpose service design.

## 2026-03-15

### Theme and AI chat refinement
- Switched the default site theme to `midnight`, while keeping `paper` as the alternate manual mode.
- Reworked the AI chat page into a roomier single-column conversation area with separate support panels below.
- Fixed browser chat submissions by ensuring the chat page sets a CSRF cookie before front-end requests.
- Improved AI chat error handling so timeout, config, and upstream failures return stable JSON messages.

### Docker deployment hardening
- Added production-oriented Django environment settings for proxy, HTTPS, HSTS, secure cookies, and timezone handling.
- Adjusted Docker Compose for Linux deployment by keeping only the persistent `data/` mount.
- Improved the container entrypoint for SQLite initialization and service startup.
- Added deployment notes for Linux Docker rollout.

## 2026-03-14

### Avatar update
- Replaced the temporary avatar placeholder with the user-provided WeChat avatar image.

### `ccf269e` Improve scroll performance and reduce animation overhead
- Reduced heavy fixed-background and glow effects to improve page scrolling smoothness.
- Moved the reading-progress updates onto `requestAnimationFrame` to lower scroll-time repaint pressure.
- Kept the animated text treatment while switching to a lighter implementation.

### `4e543dd` Personalize profile and animated site styling
- Replaced site profile text with Sun Bofu / Noah Brooks personal information.
- Renamed the blog to `孙伯符的博客`.
- Added avatar, contact, school, and city profile sections.
- Updated multiple pages with animated highlight styles and gradients.
- Added the `blogsite/blog/static/blog/noah-avatar.svg` asset.
- Synced README and tests with the site refresh.

### `5390da1` Improve layout spacing and UI sound feedback
- Increased spacing and improved layout density across the site.
- Added Web Audio click and toggle effects to key interactions.
- Added a sound toggle control.

### `bf6b55c` Redesign homepage and blog experience
- Rebuilt `/` as the personal homepage.
- Moved the blog listing to `/blog/`.
- Kept and reworked the article detail page and AI chat page.
- Rewrote the front-end structure around the reference style.
- Updated README with site feature notes.
