from __future__ import annotations

import re


def strip_excluded(name: str, exclude: str) -> str:
    """Removes every configured exclude term (case-insensitive) from a
    filename/title/caption, e.g. ad/site tags like '@channel' or
    '[www.example.com]'. `exclude` can hold multiple terms, one per
    line — each non-empty line is stripped independently, in order, so
    a user can exclude several usernames/links/notes at once instead of
    being limited to a single substring. A single-line value (the old
    format) behaves exactly as before. Leaves the name alone when no
    exclude text is configured, or when none of the terms appear."""

    if not exclude:
        return name

    terms = [line.strip() for line in exclude.splitlines() if line.strip()]

    if not terms:
        return name

    cleaned = name

    for term in terms:
        cleaned = re.sub(re.escape(term), "", cleaned, flags=re.IGNORECASE)

    # Collapse leftover double spaces/dashes created by the removal(s).
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -_")

    return cleaned or name


def summarize_exclude(exclude: str, max_len: int = 60) -> str:
    """One-line, settings-menu-friendly preview of a (possibly
    multi-line/multi-term) exclude_text value — used only for display,
    never for the actual stripping (see strip_excluded)."""

    terms = [line.strip() for line in exclude.splitlines() if line.strip()]

    if not terms:
        return ""

    if len(terms) == 1:
        return terms[0]

    preview = " | ".join(terms)

    if len(preview) > max_len:
        preview = preview[: max_len - 1].rstrip() + "…"

    return f"{len(terms)} مورد: {preview}"
