-- 013_decision_votes.sql: which votes a decision was based on, with each vote as the curator saw it.
-- Same-project and same-subject are enforced in PgStore.create, not here.

CREATE TABLE decision_votes (
    decision_id     BIGINT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    vote_id         BIGINT NOT NULL REFERENCES votes(id) ON DELETE RESTRICT,
    answer          TEXT NOT NULL,
    proposal        JSONB,
    subject_hash    TEXT NOT NULL,
    vote_rev        INTEGER NOT NULL,
    PRIMARY KEY (decision_id, vote_id)
);
CREATE INDEX ix_decision_votes_vote ON decision_votes (vote_id);
