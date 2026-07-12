from core.job import Job
from core.job_options import JobOptions


def test_job_options_sane_defaults():
    options = JobOptions()
    assert options.quality == "360"
    assert options.watermark is True
    assert options.upload_as == "video"
    assert options.target_chat_id == 0
    assert options.rename_to == ""


def test_job_stem_uses_original_name_by_default():
    job = Job(user_id=1, message_id=2, options=JobOptions())
    job.original_name = "001 Grammar.mp3"
    assert job.stem == "001 Grammar"


def test_job_stem_prefers_rename_to_when_set():
    job = Job(user_id=1, message_id=2, options=JobOptions(rename_to="My Custom Name"))
    job.original_name = "001 Grammar.mp3"
    assert job.stem == "My Custom Name"


def test_job_has_output_reflects_added_files(tmp_path):
    job = Job(user_id=1, message_id=2, options=JobOptions())
    assert job.has_output is False

    fake_file = tmp_path / "out.mp4"
    fake_file.write_bytes(b"data")

    entry = job.add_output(fake_file, kind="video")

    assert entry is not None
    assert job.has_output is True
    assert job.output_files[0].kind == "video"


def test_add_output_ignores_missing_file(tmp_path):
    job = Job(user_id=1, message_id=2, options=JobOptions())
    missing = tmp_path / "does_not_exist.mp4"

    entry = job.add_output(missing)

    assert entry is None
    assert job.has_output is False
