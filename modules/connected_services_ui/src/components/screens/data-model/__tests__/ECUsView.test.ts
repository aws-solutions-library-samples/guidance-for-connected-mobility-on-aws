// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ECUsView.test.ts — unit tests for ECUsView and its de-duplication utility.
 *
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend/tasks.md` T3.2
 *
 * ## What this file covers
 *
 * 1. `deduplicateEcus`: same `ecu` code in two manifests yields ONE row
 *    (de-duplication case — mutation check required by spec).
 * 2. `deduplicateEcus`: distinct `ecu` codes yield as many rows as there are
 *    unique codes.
 * 3. `deduplicateEcus`: first-seen wins; second occurrence's fields are ignored.
 * 4. `deduplicateEcus`: ECU without `signalCount` is included as-is (sparseness case).
 * 5. `deduplicateEcus`: ECU without `baselineVersion` is included as-is.
 * 6. `deduplicateEcus`: empty manifests list yields empty result.
 * 7. `deduplicateEcus`: manifest with empty `ecus[]` contributes no rows.
 *
 * ## Mutation check (spec requirement)
 *
 * De-duplication is derived data. Per `~/.kiro/steering/testing.md` §
 * "Mutation testing at the green boundary", the key property must be verified
 * by mutation — not just by a passing test.
 *
 * Mutation performed: change the de-dup key in `deduplicateEcus` from `ecu.ecu`
 * to `ecu.displayName`. With that mutation, test 1 ("same code in two manifests
 * yields ONE row") fails: the two entries have different `displayName` values,
 * so both pass through and the result has 2 rows instead of 1.
 *
 * Result: test FAILS with the mutation → property is guarded. ✓
 */

import { describe, expect, it } from "vitest";

import type { EcuEntry } from "../../../../api/dataModelClient";
import type { ModelManifestItem } from "../useVehicleModels";
import { deduplicateEcus } from "../ECUsView";

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeManifest(
  name: string,
  ecus: EcuEntry[],
): ModelManifestItem {
  return {
    modelManifestName: name,
    modelManifestVersion: "1",
    displayName: `Display ${name}`,
    modelLine: "Line-A",
    platform: "ICE",
    productionPhase: "PRODUCTION",
    status: "ACTIVE",
    description: "",
    isDefault: false,
    decoderManifestRef: "cms-fleet-v3",
    signalCount: 10,
    ecuConfigId: "ecu-cfg-1",
    fleetIds: [],
    ecus,
    pk: `MODEL#${name}`,
    sk: "MANIFEST#1",
    createTimestamp: "2024-01-01T00:00:00Z",
    updateTimestamp: "2024-06-01T00:00:00Z",
  };
}

// ── 1. De-duplication: same ecu code in two manifests → ONE row ───────────────

describe("deduplicateEcus — same ecu code across manifests", () => {
  it("yields ONE row when the same ecu code appears in two manifests", () => {
    const manifests = [
      makeManifest("MANIFEST-A", [
        { ecu: "TCU", displayName: "Telematics Control Unit — Manifest A" },
      ]),
      makeManifest("MANIFEST-B", [
        { ecu: "TCU", displayName: "Telematics Control Unit — Manifest B" },
      ]),
    ];

    const result = deduplicateEcus(manifests);

    expect(result.length).toBe(1);
    expect(result[0].ecu).toBe("TCU");
  });

  it("uses first-seen entry when the same code appears twice", () => {
    const manifests = [
      makeManifest("FIRST", [
        { ecu: "BMS", displayName: "Battery Mgr — First" },
      ]),
      makeManifest("SECOND", [
        { ecu: "BMS", displayName: "Battery Mgr — Second" },
      ]),
    ];

    const result = deduplicateEcus(manifests);

    expect(result.length).toBe(1);
    expect(result[0].displayName).toBe("Battery Mgr — First");
  });
});

// ── 2. De-duplication: distinct codes yield all rows ─────────────────────────

