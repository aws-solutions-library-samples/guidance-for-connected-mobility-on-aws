// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SignalCatalogView tests — T3.1
 *
 * ## What this file covers
 *
 * 1. `groupSignalsByBranch` groups by the VSS top-level branch correctly.
 * 2. Fabricated column guard: `in_products` has no API counterpart and must not
 *    appear in the source code (outside comments and this guard itself).
 * 3. Sparseness guard: absent `can_id` must render differently from zero
 *    — the type declares both as optional/undefined so callers cannot conflate
 *    them.
 * 4. No `source_ecu` or ECU grouping — fixture-only field, absent from the API.
 * 5. Unconfigured API vs empty vs error are three distinct states — all three
 *    string tokens must appear in the source.
 */

import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { groupSignalsByBranch } from "../SignalCatalogView";
import type { SignalItem } from "../../../../api/dataModelClient";

// ── Source-level assertions (read the compiled-out file) ──────────────────────

const SOURCE = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "..", "SignalCatalogView.tsx"),
  "utf8",
);

/** Strip block comments and line comments before asserting absence. */
const CODE = SOURCE.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

// ── Minimal SignalItem factory ─────────────────────────────────────────────────

function sig(over: Partial<SignalItem> & Pick<SignalItem, "signal_id" | "vss_path" | "signal_group">): SignalItem {
  return {
    data_type: "double",
    // `String(...)` because `signal_id` is `string | number` — the live API sends a JSON
    // number (`1.0`), which the interface declared as `string` until 2026-09-20. See
    // `SignalItem.signal_id`'s docstring, and
    // `issues/2026-09-20-signal-detail-never-resolves-by-signal-id/` for the live bug
    // that wrong type was hiding.
    signal_name: String(over.signal_id),
    status: "ACTIVE",
    ...over,
  };
}

// ── 1. groupSignalsByBranch ────────────────────────────────────────────────────

describe("groupSignalsByBranch", () => {
  it("is exported and callable on an empty array", () => {
    expect(typeof groupSignalsByBranch).toBe("function");
    expect(groupSignalsByBranch([])).toEqual([]);
  });

  it("groups by the second VSS segment (top-level branch)", () => {
    const signals: SignalItem[] = [
      sig({ signal_id: "s1", vss_path: "Vehicle.Chassis.Axle.Pressure", signal_group: "chassis" }),
      sig({ signal_id: "s2", vss_path: "Vehicle.Chassis.Brake.Pad", signal_group: "chassis" }),
      sig({ signal_id: "s3", vss_path: "Vehicle.Powertrain.Engine.Speed", signal_group: "powertrain" }),
    ];
    const groups = groupSignalsByBranch(signals);
    expect(groups).toHaveLength(2);
    const byBranch = new Map(groups.map((g) => [g.branch, g.signals]));
    expect(byBranch.get("Chassis")).toHaveLength(2);
    expect(byBranch.get("Powertrain")).toHaveLength(1);
  });

  it("falls back to signal_group when vss_path has only two segments", () => {
    // "Vehicle.Speed" has only 2 parts — branch fallback to signal_group
    const signals: SignalItem[] = [
      sig({ signal_id: "s1", vss_path: "Vehicle.Speed", signal_group: "core_telemetry" }),
    ];
    const groups = groupSignalsByBranch(signals);
    expect(groups).toHaveLength(1);
    expect(groups[0].branch).toBe("core_telemetry");
    expect(groups[0].signals).toHaveLength(1);
  });

  it("sorts groups alphabetically by branch", () => {
    const signals: SignalItem[] = [
      sig({ signal_id: "s1", vss_path: "Vehicle.Powertrain.Speed", signal_group: "powertrain" }),
      sig({ signal_id: "s2", vss_path: "Vehicle.Chassis.Brake", signal_group: "chassis" }),
      sig({ signal_id: "s3", vss_path: "Vehicle.ADAS.Cruise", signal_group: "adas" }),
    ];
    const groups = groupSignalsByBranch(signals);
    expect(groups.map((g) => g.branch)).toEqual(["ADAS", "Chassis", "Powertrain"]);
  });
});

// ── 2. Fabricated column guard ────────────────────────────────────────────────

