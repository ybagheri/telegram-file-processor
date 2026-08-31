"""
Tests for services/url_downloader.py — the streaming HTTP download is
fully mocked (fake urlopen responses), so no real network calls happen.
Covers the Content-Length pre-check, the hard cap while streaming,
timeout/network error mapping, and the zero-byte case.
"""

import io

import pytest

import services.url_downloader as url_downloader

from services.url_downloader import (
    REASON_EMPTY,
    REASON_NETWORK,
    REASON_TIMEOUT,
    REASON_TOO_LARGE,
    URLDownloadError,
    download_to_disk_sync,
)


class FakeResponse:
    """Mimics the parts of a urllib response the downloader uses."""

    def __init__(self, chunks: list[bytes], headers: dict | None = None, url: str = ""):
        self._stream = io.BytesIO(b"".join(chunks))
        self.headers = headers or {}
        self._url = url

    def read(self, size: int) -> bytes:
        return self._stream.read(size)

    def geturl(self) -> str:
        return self._url

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHeaders(dict):
    """Case-insensitive dict, like http.client.HTTPMessage."""

    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


@pytest.fixture
def fake_urlopen(monkeypatch):

    def install(response):

        def _urlopen(request, timeout=None):
            return response

        monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _urlopen)

    return install


# ======================================================================
# Happy path
# ======================================================================


def test_download_writes_all_bytes_to_disk(fake_urlopen, tmp_path):

    fake_urlopen(
        FakeResponse(
            [b"hello ", b"world"],
            {"Content-Length": "11"},
        )
    )

    destination = tmp_path / "sub" / "file.mp4"

    result = download_to_disk_sync("https://example.com/file.mp4", destination, 1000)

    assert result == destination
    assert destination.read_bytes() == b"hello world"


def test_download_tolerates_case_insensitive_content_length(fake_urlopen, tmp_path):

    fake_urlopen(
        FakeResponse(
            [b"data"],
            FakeHeaders({"content-length": "4"}),
        )
    )

    destination = tmp_path / "file.mp4"

    download_to_disk_sync("https://example.com/file.mp4", destination, 1000)

    assert destination.read_bytes() == b"data"


# ======================================================================
# Size caps
# ======================================================================


def test_content_length_over_limit_rejected_before_download(fake_urlopen, tmp_path):

    # The body is small, but the header says too big — must be rejected
    # on the header alone, before a single byte is read.
    fake_urlopen(
        FakeResponse(
            [b"tiny"],
            {"Content-Length": str(9999)},
        )
    )

    with pytest.raises(URLDownloadError) as exc_info:
        download_to_disk_sync("https://example.com/file.mp4", tmp_path / "f", 1000)

    assert exc_info.value.reason == REASON_TOO_LARGE


def test_streaming_hard_cap_when_header_is_missing(fake_urlopen, tmp_path):

    # No Content-Length, but the body is larger than max_size — the
    # streaming cap must abort mid-download.
    fake_urlopen(
        FakeResponse(
            [b"x" * 600, b"x" * 600],
            {},
        )
    )

    destination = tmp_path / "f"

    with pytest.raises(URLDownloadError) as exc_info:
        download_to_disk_sync("https://example.com/file.mp4", destination, 1000)

    assert exc_info.value.reason == REASON_TOO_LARGE


def test_streaming_hard_cap_when_header_lies(fake_urlopen, tmp_path):

    # Header claims a small size but the body streams far more.
    fake_urlopen(
        FakeResponse(
            [b"y" * 1500],
            {"Content-Length": "10"},
        )
    )

    with pytest.raises(URLDownloadError) as exc_info:
        download_to_disk_sync("https://example.com/file.mp4", tmp_path / "f", 1000)

    assert exc_info.value.reason == REASON_TOO_LARGE


# ======================================================================
# Failure mapping
# ======================================================================


