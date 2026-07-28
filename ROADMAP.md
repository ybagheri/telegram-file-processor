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

- [ ] Split `bot.py` into smaller handler modules (admin / settings / file-flow)
- [ ] Small pytest suite for the pure-logic modules (`access_store`, `core/protocol.py`, `core/job_options.py`)
- [ ] Multiple saved delivery targets per user (currently one "custom" target at a time)
- [ ] Image processing (resize/compress/watermark) as its own processor
- [ ] OCR for scanned PDFs
- [ ] Subtitle extraction/embedding for video
- [ ] AI-generated captions/summaries for delivered files
- [ ] Per-file progress reporting for very large archives
- [ ] Optional virus/malware scanning before delivery
- [ ] Docker packaging (Dockerfile/docker-compose for the two long-running processes)
