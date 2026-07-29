# qa-userbot — live end-to-end testing against the real bot

A **real Telegram user account** (via [Telethon](https://docs.telethon.dev/)) that sends test media to your actual running bot and clicks through its inline keyboards, so you can watch/verify the real pipeline end-to-end — real ffmpeg conversion, real Telegram delivery, real button behavior — not a stub or simulation.

**This folder is completely separate from the main project.** It doesn't import anything from `bot.py`/`worker.py`/`services/`/`core/`, doesn't share a session file with them, and nothing in the main project imports from here. It's a black-box client: as far as your bot is concerned, this is just another user.

## Why this exists

The main project's `tests/` folder (pure-logic pytest suite) and the ad-hoc stub-based scripts used during development both simulate Telegram objects in Python — they never touch a live bot, a live ffmpeg conversion, or real file delivery. This is the piece that actually does, driven by a script instead of your thumb.

## Setup (one time)

```bash
cd qa-userbot
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

- `USERBOT_API_ID` / `USERBOT_API_HASH` — from <https://my.telegram.org>. This must be a **real user account's** credentials, not the bot's own token — only a user account can send files to a bot and tap its buttons the way a person does.
- `TARGET_BOT_USERNAME` — the real bot you're testing (with or without the leading `@`).
- `TEST_SOURCE_CHAT` — a group/channel the user account above is already a member of, where **you manually upload sample media** (a photo, a video, an archive containing mixed media, etc.) for the script to pick up and forward on to the bot.

## First run — interactive login

```bash
python userbot_test.py
```

The first time, Telethon will prompt for the phone number, the login code Telegram just texted/sent you, and your 2FA password if you have one. Do this once, right here in your own terminal — **this can't be done from an AI sandbox**, since it needs a live code sent to your real phone/account. After the first successful login, a session file (`<USERBOT_SESSION_NAME>.session`) is saved in this folder and every later run is non-interactive.

## Running it

```bash
python userbot_test.py                    # run every scenario below
python userbot_test.py video_quality_360   # run just one, by name
```

For each scenario, the script:

1. Looks through `TEST_SOURCE_CHAT` for a not-yet-used message whose media matches (a photo, a video, an archive, ...).
2. Downloads it, then sends it **fresh** (not forwarded) to `TARGET_BOT_USERNAME` — exactly like a real user attaching a file.
3. Waits for the bot's replies. Whenever one has an inline keyboard, it clicks through the scenario's list of button texts as they come up.
4. Prints every bot message and every click live, so you can follow along or scroll back afterward.

**It does not auto-verify the final result** (e.g. that a "voice only" request actually produced a voice note, or that the watermark looks right). Open the `TARGET_BOT_USERNAME` chat yourself and look — that's still the real pass/fail check. This script's job is just to drive the conversation for you so you don't have to tap through it by hand every time.

## Scenarios included

| Name | Test media needed in `TEST_SOURCE_CHAT` | What it clicks |
|---|---|---|
| `photo_watermark` | any photo | Watermark confirmation on a photo |
| `video_quality_360` | any video | Pick 360p, then confirm upload |
| `video_voice_only` | any video | Pick "🎙 وویس" (voice-only extraction), then confirm |
| `video_collage_only` | any video | Pick "🖼 فقط کولاژ تامبنیل" (thumbnail collage, no conversion), then confirm |
| `archive_mixed_media` | a `.zip`/`.rar`/`.7z` containing a mix of photos/videos/audio | Confirm upload (the archive's per-folder/per-file flow is otherwise driven by the bot automatically) |

Upload the corresponding sample media to `TEST_SOURCE_CHAT` before running each scenario. A scenario is skipped (not failed) if it can't find matching, not-yet-used media.

## Adding a new scenario

Edit `SCENARIOS` in `userbot_test.py`:

```python
"my_new_scenario": (
    lambda m: m.video is not None,             # a filter over Telethon Message objects
    ["720p", "✅ آپلود کن"],                    # ordered button texts to click as they appear
),
```

**Copy button text directly out of `bot.py`** rather than retyping it — that's the #1 way a scenario silently stops matching anything (a missing/extra space, a different emoji, etc.). `scenario_engine.find_button()` does a substring match, so a shorter distinctive fragment of a long label is fine and more robust than the full string.

## Tests

`scenario_engine.py`'s button-matching logic is pure Python (no network) and has its own unit tests:

```bash
cd qa-userbot
python -m pytest tests/
```

These don't need Telegram, `telethon`, or any credentials — they use plain fake objects standing in for Telethon's `Message`/`Button`. They only cover the matching/click-ordering logic, not the live script itself (which genuinely can't be tested without a real Telegram connection).

## Known limitations

- No automatic pass/fail — you review the delivered result yourself.
- `archive_mixed_media`'s single "confirm upload" click assumes the bot doesn't ask anything else first (e.g. a multipart-archive password, or per-folder confirmation) for your specific test archive. If your archive triggers extra prompts, extend that scenario's click list to match, or watch the printed log and click the remaining steps by hand in the actual Telegram app — the script only automates what you tell it to.
- The script polls for the bot's next message every couple of seconds rather than using a push-based event handler — simple and reliable, just not instant.
- For a **private** `TEST_SOURCE_CHAT` given as a numeric id (not a `@username`), `resolve_source_chat()` falls back to scanning this account's own dialog list if a direct lookup fails — this only works if the account has opened that chat at least once, which is true by definition here since it needs to be a member to read the test media anyway.
