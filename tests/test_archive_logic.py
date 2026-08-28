"""
Tests for the pure logic in processors/archive.py — no ffmpeg/unrar
binary is ever executed. Covers natural-sort keys, leading-number
pairing, `unrar v` listing parsing, multi-volume part ordering, the
keep-window size calculation, and folder/file ordering (TOC/order
building) against a real Job rooted in tmp_path.
"""

import os

import time

from pathlib import Path

from types import SimpleNamespace

import pytest

from config import Paths

from core.job import Job

import processors.archive as archive_module

from processors.archive import ArchiveProcessor


from processors.archive import (
    _leading_number,
    _natural_key,
    _parse_unrar_listing,
)


@pytest.fixture
def job_root(tmp_path, monkeypatch):

    monkeypatch.setattr(Paths, "DOWNLOADS", tmp_path)
    return tmp_path


@pytest.fixture
def job(job_root):

    return Job(user_id=1, message_id=1)


# ======================================================================
# _natural_key — human numeric ordering ("2" before "10")
# ======================================================================


def test_natural_key_orders_numbers_numerically():

    names = ["lesson 10.mp4", "lesson 2.mp4", "lesson 1.mp4"]

    ordered = sorted(names, key=_natural_key)

    assert ordered == ["lesson 1.mp4", "lesson 2.mp4", "lesson 10.mp4"]


def test_natural_key_ignores_extension_when_comparing():

    # Key is derived from the stem, so .mp3 and .pdf with the same stem
    # compare equal (stable sort keeps input order).
    assert _natural_key("01 intro.mp3") == _natural_key("01 intro.pdf")


def test_natural_key_is_case_insensitive_for_text_parts():

    assert _natural_key("Intro") == _natural_key("intro")


# ======================================================================
# _leading_number — pairing "001.mp3" with "001.pdf"
# ======================================================================


def test_leading_number_extracts_first_digit_run():

    assert _leading_number("001 intro.mp3") == 1
    assert _leading_number("12 - chapter.mp3") == 12
    assert _leading_number("lesson07.pdf") == 7


def test_leading_number_none_without_digits():

    assert _leading_number("intro.mp3") is None


def test_leading_number_uses_stem_not_parent_dirs():

    assert _leading_number("folder/05 track.mp3") == 5


# ======================================================================
# _parse_unrar_listing — `unrar v` output parsing
# ======================================================================


def _unrar_line(attrs, size, packed, ratio, date, time_str, checksum, name):

    return f" {attrs}  {size}  {packed}  {ratio}%  {date} {time_str}  {checksum}  {name}"


def test_parse_unrar_listing_returns_files_with_size_and_mtime():

    output = "\n".join(
        [
            "UNRAR 6.11 beta",
            _unrar_line("rw-rw-rw-", "1797", "869", "48", "2023-02-22", "13:23", "6E2D3B9A", "Lesson 01/intro.mp3"),
            _unrar_line("rw-rw-rw-", "250000", "100000", "40", "2023-02-22", "13:24", "AAAA1111", "Lesson 01/intro.pdf"),
        ]
    )

    entries = _parse_unrar_listing(output)

    assert len(entries) == 2

    rel_path, mtime, size = entries[0]

    assert rel_path == "Lesson 01/intro.mp3"
    assert size == 1797
    assert mtime > 0


def test_parse_unrar_listing_skips_directory_entries():

    output = "\n".join(
        [
            _unrar_line("d-rw-rw-", "0", "0", "0", "2023-02-22", "13:23", "00000000", "Lesson 01"),
            _unrar_line("rw-rw-rw-", "100", "50", "50", "2023-02-22", "13:23", "AAAA1111", "file.mp3"),
        ]
    )

    entries = _parse_unrar_listing(output)

    assert [name for name, _mtime, _size in entries] == ["file.mp3"]


def test_parse_unrar_listing_normalizes_backslashes():

    output = _unrar_line(
        "rw-rw-rw-", "100", "50", "50", "2023-02-22", "13:23", "AAAA1111",
        "folder\\sub\\file.mp3",
    )

    entries = _parse_unrar_listing(output)

    assert entries[0][0] == "folder/sub/file.mp3"


def test_parse_unrar_listing_tolerates_bad_dates():

    # Regex-valid digits but not a real date: strptime fails, mtime
    # falls back to 0.0 instead of raising.
    output = _unrar_line(
        "rw-rw-rw-", "100", "50", "50", "2023-13-45", "25:99", "AAAA1111",
        "file.mp3",
    )

    entries = _parse_unrar_listing(output)

    assert entries[0][1] == 0.0  # unparseable mtime falls back to 0.0


# ======================================================================
# Multi-volume part ordering (_sort_parts) and keep-window sizing
# ======================================================================


def _volume_message(name: str):

    return SimpleNamespace(file=SimpleNamespace(name=name))


