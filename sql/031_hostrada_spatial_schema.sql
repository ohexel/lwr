BEGIN;

CREATE TABLE IF NOT EXISTS normalized.hostrada_grid (
    source_grid_id TEXT PRIMARY KEY,
    grid_fingerprint TEXT NOT NULL UNIQUE,
    dataset_version TEXT NOT NULL,
    source_srid INTEGER NOT NULL,
    target_srid INTEGER NOT NULL,
    x_origin_m DOUBLE PRECISION NOT NULL,
    y_origin_m DOUBLE PRECISION NOT NULL,
    x_count INTEGER NOT NULL,
    y_count INTEGER NOT NULL,
    x_spacing_m DOUBLE PRECISION NOT NULL,
    y_spacing_m DOUBLE PRECISION NOT NULL,
    registered_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (BTRIM(source_grid_id) <> ''),
    CHECK (grid_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (BTRIM(dataset_version) <> ''),
    CHECK (source_srid = 3034),
    CHECK (target_srid = 25833),
    CHECK (x_count > 0),
    CHECK (y_count > 0),
    CHECK (x_spacing_m > 0),
    CHECK (y_spacing_m > 0)
);


CREATE TABLE IF NOT EXISTS normalized.hostrada_cell (
    source_grid_id TEXT NOT NULL,
    geography_version TEXT NOT NULL,
    y_index INTEGER NOT NULL,
    x_index INTEGER NOT NULL,
    geometry geometry(Polygon, 25833) NOT NULL,
    hostrada_cell_area_m2 DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (
        source_grid_id,
        geography_version,
        y_index,
        x_index
    ),
    FOREIGN KEY (source_grid_id)
        REFERENCES normalized.hostrada_grid (source_grid_id)
        ON DELETE CASCADE,
    CHECK (BTRIM(geography_version) <> ''),
    CHECK (y_index >= 0),
    CHECK (x_index >= 0),
    CHECK (ST_IsValid(geometry)),
    CHECK (NOT ST_IsEmpty(geometry)),
    CHECK (hostrada_cell_area_m2 > 0)
);

CREATE INDEX IF NOT EXISTS idx_hostrada_cell_geometry
    ON normalized.hostrada_cell
    USING GIST (geometry);


CREATE TABLE IF NOT EXISTS normalized.hostrada_plr_area_bridge (
    plr_id TEXT NOT NULL,
    geography_version TEXT NOT NULL,
    source_grid_id TEXT NOT NULL,
    y_index INTEGER NOT NULL,
    x_index INTEGER NOT NULL,
    intersection_area_m2 DOUBLE PRECISION NOT NULL,
    plr_area_m2 DOUBLE PRECISION NOT NULL,
    hostrada_cell_area_m2 DOUBLE PRECISION NOT NULL,
    fraction_of_plr DOUBLE PRECISION NOT NULL,
    fraction_of_hostrada_cell DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (
        plr_id,
        geography_version,
        source_grid_id,
        y_index,
        x_index
    ),
    FOREIGN KEY (
        plr_id,
        geography_version
    ) REFERENCES normalized.plr (
        plr_id,
        geography_version
    ),
    FOREIGN KEY (
        source_grid_id,
        geography_version,
        y_index,
        x_index
    ) REFERENCES normalized.hostrada_cell (
        source_grid_id,
        geography_version,
        y_index,
        x_index
    ) ON DELETE CASCADE,
    CHECK (intersection_area_m2 > 0),
    CHECK (plr_area_m2 > 0),
    CHECK (hostrada_cell_area_m2 > 0),
    CHECK (fraction_of_plr > 0),
    CHECK (fraction_of_hostrada_cell > 0)
);

CREATE INDEX IF NOT EXISTS idx_hostrada_plr_bridge_cell
    ON normalized.hostrada_plr_area_bridge (
        source_grid_id,
        geography_version,
        y_index,
        x_index
    );

COMMIT;
