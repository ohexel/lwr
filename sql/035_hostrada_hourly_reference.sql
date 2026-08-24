BEGIN;

-- Reference rows deliberately retain only the internal geography key,
-- calendar coordinates, six measurements, and an auditable sample count.
CREATE TABLE IF NOT EXISTS analytical.hostrada_plr_hourly_reference (
    calendar_month SMALLINT NOT NULL,
    geography_version TEXT NOT NULL,
    plr_id TEXT NOT NULL,
    calendar_day SMALLINT NOT NULL,
    local_hour SMALLINT NOT NULL,
    sample_count SMALLINT NOT NULL,
    temperature_median_c DOUBLE PRECISION NOT NULL,
    temperature_p90_c DOUBLE PRECISION NOT NULL,
    temperature_max_c DOUBLE PRECISION NOT NULL,
    apparent_temperature_median_c DOUBLE PRECISION NOT NULL,
    apparent_temperature_p90_c DOUBLE PRECISION NOT NULL,
    apparent_temperature_max_c DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (
        calendar_month,
        geography_version,
        plr_id,
        calendar_day,
        local_hour
    ),
    FOREIGN KEY (plr_id, geography_version)
        REFERENCES normalized.plr (plr_id, geography_version),
    CHECK (calendar_month BETWEEN 1 AND 12),
    CHECK (calendar_day BETWEEN 1 AND 31),
    CHECK (local_hour BETWEEN 0 AND 23),
    CHECK (NOT (calendar_month = 2 AND calendar_day = 29)),
    CHECK (sample_count > 0),
    CHECK (
        temperature_median_c <= temperature_p90_c
        AND temperature_p90_c <= temperature_max_c
    ),
    CHECK (
        apparent_temperature_median_c <= apparent_temperature_p90_c
        AND apparent_temperature_p90_c <= apparent_temperature_max_c
    )
);


CREATE TABLE IF NOT EXISTS analytical.hostrada_berlin_hourly_reference (
    calendar_month SMALLINT NOT NULL,
    geography_version TEXT NOT NULL,
    calendar_day SMALLINT NOT NULL,
    local_hour SMALLINT NOT NULL,
    sample_count SMALLINT NOT NULL,
    temperature_median_c DOUBLE PRECISION NOT NULL,
    temperature_p90_c DOUBLE PRECISION NOT NULL,
    temperature_max_c DOUBLE PRECISION NOT NULL,
    apparent_temperature_median_c DOUBLE PRECISION NOT NULL,
    apparent_temperature_p90_c DOUBLE PRECISION NOT NULL,
    apparent_temperature_max_c DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (
        calendar_month,
        geography_version,
        calendar_day,
        local_hour
    ),
    CHECK (calendar_month BETWEEN 1 AND 12),
    CHECK (calendar_day BETWEEN 1 AND 31),
    CHECK (local_hour BETWEEN 0 AND 23),
    CHECK (NOT (calendar_month = 2 AND calendar_day = 29)),
    CHECK (sample_count > 0),
    CHECK (
        temperature_median_c <= temperature_p90_c
        AND temperature_p90_c <= temperature_max_c
    ),
    CHECK (
        apparent_temperature_median_c <= apparent_temperature_p90_c
        AND apparent_temperature_p90_c <= apparent_temperature_max_c
    )
);


