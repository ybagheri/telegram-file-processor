# Roadmap

## Done

- [x] Video conversion (multiple quality profiles) with watermark + thumbnail
- [x] Video thumbnail collage ("contact sheet" grid of frames), configurable count/columns — standalone or combined with a normal conversion/audio extraction
- [x] Oversized watermark logos are auto-downscaled before use (avoids ffmpeg hangs/slowness on small servers)
- [x] Optional access control: admin(s) approve users before they can use the bot, with an in-chat panel to add users
- [x] Access control: duration-based expiry (1 week/3 months/6 months/1 year/unlimited), renewing an existing user's expiry, enabling/disabling a user without deleting their record, manual name/username entry for numeric-id-only additions
- [x] Access control storage moved from a flat JSON file to SQLite (atomic writes, auto-migration from the old file)
- [x] Per-user `/settings` storage also moved from a flat JSON file to SQLite (same reasoning, same auto-migration pattern)
- [x] Confirmation prompt before disabling a user; a "remove/delete" panel action separate from disable
- [x] Periodic near-expiry reminder DM to users about to lose access
- [x] Inline per-row renew/disable/delete buttons directly in the "📋 لیست کاربران مجاز" list
- [x] Small pytest suite for the pure-logic modules (`access_store`, `settings_store`, `core/protocol.py`, `core/job_options.py`) — see `tests/`
- [x] Live end-to-end QA tool: a real Telegram user account that sends test media to the actual bot and clicks through its keyboards — see `qa-userbot/` (run by you, not from this sandbox)
- [x] Track users who `/start` without registering (separate `pending_users` table) and DM every admin once per new one; entry is removed once the admin actually registers them
- [x] Split `bot.py` into smaller handler modules (`utils/`, `keyboards/`, `models/`, `state.py`, `services/`, `handlers/` — aiogram 3 Router-based). `bot.py`: 2021 → 143 lines. See phases 7a-7g in `CLAUDE.md`'s change log.
- [x] Broadcast a message to everyone in `pending_users` — admin panel button, with a preview/confirm step and success/failure reporting
- [x] Watermark-only flow for plain photos (not just video)
- [x] Video → MP3 / M4A / voice note extraction
- [x] Audio re-tagging (title, artist, cover)
- [x] PDF compression (size-threshold based)
- [x] Archive extraction: zip / rar / 7z, password-protected included
- [x] Recursive, folder-ordered archive processing with audio/PDF pairing
- [x] Per-user settings (`/settings`): quality, watermark, upload-as, delivery target, caption, sort order, filename cleanup (EXCLUDE), watermark logo + 9-position picker
- [x] Delivery to a channel/group of the user's choice, not just back to themselves
- [x] Linked Table of Contents for archive folders (channel/group delivery only)
- [x] FloodWait-aware sending for large batches
- [x] Processor registry (plugin-style, no dispatcher edits needed for new types)

## Planned / ideas

- [ ] Simple pending-vs-registered conversion count/report for the admin
- [ ] Multiple saved delivery targets per user (currently one "custom" target at a time)
- [ ] Image processing (resize/compress/watermark) as its own processor
- [ ] OCR for scanned PDFs
- [ ] Subtitle extraction/embedding for video
- [ ] AI-generated captions/summaries for delivered files
- [ ] Per-file progress reporting for very large archives
- [ ] Optional virus/malware scanning before delivery
- [ ] Docker packaging (Dockerfile/docker-compose for the two long-running processes)
- [ ] Pytest coverage for `processors/archive.py`'s and `services/media.py`'s pure logic (not covered by the `bot.py` module-split effort, since it never needed to touch them)
