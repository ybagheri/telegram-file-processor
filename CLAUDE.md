# CLAUDE.md

Guidance for Claude (or any AI coding agent) working in this repository. Read this before making changes — several of the rules below exist because of real bugs found and fixed during development, not just style preference.

## Project overview

A two-process Telegram bot: `bot.py` talks to the user (aiogram, Bot API), `worker.py` does the actual file processing (Telethon, a real user account — needed to get around the Bot API's small download-size limits). They **never call each other's code directly**; they only exchange small JSON messages through a private Telegram group (the "bridge"), defined in `core/protocol.py`.

Read `docs/architecture.md` for the full picture before making non-trivial changes. It documents the job lifecycle, the bridge protocol, and the archive folder-walk logic in more depth than inline comments do.

## Hard rules

- **Access control is opt-in.** `is_authorized()` in `bot.py` treats an empty `Telegram.ADMIN_IDS` as "no access control configured" — everyone is allowed. Don't change this default; a server with no admin set and access control forced on would lock out the operator with no way to add anyone.
- **`services/access_store.py` is SQLite-backed (`config_data/access.db`), not a hand-written JSON file.** A user's record has `expires_at` (Unix timestamp, `None` = unlimited) and `active` (bool) in addition to identity fields — access is granted only when the user exists, `active` is true, *and* they're not expired (`AccessStore.is_authorized`). On first run against an old JSON-file deployment, `AccessStore.__init__` auto-migrates `authorized_users.json` into the DB once (only if the table is empty) and renames the old file to `authorized_users.json.migrated` as a backup — don't delete that migration step, and don't add a second, competing migration path.
- **Never delete a user's row just to disable them.** Use `access_store.set_active(user_id, False)` — it preserves `expires_at`/history so re-enabling (`set_active(..., True)`) or renewing (`update_expiry`) later doesn't need the admin to re-enter everything. `access_store.remove()` is for genuinely deleting a record and is a separate, more destructive action.
- **`bot.py`'s admin "add/renew/disable a user" flows are multi-step and share two bits of transient state**: `awaiting_state[admin_id]` (which step we're on) and `admin_flow[admin_id]` (scratch data collected so far — chosen duration, target id, typed name). Both are keyed by the *admin's* user_id, not the target's, and both must be cleared together (see `/cancel` and the terminal branch of each flow) or a later flow will silently pick up stale data from an abandoned one.
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
| Who's allowed to use the bot, add/renew/disable a user | `services/access_store.py` (SQLite), `bot.py` (`admin:*` callbacks, `admin_add_user`/`admin_add_name`/`admin_add_username`/`admin_renew_target`/`admin_toggle_target` states) |

## Change log

Dated entries for anything user-facing or architecturally significant, newest first. Keep this updated when you make a non-trivial change — it's how the next session (human or AI) knows what already happened without re-reading every diff.

- **2026-07-27 — Access control: duration-based expiry, active/inactive, manual name/username, SQLite backend.**
  Admin panel gained three things: (1) adding a user now asks for a duration first (1 week / 3 months / 6 months / 1 year / unlimited) via inline keyboard, stored as `expires_at`; (2) "⏳ تمدید / تغییر انقضا" updates just the expiry on an existing user; (3) "🚫 فعال/غیرفعال کردن کاربر" toggles `active` without deleting the record. When a user is identified by a manually-typed numeric id (as opposed to a real Telegram forward), the admin is now asked for name/username by hand (both optional, `-` to skip) since Telegram never discloses them for a bare id. `is_authorized`/`not_authorized_text` are now expiry/active-aware and give the user a specific reason (disabled vs. expired vs. never authorized). The store itself moved from a hand-written JSON file to SQLite (`config_data/access.db`) for atomic writes; a legacy `authorized_users.json` is auto-migrated once on startup and renamed to `.migrated`. See `services/access_store.py` and the `admin_flow`/`awaiting_state` multi-step flows in `bot.py`.
  Planned next (not yet done, in priority order): confirmation prompt before disabling a user + a real "remove/delete" panel action; a periodic near-expiry reminder DM; inline per-row renew/disable buttons directly in the user list; splitting `bot.py` into smaller handler modules; a small pytest suite for the pure-logic modules (`access_store`, `core/protocol.py`, `core/job_options.py`).
