CREATE OR REPLACE TABLE fuel_mix AS
SELECT
    fuel,
    round(avg(perc), 2)   AS avg_share,
    any_value(is_zero_carbon) AS is_zero_carbon
FROM stg_generation
GROUP BY fuel
ORDER BY avg_share DESC;
