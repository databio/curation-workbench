-- 001_snapgate.sql: snapgate's base tables (users, refresh tokens, API keys, exchange codes,
-- OAuth tables), copied from Pigeon's frozen 001_snapgate.sql (generated from snapgate's
-- SQLModel metadata). Frozen: a snapgate model change gets a new 09x file. Ranges: README.

CREATE TYPE userstatus AS ENUM ('PENDING', 'APPROVED', 'DENIED', 'SUSPENDED');

CREATE TABLE snapgate_users (
	id SERIAL NOT NULL, 
	google_id VARCHAR NOT NULL, 
	email VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	picture VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE, 
	last_login TIMESTAMP WITH TIME ZONE, 
	status userstatus NOT NULL, 
	approved_by INTEGER, 
	approved_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(approved_by) REFERENCES snapgate_users (id)
);

CREATE UNIQUE INDEX ix_snapgate_users_email ON snapgate_users (email);

CREATE UNIQUE INDEX ix_snapgate_users_google_id ON snapgate_users (google_id);

CREATE TABLE refresh_tokens (
	id SERIAL NOT NULL, 
	token_hash VARCHAR NOT NULL, 
	user_id INTEGER NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE, 
	used BOOLEAN NOT NULL, 
	used_at TIMESTAMP WITH TIME ZONE, 
	revoked BOOLEAN NOT NULL, 
	token_family VARCHAR NOT NULL, 
	parent_token_id INTEGER, 
	created_from_ip VARCHAR, 
	last_used_from_ip VARCHAR, 
	user_agent VARCHAR, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES snapgate_users (id), 
	FOREIGN KEY(parent_token_id) REFERENCES refresh_tokens (id)
);

CREATE INDEX ix_refresh_tokens_token_family ON refresh_tokens (token_family);

CREATE UNIQUE INDEX ix_refresh_tokens_token_hash ON refresh_tokens (token_hash);

CREATE INDEX ix_refresh_tokens_used ON refresh_tokens (used);

CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);

CREATE TABLE oauth_connections (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	client_id VARCHAR NOT NULL, 
	client_name VARCHAR NOT NULL, 
	client_host VARCHAR, 
	scopes JSON DEFAULT '[]' NOT NULL, 
	refresh_hash VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE, 
	last_refreshed_at TIMESTAMP WITH TIME ZONE, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES snapgate_users (id)
);

CREATE INDEX ix_oauth_connections_client_id ON oauth_connections (client_id);

CREATE INDEX ix_oauth_connections_expires_at ON oauth_connections (expires_at);

CREATE UNIQUE INDEX ix_oauth_connections_refresh_hash ON oauth_connections (refresh_hash);

CREATE INDEX ix_oauth_connections_user_id ON oauth_connections (user_id);

CREATE TABLE api_keys (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	description VARCHAR, 
	key_hash VARCHAR NOT NULL, 
	prefix VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE, 
	last_used_at TIMESTAMP WITH TIME ZONE, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	revoked BOOLEAN NOT NULL, 
	scopes JSON DEFAULT '[]' NOT NULL, 
	confined_to VARCHAR, 
	connection_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES snapgate_users (id), 
	FOREIGN KEY(connection_id) REFERENCES oauth_connections (id)
);

CREATE INDEX ix_api_keys_connection_id ON api_keys (connection_id);

CREATE INDEX ix_api_keys_prefix ON api_keys (prefix);

CREATE INDEX ix_api_keys_user_id ON api_keys (user_id);

CREATE TABLE exchange_codes (
	id SERIAL NOT NULL, 
	code_hash VARCHAR NOT NULL, 
	user_id INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE, 
	used BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES snapgate_users (id)
);

CREATE UNIQUE INDEX ix_exchange_codes_code_hash ON exchange_codes (code_hash);

CREATE TABLE oauth_clients (
	id SERIAL NOT NULL, 
	client_id VARCHAR NOT NULL, 
	client_name VARCHAR NOT NULL, 
	redirect_uris JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_oauth_clients_client_id ON oauth_clients (client_id);

CREATE TABLE oauth_codes (
	id SERIAL NOT NULL, 
	code_hash VARCHAR NOT NULL, 
	client_id VARCHAR NOT NULL, 
	client_name VARCHAR NOT NULL, 
	client_host VARCHAR, 
	user_id INTEGER NOT NULL, 
	scopes JSON DEFAULT '[]' NOT NULL, 
	redirect_uri VARCHAR NOT NULL, 
	code_challenge VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	used_at TIMESTAMP WITH TIME ZONE, 
	connection_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES snapgate_users (id), 
	FOREIGN KEY(connection_id) REFERENCES oauth_connections (id) ON DELETE SET NULL
);

CREATE INDEX ix_oauth_codes_client_id ON oauth_codes (client_id);

CREATE UNIQUE INDEX ix_oauth_codes_code_hash ON oauth_codes (code_hash);

CREATE INDEX ix_oauth_codes_connection_id ON oauth_codes (connection_id);

CREATE INDEX ix_oauth_codes_expires_at ON oauth_codes (expires_at);

CREATE INDEX ix_oauth_codes_user_id ON oauth_codes (user_id);

CREATE TABLE oauth_refresh_history (
	id SERIAL NOT NULL, 
	connection_id INTEGER NOT NULL, 
	token_hash VARCHAR NOT NULL, 
	retired_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	grace_until TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(connection_id) REFERENCES oauth_connections (id) ON DELETE CASCADE
);

CREATE INDEX ix_oauth_refresh_history_connection_id ON oauth_refresh_history (connection_id);

CREATE UNIQUE INDEX ix_oauth_refresh_history_token_hash ON oauth_refresh_history (token_hash);
