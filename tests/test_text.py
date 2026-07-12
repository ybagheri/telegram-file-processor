from utils.text import strip_excluded


def test_no_exclude_configured_returns_name_unchanged():
    assert strip_excluded("001 Grammar.mp3", "") == "001 Grammar.mp3"


def test_strips_configured_substring_case_insensitively():
    result = strip_excluded("001 Grammar [WWW.EasyTalk.ir].mp3", "[www.easytalk.ir]")
    assert "easytalk" not in result.lower()
    assert result.startswith("001 Grammar")


def test_collapses_leftover_whitespace_after_stripping():
    result = strip_excluded("Lesson  [ad]  Notes", "[ad]")
    assert "  " not in result


def test_never_returns_empty_string():
    # if stripping would leave nothing useful, fall back to the original
    result = strip_excluded("[ad]", "[ad]")
    assert result
