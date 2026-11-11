-- The shape of an average day: which hours tend to be cleanest.
CREATE OR REPLACE TABLE hourly_profile AS
SELECT
    hour_utc,
    round(avg(intensity), 1) AS avg_intensity,
    count(*)                 AS periods
FROM stg_intensity
GROUP BY hour_utc
ORDER BY hour_utc;
