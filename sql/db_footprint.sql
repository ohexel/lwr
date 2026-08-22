SELECT
    schemaname,
    relname,
    pg_size_pretty(
	pg_total_relation_size(
		quote_ident(schemaname) || '.' || quote_ident(relname)
	)
    ) AS total_size,
    pg_size_pretty(
	pg_relation_size(
		quote_ident(schemaname) || '.' || quote_ident(relname)
	)
    ) AS table_size,
    pg_size_pretty(
	pg_indexes_size(
		quote_ident(schemaname) || '.' || quote_ident(relname)
	)
    ) AS index_size
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(quote_ident(schemaname) || '.' || quote_ident(relname)) DESC
LIMIT 20;
