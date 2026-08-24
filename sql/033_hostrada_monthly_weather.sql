BEGIN;

-- Raw source files remain on disk. PostgreSQL retains their reproducible
-- provenance without copying three Germany-wide NetCDF grids into a table.
CREATE TABLE IF NOT EXISTS raw.hostrada_month_source (
    source_month_utc DATE NOT NULL,
    variable_name TEXT NOT NULL,
    source_grid_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_size_bytes BIGINT NOT NULL,
    source_unit TEXT NOT NULL,
    first_valid_time_utc TIMESTAMPTZ NOT NULL,
    last_valid_time_utc TIMESTAMPTZ NOT NULL,
    source_hour_count INTEGER NOT NULL,
    loaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source_month_utc, variable_name),
    FOREIGN KEY (source_grid_id)
        REFERENCES normalized.hostrada_grid (source_grid_id),
    CHECK (
        source_month_utc
            = DATE_TRUNC('month', source_month_utc::TIMESTAMP)::DATE
    ),
    CHECK (variable_name IN ('tas', 'hurs', 'sfcWind')),
    CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (source_size_bytes > 0),
    CHECK (source_hour_count > 0),
    CHECK (last_valid_time_utc >= first_valid_time_utc)
);


-- These hourly tables benchmark one complete month before choosing the final
-- historical aggregation design. Humidity and wind are intentionally absent.
CREATE TABLE IF NOT EXISTS analytical.hostrada_plr_hourly (
    source_month_utc DATE NOT NULL,
    valid_time_utc TIMESTAMPTZ NOT NULL,
    plr_id TEXT NOT NULL,
    geography_version TEXT NOT NULL,
    source_grid_id TEXT NOT NULL,
    temperature_c DOUBLE PRECISION NOT NULL,
    apparent_temperature_shade_c DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (source_month_utc, valid_time_utc, plr_id),
    FOREIGN KEY (plr_id, geography_version)
        REFERENCES normalized.plr (plr_id, geography_version),
    FOREIGN KEY (source_grid_id)
        REFERENCES normalized.hostrada_grid (source_grid_id)
);


CREATE TABLE IF NOT EXISTS analytical.hostrada_berlin_hourly (
    source_month_utc DATE NOT NULL,
    valid_time_utc TIMESTAMPTZ NOT NULL,
    geography_version TEXT NOT NULL,
    source_grid_id TEXT NOT NULL,
    temperature_c DOUBLE PRECISION NOT NULL,
    apparent_temperature_shade_c DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (source_month_utc, valid_time_utc),
    FOREIGN KEY (source_grid_id)
        REFERENCES normalized.hostrada_grid (source_grid_id)
);