def test_sort_parts_orders_partNN_names_numerically():

    messages = [
        _volume_message("course.part3.rar"),
        _volume_message("course.part1.rar"),
        _volume_message("course.part2.rar"),
    ]

    ordered = ArchiveProcessor()._sort_parts(messages)

    assert [m.file.name for m in ordered] == [
        "course.part1.rar",
        "course.part2.rar",
        "course.part3.rar",
    ]


def test_sort_parts_orders_rXX_suffixes():

    messages = [
        _volume_message("course.r01"),
        _volume_message("course.rar"),
        _volume_message("course.r00"),
    ]

    ordered = ArchiveProcessor()._sort_parts(messages)

    # course.rar doesn't match either pattern -> key 0 -> stays first;
    # r00 -> 1, r01 -> 2.
    assert [m.file.name for m in ordered] == [
        "course.rar",
        "course.r00",
        "course.r01",
    ]


def test_compute_keep_window_falls_back_to_three_without_sizes():

    assert ArchiveProcessor()._compute_keep_window(0, []) == 3
    assert ArchiveProcessor()._compute_keep_window(-5, []) == 3


def test_compute_keep_window_covers_spanning_files():

    volume_size = 1000

    # One file whose uncompressed size spans 2.5 volumes -> needs 3
    # volumes (+1) present at once.
    entries = [
        ("a.mp3", 0.0, int(volume_size * 2.5)),
        ("b.mp3", 0.0, 10),
    ]

    window = ArchiveProcessor()._compute_keep_window(volume_size, entries)

    assert window == 4  # ceil(2.5) + 1


def test_compute_keep_window_has_a_minimum_of_two():

    entries = [("small.mp3", 0.0, 5)]

    assert ArchiveProcessor()._compute_keep_window(10_000, entries) == 2


# ======================================================================
# _order_folder_files — TOC / folder-order building
# ======================================================================


def _make_file(directory: Path, name: str) -> Path:

    path = directory / name

    path.write_bytes(b"x")

    return path


def test_order_folder_files_natural_order_and_type_grouping(job, tmp_path):

    files = [
        _make_file(tmp_path, "lesson 10.mp3"),
        _make_file(tmp_path, "lesson 2.mp3"),
        _make_file(tmp_path, "lesson 1.mp3"),
        _make_file(tmp_path, "notes.pdf"),
        _make_file(tmp_path, "cover.jpg"),
    ]

    ordered = ArchiveProcessor()._order_folder_files(job, files)

    names = [p.name for p in ordered]

    # Audio (natural order) first, then PDFs, then everything else.
    assert names == [
        "lesson 1.mp3",
        "lesson 2.mp3",
        "lesson 10.mp3",
        "notes.pdf",
        "cover.jpg",
    ]


def test_order_folder_files_pairs_audio_with_matching_pdf(job, tmp_path):

    files = [
        _make_file(tmp_path, "01 intro.mp3"),
        _make_file(tmp_path, "02 deep.mp3"),
        _make_file(tmp_path, "02 deep.pdf"),
        _make_file(tmp_path, "01 intro.pdf"),
    ]

    ordered = ArchiveProcessor()._order_folder_files(job, files)

    names = [p.name for p in ordered]

    # Each PDF is inserted immediately after its matching audio.
    assert names.index("01 intro.pdf") == names.index("01 intro.mp3") + 1
    assert names.index("02 deep.pdf") == names.index("02 deep.mp3") + 1


def test_order_folder_files_pairs_by_leading_number(job, tmp_path):

    files = [
        _make_file(tmp_path, "1 intro.mp3"),
        _make_file(tmp_path, "1 summary.pdf"),
        _make_file(tmp_path, "2 outro.mp3"),
    ]

    ordered = ArchiveProcessor()._order_folder_files(job, files)

    names = [p.name for p in ordered]

    # "1 summary.pdf" has a different name but the same leading number
    # as "1 intro.mp3", so it's still paired right after it.
    assert names.index("1 summary.pdf") == names.index("1 intro.mp3") + 1
    assert names[-1] == "2 outro.mp3"


def test_order_folder_files_desc_order(job, tmp_path):

    job.options.sort_order = "desc"

    files = [
        _make_file(tmp_path, "01 a.mp3"),
        _make_file(tmp_path, "02 b.mp3"),
    ]

    ordered = ArchiveProcessor()._order_folder_files(job, files)

    assert [p.name for p in ordered] == ["02 b.mp3", "01 a.mp3"]


def test_order_folder_files_date_mode(job, tmp_path):

    job.options.sort_mode = "date"

    old_file = _make_file(tmp_path, "old.mp3")

    new_file = _make_file(tmp_path, "new.mp3")

    past = time.time() - 10_000

    os.utime(old_file, (past, past))

    ordered = ArchiveProcessor()._order_folder_files(job, [new_file, old_file])

    assert [p.name for p in ordered] == ["old.mp3", "new.mp3"]
