# SNAPGATE module - do not edit. Overwritten on updates. Customize via SnapgateHooks.
"""OAuth Client ID Metadata Documents: a chat app whose client_id is a URL.

draft-ietf-oauth-client-id-metadata-document (MCP 2025-11-25 prefers it to
dynamic registration). The client_id is an https URL; the JSON document there
describes the app (its name and redirect URIs). Snapgate fetches it through
:mod:`snapgate.safe_http` (public addresses only, pinned at connect time), never
follows a redirect, reads at most :data:`MAX_BYTES`, never fetches any URL named
inside the document, and caches a VALID document per its ``Cache-Control``
(never an error).

The document is checked only at the consent page. A connection already made is
not cut off if the document later changes or disappears: the token endpoint
compares the stored client_id string and fetches nothing.

Snapgate's own registered ids start ``sgo_``, so a client_id starting
``https://`` is always a metadata-document client.
"""

from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

URL_PREFIX = "https://"
MAX_URL_LENGTH = 512
#: The most of a document snapgate reads. The draft recommends 5 KB; real
#: documents are well under that, and 10 KB leaves room.
MAX_BYTES = 10 * 1024
#: Seconds, connect and read.
FETCH_TIMEOUT = 5.0
#: How long a document with no usable cache header is kept: 10 minutes.
DEFAULT_TTL = 600
#: Never trust a cached document longer than a day.
MAX_TTL = 24 * 3600
#: LRU cap on cached documents.
MAX_CACHED = 1000
MAX_REDIRECT_URIS = 20
MAX_NAME_LENGTH = 80

_JSON_TYPE = re.compile(r"^application/([a-z0-9.\-]+\+)?json$")


class ClientMetadataError(ValueError):
    """The client_id URL or its document is unusable. ``str(e)`` is safe to show."""


@dataclass(frozen=True)
class ClientMetadata:
    #: The URL, exactly as given.
    client_id: str
    client_name: str
    redirect_uris: tuple[str, ...]
    #: ``urlsplit(client_id).hostname``, lowercased.
    host: str


def is_metadata_client_id(client_id: str) -> bool:
    """True for a client_id that is a URL (the only branch point)."""
    return client_id.startswith(URL_PREFIX)


def redirect_uri_ok(uri: str) -> bool:
    """https anywhere; plain http only back to this machine (desktop clients).

    One rule for registered apps and metadata-document apps alike.
    """
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    if parts.fragment or "#" in uri or not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def check_client_id_url(url: str) -> str:
    """The host of a well-formed client_id URL; raises :class:`ClientMetadataError`.

    Plain parsing, no DNS: the address check happens only at connect time in
    :mod:`snapgate.safe_http`.
    """
    if len(url) > MAX_URL_LENGTH:
        raise ClientMetadataError("the app's address is too long")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        username, password = parts.username, parts.password
    except ValueError as e:
        raise ClientMetadataError("the app's address is not a valid URL") from e
    if parts.scheme != "https":
        raise ClientMetadataError("the app's address must be an https URL")
    if not host:
        raise ClientMetadataError("the app's address has no host")
    if username is not None or password is not None:
        raise ClientMetadataError("the app's address must not carry a user name or password")
    if parts.fragment or "#" in url:
        raise ClientMetadataError("the app's address must not have a fragment")
    if parts.query or "?" in url:
        raise ClientMetadataError("the app's address must not have a query")
    if parts.path in ("", "/"):
        raise ClientMetadataError("the app's address must have a path")
    for segment in parts.path.split("/"):
        if segment in (".", "..") or re.fullmatch(r"(\.|%2e){1,2}", segment, re.IGNORECASE):
            raise ClientMetadataError("the app's address must not have . or .. segments")
    host = host.lower()
    if _is_ip_literal(host):
        raise ClientMetadataError("the app's address must use a domain name, not an IP address")
    if "." not in host:
        raise ClientMetadataError("the app's address must use a public domain name")
    return host


def _clean_name(raw: str) -> str:
    """Drop control and other non-printing characters (the name is shown on pages)."""
    text = "".join(c for c in raw if unicodedata.category(c)[0] != "C")
    return text.strip()[:MAX_NAME_LENGTH].strip()


