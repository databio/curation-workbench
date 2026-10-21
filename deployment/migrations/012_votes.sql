-- 012_votes.sql: one vote per person per review question (subject + review_type).
-- user_id gets its FK from plan 2 (022); stale_since_snapshot_id from plan 4 (040).

CREATE TABLE votes (
    id                      BIGSERIAL PRIMARY KEY,
    project_id              INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    user_id                 INTEGER NOT NULL,           -- FK added by plan 2 (022)
    subject                 TEXT NOT NULL,              -- SID
    review_type             TEXT NOT NULL,              -- '<track>/<type>' from review_types.yaml, or 'subject'
    answer                  TEXT NOT NULL,              -- answer key (or the verdict for 'subject')
    proposal                JSONB,                      -- optional draft decision {verdict, operation?, rationale?, also_affects?, evidence?}
    comment                 TEXT NOT NULL DEFAULT '',
    subject_hash            TEXT NOT NULL,              -- the subject's hash when the voter saw it
    schema_fingerprint      TEXT NOT NULL,
    rev                     INTEGER NOT NULL DEFAULT 1,
    stale_since_snapshot_id BIGINT,                     -- written by plan 4's worker; FK added in 040
    stale_reason            TEXT,
    stale_new_sid           TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_votes_one_per_person UNIQUE (project_id, subject, review_type, user_id),
    CONSTRAINT ck_votes_review_type CHECK (review_type ~ '^([a-z0-9_-]+/[a-z0-9_-]+|subject)$'),
    CONSTRAINT ck_votes_answer CHECK (answer ~ '^[a-z0-9][a-z0-9_.-]{0,63}$'),
    CONSTRAINT ck_votes_stale_reason CHECK (stale_reason IS NULL OR stale_reason IN ('subject_changed', 'renamed', 'subject_removed'))
);
CREATE INDEX ix_votes_project_subject ON votes (project_id, subject);
CREATE INDEX ix_votes_project_type ON votes (project_id, review_type);
