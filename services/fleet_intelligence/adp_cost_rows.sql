-- adp_cost_rows.sql — D2 query fixture for services/fleet_intelligence/adp_source.fetch_cost_rows
--
-- This file is the load-bearing contract for T1.5 and T2.3.
-- Any change to the SQL must update this file AND update the T1.5 test in the same commit.
--
-- Parameterisation (spec § D2, corrected 2026-09-12, redesigned 2026-09-12):
--
--   {stage}      — Python str.replace interpolation at call time (not a bind var).
--                  Validated against {"staging","prod"} by _require_stage() before use.
--
--   ?            — Athena ExecutionParameters POSITIONAL placeholder.
--                  Every value MUST arrive pre-quoted as a SQL literal, e.g. '2025-09-10'
--                  and 'MRDN0000000000005', because Athena evaluates substituted values
--                  as SQL expressions (Measurement 2, decisions.md D2 redesign).
--                  An unquoted date is substituted as arithmetic and the query fails
--                  TYPE_MISMATCH: Cannot cast integer to date.
--
--   {vin_list}   — Python str.replace token expanded to the correct number of ?
--                  before the SQL is submitted.  Each vin bind is a pre-quoted
--                  SQL literal, e.g. 'MRDN0000000000005'.
--
-- ExecutionParameters ORDER (positional, per CTE in fixture order):
--   maint:  ['window_start', 'vin_1', 'vin_2', ..., 'vin_N']
--   energy: ['window_start', 'vin_1', 'vin_2', ..., 'vin_N']
--   charge: ['window_start', 'vin_1', 'vin_2', ..., 'vin_N']
--   Total:  3 * (1 + N) parameters
--
-- The `keys` UNION CTE is the (vin × year_month) spine — a vehicle can have charging
-- events in a month with no maintenance and no logged miles, so any single starting
-- table would drop rows the CPM view needs.  LEFT JOINs + COALESCE(…, 0.0) give the
-- correct CPM semantic for empty-component months (spec § D2 + v1 § D2).
--
-- vin IN ({vin_list}) is placed in EACH CTE so the filter pushes down and Athena
-- does not aggregate 1.86M vins before the join (Measurement 3, decisions.md D2 redesign).
WITH maint AS (
  SELECT vin,
         DATE_FORMAT(service_date, '%Y-%m') AS year_month,
         SUM(CAST(total_cost_usd AS DOUBLE)) AS maintenance_cost_usd
    FROM adp_{stage}_service_records.service_records
   WHERE service_date >= CAST(? AS DATE)
     AND vin IN ({vin_list})
   GROUP BY vin, DATE_FORMAT(service_date, '%Y-%m')
),
energy AS (
  SELECT vin,
         DATE_FORMAT(usage_date, '%Y-%m') AS year_month,
         SUM(total_miles_driven) AS total_miles
    FROM adp_{stage}_energy_usage.energy_usage
   WHERE usage_date >= CAST(? AS DATE)
     AND vin IN ({vin_list})
   GROUP BY vin, DATE_FORMAT(usage_date, '%Y-%m')
),
charge AS (
  SELECT vin,
         DATE_FORMAT(start_time, '%Y-%m') AS year_month,
         SUM(CAST(cost_usd AS DOUBLE)) AS fuel_cost_usd
    FROM adp_{stage}_charging_sessions.charging_sessions
   WHERE start_time >= from_iso8601_timestamp(? || 'T00:00:00Z')
     AND vin IN ({vin_list})
   GROUP BY vin, DATE_FORMAT(start_time, '%Y-%m')
),
keys AS (
  SELECT DISTINCT vin, year_month FROM maint
  UNION SELECT DISTINCT vin, year_month FROM energy
  UNION SELECT DISTINCT vin, year_month FROM charge
)
SELECT k.vin,
       k.year_month,
       COALESCE(m.maintenance_cost_usd, 0.0) AS maintenance_cost,
       COALESCE(c.fuel_cost_usd, 0.0) AS fuel_cost,
       COALESCE(e.total_miles, 0.0) AS total_miles
  FROM keys k
  LEFT JOIN maint m ON k.vin = m.vin AND k.year_month = m.year_month
  LEFT JOIN energy e ON k.vin = e.vin AND k.year_month = e.year_month
  LEFT JOIN charge c ON k.vin = c.vin AND k.year_month = c.year_month
 WHERE k.vin IS NOT NULL