describe("SignalCatalogView — no fabricated columns", () => {
  it("anti-vacuity: comment stripping leaves real code", () => {
    // If CODE were over-stripped, the absence assertions would pass trivially.
    expect(CODE).toMatch(/const SignalCatalogView/);
    expect(CODE).toMatch(/groupSignalsByBranch/);
    expect(CODE).toMatch(/fetchSignals/);
  });

  it("in_products column is absent — no API field backs it", () => {
    // in_products appeared in STUB_SIGNALS and the old column definition.
    // The /signals endpoint carries no such field on any of the 302 signals.
    // This test fails if in_products is re-added to the rendered output.
    expect(
      CODE,
      '"in_products" has no counterpart on GET /signals. ' +
        "Do not re-add a column or field using it — it would be fabricated.",
    ).not.toMatch(/in_products/);
  });

  it("source_ecu is absent — fixture-only field not present on the real API", () => {
    expect(
      CODE,
      '"source_ecu" exists only in vehicleModelsData.ts fixture. ' +
        "The live /signals payload (302 items) carries no ECU field. " +
        "Do not relabel signal_group as ECU — they are different axes.",
    ).not.toMatch(/source_ecu/);
  });

  it("reads the real data client", () => {
    // Positive control: fabrication absence is worthless if the screen renders nothing.
    expect(CODE).toMatch(/fetchSignals/);
  });
});

// ── 3. Sparseness — absent ≠ zero ─────────────────────────────────────────────

describe("SignalItem sparseness — can_id and cycle_ms", () => {
  /**
   * This test guards the semantic distinction between absent and zero.
   * can_id (65/302) and cycle_ms (204/302) are sparse fields typed as optional
   * in SignalItem. An item with can_id === undefined must render differently from
   * one with can_id === 0.
   *
   * The test is structural: it verifies that the TypeScript interface uses `?:`
   * for these fields (optional) rather than `number` (always present), so the
   * type cannot be confused with zero.
   *
   * Mutation: if you change `readonly can_id?: number` to `readonly can_id: number`,
   * this assertion fails — correct, because callers could then mistake undefined for 0.
   */
  it("can_id is typed as optional (sparse field — 65/302 signals)", () => {
    const clientSource = readFileSync(
      resolve(
        dirname(fileURLToPath(import.meta.url)),
        "../../../../api/dataModelClient.ts",
      ),
      "utf8",
    );
    // Optional marker `?:` must appear for can_id
    expect(
      clientSource,
      "can_id must be optional (?: ) because only 65 of 302 signals carry it. " +
        "absent !== 0; a required number field silently conflates the two.",
    ).toMatch(/readonly can_id\?: number/);
  });

  it("cycle_ms is typed as optional (sparse field — 204/302 signals)", () => {
    const clientSource = readFileSync(
      resolve(
        dirname(fileURLToPath(import.meta.url)),
        "../../../../api/dataModelClient.ts",
      ),
      "utf8",
    );
    expect(
      clientSource,
      "cycle_ms must be optional (?: ) because only 204 of 302 signals carry it.",
    ).toMatch(/readonly cycle_ms\?: number/);
  });
});

// ── 4. No ECU grouping axis ────────────────────────────────────────────────────

describe("SignalCatalogView — no ECU relabelling", () => {
  it("does not use signal_group as ECU label in the grouping function", () => {
    // The grouping function groups by VSS branch, not by ECU.
    // Relabelling signal_group as "ECU" would conflate two different axes.
    const signals: SignalItem[] = [
      sig({ signal_id: "s1", vss_path: "Vehicle.Powertrain.Battery.Soc", signal_group: "ev_specific" }),
      sig({ signal_id: "s2", vss_path: "Vehicle.Powertrain.Motor.Speed", signal_group: "ev_specific" }),
    ];
    const groups = groupSignalsByBranch(signals);
    // Both belong to "Powertrain" branch by vss_path, NOT split by signal_group
    expect(groups).toHaveLength(1);
    expect(groups[0].branch).toBe("Powertrain");
  });
});

// ── 5. Three distinct states ───────────────────────────────────────────────────

describe("SignalCatalogView — three distinct states", () => {
  it("source has distinct unconfigured / error / empty handling", () => {
    // "unavailable" is the unconfigured state token
    expect(CODE, "unconfigured must surface as 'unavailable', not as zero").toMatch(
      /unavailable/,
    );
    // error state
    expect(CODE).toMatch(/error/);
    // ready state (implies empty is a sub-case of ready, with branches.length === 0)
    expect(CODE).toMatch(/ready/);
    // null check — null from fetchSignals() means unconfigured
    expect(
      CODE,
      "null response from fetchSignals() must degrade to unconfigured, not zero",
    ).toMatch(/=== null/);
  });
});
