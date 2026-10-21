# SNAPGATE module - do not edit. Overwritten on updates. Customize via SnapgateHooks.
"""App-defined API key scopes, the ``require_scope`` guard, and key confinement.

Snapgate stores scopes as opaque strings on each API key. The host app owns the
vocabulary: it declares the names, what each one means in plain language, which
names imply others (``write`` -> ``read``), the permission groups a person ticks on
the OAuth consent page and the Connected-apps list, and the scopes a key made on
the Settings page gets by default.

A JWT (browser session) caller holds every scope: scopes restrict keys only.

Confinement: a key minted by an OAuth connection carries ``confined_to`` (the
app's MCP path, e.g. ``/mcp``). It works only on requests under that path, with
no exception: an app's MCP tools call service functions, never the app's own
HTTP API (see :mod:`snapgate.service`). Anywhere else it answers 403
``key_confined``. That is a valid credential in the wrong place, not a bad
credential: apps must not count it toward failed-credential limits.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from fastapi import Depends, Request

from .api_keys import MatchedKey
from .core import AuthError


class UnknownScopeError(ValueError):
    """A scope name the app did not declare."""


_GROUP_ID = re.compile(r"[a-z0-9_]+")


@dataclass(frozen=True)
class ScopeGroup:
    """One checkbox on the consent page: a permission a person grants, and its scopes."""

    id: str  # form field suffix, [a-z0-9_]+
    label: str  # "Edit"
    scopes: tuple[str, ...]
    description: str | None = None  # None: the page lists the scope descriptions


def _group_id(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower())


@dataclass(frozen=True)
class ScopeConfig:
    """An app's scope vocabulary.

    Args:
        scopes: name -> plain-language description.
        default_scopes: what a key made on the Settings page gets.
        groups: the permissions a person grants on the consent page and the
            Connected-apps list, as checkboxes, in display order. Every declared
            scope belongs to exactly one group. Empty: one group per scope.
        implies: name -> names it includes (e.g. write -> [read]). Transitive.
    """

    scopes: Mapping[str, str]
    default_scopes: Sequence[str]
    groups: Sequence[ScopeGroup] = ()
    implies: Mapping[str, Sequence[str]] = field(default_factory=dict)

    def __post_init__(self):
        declared = set(self.scopes)
        used: list[str] = list(self.default_scopes)
        for g in self.groups:
            used.extend(g.scopes)
        for name, implied in self.implies.items():
            used.append(name)
            used.extend(implied)
        unknown = sorted(set(used) - declared)
        if unknown:
            raise UnknownScopeError(f"undeclared scopes: {', '.join(unknown)}")
        groups = tuple(self.groups) or tuple(
            ScopeGroup(id=_group_id(n), label=n, scopes=(n,), description=d)
            for n, d in self.scopes.items()
        )
        ids = [g.id for g in groups]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate group ids: {ids}")
        bad = [i for i in ids if not _GROUP_ID.fullmatch(i)]
        if bad:
            raise ValueError(f"group ids must match [a-z0-9_]+: {bad}")
        owner: dict[str, str] = {}
        for g in groups:
            if not g.scopes:
                raise ValueError(f"group {g.id!r} has no scopes")
            for name in g.scopes:
                if name in owner and owner[name] != g.id:
                    raise ValueError(f"scope {name!r} is in groups {owner[name]!r} and {g.id!r}")
                owner[name] = g.id
        ungrouped = sorted(declared - set(owner))
        if ungrouped:
            raise ValueError(f"scopes in no group: {', '.join(ungrouped)}")
        object.__setattr__(self, "groups", groups)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self.scopes)

    def expand(self, names: Iterable[str]) -> frozenset[str]:
        """``names`` plus everything they imply, transitively."""
        out: set[str] = set()
        todo = list(names)
        while todo:
            name = todo.pop()
            if name in out:
                continue
            out.add(name)
            todo.extend(self.implies.get(name, ()))
        return frozenset(out)

    def validate(self, names: Iterable[str]) -> frozenset[str]:
        """The expanded set; raises :class:`UnknownScopeError` for an undeclared name."""
        names = list(names)
        unknown = sorted(set(names) - self.names)
        if unknown:
            raise UnknownScopeError(f"undeclared scopes: {', '.join(unknown)}")
        return self.expand(names)

    def parse_request(self, raw: str) -> frozenset[str] | None:
        """An OAuth ``scope`` parameter: empty asks for everything, unknown is None."""
        names = (raw or "").split()
        if not names:
            return self.names
        try:
            return self.validate(names)
        except UnknownScopeError:
            return None

    def offered_groups(self, requested: frozenset[str]) -> list[ScopeGroup]:
        """Groups that share a scope with the (expanded) request, in declared order."""
        requested = self.expand(requested)
        return [g for g in self.groups if set(g.scopes) & requested]

    def grant(self, requested: frozenset[str], chosen: Iterable[str]) -> frozenset[str] | None:
        """What ticking ``chosen`` group ids grants against ``requested``.

        ``expand`` of the union of ``group.scopes & requested`` over the ticked
        groups. None when a chosen id is not offered, nothing is chosen, or the
        result exceeds the (expanded) request.
        """
        requested = self.expand(requested)
        offered = {g.id: g for g in self.offered_groups(requested)}
        chosen = set(chosen)
        if not chosen or not chosen <= set(offered):
            return None
        picked: set[str] = set()
        for gid in chosen:
            picked |= set(offered[gid].scopes) & requested
        granted = self.expand(picked)
        if not granted or not granted <= requested:
            return None
        return granted

    def groups_held(self, names: Iterable[str]) -> list[str]:
        """Ids of the groups whose scopes are all in ``expand(names)``, in declared order."""
        held = self.expand(names)
        return [g.id for g in self.groups if set(g.scopes) <= held]

    def group(self, group_id: str) -> ScopeGroup | None:
        """The group with this id, or None."""
        for g in self.groups:
            if g.id == group_id:
                return g
        return None

    def includes(self, group: ScopeGroup) -> list[ScopeGroup]:
        """Other groups whose scopes all follow from ``group``'s through ``implies``."""
        got = self.expand(group.scopes)
        return [g for g in self.groups if g.id != group.id and set(g.scopes) <= got]

    def scope_string(self, names: Iterable[str]) -> str:
        """Sorted, space-separated (the OAuth ``scope`` format)."""
        return " ".join(sorted(set(names)))

    def describe(self, names: Iterable[str]) -> list[dict[str, str]]:
        """``[{"name", "description"}]`` for ``names``, sorted by name."""
        return [{"name": n, "description": self.scopes.get(n, "")} for n in sorted(set(names))]


