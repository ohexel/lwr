BEGIN;

-- Source files are a bounded landing zone, not a permanent Germany-wide
-- archive. Keep their URL, checksum, size, and coverage after local deletion.
ALTER TABLE raw.hostrada_month_source
    ADD COLUMN IF NOT EXISTS source_deleted_at_utc TIMESTAMPTZ;

COMMENT ON COLUMN raw.hostrada_month_source.source_deleted_at_utc IS
    'Time at which the validated local source file was removed after both '
    'monthly analytical outputs passed their completeness check.';

COMMIT;
