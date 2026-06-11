# OEM1 Catalog Gap Analysis

**Date:** 2026-06-02
**Scope:** OEM1 gRPC streaming feed integration (Phase 4 of transform-manifest staging E2E)

## Executive Summary

OEM1 provides a comprehensive telematics feed covering fleet management, engine diagnostics, and driver behavior. The OEM1 dictionary includes approximately 155 telemetry signals and events. After normalizing to CMS semantic categories, the CMS base catalog (70 signals pre-Phase-4) covers approximately 80% of OEM1's deliverable signals. The remaining ~30 uncovered OEM1 signals fall into three categories:

1. **OEM-specific fleet-tier reporting** (e.g., fleet maintenance schedules, regulatory compliance signals unique to OEM1's enterprise program)
2. **Extended diagnostics not prioritized for v1** (e.g., auxiliary sensor networks, redundant fault trees)
3. **Deferred signals** — cataloged in the manifest as `metadata.deferred_signals[]` for transparency without blocking ingestion

## Catalog Additions in Phase 4

As part of the OEM1 v1 integration, the CMS base catalog was extended with 9 signals and 13 events that apply universally across OEM sources. These additions close the gap for the most frequently accessed OEM1 data points.

### 9 Signals Added to BASE Catalog

| Signal | Category | Unit | Description | OEM1 Motivation |
|--------|----------|------|-------------|------------------|
| `EngineOilTemp` | core_telemetry | °F | Engine oil temperature | Real-time engine health monitoring; earlier degradation detection |
| `TractionControlActive` | safety | boolean | Traction control active flag | Driver behavior analytics; harsh cornering quantification |
| `PowerTakeOffStatus` | powertrain | boolean | PTO engaged (commercial vehicles) | Idling analysis for commercial fleets; fuel consumption normalization |
| `ImpactStatus` | safety | boolean | Vehicle impact detected | Collision detection; automated incident reporting |
| `TotalEngineTimeIdle` | core_telemetry | hours | Cumulative idle time | Fleet fuel optimization; operational efficiency analysis |
| `WaterInFuelStatus` | maintenance | boolean | Water in fuel alert | Preventive maintenance; fuel system health |
| `YawRate` | driving | rad/s | Yaw rotation rate | Advanced vehicle dynamics; safety event modeling |
| `HarshCorneringMaxLateralAccel` | driving | G | Peak lateral acceleration during turn | Driver coaching; collision risk prediction |
| `HarshMaxLongitudinalAccel` | driving | G | Peak longitudinal acceleration during drive event | Harsh acceleration/braking detection; driver safety scores |

### 13 Events Added to BASE Catalog + 2 New Categories

| Event | Category (NEW if marked) | Severity | Description | OEM1 Motivation |
|-------|----------|----------|-------------|------------------|
| `washer_fluid_low` | maintenance | MEDIUM | Washer fluid level low | Preventive service alerts |
| `trailer_brake_disconnected` | maintenance | HIGH | Trailer brake connection lost | Safety-critical for commercial operations |
| `check_engine_light` | maintenance | HIGH | Check engine indicator illuminated | OBD-II diagnostic alert |
| `airbag_warning` | safety | CRITICAL | Airbag system fault | Occupant protection system status |
| `def_level_low` | maintenance | MEDIUM | Diesel exhaust fluid level low | Commercial fleet compliance (EPA emissions) |
| `antilock_brake_fault` | safety | CRITICAL | ABS system malfunction | Braking safety system status |
| `service_steering` | safety | HIGH | Power steering service required | Steering system health |
| `lighting_system_failure` | safety | HIGH | Exterior/interior lighting fault | Visibility and safety compliance |
| `powertrain_malfunction` | maintenance | HIGH | Generic powertrain system error | Catch-all for complex multi-sensor faults |
| `charge_system_fault` | maintenance | HIGH | Battery/charging system failure | Vehicle electrical health |
| `water_in_fuel` | maintenance | HIGH | Water contamination in fuel tank | Fuel quality; injector protection |
| `power_take_off_engaged` | **commercial** (NEW) | MEDIUM | Power take-off system active | Commercial fleet duty-cycle tracking |
| `excessive_idle` | **commercial** (NEW) | MEDIUM | Vehicle idling beyond threshold | Fuel efficiency; idle-time penalties |

Two new event categories were introduced to organize OEM1's commercial fleet telemetry:
- **`commercial.power_take_off_engaged`** — PTO device usage (e.g., hydraulic dump truck, refrigerated trailer)
- **`commercial.excessive_idle`** — Idle duration threshold breach for fleet optimization

### 5 Trip-Table Columns Added

The `cms-staging-storage-trips` table was extended with five new columns to support OEM1-specific efficiency metrics:

| Column | Type | Description | OEM1 Use Case |
|--------|------|-------------|---------------|
| `engine_time_total_seconds` | INTEGER | Cumulative engine-on time during trip | Fuel efficiency normalization |
| `engine_time_idle_seconds` | INTEGER | Time spent idling during trip | Idle fuel penalty quantification |
| `fuel_consumed_liters` | FLOAT | Total fuel consumed during trip | Cost tracking; efficiency benchmarking |
| `fuel_consumed_idle_liters` | FLOAT | Fuel used during idle periods | Idle impact quantification |
| `max_speed_mph` | FLOAT | Peak speed during trip | Driver behavior; speed compliance analysis |

These columns are nullable for backward compatibility with existing (non-OEM1) trip data. DynamoDB scans filter by presence when OEM1-specific reporting is needed.

## Deferred Signals (No Catalog Match)

Approximately 30 OEM1 signals fall outside the v1 scope. These are declared in the `oem1-transform.json` manifest under `metadata.deferred_signals[]` for full transparency. Examples include:

- **OEM1-unique fleet-tier reporting**: maintenance schedules managed by OEM1's enterprise service portal, regulatory compliance signals specific to OEM1's supply-chain program
- **Redundant fault trees**: OEM1 sends multiple fault representations for the same underlying issue (e.g., generic and vendor-specific DTC codes); only the canonical one is used in v1
- **Extended sensor networks**: OEM1 can relay data from third-party hardware (e.g., tire-pressure monitoring vendors, trailer weight scales) when configured; these are out of scope for v1

No signals are silently dropped. Every OEM1 signal is either:
- Mapped to the CMS catalog (via `oem1-transform.json` `signal_mappings[]`), or
- Listed in `metadata.deferred_signals[]` with a reason code

This ensures the manifest serves as the source of truth for what OEM1 is sending and what CMS is doing with it.

## Integration Notes

### Manifest-Driven Extensibility

The 22 catalog additions (9 signals + 13 events) are **universal to all OEMs**, not OEM1-specific. The OEM1 transform manifest (`services/data_processing/manifests/oem1-transform.json`) references these catalog entries and may also define OEM1-specific event mappings with per-OEM severity normalization rules (e.g., "HIGH + engine DTC → CRITICAL").

Future OEM integrations (OEM2, Tesla, Geotab, etc.) will also reference the same base catalog, reducing friction and enabling cross-OEM fleet analytics.

### Signal Coverage Summary

| Metric | Count |
|--------|-------|
| OEM1 telemetry signals (estimated) | ~155 |
| CMS base catalog before Phase 4 | 70 |
| Signals added in Phase 4 | 9 |
| **CMS base catalog after Phase 4** | **79** |
| OEM1 signals mapped to CMS catalog | ~125 (80%) |
| OEM1 signals deferred | ~30 (20%) |

The 80% coverage target for v1 is satisfied. Deferred signals are tracked in the manifest for v1.1 prioritization.

### Operational Considerations

1. **Backward Compatibility**: Existing simulator and FleetWise Edge data continue to work unchanged. New columns in the trips table are NULL-tolerant; existing queries unaffected.
2. **Manifest Evolution**: Adding a signal to the catalog does NOT require Flink redeploy. The manifest layer handles any future signal add-ons or remapping.
3. **Cross-OEM Reporting**: With a unified catalog and manifest-driven mapping, dashboards and ML models can now operate across OEM boundaries (e.g., "harsh braking incidents" work the same for all OEMs).

## References

- **Transform Manifest Schema**: `services/data_processing/transform-manifest-schema.json` (v2.1.0)
- **OEM1 Manifest**: `services/data_processing/manifests/oem1-transform.json`
- **Signal Catalog**: `services/data_processing/signal-catalog.json` (updated post-Phase-4)
- **Event Catalog**: `services/data_processing/event-catalog.json` (updated post-Phase-4)
- **Trip Table Definition**: `deployment/stacks/storage_stack.py` (trips table columns)
