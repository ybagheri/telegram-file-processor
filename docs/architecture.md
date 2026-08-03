# Architecture

## Module map (`bot.py`'s package structure)

`bot.py` used to be one ~2000-line file. It's now a ~140-line entrypoint that only creates the `Dispatcher`, registers seven aiogram 3 `Router`s in a specific order (see the big comment at the top of `bot.py` for why the order matters — every specific-filter router before the catch-all), and runs `main()`. Everything else lives in:

| Package/file | What's there |
|---|---|
| `utils/access_control.py` | `is_admin`/`is_authorized`/`not_authorized_text`, expiry helpers, `track_pending_user_if_needed` |
| `keyboards/` | Every inline-keyboard builder (`admin.py`, `settings.py`, `files.py`, `photo.py`), plus `constants.py` for shared label data |
| `models/`, `state.py` | `PendingFile`/`PendingPhoto` dataclasses and every shared in-process dict (`pending_files`, `awaiting_state`, `admin_flow`, etc.) — a single source of truth every handler module imports |
| `services/` | `access_store.py`, `settings_store.py`, `pending_user_store.py` (all SQLite), `target_resolver.py`, `expiry_reminder.py` — the non-Telegram-routing business logic |
| `handlers/` | One aiogram Router per domain: `admin.py`, `settings.py`, `files.py`, `photo.py`, `bridge.py`, `core.py` (`/start`, `/cancel`, and the catch-all `handle_private_message`) |

`handle_awaited_input()` (the free-text "what state is this user in" dispatcher) lives in `handlers/core.py`, composing `handle_admin_awaited_input`/`handle_settings_awaited_input`/`handle_file_awaited_input` from the other three handler modules by state-name prefix — deliberately placed there rather than in `bot.py`, so no handler module ever has to import `bot.py` back.

Full history of how this split happened, in order, is in `CLAUDE.md`'s change log under phases 7a–7g — worth reading before doing a large refactor near this code again, since phase 7d found a genuine aiogram router-precedence bug the hard way (a broad catch-all handler beats a more specific sub-router unless it's *also* on its own sub-router, included after the specific ones).

## Why two processes?

Telegram's Bot API caps file downloads at 20MB (and uploads it handles itself, capped around 50MB for bots vs. 2-4GB for user accounts). To handle real-world course archives (hundreds of MB), `worker.py` logs in as a **real user account** via Telethon, which has none of those limits. `bot.py` stays a normal bot (via aiogram) so it can be added to chats, respond to commands, etc.

They run as separate OS processes and communicate **only** by posting messages into a private Telegram group both accounts are members of — the "bridge group". Nothing is shared in memory; there's no RPC, no shared database. This means either process can restart independently without the other needing to know.

## The bridge protocol

`core/protocol.py` defines `Protocol.encode`/`decode`: every bridge message is a JSON object with `project`, `version`, and `type` (see `core.constants.MessageType`), plus type-specific fields. `decode` rejects anything that doesn't match `project`/`version`, so the bridge group can safely be used for nothing else without the two processes getting confused by unrelated messages.

| Type               | Direction        | Purpose |
|--------------------|------------------|---------|
| `job`              | bot → worker     | A new file to process (carries `message_id` of the forwarded file, `file_type`, `options`) |
| `password_request`  | worker → bot     | An archive needs a password; bot relays to the user and remembers `job_id` |
| `password_response` | bot → worker     | The user's reply, resolved via `core/password_broker.py`'s in-memory future |
| `result`           | worker → bot     | Either a status ping (filenames only) or, when it carries an actual uploaded file, the thing to relay |
| `error`            | worker → bot     | Something failed; relayed as plain text to the user |
| `folder`           | worker → bot     | An archive folder was entered; relayed as text, and its resulting message_id is tracked for the TOC |
| `done`             | worker → bot     | Every output for this job has been uploaded; triggers the "✅ all done" notice and (if any folders were tracked and the destination isn't the user's own chat) the linked Table of Contents |

**Why `job`'s file and its JSON metadata are two separate bridge messages:** `bot.py` forwards the original file into the bridge group (getting back a `message_id`), then separately posts the JSON job description referencing that `message_id`. `worker.py`'s Telethon event handler only reacts to text messages (`event.message.message`), then fetches the *actual* file message by `message_id` via `client.get_messages(...)`. Don't try to read `event.message.media` directly — that event fired for the JSON text message, which has no media.

## Job lifecycle (`core/job.py`)

