-- 040_residents_sync.sql: resident projects and the sync worker (plan 4).
-- A resident says where a hosted project's git repo is; the worker mirrors it, builds one
-- snapshot bundle per commit in the isolated runner, derives status/review from the bundle
-- plus the current decisions, and marks votes/decisions stale when their subject changed.
-- Snapshots are never deleted: stale marks on votes and decisions refer to them.

CREATE TABLE cw_residents (
    id                      SERIAL PRIMARY KEY,
    project_id              INTEGER NOT NULL UNIQUE REFERENCES projects(id) ON DELETE RESTRICT,
    repo_url                TEXT NOT NULL,              -- https://<allowed host>/..., or dev:<abs path>
    branch                  TEXT NOT NULL,
    config_path             TEXT NOT NULL DEFAULT 'workbench.yaml',  -- in the repo
    plugin_dir              TEXT,                       -- in the repo; plugins run only from an approved tree
    enabled                 BOOLEAN NOT NULL DEFAULT true,
    poll_seconds            INTEGER NOT NULL DEFAULT 300,
    webhook_secret          TEXT,                       -- hex; shown once at creation / rotation
    last_seen_commit        TEXT,
    current_snapshot_id     BIGINT,                     -- FK added below (cw_snapshots)
    last_error              TEXT,
    last_checked_at         TIMESTAMPTZ,
    plugins_revoked_at      TIMESTAMPTZ,                -- approvals older than this are inactive
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by              INTEGER,                    -- snapgate user id; NULL = CLI operator
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_cw_residents_poll CHECK (poll_seconds >= 30)
);

CREATE TABLE cw_plugin_approvals (
    id                      SERIAL PRIMARY KEY,
    resident_id             INTEGER NOT NULL REFERENCES cw_residents(id) ON DELETE RESTRICT,
    tree_sha                TEXT NOT NULL,              -- git tree id of plugin_dir
    files                   BYTEA NOT NULL,             -- gzip tar of that tree
    approved_by             INTEGER,                    -- snapgate user id; NULL = CLI operator
    approved_at             TIMESTAMPTZ NOT NULL DEFAULT now(),  -- newest active row wins
    CONSTRAINT uq_cw_plugin_approvals UNIQUE (resident_id, tree_sha)
);

CREATE TABLE cw_snapshots (
    id                      BIGSERIAL PRIMARY KEY,
    resident_id             INTEGER NOT NULL REFERENCES cw_residents(id) ON DELETE RESTRICT,
    commit_sha              TEXT NOT NULL,
    commit_time             TIMESTAMPTZ,
    schema_fingerprint      TEXT NOT NULL,
    plugin_tree_used        TEXT,                       -- NULL: generic only
    plugin_tree_at_commit   TEXT,
    bundle                  BYTEA NOT NULL,             -- gzip of the cwb-snapshot/1 JSON
    bundle_sha256           TEXT NOT NULL,              -- of the uncompressed JSON
    warnings                JSONB NOT NULL DEFAULT '[]',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_cw_snapshots_commit
    ON cw_snapshots (resident_id, commit_sha, COALESCE(plugin_tree_used, ''));

ALTER TABLE cw_residents ADD CONSTRAINT fk_cw_residents_current_snapshot
    FOREIGN KEY (current_snapshot_id) REFERENCES cw_snapshots(id);

-- status/review are json (not jsonb) so their key order survives the round trip.
CREATE TABLE cw_derived (
    resident_id             INTEGER PRIMARY KEY REFERENCES cw_residents(id) ON DELETE RESTRICT,
    snapshot_id             BIGINT NOT NULL REFERENCES cw_snapshots(id),
    decisions_version       BIGINT NOT NULL,            -- projects.decisions_rev it was derived at
    status                  JSON NOT NULL,
    review                  JSON,
    decision_checks         JSONB NOT NULL DEFAULT '{}',  -- {decision id: [error]}
    checks_decisions_version BIGINT,
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cw_sync_jobs (
    id                      BIGSERIAL PRIMARY KEY,
    resident_id             INTEGER NOT NULL REFERENCES cw_residents(id) ON DELETE RESTRICT,
    kind                    TEXT NOT NULL,
    trigger                 TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'queued',
    attempt                 INTEGER NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at              TIMESTAMPTZ,
    finished_at             TIMESTAMPTZ,
    error                   TEXT,                       -- cut to 4000 characters
    commit_sha              TEXT,
    snapshot_id             BIGINT REFERENCES cw_snapshots(id),
    CONSTRAINT ck_cw_sync_jobs_kind CHECK (kind IN ('commit', 'derive', 'checks')),
    CONSTRAINT ck_cw_sync_jobs_trigger CHECK (trigger IN ('poll', 'webhook', 'manual', 'decisions')),
    CONSTRAINT ck_cw_sync_jobs_status CHECK (status IN ('queued', 'running', 'done', 'failed'))
);
-- One live job per resident and kind; a second enqueue is a no-op.
CREATE UNIQUE INDEX uq_cw_sync_jobs_live ON cw_sync_jobs (resident_id, kind)
    WHERE status IN ('queued', 'running');
CREATE INDEX ix_cw_sync_jobs_queued ON cw_sync_jobs (created_at, id) WHERE status = 'queued';
CREATE INDEX ix_cw_sync_jobs_resident ON cw_sync_jobs (resident_id, id);

-- Append-only: every stale mark and clear, so clearing never loses history.
CREATE TABLE cw_stale_events (
    id                      BIGSERIAL PRIMARY KEY,
    resident_id             INTEGER NOT NULL REFERENCES cw_residents(id) ON DELETE RESTRICT,
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

-- Plan 1's stale columns now point at the snapshot that made the row stale.
ALTER TABLE votes ADD CONSTRAINT fk_votes_stale_snapshot
    FOREIGN KEY (stale_since_snapshot_id) REFERENCES cw_snapshots(id);
ALTER TABLE decisions ADD CONSTRAINT fk_decisions_stale_snapshot
    FOREIGN KEY (stale_since_snapshot_id) REFERENCES cw_snapshots(id);
