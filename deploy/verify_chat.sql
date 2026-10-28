SELECT VERSION() AS mysql_version;
SELECT COUNT(*) AS total_rows,
       COUNT(records) AS non_null_rows,
       SUM(records IS NULL) AS null_rows,
       COUNT(DISTINCT id) AS distinct_ids,
       MIN(id) AS min_id, MAX(id) AS max_id,
       MIN(time) AS min_time, MAX(time) AS max_time,
       SUM(records REGEXP '[一-龥]') AS rows_containing_chinese
FROM chat.records;
SELECT table_schema, table_name, column_name, column_type, is_nullable, column_key
FROM information_schema.columns
WHERE (table_schema = 'chat' AND table_name = 'records')
   OR (table_schema = 'wechat' AND table_name IN ('chathistory', 'new_chathistory'))
ORDER BY table_schema, table_name, ordinal_position;
