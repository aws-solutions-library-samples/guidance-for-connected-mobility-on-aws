// Engineering Knowledge Base corpus.
// In production these are markdown documents in S3 indexed by Bedrock Knowledge Base.
// For the demo, exported as string constants the agent script "retrieves" and
// the EvidencePanel renders.

export interface KbDocument {
  docId: string;
  title: string;
  category: 'thermal-spec' | 'pfmea' | 'datasheet' | 'prd-draft' | 'test-plan';
  modelLine: string;
  lastModified: string;
  content: string;
}

export const KB_DOCUMENTS: KbDocument[] = [
  // ============================================================================
  // BE 6 THERMAL MANAGEMENT SPEC (the one with the stale assumption)
  // ============================================================================
  {
    docId: 'be6-thermal-management-spec',
    title: 'BE 6 — Battery Thermal Management Specification',
    category: 'thermal-spec',
    modelLine: 'BE 6',
    lastModified: '2024-08-22',
    content: `# BE 6 — Battery Thermal Management Specification

**Document ID:** BE6-TMS-2024-001 | **Revision:** 1.4 | **Owner:** Powertrain Thermal CoE

## 1. Operating envelope

The BE 6 high-voltage battery pack is qualified for the following operating envelope:

| Parameter | Min | Nominal | Max |
|---|---|---|---|
| Ambient temperature | -20°C | 25°C | +45°C |
| Cell temperature (operating) | -10°C | 25°C | **45°C** |
| Cell temperature (peak transient, ≤60s) | — | — | 50°C |

> **Cell thermal limit reference:** Voltrix NMC811 datasheet **rev 2.1** (effective 2024-04-15), max continuous operating temperature 45°C.

## 2. Cooling architecture

- **Passive convection** via finned pack housing
- **Low-flow liquid cooling loop** (8 L/min nominal) with cabin HVAC integration
- **No active phase-change material**; thermal mass relies on coolant volume + pack mass

## 3. Margin policy

A nominal 3°C margin is maintained between the cell thermal limit and the maximum
expected pack temperature under high-ambient + fast-charge stack-up. This margin was
sized against the 45°C limit referenced in §1.

## 4. Open items

- (CLOSED 2024-06) Thermal de-rating curve calibration
- (CLOSED 2024-07) Cabin HVAC mode arbitration
- (DEFERRED — see PFMEA-BE6-bat-cool RPN 240) Mitigation for cell temperature exceeding supplier thermal limit in extreme ambient conditions

## 5. Change log

| Rev | Date | Change |
|---|---|---|
| 1.0 | 2024-01-30 | Initial release |
| 1.2 | 2024-04-22 | Updated coolant flow rate to 8 L/min |
| 1.4 | 2024-08-22 | Margin policy clarified (no functional change) |

> **NOTE FROM ENGINEER PERSONA AGENT (auto-flagged 2026-05-18):** Voltrix datasheet rev 2.3 (effective 2025-07-01) lowered the cell thermal limit to 42°C. This BE 6 specification has not been updated to reflect the change. The 3°C margin policy in §3 was sized against 45°C; against 42°C the effective margin is 0°C in Maharashtra/Gujarat summer peak ambient.
`,
  },

  // ============================================================================
  // PFMEA — battery cooling (the deferred RPN 240)
  // ============================================================================
  {
    docId: 'be6-pfmea-battery-cooling',
    title: 'BE 6 — PFMEA: Battery Cooling Subsystem',
    category: 'pfmea',
    modelLine: 'BE 6',
    lastModified: '2024-09-15',
    content: `# BE 6 — Process FMEA: Battery Cooling Subsystem

**PFMEA ID:** BE6-PFMEA-bat-cool | **Revision:** 2.0 | **Cross-functional team:** Thermal CoE, Manufacturing, Quality

## Selected high-RPN entries

### RPN 240 — Cell temperature exceeds supplier thermal limit in extreme ambient

| Field | Value |
|---|---|
| Failure mode | Cell continuous operating temperature exceeds supplier-rated thermal limit |
| Effect | Accelerated SoH degradation; potential thermal runaway risk if compounded with charging stress |
| Severity (S) | 8 |
| Cause | Pack cooling capacity insufficient to maintain margin against supplier thermal limit when ambient peaks ≥ 44°C and load is sustained |
| Occurrence (O) | 5 — historically rare; expected to increase with hot-region market expansion |
| Detection (D) | 6 — telemetry-based, post-hoc only |
| **RPN** | **240** |
| **Status** | **DEFERRED** — mitigation deferred to BE.07 program; current BE 6 risk accepted on basis of historical hot-region penetration <8% |

### Recommended actions (deferred)

1. Increase pack thermal mass via phase-change material insert
2. Active liquid cooling channel addition  
3. Supplier diversification to higher-thermal-limit cell

> **NOTE FROM ENGINEER PERSONA AGENT (auto-flagged 2026-05-18):** Hot-region penetration is currently 65% of BE 6 cohort (Maharashtra + Gujarat + Tamil Nadu). The "<8%" assumption from 2024 is no longer valid. RPN 240 deferral should be revisited.
`,
  },

  // ============================================================================
  // VOLTRIX DATASHEET REV 2.3 (the smoking gun)
  // ============================================================================
  {
    docId: 'supplier-voltrix-datasheet-rev2-3',
    title: 'Voltrix NMC811 Cell — Datasheet Rev 2.3',
    category: 'datasheet',
    modelLine: 'BE 6',
    lastModified: '2025-07-01',
    content: `# Voltrix Energy Systems — NMC811 Cell Datasheet

**Cell SKU:** VTX-NMC811-79 | **Revision:** 2.3 | **Effective:** 2025-07-01 | **Supersedes:** Rev 2.1 (2024-04-15)

## Electrical specifications

| Parameter | Value |
|---|---|
| Nominal capacity | 79 kWh (pack-level) |
| Cell chemistry | NMC811 |
| Nominal voltage | 3.7V/cell |
| Charge cutoff | 4.2V/cell |

## Thermal specifications

| Parameter | Rev 2.1 (deprecated) | **Rev 2.3 (current)** |
|---|---|---|
| Optimal operating temperature | 25°C | 25°C |
| **Maximum continuous operating temperature** | **45°C** | **42°C** |
| Maximum peak transient (≤60s) | 50°C | 48°C |
| Thermal de-rating onset | 38°C | 36°C |

## Rev 2.3 change rationale

Cycle-life characterization at 45°C ambient, completed Q2 2025, indicated accelerated
capacity fade beyond original Rev 2.1 model. Field returns from select markets confirmed
characterization findings. **Maximum continuous operating temperature reduced from 45°C
to 42°C effective Q3 2025 production.**

Customers integrating the VTX-NMC811-79 cell into thermal management systems with margin
sized against 45°C limit are advised to revalidate and update affected design documents.

## Affected cell lots

All cell lots produced from cell-line CL-VTX-2025-Q3 onwards conform to Rev 2.3.
`,
  },

  // ============================================================================
  // CELLPRIME DATASHEET (for option C reference)
  // ============================================================================
  {
    docId: 'supplier-cellprime-datasheet-rev1-4',
    title: 'CellPrime NMC811 Cell — Datasheet Rev 1.4',
    category: 'datasheet',
    modelLine: 'BE 6',
    lastModified: '2025-03-12',
    content: `# CellPrime Industries — NMC811 Cell Datasheet

**Cell SKU:** CPR-NMC811-79 | **Revision:** 1.4 | **Effective:** 2025-03-12

## Electrical specifications

| Parameter | Value |
|---|---|
| Nominal capacity | 79 kWh (pack-level) |
| Cell chemistry | NMC811 |
| Nominal voltage | 3.7V/cell |
| Charge cutoff | 4.2V/cell |

## Thermal specifications

| Parameter | Value |
|---|---|
| Optimal operating temperature | 28°C |
| **Maximum continuous operating temperature** | **48°C** |
| Maximum peak transient (≤60s) | 55°C |
| Thermal de-rating onset | 42°C |

## Commercial

- BOM cost +3.8% vs Voltrix VTX-NMC811-79
- Lead time: 14 weeks (vs 8 weeks Voltrix)
- Qualified for Acme Motors programs since 2024-09
`,
  },

  // ============================================================================
  // BE.07 THERMAL PRD DRAFT (where the agent writes back)
  // ============================================================================
  {
    docId: 'be07-thermal-management-prd-draft',
    title: 'BE.07 — Battery Thermal Management PRD (Draft v0.7)',
    category: 'prd-draft',
    modelLine: 'BE.07',
    lastModified: '2026-04-30',
    content: `# BE.07 — Battery Thermal Management PRD

**Document ID:** BE07-TMS-PRD-001 | **Status:** DRAFT v0.7 | **Owner:** Powertrain Thermal CoE

## 1. Scope

Battery thermal management for the BE.07 platform, succeeding BE 6.

## 2. Carry-forward from BE 6

- Pack architecture: BE 6 baseline (passive convection + low-flow liquid loop)
- Cell supplier: Voltrix VTX-NMC811-79 (primary)
- Operating envelope: BE 6 baseline (-20°C to +45°C ambient)

## 3. Open questions

- **OQ-1 (HIGH):** Validate thermal margin against 2026 supplier cell datasheets. _Carry-forward of BE 6 thermal architecture assumes 45°C cell limit; need to confirm against current Voltrix datasheet revision._
- **OQ-2 (MEDIUM):** BE.07 hot-region market mix is projected 70%+ vs BE 6 65%; confirm thermal architecture is sized for elevated hot-region duty cycle.
- **OQ-3 (LOW):** Charging-induced thermal stack-up in 80–100% SoC band — recharacterise for BE.07 fast-charge curve.

## 4. Validation plan

See \`be07-thermal-validation-test-plan.md\`. 25-vehicle BE.07 test fleet split across
hot-region road testing, climate chambers, and cold-region operation.

## 5. Change log

| Rev | Date | Change |
|---|---|---|
| 0.5 | 2026-02-12 | Initial draft |
| 0.7 | 2026-04-30 | Open questions formalized |
`,
  },

  // ============================================================================
  // BE.07 VALIDATION TEST PLAN
  // ============================================================================
  {
    docId: 'be07-thermal-validation-test-plan',
    title: 'BE.07 — Thermal Validation Test Plan',
    category: 'test-plan',
    modelLine: 'BE.07',
    lastModified: '2026-03-22',
    content: `# BE.07 — Thermal Validation Test Plan

**Document ID:** BE07-TVP-001 | **Revision:** 0.4 | **Owner:** Validation Engineering

## Test fleet

25 BE.07 pre-production vehicles, instrumented (full CAN dump + auxiliary thermal sensors),
distributed across regions:

| Region | Count | Purpose |
|---|---|---|
| Maharashtra-Hot | 6 | Hot-region road testing, daily commute profile |
| Gujarat-Hot | 5 | Hot-arid validation, peak ambient stress |
| Tamil-Nadu-Hot | 5 | Hot-humid validation |
| Punjab-Cool | 5 | Cold-region operation, cold-soak start |
| Karnataka-Moderate | 4 | Baseline reference |

## Protocols

1. **Daily-cycle thermal stress** — repeat real-world charge/drive cycles, log peak cell temp
2. **Climate chamber sweep** — controlled ambient -25°C to +50°C, quasi-static + dynamic load
3. **Fast-charge thermal stack-up** — DC fast-charge from 20% → 80% at high ambient
4. **Cycle-life acceleration** — 65,000 km equivalent over 6 months

## Acceptance criteria

| Criterion | Threshold |
|---|---|
| Peak cell temperature | ≤ supplier thermal_limit − 3°C margin |
| SoH degradation | ≤ 0.85% per month projected over 8-year warranty |
| Thermal events (defined as peak > thermal_limit) | 0 in 6-month protocol |

## Open items

- Awaiting alignment with PRD v0.7 OQ-1 (supplier datasheet revision check)
`,
  },
];

export const getKbDocument = (docId: string) =>
  KB_DOCUMENTS.find((d) => d.docId === docId);
