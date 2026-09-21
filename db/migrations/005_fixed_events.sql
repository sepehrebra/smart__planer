CREATE TABLE fixed_events (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title varchar(200) NOT NULL CHECK (length(btrim(title)) > 0),
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at > starts_at)
);

CREATE INDEX fixed_events_user_time_idx
    ON fixed_events (user_id, starts_at, ends_at);

INSERT INTO schema_migrations(version) VALUES (5);
