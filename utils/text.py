from __future__ import annotations

import re


def strip_excluded(name: str, exclude: str) -> str:
    """Remove a configured substring (case-insensitive) from a filename,
    e.g. an ad/site tag like '[www.example.com]'. Leaves the name alone
    when no exclude text is configured or it doesn't appear."""

    if not exclude:
        return name

    cleaned = re.sub(re.escape(exclude), "", name, flags=re.IGNORECASE)

    # Collapse leftover double spaces/dashes created by the removal.
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -_")

    return cleaned or name
