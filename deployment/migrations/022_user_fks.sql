-- 022_user_fks.sql: votes and hosted decisions point at the snapgate account that made them.

ALTER TABLE votes
    ADD CONSTRAINT fk_votes_user FOREIGN KEY (user_id) REFERENCES snapgate_users(id);
ALTER TABLE decisions
    ADD CONSTRAINT fk_decisions_decided_by_user FOREIGN KEY (decided_by_user_id) REFERENCES snapgate_users(id);
