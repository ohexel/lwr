-- One-time/idempotent cleanup for databases 

DROP FUNCTION IF EXISTS
    normalized.refresh_icon_plr_area_bridge(
        TEXT,
        TEXT,
        INTEGER,
        INTEGER
    );

DO $$
DECLARE
    stale_function RECORD;
BEGIN
    FOR stale_function IN
        SELECT
            function_row.oid::regprocedure::TEXT AS signature
        FROM pg_proc AS function_row
        JOIN pg_namespace AS namespace_row
          ON namespace_row.oid = function_row.pronamespace
        WHERE namespace_row.nspname = 'normalized'
          AND function_row.proname = 'check_icon_plr_area_bridge'
    LOOP
        EXECUTE format(
            'DROP FUNCTION %s',
            stale_function.signature
        );
    END LOOP;
END
$$;
