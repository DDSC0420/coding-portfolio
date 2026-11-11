-- One row per half-hour that has a measured value. Forecast-only rows
-- (the most recent periods, not yet settled) are left out of every mart.
CREATE OR REPLACE VIEW stg_intensity AS
SELECT
    period_start,
    CAST(period_start AS DATE)        AS day,
    CAST(hour(period_start) AS INT)   AS hour_utc,
    actual                            AS intensity,
    forecast,
    band
FROM raw_intensity
WHERE actual IS NOT NULL;
