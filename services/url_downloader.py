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

from pathlib import Path

import socket

import urllib.error

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


class URLDownloadError(Exception):
    """Raised for any download failure; `reason` is one of the REASON_*
    tokens (the worker maps it to a Persian bridge error message) and
    `detail` is a human-readable English string for the log."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


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
) -> Path:
    """Blocking streaming download. Raises URLDownloadError on any
    failure; on success the file exists at `destination` and its size is
    guaranteed <= max_size."""

    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT},
    )

    try:
        response = urllib.request.urlopen(
            request,
            timeout=timeout,
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
) -> Path:
    """Async wrapper — runs the blocking streaming download in a worker
    thread so the Telethon event loop never blocks."""

    return await asyncio.to_thread(
        download_to_disk_sync,
        url,
        destination,
        max_size,
        timeout,
    )
