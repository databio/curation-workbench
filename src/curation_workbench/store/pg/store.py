"""`PgStore`: one hosted project's decisions and votes in Postgres.

Implements `DecisionStore` and `VoteStore`. Every row carries `project_id`.
Postgres holds hosted decisions only (ids `H-NNNN`, minted from the project's
`next_decision_number`); the repo's own `DEC-` decisions reach the hosted
service through the project snapshot, never by import.

Concurrency: every write locks the project row first (`FOR UPDATE`), which
serializes id minting and the `decisions_rev` counter per project. Each record
and vote also has a `rev` integer for compare-and-swap (`expect_rev`), and
`decisions_rev` is the store-wide token (`expect_token`).

Staleness is stored, not computed: plan 4's worker writes the `stale_*`
columns through `set_stale` / `clear_stale`.
"""
from __future__ import annotations

import contextlib
import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from ..validate import (FIELDS, STALE_REASONS, StoreError, check_updatable, check_withdrawable,
                        normalize, to_plain, validate, validate_vote)
from .tables import decision_votes, decisions, projects, votes

# Keys a record may carry that are not stored as fields or `extra`.
INTERNAL_KEYS = ("rev", "vote_ids", "stale", "stale_reason", "stale_new_sid", "decided_by_user_id")
STALE_KINDS = {"vote": votes, "decision": decisions}

_CONSTRAINT_ERRORS = {
    "uq_votes_one_per_person": "this person already voted on this question",
    "uq_decisions_project_dec": "that decision id already exists in this project; retry",
    "decision_votes_vote_id_fkey": "this vote was used by a decision",
}


def _translate(exc: IntegrityError) -> StoreError:
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    name = getattr(diag, "constraint_name", None)
    msg = _CONSTRAINT_ERRORS.get(name) or f"conflict with existing data ({name or exc.orig})"
    return StoreError(409, [msg])


def _iso(v):
    return v.isoformat() if v is not None else None


def _row_to_record(row, vote_ids=()) -> dict:
    """A decision row as the plain dict `YamlStore.records()` produces, plus
    `rev`, `vote_ids` and the stale marks."""
    rec = {
        "id": row.dec_id,
        "subject": row.subject,
        "also_affects": row.also_affects,
        "verdict": row.verdict,
        "operation": row.operation,
        "rationale": row.rationale,
        "evidence": row.evidence,
        "decided_by": row.decided_by,
        "decided_on": row.decided_on.isoformat(),
        "subject_hashes": row.subject_hashes,
        "schema_fingerprint": row.schema_fingerprint,
        "status": row.status,
        "applied": row.applied,
        "supersedes": row.supersedes,
        "origin": row.origin,
    }
    for k, v in (row.extra or {}).items():
        rec.setdefault(k, v)
    rec["rev"] = row.rev
    rec["vote_ids"] = list(vote_ids)
    rec["stale"] = row.stale_since_snapshot_id is not None
    rec["stale_reason"] = row.stale_reason
    rec["stale_new_sid"] = row.stale_new_sid
    return rec


def _record_to_row(rec) -> dict:
    """Columns for a validated record (`decided_on` a date)."""
    plain = to_plain({k: v for k, v in rec.items() if k != "decided_on"})
    return {
        "dec_id": rec["id"],
        "subject": rec["subject"],
        "also_affects": plain["also_affects"],
        "verdict": rec["verdict"],
        "operation": plain["operation"],
        "rationale": rec.get("rationale") or "",
        "evidence": plain["evidence"],
        "decided_by": rec["decided_by"],
        "decided_on": rec["decided_on"],
        "subject_hashes": plain["subject_hashes"],
        "schema_fingerprint": rec["schema_fingerprint"],
        "status": rec["status"],
        "applied": plain["applied"],
        "supersedes": rec["supersedes"],
        "origin": plain["origin"],
        "extra": {k: v for k, v in plain.items() if k not in FIELDS and k not in INTERNAL_KEYS},
    }


def _vote_out(row) -> dict:
    return {
        "id": row.id, "subject": row.subject, "review_type": row.review_type,
        "answer": row.answer, "proposal": row.proposal, "comment": row.comment,
        "user_id": row.user_id, "subject_hash": row.subject_hash,
        "schema_fingerprint": row.schema_fingerprint, "rev": row.rev,
        "stale": row.stale_since_snapshot_id is not None,
        "stale_reason": row.stale_reason, "stale_new_sid": row.stale_new_sid,
        "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at),
    }


