-- Germany layer, forecast: grid subset, cell-Gemeinde bridge, forecast facts,
-- roll-up views, checks 4-7 and retention. Additive and idempotent: only
-- de_-prefixed objects; Berlin objects are read, never written.

-- ---------------------------------------------------------------------------
-- Germany grid subset: ICON cells intersecting any Gemeinde, in EPSG:3035,
-- transformed from the Berlin pipeline's cell polygons (EPSG:25833,
-- read-only). Area weights are computed in EPSG:3035 (ADR 014).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS normalized.de_icon_cell (
    geography_version text NOT NULL,
    source_grid_id text NOT NULL,
    cell_index integer NOT NULL,
    geometry public.geometry(Polygon, 3035) NOT NULL,
    cell_area_m2 double precision NOT NULL CHECK (cell_area_m2 > 0),
    PRIMARY KEY (geography_version, source_grid_id, cell_index)
);

CREATE INDEX IF NOT EXISTS de_icon_cell_geometry_idx
    ON normalized.de_icon_cell USING gist (geometry);

CREATE TABLE IF NOT EXISTS normalized.de_icon_cell_gemeinde_bridge (
    geography_version text NOT NULL,
    source_grid_id text NOT NULL,
    cell_index integer NOT NULL,
    ars text NOT NULL,
    intersection_area_m2 double precision NOT NULL
        CHECK (intersection_area_m2 > 0),
    gemeinde_area_m2 double precision NOT NULL CHECK (gemeinde_area_m2 > 0),
    fraction_of_gemeinde double precision NOT NULL
        CHECK (fraction_of_gemeinde > 0 AND fraction_of_gemeinde <= 1.000001),
    PRIMARY KEY (geography_version, source_grid_id, ars, cell_index),
    FOREIGN KEY (geography_version, ars)
        REFERENCES normalized.de_gemeinde (geography_version, ars),
    FOREIGN KEY (geography_version, source_grid_id, cell_index)
        REFERENCES normalized.de_icon_cell (
            geography_version, source_grid_id, cell_index
        )
);

-- Supports the foreign key to de_icon_cell: without it, deleting the cells of
-- a geography version (refresh_de_icon_bridge) scans the bridge once per cell.
CREATE INDEX IF NOT EXISTS de_icon_cell_gemeinde_bridge_cell_idx
    ON normalized.de_icon_cell_gemeinde_bridge (
        geography_version, source_grid_id, cell_index
    );

-- ---------------------------------------------------------------------------
-- Forecast facts. Retained for the newest complete run and the complete run
-- before it (plus a newer run while it is still loading).
-- ---------------------------------------------------------------------------
-- One row per run: the geography version and grid the cell values belong
-- to. Cell rows reference it, so they need not repeat these text keys.
CREATE TABLE IF NOT EXISTS normalized.de_forecast_run (
    run_time_utc timestamp with time zone PRIMARY KEY,
    geography_version text NOT NULL,
    source_grid_id text NOT NULL
);

-- Per-cell values (decision B, slimmed 2026-11-09): run, lead hour and cell
-- index only; lead_time label and valid time are derived (valid = run +
-- lead_hour). double precision is kept: float4 would save only ~10 % more.
CREATE TABLE IF NOT EXISTS normalized.de_icon_cell_value (
    run_time_utc timestamp with time zone NOT NULL
        REFERENCES normalized.de_forecast_run (run_time_utc),
    lead_hour smallint NOT NULL CHECK (lead_hour BETWEEN 0 AND 24),
    cell_index integer NOT NULL,
    temperature_c double precision,
    apparent_temperature_shade_c double precision,
    PRIMARY KEY (run_time_utc, lead_hour, cell_index)
);

CREATE TABLE IF NOT EXISTS normalized.de_gemeinde_forecast (
    run_time_utc timestamp with time zone NOT NULL,
    lead_time text NOT NULL,
    valid_time_utc timestamp with time zone NOT NULL,
    geography_version text NOT NULL,
    source_grid_id text NOT NULL,
    ars text NOT NULL,
    temperature_c double precision,
    apparent_temperature_shade_c double precision,
    PRIMARY KEY (run_time_utc, lead_time, geography_version, ars),
    FOREIGN KEY (geography_version, ars)
        REFERENCES normalized.de_gemeinde (geography_version, ars)
);

-- ---------------------------------------------------------------------------
-- Refresh: Germany cells and bridge (static per geography and grid version)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.refresh_de_icon_bridge(
    p_geography_version text,
    p_source_grid_id text
) RETURNS TABLE(
    cell_count bigint,
    bridge_row_count bigint,
    gemeinde_count bigint
)
    LANGUAGE plpgsql
    AS $$
