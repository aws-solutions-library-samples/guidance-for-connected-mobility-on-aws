// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Diagnostics verdict utility — pure unit tests (DX2, DX3).
 *
 * Spec: `.kiro/specs/2026-09-02-cms-diagnostics-platform` test-ID table, rows DX2 and DX3.
 * Issue: `issues/2026-09-02-event-catalog-duplicate-dtc-codes-conflicting-verdicts/`
 *
 * ── DX2 ────────────────────────────────────────────────────────────────────────
 * Verdict tier = highest severity_hint across all matching DTCs; order-independent.
 *
 * Amendment (2026-09-02): order-independence is only satisfiable when `dtc_code`
 * is a unique catalog key. With duplicate keys the verdict follows scan order
 * (non-deterministic on DynamoDB), so the test ALSO asserts catalog-wide uniqueness —
 * not merely for the codes under test.
 *
 * The 4 codes that currently carry conflicting entries are:
 *   C1234, P0217, C0035, P0562
 * (see issue report for details).  T2A.4 is the fix; this test asserts the invariant
 * so that once T2A.4 ships, re-introduction is prevented.
 *
 * ── DX3 ────────────────────────────────────────────────────────────────────────
 * A DTC code absent from the catalog → result is `{ tier: 'Unrecognised' }`.
 * Critically: **no severity field is produced** — not merely a different label.
 * The returned object must not contain `severity`, `severity_hint`, or any field
 * that could be mistaken for a health verdict.
 *
 * ── RED PHASE ──────────────────────────────────────────────────────────────────
 * `@/utils/diagnosticsVerdict` does not exist yet (Group 3, T3.2 creates it).
 * This file is expected to fail with "Cannot find module …" until that happens.
 * Do NOT create a stub, do NOT weaken assertions to get green.
 *
 * ── PURE UNIT TEST ─────────────────────────────────────────────────────────────
 * No DOM, no `render`, no `fetch`, no React. This is logic-only.
 */

import { describe, it, expect } from 'vitest';

// ── Import under test ─────────────────────────────────────────────────────────
//
// This module does not exist yet. The expected failure is:
//   Error: Cannot find module '@/utils/diagnosticsVerdict' or its corresponding type declarations
//
// Do not create this module. Do not mock it. The test file must fail at this import.
//
import {
  computeVerdict,
  assertCatalogDtcCodesUnique,
} from '@/utils/diagnosticsVerdict';

// ── Types ─────────────────────────────────────────────────────────────────────
//
// Mirror the shape documented in docs/tech.md § "GET /api/v1/event-catalog".
// These are used to build fixtures; they impose no coupling to the implementation.

interface CatalogEntry {
  event_id: string;
  category: string;
  severity: number;        // 4 = CRITICAL (P0), 3 = HIGH (P1), 2 = MEDIUM (P2), 1 = LOW (P3)
  severity_hint: string;   // "P0" | "P1" | "P2" | "P3"
  description: string;
  trigger_signal: string;
  threshold_operator: string;
  threshold_value: number;
  dtc_code?: string;       // Optional — absent on non-DTC events
}

interface DtcReading {
  code: string;            // e.g. "P0217"
  status: string;          // e.g. "CONFIRMED_DTC"
  ecu?: string;
}

// ── Catalog fixtures ──────────────────────────────────────────────────────────
//
// A minimal, fully-unique test catalog for DX2 assertions.
// Each `dtc_code` appears exactly once — the uniqueness invariant that DX2 requires.

const CATALOG_P0: CatalogEntry = {
  event_id: 'maintenance.coolant_critical_overheat',
  category: 'safety',
  severity: 4,
  severity_hint: 'P0',
  description: 'Engine coolant critically overheated — stop driving, let engine cool.',
  trigger_signal: 'engine_coolant_temp',
  threshold_operator: '>',
  threshold_value: 130,
  dtc_code: 'P0217',
};

