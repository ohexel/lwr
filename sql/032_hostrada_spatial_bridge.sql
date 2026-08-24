BEGIN;

CREATE OR REPLACE FUNCTION normalized.refresh_hostrada_cell_geometry(
    p_geography_version TEXT,
    p_source_grid_id TEXT
)
RETURNS TABLE (
    cell_row_count BIGINT,
    represented_plr_count BIGINT,
    candidate_cell_count BIGINT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_grid normalized.hostrada_grid%ROWTYPE;
    v_extent BOX3D;
    v_source_plr_count BIGINT;
    v_x_start INTEGER;
    v_x_stop INTEGER;
    v_y_start INTEGER;
    v_y_stop INTEGER;
BEGIN
    IF p_geography_version IS NULL
       OR BTRIM(p_geography_version) = '' THEN
        RAISE EXCEPTION 'p_geography_version must be non-empty';
    END IF;

    IF p_source_grid_id IS NULL
       OR BTRIM(p_source_grid_id) = '' THEN
        RAISE EXCEPTION 'p_source_grid_id must be non-empty';
    END IF;

    SELECT grid_row.*
    INTO v_grid
    FROM normalized.hostrada_grid AS grid_row
    WHERE grid_row.source_grid_id = p_source_grid_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'HOSTRADA grid % has not been registered',
            p_source_grid_id;
    END IF;

    SELECT
        COUNT(*)::BIGINT,
        ST_Extent(
            ST_Transform(plr_row.geometry, v_grid.source_srid)
        )::BOX3D
    INTO
        v_source_plr_count,
        v_extent
    FROM normalized.plr AS plr_row
    WHERE plr_row.geography_version = p_geography_version;

    IF v_source_plr_count = 0 OR v_extent IS NULL THEN
        RAISE EXCEPTION
            'No normalized PLRs found for geography_version=%',
            p_geography_version;
    END IF;

    IF ST_XMin(v_extent) < v_grid.x_origin_m - v_grid.x_spacing_m / 2.0
       OR ST_YMin(v_extent) < v_grid.y_origin_m - v_grid.y_spacing_m / 2.0
       OR ST_XMax(v_extent)
            > v_grid.x_origin_m
              + (v_grid.x_count - 1) * v_grid.x_spacing_m
              + v_grid.x_spacing_m / 2.0
       OR ST_YMax(v_extent)
            > v_grid.y_origin_m
              + (v_grid.y_count - 1) * v_grid.y_spacing_m
              + v_grid.y_spacing_m / 2.0 THEN
        RAISE EXCEPTION
            'PLR geography % is not fully covered by HOSTRADA grid %',
            p_geography_version,
            p_source_grid_id;
    END IF;

    v_x_start := GREATEST(
        0,
        CEIL(
            (
                ST_XMin(v_extent)
                - v_grid.x_origin_m
                - v_grid.x_spacing_m / 2.0
            ) / v_grid.x_spacing_m
        )::INTEGER
    );
    v_x_stop := LEAST(
        v_grid.x_count,
        FLOOR(
            (
                ST_XMax(v_extent)
                - v_grid.x_origin_m
                + v_grid.x_spacing_m / 2.0
            ) / v_grid.x_spacing_m
        )::INTEGER + 1
    );
    v_y_start := GREATEST(
        0,
        CEIL(
            (
                ST_YMin(v_extent)
                - v_grid.y_origin_m
                - v_grid.y_spacing_m / 2.0
            ) / v_grid.y_spacing_m
        )::INTEGER
    );
    v_y_stop := LEAST(
        v_grid.y_count,
        FLOOR(
            (
                ST_YMax(v_extent)
                - v_grid.y_origin_m
                + v_grid.y_spacing_m / 2.0
            ) / v_grid.y_spacing_m
        )::INTEGER + 1
    );

    IF v_x_start >= v_x_stop OR v_y_start >= v_y_stop THEN
        RAISE EXCEPTION
            'PLR geography % does not overlap HOSTRADA grid %',
            p_geography_version,
            p_source_grid_id;
    END IF;

    DELETE FROM normalized.hostrada_cell AS cell_row
    WHERE cell_row.geography_version = p_geography_version
      AND cell_row.source_grid_id = p_source_grid_id;

    INSERT INTO normalized.hostrada_cell (
        source_grid_id,
        geography_version,
        y_index,
        x_index,
        geometry,
        hostrada_cell_area_m2
    )
    WITH candidate_cells AS MATERIALIZED (
        SELECT
            y_grid.y_index,
            x_grid.x_index,
            ST_Transform(
                ST_MakeEnvelope(
                    v_grid.x_origin_m
                        + x_grid.x_index * v_grid.x_spacing_m
                        - v_grid.x_spacing_m / 2.0,
                    v_grid.y_origin_m
                        + y_grid.y_index * v_grid.y_spacing_m
                        - v_grid.y_spacing_m / 2.0,
                    v_grid.x_origin_m
                        + x_grid.x_index * v_grid.x_spacing_m
                        + v_grid.x_spacing_m / 2.0,
                    v_grid.y_origin_m
                        + y_grid.y_index * v_grid.y_spacing_m
                        + v_grid.y_spacing_m / 2.0,
                    v_grid.source_srid
                ),
                v_grid.target_srid
            )::geometry(Polygon, 25833) AS geometry
        FROM generate_series(v_x_start, v_x_stop - 1) AS x_grid(x_index)
        CROSS JOIN generate_series(v_y_start, v_y_stop - 1) AS y_grid(y_index)
    )
    SELECT
        p_source_grid_id,
        p_geography_version,
        candidate.y_index,
        candidate.x_index,
        candidate.geometry,
        ST_Area(candidate.geometry)::DOUBLE PRECISION
    FROM candidate_cells AS candidate
    WHERE EXISTS (
        SELECT 1
        FROM normalized.plr AS plr_row
        WHERE plr_row.geography_version = p_geography_version
          AND plr_row.geometry && candidate.geometry
          AND ST_Intersects(plr_row.geometry, candidate.geometry)
          AND ST_Area(
                ST_Intersection(plr_row.geometry, candidate.geometry)
              ) > 0
    );

    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT,
        (
            SELECT COUNT(DISTINCT plr_row.plr_id)::BIGINT
            FROM normalized.plr AS plr_row
            JOIN normalized.hostrada_cell AS cell_row
              ON cell_row.source_grid_id = p_source_grid_id
             AND cell_row.geography_version = p_geography_version
             AND plr_row.geometry && cell_row.geometry
             AND ST_Intersects(plr_row.geometry, cell_row.geometry)
             AND ST_Area(
                    ST_Intersection(plr_row.geometry, cell_row.geometry)
                 ) > 0
            WHERE plr_row.geography_version = p_geography_version
        ),
        ((v_x_stop - v_x_start) * (v_y_stop - v_y_start))::BIGINT
    FROM normalized.hostrada_cell AS cell_row
    WHERE cell_row.geography_version = p_geography_version
      AND cell_row.source_grid_id = p_source_grid_id;
END;
$$;


CREATE OR REPLACE FUNCTION normalized.refresh_hostrada_plr_area_bridge(
    p_geography_version TEXT,
    p_source_grid_id TEXT
)
RETURNS TABLE (
    bridge_row_count BIGINT,
    represented_plr_count BIGINT,
    represented_hostrada_cell_count BIGINT
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_geography_version IS NULL
       OR BTRIM(p_geography_version) = '' THEN
        RAISE EXCEPTION 'p_geography_version must be non-empty';
    END IF;

    IF p_source_grid_id IS NULL
       OR BTRIM(p_source_grid_id) = '' THEN
        RAISE EXCEPTION 'p_source_grid_id must be non-empty';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM normalized.plr AS plr_row
        WHERE plr_row.geography_version = p_geography_version
    ) THEN
        RAISE EXCEPTION
            'No normalized PLRs found for geography_version=%',
            p_geography_version;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM normalized.hostrada_cell AS cell_row
        WHERE cell_row.source_grid_id = p_source_grid_id
          AND cell_row.geography_version = p_geography_version
    ) THEN
        RAISE EXCEPTION
            'No HOSTRADA cells found for geography_version=% and grid=%',
            p_geography_version,
            p_source_grid_id;
    END IF;

    DELETE FROM normalized.hostrada_plr_area_bridge AS bridge_row
    WHERE bridge_row.geography_version = p_geography_version
      AND bridge_row.source_grid_id = p_source_grid_id;

    INSERT INTO normalized.hostrada_plr_area_bridge (
        plr_id,
        geography_version,
        source_grid_id,
        y_index,
        x_index,
        intersection_area_m2,
        plr_area_m2,
        hostrada_cell_area_m2,
        fraction_of_plr,
        fraction_of_hostrada_cell
    )
    WITH measured_intersections AS (
        SELECT
            plr_row.plr_id,
            plr_row.geography_version,
            cell_row.source_grid_id,
            cell_row.y_index,
            cell_row.x_index,
            ST_Area(plr_row.geometry)::DOUBLE PRECISION AS plr_area_m2,
            cell_row.hostrada_cell_area_m2,
            ST_Area(
                ST_Intersection(plr_row.geometry, cell_row.geometry)
            )::DOUBLE PRECISION AS intersection_area_m2
        FROM normalized.plr AS plr_row
        JOIN normalized.hostrada_cell AS cell_row
          ON cell_row.source_grid_id = p_source_grid_id
         AND cell_row.geography_version = p_geography_version
         AND plr_row.geometry && cell_row.geometry
         AND ST_Intersects(plr_row.geometry, cell_row.geometry)
        WHERE plr_row.geography_version = p_geography_version
    )
    SELECT
        measured.plr_id,
        measured.geography_version,
        measured.source_grid_id,
        measured.y_index,
        measured.x_index,
        measured.intersection_area_m2,
        measured.plr_area_m2,
        measured.hostrada_cell_area_m2,
        measured.intersection_area_m2 / measured.plr_area_m2,
        measured.intersection_area_m2 / measured.hostrada_cell_area_m2
    FROM measured_intersections AS measured
    WHERE measured.intersection_area_m2 > 0
      AND measured.plr_area_m2 > 0
      AND measured.hostrada_cell_area_m2 > 0;

    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT,
        COUNT(DISTINCT bridge_row.plr_id)::BIGINT,
        COUNT(
            DISTINCT (bridge_row.y_index, bridge_row.x_index)
        )::BIGINT
    FROM normalized.hostrada_plr_area_bridge AS bridge_row
    WHERE bridge_row.geography_version = p_geography_version
      AND bridge_row.source_grid_id = p_source_grid_id;