BEGIN
    DELETE FROM normalized.de_icon_cell_gemeinde_bridge AS b
    WHERE b.geography_version = p_geography_version
      AND b.source_grid_id = p_source_grid_id;
    DELETE FROM normalized.de_icon_cell AS c
    WHERE c.geography_version = p_geography_version
      AND c.source_grid_id = p_source_grid_id;

    -- Germany cells: the Berlin pipeline's ICON cell polygons (read-only,
    -- EPSG:25833) transformed to EPSG:3035, kept where they intersect a Gemeinde.
    INSERT INTO normalized.de_icon_cell
    WITH cells AS (
        SELECT
            icon.cell_index,
            public.ST_Transform(icon.geometry, 3035) AS geometry
        FROM normalized.icon_cell AS icon
        WHERE icon.source_grid_id = p_source_grid_id
    )
    SELECT
        p_geography_version,
        p_source_grid_id,
        cells.cell_index,
        public.ST_Normalize(cells.geometry)::public.geometry(Polygon, 3035),
        public.ST_Area(cells.geometry)
    FROM cells
    WHERE EXISTS (
        SELECT 1
        FROM normalized.de_gemeinde AS g
        WHERE g.geography_version = p_geography_version
          AND public.ST_Intersects(g.geometry, cells.geometry)
    );

    INSERT INTO normalized.de_icon_cell_gemeinde_bridge
    SELECT
        p_geography_version,
        p_source_grid_id,
        pieces.cell_index,
        pieces.ars,
        pieces.intersection_area_m2,
        pieces.gemeinde_area_m2,
        pieces.intersection_area_m2 / pieces.gemeinde_area_m2
    FROM (
        SELECT
            c.cell_index,
            g.ars,
            g.area_m2 AS gemeinde_area_m2,
            public.ST_Area(public.ST_Intersection(g.geometry, c.geometry))
                AS intersection_area_m2
        FROM normalized.de_icon_cell AS c
        JOIN normalized.de_gemeinde AS g
          ON g.geography_version = c.geography_version
         AND public.ST_Intersects(g.geometry, c.geometry)
        WHERE c.geography_version = p_geography_version
          AND c.source_grid_id = p_source_grid_id
    ) AS pieces
    WHERE pieces.intersection_area_m2 > 0;

    RETURN QUERY
    SELECT
        (SELECT COUNT(*) FROM normalized.de_icon_cell AS c
         WHERE c.geography_version = p_geography_version
           AND c.source_grid_id = p_source_grid_id),
        (SELECT COUNT(*) FROM normalized.de_icon_cell_gemeinde_bridge AS b
         WHERE b.geography_version = p_geography_version
           AND b.source_grid_id = p_source_grid_id),
        (SELECT COUNT(DISTINCT b.ars) FROM normalized.de_icon_cell_gemeinde_bridge AS b
         WHERE b.geography_version = p_geography_version
           AND b.source_grid_id = p_source_grid_id);
END;
$$;

-- ---------------------------------------------------------------------------
-- SQL aggregation of one partition. Input: pg_temp.de_cell_stage with the
-- four raw indicators of all Germany cells, staged by the caller. Units and
-- formulas are those of normalized.refresh_icon_d2_ruc_weather (Berlin):
-- K -> °C, wind speed from U/V, normalized.calculate_apparent_temperature_shade_c.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.refresh_de_forecast_partition(
    p_run_time_utc timestamp with time zone,
    p_lead_time text,
    p_valid_time_utc timestamp with time zone,
    p_geography_version text,
    p_source_grid_id text
) RETURNS TABLE(
    cell_row_count bigint,
    gemeinde_row_count bigint
)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_cells bigint;
    v_gemeinden bigint;
    v_lead_hour smallint := (
        EXTRACT(EPOCH FROM (p_valid_time_utc - p_run_time_utc)) / 3600
    )::smallint;
    v_run normalized.de_forecast_run%ROWTYPE;
