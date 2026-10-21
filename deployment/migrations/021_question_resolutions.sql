-- 021_question_resolutions.sql: a curator closing (or reopening) one review question,
-- (subject, review_type). Append-only: the newest row per (project_id, subject,
-- review_type) is the question's current state; no row means open.

CREATE TABLE question_resolutions (
    id           BIGSERIAL PRIMARY KEY,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    subject      TEXT NOT NULL,
    review_type  TEXT NOT NULL,
    state        TEXT NOT NULL,
    outcome      TEXT,                                     -- NULL on a reopen
    decision_id  BIGINT REFERENCES decisions(id),
    resolved_by  INTEGER REFERENCES snapgate_users(id),    -- the curator
    note         TEXT NOT NULL DEFAULT '',
    at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_question_resolutions_state CHECK (state IN ('resolved', 'reopened')),
    CONSTRAINT ck_question_resolutions_outcome CHECK (outcome IN ('decision', 'no_action')),
    CONSTRAINT ck_question_resolutions_decision CHECK (outcome IS DISTINCT FROM 'decision' OR decision_id IS NOT NULL)
);
CREATE INDEX ix_question_resolutions_question
    ON question_resolutions (project_id, subject, review_type, id);
