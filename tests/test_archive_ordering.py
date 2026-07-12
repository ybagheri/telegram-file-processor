from pathlib import Path

from processors.archive import ArchiveProcessor, _natural_key, _leading_number
from core.job import Job
from core.job_options import JobOptions


def test_natural_key_orders_numbers_correctly():
    names = ["10 Lesson.mp3", "2 Lesson.mp3", "1 Lesson.mp3"]
    ordered = sorted(names, key=_natural_key)
    assert ordered == ["1 Lesson.mp3", "2 Lesson.mp3", "10 Lesson.mp3"]


def test_leading_number_extracts_digits_anywhere_near_the_start():
    assert _leading_number("005 Lesson.mp3") == 5
    assert _leading_number("aaa_random_005.pdf") == 5
    assert _leading_number("no_numbers_here.pdf") is None


def test_matching_pdf_is_moved_right_after_its_audio(tmp_path):
    # Same scenario validated manually during development: a PDF that
    # matches an audio file by leading number must be promoted to sit
    # right after it, even if it would otherwise sort much later/earlier.
    audio = tmp_path / "005 Lesson.mp3"
    matching_pdf = tmp_path / "zzz_005_notes.pdf"
    unrelated_pdf = tmp_path / "aaa_unrelated.pdf"
    other = tmp_path / "000 first.txt"

    for f in (audio, matching_pdf, unrelated_pdf, other):
        f.write_bytes(b"x")

    job = Job(user_id=1, message_id=2, options=JobOptions(sort_mode="name", sort_order="asc"))

    ordered = ArchiveProcessor()._order_folder_files(
        job, [other, unrelated_pdf, matching_pdf, audio],
    )

    names = [p.name for p in ordered]

    assert names.index(audio.name) < names.index(matching_pdf.name)
    assert names.index(matching_pdf.name) < names.index(unrelated_pdf.name)
    assert names[-1] == other.name
