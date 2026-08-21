DO $$
DECLARE
    missing_schema_count INTEGER;
    postgis_version_text TEXT;
BEGIN
    SELECT COUNT(*)
    INTO missing_schema_count
    FROM (
        VALUES
            ('raw'),
            ('normalized'),
            ('analytical')
    ) AS expected(schema_name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM information_schema.schemata s
        WHERE s.schema_name = expected.schema_name
    );

    IF missing_schema_count <> 0 THEN
        RAISE EXCEPTION
            'Database bootstrap failed: one or more required schemas are missing';
    END IF;

    SELECT postgis_version()
    INTO postgis_version_text;

    IF postgis_version_text IS NULL THEN
        RAISE EXCEPTION
            'Database bootstrap failed: PostGIS extension is unavailable';
    END IF;

    RAISE NOTICE
        'Database bootstrap healthy. PostGIS version: %',
        postgis_version_text;
END
$$;
