ALTER TABLE user_preferences
    ADD COLUMN activity_start time without time zone NOT NULL DEFAULT '09:00',
    ADD COLUMN activity_end time without time zone NOT NULL DEFAULT '18:00',
    ADD CONSTRAINT user_preferences_activity_order CHECK (activity_end > activity_start);

INSERT INTO schema_migrations(version) VALUES (6);