def parse_client_metadata(url: str, raw: bytes) -> ClientMetadata:
    """Validate a fetched document for ``url``. Pure, so it is easy to test."""
    host = check_client_id_url(url)
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise ClientMetadataError("the app's description is not a JSON object") from e
    if not isinstance(doc, dict):
        raise ClientMetadataError("the app's description is not a JSON object")
    if doc.get("client_id") != url:
        raise ClientMetadataError(
            "the app's description has a client_id that does not match its address"
        )
    name = doc.get("client_name")
    name = _clean_name(name) if isinstance(name, str) else ""
    if not name:
        raise ClientMetadataError("the app's description has no client_name")
    uris = doc.get("redirect_uris")
    if (
        not isinstance(uris, list)
        or not uris
        or len(uris) > MAX_REDIRECT_URIS
        or not all(isinstance(u, str) and redirect_uri_ok(u) for u in uris)
    ):
        raise ClientMetadataError(
            "the app's description needs redirect_uris: https URLs "
            "(http only for localhost), without a fragment"
        )
    if "client_secret" in doc or "client_secret_expires_at" in doc:
        raise ClientMetadataError("the app's description must not contain a client secret")
    # A client may prefer another method (ChatGPT's says ``private_key_jwt``)
    # while also listing ``none``; this server only offers ``none``
    # (token_endpoint_auth_methods_supported), so such a client signs in as a
    # public client with PKCE.
    method = doc.get("token_endpoint_auth_method")
    supported = doc.get("token_endpoint_auth_methods_supported")
    also_none = isinstance(supported, list) and "none" in supported
    if method is not None and method != "none" and not also_none:
        raise ClientMetadataError(
            "only public clients are supported (token_endpoint_auth_method none)"
        )
    grant_types = doc.get("grant_types")
    if grant_types is not None and (
        not isinstance(grant_types, list) or "authorization_code" not in grant_types
    ):
        raise ClientMetadataError("the app's description must allow the authorization_code grant")
    response_types = doc.get("response_types")
    if response_types is not None and (
        not isinstance(response_types, list) or "code" not in response_types
    ):
        raise ClientMetadataError("the app's description must allow response type code")
    return ClientMetadata(client_id=url, client_name=name, redirect_uris=tuple(uris), host=host)


def cache_ttl(headers: Mapping[str, str]) -> int:
    """Seconds to cache a valid document, from ``Cache-Control`` only."""
    raw = headers.get("cache-control") or ""
    directives = [d.strip().lower() for d in raw.split(",") if d.strip()]
    if any(d in ("no-store", "no-cache", "private") for d in directives):
        return 0
    for d in directives:
        if d.startswith("max-age="):
            value = d.removeprefix("max-age=").strip().strip('"')
            if value.isdigit():
                return min(int(value), MAX_TTL)
    return DEFAULT_TTL


def fetch_client_metadata(
    url: str, *, transport=None, app_name: str = "snapgate"
) -> tuple[ClientMetadata, int]:
    """Fetch and validate the document at ``url``: ``(metadata, ttl_seconds)``. No cache.

    ``app_name`` goes into the ``User-Agent``.
    """
    import httpx

    from . import __version__
    from .safe_http import blocked_reason, guarded_client

    check_client_id_url(url)
    try:
        with (
            guarded_client(timeout=FETCH_TIMEOUT, transport=transport) as http,
            http.stream(
                "GET",
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": f"snapgate/{__version__} ({app_name})",
                },
            ) as resp,
        ):
            status = resp.status_code
            if 300 <= status < 400:
                raise ClientMetadataError(
                    "the app's description redirects; it must be served directly"
                )
            if status != 200:
                raise ClientMetadataError(f"the app's description answered HTTP {status}")
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if not _JSON_TYPE.match(ctype):
                raise ClientMetadataError("the app's description is not served as JSON")
            length = resp.headers.get("content-length")
            if length is not None and length.strip().isdigit() and int(length) > MAX_BYTES:
                raise ClientMetadataError("the app's description is too large")
            body = bytearray()
            for chunk in resp.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ClientMetadataError("the app's description is too large")
            ttl = cache_ttl(resp.headers)
    except httpx.ConnectError as e:
        if blocked_reason(e) is not None:
            raise ClientMetadataError("the app's address is not public") from e
        raise ClientMetadataError("the app's description could not be fetched") from e
    except httpx.TimeoutException as e:
        raise ClientMetadataError("fetching the app's description timed out") from e
    except httpx.HTTPError as e:
        raise ClientMetadataError("the app's description could not be fetched") from e
    return parse_client_metadata(url, bytes(body)), ttl


Fetch = Callable[[str], "tuple[ClientMetadata, int]"]


class ClientMetadataCache:
    """In-process, thread-safe LRU of valid documents.

    In-process like snapgate's rate limits (snapgate assumes one process);
    losing the cache on restart only costs a fetch. Only successes with a positive TTL are stored; an error is never cached.
    """

    def __init__(
        self,
        fetch: Fetch = fetch_client_metadata,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = MAX_CACHED,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._max = max_entries
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[ClientMetadata, float]] = OrderedDict()

    def peek(self, url: str) -> ClientMetadata | None:
        """The cached document for ``url``, or None. Never fetches."""
        with self._lock:
            entry = self._entries.get(url)
            if entry is None:
                return None
            md, expires = entry
            if expires <= self._clock():
                del self._entries[url]
                return None
            self._entries.move_to_end(url)
            return md

    def get(self, url: str) -> ClientMetadata:
        """A valid document for ``url``, from cache or a fresh fetch. Raises ClientMetadataError."""
        cached = self.peek(url)
        if cached is not None:
            return cached
        md, ttl = self._fetch(url)
        if ttl > 0:
            with self._lock:
                self._entries[url] = (md, self._clock() + ttl)
                self._entries.move_to_end(url)
                while len(self._entries) > self._max:
                    self._entries.popitem(last=False)
        return md

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


__all__ = [
    "MAX_BYTES",
    "ClientMetadata",
    "ClientMetadataCache",
    "ClientMetadataError",
    "cache_ttl",
    "check_client_id_url",
    "fetch_client_metadata",
    "is_metadata_client_id",
    "parse_client_metadata",
    "redirect_uri_ok",
]