END;
$$;


CREATE OR REPLACE FUNCTION normalized.check_hostrada_plr_area_bridge_quality(
    p_geography_version TEXT,
    p_source_grid_id TEXT,
    p_expected_plr_count INTEGER DEFAULT 542,
    p_weight_tolerance DOUBLE PRECISION DEFAULT 0.000001
)
RETURNS TABLE (
    passed BOOLEAN,
    bridge_row_count BIGINT,
    source_plr_count BIGINT,
    represented_plr_count BIGINT,
    source_hostrada_cell_count BIGINT,
    represented_hostrada_cell_count BIGINT,
    missing_plr_count BIGINT,
    unused_hostrada_cell_count BIGINT,
    orphan_plr_count BIGINT,
    orphan_hostrada_cell_count BIGINT,
    nonpositive_area_count BIGINT,
    invalid_fraction_count BIGINT,
    plr_weight_failure_count BIGINT,
    max_plr_weight_error DOUBLE PRECISION
)
LANGUAGE sql
STABLE
AS $$
WITH source_plrs AS (
    SELECT
        plr_row.plr_id,
        plr_row.geography_version
    FROM normalized.plr AS plr_row
    WHERE plr_row.geography_version = p_geography_version
),
source_cells AS (
    SELECT
        cell_row.source_grid_id,
        cell_row.geography_version,
        cell_row.y_index,
        cell_row.x_index
    FROM normalized.hostrada_cell AS cell_row
    WHERE cell_row.geography_version = p_geography_version
      AND cell_row.source_grid_id = p_source_grid_id
),
bridge_rows AS (
    SELECT bridge_row.*
    FROM normalized.hostrada_plr_area_bridge AS bridge_row
    WHERE bridge_row.geography_version = p_geography_version
      AND bridge_row.source_grid_id = p_source_grid_id
),
bridge_summary AS (
    SELECT
        COUNT(*)::BIGINT AS bridge_row_count,
        COUNT(DISTINCT bridge_row.plr_id)::BIGINT AS represented_plr_count,
        COUNT(
            DISTINCT (bridge_row.y_index, bridge_row.x_index)
        )::BIGINT AS represented_hostrada_cell_count
    FROM bridge_rows AS bridge_row
),
source_summary AS (
    SELECT
        (SELECT COUNT(*) FROM source_plrs)::BIGINT AS source_plr_count,
        (SELECT COUNT(*) FROM source_cells)::BIGINT
            AS source_hostrada_cell_count
),
missing_plrs AS (
    SELECT COUNT(*)::BIGINT AS missing_plr_count
    FROM source_plrs AS plr_row
    WHERE NOT EXISTS (
        SELECT 1
        FROM bridge_rows AS bridge_row
        WHERE bridge_row.plr_id = plr_row.plr_id
          AND bridge_row.geography_version = plr_row.geography_version
    )
),
unused_cells AS (
    SELECT COUNT(*)::BIGINT AS unused_hostrada_cell_count
    FROM source_cells AS cell_row
    WHERE NOT EXISTS (
        SELECT 1
        FROM bridge_rows AS bridge_row
        WHERE bridge_row.source_grid_id = cell_row.source_grid_id
          AND bridge_row.geography_version = cell_row.geography_version
          AND bridge_row.y_index = cell_row.y_index
          AND bridge_row.x_index = cell_row.x_index
    )
),
orphan_plrs AS (
    SELECT COUNT(*)::BIGINT AS orphan_plr_count
    FROM bridge_rows AS bridge_row
    LEFT JOIN normalized.plr AS plr_row
      ON plr_row.plr_id = bridge_row.plr_id
     AND plr_row.geography_version = bridge_row.geography_version
    WHERE plr_row.plr_id IS NULL
),
orphan_cells AS (
    SELECT COUNT(*)::BIGINT AS orphan_hostrada_cell_count
    FROM bridge_rows AS bridge_row
    LEFT JOIN normalized.hostrada_cell AS cell_row
      ON cell_row.source_grid_id = bridge_row.source_grid_id
     AND cell_row.geography_version = bridge_row.geography_version
     AND cell_row.y_index = bridge_row.y_index
     AND cell_row.x_index = bridge_row.x_index
    WHERE cell_row.source_grid_id IS NULL
),
bad_areas AS (
    SELECT COUNT(*)::BIGINT AS nonpositive_area_count
    FROM bridge_rows AS bridge_row
    WHERE bridge_row.intersection_area_m2 <= 0
       OR bridge_row.plr_area_m2 <= 0
       OR bridge_row.hostrada_cell_area_m2 <= 0
),
bad_fractions AS (
    SELECT COUNT(*)::BIGINT AS invalid_fraction_count
    FROM bridge_rows AS bridge_row
    WHERE bridge_row.fraction_of_plr <= 0
       OR bridge_row.fraction_of_plr > 1 + p_weight_tolerance
       OR bridge_row.fraction_of_hostrada_cell <= 0
       OR bridge_row.fraction_of_hostrada_cell > 1 + p_weight_tolerance
),
plr_weight_sums AS (
    SELECT
        bridge_row.plr_id,
        SUM(bridge_row.fraction_of_plr)::DOUBLE PRECISION AS fraction_sum
    FROM bridge_rows AS bridge_row
    GROUP BY bridge_row.plr_id
),
weight_summary AS (
    SELECT
        COUNT(*) FILTER (
            WHERE ABS(weight_row.fraction_sum - 1.0) > p_weight_tolerance
        )::BIGINT AS plr_weight_failure_count,
        COALESCE(
            MAX(ABS(weight_row.fraction_sum - 1.0)),
            0.0
        )::DOUBLE PRECISION AS max_plr_weight_error
    FROM plr_weight_sums AS weight_row
)
SELECT
    (
        p_expected_plr_count > 0
        AND p_weight_tolerance >= 0
        AND source_summary.source_plr_count = p_expected_plr_count
        AND source_summary.source_hostrada_cell_count > 0
        AND bridge_summary.bridge_row_count > 0
        AND bridge_summary.represented_plr_count
            = source_summary.source_plr_count
        AND bridge_summary.represented_hostrada_cell_count
            = source_summary.source_hostrada_cell_count
        AND missing_plrs.missing_plr_count = 0
        AND unused_cells.unused_hostrada_cell_count = 0
        AND orphan_plrs.orphan_plr_count = 0
        AND orphan_cells.orphan_hostrada_cell_count = 0
        AND bad_areas.nonpositive_area_count = 0
        AND bad_fractions.invalid_fraction_count = 0
        AND weight_summary.plr_weight_failure_count = 0
    ) AS passed,
    bridge_summary.bridge_row_count,
    source_summary.source_plr_count,
    bridge_summary.represented_plr_count,
    source_summary.source_hostrada_cell_count,
    bridge_summary.represented_hostrada_cell_count,
    missing_plrs.missing_plr_count,
    unused_cells.unused_hostrada_cell_count,
    orphan_plrs.orphan_plr_count,
    orphan_cells.orphan_hostrada_cell_count,
    bad_areas.nonpositive_area_count,
    bad_fractions.invalid_fraction_count,
    weight_summary.plr_weight_failure_count,
    weight_summary.max_plr_weight_error
FROM bridge_summary
CROSS JOIN source_summary
CROSS JOIN missing_plrs
CROSS JOIN unused_cells
CROSS JOIN orphan_plrs
CROSS JOIN orphan_cells
CROSS JOIN bad_areas
CROSS JOIN bad_fractions
CROSS JOIN weight_summary;
$$;

COMMIT;
