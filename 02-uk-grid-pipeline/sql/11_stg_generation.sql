-- Fuel shares per half-hour, tagged with whether the fuel emits no carbon
-- at the point of generation. Biomass and imports are deliberately not counted.
CREATE OR REPLACE VIEW stg_generation AS
SELECT
    period_start,
    CAST(period_start AS DATE) AS day,
    fuel,
    perc,
    fuel IN ('wind', 'solar', 'nuclear', 'hydro') AS is_zero_carbon
FROM raw_generation;
