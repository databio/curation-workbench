-- 020_project_members.sql: per-project curation teams (plan 2). A superadmin adds an
-- APPROVED account as `voter` or `curator`; removing a member sets removed_at (the row
-- and their votes stay; their votes are shown as a former member's and not counted).

CREATE TABLE project_members (
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    user_id     INTEGER NOT NULL REFERENCES snapgate_users(id),
    role        TEXT NOT NULL,
    added_by    INTEGER REFERENCES snapgate_users(id),   -- NULL: the operator CLI
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    removed_at  TIMESTAMPTZ,
    PRIMARY KEY (project_id, user_id),
    CONSTRAINT ck_project_members_role CHECK (role IN ('voter', 'curator'))
);
CREATE INDEX ix_project_members_user ON project_members (user_id);
