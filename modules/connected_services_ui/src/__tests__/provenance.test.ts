// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Provenance enforcement tests — T3.2.
 *
 * Spec D5: "Every rendered value must display its provenance.
 *   - 'absent' renders an explicit empty state
 *   - 'simulated' renders a visible label
 *   - 'live' renders normally
 *   - A component given a value with no marker THROWS"
 *
 * T3.2 Verify: "includes a test asserting that a component given a value with
 * no provenance marker throws rather than rendering."
 */

import { describe, expect, it } from "vitest";
import {
  VALID_PROVENANCE_MARKERS,
  assertProvenance,
  type ProvenanceValue,
} from "../types";

// ---------------------------------------------------------------------------
// assertProvenance — the enforcement function
// ---------------------------------------------------------------------------

describe("assertProvenance", () => {
  it("accepts a live marker without throwing", () => {
    const pv: ProvenanceValue<string> = { value: "VIN-001", provenance: "live" };
    expect(() => assertProvenance(pv, "vin")).not.toThrow();
  });

  it("accepts a simulated marker without throwing", () => {
    const pv: ProvenanceValue<string> = { value: "VIN-002", provenance: "simulated" };
    expect(() => assertProvenance(pv, "vin")).not.toThrow();
  });

  it("accepts an absent marker without throwing", () => {
    const pv: ProvenanceValue<string> = { value: null, provenance: "absent" };
    expect(() => assertProvenance(pv, "vin")).not.toThrow();
  });

  // T3.2 core requirement: "a component given a value with no provenance marker throws"
  it("throws when the ProvenanceValue is null (no marker provided)", () => {
    // This is the T3.2 Verify assertion.
    // Spec D5: "No component may render a value whose marker it did not receive."
    expect(() => assertProvenance(null, "vin")).toThrow(
      /Connected Services portal.*vin.*without a provenance marker/
    );
  });

  it("throws when the ProvenanceValue is undefined (no marker provided)", () => {
    expect(() => assertProvenance(undefined, "iccid")).toThrow(
      /Connected Services portal.*iccid.*without a provenance marker/
    );
  });

  it("throws when the provenance marker is unrecognised (fourth-case guard)", () => {
    // Spec D5: "There is no fourth case."
    // 'measured' is valid in fleet_intelligence but NOT in the connected-services portal.
    const pv = { value: "VIN-003", provenance: "measured" } as unknown as ProvenanceValue<string>;
    expect(() => assertProvenance(pv, "vin")).toThrow(
      /unrecognised provenance marker "measured"/
    );
  });

  it("throws when the provenance marker is an empty string", () => {
    const pv = { value: "VIN-004", provenance: "" } as unknown as ProvenanceValue<string>;
    expect(() => assertProvenance(pv, "vin")).toThrow(/unrecognised provenance marker ""/);
  });

  it("error message names the field", () => {
    try {
      assertProvenance(null, "policy_reference");
    } catch (e: unknown) {
      expect(String(e)).toContain("policy_reference");
    }
  });
});

// ---------------------------------------------------------------------------
// VALID_PROVENANCE_MARKERS — spec D5: exactly three values
// ---------------------------------------------------------------------------

describe("VALID_PROVENANCE_MARKERS", () => {
  it("contains exactly 'live', 'simulated', and 'absent'", () => {
    expect([...VALID_PROVENANCE_MARKERS].sort()).toEqual(["absent", "live", "simulated"]);
  });

  it("does not contain 'measured' (fleet_intelligence marker, not permitted here)", () => {
    expect(VALID_PROVENANCE_MARKERS.has("measured")).toBe(false);
  });

  it("does not contain 'unknown'", () => {
    expect(VALID_PROVENANCE_MARKERS.has("unknown")).toBe(false);
  });
});