BEGIN
    IF p_lead_time <> format('PT%sH00M', lpad(v_lead_hour::text, 3, '0')) THEN
        RAISE EXCEPTION 'lead_time % does not match valid - run = % h',
            p_lead_time, v_lead_hour;
    END IF;
    INSERT INTO normalized.de_forecast_run
    VALUES (p_run_time_utc, p_geography_version, p_source_grid_id)
    ON CONFLICT (run_time_utc) DO NOTHING;
    SELECT * INTO v_run FROM normalized.de_forecast_run AS r
    WHERE r.run_time_utc = p_run_time_utc;
    IF v_run.geography_version <> p_geography_version
       OR v_run.source_grid_id <> p_source_grid_id THEN
        RAISE EXCEPTION 'run % is registered for % / %, not % / %',
            p_run_time_utc, v_run.geography_version, v_run.source_grid_id,
            p_geography_version, p_source_grid_id;
    END IF;

    DELETE FROM normalized.de_icon_cell_value AS w
    WHERE w.run_time_utc = p_run_time_utc AND w.lead_hour = v_lead_hour;
    DELETE FROM normalized.de_gemeinde_forecast AS f
    WHERE f.run_time_utc = p_run_time_utc AND f.lead_time = p_lead_time
      AND f.geography_version = p_geography_version;

    INSERT INTO normalized.de_icon_cell_value
    SELECT
        p_run_time_utc,
        v_lead_hour,
        physical.cell_index,
        physical.temperature_c,
        normalized.calculate_apparent_temperature_shade_c(
            physical.temperature_c,
            physical.relative_humidity_percent,
            physical.wind_speed_10m_ms
        )
    FROM (
        SELECT
            stage.cell_index,
            stage.temperature_k - 273.15 AS temperature_c,
            stage.relative_humidity_percent,
            SQRT(POWER(stage.wind_u_10m_ms, 2) + POWER(stage.wind_v_10m_ms, 2))
                AS wind_speed_10m_ms
        FROM pg_temp.de_cell_stage AS stage
    ) AS physical;
    GET DIAGNOSTICS v_cells = ROW_COUNT;

    INSERT INTO normalized.de_gemeinde_forecast
    SELECT
        p_run_time_utc,
        p_lead_time,
        p_valid_time_utc,
        p_geography_version,
        p_source_grid_id,
        b.ars,
        SUM(w.temperature_c * b.fraction_of_gemeinde)
            / SUM(b.fraction_of_gemeinde),
        SUM(w.apparent_temperature_shade_c * b.fraction_of_gemeinde)
            / SUM(b.fraction_of_gemeinde)
    FROM normalized.de_icon_cell_gemeinde_bridge AS b
    JOIN normalized.de_icon_cell_value AS w
      ON w.run_time_utc = p_run_time_utc
     AND w.lead_hour = v_lead_hour
     AND w.cell_index = b.cell_index
    WHERE b.geography_version = p_geography_version
      AND b.source_grid_id = p_source_grid_id
    GROUP BY b.ars;
    GET DIAGNOSTICS v_gemeinden = ROW_COUNT;

    RETURN QUERY SELECT v_cells, v_gemeinden;
END;
$$;

