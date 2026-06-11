# OEM1 DTC Pipeline — Preliminary Reference

> **Status**: PRELIMINARY DRAFT (Group 3). Group 6 will expand this into the full operator runbook after Phase C deploy + e2e validation.

Spec: `2026-06-09-cms-oem1-dtc-engine-light-pipeline`
Decisions of record: `.kiro/specs/2026-06-09-cms-oem1-dtc-engine-light-pipeline/decisions.md § "2026-06-10 PM — Phase A re-revision: Path ε CONFIRMED"`

---

## Overview

OEM1 emits diagnostic fault events via the OEM1 vendor's VHA Custom Diagnostic Event path (Path ε). These events flow through the connector → Flink OEMTelemetryProcessor (manifest extraction) → Flink MaintenanceProcessor (canonical-indicator handler) → `cms-<stage>-storage-dtc-history` + `cms-<stage>-vfo-action-queue`.

**Routing (Fix Group 3.1)**: The manifest contains one catch-all entry matching `stringLabelEndsWith: ":custom:vha-diagnostics-processed-event"`, producing `cms_event_type = "cms.vha_diagnostic_event"`. The 4-state sub-classification is performed inside `MaintenanceProcessor.handleCanonicalIndicatorEvent` by inspecting `(indicator_state, dtc_clear, dtc_code)`:

| `indicator_state` | `dtc_clear` | `dtc_code` | `dtc-history.status` outcome |
|---|---|---|---|
| `ON` | absent/empty | non-empty | `ACTIVE` |
| `ON` | absent/empty | empty | `ACTIVE_NO_DTC` |
| `OFF` | `Y` | any | `CLEARED` (updates existing rows) |
| `ON` | `Y` | any | `DTC_CLEARED_INDICATOR_ACTIVE` (updates existing rows) |

```
gRPC feed (OEM1 vendor endpoint)
  └─ connector (oem1/connector.py)          ← string_label TriggeredEvent decoded (Group 2)
       └─ cms-telemetry-preprocessed (MSK)
            └─ OEMTelemetryProcessor (Flink) ← manifest stringLabelEndsWith matcher + extraction (Group 2/3)
                 └─ cms-telemetry-maintenance (MSK / KDA internal)
                      └─ MaintenanceProcessor (Flink) ← handleCanonicalIndicatorEvent (Group 3)
                           ├─ cms-<stage>-storage-dtc-history  (DDB)
                           └─ cms-<stage>-vfo-action-queue     (DDB, CRITICAL only)
```

---

## dtc-history.status enum

The `status` column in `cms-<stage>-storage-dtc-history` carries one of four values:

### `ACTIVE`
- **Written by**: FWE threshold path (`MaintenanceProcessor.storeActiveDtc`) AND OEM1 canonical path (`handleCanonicalIndicatorEvent` — sub-state: `indicator_state=ON`, no `DtcClear` tag, `dtc_code` non-empty)
- **Semantics**: A DTC code is active and the indicator is on. The vehicle has a known fault.
- **Reader contract**: VFO triage classifier reads ACTIVE rows when generating fault explanations. CVX agent reads `description` and `agentResponse` for driver-facing fault summaries.

### `CLEARED`
- **Written by**: FWE threshold path (when the warning condition no longer fires) AND OEM1 canonical path (`handleCanonicalIndicatorEvent` — sub-state: `indicator_state=OFF`, `DtcClear=Y`)
- **Semantics**: The warning has been cleared — both the indicator is off and the DTC is no longer active.
- **Reader contract**: `clearedDate` is set on transition. Downstream readers should treat CLEARED rows as historical (not current fault).

### `ACTIVE_NO_DTC` *(NEW — OEM1 only)*
- **Written by**: OEM1 canonical path only (`handleCanonicalIndicatorEvent` — sub-state: `indicator_state=ON`, no `DtcClear` tag, `dtc_code` empty)
- **Semantics**: An indicator warning is active but no DTC code was reported. The warning fired without an associated OBD-II code (e.g. low washer fluid, trailer brake connection warning). `code` column is empty string.
- **Reader contract**: Consumers must tolerate `code = ""`. VFO classifier should surface the `indicator` field for agent context instead of the DTC code. FWE never writes this status.

