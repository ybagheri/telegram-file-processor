"""
Pure logic used by userbot_test.py, deliberately kept free of any Telethon
network calls so it can be unit-tested without a live connection (see
qa-userbot/tests/test_scenario_engine.py). userbot_test.py imports this and
does the actual network I/O around it.
"""
from __future__ import annotations


def find_button(message, wanted_text: str):
    """Search a message's inline keyboard for a button whose visible text
    contains `wanted_text`. Substring match (not exact) because it's more
    forgiving of incidental differences than an exact match would be,
    while `wanted_text` values are chosen specific enough (e.g. "360p",
    "✅ آپلود کن") that they won't accidentally match the wrong button.

    Works against Telethon's real `Message` objects (`.buttons` is a
    `list[list[Button]]` or `None`) and against the `FakeMessage`/
    `FakeButton` stand-ins used in tests — both only need `.buttons` and
    each button only needs `.text`.
    """
    buttons = getattr(message, "buttons", None)
    if not buttons:
        return None
    for row in buttons:
        for button in row:
            if wanted_text in (button.text or ""):
                return button
    return None


def message_snapshot(message):
    """A cheap, comparable fingerprint of a message's visible state (text +
    the label of every button). Callers use this to detect that a message
    was edited *in place* rather than replaced by a new one.

    This matters because this bot's callback handlers almost always call
    `edit_text`/`edit_reply_markup` on the SAME message (aiogram's normal
    pattern for a step-by-step inline flow) instead of sending a fresh
    message — so a poll that only watches for "a message with a higher id"
    would wait forever after the very first click, since the id never
    changes on an edit.
    """
    buttons = getattr(message, "buttons", None) or []
    labels = tuple(button.text for row in buttons for button in row)
    return (getattr(message, "text", None), labels)


def next_click_target(remaining: list[str], message):
    """Given the still-unclicked steps of a scenario (in order) and the
    bot's latest message, return `(index, button)` for the first remaining
    step whose button is present on this message — so scenarios don't have
    to assume every step's keyboard appears on its own separate message
    (e.g. a quality pick and the final confirm button might not always be
    exactly one message apart). Returns `(None, None)` if nothing in
    `remaining` matches anything on this message.
    """
    for i, wanted_text in enumerate(remaining):
        button = find_button(message, wanted_text)
        if button:
            return i, button
    return None, None
