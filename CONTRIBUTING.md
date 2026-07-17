# Contributing

## Setup

```bash
git clone <this repo>
cd telegram-file-processor
cp .env.example .env   # fill in real values to run the bot
pip install -r requirements.txt
```

## Workflow

1. Fork the repo and branch off `main`.
2. Make your change.
3. Sanity-check it manually (see `CLAUDE.md` for the gotchas that have bitten this project before — please read it before touching `core/job.py`, `core/protocol.py`, or anything in `processors/archive.py`).
4. Open a PR describing what changed and why.

## Coding style

See [`AGENTS.md`](AGENTS.md) for style conventions and [`CLAUDE.md`](CLAUDE.md) for project-specific rules.

## Reporting bugs

Include: what you sent the bot, what settings were active (`/settings` screenshot helps), the relevant lines from `logs/bot.log` / `logs/worker.log`, and what you expected instead.
