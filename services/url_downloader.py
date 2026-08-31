"""
Streaming HTTP(S) downloader for URL jobs ("URL uploader").

The bytes are fetched with the stdlib (urllib) inside a worker thread
(asyncio.to_thread) — no new dependency. Size limits are enforced twice:
up front via the Content-Length header when present, and as a hard cap
while streaming, so a missing or lying header can't overflow the disk
(these mirror worker.py's pre-download guards for uploaded files, which
the resulting Job then goes through as well).
"""
from __future__ import annotations

import asyncio

import re

from pathlib import Path

import socket

import urllib.error

import urllib.parse

import urllib.request

from core.logger import get_logger

logger = get_logger(__name__)

CHUNK_SIZE = 512 * 1024

DEFAULT_TIMEOUT_SECONDS = 30

USER_AGENT = "Mozilla/5.0 (compatible; telegram-file-processor)"

# Stable reason tokens (same style as utils/url_validation.py).
REASON_NETWORK = "network"
REASON_TIMEOUT = "timeout"
REASON_TOO_LARGE = "too_large"
REASON_EMPTY = "empty"
REASON_LINK_RESOLVE_FAILED = "link_resolve_failed"
REASON_PASSWORD_PROTECTED = "password_protected"


class URLDownloadError(Exception):
    """Raised for any download failure; `reason` is one of the REASON_*
    tokens (the worker maps it to a Persian bridge error message) and
    `detail` is a human-readable English string for the log."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def describe_download_error(reason: str, detail: str = "") -> str:
    """Shared Persian user-facing text for a download/resolve failure.
    Used both at submission time (handlers/core.py — before a Job even
    exists, e.g. a dead/password-protected link caught while resolving
    it just to detect the file type) and at actual-download time
    (worker.py). REASON_TOO_LARGE gets only a generic message here since
    this module doesn't know a user's tier limit — worker.py renders a
    more specific one itself for that one reason before falling back to
    this."""

    if reason == REASON_TIMEOUT:
        return "❌ دانلود فایل بیش از حد طول کشید و متوقف شد. لطفاً دوباره تلاش کنید."

    if reason == REASON_EMPTY:
        return "❌ فایل دریافتی از لینک خالی بود."

    if reason == REASON_PASSWORD_PROTECTED:
        return (
            "❌ این فایل با رمز عبور محافظت شده است. در حال حاضر لینک‌های "
            "دارای رمز پشتیبانی نمی‌شوند."
        )

    if reason == REASON_LINK_RESOLVE_FAILED:
        return (
            "❌ دریافت لینک مستقیم از این صفحه ناموفق بود. لطفاً بررسی کنید "
            "لینک هنوز معتبر است یا لینک دانلود مستقیم فایل را ارسال کنید."
        )

    if reason == REASON_TOO_LARGE:
        return "❌ حجم فایل در لینک بیشتر از حد مجاز است."

    return "❌ دانلود فایل از لینک ناموفق بود. لطفاً لینک را بررسی کنید."


def _request_headers(url: str) -> dict:
    """Many file hosts (picofile.com among them) guard their "get direct
    link" URLs with a same-origin Referer check: the token is only meant
    to be followed from their own download page, and a request without a
    matching Referer gets a small HTML/JSON error page back instead of
    the file — which looks exactly like "downloads a few KB instead of
    the real file". Setting Referer to the download URL's own origin is
    a generic fix that costs nothing for hosts that don't check it, and
    resolves the common case for ones that do.

    This does NOT help if a host instead binds the token to the
    requesting IP address (the browser that clicked "get direct link"
    must be the same IP that then downloads it) — that's a server-side
    session check no request header can work around, since this bot
    downloads from its own server, not the user's device."""

    from urllib.parse import urlsplit

    headers = {"User-Agent": USER_AGENT}

    parsed = urlsplit(url)

    if parsed.scheme and parsed.netloc:
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

    return headers


# =====================================================
# Landing-page -> direct-link resolution
#
# Some hosts hand a user a *page* URL, not a direct download link — the
# real link only exists after a "get direct link" button makes one more
# request. Reproducing that single request here means the bot accepts
# the page URL a person would actually share, and — since the resolve
# request and the download itself both happen from this same server —
# it also sidesteps any binding of the generated link to the requesting
# IP address, which a plain Referer header cannot do (see CLAUDE.md).
#
# Each entry is (host matcher, resolver). A resolver returns the direct
# URL, or None if this particular URL on that host doesn't need
# resolving (e.g. it's already a direct link).
# =====================================================

