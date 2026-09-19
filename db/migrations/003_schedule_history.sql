BEGIN;

CREATE TABLE schedules (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    start_at timestamptz NOT NULL,
    end_at timestamptz NOT NULL CHECK (end_at > start_at),
    timezone text NOT NULL CHECK (char_length(timezone) BETWEEN 1 AND 100),
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    current_revision integer NOT NULL DEFAULT 1 CHECK (current_revision >= 1),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (id, user_id),
    CHECK (updated_at >= created_at)
);
CREATE INDEX schedules_owner_horizon ON schedules (user_id, start_at, end_at);
CREATE INDEX schedules_owner_updated ON schedules (user_id, updated_at DESC, id DESC);

CREATE TABLE schedule_versions (
    schedule_id uuid NOT NULL,
    user_id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    state jsonb NOT NULL CHECK (jsonb_typeof(state) = 'object' AND octet_length(state::text) <= 524288),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (schedule_id, revision),
    FOREIGN KEY (schedule_id, user_id) REFERENCES schedules(id, user_id) ON DELETE CASCADE
);
ALTER TABLE schedules ADD CONSTRAINT schedules_current_revision_fk
    FOREIGN KEY (id, current_revision) REFERENCES schedule_versions(schedule_id, revision)
    DEFERRABLE INITIALLY DEFERRED;

-- Small durable receipts survive history trimming and redo invalidation.
CREATE TABLE schedule_commands (
    user_id uuid NOT NULL,
    client_request_id uuid NOT NULL,
    schedule_id uuid NOT NULL,
    request_digest text NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    applied_version integer NOT NULL CHECK (applied_version >= 1),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, client_request_id),
    FOREIGN KEY (schedule_id, user_id) REFERENCES schedules(id, user_id) ON DELETE CASCADE
);
CREATE INDEX schedule_commands_schedule ON schedule_commands(schedule_id);

-- A one-time task may be selected in several plans, but assigned in only one.
CREATE TABLE schedule_allocations (
    user_id uuid NOT NULL,
    task_id uuid NOT NULL,
    schedule_id uuid NOT NULL,
    PRIMARY KEY (user_id, task_id),
    FOREIGN KEY (schedule_id, user_id) REFERENCES schedules(id, user_id) ON DELETE CASCADE,
    FOREIGN KEY (task_id, user_id) REFERENCES tasks(id, user_id) ON DELETE CASCADE
);
CREATE INDEX schedule_allocations_schedule ON schedule_allocations(schedule_id);

INSERT INTO schema_migrations (version) VALUES (3);
COMMIT;