def test_http_error_maps_to_network_reason(monkeypatch, tmp_path):

    import urllib.error

    def _urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            "https://example.com", 404, "Not Found", {}, io.BytesIO(b"")
        )

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _urlopen)

    with pytest.raises(URLDownloadError) as exc_info:
        download_to_disk_sync("https://example.com/f", tmp_path / "f", 1000)

    assert exc_info.value.reason == REASON_NETWORK


def test_url_error_with_timeout_reason_maps_to_timeout(monkeypatch, tmp_path):

    import socket as _socket

    import urllib.error

    def _urlopen(request, timeout=None):
        raise urllib.error.URLError(_socket.timeout("timed out"))

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _urlopen)

    with pytest.raises(URLDownloadError) as exc_info:
        download_to_disk_sync("https://example.com/f", tmp_path / "f", 1000)

    assert exc_info.value.reason == REASON_TIMEOUT


def test_url_error_maps_to_network_reason(monkeypatch, tmp_path):

    import urllib.error

    def _urlopen(request, timeout=None):
        raise urllib.error.URLError(OSError("connection refused"))

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _urlopen)

    with pytest.raises(URLDownloadError) as exc_info:
        download_to_disk_sync("https://example.com/f", tmp_path / "f", 1000)

    assert exc_info.value.reason == REASON_NETWORK


def test_zero_byte_response_is_an_error_and_leaves_no_file(fake_urlopen, tmp_path):

    fake_urlopen(FakeResponse([], {}))

    destination = tmp_path / "f"

    with pytest.raises(URLDownloadError) as exc_info:
        download_to_disk_sync("https://example.com/f", destination, 1000)

    assert exc_info.value.reason == REASON_EMPTY
    assert not destination.exists()


# ======================================================================
# Same-origin Referer header (fixes the "get direct link" button on
# hosts like picofile.com returning a tiny error page instead of the
# file — see services/url_downloader.py::_request_headers)
# ======================================================================


def test_request_sends_a_same_origin_referer(monkeypatch, tmp_path):

    captured = {}

    def _urlopen(request, timeout=None):
        captured["headers"] = dict(request.header_items())
        return FakeResponse([b"data"], {"Content-Length": "4"})

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _urlopen)

    download_to_disk_sync(
        "https://s8.picofile.com/d/8365509176/abc/file.zip",
        tmp_path / "f",
        1000,
    )

    # urllib capitalizes header names as Title-Case internally.
    assert captured["headers"]["Referer"] == "https://s8.picofile.com/"
    assert "Mozilla" in captured["headers"]["User-agent"]


# ======================================================================
# picofile.com landing-page -> direct-link resolution
# (services/url_downloader.py::resolve_download_url)
# ======================================================================


PICOFILE_PAGE_URL = (
    "https://s8.picofile.com/file/8365509176/"
    "Saison_1_livre_audio_www_iranfrench_ir_.zip.html"
)

PICOFILE_DIRECT_URL = (
    "https://s8.picofile.com/d/8365509176/"
    "399d0c56-ce42-4a9c-9044-142792f5c623/"
    "Saison_1_livre_audio_www_iranfrench_ir_.zip"
)


def test_resolve_leaves_non_picofile_urls_unchanged():

    url = "https://example.com/some/file.zip"

    assert url_downloader.resolve_download_url(url, timeout=10) == url


def test_resolve_leaves_an_already_direct_picofile_link_unchanged():

    # /d/... links (what the "دریافت لینک دانلود" button itself produces)
    # don't match the /file/{id}/... landing-page pattern, so no extra
    # request should be made for them.
    assert (
        url_downloader.resolve_download_url(PICOFILE_DIRECT_URL, timeout=10)
        == PICOFILE_DIRECT_URL
    )


