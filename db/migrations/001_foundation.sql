-- Initial PostgreSQL schema proposal. NOT executed against PostgreSQL in this step.
-- Apply once to an empty development database after review. No DROP statements.
-- Later writes must enforce owner ID and expected_version atomically.
BEGIN;

CREATE TABLE schema_migrations (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text NOT NULL CHECK (char_length(email) BETWEEN 3 AND 254),
    password_hash text NOT NULL,
    timezone text NOT NULL DEFAULT 'UTC',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (email = btrim(email)),
    CHECK (char_length(timezone) BETWEEN 1 AND 100)
);
CREATE UNIQUE INDEX users_email_lower_unique ON users (lower(email));

CREATE TABLE user_preferences (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    chronotype text NOT NULL DEFAULT 'neutral'
        CHECK (chronotype IN ('morning', 'neutral', 'evening')),
    focus_block_minutes integer NOT NULL DEFAULT 45
        CHECK (focus_block_minutes IN (25, 45, 60, 90)),
    break_minutes integer NOT NULL DEFAULT 10
        CHECK (break_minutes BETWEEN 0 AND 60),
    flexibility text NOT NULL DEFAULT 'medium'
        CHECK (flexibility IN ('low', 'medium', 'high')),
    workload text NOT NULL DEFAULT 'medium'
        CHECK (workload IN ('light', 'medium', 'intense')),
    deep_work_period text NOT NULL DEFAULT 'afternoon'
        CHECK (deep_work_period IN ('morning', 'afternoon', 'evening')),
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_request_id uuid NOT NULL,
    title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 200),
    description text NOT NULL DEFAULT '' CHECK (char_length(description) <= 2000),
    duration_minutes integer NOT NULL CHECK (duration_minutes BETWEEN 1 AND 10080),
    priority integer NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'completed', 'cancelled')),
    splittable boolean NOT NULL DEFAULT false,
    preferred_period text NOT NULL DEFAULT 'any'
        CHECK (preferred_period IN ('any', 'morning', 'afternoon', 'evening')),
    earliest_start timestamptz,
    deadline timestamptz,
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, client_request_id),
    -- Enables future composite FKs enforcing same-owner schedule block links.
    UNIQUE (id, user_id),
    CHECK (updated_at >= created_at),
    CHECK (
        earliest_start IS NULL OR deadline IS NULL OR
        EXTRACT(EPOCH FROM (deadline - earliest_start)) >= duration_minutes * 60
    )
);
CREATE INDEX tasks_owner_status_deadline ON tasks (user_id, status, deadline);

INSERT INTO schema_migrations (version) VALUES (1);
COMMIT;