class PgStore:
    kind = "postgres"

    def __init__(self, engine, project_id: int):
        self.engine = engine
        self.project_id = int(project_id)
        self._tx = None

    # ------------------------------------------------------------ connections

    @contextlib.contextmanager
    def transaction(self):
        """Yield a Connection; every method on this instance runs on it until the
        block ends, and the block commits or rolls back as a whole."""
        if self._tx is not None:
            yield self._tx
            return
        with self.engine.begin() as conn:
            self._tx = conn
            try:
                yield conn
            finally:
                self._tx = None

    @contextlib.contextmanager
    def using(self, conn):
        """Run every method on this instance on `conn`, a transaction the caller
        already holds (e.g. plan 4's publish), until the block ends."""
        prev, self._tx = self._tx, conn
        try:
            yield conn
        finally:
            self._tx = prev

    @contextlib.contextmanager
    def _conn(self):
        """The open transaction() connection, or a fresh transaction of our own.
        No raw IntegrityError escapes."""
        try:
            if self._tx is not None:
                yield self._tx
            else:
                with self.engine.begin() as conn:
                    yield conn
        except IntegrityError as e:
            raise _translate(e) from None

    def _lock_project(self, conn):
        row = conn.execute(
            select(projects.c.next_decision_number, projects.c.decisions_rev)
            .where(projects.c.id == self.project_id).with_for_update()).one_or_none()
        if row is None:
            raise StoreError(404, [f"no project {self.project_id}"])
        return row

    def _bump(self, conn, next_number=False):
        values = {"decisions_rev": projects.c.decisions_rev + 1, "updated_at": func.now()}
        if next_number:
            values["next_decision_number"] = projects.c.next_decision_number + 1
        conn.execute(update(projects).where(projects.c.id == self.project_id).values(**values))

    @staticmethod
    def _check_token(proj, expect_token):
        if expect_token is not None and str(expect_token) != str(proj.decisions_rev):
            raise StoreError(409, ["the project's decisions changed since they were read; reload"])

    def _known_ids(self, conn, ctx):
        ids = conn.execute(select(decisions.c.dec_id)
                           .where(decisions.c.project_id == self.project_id)).scalars()
        return set(ids) | set(ctx.extra_known_ids)

    def _vote_ids_of(self, conn, decision_ids):
        out = {i: [] for i in decision_ids}
        if decision_ids:
            q = (select(decision_votes.c.decision_id, decision_votes.c.vote_id)
                 .where(decision_votes.c.decision_id.in_(decision_ids))
                 .order_by(decision_votes.c.vote_id))
            for did, vid in conn.execute(q):
                out[did].append(vid)
        return out

    def _decision_row(self, conn, dec_id, lock=False):
        q = select(decisions).where(decisions.c.project_id == self.project_id,
                                    decisions.c.dec_id == dec_id)
        if lock:
            q = q.with_for_update()
        row = conn.execute(q).one_or_none()
        if row is None:
            raise StoreError(404, [f"no decision {dec_id}"])
        return row

    def _record(self, conn, row):
        return _row_to_record(row, self._vote_ids_of(conn, [row.id])[row.id])

    # ------------------------------------------------------------ reads

    def decisions_rev(self) -> int:
        with self._conn() as conn:
            rev = conn.execute(select(projects.c.decisions_rev)
                               .where(projects.c.id == self.project_id)).scalar_one_or_none()
        if rev is None:
            raise StoreError(404, [f"no project {self.project_id}"])
        return int(rev)

    def token(self) -> str:
        return str(self.decisions_rev())

    def _records(self, conn):
        rows = conn.execute(select(decisions).where(decisions.c.project_id == self.project_id)
                            .order_by(decisions.c.id)).all()
        vids = self._vote_ids_of(conn, [r.id for r in rows])
        return [_row_to_record(r, vids[r.id]) for r in rows]

    def records(self) -> list[dict]:
        with self._conn() as conn:
            return self._records(conn)

    def get(self, dec_id: str) -> dict:
        with self._conn() as conn:
            return self._record(conn, self._decision_row(conn, dec_id))

    def snapshot(self) -> dict:
        with self._conn() as conn:
            proj = conn.execute(select(projects.c.next_decision_number, projects.c.decisions_rev)
                                .where(projects.c.id == self.project_id)).one_or_none()
            if proj is None:
                raise StoreError(404, [f"no project {self.project_id}"])
            recs = self._records(conn)
        return {"version": 1, "next_id": proj.next_decision_number, "decisions": recs,
                "token": str(proj.decisions_rev)}

    # ------------------------------------------------------------ decision writes

    def create(self, rec, ctx, *, expect_token=None, vote_ids=(), decided_by_user_id=None) -> dict:
        """Validate and insert `rec` as the project's next H- decision.
        `decided_by_user_id` is the signed-in account (plan 2); it is stored in
        its own column, never in the record."""
        vote_ids = list(vote_ids or ())
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in vote_ids):
            raise StoreError(400, ["vote_ids must be vote ids (integers)"])
        with self._conn() as conn:
            proj = self._lock_project(conn)
            self._check_token(proj, expect_token)
            rec = normalize({k: v for k, v in rec.items() if k not in INTERNAL_KEYS})
            dec_id = f"H-{proj.next_decision_number:04d}"
            rec["id"] = dec_id
            rec["origin"] = {"kind": "hosted", "ref": dec_id}
            rec.setdefault("decided_on", datetime.date.today())
            rec.setdefault("schema_fingerprint", ctx.subjects.fingerprint)
            validate(rec, ctx.subjects, ctx.verdicts, known_ids=self._known_ids(conn, ctx), wb=ctx.wb)

            linked = []
            if vote_ids:
                rows = {r.id: r for r in conn.execute(
                    select(votes).where(votes.c.id.in_(vote_ids),
                                        votes.c.project_id == self.project_id)
                    .with_for_update(read=True))}
                covered = {rec["subject"], *rec["also_affects"]}
                errors = []
                for vid in dict.fromkeys(vote_ids):
                    row = rows.get(vid)
                    if row is None:
                        errors.append(f"no vote {vid} in this project")
                    elif row.subject not in covered:
                        errors.append(f"vote {vid} is on {row.subject}, not on this decision's subjects")
                    else:
                        linked.append(row)
                if errors:
                    raise StoreError(400, errors)

            new_id = conn.execute(decisions.insert().values(project_id=self.project_id,
                                                            decided_by_user_id=decided_by_user_id,
                                                            **_record_to_row(rec))
                                  .returning(decisions.c.id)).scalar_one()
            if linked:
                conn.execute(decision_votes.insert(), [
                    {"decision_id": new_id, "vote_id": v.id, "answer": v.answer,
                     "proposal": v.proposal, "subject_hash": v.subject_hash, "vote_rev": v.rev}
                    for v in linked])
            self._bump(conn, next_number=True)
            return self._record(conn, self._decision_row(conn, dec_id))

    def _locked_current(self, conn, dec_id, expect_token, expect_rev):
        proj = self._lock_project(conn)
        self._check_token(proj, expect_token)
        row = self._decision_row(conn, dec_id, lock=True)
        return row, self._record(conn, row)

    @staticmethod
    def _check_rev(row, cur, expect_rev):
        if expect_rev is not None and int(expect_rev) != row.rev:
            raise StoreError(409, [f"{row.dec_id} changed since it was read "
                                   f"(rev {expect_rev}, current {row.rev}); reload"], current=cur)

    def update(self, dec_id, rec, ctx, *, expect_token=None, expect_rev=None,
               decided_by_user_id=None) -> dict:
        """Validate and replace pending decision `dec_id`. Clears its stale marks
        (the new version was validated against the current hashes)."""
        with self._conn() as conn:
            row, cur = self._locked_current(conn, dec_id, expect_token, expect_rev)
            check_updatable(cur)
            self._check_rev(row, cur, expect_rev)
            rec = normalize({k: v for k, v in rec.items() if k not in INTERNAL_KEYS})
            if rec.setdefault("id", dec_id) != dec_id:
                raise StoreError(400, [f"record id {rec['id']} does not match {dec_id}"])
            rec["origin"] = {"kind": "hosted", "ref": dec_id}
            validate(rec, ctx.subjects, ctx.verdicts, known_ids=self._known_ids(conn, ctx), wb=ctx.wb)
            values = _record_to_row(rec)
            values.pop("dec_id")
            if decided_by_user_id is not None:
                values["decided_by_user_id"] = decided_by_user_id
            conn.execute(update(decisions).where(decisions.c.id == row.id).values(
                rev=decisions.c.rev + 1, updated_at=func.now(), stale_since_snapshot_id=None,
                stale_reason=None, stale_new_sid=None, **values))
            self._bump(conn)
            return self._record(conn, self._decision_row(conn, dec_id))

    def withdraw(self, dec_id, *, expect_token=None, expect_rev=None) -> dict:
        """Set `status: withdrawn`. Records are never removed."""
        with self._conn() as conn:
            row, cur = self._locked_current(conn, dec_id, expect_token, expect_rev)
            check_withdrawable(cur)
            self._check_rev(row, cur, expect_rev)
            conn.execute(update(decisions).where(decisions.c.id == row.id).values(
                status="withdrawn", rev=decisions.c.rev + 1, updated_at=func.now()))
            self._bump(conn)
            return self._record(conn, self._decision_row(conn, dec_id))

    def mark_applied(self, stamps) -> None:
        """Set `status: applied` and the `applied` block of each id, in one transaction."""
        with self._conn() as conn:
            self._lock_project(conn)
            rows = {r.dec_id: r for r in conn.execute(
                select(decisions).where(decisions.c.project_id == self.project_id,
                                        decisions.c.dec_id.in_(list(stamps)))
                .with_for_update())}
            missing = [d for d in stamps if d not in rows]
            if missing:
                raise StoreError(404, [f"no decision {d}" for d in missing])
            for dec_id, block in stamps.items():
                conn.execute(update(decisions).where(decisions.c.id == rows[dec_id].id).values(
                    status="applied", applied=to_plain(block), rev=decisions.c.rev + 1,
                    updated_at=func.now()))
            self._bump(conn)

    # ------------------------------------------------------------ votes

    def votes(self, subject=None, review_type=None, user_id=None) -> list[dict]:
        q = select(votes).where(votes.c.project_id == self.project_id).order_by(votes.c.id)
        if subject is not None:
            q = q.where(votes.c.subject == subject)
        if review_type is not None:
            q = q.where(votes.c.review_type == review_type)
        if user_id is not None:
            q = q.where(votes.c.user_id == user_id)
        with self._conn() as conn:
            return [_vote_out(r) for r in conn.execute(q)]

    def _vote_row(self, conn, subject, review_type, user_id):
        return conn.execute(select(votes).where(
            votes.c.project_id == self.project_id, votes.c.subject == subject,
            votes.c.review_type == review_type, votes.c.user_id == user_id)).one_or_none()

    def cast_vote(self, vote, ctx, *, user_id, expect_rev=None) -> dict:
        """Record (or change) `user_id`'s vote on one question. A changed vote
        keeps its row, bumps `rev` and clears its stale marks."""
        v = validate_vote(vote, ctx)
        with self._conn() as conn:
            self._lock_project(conn)
            values = {"answer": v["answer"], "proposal": v["proposal"], "comment": v["comment"],
                      "subject_hash": v["subject_hash"], "schema_fingerprint": v["schema_fingerprint"]}
            stmt = pg_insert(votes).values(project_id=self.project_id, user_id=user_id,
                                           subject=v["subject"], review_type=v["review_type"],
                                           **values)
            where = votes.c.rev == int(expect_rev) if expect_rev is not None else None
            stmt = stmt.on_conflict_do_update(
                constraint="uq_votes_one_per_person",
                set_={**values, "rev": votes.c.rev + 1, "updated_at": func.now(),
                      "stale_since_snapshot_id": None, "stale_reason": None, "stale_new_sid": None},
                where=where).returning(*votes.c)
            row = conn.execute(stmt).one_or_none()
            if row is None:
                cur = self._vote_row(conn, v["subject"], v["review_type"], user_id)
                raise StoreError(409, [f"this vote changed since it was read (rev {expect_rev}, "
                                       f"current {cur.rev if cur else None}); reload"],
                                 current=_vote_out(cur) if cur else None)
            self._bump(conn)
            return _vote_out(row)

    def retract_vote(self, subject, review_type, *, user_id) -> None:
        """Delete the caller's own vote on one question (refused if a decision used it)."""
        with self._conn() as conn:
            self._lock_project(conn)
            row = self._vote_row(conn, subject, review_type, user_id)
            if row is None:
                raise StoreError(404, [f"no vote on {subject} ({review_type})"])
            used = conn.execute(
                select(decisions.c.dec_id).join(decision_votes,
                                                decision_votes.c.decision_id == decisions.c.id)
                .where(decision_votes.c.vote_id == row.id).order_by(decisions.c.id)).scalars().all()
            if used:
                raise StoreError(409, [f"this vote was used by {', '.join(used)}"])
            conn.execute(votes.delete().where(votes.c.id == row.id))
            self._bump(conn)

    def decision_votes(self, dec_id) -> list[dict]:
        """The votes a decision was based on, as the curator saw them."""
        with self._conn() as conn:
            row = self._decision_row(conn, dec_id)
            q = (select(decision_votes).where(decision_votes.c.decision_id == row.id)
                 .order_by(decision_votes.c.vote_id))
            return [{"vote_id": r.vote_id, "answer": r.answer, "proposal": r.proposal,
                     "subject_hash": r.subject_hash, "vote_rev": r.vote_rev}
                    for r in conn.execute(q)]

    # ------------------------------------------------------------ staleness (plan 4's worker)

    @staticmethod
    def _marks(r) -> dict:
        return {"stale": r.stale_since_snapshot_id is not None,
                "stale_since_snapshot_id": r.stale_since_snapshot_id,
                "stale_reason": r.stale_reason, "stale_new_sid": r.stale_new_sid}

    def open_votes(self) -> list[dict]:
        """Every vote in the project: [{id, subject, subject_hash, stale,
        stale_since_snapshot_id, stale_reason, stale_new_sid}]."""
        with self._conn() as conn:
            q = (select(votes.c.id, votes.c.subject, votes.c.subject_hash,
                        votes.c.stale_since_snapshot_id, votes.c.stale_reason, votes.c.stale_new_sid)
                 .where(votes.c.project_id == self.project_id).order_by(votes.c.id))
            return [{"id": r.id, "subject": r.subject, "subject_hash": r.subject_hash,
                     **self._marks(r)} for r in conn.execute(q)]

    def pending_decisions(self) -> list[dict]:
        """Pending hosted decisions: [{id, dec_id, subject_hashes, stale,
        stale_since_snapshot_id, stale_reason, stale_new_sid}]."""
        with self._conn() as conn:
            q = (select(decisions.c.id, decisions.c.dec_id, decisions.c.subject_hashes,
                        decisions.c.stale_since_snapshot_id, decisions.c.stale_reason,
                        decisions.c.stale_new_sid)
                 .where(decisions.c.project_id == self.project_id, decisions.c.status == "pending")
                 .order_by(decisions.c.id))
            return [{"id": r.id, "dec_id": r.dec_id, "subject_hashes": r.subject_hashes,
                     **self._marks(r)} for r in conn.execute(q)]

    def _stale(self, kind, row_id, values):
        table = STALE_KINDS.get(kind)
        if table is None:
            raise StoreError(400, [f"kind must be one of {sorted(STALE_KINDS)}"])
        with self._conn() as conn:
            done = conn.execute(update(table).where(table.c.id == row_id,
                                                    table.c.project_id == self.project_id)
                                .values(**values)).rowcount
        if not done:
            raise StoreError(404, [f"no {kind} {row_id}"])

    def set_stale(self, kind, id, snapshot_id, reason, new_sid=None) -> None:
        """Mark a vote or decision row stale as of `snapshot_id`. Never deletes,
        never bumps `decisions_rev`."""
        if reason not in STALE_REASONS:
            raise StoreError(400, [f"stale reason must be one of {list(STALE_REASONS)}"])
        self._stale(kind, id, {"stale_since_snapshot_id": snapshot_id, "stale_reason": reason,
                               "stale_new_sid": new_sid})

    def clear_stale(self, kind, id) -> None:
        self._stale(kind, id, {"stale_since_snapshot_id": None, "stale_reason": None,
                               "stale_new_sid": None})


__all__ = ["PgStore"]