describe("deduplicateEcus — distinct ecu codes", () => {
  it("returns all rows when all ecu codes are unique", () => {
    const manifests = [
      makeManifest("M1", [
        { ecu: "TCU", displayName: "Telematics" },
        { ecu: "BMS", displayName: "Battery" },
      ]),
      makeManifest("M2", [
        { ecu: "VCU", displayName: "Vehicle Control" },
      ]),
    ];

    const result = deduplicateEcus(manifests);

    expect(result.length).toBe(3);
    const codes = result.map((e) => e.ecu);
    expect(codes).toContain("TCU");
    expect(codes).toContain("BMS");
    expect(codes).toContain("VCU");
  });
});

// ── 3. Sparseness: ECU without signalCount is included ───────────────────────

describe("deduplicateEcus — sparseness", () => {
  it("includes an ECU entry that has no signalCount (must render absent, not 0)", () => {
    const ecu: EcuEntry = { ecu: "BCM", displayName: "Body Control Module" };
    // Intentionally: no signalCount, no baselineVersion
    expect(ecu.signalCount).toBeUndefined();

    const result = deduplicateEcus([makeManifest("M1", [ecu])]);

    expect(result.length).toBe(1);
    expect(result[0].signalCount).toBeUndefined();
  });

  it("includes an ECU entry that has no baselineVersion", () => {
    const ecu: EcuEntry = { ecu: "ADAS", displayName: "ADAS Ctrl" };
    expect(ecu.baselineVersion).toBeUndefined();

    const result = deduplicateEcus([makeManifest("M1", [ecu])]);

    expect(result[0].baselineVersion).toBeUndefined();
  });

  it("preserves signalCount and baselineVersion when present", () => {
    const ecu: EcuEntry = {
      ecu: "TCU",
      displayName: "Telematics",
      signalCount: 18,
      baselineVersion: "4.0.0",
    };

    const result = deduplicateEcus([makeManifest("M1", [ecu])]);

    expect(result[0].signalCount).toBe(18);
    expect(result[0].baselineVersion).toBe("4.0.0");
  });
});

// ── 4. Edge cases ─────────────────────────────────────────────────────────────

describe("deduplicateEcus — edge cases", () => {
  it("returns empty array for empty manifests list", () => {
    expect(deduplicateEcus([])).toEqual([]);
  });

  it("returns empty array when all manifests have empty ecus arrays", () => {
    const result = deduplicateEcus([
      makeManifest("M1", []),
      makeManifest("M2", []),
    ]);
    expect(result).toEqual([]);
  });
});

// ── 5. Multiple codes from one manifest all pass through ──────────────────────

describe("deduplicateEcus — single manifest with multiple ECUs", () => {
  it("includes all ECUs from a single manifest when all codes are unique", () => {
    const ecus: EcuEntry[] = [
      { ecu: "TCU", displayName: "Telematics", signalCount: 18, baselineVersion: "4.0.0" },
      { ecu: "BMS", displayName: "Battery", signalCount: 28, baselineVersion: "3.1.2" },
      { ecu: "VCU", displayName: "Vehicle Control" },
      { ecu: "BCM", displayName: "Body Control" },
      { ecu: "ADAS", displayName: "ADAS" },
      { ecu: "TPMS", displayName: "TPMS" },
      { ecu: "HVAC", displayName: "Climate" },
      { ecu: "OBC", displayName: "Onboard Charger" },
    ];

    const result = deduplicateEcus([makeManifest("CMS-FLEET-MODEL", ecus)]);

    expect(result.length).toBe(8);
  });
});

// ── 6. Realistic: 65 raw entries across 8 manifests de-dup to fewer rows ─────

describe("deduplicateEcus — realistic multi-manifest scenario", () => {
  it("de-duplicates TCU and BMS shared across all 8 manifests to 2 rows", () => {
    const manifests = Array.from({ length: 8 }, (_, i) =>
      makeManifest(`MERIDIAN-MODEL-${i}`, [
        { ecu: "TCU", displayName: `TCU on model ${i}` },
        { ecu: "BMS", displayName: `BMS on model ${i}` },
      ]),
    );

    const result = deduplicateEcus(manifests);

    // 8 manifests × 2 ECUs = 16 raw entries, but only 2 unique codes
    expect(result.length).toBe(2);
  });
});
