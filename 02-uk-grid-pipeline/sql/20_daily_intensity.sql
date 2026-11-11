-- A day is "complete" when at least 44 of its 48 half-hours have a measured value.
-- The first and last day of a fetch are usually partial, and the average of half
-- a day is not comparable with the average of a whole one.
CREATE OR REPLACE TABLE daily_intensity AS
SELECT
    day,
    round(avg(intensity), 1) AS avg_intensity,
    min(intensity)           AS min_intensity,
    max(intensity)           AS max_intensity,
    count(*)                 AS periods,
    count(*) >= 44           AS is_complete
FROM stg_intensity
GROUP BY day
ORDER BY day;
