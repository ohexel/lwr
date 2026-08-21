CREATE TABLE IF NOT EXISTS raw.afs_population (
    source_row_id BIGSERIAL PRIMARY KEY,
    plr_id_source TEXT,
    population_total_source TEXT,
    population_65_79_source TEXT,
    population_80plus_source TEXT,
    reference_date DATE,
    publication_date DATE,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    loaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw.lor_plr (
    source_row_id BIGSERIAL PRIMARY KEY,
    plr_id_source TEXT,
    geometry_source geometry,
    source_crs TEXT,
    geography_version TEXT,
    reference_date DATE,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    loaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw.icon_grid_vertex (
    source_grid_id TEXT NOT NULL,
    vertex_index INTEGER NOT NULL,
    longitude_deg DOUBLE PRECISION NOT NULL,
    latitude_deg DOUBLE PRECISION NOT NULL,
    loaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source_grid_id, vertex_index)
);

CREATE TABLE IF NOT EXISTS raw.icon_grid_cell_vertex (
    source_grid_id TEXT NOT NULL,
    cell_index INTEGER NOT NULL,
    vertex_order SMALLINT NOT NULL,
    vertex_index INTEGER NOT NULL,
    loaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        source_grid_id,
        cell_index,
        vertex_order
    ),
    CONSTRAINT icon_grid_cell_vertex_vertex_fk
        FOREIGN KEY (
            source_grid_id,
            vertex_index
        )
        REFERENCES raw.icon_grid_vertex (
            source_grid_id,
            vertex_index
        )
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS raw.icon_d2_ruc_field (
    run_time_utc TIMESTAMPTZ NOT NULL,
    lead_time TEXT NOT NULL,
    valid_time_utc TIMESTAMPTZ NOT NULL,
    indicator TEXT NOT NULL,
    cell_index INTEGER NOT NULL,
    source_value DOUBLE PRECISION,
    source_unit TEXT NOT NULL,
    source_url TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    loaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        run_time_utc,
        lead_time,
        indicator,
        cell_index
    )
);
