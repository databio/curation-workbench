-- 011_decisions.sql: hosted decisions (ids H-NNNN), one row per record; columns mirror store FIELDS.
-- The CHECKs only back up the shared validator (store/validate.py), which owns the real rules.
-- supersedes has no FK: it may name a repo DEC- id that exists only in the project's snapshot.
-- decided_by_user_id gets its FK from plan 2 (022); stale_since_snapshot_id from plan 4 (040).

CREATE TABLE decisions (
    id                      BIGSERIAL PRIMARY KEY,      -- also the record order (oldest first)
    project_id              INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    dec_id                  TEXT NOT NULL,              -- 'H-0042'
    subject                 TEXT NOT NULL,
    also_affects            JSONB NOT NULL DEFAULT '[]',
    verdict                 TEXT NOT NULL,
    operation               JSONB,
    rationale               TEXT NOT NULL DEFAULT '',
    evidence                JSONB NOT NULL DEFAULT '[]',
    decided_by              TEXT NOT NULL,              -- display name, as written to YAML
    decided_by_user_id      INTEGER,                    -- FK added by plan 2
    decided_on              DATE NOT NULL,
    subject_hashes          JSONB NOT NULL,
    schema_fingerprint      TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'pending',
    applied                 JSONB NOT NULL,             -- {on, subject_hashes_after, created, retired[, renamed]}
    supersedes              TEXT,                       -- an H- id here, or a DEC- id from the repo
    origin                  JSONB NOT NULL,             -- {kind: 'hosted', ref: dec_id}
    extra                   JSONB NOT NULL DEFAULT '{}', -- unknown top-level keys, kept for round trip
    rev                     INTEGER NOT NULL DEFAULT 1,
    stale_since_snapshot_id BIGINT,                     -- written by plan 4's worker; FK added in 040
    stale_reason            TEXT,
    stale_new_sid           TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_decisions_project_dec UNIQUE (project_id, dec_id),
    CONSTRAINT ck_decisions_dec_id CHECK (dec_id ~ '^H-[0-9]{4,}$'),
    CONSTRAINT ck_decisions_status CHECK (status IN ('pending', 'applied', 'withdrawn')),
    CONSTRAINT ck_decisions_supersedes CHECK (supersedes IS NULL OR supersedes <> dec_id),
    CONSTRAINT ck_decisions_stale_reason CHECK (stale_reason IS NULL OR stale_reason IN ('subject_changed', 'renamed', 'subject_removed'))
);
CREATE INDEX ix_decisions_project_subject ON decisions (project_id, subject);
CREATE INDEX ix_decisions_project_status ON decisions (project_id, status);
