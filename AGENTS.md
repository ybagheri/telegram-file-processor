# AGENTS.md

Generic instructions for any coding agent (Claude Code, Cursor, Aider, Copilot Workspace, etc.) working in this repo. Project-specific architecture and gotchas live in [`CLAUDE.md`](CLAUDE.md) — read that too before making non-trivial changes.

## Stack

- Python 3.10+, fully `async`/`await` in the runtime code paths.
- `aiogram` (Bot API) in `bot.py`, `telethon` (user account) in `worker.py`.
- No web framework, no database — per-user settings are a flat JSON file (`services/settings_store.py`); jobs are in-memory (`core/job.py`), nothing persists across a `worker.py` restart mid-job.

## Coding style

- Match the existing formatting: generous blank lines inside function bodies, one argument per line in multi-arg calls. It looks sparse but it's consistent — don't reformat unrelated code in the same diff as a fix.
- Type hints where they add clarity (dataclass fields, function signatures); not enforced strictly project-wide.
- Prefer small, focused functions over long ones — most files in `processors/` and `services/` follow a "one clear method per operation" style.
- Comments explain *why*, not *what* — especially around anything that looks like it could be simplified but isn't (e.g. the caption-override rule, the FloodWait retries).

## Before you change something

1. Check `CLAUDE.md`'s "Hard rules" section — several past bugs came from violating one of those.
2. If the change touches `core/job.py`, `core/job_options.py`, or the bridge protocol (`core/protocol.py`), grep for every call site — these are shared across `bot.py`, `worker.py`, and every processor.

## Logging

`core/logger.py` routes to `logs/bot.log` or `logs/worker.log` depending on which entrypoint is running (detected from `sys.argv[0]`), plus stderr. Use `from core.logger import get_logger; logger = get_logger(__name__)` — don't call `logging.getLogger` directly.
