BEGIN;

ALTER TABLE tasks ADD COLUMN creation_digest text;
ALTER TABLE tasks ADD COLUMN deleted_at timestamptz;
ALTER TABLE tasks ADD CONSTRAINT tasks_creation_digest_format
    CHECK (creation_digest IS NULL OR creation_digest ~ '^[0-9a-f]{64}$');
-- Older rows remain readable/editable. Reusing their request IDs returns 409;
-- the unknown original creation payload is never invented during migration.

CREATE TABLE sessions (
    token_digest text PRIMARY KEY CHECK (token_digest ~ '^[0-9a-f]{64}$'),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > created_at)
);
CREATE INDEX sessions_owner ON sessions (user_id);
CREATE INDEX sessions_expiry ON sessions (expires_at);
CREATE INDEX tasks_owner_live_created ON tasks (user_id, created_at DESC, id DESC)
    WHERE deleted_at IS NULL;

INSERT INTO schema_migrations (version) VALUES (2);
COMMIT;