CREATE OR REPLACE FUNCTION analytical.refresh_hostrada_month(
    p_source_month_utc DATE,
    p_geography_version TEXT,
    p_source_grid_id TEXT
)
RETURNS TABLE (
    source_cell_hour_count BIGINT,
    plr_hour_count BIGINT,
    berlin_hour_count BIGINT,
    expected_hour_count BIGINT,
    expected_cell_count BIGINT,
    expected_plr_count BIGINT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_start_utc TIMESTAMPTZ;
    v_end_utc TIMESTAMPTZ;
    v_expected_hours BIGINT;
    v_expected_cells BIGINT;
    v_expected_plrs BIGINT;
    v_source_rows BIGINT;
    v_plr_rows BIGINT;
    v_berlin_rows BIGINT;
    v_bridge_passed BOOLEAN;
BEGIN
    IF p_source_month_utc IS NULL
       OR p_source_month_utc
            <> DATE_TRUNC('month', p_source_month_utc::TIMESTAMP)::DATE THEN
        RAISE EXCEPTION 'p_source_month_utc must be the first UTC month day';
    END IF;

    v_start_utc := p_source_month_utc::TIMESTAMP AT TIME ZONE 'UTC';
    v_end_utc := (
        p_source_month_utc::TIMESTAMP + INTERVAL '1 month'
    ) AT TIME ZONE 'UTC';
    v_expected_hours := (
        EXTRACT(EPOCH FROM (v_end_utc - v_start_utc)) / 3600
    )::BIGINT;

    SELECT COUNT(*)::BIGINT
    INTO v_expected_plrs
    FROM normalized.plr AS plr_row
    WHERE plr_row.geography_version = p_geography_version;

    SELECT COUNT(*)::BIGINT
    INTO v_expected_cells
    FROM normalized.hostrada_cell AS cell_row
    WHERE cell_row.geography_version = p_geography_version
      AND cell_row.source_grid_id = p_source_grid_id;

    IF v_expected_plrs = 0 OR v_expected_cells = 0 THEN
        RAISE EXCEPTION
            'HOSTRADA geography % or source grid % is not materialized',
            p_geography_version,
            p_source_grid_id;
    END IF;

    SELECT bridge_quality.passed
    INTO v_bridge_passed
    FROM normalized.check_hostrada_plr_area_bridge_quality(
        p_geography_version,
        p_source_grid_id,
        v_expected_plrs::INTEGER
    ) AS bridge_quality;

    IF v_bridge_passed IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'HOSTRADA spatial bridge failed its quality check';
    END IF;

    IF (
        SELECT COUNT(*)
        FROM raw.hostrada_month_source AS source_row
        WHERE source_row.source_month_utc = p_source_month_utc
          AND source_row.source_grid_id = p_source_grid_id
          AND source_row.source_hour_count = v_expected_hours
          AND source_row.first_valid_time_utc = v_start_utc
          AND source_row.last_valid_time_utc
                = v_end_utc - INTERVAL '1 hour'
    ) <> 3 THEN
        RAISE EXCEPTION
            'HOSTRADA month % requires three validated source files',
            p_source_month_utc;
    END IF;

    SELECT COUNT(*)::BIGINT
    INTO v_source_rows
    FROM pg_temp.hostrada_cell_hour_stage AS stage_row;

    IF v_source_rows <> v_expected_hours * v_expected_cells THEN
        RAISE EXCEPTION
            'HOSTRADA staging has % rows; expected % hours x % cells',
            v_source_rows,
            v_expected_hours,
            v_expected_cells;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_temp.hostrada_cell_hour_stage AS stage_row
        LEFT JOIN normalized.hostrada_cell AS cell_row
          ON cell_row.source_grid_id = p_source_grid_id
         AND cell_row.geography_version = p_geography_version
         AND cell_row.y_index = stage_row.y_index
         AND cell_row.x_index = stage_row.x_index
        WHERE stage_row.valid_time_utc < v_start_utc
           OR stage_row.valid_time_utc >= v_end_utc
           OR cell_row.source_grid_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'HOSTRADA staging contains an unexpected hour or grid cell';
    END IF;

    -- Deletes and replacements share the caller's transaction. A failed rerun
    -- therefore rolls back to the last accepted version of both tables.
    DELETE FROM analytical.hostrada_plr_hourly AS hourly_row
    WHERE hourly_row.source_month_utc = p_source_month_utc;

    DELETE FROM analytical.hostrada_berlin_hourly AS hourly_row
    WHERE hourly_row.source_month_utc = p_source_month_utc;

    INSERT INTO analytical.hostrada_plr_hourly (
        source_month_utc,
        valid_time_utc,
        plr_id,
        geography_version,
        source_grid_id,
        temperature_c,
        apparent_temperature_shade_c
    )
    WITH cell_weather AS MATERIALIZED (
        SELECT
            stage_row.valid_time_utc,
            stage_row.y_index,
            stage_row.x_index,
            stage_row.temperature_c,
            normalized.calculate_apparent_temperature_shade_c(
                stage_row.temperature_c,
                stage_row.relative_humidity_percent,
                stage_row.wind_speed_10m_ms
            ) AS apparent_temperature_shade_c
        FROM pg_temp.hostrada_cell_hour_stage AS stage_row
    )
    SELECT
        p_source_month_utc,
        weather_row.valid_time_utc,
        bridge_row.plr_id,
        p_geography_version,
        p_source_grid_id,
        SUM(weather_row.temperature_c * bridge_row.fraction_of_plr)
            / SUM(bridge_row.fraction_of_plr),
        SUM(
            weather_row.apparent_temperature_shade_c
            * bridge_row.fraction_of_plr
        ) / SUM(bridge_row.fraction_of_plr)
    FROM cell_weather AS weather_row
    JOIN normalized.hostrada_plr_area_bridge AS bridge_row
      ON bridge_row.geography_version = p_geography_version
     AND bridge_row.source_grid_id = p_source_grid_id
     AND bridge_row.y_index = weather_row.y_index
     AND bridge_row.x_index = weather_row.x_index
    GROUP BY weather_row.valid_time_utc, bridge_row.plr_id;

    GET DIAGNOSTICS v_plr_rows = ROW_COUNT;

    IF v_plr_rows <> v_expected_hours * v_expected_plrs THEN
        RAISE EXCEPTION
            'HOSTRADA PLR output has % rows; expected % hours x % PLRs',
            v_plr_rows,
            v_expected_hours,
            v_expected_plrs;
    END IF;

    -- Berlin means the actual union of PLR areas, not the bounding box and not
    -- an unweighted mean of differently sized planning areas.
    INSERT INTO analytical.hostrada_berlin_hourly (
        source_month_utc,
        valid_time_utc,
        geography_version,
        source_grid_id,
        temperature_c,
        apparent_temperature_shade_c
    )
    SELECT
        p_source_month_utc,
        hourly_row.valid_time_utc,
        p_geography_version,
        p_source_grid_id,
        SUM(hourly_row.temperature_c * ST_Area(plr_row.geometry))
            / SUM(ST_Area(plr_row.geometry)),
        SUM(
            hourly_row.apparent_temperature_shade_c
            * ST_Area(plr_row.geometry)
        ) / SUM(ST_Area(plr_row.geometry))
    FROM analytical.hostrada_plr_hourly AS hourly_row
    JOIN normalized.plr AS plr_row
      ON plr_row.plr_id = hourly_row.plr_id
     AND plr_row.geography_version = p_geography_version
    WHERE hourly_row.source_month_utc = p_source_month_utc
      AND hourly_row.geography_version = p_geography_version
      AND hourly_row.source_grid_id = p_source_grid_id
    GROUP BY hourly_row.valid_time_utc;

    GET DIAGNOSTICS v_berlin_rows = ROW_COUNT;

    IF v_berlin_rows <> v_expected_hours THEN
        RAISE EXCEPTION
            'HOSTRADA Berlin output has % rows; expected %',
            v_berlin_rows,
            v_expected_hours;
    END IF;

    RETURN QUERY
    SELECT
        v_source_rows,
        v_plr_rows,
        v_berlin_rows,
        v_expected_hours,
        v_expected_cells,
        v_expected_plrs;
END;
$$;


CREATE OR REPLACE FUNCTION analytical.check_hostrada_month_quality(
    p_source_month_utc DATE,
    p_geography_version TEXT,
    p_source_grid_id TEXT
)
RETURNS TABLE (
    passed BOOLEAN,
    source_file_count BIGINT,
    expected_hour_count BIGINT,
    expected_plr_count BIGINT,
    plr_hour_count BIGINT,
    berlin_hour_count BIGINT,
    incomplete_plr_hour_count BIGINT,
    missing_berlin_hour_count BIGINT
)
LANGUAGE sql
STABLE
AS $$
    WITH bounds AS (
        SELECT
            p_source_month_utc::TIMESTAMP
                AT TIME ZONE 'UTC' AS start_utc,
            (
                p_source_month_utc::TIMESTAMP + INTERVAL '1 month'
            ) AT TIME ZONE 'UTC' AS end_utc
    ),
    expected_hours AS (
        SELECT generated_hour.valid_time_utc
        FROM bounds
        CROSS JOIN LATERAL generate_series(
            bounds.start_utc,
            bounds.end_utc - INTERVAL '1 hour',
            INTERVAL '1 hour'
        ) AS generated_hour(valid_time_utc)
    ),
    plr_counts AS (
        SELECT
            hourly_row.valid_time_utc,
            COUNT(*)::BIGINT AS row_count
        FROM analytical.hostrada_plr_hourly AS hourly_row
        WHERE hourly_row.source_month_utc = p_source_month_utc
          AND hourly_row.geography_version = p_geography_version
          AND hourly_row.source_grid_id = p_source_grid_id
        GROUP BY hourly_row.valid_time_utc
    ),
    totals AS (
        SELECT
            (
                SELECT COUNT(*)::BIGINT
                FROM raw.hostrada_month_source AS source_row
                WHERE source_row.source_month_utc = p_source_month_utc
                  AND source_row.source_grid_id = p_source_grid_id
            ) AS files,
            (SELECT COUNT(*)::BIGINT FROM expected_hours) AS hours,
            (
                SELECT COUNT(*)::BIGINT
                FROM normalized.plr AS plr_row
                WHERE plr_row.geography_version = p_geography_version
            ) AS plrs,
            (
                SELECT COUNT(*)::BIGINT
                FROM analytical.hostrada_plr_hourly AS hourly_row
                WHERE hourly_row.source_month_utc = p_source_month_utc
                  AND hourly_row.geography_version = p_geography_version
                  AND hourly_row.source_grid_id = p_source_grid_id
            ) AS plr_rows,
            (
                SELECT COUNT(*)::BIGINT
                FROM analytical.hostrada_berlin_hourly AS hourly_row
                WHERE hourly_row.source_month_utc = p_source_month_utc
                  AND hourly_row.geography_version = p_geography_version
                  AND hourly_row.source_grid_id = p_source_grid_id
            ) AS berlin_rows
    ),
    hourly_quality AS (
        SELECT
            COUNT(*) FILTER (
                WHERE COALESCE(plr_counts.row_count, 0) <> totals.plrs
            )::BIGINT AS incomplete_plr_hours,
            COUNT(*) FILTER (
                WHERE berlin_row.valid_time_utc IS NULL
            )::BIGINT AS missing_berlin_hours
        FROM expected_hours AS expected_hour
        CROSS JOIN totals
        LEFT JOIN plr_counts
          ON plr_counts.valid_time_utc = expected_hour.valid_time_utc
        LEFT JOIN analytical.hostrada_berlin_hourly AS berlin_row
          ON berlin_row.source_month_utc = p_source_month_utc
         AND berlin_row.valid_time_utc = expected_hour.valid_time_utc
         AND berlin_row.geography_version = p_geography_version
         AND berlin_row.source_grid_id = p_source_grid_id
    )
    SELECT
        totals.files = 3
            AND totals.plrs > 0
            AND totals.plr_rows = totals.hours * totals.plrs
            AND totals.berlin_rows = totals.hours
            AND hourly_quality.incomplete_plr_hours = 0
            AND hourly_quality.missing_berlin_hours = 0,
        totals.files,
        totals.hours,
        totals.plrs,
        totals.plr_rows,
        totals.berlin_rows,
        hourly_quality.incomplete_plr_hours,
        hourly_quality.missing_berlin_hours
    FROM totals
    CROSS JOIN hourly_quality;
$$;

COMMIT;
