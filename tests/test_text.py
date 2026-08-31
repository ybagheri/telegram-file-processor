"""Tests for utils/text.py: strip_excluded (now multi-term, one per
line) and summarize_exclude (settings-menu preview)."""

import pytest

from utils.text import strip_excluded, summarize_exclude


# ======================================================================
# strip_excluded
# ======================================================================


def test_no_exclude_configured_returns_name_unchanged():
    assert strip_excluded("Movie Name.mp4", "") == "Movie Name.mp4"


def test_single_term_matches_the_old_single_line_behavior():
    assert strip_excluded("Movie [www.site.com].mp4", "[www.site.com]") == "Movie .mp4".strip(" -_")


def test_single_term_is_case_insensitive():
    assert "SITE.COM" not in strip_excluded("Movie SITE.COM.mp4", "site.com")


def test_term_not_present_leaves_name_unchanged():
    assert strip_excluded("Movie Name.mp4", "not-in-here") == "Movie Name.mp4"


def test_multiple_terms_one_per_line_are_all_removed():

    name = "@username Movie [www.website.com](https://www.website.com) third_note_for_exclude.mp4"

    exclude = (
        "@username\n"
        "[www.website.com](https://www.website.com)\n"
        "third_note_for_exclude"
    )

    result = strip_excluded(name, exclude)

    assert "@username" not in result
    assert "www.website.com" not in result
    assert "third_note_for_exclude" not in result
    assert "Movie" in result
    assert result.endswith(".mp4")


def test_blank_lines_between_terms_are_ignored():

    exclude = "\n@username\n\n\nthird_note\n\n"

    result = strip_excluded("@username file third_note.mp4", exclude)

    assert "@username" not in result
    assert "third_note" not in result
    assert "file" in result


def test_only_blank_lines_leaves_name_unchanged():
    assert strip_excluded("Movie Name.mp4", "\n\n   \n") == "Movie Name.mp4"


def test_a_term_that_empties_the_whole_name_falls_back_to_original():
    # Guard against handing back "" or "-" as a filename.
    assert strip_excluded("@username", "@username") == "@username"


def test_collapses_double_spaces_and_stray_dashes_left_behind():

    result = strip_excluded("Movie - @username - Name.mp4", "@username")

    assert "  " not in result
    assert "@username" not in result


# ======================================================================
# summarize_exclude
# ======================================================================


def test_summarize_empty_is_empty_string():
    assert summarize_exclude("") == ""
    assert summarize_exclude("\n\n") == ""


def test_summarize_single_term_shown_as_is():
    assert summarize_exclude("@username") == "@username"


def test_summarize_multiple_terms_shows_a_count_and_preview():

    result = summarize_exclude("@username\nwww.site.com\nthird note")

    assert result.startswith("3 مورد:")
    assert "@username" in result
    assert "www.site.com" in result


def test_summarize_truncates_a_long_preview():

    terms = "\n".join(f"term-{i}-quite-a-bit-of-text-here" for i in range(10))

    result = summarize_exclude(terms, max_len=40)

    assert result.startswith("10 مورد:")
    assert result.endswith("…")
    assert len(result) < len(terms)
