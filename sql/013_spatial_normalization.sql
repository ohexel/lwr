CREATE OR REPLACE FUNCTION normalized.refresh_plr_geometry(
    p_source_sha256 TEXT,
    p_expected_plr_count INTEGER DEFAULT 542
)
RETURNS TABLE (
    source_row_count BIGINT,
    normalized_row_count BIGINT,
    rejected_row_count BIGINT,
    geography_version TEXT,
    rejection_reasons JSONB
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_source_count BIGINT;
    v_normalized_count BIGINT;
    v_rejected_count BIGINT;
    v_geography_version_count BIGINT;
    v_geography_version TEXT;
    v_reasons JSONB;
BEGIN
    SELECT
        COUNT(*),
        COUNT(DISTINCT raw_lor.geography_version),
        MIN(raw_lor.geography_version)
    INTO
        v_source_count,
        v_geography_version_count,
        v_geography_version
    FROM raw.lor_plr AS raw_lor
    WHERE raw_lor.source_sha256 = p_source_sha256;

    IF v_source_count = 0 THEN
        RAISE EXCEPTION
            'No raw LOR rows found for source_sha256 %',
            p_source_sha256;
    END IF;

    IF v_geography_version_count <> 1 THEN
        RAISE EXCEPTION
            'LOR source must contain exactly one geography version; got %',
            v_geography_version_count;
    END IF;

    DELETE FROM normalized.plr_geometry_rejected AS rejected
    WHERE rejected.source_sha256 = p_source_sha256;

    WITH classified AS (
        SELECT
            raw_lor.source_row_id,
            NULLIF(BTRIM(raw_lor.plr_id_source), '') AS plr_id,
            raw_lor.geography_version,
            raw_lor.reference_date,
            raw_lor.source_sha256,
            raw_lor.geometry_source,
            COUNT(*) OVER (
                PARTITION BY
                    NULLIF(BTRIM(raw_lor.plr_id_source), ''),
                    raw_lor.geography_version
            ) AS id_version_count,
            CASE
                WHEN NULLIF(BTRIM(raw_lor.plr_id_source), '') IS NULL
                    THEN 'missing_plr_id'
                WHEN raw_lor.geography_version IS NULL
                    THEN 'missing_geography_version'
                WHEN COUNT(*) OVER (
                    PARTITION BY
                        NULLIF(BTRIM(raw_lor.plr_id_source), ''),
                        raw_lor.geography_version
                ) > 1
                    THEN 'duplicate_plr_id'
                WHEN raw_lor.geometry_source IS NULL
                    THEN 'missing_geometry'
                WHEN ST_IsEmpty(raw_lor.geometry_source)
                    THEN 'empty_geometry'
                WHEN ST_SRID(raw_lor.geometry_source) = 0
                    THEN 'missing_srid'
                WHEN GeometryType(raw_lor.geometry_source)
                     NOT IN ('POLYGON', 'MULTIPOLYGON')
                    THEN 'non_polygonal_geometry'
                WHEN NOT ST_IsValid(raw_lor.geometry_source)
                    THEN 'invalid_geometry'
                ELSE NULL
            END AS rejection_reason
        FROM raw.lor_plr AS raw_lor
        WHERE raw_lor.source_sha256 = p_source_sha256
    )
    INSERT INTO normalized.plr_geometry_rejected (
        source_sha256,
        source_row_id,
        plr_id,
        geography_version,
        rejection_reason
    )
    SELECT
        classified.source_sha256,
        classified.source_row_id,
        classified.plr_id,
        classified.geography_version,
        classified.rejection_reason
    FROM classified
    WHERE classified.rejection_reason IS NOT NULL;

    GET DIAGNOSTICS v_rejected_count = ROW_COUNT;

    WITH classified AS (
        SELECT
            raw_lor.source_row_id,
            NULLIF(BTRIM(raw_lor.plr_id_source), '') AS plr_id,
            raw_lor.geography_version,
            raw_lor.reference_date,
            raw_lor.source_sha256,
            raw_lor.geometry_source,
            COUNT(*) OVER (
                PARTITION BY
                    NULLIF(BTRIM(raw_lor.plr_id_source), ''),
                    raw_lor.geography_version
            ) AS id_version_count,
            CASE
                WHEN NULLIF(BTRIM(raw_lor.plr_id_source), '') IS NULL
                    THEN 'missing_plr_id'
                WHEN raw_lor.geography_version IS NULL
                    THEN 'missing_geography_version'
                WHEN COUNT(*) OVER (
                    PARTITION BY
                        NULLIF(BTRIM(raw_lor.plr_id_source), ''),
                        raw_lor.geography_version
                ) > 1
                    THEN 'duplicate_plr_id'
                WHEN raw_lor.geometry_source IS NULL
                    THEN 'missing_geometry'
                WHEN ST_IsEmpty(raw_lor.geometry_source)
                    THEN 'empty_geometry'
                WHEN ST_SRID(raw_lor.geometry_source) = 0
                    THEN 'missing_srid'
                WHEN GeometryType(raw_lor.geometry_source)
                     NOT IN ('POLYGON', 'MULTIPOLYGON')
                    THEN 'non_polygonal_geometry'
                WHEN NOT ST_IsValid(raw_lor.geometry_source)
                    THEN 'invalid_geometry'
                ELSE NULL
            END AS rejection_reason
        FROM raw.lor_plr AS raw_lor
        WHERE raw_lor.source_sha256 = p_source_sha256
    ),
    canonical AS (
        SELECT
            classified.plr_id,
            classified.geography_version,
            classified.reference_date,
            classified.source_sha256,
            ST_Multi(
                CASE
                    WHEN ST_SRID(classified.geometry_source) = 25833
                        THEN classified.geometry_source
                    ELSE ST_Transform(
                        classified.geometry_source,
                        25833
                    )
                END
            )::geometry(MultiPolygon, 25833) AS geometry
        FROM classified
        WHERE classified.rejection_reason IS NULL
    )
    INSERT INTO normalized.plr (
        plr_id,
        geometry,
        geography_version,
        reference_date,
        source_sha256
    )
    SELECT
        canonical.plr_id,
        canonical.geometry,
        canonical.geography_version,
        canonical.reference_date,
        canonical.source_sha256
    FROM canonical
    WHERE ST_IsValid(canonical.geometry)
      AND NOT ST_IsEmpty(canonical.geometry)
      AND ST_Area(canonical.geometry) > 0
    ON CONFLICT ON CONSTRAINT plr_pkey
    DO UPDATE SET
        geometry = EXCLUDED.geometry,
        reference_date = EXCLUDED.reference_date,
        source_sha256 = EXCLUDED.source_sha256;

    SELECT COUNT(*)
    INTO v_normalized_count
    FROM normalized.plr AS normalized_plr
    WHERE normalized_plr.source_sha256 = p_source_sha256;

    SELECT COALESCE(
        JSONB_OBJECT_AGG(
            reason_counts.rejection_reason,
            reason_counts.reason_count
        ),
        '{}'::JSONB
    )
    INTO v_reasons
    FROM (
        SELECT
            rejected.rejection_reason,
            COUNT(*) AS reason_count
        FROM normalized.plr_geometry_rejected AS rejected
        WHERE rejected.source_sha256 = p_source_sha256
        GROUP BY rejected.rejection_reason
    ) AS reason_counts;

    RETURN QUERY
    SELECT
        v_source_count,
        v_normalized_count,
        v_rejected_count,
        v_geography_version,
        v_reasons;
END
$$;


CREATE OR REPLACE FUNCTION normalized.refresh_icon_cell_geometry(
    p_source_grid_id TEXT,
    p_expected_vertex_count INTEGER DEFAULT 272089,
    p_expected_cell_count INTEGER DEFAULT 542040
)
RETURNS TABLE (
    raw_vertex_count BIGINT,
    raw_cell_count BIGINT,
    normalized_cell_count BIGINT,
    rejected_cell_count BIGINT,
    rejection_reasons JSONB
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_vertex_count BIGINT;
    v_cell_count BIGINT;
    v_normalized_count BIGINT;
    v_rejected_count BIGINT;
    v_reasons JSONB;
BEGIN
    SELECT COUNT(*)
    INTO v_vertex_count
    FROM raw.icon_grid_vertex AS vertex
    WHERE vertex.source_grid_id = p_source_grid_id;

    SELECT COUNT(DISTINCT topology.cell_index)
    INTO v_cell_count
    FROM raw.icon_grid_cell_vertex AS topology
    WHERE topology.source_grid_id = p_source_grid_id;

    IF v_vertex_count = 0 OR v_cell_count = 0 THEN
        RAISE EXCEPTION
            'ICON grid % has no raw vertex/topology data',
            p_source_grid_id;
    END IF;

    DROP TABLE IF EXISTS pg_temp.icon_cell_candidates;

    CREATE TEMP TABLE icon_cell_candidates
    ON COMMIT DROP
    AS
    WITH grouped AS (
        SELECT
            topology.source_grid_id,
            topology.cell_index,
            COUNT(*) AS vertex_count,
            COUNT(DISTINCT topology.vertex_index)
                AS distinct_vertex_count,

            MAX(vertex.longitude_deg)
                FILTER (WHERE topology.vertex_order = 0) AS lon_0,
            MAX(vertex.latitude_deg)
                FILTER (WHERE topology.vertex_order = 0) AS lat_0,

            MAX(vertex.longitude_deg)
                FILTER (WHERE topology.vertex_order = 1) AS lon_1,
            MAX(vertex.latitude_deg)
                FILTER (WHERE topology.vertex_order = 1) AS lat_1,

            MAX(vertex.longitude_deg)
                FILTER (WHERE topology.vertex_order = 2) AS lon_2,
            MAX(vertex.latitude_deg)
                FILTER (WHERE topology.vertex_order = 2) AS lat_2
        FROM raw.icon_grid_cell_vertex AS topology
        JOIN raw.icon_grid_vertex AS vertex
          ON vertex.source_grid_id = topology.source_grid_id
         AND vertex.vertex_index = topology.vertex_index
        WHERE topology.source_grid_id = p_source_grid_id
        GROUP BY
            topology.source_grid_id,
            topology.cell_index
    ),
    preliminary AS (
        SELECT
            grouped.*,
            CASE
                WHEN grouped.vertex_count <> 3
                    THEN 'invalid_vertex_count'
                WHEN grouped.distinct_vertex_count <> 3
                    THEN 'repeated_vertex'
                WHEN grouped.lon_0 IS NULL
                  OR grouped.lat_0 IS NULL
                  OR grouped.lon_1 IS NULL
                  OR grouped.lat_1 IS NULL
                  OR grouped.lon_2 IS NULL
                  OR grouped.lat_2 IS NULL
                    THEN 'missing_vertex_coordinate'
                WHEN grouped.lon_0 NOT BETWEEN -180 AND 180
                  OR grouped.lon_1 NOT BETWEEN -180 AND 180
                  OR grouped.lon_2 NOT BETWEEN -180 AND 180
                  OR grouped.lat_0 NOT BETWEEN -90 AND 90
                  OR grouped.lat_1 NOT BETWEEN -90 AND 90
                  OR grouped.lat_2 NOT BETWEEN -90 AND 90
                    THEN 'invalid_vertex_coordinate'
                ELSE NULL
            END AS preliminary_rejection_reason
        FROM grouped
    ),
    geometry_built AS (
        SELECT
            preliminary.source_grid_id,
            preliminary.cell_index,
            CASE
                WHEN preliminary.preliminary_rejection_reason IS NULL
                THEN ST_Transform(
                    ST_SetSRID(
                        ST_MakePolygon(
                            ST_MakeLine(
                                ARRAY[
                                    ST_MakePoint(
                                        preliminary.lon_0,
                                        preliminary.lat_0
                                    ),
                                    ST_MakePoint(
                                        preliminary.lon_1,
                                        preliminary.lat_1
                                    ),
                                    ST_MakePoint(
                                        preliminary.lon_2,
                                        preliminary.lat_2
                                    ),
                                    ST_MakePoint(
                                        preliminary.lon_0,
                                        preliminary.lat_0
                                    )
                                ]
                            )
                        ),
                        4326
                    ),
                    25833
                )::geometry(Polygon, 25833)
                ELSE NULL
            END AS geometry,
            preliminary.preliminary_rejection_reason
        FROM preliminary
    )
    SELECT
        geometry_built.source_grid_id,
        geometry_built.cell_index,
        geometry_built.geometry,
        CASE
            WHEN geometry_built.preliminary_rejection_reason IS NOT NULL
                THEN geometry_built.preliminary_rejection_reason
            WHEN geometry_built.geometry IS NULL
                THEN 'geometry_construction_failed'
            WHEN ST_IsEmpty(geometry_built.geometry)
                THEN 'empty_geometry'
            WHEN NOT ST_IsValid(geometry_built.geometry)
                THEN 'invalid_geometry'
            WHEN ST_Area(geometry_built.geometry) <= 0
                THEN 'non_positive_area'
            WHEN ST_NPoints(
                ST_ExteriorRing(geometry_built.geometry)
            ) <> 4
                THEN 'not_triangular'
            ELSE NULL
        END AS rejection_reason
    FROM geometry_built;

    DELETE FROM normalized.icon_geometry_rejected AS rejected
    WHERE rejected.source_grid_id = p_source_grid_id;

    INSERT INTO normalized.icon_geometry_rejected (
        source_grid_id,
        cell_index,
        rejection_reason
    )
    SELECT
        candidate.source_grid_id,
        candidate.cell_index,
        candidate.rejection_reason
    FROM icon_cell_candidates AS candidate
    WHERE candidate.rejection_reason IS NOT NULL;

    GET DIAGNOSTICS v_rejected_count = ROW_COUNT;

    INSERT INTO normalized.icon_cell (
        source_grid_id,
        cell_index,
        geometry,
        icon_cell_area_m2
    )
    SELECT
        candidate.source_grid_id,
        candidate.cell_index,
        candidate.geometry,
        ST_Area(candidate.geometry)
    FROM icon_cell_candidates AS candidate
    WHERE candidate.rejection_reason IS NULL
    ON CONFLICT (
        source_grid_id,
        cell_index
    )
    DO UPDATE SET
        geometry = EXCLUDED.geometry,
        icon_cell_area_m2 = EXCLUDED.icon_cell_area_m2;

    SELECT COUNT(*)
    INTO v_normalized_count
    FROM normalized.icon_cell AS normalized_cell
    WHERE normalized_cell.source_grid_id = p_source_grid_id;

    SELECT COALESCE(
        JSONB_OBJECT_AGG(
            reason_counts.rejection_reason,
            reason_counts.reason_count
        ),
        '{}'::JSONB
    )
    INTO v_reasons
    FROM (
        SELECT
            rejected.rejection_reason,
            COUNT(*) AS reason_count
        FROM normalized.icon_geometry_rejected AS rejected
        WHERE rejected.source_grid_id = p_source_grid_id
        GROUP BY rejected.rejection_reason
    ) AS reason_counts;

    RETURN QUERY
    SELECT
        v_vertex_count,
        v_cell_count,
        v_normalized_count,
        v_rejected_count,
        v_reasons;
END
$$;
