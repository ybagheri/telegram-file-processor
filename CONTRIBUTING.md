# Contributing

## Setup

```bash
git clone <this repo>
cd telegram-file-processor
cp .env.example .env   # dummy values are fine for tests, real ones for running the bot
pip install -r requirements.txt
pip install pytest ruff
```

## Workflow

1. Fork the repo and branch off `main`.
2. Make your change. If it's logic that doesn't need real Telegram/ffmpeg (sorting, filename cleanup, protocol encode/decode, dispatcher wiring, job/option defaults), add a test under `tests/`.
3. Run the checks below.
4. Open a PR describing what changed and why.

## Checks before opening a PR

```bash
ruff check .
pytest tests/
```

CI (`.github/workflows/python.yml`) runs the same two commands on every push/PR.

## Coding style

See [`AGENTS.md`](AGENTS.md) for style conventions and [`CLAUDE.md`](CLAUDE.md) for the project-specific rules and gotchas — several exist because of real bugs that were subtle enough to ship once already; please read them before touching `core/job.py`, `core/protocol.py`, or anything in `processors/archive.py`.

## Reporting bugs

Include: what you sent the bot, what settings were active (`/settings` screenshot helps), the relevant lines from `logs/bot.log` / `logs/worker.log`, and what you expected instead.
