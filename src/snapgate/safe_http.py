# SNAPGATE module - do not edit. Overwritten on updates. Customize via SnapgateHooks.
"""The one guarded outbound HTTP client for user-supplied URLs.

Why this exists (DNS rebinding): a "resolve, check the IPs are public, then
fetch the hostname" guard has a check-then-connect race. The HTTP library
resolves the name a *second* time when it opens the socket, and an attacker who
controls DNS can answer the first lookup with a public address and the second
with ``127.0.0.1``, ``169.254.169.254`` (cloud metadata), or an internal
service IP. The pre-check passes; the connection goes inside.

The fix is to pin at connect time, not to pre-check. :class:`PinnedPublicBackend`
is an httpcore network backend whose ``connect_tcp`` resolves the host exactly
once, rejects the connection if ANY resolved address is non-public, and then
hands the validated IP *literal* to ``socket.create_connection``, so there is no
second lookup to race. TLS is untouched: httpcore calls
``start_tls(server_hostname=<original host>)``, so SNI and certificate hostname
verification still target the name, and the ``Host`` header comes from the
unchanged URL.

Pooled keep-alive connections are reused only for the same origin
(scheme, host, port), which was validated when that connection was opened. A
redirect to a different host opens a new connection and is validated again.

:func:`guarded_client` builds an ``httpx.Client`` with ``trust_env=False`` (so
an ``HTTP(S)_PROXY`` env var can never route the fetch around the check),
redirects off (callers follow them hop by hop), and only a
:class:`PinnedTransport`.
"""

from __future__ import annotations

import ipaddress
import socket
import typing
from collections.abc import Callable

import httpcore
import httpx

Resolver = Callable[[str], list[str]]


def resolve_host(host: str) -> list[str]:
    """Resolve ``host`` to the list of IP-literal strings it maps to.

    An IP literal is returned as-is (no DNS). A name is resolved via
    ``getaddrinfo`` across all A/AAAA records so the caller can reject if ANY
    resolved address is non-public.
    """
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({str(info[4][0]) for info in infos})


#: The NAT64 well-known prefix (RFC 6052): ``64:ff9b::a.b.c.d`` reaches the
#: IPv4 address ``a.b.c.d`` through a NAT64 gateway.
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")


def is_disallowed_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if ``addr`` is anything not globally routable (RFC 6890 special-use).

    Covers the SSRF-relevant non-public ranges: RFC1918 private, loopback
    (127/8, ::1), link-local (169.254/16 incl. the 169.254.169.254 cloud
    metadata endpoint, fe80::/10), multicast, reserved, the unspecified
    address, and every other special-use range ``is_global`` excludes
    (100.64/10 carrier-grade NAT, 192.0.0/24, 198.18/15 benchmarking, the
    TEST-NETs, ...). IPv4-mapped IPv6 (``::ffff:a.b.c.d``) and NAT64
    (``64:ff9b::a.b.c.d``) are unwrapped and re-checked so an embedded internal
    v4 address cannot slip through.
    """
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return is_disallowed_address(addr.ipv4_mapped)
        if addr in _NAT64:
            return is_disallowed_address(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    return (
        not addr.is_global
        or addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


class BlockedAddressError(httpcore.ConnectError):
    """A connection was refused because the host is (or resolves to) non-public.

    Subclasses ``httpcore.ConnectError`` so httpx surfaces it as
    ``httpx.ConnectError`` with this exception as ``__cause__``.
    """


#: Prefix of every refusal message; callers match on it to give a clear error.
REFUSAL_PREFIX = "refusing"


def blocked_reason(exc: BaseException) -> str | None:
    """The refusal message if ``exc`` was caused by a blocked address, else None."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, BlockedAddressError):
            return str(cur)
        cur = cur.__cause__ or cur.__context__
    msg = str(exc)
    if msg.startswith(REFUSAL_PREFIX):
        return msg
    return None


class PinnedPublicBackend(httpcore.SyncBackend):
    """httpcore backend that resolves once and connects to a validated IP.

    ``resolver`` defaults to :func:`resolve_host`, looked up on this module at
    call time (not bound at import) so tests can monkeypatch it. The address
    check likewise looks up :func:`is_disallowed_address` at call time.
    """

    def __init__(self, *, resolver: Resolver | None = None) -> None:
        self._resolver = resolver

    def _resolve(self, host: str) -> list[str]:
        try:
            ipaddress.ip_address(host)
            return [host]  # an IP literal needs no DNS (and cannot rebind)
        except ValueError:
            pass
        resolver = self._resolver or globals()["resolve_host"]
        try:
            return list(resolver(host))
        except OSError as e:
            raise BlockedAddressError(f"{REFUSAL_PREFIX}: could not resolve {host!r}: {e}") from e

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        addrs = self._resolve(host)
        if not addrs:
            raise BlockedAddressError(f"{REFUSAL_PREFIX}: host {host!r} resolved to no addresses")
        check = globals()["is_disallowed_address"]
        for a in addrs:
            try:
                ip = ipaddress.ip_address(a)
            except ValueError as e:
                raise BlockedAddressError(
                    f"{REFUSAL_PREFIX} unparseable address {a!r} for host {host!r}"
                ) from e
            if check(ip):
                raise BlockedAddressError(
                    f"{REFUSAL_PREFIX} non-public address {a} for host {host!r}"
                )
        last: Exception | None = None
        for a in addrs:
            try:
                return super().connect_tcp(a, port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last = e
        assert last is not None
        raise last

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        raise BlockedAddressError(f"{REFUSAL_PREFIX} unix socket connection {path!r}")


class PinnedTransport(httpx.HTTPTransport):
    """``httpx.HTTPTransport`` whose pool connects only via :class:`PinnedPublicBackend`.

    This deliberately skips ``HTTPTransport.__init__`` and builds the pool
    itself. It relies on httpx 0.28 keeping the pool in ``self._pool`` (checked
    against httpx 0.28.1 / httpcore 1.0.9);
    ``handle_request`` and ``close`` are inherited and only use ``self._pool``.
    ``tests/test_safe_http.py::test_pinned_transport_owns_its_pool`` fails
    loudly if an httpx upgrade changes that.
    """

    def __init__(self, *, resolver: Resolver | None = None, max_connections: int = 10) -> None:
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=PinnedPublicBackend(resolver=resolver),
            max_connections=max_connections,
            http1=True,
            http2=False,
            retries=0,
        )


def guarded_client(*, timeout: float, transport: PinnedTransport | None = None) -> httpx.Client:
    """An ``httpx.Client`` for fetching user-supplied URLs safely.

    Redirects are off (callers follow them hop by hop), ``trust_env=False`` so
    env proxies are never mounted, and the transport is always pinned.
    """
    if transport is not None and not isinstance(transport, PinnedTransport):
        raise TypeError("guarded_client requires a PinnedTransport")
    return httpx.Client(
        transport=transport or PinnedTransport(),
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )


__all__ = [
    "REFUSAL_PREFIX",
    "BlockedAddressError",
    "PinnedPublicBackend",
    "PinnedTransport",
    "blocked_reason",
    "guarded_client",
    "is_disallowed_address",
    "resolve_host",
]