const CATALOG_P1: CatalogEntry = {
  event_id: 'safety.antilock_brake_fault',
  category: 'safety',
  severity: 3,
  severity_hint: 'P1',
  description: 'Anti-lock braking system fault — braking distances may increase.',
  trigger_signal: 'abs_fault',
  threshold_operator: '==',
  threshold_value: 1,
  dtc_code: 'C0035',
};

const CATALOG_P2: CatalogEntry = {
  event_id: 'maintenance.system_voltage_low_minor',
  category: 'maintenance',
  severity: 2,
  severity_hint: 'P2',
  description: 'System voltage slightly low — alternator may be degrading.',
  trigger_signal: 'battery_voltage',
  threshold_operator: '<',
  threshold_value: 12.2,
  dtc_code: 'P0562',
};

const CATALOG_P3: CatalogEntry = {
  event_id: 'maintenance.brake_pad_wear',
  category: 'maintenance',
  severity: 1,
  severity_hint: 'P3',
  description: 'Brake pad wear sensor indicates pads need replacement.',
  trigger_signal: 'brake_pad_wear',
  threshold_operator: '<',
  threshold_value: 3,
  dtc_code: 'C1235',
};

// Non-DTC event — no dtc_code field. Should not interfere with DTC-keyed lookups.
const CATALOG_NO_DTC: CatalogEntry = {
  event_id: 'safety.door_open_while_moving',
  category: 'safety',
  severity: 3,
  severity_hint: 'P1',
  description: 'Door opened while vehicle is in motion.',
  trigger_signal: 'door_status',
  threshold_operator: '==',
  threshold_value: 1,
  // dtc_code intentionally absent
};

// UNIQUE test catalog — all dtc_codes are distinct.
const UNIQUE_CATALOG: CatalogEntry[] = [
  CATALOG_P0,
  CATALOG_P1,
  CATALOG_P2,
  CATALOG_P3,
  CATALOG_NO_DTC,
];

// ── Duplicate-code fixtures (for DX2 uniqueness assertion) ────────────────────
//
// These mirror the live staging defect documented in the issue report:
//   issues/2026-09-02-event-catalog-duplicate-dtc-codes-conflicting-verdicts/
//
// The specific conflict: C1234 maps to both a P0 stop-driving entry and a
// routine tire-pressure notice. This is the worst-case: safety instruction
// silently replaced by a routine notice.

const DUPLICATE_P0_C1234: CatalogEntry = {
  event_id: 'maintenance.brake_system_fault',
  category: 'safety',
  severity: 4,
  severity_hint: 'P0',
  description: 'Brake system fault — stop driving, do not operate vehicle.',
  trigger_signal: 'brake_fault',
  threshold_operator: '==',
  threshold_value: 1,
  dtc_code: 'C1234',
};

const DUPLICATE_NONE_C1234: CatalogEntry = {
  event_id: 'maintenance.tire_pressure',
  category: 'maintenance',
  severity: 1,
  severity_hint: 'P3',
  description: 'Tire pressure below safe level.',
  trigger_signal: 'tire_pressure',
  threshold_operator: '<',
  threshold_value: 32,
  dtc_code: 'C1234',  // Same code as DUPLICATE_P0_C1234 — the conflict
};

const CATALOG_WITH_DUPLICATES: CatalogEntry[] = [
  ...UNIQUE_CATALOG,
  DUPLICATE_P0_C1234,
  DUPLICATE_NONE_C1234,
];

// ── Tests — DX2 ───────────────────────────────────────────────────────────────

