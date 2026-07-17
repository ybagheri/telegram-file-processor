# Architecture

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

A flat JSON file (`config_data/user_settings.json`) keyed by `user_id`, holding defaults: quality, watermark on/off + logo path/position, upload-as, delivery target, caption, sort mode/order, and the EXCLUDE filename-cleanup text. `bot.py` copies these into a job's `options` dict when a file is first received, and the per-file inline-keyboard flow can override any of them before the job is sent to the bridge.

## Delivery & the caption rule

`bot.py`'s `handle_bridge_message` is the only place that talks to the actual destination chat (the user, or their configured channel/group). Every `copy_message_to(...)` call **must** pass an explicit `caption=` — Telegram's `copyMessage` keeps the original caption when it's omitted, and the "original caption" here is always the bridge's internal JSON. The delivered caption is built as `<filename without extension>` plus, if the user configured one in `/settings`, their fixed caption text appended below it.
