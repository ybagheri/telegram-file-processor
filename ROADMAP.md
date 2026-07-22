# Roadmap

## Done

- [x] Video conversion (multiple quality profiles) with watermark + thumbnail
- [x] Video thumbnail collage ("contact sheet" grid of frames), configurable count/columns — standalone or combined with a normal conversion/audio extraction
- [x] Oversized watermark logos are auto-downscaled before use (avoids ffmpeg hangs/slowness on small servers)
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

- [ ] Multiple saved delivery targets per user (currently one "custom" target at a time)
- [ ] Image processing (resize/compress/watermark) as its own processor
- [ ] OCR for scanned PDFs
- [ ] Subtitle extraction/embedding for video
- [ ] AI-generated captions/summaries for delivered files
- [ ] Per-file progress reporting for very large archives
- [ ] Optional virus/malware scanning before delivery
- [ ] Docker packaging (Dockerfile/docker-compose for the two long-running processes)