def test_resolve_picofile_landing_page_posts_to_generate_download_link(monkeypatch):

    captured = {}

    def _urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["data"] = request.data
        captured["headers"] = dict(request.header_items())
        return FakeResponse([PICOFILE_DIRECT_URL.encode()])

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _urlopen)

    resolved = url_downloader.resolve_download_url(PICOFILE_PAGE_URL, timeout=10)

    assert resolved == PICOFILE_DIRECT_URL
    assert captured["method"] == "POST"
    assert (
        captured["url"]
        == "https://s8.picofile.com/file/generateDownloadLink?fileId=8365509176"
    )
    assert captured["data"] == b"password="
    assert captured["headers"]["Referer"] == "https://s8.picofile.com/"


def test_resolve_picofile_raises_password_protected_on_403(monkeypatch):

    import urllib.error

    def _urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", None, None)

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _urlopen)

    with pytest.raises(URLDownloadError) as exc_info:
        url_downloader.resolve_download_url(PICOFILE_PAGE_URL, timeout=10)

    assert exc_info.value.reason == url_downloader.REASON_PASSWORD_PROTECTED


def test_resolve_picofile_raises_resolve_failed_on_other_http_errors(monkeypatch):

    import urllib.error

    def _urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", None, None)

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _urlopen)

    with pytest.raises(URLDownloadError) as exc_info:
        url_downloader.resolve_download_url(PICOFILE_PAGE_URL, timeout=10)

    assert exc_info.value.reason == url_downloader.REASON_LINK_RESOLVE_FAILED


def test_resolve_picofile_raises_resolve_failed_on_unexpected_response_body(monkeypatch):

    def _urlopen(request, timeout=None):
        return FakeResponse([b"<html>error page</html>"])

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _urlopen)

    with pytest.raises(URLDownloadError) as exc_info:
        url_downloader.resolve_download_url(PICOFILE_PAGE_URL, timeout=10)

    assert exc_info.value.reason == url_downloader.REASON_LINK_RESOLVE_FAILED


def test_download_to_disk_sync_resolves_a_picofile_page_end_to_end(monkeypatch, tmp_path):
    """The full flow a user actually triggers: hand the bot the landing
    page URL, get the real file — resolve step and download step chained
    through a single download_to_disk_sync() call."""

    calls = []

    def _urlopen(request, timeout=None):
        calls.append(request.full_url)

        if request.full_url == PICOFILE_DIRECT_URL:
            return FakeResponse(
                [b"the actual file bytes"],
                {"Content-Length": "22"},
                url=PICOFILE_DIRECT_URL,
            )

        return FakeResponse([PICOFILE_DIRECT_URL.encode()])

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _urlopen)

    destination = tmp_path / "f.zip"

    result = download_to_disk_sync(PICOFILE_PAGE_URL, destination, 1000)

    assert result == destination
    assert destination.read_bytes() == b"the actual file bytes"
    assert calls[0].startswith("https://s8.picofile.com/file/generateDownloadLink")
    assert calls[1] == PICOFILE_DIRECT_URL


# ======================================================================
# Self-healing on an expired picofile direct link: instead of an HTTP
# error, picofile redirects a dead `/d/...` link to the file's landing
# page — a plain downloader would silently save that HTML as "the file".
# ======================================================================


def test_expired_direct_link_is_refreshed_and_retried(monkeypatch, tmp_path):

    calls = []

    def _urlopen(request, timeout=None):
        calls.append(request.full_url)

        if request.get_method() == "POST":
            # The refresh request (generateDownloadLink).
            return FakeResponse([PICOFILE_DIRECT_URL.encode()])

        if request.full_url == PICOFILE_DIRECT_URL and len(calls) == 1:
            # First attempt at the (stale) direct link the caller gave
            # us: picofile silently redirects to the landing page.
            return FakeResponse([b"<html>landing page</html>"], url=PICOFILE_PAGE_URL)

        # Second attempt, using the freshly resolved link: the real file.
        return FakeResponse(
            [b"the actual file bytes"],
            {"Content-Length": "22"},
            url=PICOFILE_DIRECT_URL,
        )

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _urlopen)

    destination = tmp_path / "f.zip"

    result = download_to_disk_sync(PICOFILE_DIRECT_URL, destination, 1000)

    assert result == destination
    assert destination.read_bytes() == b"the actual file bytes"

    # attempt 1 (stale link, gets redirected) -> refresh POST -> attempt 2.
    assert calls[0] == PICOFILE_DIRECT_URL
    assert calls[2] == PICOFILE_DIRECT_URL