-- A Berlin-local calendar month can overlap two UTC source months. Return
-- non-overlapping UTC windows so the existing source-month/hour indexes apply.
CREATE OR REPLACE FUNCTION analytical.hostrada_reference_source_windows(
    p_calendar_month INTEGER
)
RETURNS TABLE (
    reference_year INTEGER,
    source_month_utc DATE,
    start_utc TIMESTAMPTZ,
    end_utc TIMESTAMPTZ
)
LANGUAGE sql
STABLE
ROWS 64
AS $$
    WITH local_month_bounds AS (
        SELECT
            years.reference_year,
            make_timestamptz(
                years.reference_year,
                p_calendar_month,
                1,
                0,
                0,
                0,
                'Europe/Berlin'
            ) AS local_start_utc,
            (
                make_date(years.reference_year, p_calendar_month, 1)
                    + INTERVAL '1 month'
            )::TIMESTAMP AT TIME ZONE 'Europe/Berlin'
                AS local_end_utc
        FROM generate_series(1995, 2025) AS years(reference_year)
        WHERE p_calendar_month BETWEEN 1 AND 12
    ),
    candidate_source_months AS (
        SELECT
            local_bounds.reference_year,
            candidate.source_month_utc,
            local_bounds.local_start_utc,
            local_bounds.local_end_utc
        FROM local_month_bounds AS local_bounds
        CROSS JOIN LATERAL (
            SELECT DATE_TRUNC(
                'month',
                local_bounds.local_start_utc AT TIME ZONE 'UTC'
            )::DATE AS source_month_utc

            UNION

            SELECT DATE_TRUNC(
                'month',
                (
                    local_bounds.local_end_utc - INTERVAL '1 hour'
                ) AT TIME ZONE 'UTC'
            )::DATE AS source_month_utc
        ) AS candidate
        WHERE candidate.source_month_utc >= DATE '1995-01-01'
          AND candidate.source_month_utc < DATE '2026-01-01'
    )
    SELECT
        candidate.reference_year,
        candidate.source_month_utc,
        GREATEST(
            candidate.local_start_utc,
            candidate.source_month_utc::TIMESTAMP AT TIME ZONE 'UTC'
        ) AS start_utc,
        LEAST(
            candidate.local_end_utc,
            (
                candidate.source_month_utc::TIMESTAMP + INTERVAL '1 month'
            ) AT TIME ZONE 'UTC'
        ) AS end_utc
    FROM candidate_source_months AS candidate
    ORDER BY
        candidate.reference_year,
        candidate.source_month_utc;
$$;


-- Expected sample counts come from UTC chronology, not hard-coded daylight-
-- saving rules. Historical Europe/Berlin fallbacks also occur in September.
CREATE OR REPLACE FUNCTION analytical.hostrada_reference_expected_hours(
    p_calendar_month INTEGER
)
RETURNS TABLE (
    calendar_month SMALLINT,
    calendar_day SMALLINT,
    local_hour SMALLINT,
    sample_count SMALLINT
)
LANGUAGE sql
STABLE
ROWS 744
AS $$
    WITH local_hours AS (
        SELECT
            generated_hour.valid_time_utc
                AT TIME ZONE 'Europe/Berlin' AS valid_time_berlin
        FROM analytical.hostrada_reference_source_windows(
            p_calendar_month
        ) AS source_window
        CROSS JOIN LATERAL generate_series(
            source_window.start_utc,
            source_window.end_utc - INTERVAL '1 hour',
            INTERVAL '1 hour'
        ) AS generated_hour(valid_time_utc)
    )
    SELECT
        EXTRACT(MONTH FROM local_hour.valid_time_berlin)::SMALLINT,
        EXTRACT(DAY FROM local_hour.valid_time_berlin)::SMALLINT,
        EXTRACT(HOUR FROM local_hour.valid_time_berlin)::SMALLINT,
        COUNT(*)::SMALLINT
    FROM local_hours AS local_hour
    WHERE NOT (
        EXTRACT(MONTH FROM local_hour.valid_time_berlin) = 2
        AND EXTRACT(DAY FROM local_hour.valid_time_berlin) = 29
    )
    GROUP BY 1, 2, 3
    ORDER BY 1, 2, 3;
$$;