-- ---------------------------------------------------------------------------
-- Completeness and the "current" run: newest run in which every Gemeinde has
-- all 25 lead times (same switching rule as Berlin's 25-hour views).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW normalized.de_forecast_run_completeness AS
SELECT
    f.geography_version,
    f.run_time_utc,
    COUNT(DISTINCT f.lead_time) AS lead_time_count,
    COUNT(*) AS gemeinde_lead_row_count,
    (SELECT COUNT(*) FROM normalized.de_gemeinde AS g
     WHERE g.geography_version = f.geography_version) * 25 AS expected_row_count,
    COUNT(*) = (SELECT COUNT(*) FROM normalized.de_gemeinde AS g
                WHERE g.geography_version = f.geography_version) * 25
        AND COUNT(DISTINCT f.lead_time) = 25 AS is_complete
FROM normalized.de_gemeinde_forecast AS f
GROUP BY f.geography_version, f.run_time_utc;

CREATE OR REPLACE VIEW analytical.de_current_run AS
SELECT geography_version, run_time_utc
FROM normalized.de_forecast_run_completeness
WHERE is_complete
ORDER BY run_time_utc DESC
LIMIT 1;

-- ---------------------------------------------------------------------------
-- Serving views (D5: measurements only, no thresholds or classes).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytical.de_current_gemeinde_forecast_25h AS
SELECT
    g.ars,
    g.ags,
    g.gemeinde_name,
    g.kreis_key,
    k.kreis_name,
    g.land_key,
    l.land_name,
    f.run_time_utc,
    f.lead_time,
    (EXTRACT(EPOCH FROM (f.valid_time_utc - f.run_time_utc)) / 3600)::integer
        AS lead_hour,
    f.valid_time_utc,
    (f.valid_time_utc AT TIME ZONE 'Europe/Berlin') AS valid_time_berlin,
    f.temperature_c,
    f.apparent_temperature_shade_c,
    p.population_total,
    p.population_status
FROM analytical.de_current_run AS cur
JOIN normalized.de_gemeinde_forecast AS f
  ON f.run_time_utc = cur.run_time_utc
 AND f.geography_version = cur.geography_version
JOIN normalized.de_gemeinde AS g
  ON g.geography_version = f.geography_version AND g.ars = f.ars
JOIN normalized.de_kreis AS k
  ON k.geography_version = g.geography_version AND k.kreis_key = g.kreis_key
JOIN normalized.de_land AS l
  ON l.geography_version = g.geography_version AND l.land_key = g.land_key
LEFT JOIN normalized.de_gemeinde_population AS p
  ON p.geography_version = g.geography_version AND p.ars = g.ars;

-- Roll-up inputs: Gemeinde area weights (Gemeinden partition Kreise in
-- VG250, so this equals direct area weighting) and total-population weights
-- (NULL population contributes no weight; it is never treated as 0 residents).
CREATE OR REPLACE VIEW normalized.de_current_rollup_input AS
SELECT
    f.*,
    g.area_m2,
    COALESCE(f.population_total, 0) AS population_weight
FROM analytical.de_current_gemeinde_forecast_25h AS f
JOIN normalized.de_gemeinde AS g
  ON g.ars = f.ars
 AND g.geography_version = (SELECT geography_version FROM analytical.de_current_run);

CREATE OR REPLACE VIEW analytical.de_current_kreis_forecast_25h AS
SELECT
    i.kreis_key,
    MIN(i.kreis_name) AS kreis_name,
    MIN(i.land_key) AS land_key,
    MIN(i.land_name) AS land_name,
    i.run_time_utc,
    i.lead_time,
    i.lead_hour,
    i.valid_time_utc,
    i.valid_time_berlin,
    SUM(i.temperature_c * i.area_m2) / SUM(i.area_m2)
        AS temperature_area_weighted_c,
    SUM(i.temperature_c * i.population_weight)
        / NULLIF(SUM(i.population_weight), 0)
        AS temperature_population_weighted_c,
    SUM(i.apparent_temperature_shade_c * i.area_m2) / SUM(i.area_m2)
        AS apparent_temperature_shade_area_weighted_c,
    SUM(i.apparent_temperature_shade_c * i.population_weight)
        / NULLIF(SUM(i.population_weight), 0)
        AS apparent_temperature_shade_population_weighted_c,
    SUM(i.area_m2) AS area_m2,
    SUM(i.population_total) AS population_total,
    COUNT(*) AS gemeinde_count
FROM normalized.de_current_rollup_input AS i
GROUP BY i.kreis_key, i.run_time_utc, i.lead_time, i.lead_hour,
         i.valid_time_utc, i.valid_time_berlin;

CREATE OR REPLACE VIEW analytical.de_current_land_forecast_25h AS
SELECT
    i.land_key,
    MIN(i.land_name) AS land_name,
    i.run_time_utc,
    i.lead_time,
    i.lead_hour,
    i.valid_time_utc,
    i.valid_time_berlin,
    SUM(i.temperature_c * i.area_m2) / SUM(i.area_m2)
        AS temperature_area_weighted_c,
    SUM(i.temperature_c * i.population_weight)
        / NULLIF(SUM(i.population_weight), 0)
        AS temperature_population_weighted_c,
    SUM(i.apparent_temperature_shade_c * i.area_m2) / SUM(i.area_m2)
        AS apparent_temperature_shade_area_weighted_c,
    SUM(i.apparent_temperature_shade_c * i.population_weight)
        / NULLIF(SUM(i.population_weight), 0)
        AS apparent_temperature_shade_population_weighted_c,
    SUM(i.area_m2) AS area_m2,
    SUM(i.population_total) AS population_total,
    COUNT(*) AS gemeinde_count
FROM normalized.de_current_rollup_input AS i
GROUP BY i.land_key, i.run_time_utc, i.lead_time, i.lead_hour,
         i.valid_time_utc, i.valid_time_berlin;

CREATE OR REPLACE VIEW analytical.de_current_bund_forecast_25h AS
SELECT
    i.run_time_utc,
    i.lead_time,
    i.lead_hour,
    i.valid_time_utc,
    i.valid_time_berlin,
    SUM(i.temperature_c * i.area_m2) / SUM(i.area_m2)
        AS temperature_area_weighted_c,
    SUM(i.temperature_c * i.population_weight)
        / NULLIF(SUM(i.population_weight), 0)
        AS temperature_population_weighted_c,
    SUM(i.apparent_temperature_shade_c * i.area_m2) / SUM(i.area_m2)
        AS apparent_temperature_shade_area_weighted_c,
    SUM(i.apparent_temperature_shade_c * i.population_weight)
        / NULLIF(SUM(i.population_weight), 0)
        AS apparent_temperature_shade_population_weighted_c,
    SUM(i.area_m2) AS area_m2,
    SUM(i.population_total) AS population_total,
    COUNT(*) AS gemeinde_count
FROM normalized.de_current_rollup_input AS i
GROUP BY i.run_time_utc, i.lead_time, i.lead_hour,
         i.valid_time_utc, i.valid_time_berlin;

CREATE OR REPLACE VIEW analytical.de_current_gemeinde_summary AS
WITH ranked AS (
    SELECT
        f.*,
        ROW_NUMBER() OVER (
            PARTITION BY f.ars
            ORDER BY f.temperature_c DESC, f.valid_time_utc
        ) AS temperature_rank,
        ROW_NUMBER() OVER (
            PARTITION BY f.ars
            ORDER BY f.apparent_temperature_shade_c DESC, f.valid_time_utc
        ) AS apparent_rank
    FROM analytical.de_current_gemeinde_forecast_25h AS f
)
SELECT
    t.ars,
    t.ags,
    t.gemeinde_name,
    t.kreis_key,
    t.kreis_name,
    t.land_key,
    t.land_name,
    t.run_time_utc,
    (t.run_time_utc AT TIME ZONE 'Europe/Berlin') AS run_time_berlin,
    t.temperature_c AS max_temperature_c,
    t.valid_time_berlin AS max_temperature_at_berlin,
    a.apparent_temperature_shade_c AS max_apparent_temperature_shade_c,
    a.valid_time_berlin AS max_apparent_temperature_shade_at_berlin,
    t.population_total,
    t.population_status
FROM ranked AS t
JOIN ranked AS a ON a.ars = t.ars AND a.apparent_rank = 1
WHERE t.temperature_rank = 1;

-- ---------------------------------------------------------------------------
-- Kreis, Land and Bund summaries: 24-hour maximum of each served measure, the
-- Berlin time and the lead hour of its first occurrence (same rule as the
-- Gemeinde summary: highest value, earliest time on a tie; NULL when the
-- measure is NULL in every lead hour).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytical.de_current_kreis_summary AS
WITH ranked AS (
    SELECT
        k.*,
        ROW_NUMBER() OVER (PARTITION BY k.kreis_key
            ORDER BY k.temperature_area_weighted_c DESC NULLS LAST, k.valid_time_utc) AS t_area_rank,
        ROW_NUMBER() OVER (PARTITION BY k.kreis_key
            ORDER BY k.temperature_population_weighted_c DESC NULLS LAST, k.valid_time_utc) AS t_pop_rank,
        ROW_NUMBER() OVER (PARTITION BY k.kreis_key
            ORDER BY k.apparent_temperature_shade_area_weighted_c DESC NULLS LAST, k.valid_time_utc) AS a_area_rank,
        ROW_NUMBER() OVER (PARTITION BY k.kreis_key
            ORDER BY k.apparent_temperature_shade_population_weighted_c DESC NULLS LAST, k.valid_time_utc) AS a_pop_rank
    FROM analytical.de_current_kreis_forecast_25h AS k
)
SELECT
    r.kreis_key,
    MIN(r.kreis_name) AS kreis_name,
    MIN(r.land_key) AS land_key,
    MIN(r.land_name) AS land_name,
    MIN(r.run_time_utc) AS run_time_utc,
    MAX(r.temperature_area_weighted_c) FILTER (WHERE r.t_area_rank = 1)
        AS max_temperature_area_weighted_c,
    MAX(r.valid_time_berlin) FILTER (
        WHERE r.t_area_rank = 1 AND r.temperature_area_weighted_c IS NOT NULL)
        AS max_temperature_area_weighted_at_berlin,
    MAX(r.lead_hour) FILTER (
        WHERE r.t_area_rank = 1 AND r.temperature_area_weighted_c IS NOT NULL)
        AS max_temperature_area_weighted_lead_hour,
    MAX(r.temperature_population_weighted_c) FILTER (WHERE r.t_pop_rank = 1)
        AS max_temperature_population_weighted_c,
    MAX(r.valid_time_berlin) FILTER (
        WHERE r.t_pop_rank = 1 AND r.temperature_population_weighted_c IS NOT NULL)
        AS max_temperature_population_weighted_at_berlin,
    MAX(r.lead_hour) FILTER (
        WHERE r.t_pop_rank = 1 AND r.temperature_population_weighted_c IS NOT NULL)
        AS max_temperature_population_weighted_lead_hour,
    MAX(r.apparent_temperature_shade_area_weighted_c) FILTER (WHERE r.a_area_rank = 1)
        AS max_apparent_temperature_shade_area_weighted_c,
    MAX(r.valid_time_berlin) FILTER (
        WHERE r.a_area_rank = 1 AND r.apparent_temperature_shade_area_weighted_c IS NOT NULL)
        AS max_apparent_temperature_shade_area_weighted_at_berlin,
    MAX(r.lead_hour) FILTER (
        WHERE r.a_area_rank = 1 AND r.apparent_temperature_shade_area_weighted_c IS NOT NULL)
        AS max_apparent_temperature_shade_area_weighted_lead_hour,
    MAX(r.apparent_temperature_shade_population_weighted_c) FILTER (WHERE r.a_pop_rank = 1)
        AS max_apparent_temperature_shade_population_weighted_c,
    MAX(r.valid_time_berlin) FILTER (
        WHERE r.a_pop_rank = 1 AND r.apparent_temperature_shade_population_weighted_c IS NOT NULL)
        AS max_apparent_temperature_shade_population_weighted_at_berlin,
    MAX(r.lead_hour) FILTER (
        WHERE r.a_pop_rank = 1 AND r.apparent_temperature_shade_population_weighted_c IS NOT NULL)
        AS max_apparent_temperature_shade_population_weighted_lead_hour,
    MIN(r.population_total) AS population_total,
    MIN(r.gemeinde_count) AS gemeinde_count,
    COUNT(*) AS lead_time_count
FROM ranked AS r
GROUP BY r.kreis_key;

CREATE OR REPLACE VIEW analytical.de_current_land_summary AS
WITH ranked AS (
    SELECT
        k.*,
        ROW_NUMBER() OVER (PARTITION BY k.land_key
            ORDER BY k.temperature_area_weighted_c DESC NULLS LAST, k.valid_time_utc) AS t_area_rank,
        ROW_NUMBER() OVER (PARTITION BY k.land_key
            ORDER BY k.temperature_population_weighted_c DESC NULLS LAST, k.valid_time_utc) AS t_pop_rank,
        ROW_NUMBER() OVER (PARTITION BY k.land_key
            ORDER BY k.apparent_temperature_shade_area_weighted_c DESC NULLS LAST, k.valid_time_utc) AS a_area_rank,
        ROW_NUMBER() OVER (PARTITION BY k.land_key
            ORDER BY k.apparent_temperature_shade_population_weighted_c DESC NULLS LAST, k.valid_time_utc) AS a_pop_rank
    FROM analytical.de_current_land_forecast_25h AS k
)
SELECT
    r.land_key,
    MIN(r.land_name) AS land_name,
    MIN(r.run_time_utc) AS run_time_utc,
    MAX(r.temperature_area_weighted_c) FILTER (WHERE r.t_area_rank = 1)
        AS max_temperature_area_weighted_c,
    MAX(r.valid_time_berlin) FILTER (
        WHERE r.t_area_rank = 1 AND r.temperature_area_weighted_c IS NOT NULL)
        AS max_temperature_area_weighted_at_berlin,
    MAX(r.lead_hour) FILTER (
        WHERE r.t_area_rank = 1 AND r.temperature_area_weighted_c IS NOT NULL)
        AS max_temperature_area_weighted_lead_hour,
    MAX(r.temperature_population_weighted_c) FILTER (WHERE r.t_pop_rank = 1)
        AS max_temperature_population_weighted_c,
    MAX(r.valid_time_berlin) FILTER (
        WHERE r.t_pop_rank = 1 AND r.temperature_population_weighted_c IS NOT NULL)
        AS max_temperature_population_weighted_at_berlin,
    MAX(r.lead_hour) FILTER (
        WHERE r.t_pop_rank = 1 AND r.temperature_population_weighted_c IS NOT NULL)
        AS max_temperature_population_weighted_lead_hour,
    MAX(r.apparent_temperature_shade_area_weighted_c) FILTER (WHERE r.a_area_rank = 1)
        AS max_apparent_temperature_shade_area_weighted_c,
    MAX(r.valid_time_berlin) FILTER (
        WHERE r.a_area_rank = 1 AND r.apparent_temperature_shade_area_weighted_c IS NOT NULL)
        AS max_apparent_temperature_shade_area_weighted_at_berlin,
    MAX(r.lead_hour) FILTER (
        WHERE r.a_area_rank = 1 AND r.apparent_temperature_shade_area_weighted_c IS NOT NULL)
        AS max_apparent_temperature_shade_area_weighted_lead_hour,
    MAX(r.apparent_temperature_shade_population_weighted_c) FILTER (WHERE r.a_pop_rank = 1)
        AS max_apparent_temperature_shade_population_weighted_c,
    MAX(r.valid_time_berlin) FILTER (
        WHERE r.a_pop_rank = 1 AND r.apparent_temperature_shade_population_weighted_c IS NOT NULL)
        AS max_apparent_temperature_shade_population_weighted_at_berlin,
    MAX(r.lead_hour) FILTER (
        WHERE r.a_pop_rank = 1 AND r.apparent_temperature_shade_population_weighted_c IS NOT NULL)
        AS max_apparent_temperature_shade_population_weighted_lead_hour,
    MIN(r.population_total) AS population_total,
    MIN(r.gemeinde_count) AS gemeinde_count,
    COUNT(*) AS lead_time_count
FROM ranked AS r
GROUP BY r.land_key;

CREATE OR REPLACE VIEW analytical.de_current_bund_summary AS
WITH ranked AS (
    SELECT
        k.*,
        ROW_NUMBER() OVER (PARTITION BY k.run_time_utc
            ORDER BY k.temperature_area_weighted_c DESC NULLS LAST, k.valid_time_utc) AS t_area_rank,
        ROW_NUMBER() OVER (PARTITION BY k.run_time_utc
            ORDER BY k.temperature_population_weighted_c DESC NULLS LAST, k.valid_time_utc) AS t_pop_rank,
        ROW_NUMBER() OVER (PARTITION BY k.run_time_utc
            ORDER BY k.apparent_temperature_shade_area_weighted_c DESC NULLS LAST, k.valid_time_utc) AS a_area_rank,
        ROW_NUMBER() OVER (PARTITION BY k.run_time_utc
            ORDER BY k.apparent_temperature_shade_population_weighted_c DESC NULLS LAST, k.valid_time_utc) AS a_pop_rank
    FROM analytical.de_current_bund_forecast_25h AS k
)
SELECT
    r.run_time_utc,
    MAX(r.temperature_area_weighted_c) FILTER (WHERE r.t_area_rank = 1)
        AS max_temperature_area_weighted_c,
    MAX(r.valid_time_berlin) FILTER (
        WHERE r.t_area_rank = 1 AND r.temperature_area_weighted_c IS NOT NULL)
        AS max_temperature_area_weighted_at_berlin,
    MAX(r.lead_hour) FILTER (
        WHERE r.t_area_rank = 1 AND r.temperature_area_weighted_c IS NOT NULL)
        AS max_temperature_area_weighted_lead_hour,
    MAX(r.temperature_population_weighted_c) FILTER (WHERE r.t_pop_rank = 1)
        AS max_temperature_population_weighted_c,
    MAX(r.valid_time_berlin) FILTER (
        WHERE r.t_pop_rank = 1 AND r.temperature_population_weighted_c IS NOT NULL)
        AS max_temperature_population_weighted_at_berlin,
    MAX(r.lead_hour) FILTER (
        WHERE r.t_pop_rank = 1 AND r.temperature_population_weighted_c IS NOT NULL)
        AS max_temperature_population_weighted_lead_hour,
    MAX(r.apparent_temperature_shade_area_weighted_c) FILTER (WHERE r.a_area_rank = 1)
        AS max_apparent_temperature_shade_area_weighted_c,
    MAX(r.valid_time_berlin) FILTER (
        WHERE r.a_area_rank = 1 AND r.apparent_temperature_shade_area_weighted_c IS NOT NULL)
        AS max_apparent_temperature_shade_area_weighted_at_berlin,
    MAX(r.lead_hour) FILTER (
        WHERE r.a_area_rank = 1 AND r.apparent_temperature_shade_area_weighted_c IS NOT NULL)
        AS max_apparent_temperature_shade_area_weighted_lead_hour,
    MAX(r.apparent_temperature_shade_population_weighted_c) FILTER (WHERE r.a_pop_rank = 1)
        AS max_apparent_temperature_shade_population_weighted_c,
    MAX(r.valid_time_berlin) FILTER (
        WHERE r.a_pop_rank = 1 AND r.apparent_temperature_shade_population_weighted_c IS NOT NULL)
        AS max_apparent_temperature_shade_population_weighted_at_berlin,
    MAX(r.lead_hour) FILTER (
        WHERE r.a_pop_rank = 1 AND r.apparent_temperature_shade_population_weighted_c IS NOT NULL)
        AS max_apparent_temperature_shade_population_weighted_lead_hour,
    MIN(r.population_total) AS population_total,
    MIN(r.gemeinde_count) AS gemeinde_count,
    COUNT(*) AS lead_time_count
FROM ranked AS r
GROUP BY r.run_time_utc;

-- ---------------------------------------------------------------------------
-- Check 4: bridge coverage per Gemeinde (Σ intersection / Gemeinde area).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.check_de_bridge_coverage(
    p_geography_version text,
    p_source_grid_id text,
    p_min_coverage double precision DEFAULT 0.999
) RETURNS TABLE(
    ars text,
    gemeinde_name text,
    coverage double precision
)
    LANGUAGE sql STABLE
    AS $$
    SELECT g.ars, g.gemeinde_name, COALESCE(SUM(b.fraction_of_gemeinde), 0)
    FROM normalized.de_gemeinde AS g
    LEFT JOIN normalized.de_icon_cell_gemeinde_bridge AS b
      ON b.geography_version = g.geography_version
     AND b.ars = g.ars
     AND b.source_grid_id = p_source_grid_id
    WHERE g.geography_version = p_geography_version
    GROUP BY g.ars, g.gemeinde_name
    HAVING COALESCE(SUM(b.fraction_of_gemeinde), 0) < p_min_coverage
        OR SUM(b.fraction_of_gemeinde) > 1.000001
    ORDER BY 3
$$;

-- ---------------------------------------------------------------------------
-- Check 7 support: Berlin from the Germany bridge vs. the 542 PLRs.
-- PLR values are recomputed from the same Germany cell values through
-- Berlin's existing bridge (read-only); PLRs are rolled up by PLR area.
-- Where Berlin's own stored output exists for the run, it is compared too.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.de_berlin_cross_layer(
    p_run_time_utc timestamp with time zone,
    p_geography_version text,
    p_source_grid_id text
) RETURNS TABLE(
    lead_time text,
    germany_temperature_c double precision,
    plr_rollup_temperature_c double precision,
    stored_plr_rollup_temperature_c double precision,
    germany_apparent_c double precision,
    plr_rollup_apparent_c double precision,
    stored_plr_rollup_apparent_c double precision,
    plr_count bigint,
    stored_plr_count bigint
)
    LANGUAGE sql STABLE
    AS $$
    WITH plr AS (
        SELECT
            format('PT%sH00M', lpad(w.lead_hour::text, 3, '0')) AS lead_time,
            b.plr_id,
            MIN(b.plr_area_m2) AS plr_area_m2,
            SUM(w.temperature_c * b.fraction_of_plr) / SUM(b.fraction_of_plr)
                AS temperature_c,
            SUM(w.apparent_temperature_shade_c * b.fraction_of_plr)
                / SUM(b.fraction_of_plr) AS apparent_c
        FROM normalized.icon_plr_area_bridge AS b
        JOIN normalized.de_forecast_run AS run
          ON run.run_time_utc = p_run_time_utc
         AND run.geography_version = p_geography_version
         AND run.source_grid_id = b.source_grid_id
        JOIN normalized.de_icon_cell_value AS w
          ON w.run_time_utc = run.run_time_utc
         AND w.cell_index = b.cell_index
        WHERE b.source_grid_id = p_source_grid_id
        GROUP BY w.lead_hour, b.plr_id
    ),
    plr_rollup AS (
        SELECT plr.lead_time,
               SUM(plr.temperature_c * plr.plr_area_m2) / SUM(plr.plr_area_m2) AS t,
               SUM(plr.apparent_c * plr.plr_area_m2) / SUM(plr.plr_area_m2) AS a,
               COUNT(*) AS n
        FROM plr
        GROUP BY plr.lead_time
    ),
    stored AS (
        SELECT pw.lead_time,
               SUM(pw.temperature_c * pa.plr_area_m2) / SUM(pa.plr_area_m2) AS t,
               SUM(pw.apparent_temperature_shade_c * pa.plr_area_m2)
                   / SUM(pa.plr_area_m2) AS a,
               COUNT(*) AS n
        FROM analytical.plr_weather AS pw
        JOIN (SELECT DISTINCT b.plr_id, b.plr_area_m2
              FROM normalized.icon_plr_area_bridge AS b
              WHERE b.source_grid_id = p_source_grid_id) AS pa
          ON pa.plr_id = pw.plr_id
        WHERE pw.run_time_utc = p_run_time_utc
        GROUP BY pw.lead_time
    )
    SELECT f.lead_time, f.temperature_c, r.t, s.t,
           f.apparent_temperature_shade_c, r.a, s.a,
           COALESCE(r.n, 0), COALESCE(s.n, 0)
    FROM normalized.de_gemeinde_forecast AS f
    LEFT JOIN plr_rollup AS r ON r.lead_time = f.lead_time
    LEFT JOIN stored AS s ON s.lead_time = f.lead_time
    WHERE f.run_time_utc = p_run_time_utc
      AND f.geography_version = p_geography_version
      AND f.ars = '110000000000'
    ORDER BY f.lead_time