describe('computeVerdict — DX2: verdict = highest severity_hint, order-independent', () => {

  // ── DX2 uniqueness guard (catalog-wide, not just for codes under test) ─────
  //
  // assertCatalogDtcCodesUnique must throw (or return a non-empty violations list)
  // when any dtc_code appears more than once in the catalog.
  // This is the precondition that makes order-independence possible at all.
  //
  it('DX2-uniqueness: assertCatalogDtcCodesUnique passes on a clean catalog', () => {
    // Should not throw and should return an empty violations list.
    const violations = assertCatalogDtcCodesUnique(UNIQUE_CATALOG);
    expect(violations).toEqual([]);
  });

  it('DX2-uniqueness: assertCatalogDtcCodesUnique detects C1234 as a duplicate', () => {
    const violations = assertCatalogDtcCodesUnique(CATALOG_WITH_DUPLICATES);
    const duplicateCodes = violations.map((v: { dtc_code: string }) => v.dtc_code);
    expect(duplicateCodes).toContain('C1234');
  });

  it('DX2-uniqueness: assertCatalogDtcCodesUnique surfaces all 4 live staging duplicates', () => {
    // Build a catalog that reproduces all four conflicts from the issue report.
    const P0217_DUPLICATE: CatalogEntry = {
      ...CATALOG_P0,
      event_id: 'maintenance.high_engine_temp',
      severity: 1,
      severity_hint: 'P3',
      description: 'Engine temperature critically high.',
      // dtc_code: 'P0217' — same as CATALOG_P0
    };
    const C0035_DUPLICATE: CatalogEntry = {
      ...CATALOG_P1,
      event_id: 'maintenance.wheel_speed_sensor_lf',
      severity: 2,
      severity_hint: 'P2',
      description: 'Left-front wheel speed sensor circuit issue — ABS degraded.',
      // dtc_code: 'C0035' — same as CATALOG_P1
    };
    const P0562_DUPLICATE: CatalogEntry = {
      ...CATALOG_P2,
      event_id: 'maintenance.low_battery',
      severity_hint: undefined as unknown as string,
      description: 'Battery voltage low.',
      // dtc_code: 'P0562' — same as CATALOG_P2
    };

    const stagingCatalogWithAllDuplicates: CatalogEntry[] = [
      CATALOG_P0,         // P0217
      P0217_DUPLICATE,    // P0217 — conflict
      CATALOG_P1,         // C0035
      C0035_DUPLICATE,    // C0035 — conflict
      CATALOG_P2,         // P0562
      P0562_DUPLICATE,    // P0562 — conflict
      DUPLICATE_P0_C1234, // C1234
      DUPLICATE_NONE_C1234, // C1234 — conflict
    ];

    const violations = assertCatalogDtcCodesUnique(stagingCatalogWithAllDuplicates);
    const duplicateCodes = violations.map((v: { dtc_code: string }) => v.dtc_code);

    expect(duplicateCodes).toContain('P0217');
    expect(duplicateCodes).toContain('C0035');
    expect(duplicateCodes).toContain('P0562');
    expect(duplicateCodes).toContain('C1234');
  });

  // ── DX2 verdict = highest severity_hint ───────────────────────────────────

  it('DX2: single P0 DTC → verdict tier is P0', () => {
    const dtcs: DtcReading[] = [{ code: 'P0217', status: 'CONFIRMED_DTC' }];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    expect(result.tier).toBe('P0');
  });

  it('DX2: single P1 DTC → verdict tier is P1', () => {
    const dtcs: DtcReading[] = [{ code: 'C0035', status: 'CONFIRMED_DTC' }];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    expect(result.tier).toBe('P1');
  });

  it('DX2: single P2 DTC → verdict tier is P2', () => {
    const dtcs: DtcReading[] = [{ code: 'P0562', status: 'CONFIRMED_DTC' }];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    expect(result.tier).toBe('P2');
  });

  it('DX2: single P3 DTC → verdict tier is P3', () => {
    const dtcs: DtcReading[] = [{ code: 'C1235', status: 'CONFIRMED_DTC' }];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    expect(result.tier).toBe('P3');
  });

  it('DX2: mixed [P3, P1] → verdict tier is P1 (highest wins)', () => {
    const dtcs: DtcReading[] = [
      { code: 'C1235', status: 'CONFIRMED_DTC' },   // P3
      { code: 'C0035', status: 'CONFIRMED_DTC' },   // P1
    ];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    expect(result.tier).toBe('P1');
  });

  it('DX2: mixed [P1, P0] → verdict tier is P0 (P0 is most severe)', () => {
    const dtcs: DtcReading[] = [
      { code: 'C0035', status: 'CONFIRMED_DTC' },   // P1
      { code: 'P0217', status: 'CONFIRMED_DTC' },   // P0
    ];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    expect(result.tier).toBe('P0');
  });

  it('DX2: mixed [P0, P2, P3] → verdict tier is P0', () => {
    const dtcs: DtcReading[] = [
      { code: 'P0217', status: 'CONFIRMED_DTC' },   // P0
      { code: 'P0562', status: 'CONFIRMED_DTC' },   // P2
      { code: 'C1235', status: 'CONFIRMED_DTC' },   // P3
    ];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    expect(result.tier).toBe('P0');
  });

  // ── DX2 order-independence ─────────────────────────────────────────────────
  //
  // The verdict must be identical regardless of the order DTCs are presented
  // in the input array. If the implementation scans left-to-right and returns
  // the first match, these tests will catch it.

  it('DX2-order: [P1, P0] and [P0, P1] produce the same verdict', () => {
    const dtcsAscending: DtcReading[] = [
      { code: 'C0035', status: 'CONFIRMED_DTC' },   // P1
      { code: 'P0217', status: 'CONFIRMED_DTC' },   // P0
    ];
    const dtcsDescending: DtcReading[] = [
      { code: 'P0217', status: 'CONFIRMED_DTC' },   // P0
      { code: 'C0035', status: 'CONFIRMED_DTC' },   // P1
    ];
    const resultA = computeVerdict(dtcsAscending, UNIQUE_CATALOG);
    const resultB = computeVerdict(dtcsDescending, UNIQUE_CATALOG);
    expect(resultA.tier).toBe(resultB.tier);
    expect(resultA.tier).toBe('P0');
  });

  it('DX2-order: [P3, P2, P1, P0] in all rotation orders produce P0', () => {
    const base: DtcReading[] = [
      { code: 'C1235', status: 'CONFIRMED_DTC' },   // P3
      { code: 'P0562', status: 'CONFIRMED_DTC' },   // P2
      { code: 'C0035', status: 'CONFIRMED_DTC' },   // P1
      { code: 'P0217', status: 'CONFIRMED_DTC' },   // P0
    ];
    // Three rotations of the same four items
    const rotations: DtcReading[][] = [
      [base[1], base[0], base[3], base[2]],
      [base[3], base[2], base[1], base[0]],
      [base[2], base[3], base[0], base[1]],
    ];
    for (const rotation of rotations) {
      const result = computeVerdict(rotation, UNIQUE_CATALOG);
      expect(result.tier).toBe('P0');
    }
  });

  it('DX2-order: verdict matches regardless of catalog array order', () => {
    // The catalog itself may be returned in any order by DynamoDB scan.
    // The verdict must be the same whether P0 entry is first or last.
    const catalogP0First: CatalogEntry[] = [CATALOG_P0, CATALOG_P1, CATALOG_P2, CATALOG_P3];
    const catalogP0Last:  CatalogEntry[] = [CATALOG_P3, CATALOG_P2, CATALOG_P1, CATALOG_P0];

    const dtcs: DtcReading[] = [
      { code: 'P0217', status: 'CONFIRMED_DTC' },   // P0
      { code: 'C0035', status: 'CONFIRMED_DTC' },   // P1
    ];

    const resultFirst = computeVerdict(dtcs, catalogP0First);
    const resultLast  = computeVerdict(dtcs, catalogP0Last);
    expect(resultFirst.tier).toBe(resultLast.tier);
    expect(resultFirst.tier).toBe('P0');
  });

  // ── DX2 zero DTCs → Healthy ────────────────────────────────────────────────

  it('DX2: empty DTC list → verdict tier is Healthy (no issues found)', () => {
    const result = computeVerdict([], UNIQUE_CATALOG);
    expect(result.tier).toBe('Healthy');
  });

});

