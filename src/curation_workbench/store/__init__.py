"""Decision storage: where decisions are made, where they land, one validator.

  store.validate   validation and vocabulary, backend-free (`validate`,
                   `validate_vote`, `check_operation`, `Subjects`, ...)
  store.base       `DecisionRecord` / `DecisionStore` / `VoteStore` protocols,
                   `ValidationContext`
  store.pg         `PgStore`: the hosted site's decisions and votes, Postgres
                   (needs the `postgres` extra; imported lazily). The only place
                   decisions are made.
  store.yaml_store `YamlStore`: the repo's decisions.yaml, the permanent record
                   of decisions that reached the repo (read; apply stamps it)
  store.landing    `append_hosted`: hosted decisions -> decisions.yaml (`cwb pull`)
"""
from .base import DecisionRecord, DecisionStore, ValidationContext, VoteStore
from .validate import (ANSWER_RE, EVIDENCE_KINDS, FIELDS, HOSTED_ID_RE, ID_RE, ORIGINS,
                       STALE_REASONS, STATUSES, StoreError, Subjects, check_operation,
                       check_updatable, check_withdrawable, load_verdicts, normalize,
                       to_plain, validate, validate_vote)
from .yaml_store import YamlStore


def open_store(wb, backend="yaml", *, engine=None, project=None):
    """The store for `wb`: its decisions.yaml (read and apply only), or a
    project in Postgres.

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
    "ANSWER_RE", "DecisionRecord", "DecisionStore", "EVIDENCE_KINDS", "FIELDS", "HOSTED_ID_RE", "ID_RE",
    "ORIGINS", "STALE_REASONS", "STATUSES", "StoreError", "Subjects", "ValidationContext",
    "VoteStore", "YamlStore", "check_operation", "check_updatable", "check_withdrawable",
    "load_verdicts", "normalize", "open_store", "to_plain", "validate", "validate_vote",
]
