from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class JobOptions:

    # ==========================================
    # Processing
    # ==========================================

    quality: str = "360"

    password: str | None = None

    # ==========================================
    # Metadata
    # ==========================================

    title: str = ""

    artist: str = ""

    album: str = ""

    comment: str = ""

    copyright: str = ""

    # ==========================================
    # Processing Options
    # ==========================================

    watermark: bool = True

    thumbnail_second: int = 3

    logo_path: str = ""

    logo_position: str = "bottom_right"

    upload_as: str = "video"

    target_chat_id: int = 0

    custom_thumbnail: str = ""

    # ==========================================
    # Archive extraction ordering
    # ==========================================

    sort_mode: str = "name"    # "name" | "date"

    sort_order: str = "asc"    # "asc" | "desc"

    # Text/substring to strip out of every filename (and anything derived
    # from it: title, caption, etc.) before it's used anywhere — e.g. to
    # remove an ad/watermark string like "[www.site.com]".
    exclude_text: str = ""

    # ==========================================
    # Rename
    # ==========================================

    remove_words: list[str] = field(
        default_factory=list
    )

    rename_to: str = ""

    # ==========================================
    # Future
    # ==========================================

    extra: dict = field(
        default_factory=dict
    )
