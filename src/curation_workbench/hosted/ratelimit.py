"""In-process rate limits (stdlib sliding window).

Copied from Pigeon. The limiter is IN-PROCESS. It is correct only because the
hosted service runs ONE replica and ONE uvicorn worker. Scaling past that multiplies every limit by the
process count; move the counters to Postgres or Redis first. Counters reset on
restart.

Client identity: production runs ``uvicorn --proxy-headers
--forwarded-allow-ips='*'``, which makes ``request.client.host`` the LEFTMOST
``X-Forwarded-For`` entry, and that is client-controlled. So when
``CWB_CLIENT_IP_HEADER`` is set (production: ``CF-Connecting-IP``, which
Cloudflare overwrites on every proxied request), that header wins whenever it
carries a valid IP.

Scope: per-IP limits on the login routes are snapgate's own limits. This module keeps what those cannot
express: failed-credential counting and per-user limits.

Env: ``CWB_RATE_LIMITS=off`` disables everything;
``CWB_RL_<BUCKET>=count/seconds`` overrides one bucket, e.g.
``CWB_RL_USER_REQUESTS=600/60``. New buckets are fields on
:class:`RateLimitConfig`, never a second limiter. The GitHub webhook has its
own per-resident limit (``hosted.sync.webhook``).
"""

from __future__ import annotations

import dataclasses
import ipaddress
import math
import os
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fastapi import Request

from snapgate.service import RateLimited


@dataclass(frozen=True)
class Limit:
    """At most ``count`` events per sliding ``window_s`` seconds."""

    count: int
    window_s: float

    @classmethod
    def parse(cls, text: str) -> Limit:
        """Parse ``"count/seconds"``, e.g. ``"20/60"``. Raises ``ValueError``."""
        parts = text.strip().split("/")
        if len(parts) != 2:
            raise ValueError(f"rate limit must look like 'count/seconds', got {text!r}")
        count, window = int(parts[0]), float(parts[1])
        if count < 1 or window <= 0:
            raise ValueError(f"rate limit must be positive, got {text!r}")
        return cls(count, window)


_OFF = {"off", "0", "false", "no"}


@dataclass(frozen=True)
class RateLimitConfig:
    enabled: bool = True
    #: Header carrying the real client IP (production: ``CF-Connecting-IP``).
    client_ip_header: str | None = None
    #: Failed JWT / API-key attempts per client IP.
    bad_credential_per_ip: Limit = Limit(20, 300)
    #: Every authenticated ``/api/v1`` request, per user id.
    user_requests: Limit = Limit(1200, 60)
    #: Memory bound on distinct keys tracked per bucket.
    max_keys_per_bucket: int = 50_000

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ, **overrides) -> RateLimitConfig:
        """Build from env vars (``overrides`` win). A malformed value raises ``ValueError``."""
        kw: dict = {}
        flag = env.get("CWB_RATE_LIMITS")
        if flag is not None and flag.strip().lower() in _OFF:
            kw["enabled"] = False
        header = (env.get("CWB_CLIENT_IP_HEADER") or "").strip()
        if header:
            kw["client_ip_header"] = header
        for f in dataclasses.fields(cls):
            if f.type in (Limit, "Limit"):
                name = f"CWB_RL_{f.name.upper()}"
                raw = env.get(name)
                if raw is not None and raw.strip():
                    try:
                        kw[f.name] = Limit.parse(raw)
                    except ValueError as e:
                        raise ValueError(f"{name}: {e}") from e
        kw.update({k: v for k, v in overrides.items()})
        return cls(**kw)

    def limit(self, bucket: str) -> Limit:
        value = getattr(self, bucket, None)
        if not isinstance(value, Limit):
            raise KeyError(f"unknown rate-limit bucket {bucket!r}")
        return value


def _retry_after(seconds: float) -> int:
    return max(1, math.ceil(seconds))