### `DTC_CLEARED_INDICATOR_ACTIVE` *(NEW — OEM1 only)*
- **Written by**: OEM1 canonical path only (`handleCanonicalIndicatorEvent` — sub-state: `indicator_state=ON`, `DtcClear=Y`)
- **Semantics**: The specific DTC code was cleared (OBD scanner cleared it), but the warning indicator is still on — the underlying condition persists. This is distinct from CLEARED because the driver still sees a dashboard warning.
- **Reader contract**: Downstream consumers (VFO triage classifier) must not treat this as a resolved fault. The indicator is still on; further diagnosis is required. FWE never writes this status.

---

## Severity vocabulary

OEM1 vendor `Severity` tag maps to CMS severity as follows (per decisions.md § B.ε.3):

| Vendor `Severity` tag | CMS `severity` column | Action queue triggered? |
|---|---|---|
| `URGENT` | `CRITICAL` | Yes — PENDING row in `vfo-action-queue` |
| `HIGH` | `HIGH` | No |
| `MEDIUM` | `MEDIUM` | No |
| `LOW` | `LOW` | No |
| missing / unknown | `HIGH` | No |

Only `CRITICAL` triggers a `vfo-action-queue` write (`source=dtc-critical`, `sourceTag=oem1-uds-dtc`).

---

## Source disambiguator

| Pipeline | `source` column value | `sourceTag` (action queue) |
|---|---|---|
| FWE threshold path | `flink-maintenance-processor` | `dtc-threshold` |
| OEM1 canonical path | `oem1-uds-dtc` | `oem1-uds-dtc` |

---

## New dtc-history columns (OEM1-only, additive, nullable)

These columns are written by the OEM1 canonical path and are absent from FWE-sourced rows. Downstream consumers must tolerate their absence.

| Column | Type | Source field |
|---|---|---|
| `indicator` | S | `metrics[0].indicatorValue.wellKnownIndicator` |
| `indicator_extra_code` | S | `metrics[0].indicatorValue.additionalInfo.value` (vendor hex) |
| `agentResponse` | S | `metrics[0].tags[Symptom]` (symptom_text) |
| `symptom_key` | N | `metrics[0].tags[symptomKey]` |
| `customer_action_key` | N | `metrics[0].tags[customerActionKey]` |
| `category` | S | `metrics[0].tags[Category]` |
| `cloud_arrival_time` | S | `metrics[0].tags[CloudArrivalTime]` |
| `vha_read_time` | S | `metrics[0].tags[VHAReadTime]` |
| `alert_trace_id` | S | `metrics[0].tags[ALERT_TRACE_ID]` |

| `ACTIVE_NO_DTC` | FWE never writes; OEM1 only | VFO classifier, CVX agent |
| `DTC_CLEARED_INDICATOR_ACTIVE` | FWE never writes; OEM1 only | VFO classifier |

> **Downstream consumer note**: The VFO triage classifier may need updates to surface `ACTIVE_NO_DTC` and `DTC_CLEARED_INDICATOR_ACTIVE` rows appropriately. This is tracked as a separate spec if the classifier requires changes — out of scope for this pipeline spec.

---

## References

- Decisions: `.kiro/specs/2026-06-09-cms-oem1-dtc-engine-light-pipeline/decisions.md § "2026-06-10 PM — Phase A re-revision: Path ε CONFIRMED"`
- Source disambiguator reference: `modules/flink/src/main/java/com/cms/telemetry/FWTelemetryProcessor.java` (FWE path, `sourceTag=fwe-uds-dtc`)
- OEM1 handler: `modules/flink/src/main/java/com/cms/telemetry/MaintenanceProcessor.java` `handleCanonicalIndicatorEvent`
- Tests: `modules/flink/src/test/java/com/cms/telemetry/MaintenanceProcessorOEMCanonicalDtcTest.java`