class InsufficientScopeError(AuthError):
    """403: the key is good but lacks a scope this route needs."""

    def __init__(self, needed: Iterable[str], request: Request | None = None):
        self.needed = frozenset(needed)
        super().__init__(
            user_message="This key is not allowed to do that.",
            status_code=403,
            error_code="insufficient_scope",
            headers={"WWW-Authenticate": _insufficient_scope_challenge(self.needed, request)},
        )


def _insufficient_scope_challenge(needed: frozenset[str], request: Request | None) -> str:
    from . import oauth  # lazy: oauth imports this module

    if oauth._installed is not None:
        return oauth.www_authenticate(request, error="insufficient_scope", scope=needed)
    return f'Bearer error="insufficient_scope", scope="{" ".join(sorted(needed))}"'


class KeyConfinedError(AuthError):
    """403: a connection key used outside the app's AI connector.

    Not a bad credential (the key is valid, just in the wrong place): apps must
    not count it toward failed-credential limits.
    """

    def __init__(self):
        super().__init__(
            user_message="this key works only through the AI connector",
            status_code=403,
            error_code="key_confined",
        )


def create_scope_guard(
    scopes: ScopeConfig, get_request_api_key: Callable
) -> Callable[..., Callable]:
    """Build ``require_scope(*names)`` for this app's vocabulary.

    Pass the 4th value of ``create_flexible_auth_dependencies`` as
    ``get_request_api_key``: the same callable the app's user dependencies use,
    so FastAPI's per-request cache verifies the key once.

    ``require_scope("x:write")`` returns a dependency (``Depends(...)`` it or put
    it in ``dependencies=[...]``). It is a plain function too: call it inline
    with a ``MatchedKey`` you already have. A JWT caller (key ``None``) passes.
    A typo in a scope name fails at import time.
    """

    def require_scope(*needed: str) -> Callable:
        scopes.validate(needed)
        need = frozenset(needed)

        def guard(
            key: MatchedKey | None = Depends(get_request_api_key),
            request: Request = None,
        ) -> None:
            if key is None:
                return  # JWT browser session (or anonymous; the user dep rejects that)
            if not need <= scopes.expand(key.scopes):
                raise InsufficientScopeError(need, request)

        return guard

    return require_scope


# -- confinement ------------------------------------------------------------------


def request_within(request: Request, path: str) -> bool:
    """True for a request under ``path``. The ``Host`` header is never trusted."""
    p = request.url.path
    return p == path or p.startswith(path.rstrip("/") + "/")


def enforce_confinement(request: Request, key: MatchedKey) -> None:
    """Raise :class:`KeyConfinedError` for a confined key outside its path."""
    if key.confined_to and not request_within(request, key.confined_to):
        raise KeyConfinedError()
