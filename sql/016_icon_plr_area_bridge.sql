-- Canonical bridge materialization contract
--
-- CREATE OR REPLACE does not replace a function whose argument
-- signature changed. Remove the earlier four-argument overload so a
-- clean bootstrap cannot recreate the ambiguity observed during Phase 6.

DROP FUNCTION IF EXISTS
    normalized.refresh_icon_plr_area_bridge(
        TEXT,
        TEXT,
        INTEGER,
        INTEGER
    );

CREATE INDEX IF NOT EXISTS idx_icon_plr_area_bridge_icon_cell
    ON normalized.icon_plr_area_bridge (
        source_grid_id,
        cell_index
    );

CREATE OR REPLACE FUNCTION normalized.refresh_icon_plr_area_bridge(
    p_geography_version TEXT,
    p_source_grid_id TEXT
)
RETURNS TABLE (
    bridge_row_count BIGINT,
    represented_plr_count BIGINT,
    represented_icon_cell_count BIGINT
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_geography_version IS NULL
       OR btrim(p_geography_version) = '' THEN
        RAISE EXCEPTION
            'p_geography_version must be non-empty';
    END IF;

    IF p_source_grid_id IS NULL
       OR btrim(p_source_grid_id) = '' THEN
        RAISE EXCEPTION
            'p_source_grid_id must be non-empty';
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
        FROM normalized.icon_cell AS icon_row
        WHERE icon_row.source_grid_id = p_source_grid_id
    ) THEN
        RAISE EXCEPTION
            'No normalized ICON cells found for source_grid_id=%',
            p_source_grid_id;
    END IF;

    DELETE FROM normalized.icon_plr_area_bridge AS bridge_row
    WHERE bridge_row.geography_version = p_geography_version
      AND bridge_row.source_grid_id = p_source_grid_id;

    INSERT INTO normalized.icon_plr_area_bridge (
        plr_id,
        geography_version,
        source_grid_id,
        cell_index,
        intersection_area_m2,
        plr_area_m2,
        icon_cell_area_m2,
        fraction_of_plr,
        fraction_of_icon_cell
    )
    WITH candidate_pairs AS (
        SELECT
            plr_row.plr_id,
            plr_row.geography_version,
            icon_row.source_grid_id,
            icon_row.cell_index,
            plr_row.geometry AS plr_geometry,
            icon_row.geometry AS icon_geometry,
            ST_Area(plr_row.geometry)::DOUBLE PRECISION
                AS plr_area_m2,
            ST_Area(icon_row.geometry)::DOUBLE PRECISION
                AS icon_cell_area_m2
        FROM normalized.plr AS plr_row
        JOIN normalized.icon_cell AS icon_row
          ON icon_row.source_grid_id = p_source_grid_id
         AND plr_row.geometry && icon_row.geometry
         AND ST_Intersects(
                plr_row.geometry,
                icon_row.geometry
            )
        WHERE plr_row.geography_version = p_geography_version
    ),
    measured_intersections AS (
        SELECT
            candidate.plr_id,
            candidate.geography_version,
            candidate.source_grid_id,
            candidate.cell_index,
            candidate.plr_area_m2,
            candidate.icon_cell_area_m2,
            ST_Area(
                ST_Intersection(
                    candidate.plr_geometry,
                    candidate.icon_geometry
                )
            )::DOUBLE PRECISION AS intersection_area_m2
        FROM candidate_pairs AS candidate
    )
    SELECT
        measured.plr_id,
        measured.geography_version,
        measured.source_grid_id,
        measured.cell_index,
        measured.intersection_area_m2,
        measured.plr_area_m2,
        measured.icon_cell_area_m2,
        measured.intersection_area_m2 / measured.plr_area_m2,
        measured.intersection_area_m2 / measured.icon_cell_area_m2
    FROM measured_intersections AS measured
    WHERE measured.intersection_area_m2 > 0
      AND measured.plr_area_m2 > 0
      AND measured.icon_cell_area_m2 > 0;

    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT,
        COUNT(DISTINCT bridge_row.plr_id)::BIGINT,
        COUNT(DISTINCT bridge_row.cell_index)::BIGINT
    FROM normalized.icon_plr_area_bridge AS bridge_row
    WHERE bridge_row.geography_version = p_geography_version
      AND bridge_row.source_grid_id = p_source_grid_id;
END;
$$;
