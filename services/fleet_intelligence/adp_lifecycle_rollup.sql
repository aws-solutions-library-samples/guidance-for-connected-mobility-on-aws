-- adp_lifecycle_rollup.sql — D2 query fixture for adp_rollup.py
--
-- Spec: .kiro/specs/2026-09-25-cms-fi-adp-wide-lifecycle/spec.md § D2, Design
--
-- This file contains four SQL statements, each a complete Athena query.
-- They are delimited by a sentinel line of the form
-- "-- QUERY_BREAK --" (two dashes, space, QUERY_BREAK, space, two dashes).
-- adp_rollup.build_adp_rollup_sql() splits on that sentinel to produce four
-- separate SQL strings that Athena can execute concurrently.
--
-- Parameterisation (resolved by build_adp_rollup_sql):
--   {stage}               — validated ADP stage, e.g. "staging" or "prod"
--   {window_start}        — trailing-36-month start date SQL literal, e.g. '2023-09-29'
--   per_vin_pipeline      — shared per-VIN CTE block (maint/charge/energy/spine_keys/
--                           monthly/per_vin_fit); injected via the per_vin_pipeline
--                           placeholder from PER_VIN_PIPELINE in adp_rollup.py.
--                           Q3 and Q4 receive query-specific extras for the monthly
--                           and per_vin_fit CTEs.  Query 2 (monthlyTrend) does NOT
--                           use this placeholder because it only needs the spine.
--   per_vin_bucket_case   — shared bucket CASE expression from PER_VIN_BUCKET_CASE.
--
-- No vin-list restriction appears in this file. The vin-list filter used
-- by the T4.2 live parity test is injected by the builder only when explicitly
-- requested, and only into a copy of the per_vin CTE's source tables — never
-- added to this template.
--
-- ──────────────────────────────────────────────────────────────────────────────
-- D2 model (applies to every query below):
--
--   per_vin builds the monthly cost spine (union of months from maint + energy
--   + charging, left-joined) then fits a line per VIN:
--
--     x = ordinal month index: row_number() - 1 (0-based), matching lifecycle.py
--     y = monthly maintenance cost (maintenance only, no charging or miles)
--     slope     = COALESCE(regr_slope(y, x), 0)
--     intercept = COALESCE(regr_intercept(y, x), avg(y))
--     r_squared = greatest(0.0, least(1.0, COALESCE(power(corr(y, x), 2), 1.0)))
--     n         = count(*)
--
--   Edge cases (spec D2 + docs/tech.md):
--     n = 1:      slope NULL → 0, intercept NULL → avg(y), corr NULL → r² 1.0
--     constant y: corr NULL → r² 1.0
--
--   Crossover: smallest k in 1..H with intercept + slope*(n-1+k) >= dep=500
--     slope > 0:
--       k0 = ceiling((dep - intercept) / slope - CAST(n - 1 AS DOUBLE))
--       k0 = CAST(GREATEST(1.0, k0) AS BIGINT)   -- clamp k0 to [1, ∞)
--       k  = smallest of {k0-1, k0, k0+1} (each clamped >=1) where
--              intercept + slope * CAST(n-1+k AS DOUBLE) >= 500.0
--       Gate: k0 <= 37.0 as DOUBLE (so k0-1 = 36 is still attempted).
--       k0 itself is capped at <= 36; k0+1 is capped at <= 36.
--       When k0 = 37: k0-1 = 36 is tried; if that fails, k0 = 37 > 36 → NULL.
--       This matches analyze_sell_timing's Python evaluation order exactly.
--     slope <= 0: k = 1 if intercept + slope*n >= dep, else NULL (no crossover)
--     If k > H, k = NULL.
--
--   Bucket (matching analyze_fleet_lifecycle):
--     r²_meaningful: n >= 3 (_MIN_SERIES_FOR_FIT)
--     n < 3:         bucket = 'insufficient'
--     k <= 6:        bucket = 'sell_recommended'
--     k <= 12:       bucket = 'sell_soon'
--     else:          bucket = 'healthy'
--
-- ──────────────────────────────────────────────────────────────────────────────

-- ════════════════════════════════════════════════════════════════════════════
-- QUERY 1: summary (totalVehicles, bucket counts, avgMonthsToCrossover)
-- ════════════════════════════════════════════════════════════════════════════
WITH {per_vin_pipeline},
per_vin AS (
  SELECT vin, n, slope, intercept, r_squared,
         {per_vin_bucket_case}
         AS bucket,
         {per_vin_k_case} AS months_to_crossover
    FROM per_vin_fit
)
SELECT
  COUNT(*)                                                                 AS total_vehicles,
  SUM(CASE WHEN bucket = 'sell_recommended' THEN 1 ELSE 0 END)            AS sell_recommended_count,
  SUM(CASE WHEN bucket = 'sell_soon'        THEN 1 ELSE 0 END)            AS sell_soon_count,
  SUM(CASE WHEN bucket = 'healthy'          THEN 1 ELSE 0 END)            AS healthy_count,
  SUM(CASE WHEN bucket = 'insufficient'     THEN 1 ELSE 0 END)            AS insufficient_data_count,
  AVG(CAST(months_to_crossover AS DOUBLE))                                 AS avg_months_to_crossover
FROM per_vin

-- QUERY_BREAK --