def test_stuck_stale_link_raises_a_clear_error_instead_of_saving_html(monkeypatch, tmp_path):
    """If picofile keeps redirecting to the landing page even after one
    refresh, fail loudly rather than silently writing HTML to disk."""

    def _urlopen(request, timeout=None):

        if request.get_method() == "POST":
            return FakeResponse([PICOFILE_DIRECT_URL.encode()])

        return FakeResponse([b"<html>still the landing page</html>"], url=PICOFILE_PAGE_URL)

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _urlopen)

    destination = tmp_path / "f.zip"

    with pytest.raises(URLDownloadError) as exc_info:
        download_to_disk_sync(PICOFILE_DIRECT_URL, destination, 1000)

    assert exc_info.value.reason == url_downloader.REASON_LINK_RESOLVE_FAILED
    assert not destination.exists()


def test_stale_link_detection_ignores_non_picofile_redirects(monkeypatch, tmp_path):
    """The stale-link check is scoped to picofile — an ordinary redirect
    on any other host (or even a picofile URL that isn't the landing
    page pattern) must not trigger a refresh attempt."""

    def _urlopen(request, timeout=None):
        return FakeResponse(
            [b"perfectly normal file bytes"],
            {"Content-Length": "28"},
            url="https://cdn.example.com/final/path/file.zip",
        )

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _urlopen)

    destination = tmp_path / "f.zip"

    result = download_to_disk_sync("https://example.com/redirects/here", destination, 1000)

    assert result == destination
    assert destination.read_bytes() == b"perfectly normal file bytes"


# ======================================================================
# Progress reporting
# ======================================================================


def test_progress_callback_receives_cumulative_bytes_and_declared_total(fake_urlopen, tmp_path):

    fake_urlopen(
        FakeResponse(
            [b"hello ", b"world!"],
            {"Content-Length": "12"},
        )
    )

    calls = []

    download_to_disk_sync(
        "https://example.com/f",
        tmp_path / "f",
        1000,
        progress_callback=lambda current, total: calls.append((current, total)),
    )

    # The fake response's chunk list is just its total byte content —
    # CHUNK_SIZE (512 KiB) is far bigger than this payload, so the real
    # read loop drains it in a single read() and calls back once, with
    # the declared total attached.
    assert calls == [(12, 12)]


def test_progress_callback_gets_none_total_when_content_length_missing(fake_urlopen, tmp_path):

    fake_urlopen(FakeResponse([b"data"], {}))

    calls = []

    download_to_disk_sync(
        "https://example.com/f",
        tmp_path / "f",
        1000,
        progress_callback=lambda current, total: calls.append((current, total)),
    )

    # Spec: never invent a total/percentage when the server didn't send
    # a usable Content-Length.
    assert calls == [(4, None)]


def test_broken_progress_callback_does_not_break_the_download(fake_urlopen, tmp_path):

    fake_urlopen(FakeResponse([b"hello world"], {"Content-Length": "11"}))

    destination = tmp_path / "f"

    def _boom(current, total):
        raise RuntimeError("boom")

    result = download_to_disk_sync(
        "https://example.com/f",
        destination,
        1000,
        progress_callback=_boom,
    )

    assert result == destination
    assert destination.read_bytes() == b"hello world"

# ======================================================================
# Expired-TLS-certificate fallback (services/url_downloader.py::
# _open_url_allow_expired_cert) — narrowly scoped to *expired* certs
# only, never other verification failures.
# ======================================================================