_PICOFILE_FILE_PAGE = re.compile(r"^/file/(?P<file_id>\d+)/")


def _is_picofile_host(netloc: str) -> bool:

    host = netloc.split(":")[0].lower()

    return host == "picofile.com" or host.endswith(".picofile.com")


def _resolve_picofile_link(parsed, timeout: int) -> str | None:

    match = _PICOFILE_FILE_PAGE.match(parsed.path)

    if not match:
        # Already a direct /d/... link (or something else on the same
        # host) — nothing to resolve.
        return None

    file_id = match.group("file_id")
    origin = f"{parsed.scheme}://{parsed.netloc}"

    request = urllib.request.Request(
        f"{origin}/file/generateDownloadLink?fileId={file_id}",
        data=urllib.parse.urlencode({"password": ""}).encode(),
        headers={
            "User-Agent": USER_AGENT,
            "Referer": f"{origin}/",
        },
        method="POST",
    )

    try:
        response = urllib.request.urlopen(request, timeout=timeout)

    except urllib.error.HTTPError as e:

        if e.code == 403:
            raise URLDownloadError(
                REASON_PASSWORD_PROTECTED,
                f"picofile file {file_id} requires a password",
            )

        raise URLDownloadError(
            REASON_LINK_RESOLVE_FAILED,
            f"picofile generateDownloadLink returned HTTP {e.code}",
        )

    except (urllib.error.URLError, socket.timeout) as e:

        raise URLDownloadError(
            REASON_LINK_RESOLVE_FAILED,
            f"picofile generateDownloadLink request failed: {e}",
        )

    direct_url = response.read(4096).decode("utf-8", errors="replace").strip()

    if not direct_url.lower().startswith("http"):
        raise URLDownloadError(
            REASON_LINK_RESOLVE_FAILED,
            f"unexpected generateDownloadLink response: {direct_url[:200]!r}",
        )

    logger.info("Resolved picofile file %s to a direct download link", file_id)

    return direct_url


_LINK_RESOLVERS = [
    (_is_picofile_host, _resolve_picofile_link),
]


def resolve_download_url(url: str, timeout: int) -> str:

    parsed = urllib.parse.urlsplit(url)

    for matches_host, resolver in _LINK_RESOLVERS:

        if matches_host(parsed.netloc):

            resolved = resolver(parsed, timeout)

            if resolved:
                return resolved

            break

    return url


def _open_url(url: str, timeout: int):
    """Single-attempt HTTP GET (urllib follows redirects itself).
    Factored out so the stale-link retry below can reopen a freshly
    resolved URL with identical request-building logic."""

    request = urllib.request.Request(
        url,
        headers=_request_headers(url),
    )

    return urllib.request.urlopen(request, timeout=timeout)


def _stale_picofile_landing_page(response) -> str | None:
    """picofile does not error when a `/d/...` direct link has expired —
    it silently redirects to the file's *landing page* instead, and a
    plain downloader saves that HTML as if it were the real file (this
    is exactly the "downloads a few KB, unrelated to the real file"
    symptom). urllib follows the redirect on its own, so the only way to
    notice is checking where we actually ended up. Returns that landing
    page URL (to hand back to resolve_download_url) if this happened,
    else None."""

    final_url = response.geturl() or ""
    parsed = urllib.parse.urlsplit(final_url)

    if _is_picofile_host(parsed.netloc) and _PICOFILE_FILE_PAGE.match(parsed.path):
        return final_url

    return None


def _response_headers(response) -> dict:

    # urllib headers object is case-insensitive already; wrap for tests.
    class _CaseInsensitive(dict):

        def __getitem__(self, key):
            for k, v in self.items():
                if k.lower() == key.lower():
                    return v
            raise KeyError(key)

        def get(self, key, default=None):
            for k, v in self.items():
                if k.lower() == key.lower():
                    return v
            return default

    return _CaseInsensitive(dict(response.headers.items()))


