"""
URL validation for the URL-upload feature ("send a link instead of a
file"). Pure logic — no network calls happen unless `resolve_dns=True`,
in which case only a hostname resolution (socket.getaddrinfo) is done.

SSRF guard: every resolved address (and IP-literal hosts directly) is
checked against the private/loopback/link-local/reserved ranges, so a
user can't make the *server* fetch internal services (127.0.0.1, 10.x,
192.168.x, 169.254.x metadata endpoints, ...) or the bot's own bridge
infrastructure.
"""
from __future__ import annotations

import ipaddress

import re

import socket

from pathlib import Path

from urllib.parse import unquote, urlparse


_URL_ONLY_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

# Possible outcomes of validate_url() — stable English tokens the bot
# layer maps to Persian user-facing messages.
REASON_OK = "ok"
REASON_BAD_SCHEME = "bad_scheme"
REASON_NO_HOST = "no_host"
REASON_PRIVATE_ADDRESS = "private_address"
REASON_UNRESOLVABLE = "unresolvable"


def extract_url(text: str) -> str | None:
    """Returns the URL when the message is a URL-only text message
    (nothing but an http/https link, modulo surrounding whitespace) —
    deliberately strict so normal chat text is never mistaken for a
    download request."""

    if not text:
        return None

    candidate = text.strip()

    if _URL_ONLY_RE.match(candidate):
        return candidate

    return None


def is_private_ip(ip: str) -> bool:
    """True when the string is an IP literal in a range the server must
    never fetch: loopback, private, link-local (169.254.x — includes
    cloud metadata endpoints), reserved, multicast or unspecified."""

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False  # a hostname, not an IP literal

    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_url(
    url: str,
    resolve_dns: bool = True,
) -> tuple[bool, str]:
    """Returns (ok, reason) with reason one of the REASON_* constants.
    http/https only, a host must be present, and neither the host
    literal nor any of its DNS-resolved addresses may be private."""

    try:
        parsed = urlparse(url)
    except ValueError:
        return False, REASON_NO_HOST

    if parsed.scheme.lower() not in ("http", "https"):
        return False, REASON_BAD_SCHEME

    host = parsed.hostname

    if not host:
        return False, REASON_NO_HOST

    if is_private_ip(host):
        return False, REASON_PRIVATE_ADDRESS

    if resolve_dns:

        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False, REASON_UNRESOLVABLE
        except OSError:
            return False, REASON_UNRESOLVABLE

        for info in infos:

            address = info[4][0] if info[4] else None

            if address and is_private_ip(address):
                return False, REASON_PRIVATE_ADDRESS

    return True, REASON_OK


def filename_from_url(url: str) -> str:
    """Best-effort filename from the URL path (query string stripped,
    percent-encoding decoded). Falls back to "download" when the path is
    empty — callers that can't infer a type from the name will reject
    the URL before any download starts."""

    path = urlparse(url).path

    name = Path(unquote(path)).name if path else ""

    return name or "download"