-- ════════════════════════════════════════════════════════════════════════════
-- QUERY 2: monthlyTrend (yearMonth, avgMaintenance, vehicleCount)
-- ════════════════════════════════════════════════════════════════════════════
WITH maint AS (
  SELECT vin,
         DATE_FORMAT(service_date, '%Y-%m') AS year_month,
         SUM(CAST(total_cost_usd AS DOUBLE)) AS maintenance_cost
    FROM adp_{stage}_service_records.service_records
   WHERE service_date >= CAST({window_start} AS DATE)
   GROUP BY vin, DATE_FORMAT(service_date, '%Y-%m')
),
charge AS (
  SELECT vin,
         DATE_FORMAT(start_time, '%Y-%m') AS year_month,
         SUM(CAST(cost_usd AS DOUBLE)) AS fuel_cost
    FROM adp_{stage}_charging_sessions.charging_sessions
   WHERE start_time >= from_iso8601_timestamp({window_start} || 'T00:00:00Z')
   GROUP BY vin, DATE_FORMAT(start_time, '%Y-%m')
),
energy AS (
  SELECT vin,
         DATE_FORMAT(usage_date, '%Y-%m') AS year_month,
         SUM(total_miles_driven) AS total_miles
    FROM adp_{stage}_energy_usage.energy_usage
   WHERE usage_date >= CAST({window_start} AS DATE)
   GROUP BY vin, DATE_FORMAT(usage_date, '%Y-%m')
),
spine_keys AS (
  SELECT DISTINCT vin, year_month FROM maint
  UNION SELECT DISTINCT vin, year_month FROM charge
  UNION SELECT DISTINCT vin, year_month FROM energy
),
monthly AS (
  SELECT k.vin,
         k.year_month,
         COALESCE(m.maintenance_cost, 0.0) AS y
    FROM spine_keys k
    LEFT JOIN maint  m ON k.vin = m.vin AND k.year_month = m.year_month
)
SELECT year_month,
       AVG(y)            AS avg_maintenance,
       COUNT(DISTINCT vin) AS vehicle_count
  FROM monthly
 GROUP BY year_month
 ORDER BY year_month

-- QUERY_BREAK --

-- ════════════════════════════════════════════════════════════════════════════
-- QUERY 3: cohorts (model × model_year, bucket counts, averages)
-- ════════════════════════════════════════════════════════════════════════════
WITH {per_vin_pipeline},
per_vin AS (
  SELECT vin, n, slope, intercept, r_squared, total_maintenance, total_miles_sum,
         {per_vin_bucket_case}
         AS bucket,
         CASE
           WHEN n > 0 THEN total_maintenance / CAST(n AS DOUBLE)
           ELSE 0.0
         END AS avg_monthly_maintenance,
         CASE
           WHEN total_miles_sum > 0 THEN total_maintenance / total_miles_sum
           ELSE NULL
         END AS avg_cost_per_mile
    FROM per_vin_fit
),
dims AS (
  SELECT vin, model, model_year
    FROM adp_{stage}_dimensions.vins
)
SELECT d.model,
       d.model_year,
       COUNT(*)                                                   AS vehicles,
       SUM(CASE WHEN p.bucket = 'sell_recommended' THEN 1 ELSE 0 END) AS sell_recommended_count,
       SUM(CASE WHEN p.bucket = 'sell_soon'        THEN 1 ELSE 0 END) AS sell_soon_count,
       SUM(CASE WHEN p.bucket = 'healthy'          THEN 1 ELSE 0 END) AS healthy_count,
       SUM(CASE WHEN p.bucket = 'insufficient'     THEN 1 ELSE 0 END) AS insufficient_data_count,
       AVG(p.avg_monthly_maintenance)                             AS avg_monthly_maintenance,
       AVG(p.avg_cost_per_mile)                                   AS avg_cost_per_mile
  FROM per_vin p
  LEFT JOIN dims d ON p.vin = d.vin
 GROUP BY d.model, d.model_year
 ORDER BY d.model, d.model_year

-- QUERY_BREAK --

-- ════════════════════════════════════════════════════════════════════════════
-- QUERY 4: topCrossovers (top 100 soonest crossovers, with series)
-- ════════════════════════════════════════════════════════════════════════════
WITH {per_vin_pipeline},
per_vin AS (
  SELECT vin, n, slope, intercept, r_squared, last_y,
         {per_vin_k_case} AS months_to_crossover
    FROM per_vin_fit
),
top_100 AS (
  SELECT vin, n, slope, intercept, r_squared, last_y, months_to_crossover
    FROM per_vin
   WHERE months_to_crossover IS NOT NULL
   ORDER BY months_to_crossover ASC, vin ASC
   LIMIT 100
),
dims AS (
  SELECT vin, model, model_year
    FROM adp_{stage}_dimensions.vins
),
series_for_top AS (
  SELECT m.vin,
         m.year_month,
         m.y AS maintenance_cost
    FROM monthly m
   WHERE m.vin IN (SELECT vin FROM top_100)
)
SELECT t.vin,
       d.model,
       d.model_year,
       t.months_to_crossover,
       t.last_y                                            AS current_monthly_maintenance,
       t.r_squared,
       s.year_month,
       s.maintenance_cost
  FROM top_100 t
  LEFT JOIN dims d ON t.vin = d.vin
  JOIN series_for_top s ON t.vin = s.vin
 ORDER BY t.months_to_crossover ASC, t.vin ASC, s.year_month ASC
