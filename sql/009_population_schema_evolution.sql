ALTER TABLE raw.afs_population
    ADD COLUMN IF NOT EXISTS reference_code_source TEXT;

ALTER TABLE raw.afs_population
    ADD COLUMN IF NOT EXISTS source_url TEXT;

ALTER TABLE raw.afs_population
    ADD COLUMN IF NOT EXISTS publisher TEXT;

ALTER TABLE normalized.plr_population_65plus
    ADD COLUMN IF NOT EXISTS source_sha256 TEXT;

ALTER TABLE normalized.plr_population_rejected
    ADD COLUMN IF NOT EXISTS source_sha256 TEXT;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM normalized.plr_population_65plus
        WHERE source_sha256 IS NULL
    ) THEN
        RAISE EXCEPTION
            'Cannot make source_sha256 mandatory: '
            'normalized.plr_population_65plus already contains rows '
            'without provenance';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM normalized.plr_population_rejected
        WHERE source_sha256 IS NULL
    ) THEN
        RAISE EXCEPTION
            'Cannot make source_sha256 mandatory: '
            'normalized.plr_population_rejected already contains rows '
            'without provenance';
    END IF;
END
$$;

ALTER TABLE normalized.plr_population_65plus
    ALTER COLUMN source_sha256 SET NOT NULL;

ALTER TABLE normalized.plr_population_rejected
    ALTER COLUMN source_sha256 SET NOT NULL;

ALTER TABLE normalized.plr_population_65plus
    DROP CONSTRAINT IF EXISTS plr_population_65plus_pkey;

ALTER TABLE normalized.plr_population_65plus
    ADD PRIMARY KEY (
        reference_date,
        plr_id
    );

CREATE INDEX IF NOT EXISTS idx_raw_afs_population_source
    ON raw.afs_population (source_sha256);

CREATE INDEX IF NOT EXISTS idx_normalized_population_source
    ON normalized.plr_population_65plus (source_sha256);

CREATE INDEX IF NOT EXISTS idx_normalized_population_rejected_source
    ON normalized.plr_population_rejected (source_sha256);
