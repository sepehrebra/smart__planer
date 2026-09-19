BEGIN;

CREATE TABLE recurring_activities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_request_id uuid NOT NULL,
    creation_digest text NOT NULL CHECK (creation_digest ~ '^[0-9a-f]{64}$'),
    title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 200),
    description text NOT NULL DEFAULT '' CHECK (char_length(description) <= 2000),
    duration_minutes integer NOT NULL CHECK (duration_minutes BETWEEN 1 AND 1440),
    priority integer NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),
    splittable boolean NOT NULL DEFAULT false,
    preferred_period text NOT NULL DEFAULT 'any'
        CHECK (preferred_period IN ('any', 'morning', 'afternoon', 'evening')),
    frequency text NOT NULL CHECK (frequency IN ('daily', 'weekdays', 'weekly')),
    weekdays text[] NOT NULL DEFAULT '{}',
    start_date date NOT NULL CHECK (start_date BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'),
    end_date date CHECK (end_date BETWEEN start_date AND DATE '2100-12-31'),
    timezone text NOT NULL CHECK (char_length(timezone) BETWEEN 1 AND 100),
    active boolean NOT NULL DEFAULT true,
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at timestamptz,
    UNIQUE (user_id, client_request_id),
    UNIQUE (id, user_id),
    CHECK (updated_at >= created_at),
    CHECK (array_position(weekdays, NULL) IS NULL),
    CHECK (weekdays <@ ARRAY['mon','tue','wed','thu','fri','sat','sun']::text[]),
    CHECK ((frequency = 'weekdays' AND cardinality(weekdays) BETWEEN 1 AND 7)
        OR (frequency <> 'weekdays' AND cardinality(weekdays) = 0))
);
CREATE INDEX recurring_activities_owner_live ON recurring_activities (user_id, created_at DESC, id DESC)
    WHERE deleted_at IS NULL;

ALTER TABLE tasks ADD COLUMN recurrence_id uuid;
ALTER TABLE tasks ADD COLUMN occurrence_date date;
ALTER TABLE tasks ADD CONSTRAINT tasks_recurrence_pair
    CHECK ((recurrence_id IS NULL) = (occurrence_date IS NULL));
ALTER TABLE tasks ADD CONSTRAINT tasks_recurrence_owner_fk
    FOREIGN KEY (recurrence_id, user_id) REFERENCES recurring_activities(id, user_id);
-- Tombstones remain unique: deleting one occurrence must not recreate it later.
ALTER TABLE tasks ADD CONSTRAINT tasks_recurrence_day_unique
    UNIQUE (user_id, recurrence_id, occurrence_date);

INSERT INTO schema_migrations (version) VALUES (4);
COMMIT;