def download_to_disk_sync(
    url: str,
    destination: Path,
    max_size: int,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    progress_callback=None,
) -> Path:
    """Blocking streaming download. Raises URLDownloadError on any
    failure; on success the file exists at `destination` and its size is
    guaranteed <= max_size."""

    url = resolve_download_url(url, timeout)

    try:
        response = _open_url(url, timeout)

        stale_landing_page = _stale_picofile_landing_page(response)

        if stale_landing_page:

            # The /d/... link we had — given directly, or resolved
            # earlier (e.g. at submission time, well before this actual
            # download runs) — has since expired. Refresh it once via
            # the same generateDownloadLink request and retry; this is
            # what makes a stale picofile link "just work" instead of
            # silently downloading a few KB of HTML.
            response.close()

            fresh_url = resolve_download_url(stale_landing_page, timeout)

            if fresh_url == stale_landing_page:
                raise URLDownloadError(
                    REASON_LINK_RESOLVE_FAILED,
                    "picofile link expired and could not be refreshed",
                )

            logger.info(
                "Expired picofile link refreshed, retrying the download"
            )

            url = fresh_url
            response = _open_url(url, timeout)

            if _stale_picofile_landing_page(response):
                response.close()
                raise URLDownloadError(
                    REASON_LINK_RESOLVE_FAILED,
                    "picofile kept redirecting to the landing page "
                    "after a refresh",
                )

    except urllib.error.HTTPError as e:
        raise URLDownloadError(
            REASON_NETWORK,
            f"HTTP {e.code}",
        ) from e
    except urllib.error.URLError as e:

        if isinstance(e.reason, socket.timeout) or isinstance(e.reason, TimeoutError):
            raise URLDownloadError(
                REASON_TIMEOUT,
                "connection timed out",
            ) from e

        raise URLDownloadError(
            REASON_NETWORK,
            str(e.reason),
        ) from e
    except socket.timeout as e:
        raise URLDownloadError(
            REASON_TIMEOUT,
            "connection timed out",
        ) from e
    except TimeoutError as e:
        raise URLDownloadError(
            REASON_TIMEOUT,
            "connection timed out",
        ) from e

    headers = _response_headers(response)

    with response:

        declared = headers.get("Content-Length")

        declared_size = None

        if declared and str(declared).strip().isdigit():

            declared_size = int(str(declared).strip())

            if declared_size > max_size:

                logger.warning(
                    "URL download rejected: Content-Length %s > max %s",
                    declared_size,
                    max_size,
                )

                raise URLDownloadError(
                    REASON_TOO_LARGE,
                    f"Content-Length {declared_size}",
                )

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        received = 0

        try:

            with open(destination, "wb") as fh:

                while True:

                    chunk = response.read(CHUNK_SIZE)

                    if not chunk:
                        break

                    received += len(chunk)

                    # Hard cap while streaming — the header may be
                    # missing or simply lying.
                    if received > max_size:

                        raise URLDownloadError(
                            REASON_TOO_LARGE,
                            f"streamed {received} bytes",
                        )

                    fh.write(chunk)

                    if progress_callback is not None:
                        try:
                            progress_callback(received, declared_size)
                        except Exception:
                            # A broken progress callback must never take
                            # the actual download down.
                            logger.exception(
                                "URL download progress callback failed"
                            )

        except socket.timeout as e:
            raise URLDownloadError(
                REASON_TIMEOUT,
                "timed out mid-download",
            ) from e
        except TimeoutError as e:
            raise URLDownloadError(
                REASON_TIMEOUT,
                "timed out mid-download",
            ) from e

    if received == 0:

        try:
            destination.unlink()
        except OSError:
            pass

        raise URLDownloadError(
            REASON_EMPTY,
            "server returned zero bytes",
        )

    logger.info(
        "URL download finished: %s (%s bytes)",
        destination,
        received,
    )

    return destination


async def download_to_disk(
    url: str,
    destination: Path,
    max_size: int,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    progress_callback=None,
) -> Path:
    """Async wrapper — runs the blocking streaming download in a worker
    thread so the Telethon event loop never blocks.

    `progress_callback`, if given, is a plain synchronous `(current,
    total)` callable — it runs on the worker thread, same as the
    ProgressReporter callbacks used for Telethon transfers, and must stay
    cheap (it just updates a few attributes; it never touches Telegram
    directly). total is None when the server didn't send a usable
    Content-Length."""

    return await asyncio.to_thread(
        download_to_disk_sync,
        url,
        destination,
        max_size,
        timeout,
        progress_callback,
    )