CREATE OR REPLACE FUNCTION analytical.check_hostrada_reference_month_quality(
    p_calendar_month INTEGER,
    p_geography_version TEXT,
    p_source_grid_id TEXT
)
RETURNS TABLE (
    passed BOOLEAN,
    expected_plr_count BIGINT,
    expected_calendar_hour_count BIGINT,
    expected_observation_count BIGINT,
    source_month_count BIGINT,
    source_month_failure_count BIGINT,
    plr_reference_count BIGINT,
    berlin_reference_count BIGINT,
    plr_sample_count_failure_count BIGINT,
    berlin_sample_count_failure_count BIGINT
)
LANGUAGE sql
STABLE
AS $$
    WITH expected_hours AS MATERIALIZED (
        SELECT *
        FROM analytical.hostrada_reference_expected_hours(
            p_calendar_month
        )
    ),
    source_months AS MATERIALIZED (
        SELECT DISTINCT source_window.source_month_utc
        FROM analytical.hostrada_reference_source_windows(
            p_calendar_month
        ) AS source_window
    ),
    source_quality AS (
        SELECT
            COUNT(*)::BIGINT AS month_count,
            COUNT(*) FILTER (
                WHERE source_manifest.source_file_count <> 3
                   OR source_manifest.variable_count <> 3
            )::BIGINT AS failure_count
        FROM source_months AS source_month
        CROSS JOIN LATERAL (
            SELECT
                COUNT(*)::BIGINT AS source_file_count,
                COUNT(DISTINCT source_row.variable_name)::BIGINT
                    AS variable_count
            FROM raw.hostrada_month_source AS source_row
            WHERE source_row.source_month_utc
                = source_month.source_month_utc
              AND source_row.source_grid_id = p_source_grid_id
        ) AS source_manifest
    ),
    expected_totals AS (
        SELECT
            (
                SELECT COUNT(*)::BIGINT
                FROM normalized.plr AS plr_row
                WHERE plr_row.geography_version = p_geography_version
            ) AS plr_count,
            COUNT(*)::BIGINT AS hour_count,
            COALESCE(SUM(expected_hour.sample_count), 0)::BIGINT
                AS observation_count
        FROM expected_hours AS expected_hour
    ),
    plr_quality AS (
        SELECT
            COUNT(*)::BIGINT AS row_count,
            COUNT(*) FILTER (
                WHERE expected_hour.sample_count IS DISTINCT FROM
                    reference_row.sample_count
            )::BIGINT AS sample_failure_count
        FROM analytical.hostrada_plr_hourly_reference AS reference_row
        LEFT JOIN expected_hours AS expected_hour
          ON expected_hour.calendar_month = reference_row.calendar_month
         AND expected_hour.calendar_day = reference_row.calendar_day
         AND expected_hour.local_hour = reference_row.local_hour
        WHERE reference_row.calendar_month = p_calendar_month
          AND reference_row.geography_version = p_geography_version
    ),
    berlin_quality AS (
        SELECT
            COUNT(*)::BIGINT AS row_count,
            COUNT(*) FILTER (
                WHERE expected_hour.sample_count IS DISTINCT FROM
                    reference_row.sample_count
            )::BIGINT AS sample_failure_count
        FROM analytical.hostrada_berlin_hourly_reference AS reference_row
        LEFT JOIN expected_hours AS expected_hour
          ON expected_hour.calendar_month = reference_row.calendar_month
         AND expected_hour.calendar_day = reference_row.calendar_day
         AND expected_hour.local_hour = reference_row.local_hour
        WHERE reference_row.calendar_month = p_calendar_month
          AND reference_row.geography_version = p_geography_version
    )
    SELECT
        p_calendar_month BETWEEN 1 AND 12
            AND expected.plr_count > 0
            AND expected.hour_count > 0
            AND source.month_count > 0
            AND source.failure_count = 0
            AND plr.row_count = expected.hour_count * expected.plr_count
            AND berlin.row_count = expected.hour_count
            AND plr.sample_failure_count = 0
            AND berlin.sample_failure_count = 0,
        expected.plr_count,
        expected.hour_count,
        expected.observation_count,
        source.month_count,
        source.failure_count,
        plr.row_count,
        berlin.row_count,
        plr.sample_failure_count,
        berlin.sample_failure_count
    FROM expected_totals AS expected
    CROSS JOIN source_quality AS source
    CROSS JOIN plr_quality AS plr
    CROSS JOIN berlin_quality AS berlin;
$$;


CREATE OR REPLACE FUNCTION analytical.refresh_hostrada_reference_month(
    p_calendar_month INTEGER,
    p_geography_version TEXT,
    p_source_grid_id TEXT
)
RETURNS TABLE (
    calendar_month SMALLINT,
    expected_plr_count BIGINT,
    expected_calendar_hour_count BIGINT,
    expected_observation_count BIGINT,
    plr_reference_count BIGINT,
    berlin_reference_count BIGINT
)
LANGUAGE plpgsql
SET work_mem = '64MB'
AS $$
DECLARE
    v_expected_plrs BIGINT;
    v_expected_source_months BIGINT;
    v_source_files BIGINT;
    v_quality RECORD;
