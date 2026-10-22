-- 041_repo_snapshots.sql: the site reads each project's committed snapshot file
-- from GitHub. No sync worker, no runner, no plugin approvals.
--
-- Replaces 040's tables. A project's repo (owner/name on github.com and a
-- branch) lives in cw_repos; the API process fetches the snapshot file the repo
-- commits (the path is in its workbench.yaml) and stores one cw_snapshots row
-- per commit. Status and review are derived in the API on read, so there is no
-- derived-data table and no job queue. Stale marks on votes and decisions still
-- point at the snapshot that made them stale; every mark and clear is logged in
-- cw_stale_events.

ALTER TABLE votes DROP CONSTRAINT fk_votes_stale_snapshot;
ALTER TABLE decisions DROP CONSTRAINT fk_decisions_stale_snapshot;
UPDATE votes SET stale_since_snapshot_id = NULL, stale_reason = NULL, stale_new_sid = NULL
    WHERE stale_since_snapshot_id IS NOT NULL;
UPDATE decisions SET stale_since_snapshot_id = NULL, stale_reason = NULL, stale_new_sid = NULL
    WHERE stale_since_snapshot_id IS NOT NULL;

DROP TABLE cw_sync_jobs;
DROP TABLE cw_plugin_approvals;
DROP TABLE cw_derived;
DROP TABLE cw_stale_events;
ALTER TABLE cw_residents DROP CONSTRAINT fk_cw_residents_current_snapshot;
DROP TABLE cw_snapshots;
DROP TABLE cw_residents;

CREATE TABLE cw_repos (
    project_id              INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE RESTRICT,
    repo                    TEXT NOT NULL,              -- owner/name on github.com (public)
    branch                  TEXT NOT NULL,
    current_snapshot_id     BIGINT,                     -- FK added below (cw_snapshots)
    last_commit             TEXT,                       -- branch head at the last check
    last_checked_at         TIMESTAMPTZ,
    last_error              TEXT,                       -- NULL when the last check worked
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by              INTEGER,                    -- snapgate user id; NULL = CLI operator
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cw_snapshots (
    id                      BIGSERIAL PRIMARY KEY,
    project_id              INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    commit_sha              TEXT NOT NULL,
    schema_fingerprint      TEXT NOT NULL,
    bundle                  BYTEA NOT NULL,             -- gzip of the cwb-snapshot/1 JSON
    bundle_sha256           TEXT NOT NULL,              -- of the uncompressed JSON
    warnings                JSONB NOT NULL DEFAULT '[]',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_cw_snapshots_commit UNIQUE (project_id, commit_sha)
);

ALTER TABLE cw_repos ADD CONSTRAINT fk_cw_repos_current_snapshot
    FOREIGN KEY (current_snapshot_id) REFERENCES cw_snapshots(id);

-- Append-only: every stale mark and clear, so clearing never loses history.
CREATE TABLE cw_stale_events (
    id                      BIGSERIAL PRIMARY KEY,
    project_id              INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    snapshot_id             BIGINT REFERENCES cw_snapshots(id),
    object_kind             TEXT NOT NULL,
    object_id               BIGINT NOT NULL,            -- votes.id / decisions.id (no FK: votes can be retracted)
    action                  TEXT NOT NULL,
    sid                     TEXT,
    reason                  TEXT,
    new_sid                 TEXT,
    at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_cw_stale_events_kind CHECK (object_kind IN ('vote', 'decision')),
    CONSTRAINT ck_cw_stale_events_action CHECK (action IN ('marked', 'cleared'))
);
CREATE INDEX ix_cw_stale_events_object ON cw_stale_events (object_kind, object_id);

ALTER TABLE votes ADD CONSTRAINT fk_votes_stale_snapshot
    FOREIGN KEY (stale_since_snapshot_id) REFERENCES cw_snapshots(id);
ALTER TABLE decisions ADD CONSTRAINT fk_decisions_stale_snapshot
    FOREIGN KEY (stale_since_snapshot_id) REFERENCES cw_snapshots(id);
