// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * vehicleResolution.test.ts — unit tests for the three-state resolution function.
 *
 * Spec: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/group3-contract.md` § 3
 *       `tasks.md` T3.4, Mutation requirement.
 *
 * ## Coverage
 *
 *  1. A `targetLooksInvalid` assignment resolves to `malformed` — catalog not consulted.
 *  2. A valid VIN present in the catalog resolves to `resolved` with the entry.
 *  3. A valid VIN absent from the catalog resolves to `outside-scope` (not `malformed`).
 *  4. Matching is by `vin`, never by `vehicleId`.
 *  5. `entry` is `null` for `outside-scope`.
 *  6. `entry` is `null` for `malformed`.
 *  7. Live `malformed` example: `VEH-CS-DEMO-0003` (contains hyphen and lowercase).
 *  8. Live `outside-scope` example: `4T1B11HK0LU98765` (real vehicle outside 100-subset).
 *  9. Precedence: `malformed` beats catalog presence — a flagged target is `malformed`
 *     even if a catalog entry somehow has the same value as its `vin`.
 * 10. An empty catalog with a valid VIN resolves to `outside-scope`, not `malformed`.
 */

import { describe, expect, it } from "vitest";

import type { SimulationVehicleEntry } from "../../../../api/subscriptionsClient";
import type { VehicleAssignment } from "../campaignGrouping";
import { resolveVehicleTarget, type ResolvedVehicleTarget } from "../vehicleResolution";

// ── Fixtures ──────────────────────────────────────────────────────────────────

/** A realistic catalog entry with all fields. */
function makeEntry(vin: string, overrides: Partial<SimulationVehicleEntry> = {}): SimulationVehicleEntry {
  return {
    vehicleId: `VEH-MRDN-${vin.slice(-4)}`,
    vin,
    make: "Meridian",
    model: "Trailwind",
    year: "2024",
    ...overrides,
  };
}

/**
 * Build a minimal VehicleAssignment for testing.
 *
 * `targetLooksInvalid` is set by the caller — this simulates what `campaignGrouping.ts`
 * already computed and stored.
 */
function makeAssignment(
  target: string,
  targetLooksInvalid: boolean,
): VehicleAssignment {
  // The `row` is not used by `resolveVehicleTarget` but is required by the type.
  return {
    row: {
      campaignId: `cms-fleet-gps-10s-${target}`,
      campaignName: "cms-fleet-gps-10s",
      targetArn: `vehicle:${target}`,
      status: "RUNNING",
      createdAt: "2026-09-01T00:00:00Z",
      owner: "oem",
    },
    target,
    targetLooksInvalid,
    state: "assigned",
  };
}

// Catalog used by most tests — contains two real Meridian VINs.
const CATALOG: readonly SimulationVehicleEntry[] = [
  makeEntry("MRDN0000000000001"),
  makeEntry("MRDN0000000000002"),
  makeEntry("MRDN0000000000013"),
];

// ── 1. Malformed: targetLooksInvalid is true ──────────────────────────────────

describe("malformed — targetLooksInvalid", () => {
  it("resolves to malformed when targetLooksInvalid is true", () => {
    const result = resolveVehicleTarget(
      makeAssignment("VEH-CS-DEMO-0003", true),
      CATALOG,
    );
    expect(result.state).toBe("malformed");
  });

  it("does not return a catalog entry for malformed targets", () => {
    const result = resolveVehicleTarget(
      makeAssignment("VEH-CS-DEMO-0003", true),
      CATALOG,
    );
    expect(result.entry).toBeNull();
  });

  it("returns the original target string unchanged", () => {
    const result = resolveVehicleTarget(
      makeAssignment("VEH-CS-DEMO-0003", true),
      CATALOG,
    );
    expect(result.target).toBe("VEH-CS-DEMO-0003");
  });

  /**
   * Live malformed example from staging 2026-09-20.
   *
   * `VEH-CS-DEMO-0003` contains both a hyphen and lowercase letters, so
   * `targetLooksInvalid` is `true` for it in `campaignGrouping.ts`.
   */
  it("live example VEH-CS-DEMO-0003 resolves to malformed", () => {
    // This VIN-like string contains a hyphen, so it was flagged by the heuristic.
    // Simulated as already flagged — the grouping function already ran.
    const result = resolveVehicleTarget(
      makeAssignment("VEH-CS-DEMO-0003", true),
      CATALOG,
    );
    expect(result.state).toBe("malformed");
  });
});

// ── 2. Resolved: VIN found in catalog by `vin` ────────────────────────────────

describe("resolved — VIN present in catalog", () => {
  it("resolves to resolved when VIN is in the catalog", () => {
    const result = resolveVehicleTarget(
      makeAssignment("MRDN0000000000001", false),
      CATALOG,
    );
    expect(result.state).toBe("resolved");
  });

  it("returns the matching catalog entry", () => {
    const result = resolveVehicleTarget(
      makeAssignment("MRDN0000000000002", false),
      CATALOG,
    );
    expect(result.entry).not.toBeNull();
    expect(result.entry!.vin).toBe("MRDN0000000000002");
    expect(result.entry!.make).toBe("Meridian");
  });

  it("carries make/model/year from the catalog entry", () => {
    const catalog: readonly SimulationVehicleEntry[] = [
      makeEntry("MRDN0000000000013", { make: "Meridian", model: "Trailwind EV", year: "2025" }),
    ];
    const result = resolveVehicleTarget(
      makeAssignment("MRDN0000000000013", false),
      catalog,
    );
    expect(result.entry?.model).toBe("Trailwind EV");
    expect(result.entry?.year).toBe("2025");
  });
});

// ── 3. Outside-scope: valid VIN not in catalog ────────────────────────────────

describe("outside-scope — valid VIN absent from catalog", () => {
  it("resolves to outside-scope when VIN is not in the catalog", () => {
    const result = resolveVehicleTarget(
      makeAssignment("4T1B11HK0LU98765", false),
      CATALOG,
    );
    expect(result.state).toBe("outside-scope");
  });

  /**
   * Live outside-scope example from staging 2026-09-20.
   *
   * `4T1B11HK0LU98765` is a real vehicle that the producer filter (`meridian` only)
   * excludes from the 100-of-155 `/simulate/vehicles` subset.  It must NOT be
   * called malformed — calling a real vehicle invalid because it sits outside a
   * producer filter is an over-claim.
   */
  it("live example 4T1B11HK0LU98765 resolves to outside-scope, not malformed", () => {
    const result = resolveVehicleTarget(
      makeAssignment("4T1B11HK0LU98765", false),
      CATALOG,
    );
    expect(result.state).toBe("outside-scope");
    // Critical: must NOT be malformed
    expect(result.state).not.toBe("malformed");
  });

  it("returns null entry for outside-scope", () => {
    const result = resolveVehicleTarget(
      makeAssignment("4T1B11HK0LU98765", false),
      CATALOG,
    );
    expect(result.entry).toBeNull();
  });

  it("returns the target string unchanged for outside-scope", () => {
    const result = resolveVehicleTarget(
      makeAssignment("4T1B11HK0LU98765", false),
      CATALOG,
    );
    expect(result.target).toBe("4T1B11HK0LU98765");
  });

  it("empty catalog resolves a valid VIN to outside-scope", () => {
    const result = resolveVehicleTarget(
      makeAssignment("MRDN0000000000001", false),
      [],
    );
    expect(result.state).toBe("outside-scope");
    expect(result.entry).toBeNull();
  });
});

// ── 4. Matching is by `vin`, never by `vehicleId` ─────────────────────────────

describe("matching by vin, not vehicleId", () => {
  it("does NOT resolve to resolved when only vehicleId matches", () => {
    // A catalog entry where vehicleId happens to equal the assignment target,
    // but vin is different — must NOT resolve to 'resolved'.
    const catalog: readonly SimulationVehicleEntry[] = [
      {
        vehicleId: "MRDN0000000000001",  // vehicleId === target
        vin: "MRDN9999999999999",         // vin is different
        make: "Meridian",
        model: "Trailwind",
        year: "2024",
      },
    ];
    const result = resolveVehicleTarget(
      makeAssignment("MRDN0000000000001", false),
      catalog,
    );
    // vehicleId matches target but vin does not — must be outside-scope
    expect(result.state).toBe("outside-scope");
    expect(result.entry).toBeNull();
  });

  it("resolves to resolved when vin matches, regardless of vehicleId value", () => {
    const catalog: readonly SimulationVehicleEntry[] = [
      {
        vehicleId: "VEH-INTERNAL-001",
        vin: "MRDN0000000000001",
        make: "Meridian",
        model: "Trailwind",
        year: "2024",
      },
    ];
    const result = resolveVehicleTarget(
      makeAssignment("MRDN0000000000001", false),
      catalog,
    );
    expect(result.state).toBe("resolved");
    expect(result.entry?.vehicleId).toBe("VEH-INTERNAL-001");
  });
});

// ── 5–6. entry is null for non-resolved states ────────────────────────────────

describe("entry is null for non-resolved states", () => {
  it("entry is null for outside-scope", () => {
    const result = resolveVehicleTarget(makeAssignment("XXXXXXXXXXXXXXXX", false), CATALOG);
    expect(result.entry).toBeNull();
  });

  it("entry is null for malformed", () => {
    const result = resolveVehicleTarget(makeAssignment("bad-target-00", true), CATALOG);
    expect(result.entry).toBeNull();
  });
});

// ── 9. Precedence: malformed beats catalog presence ──────────────────────────

describe("precedence: malformed takes priority over catalog", () => {
  it("resolves to malformed even when the catalog contains a matching vin", () => {
    // Artificially: a catalog entry whose `vin` equals the flagged target string.
    // In practice this cannot happen on real data (a hyphened string is not a VIN),
    // but the precedence rule must still hold structurally.
    const catalog: readonly SimulationVehicleEntry[] = [
      { vehicleId: "VEH-00", vin: "VEH-CS-DEMO-0003" },
    ];
    const result = resolveVehicleTarget(
      makeAssignment("VEH-CS-DEMO-0003", true),  // targetLooksInvalid = true
      catalog,
    );
    // Malformed takes precedence — the catalog entry is irrelevant.
    expect(result.state).toBe("malformed");
    expect(result.entry).toBeNull();
  });
});

// ── Return type integrity ─────────────────────────────────────────────────────

describe("return type integrity", () => {
  it("always returns a ResolvedVehicleTarget with all required fields", () => {
    const cases: Array<{ assignment: VehicleAssignment; catalog: readonly SimulationVehicleEntry[] }> = [
      { assignment: makeAssignment("VEH-BAD-001", true), catalog: CATALOG },
      { assignment: makeAssignment("MRDN0000000000001", false), catalog: CATALOG },
      { assignment: makeAssignment("NOTINCATALOG00001", false), catalog: CATALOG },
    ];
    for (const { assignment, catalog } of cases) {
      const result: ResolvedVehicleTarget = resolveVehicleTarget(assignment, catalog);
      expect(typeof result.target).toBe("string");
      expect(["resolved", "outside-scope", "malformed"]).toContain(result.state);
      // entry is null or an object — never undefined
      expect(result.entry === null || typeof result.entry === "object").toBe(true);
    }
  });
});
