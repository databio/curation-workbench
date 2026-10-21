-- SNAPGATE file - do not edit. Overwritten on updates.
--
-- PostgreSQL tables for snapgate.useradmin (the sign-in log, the account
-- audit log, and role requests). Apps that mount snapgate.useradmin and run
-- their own SQL migrations copy this file, unchanged, into their own next
-- numbered migration. Apps that use SQLModel's create_all need nothing: the
-- tables are created once snapgate.useradmin is imported.
--
-- Needs snapgate_users. No change to any existing table.
-- tests/test_useradmin_sql.py checks this file against the SQLModel tables.

CREATE TABLE snapgate_login_events (
    id SERIAL NOT NULL,
    user_id INTEGER,
    email VARCHAR NOT NULL,
    name VARCHAR,
    allowed BOOLEAN NOT NULL,
    reason VARCHAR,
    ip VARCHAR,
    user_agent VARCHAR,
    method VARCHAR NOT NULL,
    at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (user_id) REFERENCES snapgate_users (id)
);
CREATE INDEX ix_snapgate_login_events_at ON snapgate_login_events (at);
CREATE INDEX ix_snapgate_login_events_email ON snapgate_login_events (email);
CREATE INDEX ix_snapgate_login_events_user_id ON snapgate_login_events (user_id);

-- Append-only. user_id and actor_user_id are not foreign keys, so the
-- record outlives the rows it is about.
CREATE TABLE snapgate_account_audit (
    id SERIAL NOT NULL,
    user_id INTEGER NOT NULL,
    actor_user_id INTEGER,
    actor_label VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    details JSONB DEFAULT '{}' NOT NULL,
    at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_snapgate_account_audit_action CHECK (action IN ('approve', 'suspend', 'unsuspend', 'deny', 'role_request_approve', 'role_request_reject', 'role_request_withdraw', 'role_revoke'))
);
CREATE INDEX ix_snapgate_account_audit_at ON snapgate_account_audit (at);
CREATE INDEX ix_snapgate_account_audit_user_at ON snapgate_account_audit (user_id, at);
CREATE INDEX ix_snapgate_account_audit_user_id ON snapgate_account_audit (user_id);

CREATE TABLE snapgate_role_requests (
    id SERIAL NOT NULL,
    user_id INTEGER NOT NULL,
    role VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    message VARCHAR NOT NULL,
    details JSONB DEFAULT '{}' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    decided_at TIMESTAMP WITH TIME ZONE,
    decided_by INTEGER,
    decision_note VARCHAR,
    PRIMARY KEY (id),
    CONSTRAINT ck_snapgate_role_requests_status CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn')),
    FOREIGN KEY (user_id) REFERENCES snapgate_users (id)
);
CREATE INDEX ix_snapgate_role_requests_role ON snapgate_role_requests (role);
CREATE INDEX ix_snapgate_role_requests_status ON snapgate_role_requests (status);
CREATE INDEX ix_snapgate_role_requests_user_id ON snapgate_role_requests (user_id);
-- At most one pending request per person and role.
CREATE UNIQUE INDEX uq_snapgate_role_requests_pending ON snapgate_role_requests (user_id, role) WHERE status = 'pending';
