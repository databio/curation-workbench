-- 010_projects.sql: hosted curation projects (one row per project; every other row carries project_id).
-- Where a project's repo lives (URL, branch, config path, plugin dir) is plan 4's cw_residents row.

CREATE TABLE projects (
    id                      SERIAL PRIMARY KEY,
    slug                    TEXT NOT NULL UNIQUE,       -- url-safe, e.g. 'onga'
    name                    TEXT NOT NULL,              -- display name
    next_decision_number    INTEGER NOT NULL DEFAULT 1, -- next H-NNNN
    decisions_rev           BIGINT NOT NULL DEFAULT 0,  -- bumped on every decision/vote write
    archived_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_projects_slug CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,40}$'),
    CONSTRAINT ck_projects_next CHECK (next_decision_number >= 1)
);