def _expired_cert_error(url="https://example.com/f"):

    import ssl as ssl_module

    reason = ssl_module.SSLCertVerificationError()
    reason.verify_message = "certificate has expired"

    return url_downloader.urllib.error.URLError(reason)


def _hostname_mismatch_error(url="https://example.com/f"):

    import ssl as ssl_module

    reason = ssl_module.SSLCertVerificationError()
    reason.verify_message = "Hostname mismatch, certificate is not valid for 'example.com'."

    return url_downloader.urllib.error.URLError(reason)


def test_expired_cert_falls_back_to_insecure_retry_when_enabled(monkeypatch, tmp_path):

    monkeypatch.setattr(url_downloader.Downloads, "ALLOW_EXPIRED_SSL_CERT_FALLBACK", True)

    calls = {"secure": 0, "insecure": 0}

    def _secure_urlopen(request, timeout=None):
        calls["secure"] += 1
        raise _expired_cert_error()

    class _FakeOpener:
        def open(self, request, timeout=None):
            calls["insecure"] += 1
            return FakeResponse([b"the file bytes"], {"Content-Length": "14"})

    def _fake_build_opener(*handlers):
        return _FakeOpener()

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _secure_urlopen)
    monkeypatch.setattr(url_downloader.urllib.request, "build_opener", _fake_build_opener)

    destination = tmp_path / "f"

    result = download_to_disk_sync("https://example.com/f", destination, 1000)

    assert result == destination
    assert destination.read_bytes() == b"the file bytes"
    assert calls == {"secure": 1, "insecure": 1}


def test_expired_cert_fallback_disabled_raises_ssl_expired(monkeypatch, tmp_path):

    monkeypatch.setattr(url_downloader.Downloads, "ALLOW_EXPIRED_SSL_CERT_FALLBACK", False)

    build_opener_calls = []

    def _secure_urlopen(request, timeout=None):
        raise _expired_cert_error()

    def _fake_build_opener(*handlers):
        build_opener_calls.append(handlers)
        raise AssertionError("must not attempt an insecure retry when disabled")

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _secure_urlopen)
    monkeypatch.setattr(url_downloader.urllib.request, "build_opener", _fake_build_opener)

    with pytest.raises(URLDownloadError) as exc_info:
        download_to_disk_sync("https://example.com/f", tmp_path / "f", 1000)

    assert exc_info.value.reason == url_downloader.REASON_SSL_EXPIRED
    assert build_opener_calls == []


def test_non_expired_cert_error_never_falls_back(monkeypatch, tmp_path):
    """A hostname mismatch (or any non-'expired' verification failure)
    could mean active tampering, not just a lapsed renewal — this must
    stay a hard failure even with the fallback enabled."""

    monkeypatch.setattr(url_downloader.Downloads, "ALLOW_EXPIRED_SSL_CERT_FALLBACK", True)

    build_opener_calls = []

    def _secure_urlopen(request, timeout=None):
        raise _hostname_mismatch_error()

    def _fake_build_opener(*handlers):
        build_opener_calls.append(handlers)
        raise AssertionError("must never retry insecurely for a non-expired cert error")

    monkeypatch.setattr(url_downloader.urllib.request, "urlopen", _secure_urlopen)
    monkeypatch.setattr(url_downloader.urllib.request, "build_opener", _fake_build_opener)

    with pytest.raises(URLDownloadError) as exc_info:
        download_to_disk_sync("https://example.com/f", tmp_path / "f", 1000)

    assert exc_info.value.reason == url_downloader.REASON_SSL_ERROR
    assert build_opener_calls == []


def test_expired_cert_error_message_is_persian_and_distinguishable():

    expired_text = url_downloader.describe_download_error(url_downloader.REASON_SSL_EXPIRED)
    other_ssl_text = url_downloader.describe_download_error(url_downloader.REASON_SSL_ERROR)
    network_text = url_downloader.describe_download_error(url_downloader.REASON_NETWORK)

    assert expired_text != other_ssl_text != network_text
    assert "SSL" in expired_text
