CREATE INDEX IF NOT EXISTS idx_icon_grid_cell_vertex_vertex
    ON raw.icon_grid_cell_vertex (
        source_grid_id,
        vertex_index
    );

DO $$
DECLARE
    existing_constraint_name TEXT;
BEGIN
    SELECT c.conname
    INTO existing_constraint_name
    FROM pg_constraint AS c
    WHERE c.conrelid = 'raw.icon_grid_cell_vertex'::regclass
      AND c.contype = 'f'
      AND c.confrelid = 'raw.icon_grid_vertex'::regclass
    LIMIT 1;

    IF existing_constraint_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE raw.icon_grid_cell_vertex DROP CONSTRAINT %I',
            existing_constraint_name
        );
    END IF;

    ALTER TABLE raw.icon_grid_cell_vertex
        ADD CONSTRAINT icon_grid_cell_vertex_vertex_fk
        FOREIGN KEY (
            source_grid_id,
            vertex_index
        )
        REFERENCES raw.icon_grid_vertex (
            source_grid_id,
            vertex_index
        )
        ON DELETE CASCADE;
END
$$;
