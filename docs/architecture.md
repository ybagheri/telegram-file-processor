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

1. Extract (zip/rar/7z; if encrypted, request a password via the bridge and retry, up to once).
2. Walk the extracted tree with `os.walk`, folder by folder, **announcing each folder** (`folder` message) before its files — this is what lets a channel display files in the same logical order as the original disk structure.
3. Within each folder, files are split into audio / PDF / everything-else, each sorted per the user's `sort_mode`/`sort_order`; a PDF whose name or leading number matches an audio file is moved to sit immediately after it (regardless of where it'd naturally sort), the rest of the PDFs follow, then everything else.
4. Each file is recursively handed to the same `Dispatcher` (temporarily repointing `job.input_file`/`job.file_type`/`job.original_name`; `job.current_extract_folder` is set so outputs land in a matching subfolder of `output_dir` and get tagged with the right `folder` for the TOC) — this is why `dispatcher/dispatcher.py` is imported lazily inside the method, to dodge a circular import.
5. `job.options.rename_to` is blanked for the duration of the walk (a single rename target doesn't make sense for dozens of files) and restored after.

## Settings (`services/settings_store.py`)

A flat JSON file (`config_data/user_settings.json`) keyed by `user_id`, holding defaults: quality, watermark on/off + logo path/position, upload-as, delivery target, caption, sort mode/order, and the EXCLUDE filename-cleanup text. `bot.py` copies these into a job's `options` dict when a file is first received, and the per-file inline-keyboard flow can override any of them before the job is sent to the bridge.

## Delivery & the caption rule

`bot.py`'s `handle_bridge_message` is the only place that talks to the actual destination chat (the user, or their configured channel/group). Every `copy_message_to(...)` call **must** pass an explicit `caption=` — Telegram's `copyMessage` keeps the original caption when it's omitted, and the "original caption" here is always the bridge's internal JSON. The delivered caption is built as `<filename without extension>` plus, if the user configured one in `/settings`, their fixed caption text appended below it.
