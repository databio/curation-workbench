"""Decision storage: one interface, two backends, one validator.

  store.validate   validation and vocabulary, backend-free (`validate`,
                   `validate_vote`, `check_operation`, `Subjects`, ...)
  store.base       `DecisionStore` / `VoteStore` protocols, `ValidationContext`
  store.yaml_store `YamlStore`: local mode, the repo's decisions.yaml
  store.pg         `PgStore`: hosted mode, Postgres (needs the `postgres` extra;
                   imported lazily, so local mode never loads SQLAlchemy)
"""
from .base import DecisionStore, ValidationContext, VoteStore
from .validate import (ANSWER_RE, EVIDENCE_KINDS, FIELDS, HOSTED_ID_RE, ID_RE, ORIGINS,
                       STALE_REASONS, STATUSES, StoreError, Subjects, check_operation,
                       check_updatable, check_withdrawable, load_verdicts, normalize,
                       to_plain, validate, validate_vote)
from .yaml_store import LOCK, YamlStore


def open_store(wb, backend="yaml", *, engine=None, project=None):
    """The store for `wb`: its decisions.yaml, or a project in Postgres.

    For "postgres", `project` is a project id (int) or slug (str)."""
    if backend == "yaml":
        return YamlStore(wb.cfg.decisions)
    if backend == "postgres":
        from .pg import PgStore, projects
        if engine is None or project is None:
            raise ValueError("the postgres store needs engine= and project=")
        if isinstance(project, str):
            row = projects.get_project(engine, project)
            if row is None:
                raise StoreError(404, [f"no project {project}"])
            project = row["id"]
        return PgStore(engine, project)
    raise ValueError(f"unknown store backend {backend!r}")


__all__ = [
    "ANSWER_RE", "DecisionStore", "EVIDENCE_KINDS", "FIELDS", "HOSTED_ID_RE", "ID_RE", "LOCK",
    "ORIGINS", "STALE_REASONS", "STATUSES", "StoreError", "Subjects", "ValidationContext",
    "VoteStore", "YamlStore", "check_operation", "check_updatable", "check_withdrawable",
    "load_verdicts", "normalize", "open_store", "to_plain", "validate", "validate_vote",
]
