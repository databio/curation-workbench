"""The storage interface.

`DecisionRecord` is what the apply engine needs: read the records and stamp
some applied. `YamlStore` (the repo's `decisions.yaml`) implements only that.
`DecisionStore` adds making, changing and withdrawing decisions, and
`VoteStore` adds votes; `PgStore` (the hosted site, Postgres) implements all
three. Decisions are made only on the hosted site.

Every write takes a `ValidationContext`: what the shared validator needs to
judge a record (the subject registry, the verdict vocabulary, the review types
and, where plugin code may run, the workbench for plugin checks).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..workbench import Workbench
    from .validate import Subjects


@dataclass
class ValidationContext:
    """Inputs to `validate` / `validate_vote` for one write.

    The hosted API builds it from the project's snapshot bundle with
    `wb=None` (plugin code never runs in the API) and `extra_known_ids` = the
    repo's own decision ids, so a hosted decision can supersede one of them.
    """
    subjects: Subjects
    verdicts: dict
    wb: Workbench | None = None
    review_types: dict | None = None
    extra_known_ids: frozenset[str] = field(default_factory=frozenset)


class DecisionRecord(Protocol):
    kind: str                                   # "yaml" | "postgres"

    def token(self) -> str:
        """Store-wide version token (YAML: mtime_ns; Postgres: decisions_rev)."""

    def records(self) -> list[dict]:
        """Every record as a plain JSON-safe dict, oldest first."""

    def get(self, dec_id: str) -> dict:
        """One record; StoreError(404) if absent."""

    def snapshot(self) -> dict:
        """The whole store: {version, next_id, decisions, token-ish}."""

    def mark_applied(self, stamps: dict[str, dict]) -> None:
        """Set `status: applied` and the `applied` block for each id, in one write."""


class DecisionStore(DecisionRecord, Protocol):
    """A store decisions are made in (Postgres only)."""

    def create(self, rec: dict, ctx: ValidationContext, *, expect_token: str | None = None,
               vote_ids: Any = ()) -> dict: ...

    def update(self, dec_id: str, rec: dict, ctx: ValidationContext, *,
               expect_token: str | None = None, expect_rev: int | None = None) -> dict: ...

    def withdraw(self, dec_id: str, *, expect_token: str | None = None,
                 expect_rev: int | None = None) -> dict: ...


class VoteStore(Protocol):
    """Votes on review questions. Postgres only."""

    def votes(self, subject: str | None = None, review_type: str | None = None,
              user_id: int | None = None) -> list[dict]: ...

    def cast_vote(self, vote: dict, ctx: ValidationContext, *, user_id: int,
                  expect_rev: int | None = None) -> dict: ...

    def retract_vote(self, subject: str, review_type: str, *, user_id: int) -> None: ...

    def decision_votes(self, dec_id: str) -> list[dict]: ...
