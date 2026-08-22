CREATE INDEX IF NOT EXISTS idx_icon_plr_area_bridge_cell
    ON normalized.icon_plr_area_bridge (
        source_grid_id,
        cell_index
    );

CREATE OR REPLACE FUNCTION normalized.refresh_icon_plr_area_bridge(
    p_geography_version TEXT,
    p_source_grid_id TEXT,
    p_expected_plr_count INTEGER DEFAULT 542,
    p_expected_cell_count INTEGER DEFAULT 542040
)
RETURNS TABLE (
    bridge_row_count BIGINT,
    represented_plr_count BIGINT,
    intersecting_icon_cell_count BIGINT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_plr_count BIGINT;
    v_cell_count BIGINT;
    v_bridge_count BIGINT;
    v_represented_plr_count BIGINT;
    v_intersecting_cell_count BIGINT;
BEGIN
    SELECT COUNT(*)
    INTO v_plr_count
    FROM normalized.plr AS plr
    WHERE plr.geography_version = p_geography_version;

    IF v_plr_count <> p_expected_plr_count THEN
        RAISE EXCEPTION
            'PLR count mismatch for geography version %: expected %, got %',
            p_geography_version,
            p_expected_plr_count,
            v_plr_count;
    END IF;

    SELECT COUNT(*)
    INTO v_cell_count
    FROM normalized.icon_cell AS cell
    WHERE cell.source_grid_id = p_source_grid_id;

    IF v_cell_count <> p_expected_cell_count THEN
        RAISE EXCEPTION
            'ICON cell count mismatch for grid %: expected %, got %',
            p_source_grid_id,
            p_expected_cell_count,
            v_cell_count;
    END IF;

    DELETE FROM normalized.icon_plr_area_bridge AS bridge
    WHERE bridge.geography_version = p_geography_version
      AND bridge.source_grid_id = p_source_grid_id;

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
    SELECT
        plr.plr_id,
        plr.geography_version,
        cell.source_grid_id,
        cell.cell_index,
        intersection.intersection_area_m2,
        ST_Area(plr.geometry),
        cell.icon_cell_area_m2,
        intersection.intersection_area_m2 / ST_Area(plr.geometry),
        intersection.intersection_area_m2 / cell.icon_cell_area_m2
    FROM normalized.plr AS plr
    JOIN normalized.icon_cell AS cell
      ON cell.source_grid_id = p_source_grid_id
     AND cell.geometry && plr.geometry
     AND ST_Intersects(cell.geometry, plr.geometry)
    CROSS JOIN LATERAL (
        SELECT ST_Area(
            ST_Intersection(cell.geometry, plr.geometry)
        ) AS intersection_area_m2
    ) AS intersection
    WHERE plr.geography_version = p_geography_version
      AND intersection.intersection_area_m2 > 0;

    GET DIAGNOSTICS v_bridge_count = ROW_COUNT;

    SELECT
        COUNT(DISTINCT bridge.plr_id),
        COUNT(DISTINCT bridge.cell_index)
    INTO
        v_represented_plr_count,
        v_intersecting_cell_count
    FROM normalized.icon_plr_area_bridge AS bridge
    WHERE bridge.geography_version = p_geography_version
      AND bridge.source_grid_id = p_source_grid_id;

    RETURN QUERY
    SELECT
        v_bridge_count,
        v_represented_plr_count,
        v_intersecting_cell_count;
END
$$;
