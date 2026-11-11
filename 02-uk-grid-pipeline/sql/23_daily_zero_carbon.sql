CREATE OR REPLACE TABLE daily_zero_carbon AS
WITH per_period AS (
    SELECT period_start, day, sum(perc) FILTER (WHERE is_zero_carbon) AS zero_carbon_share
    FROM stg_generation
    GROUP BY period_start, day
)
SELECT day, round(avg(coalesce(zero_carbon_share, 0)), 1) AS zero_carbon_share
FROM per_period
GROUP BY day
ORDER BY day;
