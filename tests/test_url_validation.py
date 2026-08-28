"""
Tests for utils/url_validation.py — the URL-upload entry validation
(scheme whitelist, SSRF guard, URL-only message detection, filename
extraction). DNS resolution is monkeypatched; no real network calls.
"""

import socket

from utils.url_validation import (
    REASON_BAD_SCHEME,
    REASON_NO_HOST,
    REASON_OK,
    REASON_PRIVATE_ADDRESS,
    REASON_UNRESOLVABLE,
    extract_url,
    filename_from_url,
    is_private_ip,
    validate_url,
)


# ======================================================================
# extract_url — URL-only messages
# ======================================================================


def test_extract_url_accepts_plain_http_url():

    assert extract_url("https://example.com/file.mp4") == "https://example.com/file.mp4"
    assert extract_url("http://example.com/file.mp4") == "http://example.com/file.mp4"


def test_extract_url_tolerates_surrounding_whitespace():

    assert extract_url("  https://example.com/a.zip  ") == "https://example.com/a.zip"


def test_extract_url_rejects_text_containing_more_than_a_url():

    assert extract_url("check this https://example.com/a.zip") is None
    assert extract_url("https://example.com/a.zip please") is None
    assert extract_url("سلام https://example.com/a.zip") is None


def test_extract_url_rejects_non_http_schemes():

    assert extract_url("ftp://example.com/file.zip") is None
    assert extract_url("file:///etc/passwd") is None


def test_extract_url_empty():

    assert extract_url("") is None
    assert extract_url(None) is None


# ======================================================================
# is_private_ip — SSRF guard on IP literals
# ======================================================================


def test_private_and_local_ip_literals_are_rejected():

    for ip in [
        "127.0.0.1",
        "10.1.2.3",
        "192.168.1.5",
        "172.16.0.9",
        "169.254.169.254",  # cloud metadata endpoint
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
    ]:
        assert is_private_ip(ip) is True, ip


def test_public_ips_and_hostnames_are_not_flagged():

    assert is_private_ip("8.8.8.8") is False
    assert is_private_ip("1.1.1.1") is False
    assert is_private_ip("example.com") is False  # hostname, not an IP


# ======================================================================
# validate_url — scheme/host/DNS
# ======================================================================


def test_validate_url_rejects_non_http_schemes():

    ok, reason = validate_url("ftp://example.com/file.zip", resolve_dns=False)

    assert ok is False
    assert reason == REASON_BAD_SCHEME


def test_validate_url_rejects_missing_host():

    ok, reason = validate_url("https:///file.zip", resolve_dns=False)

    assert ok is False
    assert reason == REASON_NO_HOST


def test_validate_url_rejects_private_ip_literal_hosts():

    for url in [
        "http://127.0.0.1/file.zip",
        "http://10.0.0.5/file.zip",
        "http://192.168.1.1/file.zip",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/file.zip",
    ]:
        ok, reason = validate_url(url, resolve_dns=False)

        assert ok is False, url
        assert reason == REASON_PRIVATE_ADDRESS


def test_validate_url_rejects_localhost_names_resolving_to_loopback(monkeypatch):

    def fake_getaddrinfo(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    ok, reason = validate_url("http://internal.local/file.zip")

    assert ok is False
    assert reason == REASON_PRIVATE_ADDRESS


def test_validate_url_accepts_public_dns_resolution(monkeypatch):

    def fake_getaddrinfo(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    ok, reason = validate_url("https://example.com/file.mp4")

    assert ok is True
    assert reason == REASON_OK


def test_validate_url_rejects_unresolvable_hosts(monkeypatch):

    def fake_getaddrinfo(host, port):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    ok, reason = validate_url("https://does-not-exist.example/file.mp4")

    assert ok is False
    assert reason == REASON_UNRESOLVABLE


def test_validate_url_can_skip_dns_resolution():

    ok, reason = validate_url("https://example.com/file.mp4", resolve_dns=False)

    assert ok is True


# ======================================================================
# filename_from_url
# ======================================================================


def test_filename_from_url_basic():

    assert filename_from_url("https://example.com/files/course.rar") == "course.rar"


def test_filename_from_url_strips_query_and_decodes_percent():

    assert (
        filename_from_url("https://example.com/a%20b.mp4?download=1&x=2")
        == "a b.mp4"
    )


def test_filename_from_url_falls_back_when_path_is_empty():

    assert filename_from_url("https://example.com") == "download"