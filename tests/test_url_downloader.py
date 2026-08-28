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

    def __init__(self, chunks: list[bytes], headers: dict | None = None):
        self._stream = io.BytesIO(b"".join(chunks))
        self.headers = headers or {}

    def read(self, size: int) -> bytes:
        return self._stream.read(size)

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