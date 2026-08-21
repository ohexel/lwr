ALTER TABLE raw.lor_plr
    ADD COLUMN IF NOT EXISTS source_url TEXT;

ALTER TABLE raw.lor_plr
    ADD COLUMN IF NOT EXISTS publisher TEXT;

ALTER TABLE raw.lor_plr
    ADD COLUMN IF NOT EXISTS license TEXT;


CREATE TABLE IF NOT EXISTS raw.icon_grid_source (
    source_grid_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_url TEXT NOT NULL,
    vertex_count INTEGER NOT NULL,
    cell_count INTEGER NOT NULL,
    loaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (vertex_count > 0),
    CHECK (cell_count > 0)
);


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'icon_grid_vertex_source_fk'
    ) THEN
        ALTER TABLE raw.icon_grid_vertex
            ADD CONSTRAINT icon_grid_vertex_source_fk
            FOREIGN KEY (source_grid_id)
            REFERENCES raw.icon_grid_source (source_grid_id)
            ON DELETE CASCADE;
    END IF;
END
$$;


ALTER TABLE normalized.plr
    ADD COLUMN IF NOT EXISTS source_sha256 TEXT;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM normalized.plr
        WHERE source_sha256 IS NULL
    ) THEN
        RAISE EXCEPTION
            'Cannot make normalized.plr.source_sha256 mandatory: '
            'rows without provenance already exist';
    END IF;
END
$$;

ALTER TABLE normalized.plr
    ALTER COLUMN source_sha256 SET NOT NULL;


CREATE TABLE IF NOT EXISTS normalized.plr_geometry_rejected (
    source_sha256 TEXT NOT NULL,
    source_row_id BIGINT NOT NULL,
    plr_id TEXT,
    geography_version TEXT,
    rejection_reason TEXT NOT NULL,
    rejected_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        source_sha256,
        source_row_id
    )
);


CREATE TABLE IF NOT EXISTS normalized.icon_geometry_rejected (
    source_grid_id TEXT NOT NULL,
    cell_index INTEGER NOT NULL,
    rejection_reason TEXT NOT NULL,
    rejected_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        source_grid_id,
        cell_index
    ),
    FOREIGN KEY (source_grid_id)
        REFERENCES raw.icon_grid_source (source_grid_id)
        ON DELETE CASCADE
);


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'normalized_plr_geometry_valid'
    ) THEN
        ALTER TABLE normalized.plr
            ADD CONSTRAINT normalized_plr_geometry_valid
            CHECK (ST_IsValid(geometry));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'normalized_plr_geometry_positive_area'
    ) THEN
        ALTER TABLE normalized.plr
            ADD CONSTRAINT normalized_plr_geometry_positive_area
            CHECK (ST_Area(geometry) > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'normalized_icon_cell_geometry_valid'
    ) THEN
        ALTER TABLE normalized.icon_cell
            ADD CONSTRAINT normalized_icon_cell_geometry_valid
            CHECK (ST_IsValid(geometry));
    END IF;
END
$$;


CREATE INDEX IF NOT EXISTS idx_raw_lor_source_sha256
    ON raw.lor_plr (source_sha256);

CREATE INDEX IF NOT EXISTS idx_normalized_plr_source_sha256
    ON normalized.plr (source_sha256);

CREATE INDEX IF NOT EXISTS idx_plr_geometry_rejected_source
    ON normalized.plr_geometry_rejected (source_sha256);

CREATE INDEX IF NOT EXISTS idx_icon_geometry_rejected_source
    ON normalized.icon_geometry_rejected (source_grid_id);