class SlidingWindowLimiter:
    """Sliding-window counters keyed by ``(bucket, key)``, in process memory.

    Thread-safe (sync routes run in the threadpool). Memory is bounded: every
    1024 operations a bucket is swept of empty keys, and a bucket over
    ``max_keys_per_bucket`` drops its least-recently-touched keys. Under a
    flood of distinct keys, eviction can reset an attacker's own counter; the
    per-user buckets are the backstop.
    """

    _SWEEP_EVERY = 1024

    def __init__(self, config: RateLimitConfig, *, clock: Callable[[], float] = time.monotonic):
        self.config = config
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, OrderedDict[str, deque[float]]] = {}
        self._ops = 0

    def _prune(self, q: deque[float], window: float, now: float) -> None:
        cutoff = now - window
        while q and q[0] <= cutoff:
            q.popleft()

    def _entry(self, bucket: str, key: str) -> deque[float]:
        b = self._buckets.setdefault(bucket, OrderedDict())
        q = b.get(key)
        if q is None:
            q = b[key] = deque()
        else:
            b.move_to_end(key)
        return q

    def _maybe_sweep(self, bucket: str, window: float, now: float) -> None:
        self._ops += 1
        b = self._buckets.get(bucket)
        if b is None:
            return
        if self._ops % self._SWEEP_EVERY == 0:
            empty = []
            for k, q in b.items():
                self._prune(q, window, now)
                if not q:
                    empty.append(k)
            for k in empty:
                del b[k]
        while len(b) > self.config.max_keys_per_bucket:
            b.popitem(last=False)

    def _window(self, bucket: str) -> float:
        try:
            return self.config.limit(bucket).window_s
        except KeyError:
            return 3600.0

    def hit(self, bucket: str, key: str, limit: Limit) -> None:
        """Count one event; raise :class:`RateLimited` if already at the limit."""
        if not self.config.enabled:
            return
        with self._lock:
            now = self._clock()
            q = self._entry(bucket, key)
            self._prune(q, limit.window_s, now)
            if len(q) >= limit.count:
                self._maybe_sweep(bucket, limit.window_s, now)
                raise RateLimited(_retry_after(q[0] + limit.window_s - now))
            q.append(now)
            self._maybe_sweep(bucket, limit.window_s, now)

    def check(self, bucket: str, key: str, limit: Limit) -> None:
        """Like :meth:`hit` but records nothing (for failure-counting buckets)."""
        if not self.config.enabled:
            return
        with self._lock:
            now = self._clock()
            b = self._buckets.get(bucket)
            q = b.get(key) if b is not None else None
            if not q:
                return
            self._prune(q, limit.window_s, now)
            if len(q) >= limit.count:
                raise RateLimited(_retry_after(q[0] + limit.window_s - now))

    def record(self, bucket: str, key: str) -> None:
        """Record one event without testing the limit (e.g. a failed credential)."""
        if not self.config.enabled:
            return
        with self._lock:
            now = self._clock()
            self._entry(bucket, key).append(now)
            self._maybe_sweep(bucket, self._window(bucket), now)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._ops = 0


# -- request helpers ----------------------------------------------------------


def _limiter(request: Request) -> SlidingWindowLimiter | None:
    return getattr(request.app.state, "limits", None)


def client_ip(request: Request) -> str:
    """The client IP to key limits on. Never returns unbounded attacker text."""
    limiter = _limiter(request)
    header = limiter.config.client_ip_header if limiter is not None else None
    if header:
        raw = request.headers.get(header)
        if raw:
            first = raw.split(",")[0].strip()
            try:
                return ipaddress.ip_address(first).compressed
            except ValueError:
                pass
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


def charge(limiter: SlidingWindowLimiter | None, bucket: str, key: str) -> None:
    """Count one event against ``bucket`` for ``key``; :class:`RateLimited` when over.

    The request-free core of :func:`enforce`, for adapters that have no route
    dependency to lean on (the MCP tools charge ``user_requests`` per call).
    """
    if limiter is None:
        return
    try:
        limiter.hit(bucket, key, limiter.config.limit(bucket))
    except RateLimited as e:
        raise RateLimited(e.retry_after, "too many requests; retry later") from e


def enforce(request: Request, bucket: str, key: str) -> None:
    """Count one event against ``bucket`` for ``key``; 429 when over."""
    charge(_limiter(request), bucket, key)


def guard_credential_attempt(request: Request) -> None:
    """429 if this IP has too many recent failed credentials. Records nothing."""
    limiter = _limiter(request)
    if limiter is None:
        return
    try:
        limiter.check(
            "bad_credential_per_ip",
            client_ip(request),
            limiter.config.limit("bad_credential_per_ip"),
        )
    except RateLimited as e:
        raise RateLimited(e.retry_after, "too many failed credential attempts; retry later") from e


def record_bad_credential(request: Request) -> None:
    """Count one failed credential for this IP, at most once per request."""
    limiter = _limiter(request)
    if limiter is None:
        return
    if getattr(request.state, "_bad_credential_recorded", False):
        return
    request.state._bad_credential_recorded = True
    limiter.record("bad_credential_per_ip", client_ip(request))


def rate_limit(bucket: str) -> Callable:
    """Dependency factory: count one event in ``bucket`` keyed by the current user id.

    Usage: ``dependencies=[Depends(rate_limit("user_requests"))]``. The bucket
    name is checked at import time so a typo fails loudly.
    """
    RateLimitConfig().limit(bucket)  # raises KeyError on an unknown bucket

    from fastapi import Depends

    from .auth import require_caller  # local import: auth imports this module

    def _dependency(request: Request, caller=Depends(require_caller)) -> None:
        enforce(request, bucket, str(caller.user.id))

    return _dependency


__all__ = [
    "Limit",
    "RateLimitConfig",
    "SlidingWindowLimiter",
    "charge",
    "client_ip",
    "enforce",
    "guard_credential_attempt",
    "rate_limit",
    "record_bad_credential",
]