A `Job` is created fresh per bridge `job` message, holds `user_id`, `file_type`, `options` (a `JobOptions`), and computed paths (`input_dir`, `output_dir`, `extracted_dir`, `thumbs_dir` — all under `downloads/job_<id>/`). `job.cleanup()` deletes the whole directory once the job is done, success or failure.

**`OutputFile`, not a flat `Path` list.** `job.output_files` is a list of `OutputFile` entries, each with its own `kind` (`"video"` / `"audio"` / `"voice"` / `"document"`), `title`, `artist`, `duration`, `width`, `height`, `thumbnail`, and `folder`. This exists because a single archive job can produce a mix of video, audio, and PDF outputs — putting that metadata on the shared `Job` object meant it got clobbered as processing moved from file to file. `worker.py`'s upload loop reads each entry's own `kind` to decide `force_document`/`voice_note`/attributes — it does not infer anything from `job.file_type` (which, for an archive, is just `"ARCHIVE"` and tells you nothing about any individual output).

## Dispatcher & processor registry

`dispatcher/dispatcher.py` doesn't hardcode file types. Each processor module calls `@register_processor("VIDEO")` (etc., from `core/registry.py`) on its class at import time; `Dispatcher` just looks the class up by `job.file_type` and lazily instantiates it. Adding a new file type is: write the processor, decorate it, add one import line to `dispatcher.py`.

## Archive processing (`processors/archive.py`)

**Single archive, `.zip`/`.rar`:** streamed, not bulk-extracted. The file list (with sizes/dates) is read straight from the archive's header — no extraction needed just to see the structure — and a folder tree is built from that listing alone. Then the tree is walked: each folder is **announced** (`folder` message, tracked for the TOC) before its files; within a folder, files are split into audio / PDF / everything-else, each sorted per `sort_mode`/`sort_order`, with a PDF whose name or leading number matches an audio file moved to sit immediately after it. Each file is extracted **one at a time**, immediately handed to the same `Dispatcher` (temporarily repointing `job.input_file`/`job.file_type`/`job.original_name`; `job.current_extract_folder` keeps outputs grouped correctly), then the raw extracted copy is deleted right away. Peak disk usage is roughly "the archive + one file," not "the archive + the whole extracted tree."

**`.zip`** uses the stdlib `zipfile` module directly. **`.rar`** shells out to the real `unrar` CLI (`unrar v` to list, `unrar x <archive> "<path>" <dest>/` to extract one member) — not the `rarfile` Python package, which turned out to have real gaps with partial multi-volume sets. See the note in `CLAUDE.md` before touching this.

**`.7z`** is the exception: it's extracted in bulk up front like before, because 7z's default solid compression means individual files usually can't be pulled out without decompressing everything before them in the same block anyway.

**Multi-volume RAR** (`ArchiveProcessor.process_multivolume`) is a separate entry point, used when the user explicitly declares an archive as multi-part in the per-file options screen and sends each `.partNN.rar` as its own message. Since the whole set can be too large to fit on disk at once, volumes are downloaded on demand: volume 1 is always kept (needed to list/open the set), plus a sliding window of the most recent other volumes. The window size isn't guessed — it's computed from each entry's *real* compressed size vs. the actual size of volume 1, so it's always large enough that a file spanning several volumes will find all of them present before any are evicted. When extracting a given file fails (most likely because it needs a volume not yet downloaded), the next sequential volume is fetched and the attempt retried, until it succeeds or every declared part has been downloaded.

## Settings (`services/settings_store.py`)

A SQLite table (`config_data/settings.db`, one row per `user_id`) holding defaults: quality, watermark on/off + logo path/position, upload-as, delivery target, caption, sort mode/order, and the EXCLUDE filename-cleanup text. `bot.py` copies these into a job's `options` dict when a file is first received, and the per-file inline-keyboard flow can override any of them before the job is sent to the bridge. Was a flat JSON file (`user_settings.json`) originally; auto-migrated once into the DB on first run (same pattern as `access_store`, see its note below).

## Delivery & the caption rule

`bot.py`'s `handle_bridge_message` is the only place that talks to the actual destination chat (the user, or their configured channel/group). Every `copy_message_to(...)` call **must** pass an explicit `caption=` — Telegram's `copyMessage` keeps the original caption when it's omitted, and the "original caption" here is always the bridge's internal JSON. The delivered caption is built as `<filename without extension>` plus, if the user configured one in `/settings`, their fixed caption text appended below it.

## Access control (`services/access_store.py`)