$$;

-- ---------------------------------------------------------------------------
-- Retention (open decision 3): keep the newest complete run, the complete
-- run before it, and any newer run that is still loading. Database rows
-- only; GRIB files are never deleted here.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.prune_de_forecast(
    p_geography_version text,
    p_keep_complete_runs integer DEFAULT 2
) RETURNS TABLE(
    oldest_kept_run_utc timestamp with time zone,
    deleted_cell_rows bigint,
    deleted_gemeinde_rows bigint
)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_cutoff timestamp with time zone;
    v_cells bigint := 0;
    v_gemeinden bigint := 0;
BEGIN
    SELECT MIN(kept.run_time_utc) INTO v_cutoff
    FROM (
        SELECT c.run_time_utc
        FROM normalized.de_forecast_run_completeness AS c
        WHERE c.geography_version = p_geography_version AND c.is_complete
        ORDER BY c.run_time_utc DESC
        LIMIT p_keep_complete_runs
    ) AS kept;

    -- Fewer complete runs than requested: keep everything.
    IF v_cutoff IS NULL OR (
        SELECT COUNT(*) FROM normalized.de_forecast_run_completeness AS c
        WHERE c.geography_version = p_geography_version AND c.is_complete
    ) < p_keep_complete_runs THEN
        RETURN QUERY SELECT v_cutoff, 0::bigint, 0::bigint;
        RETURN;
    END IF;

    DELETE FROM normalized.de_icon_cell_value AS w
    USING normalized.de_forecast_run AS r
    WHERE r.run_time_utc = w.run_time_utc
      AND r.geography_version = p_geography_version
      AND w.run_time_utc < v_cutoff;
    GET DIAGNOSTICS v_cells = ROW_COUNT;
    DELETE FROM normalized.de_forecast_run AS r
    WHERE r.geography_version = p_geography_version
      AND r.run_time_utc < v_cutoff;

    DELETE FROM normalized.de_gemeinde_forecast AS f
    WHERE f.geography_version = p_geography_version
      AND f.run_time_utc < v_cutoff;
    GET DIAGNOSTICS v_gemeinden = ROW_COUNT;

    RETURN QUERY SELECT v_cutoff, v_cells, v_gemeinden;
END;
$$;
