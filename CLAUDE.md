# CLAUDE.md

Guidance for Claude (or any AI coding agent) working in this repository. Read this before making changes — several of the rules below exist because of real bugs found and fixed during development, not just style preference.

## Project overview

A two-process Telegram bot: `bot.py` talks to the user (aiogram, Bot API), `worker.py` does the actual file processing (Telethon, a real user account — needed to get around the Bot API's small download-size limits). They **never call each other's code directly**; they only exchange small JSON messages through a private Telegram group (the "bridge"), defined in `core/protocol.py`.

Read `docs/architecture.md` for the full picture before making non-trivial changes. It documents the job lifecycle, the bridge protocol, and the archive folder-walk logic in more depth than inline comments do.

## Hard rules

- **Never assume a caption is safe to leak.** Every message posted to the bridge group carries a JSON payload as its caption/text (`Protocol.encode(...)`) so the other process can read it. When relaying anything from the bridge to an end user (`bot.py`'s `handle_bridge_message`), **always pass an explicit `caption=` override** — `aiogram`'s `copy_message` keeps the *original* caption when `caption=None`/omitted, which means the internal JSON leaks straight to the user if you forget this.
- **`Job` uses `slots=True`.** You cannot assign a new attribute that isn't declared as a dataclass field — it raises `AttributeError` at runtime, not at import time, so it's easy to miss until it actually executes. If you need a new piece of job-level state, add it as a real field in `core/job.py`.
- **A single archive `Job` produces many `OutputFile`s of different kinds.** Don't put per-output metadata (kind/title/artist/thumbnail/duration) on the shared `Job` — it gets overwritten as the archive processor walks from file to file, which was a real, hard-to-spot bug. Always attach it to the `OutputFile` entry returned by `job.add_output(...)`.
- **`job.options.rename_to` is neutralized during archive extraction** (`ArchiveProcessor` saves/restores it around the walk). If you touch that logic, keep the neutralization — otherwise every file in an archive collapses onto the same renamed output name.
- **Compare `MessageType` values, not raw strings**, when branching on `payload["type"]` — a previous version compared against `"JOB"` (wrong case) while the enum value was `"job"`, and the whole pipeline silently never processed a single job.
- **New processors register themselves** via `@register_processor("TYPE")` from `core/registry.py` (see `processors/*.py`). Don't add a new if/elif branch to `dispatcher/dispatcher.py`.
- **`.rar` handling shells out to the real `unrar` CLI, not the `rarfile` Python package.** `rarfile` is just a thin wrapper around `unrar`/`unar`/`bsdtar` and turned out to have real gaps around partial multi-volume sets; `processors/archive.py` now calls `unrar` directly (`v` to list, `x` to extract one member) and parses its text output. If you change this, keep testing against `unrar`'s actual output format (there's a regression risk here since output format can vary slightly by version — the parsing regex was built and tested against real `unrar 6.11` output). The real (non-free) `unrar` build must be installed and on `PATH` — Debian's default `unrar-free` package has known RAR5/multi-volume gaps and is not sufficient (see `Dockerfile`).
- **Multi-volume RAR archives are handled by `ArchiveProcessor.process_multivolume`**, a separate path from the normal single-file `process()`. It downloads volumes on demand (never all of them upfront) using a safety window sized from each entry's *real* compressed size vs. the volume size — never guessed — specifically so a volume is never deleted while some pending file might still need it. If you touch this, preserve that safety property; a wrong guess here means silent, unrecoverable data loss for the user, not just a retryable error.
- **`config.py` validates env vars at import time.** Any module that imports `config` (directly or transitively) will raise `RuntimeError` without `API_ID`/`API_HASH`/`BOT_TOKEN`/`GROUP_ID`/`SESSION_NAME` set in `.env`.
- **Telegram's thumbnail limit is 320×320.** `services/media.py`'s `generate_thumbnail`/`normalize_thumbnail` already scale down to fit; don't bypass them with a raw ffmpeg call that skips the `scale=` filter, or the thumbnail will be silently ignored by Telegram.
- **Watermark logo is resized per-video**, to fit within 1/5 width × 1/5 height of the *output* video (not the logo's native size) — see `LOGO_POSITIONS` and the `scale=` step in `services/media.py:convert_video`. This is intentional, not a leftover bug.
- **Always wrap Telegram sends in a retry for `telethon.errors.FloodWaitError`** when the call could plausibly run in a loop (per-file uploads, per-folder announcements). See the retry-once pattern in `worker.py` and `processors/archive.py` — copy it rather than reinventing it.
- **Never let one bad output file abort a whole batch.** The upload loop in `worker.py` deliberately catches per-file exceptions and keeps going — an archive can produce hundreds of files, and one corrupt one shouldn't cost the other 299.

## Conventions

- Async everywhere in `processors/`, `services/telegram.py`, `worker.py`, `bot.py`. Blocking calls (`zipfile`, `py7zr`) go through `loop.run_in_executor(...)`; `.rar` handling shells out to the real `unrar` CLI via `asyncio.create_subprocess_exec` (see the note below — not the `rarfile` Python package).
- User-facing strings (bot replies, button labels) are in Persian, matching the actual audience. Code, comments, docs, and identifiers are in English.

## Where things live

| If you're touching...                          | Look at                          |
|--------------------------------------------------|-----------------------------------|
| What happens when a user sends a file            | `bot.py` (`handle_private_message`) |
| The inline-keyboard flow (quality/options/target) | `bot.py` (`quality_keyboard`, `options_keyboard`, callbacks) |
| Per-user defaults (`/settings`)                   | `services/settings_store.py`, `bot.py` (`s:*` callbacks) |
| How a file actually gets converted/tagged         | `processors/video.py`, `processors/audio.py`, `processors/pdf.py`, `services/media.py`, `services/tags.py` |
| Archive extraction, folder walk, audio/PDF pairing, TOC | `processors/archive.py` |
| The bridge wire format                            | `core/protocol.py`, `core/constants.py` |
| Delivery (who gets what, caption building, TOC)   | `bot.py` (`handle_bridge_message`) |