Optional and off by default (see the "opt-in" hard rule in `CLAUDE.md`). When `Telegram.ADMIN_IDS` is non-empty, every non-admin user must have a row in the SQLite table `authorized_users` (`config_data/access.db`) that is both `active` and not expired.

A row is: `user_id`, `label` (display fallback), `name`/`username` (either from a real Telegram forward, or typed by hand by the admin when only a numeric id was given), `added_by`, `added_at`, `expires_at` (Unix timestamp or `NULL` for unlimited), `active` (bool). `AccessStore.is_authorized()` = exists AND active AND (`expires_at` is `NULL` OR in the future).

Three admin-panel flows, each a small state machine over `awaiting_state[admin_id]` (which step) and `admin_flow[admin_id]` (scratch data collected so far — both keyed by the *admin's* id, cleared together on completion or `/cancel`):

- **Add** (`admin:add_user` → duration keyboard → `admin_add_user` state): pick a duration (1 week / 3 months / 6 months / 1 year / unlimited) first, then identify the target by forwarding one of their messages (Telegram supplies name/username automatically) or by typing their numeric id (in which case the bot asks for name/username by hand next, each optional).
- **Renew** (`admin:renew_user` → `admin_renew_target` state → duration keyboard again, this time with the target id embedded in the callback data): identify an *existing* user, then just replace `expires_at`.
- **Toggle** (`admin:toggle_user` → `admin_toggle_target` state): identify an existing user; the bot then shows a yes/no `confirm_keyboard` naming the user and the action about to happen, and only flips `active` (`set_active`) once the admin taps confirm (`admin:toggle_confirm:<id>:<0|1>`). Tapping cancel (`admin:toggle_cancel`) leaves the record untouched.
- **Delete** (`admin:delete_user` → `admin_delete_target` state): identify an existing user, confirm (`admin:delete_confirm:<id>` / `admin:delete_cancel`), then `access_store.remove()` permanently deletes the row — unlike toggle, there's nothing left to restore afterward, which is why it gets its own explicit action instead of being folded into disable.

Confirmation prompts and result messages use `_user_display()` to show a name/username/label instead of a bare numeric id wherever we have something better on file.

A user who fails the check gets a specific reason (`not_authorized_text(user_id)`): disabled, expired, or never authorized — not a single generic "no access" message, so they know whether to wait, pay again, or ask for access for the first time.

**Quick actions from the list:** "📋 لیست کاربران مجاز" also renders one "⚙️ manage" button per user (`admin:manage:<id>`). Tapping it opens a small submenu (current status/expiry + renew/toggle/delete/back) that already knows the target id, so it skips straight to `duration_keyboard`/`confirm_keyboard` instead of asking the admin to forward a message or type the id again — it's a shortcut into the exact same renew/toggle/delete code paths described above, not a separate one.

## Tracking non-registered users (`services/pending_user_store.py`)

A separate SQLite table (`config_data/pending_users.db`) from `access_store` — this one isn't an access-control decision, it's a lightweight log of who's shown interest by `/start`-ing the bot without (yet) being registered. `start()` checks `access_store.get(user_id) is None` (existence, not authorization — someone disabled or expired was still registered at some point, so they're never added here) before touching this table.

`record_start()` returns whether this was the person's first-ever `/start` (→ every admin gets a one-time DM via `notify_admins_of_new_pending_user`, with name/username/id/timestamp) or a repeat (→ just bumps `last_seen_at`/`start_count`; identity fields stay as first captured). The moment an admin actually adds someone via `access_store.add(...)`, the same code path calls `pending_user_store.remove(target_id)` so they stop appearing as pending. Nothing reads `list_all()` yet beyond potential future admin tooling (broadcast-to-pending, conversion-rate reporting) — the table just accumulates the data those features would need.

**Migration note:** the store used to be a single hand-written JSON file. `AccessStore.__init__` still knows how to import that file's contents into SQLite once (only if the table is currently empty) and renames it to `authorized_users.json.migrated` afterward, so upgrading an existing deployment doesn't lose anyone.

**Expiry reminders:** a background task (`bot.py`'s `expiry_reminder_loop`, started from `main()`) wakes up every few hours, asks `access_store.list_expiring_soon(threshold)` for active users expiring within the threshold who haven't already been reminded *for that specific expiry*, and DMs each one. `mark_reminded()` records which `expires_at` a reminder was sent for; `update_expiry`/`add` clear that marker so renewing someone re-arms their next reminder instead of leaving them silently un-warned. A user who's blocked the bot is still marked reminded (just logged) rather than retried forever.
