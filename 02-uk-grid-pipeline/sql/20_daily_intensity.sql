CREATE OR REPLACE TABLE daily_intensity AS
SELECT
    day,
    round(avg(intensity), 1) AS avg_intensity,
    min(intensity)           AS min_intensity,
    max(intensity)           AS max_intensity,
    count(*)                 AS periods
FROM stg_intensity
GROUP BY day
ORDER BY day;
