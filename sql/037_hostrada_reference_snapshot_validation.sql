-- Validate an imported reference without consulting historical source files,
-- source manifests, HOSTRADA grid cells, or hourly weather observations.
CREATE OR REPLACE FUNCTION analytical.check_hostrada_reference_snapshot(
    p_geography_version TEXT,
    p_expected_plr_count INTEGER DEFAULT 542
)
RETURNS TABLE (
    passed BOOLEAN,
    expected_plr_count BIGINT,
    installed_plr_count BIGINT,
    expected_calendar_hour_count BIGINT,
    expected_observation_count BIGINT,
    plr_reference_count BIGINT,
    berlin_reference_count BIGINT,
    plr_sample_count_failure_count BIGINT,
    berlin_sample_count_failure_count BIGINT,
    unexpected_plr_geography_count BIGINT,
    unexpected_berlin_geography_count BIGINT,
    statistic_order_failure_count BIGINT
)
LANGUAGE sql
STABLE
AS $$
    WITH expected_hours AS MATERIALIZED (
        SELECT expected_hour.*
        FROM generate_series(1, 12) AS month(calendar_month)
        CROSS JOIN LATERAL analytical.hostrada_reference_expected_hours(
            month.calendar_month
        ) AS expected_hour
    ),
    expected_totals AS (
        SELECT
            COUNT(*)::BIGINT AS hour_count,
            COALESCE(SUM(expected_hour.sample_count), 0)::BIGINT
                AS observation_count
        FROM expected_hours AS expected_hour
    ),
    installed_geography AS (
        SELECT COUNT(*)::BIGINT AS plr_count
        FROM normalized.plr AS plr_row
        WHERE plr_row.geography_version = p_geography_version
    ),
    plr_quality AS (
        SELECT
            COUNT(*) FILTER (
                WHERE reference_row.geography_version = p_geography_version
            )::BIGINT AS row_count,
            COUNT(*) FILTER (
                WHERE reference_row.geography_version = p_geography_version
                  AND expected_hour.sample_count IS DISTINCT FROM
                      reference_row.sample_count
            )::BIGINT AS sample_failure_count,
            COUNT(*) FILTER (
                WHERE reference_row.geography_version <> p_geography_version
            )::BIGINT AS unexpected_geography_count,
            COUNT(*) FILTER (
                WHERE reference_row.geography_version = p_geography_version
                  AND NOT (
                      reference_row.temperature_median_c
                          <= reference_row.temperature_p90_c
                      AND reference_row.temperature_p90_c
                          <= reference_row.temperature_max_c
                      AND reference_row.apparent_temperature_median_c
                          <= reference_row.apparent_temperature_p90_c
                      AND reference_row.apparent_temperature_p90_c
                          <= reference_row.apparent_temperature_max_c
                  )
            )::BIGINT AS statistic_failure_count
        FROM analytical.hostrada_plr_hourly_reference AS reference_row
        LEFT JOIN expected_hours AS expected_hour
          ON expected_hour.calendar_month = reference_row.calendar_month
         AND expected_hour.calendar_day = reference_row.calendar_day
         AND expected_hour.local_hour = reference_row.local_hour
    ),
    berlin_quality AS (
        SELECT
            COUNT(*) FILTER (
                WHERE reference_row.geography_version = p_geography_version
            )::BIGINT AS row_count,
            COUNT(*) FILTER (
                WHERE reference_row.geography_version = p_geography_version
                  AND expected_hour.sample_count IS DISTINCT FROM
                      reference_row.sample_count
            )::BIGINT AS sample_failure_count,
            COUNT(*) FILTER (
                WHERE reference_row.geography_version <> p_geography_version
            )::BIGINT AS unexpected_geography_count,
            COUNT(*) FILTER (
                WHERE reference_row.geography_version = p_geography_version
                  AND NOT (
                      reference_row.temperature_median_c
                          <= reference_row.temperature_p90_c
                      AND reference_row.temperature_p90_c
                          <= reference_row.temperature_max_c
                      AND reference_row.apparent_temperature_median_c
                          <= reference_row.apparent_temperature_p90_c
                      AND reference_row.apparent_temperature_p90_c
                          <= reference_row.apparent_temperature_max_c
                  )
            )::BIGINT AS statistic_failure_count
        FROM analytical.hostrada_berlin_hourly_reference AS reference_row
        LEFT JOIN expected_hours AS expected_hour
          ON expected_hour.calendar_month = reference_row.calendar_month
         AND expected_hour.calendar_day = reference_row.calendar_day
         AND expected_hour.local_hour = reference_row.local_hour
    )
    SELECT
        p_geography_version IS NOT NULL
            AND p_expected_plr_count > 0
            AND geography.plr_count = p_expected_plr_count
            AND expected.hour_count = 8760
            AND expected.observation_count = 271559
            AND plr.row_count = expected.hour_count * p_expected_plr_count
            AND berlin.row_count = expected.hour_count
            AND plr.sample_failure_count = 0
            AND berlin.sample_failure_count = 0
            AND plr.unexpected_geography_count = 0
            AND berlin.unexpected_geography_count = 0
            AND plr.statistic_failure_count = 0
            AND berlin.statistic_failure_count = 0,
        p_expected_plr_count::BIGINT,
        geography.plr_count,
        expected.hour_count,
        expected.observation_count,
        plr.row_count,
        berlin.row_count,
        plr.sample_failure_count,
        berlin.sample_failure_count,
        plr.unexpected_geography_count,
        berlin.unexpected_geography_count,
        plr.statistic_failure_count + berlin.statistic_failure_count
    FROM expected_totals AS expected
    CROSS JOIN installed_geography AS geography
    CROSS JOIN plr_quality AS plr
    CROSS JOIN berlin_quality AS berlin;
$$;
