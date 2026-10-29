#!/usr/bin/env python3
"""Bounded HTTPS downloads. Every caller passes its own hostname allowlist."""

from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

DEFAULT_MAX_BYTES = 8_000_000


class FetchError(Exception):
    """A download was refused or could not be completed inside the limits."""


def check_url(url: str, allow_hosts: set[str]) -> str:
    """Return a canonical HTTPS URL, or raise FetchError."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise FetchError("refusing a non-HTTPS URL")
    if parts.username or parts.password or "@" in parts.netloc:
        raise FetchError("refusing a credential-bearing URL")
    if parts.port not in (None, 443):
        raise FetchError("refusing a non-standard port")
    host = (parts.hostname or "").lower().rstrip(".")
    allowed = {item.lower().rstrip(".") for item in allow_hosts}
    if not host or host not in allowed:
        raise FetchError("host is not on the allowlist")
    # Drop any userinfo and force the allowed hostname spelling.
    netloc = host
    path = parts.path or "/"
    return urlunsplit(("https", netloc, path, parts.query, ""))


class _AllowlistRedirect(HTTPRedirectHandler):
    max_redirections = 5

    def __init__(self, allow_hosts: set[str]):
        super().__init__()
        self.allow_hosts = allow_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute = urljoin(req.full_url, newurl)
        checked = check_url(absolute, self.allow_hosts)
        return super().redirect_request(req, fp, code, msg, headers, checked)


def fetch_bytes(
    url: str,
    *,
    allow_hosts: set[str] | frozenset[str],
    timeout: float = 30,
    max_bytes: int = DEFAULT_MAX_BYTES,
    data: bytes | None = None,
    headers: dict | None = None,
) -> bytes:
    """GET or POST an allowlisted HTTPS URL. Redirects are checked before use."""
    checked = check_url(url, set(allow_hosts))
    hdrs = {"User-Agent": "UKSupplyWatch/1.0", "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    req = Request(checked, data=data, headers=hdrs)
    opener = build_opener(_AllowlistRedirect(set(allow_hosts)), HTTPSHandler())
    try:
        with opener.open(req, timeout=timeout) as resp:
            check_url(resp.geturl(), set(allow_hosts))
            declared = resp.headers.get("Content-Length")
            if declared is not None:
                try:
                    if int(declared) > max_bytes:
                        raise FetchError("declared response is larger than the limit")
                except ValueError as exc:
                    raise FetchError("declared response size is not a number") from exc
            chunks: list[bytes] = []
            total = 0
            while True:
                block = resp.read(65536)
                if not block:
                    break
                total += len(block)
                if total > max_bytes:
                    raise FetchError("response exceeded the size limit")
                chunks.append(block)
            return b"".join(chunks)
    except FetchError:
        raise
    except HTTPError as exc:
        raise FetchError(f"upstream returned {exc.code}") from exc
    except URLError as exc:
        raise FetchError("upstream request failed") from exc