BEGIN
    IF p_calendar_month IS NULL
       OR p_calendar_month NOT BETWEEN 1 AND 12 THEN
        RAISE EXCEPTION 'Calendar month must be between 1 and 12';
    END IF;

    IF p_geography_version IS NULL OR p_source_grid_id IS NULL THEN
        RAISE EXCEPTION 'Geography version and HOSTRADA grid are required';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtext('analytical.hostrada_hourly_reference'),
        p_calendar_month
    );

    SELECT COUNT(*)::BIGINT
    INTO v_expected_plrs
    FROM normalized.plr AS plr_row
    WHERE plr_row.geography_version = p_geography_version;

    IF v_expected_plrs = 0 THEN
        RAISE EXCEPTION 'No PLRs exist for geography %', p_geography_version;
    END IF;

    WITH required_source_months AS (
        SELECT DISTINCT source_window.source_month_utc
        FROM analytical.hostrada_reference_source_windows(
            p_calendar_month
        ) AS source_window
    )
    SELECT
        COUNT(DISTINCT required_month.source_month_utc)::BIGINT,
        COUNT(source_row.variable_name)::BIGINT
    INTO
        v_expected_source_months,
        v_source_files
    FROM required_source_months AS required_month
    LEFT JOIN raw.hostrada_month_source AS source_row
      ON source_row.source_month_utc = required_month.source_month_utc
     AND source_row.source_grid_id = p_source_grid_id;

    IF v_expected_source_months = 0
       OR v_source_files <> v_expected_source_months * 3 THEN
        RAISE EXCEPTION
            'Incomplete HOSTRADA source manifests for local month %: % files across % required UTC months',
            p_calendar_month,
            v_source_files,
            v_expected_source_months;
    END IF;

    DELETE FROM analytical.hostrada_plr_hourly_reference AS reference_row
    WHERE reference_row.calendar_month = p_calendar_month
      AND reference_row.geography_version = p_geography_version;

    DELETE FROM analytical.hostrada_berlin_hourly_reference AS reference_row
    WHERE reference_row.calendar_month = p_calendar_month
      AND reference_row.geography_version = p_geography_version;

    WITH local_observations AS (
        SELECT
            hourly_row.plr_id,
            EXTRACT(DAY FROM local_time.valid_time_berlin)::SMALLINT
                AS calendar_day,
            EXTRACT(HOUR FROM local_time.valid_time_berlin)::SMALLINT
                AS local_hour,
            hourly_row.temperature_c,
            hourly_row.apparent_temperature_shade_c
        FROM analytical.hostrada_reference_source_windows(
            p_calendar_month
        ) AS source_window
        JOIN analytical.hostrada_plr_hourly AS hourly_row
          ON hourly_row.source_month_utc = source_window.source_month_utc
         AND hourly_row.valid_time_utc >= source_window.start_utc
         AND hourly_row.valid_time_utc < source_window.end_utc
        CROSS JOIN LATERAL (
            SELECT hourly_row.valid_time_utc
                AT TIME ZONE 'Europe/Berlin' AS valid_time_berlin
        ) AS local_time
        WHERE hourly_row.geography_version = p_geography_version
          AND hourly_row.source_grid_id = p_source_grid_id
          AND NOT (
              p_calendar_month = 2
              AND EXTRACT(DAY FROM local_time.valid_time_berlin) = 29
          )
    ),
    summaries AS (
        SELECT
            observation.plr_id,
            observation.calendar_day,
            observation.local_hour,
            COUNT(*)::SMALLINT AS sample_count,
            PERCENTILE_CONT(
                ARRAY[0.5, 0.9]::DOUBLE PRECISION[]
            ) WITHIN GROUP (
                ORDER BY observation.temperature_c
            ) AS temperature_percentiles,
            MAX(observation.temperature_c) AS temperature_max_c,
            PERCENTILE_CONT(
                ARRAY[0.5, 0.9]::DOUBLE PRECISION[]
            ) WITHIN GROUP (
                ORDER BY observation.apparent_temperature_shade_c
            ) AS apparent_temperature_percentiles,
            MAX(observation.apparent_temperature_shade_c)
                AS apparent_temperature_max_c
        FROM local_observations AS observation
        GROUP BY
            observation.plr_id,
            observation.calendar_day,
            observation.local_hour
    )
    INSERT INTO analytical.hostrada_plr_hourly_reference (
        calendar_month,
        geography_version,
        plr_id,
        calendar_day,
        local_hour,
        sample_count,
        temperature_median_c,
        temperature_p90_c,
        temperature_max_c,
        apparent_temperature_median_c,
        apparent_temperature_p90_c,
        apparent_temperature_max_c
    )
    SELECT
        p_calendar_month::SMALLINT,
        p_geography_version,
        summary.plr_id,
        summary.calendar_day,
        summary.local_hour,
        summary.sample_count,
        (summary.temperature_percentiles)[1],
        (summary.temperature_percentiles)[2],
        summary.temperature_max_c,
        (summary.apparent_temperature_percentiles)[1],
        (summary.apparent_temperature_percentiles)[2],
        summary.apparent_temperature_max_c
    FROM summaries AS summary;

    WITH local_observations AS (
        SELECT
            EXTRACT(DAY FROM local_time.valid_time_berlin)::SMALLINT
                AS calendar_day,
            EXTRACT(HOUR FROM local_time.valid_time_berlin)::SMALLINT
                AS local_hour,
            hourly_row.temperature_c,
            hourly_row.apparent_temperature_shade_c
        FROM analytical.hostrada_reference_source_windows(
            p_calendar_month
        ) AS source_window
        JOIN analytical.hostrada_berlin_hourly AS hourly_row
          ON hourly_row.source_month_utc = source_window.source_month_utc
         AND hourly_row.valid_time_utc >= source_window.start_utc
         AND hourly_row.valid_time_utc < source_window.end_utc
        CROSS JOIN LATERAL (
            SELECT hourly_row.valid_time_utc
                AT TIME ZONE 'Europe/Berlin' AS valid_time_berlin
        ) AS local_time
        WHERE hourly_row.geography_version = p_geography_version
          AND hourly_row.source_grid_id = p_source_grid_id
          AND NOT (
              p_calendar_month = 2
              AND EXTRACT(DAY FROM local_time.valid_time_berlin) = 29
          )
    ),
    summaries AS (
        SELECT
            observation.calendar_day,
            observation.local_hour,
            COUNT(*)::SMALLINT AS sample_count,
            PERCENTILE_CONT(
                ARRAY[0.5, 0.9]::DOUBLE PRECISION[]
            ) WITHIN GROUP (
                ORDER BY observation.temperature_c
            ) AS temperature_percentiles,
            MAX(observation.temperature_c) AS temperature_max_c,
            PERCENTILE_CONT(
                ARRAY[0.5, 0.9]::DOUBLE PRECISION[]
            ) WITHIN GROUP (
                ORDER BY observation.apparent_temperature_shade_c
            ) AS apparent_temperature_percentiles,
            MAX(observation.apparent_temperature_shade_c)
                AS apparent_temperature_max_c
        FROM local_observations AS observation
        GROUP BY
            observation.calendar_day,
            observation.local_hour
    )
    INSERT INTO analytical.hostrada_berlin_hourly_reference (
        calendar_month,
        geography_version,
        calendar_day,
        local_hour,
        sample_count,
        temperature_median_c,
        temperature_p90_c,
        temperature_max_c,
        apparent_temperature_median_c,
        apparent_temperature_p90_c,
        apparent_temperature_max_c
    )
    SELECT
        p_calendar_month::SMALLINT,
        p_geography_version,
        summary.calendar_day,
        summary.local_hour,
        summary.sample_count,
        (summary.temperature_percentiles)[1],
        (summary.temperature_percentiles)[2],
        summary.temperature_max_c,
        (summary.apparent_temperature_percentiles)[1],
        (summary.apparent_temperature_percentiles)[2],
        summary.apparent_temperature_max_c
    FROM summaries AS summary;

    SELECT *
    INTO v_quality
    FROM analytical.check_hostrada_reference_month_quality(
        p_calendar_month,
        p_geography_version,
        p_source_grid_id
    );

    IF v_quality IS NULL OR NOT v_quality.passed THEN
        RAISE EXCEPTION
            'HOSTRADA reference month % failed its quality gate: %',
            p_calendar_month,
            to_jsonb(v_quality);
    END IF;

    RETURN QUERY
    SELECT
        p_calendar_month::SMALLINT,
        v_quality.expected_plr_count,
        v_quality.expected_calendar_hour_count,
        v_quality.expected_observation_count,
        v_quality.plr_reference_count,
        v_quality.berlin_reference_count;
END;
$$;

COMMIT;
