"""
Plain UI-label data used when building inline keyboards / display text.
No logic, no side effects — just the Persian labels and callback-data
building blocks, extracted out of bot.py (phase B of the module split; see
CLAUDE.md's change log).
"""

QUALITY_LABELS = {
    "144": "144p", "240": "240p", "360": "360p",
    "480": "480p", "720": "720p",
    "mp3": "🎵 فقط صدا (mp3)", "m4a": "🎧 صدا (m4a)", "voice": "🎙 وویس",
}

POSITION_ICONS = {
    "top_left": "↖️", "top_center": "⬆️", "top_right": "↗️",
    "middle_left": "⬅️", "center": "⏺", "middle_right": "➡️",
    "bottom_left": "↙️", "bottom_center": "⬇️", "bottom_right": "↘️",
}

POSITION_LABELS_FA = {
    "top_left": "بالا چپ", "top_center": "بالا وسط", "top_right": "بالا راست",
    "middle_left": "وسط چپ", "center": "مرکز", "middle_right": "وسط راست",
    "bottom_left": "پایین چپ", "bottom_center": "پایین وسط", "bottom_right": "پایین راست",
}

POSITION_GRID = [
    ["top_left", "top_center", "top_right"],
    ["middle_left", "center", "middle_right"],
    ["bottom_left", "bottom_center", "bottom_right"],
]

# (label, days) options offered when picking how long a user's access lasts.
# days == 0 means "no expiry" (unlimited).
DURATION_OPTIONS = [
    ("۱ هفته", 7),
    ("۳ ماهه", 90),
    ("۶ ماهه", 180),
    ("۱ ساله", 365),
    ("نامحدود", 0),
]