// ── Tests — DX3 ───────────────────────────────────────────────────────────────

describe('computeVerdict — DX3: uncatalogued code → no severity field produced', () => {

  it('DX3: unknown code → tier is "Unrecognised"', () => {
    const dtcs: DtcReading[] = [{ code: 'U9999', status: 'CONFIRMED_DTC' }];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    expect(result.tier).toBe('Unrecognised');
  });

  it('DX3: unknown code → result object has no "severity" field', () => {
    const dtcs: DtcReading[] = [{ code: 'U9999', status: 'CONFIRMED_DTC' }];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    // The object must not carry a severity field — not merely a different label.
    // "severity" absent from the object entirely.
    expect(result).not.toHaveProperty('severity');
  });

  it('DX3: unknown code → result object has no "severity_hint" field', () => {
    const dtcs: DtcReading[] = [{ code: 'U9999', status: 'CONFIRMED_DTC' }];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    expect(result).not.toHaveProperty('severity_hint');
  });

  it('DX3: unknown code → result object has no "severityHint" field (camelCase variant)', () => {
    // Guard against an implementation that camelCases the field instead of omitting it.
    const dtcs: DtcReading[] = [{ code: 'U9999', status: 'CONFIRMED_DTC' }];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    expect(result).not.toHaveProperty('severityHint');
  });

  it('DX3: unknown code → result object has no "level" field (common alias)', () => {
    const dtcs: DtcReading[] = [{ code: 'U9999', status: 'CONFIRMED_DTC' }];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    expect(result).not.toHaveProperty('level');
  });

  it('DX3: unknown code among known codes → tier is max of known severity, unrecognised code noted', () => {
    // A mix of a known P1 and an unknown code.
    // The overall verdict for a scan is "highest known severity" but the
    // unknown code must be flagged, not silently dropped.
    const dtcs: DtcReading[] = [
      { code: 'C0035', status: 'CONFIRMED_DTC' },    // P1 — known
      { code: 'X1234', status: 'CONFIRMED_DTC' },    // not in catalog
    ];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    // Known P1 still drives the verdict.
    expect(result.tier).toBe('P1');
    // The unrecognised code must be surfaced, not silently ignored.
    // Accept either an "unrecognisedCodes" array or similar.
    const hasUnrecognisedField =
      'unrecognisedCodes' in result ||
      'unknownCodes' in result ||
      'unmatched' in result;
    expect(hasUnrecognisedField).toBe(true);
  });

  it('DX3: all unknown codes → tier is "Unrecognised", no severity field', () => {
    const dtcs: DtcReading[] = [
      { code: 'X0000', status: 'CONFIRMED_DTC' },
      { code: 'X1111', status: 'CONFIRMED_DTC' },
    ];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    expect(result.tier).toBe('Unrecognised');
    expect(result).not.toHaveProperty('severity');
    expect(result).not.toHaveProperty('severity_hint');
  });

  it('DX3: empty code string → treated as unrecognised, no severity field', () => {
    const dtcs: DtcReading[] = [{ code: '', status: 'CONFIRMED_DTC' }];
    const result = computeVerdict(dtcs, UNIQUE_CATALOG);
    expect(result.tier).toBe('Unrecognised');
    expect(result).not.toHaveProperty('severity');
  });

  it('DX3: code present in catalog but without severity_hint → "Unrecognised", no severity', () => {
    // A catalog entry that has a dtc_code but no severity_hint cannot produce a verdict.
    // This is the scenario for the 3 "present but incomplete" entries from T2A.2 before the fix.
    const INCOMPLETE_ENTRY: CatalogEntry = {
      event_id: 'maintenance.oil_pressure_low',
      category: 'maintenance',
      severity: 2,
      severity_hint: '' as string,    // empty string — no hint
      description: 'Oil pressure below operating range.',
      trigger_signal: 'oil_pressure',
      threshold_operator: '<',
      threshold_value: 20,
      dtc_code: 'P0520',
    };
    const catalogWithIncomplete: CatalogEntry[] = [...UNIQUE_CATALOG, INCOMPLETE_ENTRY];
    const dtcs: DtcReading[] = [{ code: 'P0520', status: 'CONFIRMED_DTC' }];
    const result = computeVerdict(dtcs, catalogWithIncomplete);
    // Without a usable severity_hint, must treat as unrecognised for verdict purposes.
    expect(result.tier).toBe('Unrecognised');
    expect(result).not.toHaveProperty('severity_hint');
  });

});
