"""
Tests for the structured error-reporting core (core/error_reporting.py):
stage/error-code vocabulary, the Telegram-boundary sanitizer (secrets,
paths, URL queries) and the admin report builder (escaping, traceback
preservation, size cap). No Telegram, no worker — pure functions.
"""

from config import ErrorReporting, Paths, Telegram

from core.error_reporting import (
    ErrorCode,
    JobStage,
    USER_SAFE_ERROR_TEXT,
    build_admin_report,
    sanitize_text,
)


# ======================================================================
# Vocabulary
# ======================================================================


def test_all_required_stages_exist():

    values = {stage.value for stage in JobStage}

    assert {
        "VALIDATION",
        "DOWNLOAD",
        "QUEUE",
        "PROCESSING",
        "UPLOAD",
        "TELEGRAM_API",
        "CLEANUP",
    } <= values


def test_user_safe_error_text_contains_no_internal_detail():

    # The one line every internal failure collapses to for the user:
    # short, actionable, and free of anything machine-specific.
    assert "پردازش فایل ناموفق بود" in USER_SAFE_ERROR_TEXT
    assert len(USER_SAFE_ERROR_TEXT) < 120


# ======================================================================
# sanitize_text
# ======================================================================


def test_sanitize_redacts_the_configured_bot_token():

    token = Telegram.BOT_TOKEN

    if not token:
        return  # config without a token in this environment

    assert token not in sanitize_text(f"request failed for {token} — sorry")
    assert "[REDACTED]" in sanitize_text(f"request failed for {token}")


def test_sanitize_redacts_the_configured_api_hash():

    api_hash = Telegram.API_HASH

    if not api_hash:
        return

    assert api_hash not in sanitize_text(f"auth error with {api_hash}")


def test_sanitize_redacts_bot_token_shaped_strings():

    # A token embedded by an HTTP library inside an exception message —
    # it is not (necessarily) the configured one, so shape-matching is
    # the only defence.
    text = "GET https://api.telegram.org/bot123456789:AAElPqRsTuVwXyZ0123456789abcdefghi/sendMessage failed"

    safe = sanitize_text(text)

    assert "AAElPqRsTuVwXyZ" not in safe
    assert "[REDACTED]" in safe


def test_sanitize_scrubs_url_queries_but_keeps_the_domain():

    safe = sanitize_text(
        "download failed: https://cdn.example.com/file.mp4?sig=SECRET123&exp=999"
    )

    assert "SECRET123" not in safe
    assert "https://cdn.example.com/file.mp4" in safe


def test_sanitize_replaces_project_root_paths():

    safe = sanitize_text(f'File "{Paths.BASE / "worker.py"}", line 1')

    assert str(Paths.BASE) not in safe
    assert "<server-path>" in safe
    assert "worker.py" in safe  # the relative part stays readable


def test_sanitize_is_idempotent():

    once = sanitize_text("path /opt/x and https://a.b/c?token=zz")

    assert sanitize_text(once) == once



# ======================================================================
# build_admin_report
# ======================================================================


def _report(**overrides):

    kwargs = dict(
        stage=JobStage.PROCESSING,
        code=ErrorCode.PROCESSING_FAILED,
        job_id="job123",
        user_id=777,
        username="someuser",
        file_name="course.rar",
        file_size=12345,
        file_type="ARCHIVE",
        operation="single_file",
        duration_seconds=12.34,
    )

    kwargs.update(overrides)

    return build_admin_report(**kwargs)


def test_report_contains_stage_code_job_and_file_context():

    report = _report(exception=RuntimeError("ffmpeg exploded"))

    assert "PROCESSING_FAILED" in report
    assert "PROCESSING" in report
    assert "job123" in report
    assert "777" in report
    assert "someuser" in report
    assert "course.rar" in report
    assert "ARCHIVE" in report
    assert "12.3s" in report


def test_report_preserves_the_traceback():

    try:
        raise ValueError("boom inside the pipeline")
    except ValueError as e:
        report = _report(exception=e)

    assert "ValueError: boom inside the pipeline" in report
    assert "Traceback (most recent call last)" in report
    assert "<pre>" in report and "</pre>" in report


def test_report_headline_includes_the_exception_type():

    report = _report(exception=KeyError("quality"))

    assert "KeyError" in report


def test_report_escapes_html_in_dynamic_values():

    # File names (and exception text) are user/machine-controlled —
    # they must never be able to inject HTML into the admin DM.
    report = _report(
        file_name='<b>evil</b>.mp4',
        exception=RuntimeError("<script>alert(1)</script>"),
    )

    assert "<b>evil</b>.mp4" not in report
    assert "&lt;b&gt;evil&lt;/b&gt;.mp4" in report
    assert "<script>" not in report


def test_report_sanitizes_secrets_inside_the_traceback():

    token_like = "123456789:AAElPqRsTuVwXyZ0123456789abcdefghi"

    try:
        raise RuntimeError(f"request to /bot{token_like}/sendMedia failed")
    except RuntimeError as e:
        report = _report(exception=e)

    assert token_like not in report
    assert "[REDACTED]" in report


def test_report_sanitizes_paths_inside_the_traceback():

    try:
        raise RuntimeError(f"cannot write {Paths.BASE / 'temp' / 'x.mp4'}")
    except RuntimeError as e:
        report = _report(exception=e)

    assert str(Paths.BASE) not in report


def test_report_is_capped_under_the_telegram_limit(monkeypatch):

    monkeypatch.setattr(ErrorReporting, "MAX_REPORT_CHARS", 1200)

    try:
        raise RuntimeError("x" * 5000)
    except RuntimeError as e:
        report = _report(exception=e)

    assert len(report) <= ErrorReporting.MAX_REPORT_CHARS


def test_report_truncates_a_giant_traceback_explicitly(monkeypatch):

    from core.error_reporting import _truncate_traceback

    monkeypatch.setattr(ErrorReporting, "MAX_REPORT_CHARS", 1500)

    # A 20k-char traceback (e.g. from deeply nested library frames that
    # don't collapse) must be cut at the budget WITH an explicit marker.
    tb = "Traceback (most recent call last):\n" + "x" * 20000

    cut = _truncate_traceback(tb, 1000)

    assert len(cut) <= 1100
    assert "traceback truncated" in cut
    # The head — the frames near the pipeline — survives truncation.
    assert cut.startswith("Traceback (most recent call last)")

    try:
        raise RuntimeError("x" * 5000)
    except RuntimeError as e:
        report = _report(exception=e)

    assert len(report) <= ErrorReporting.MAX_REPORT_CHARS + 1  # + ellipsis


def test_report_works_without_an_exception_object():

    report = _report(
        exception=None,
        exception_message="download returned no file",
    )

    assert "download returned no file" in report
    assert "<pre>" not in report


def test_report_includes_options_and_extra_lines():

    report = _report(
        options={"quality": "360", "watermark": True},
        extra_lines=["failed files: part2.mp4"],
    )

    assert "quality=360" in report
    assert "watermark=True" in report
    assert "failed files: part2.mp4" in report


def test_report_omits_empty_fields():

    report = build_admin_report(
        stage=JobStage.DOWNLOAD,
        code=ErrorCode.DOWNLOAD_FAILED,
    )

    assert "username:" not in report
    assert "file:" not in report
    assert "url:" not in report
    # But the mandatory bits are always there.
    assert "DOWNLOAD" in report
    assert "DOWNLOAD_FAILED" in report


def test_report_url_field_is_sanitized():

    report = _report(url="https://example.com/a.mp4?token=SECRET")

    assert "SECRET" not in report
