// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Three-state vehicle-target resolution.
 *
 * Shared by `CampaignVehiclesPanel` (T3.4) and `CampaignDetailView` (T3.1).  Lives in a
 * separate module — not as a local in the panel — so the three-state precedence rule
 * and its mutation requirement have a clean, pure target that can be tested without
 * rendering.
 *
 * ## States and precedence (MUST be applied in this order)
 *
 * 1. `malformed`     — `assignment.targetLooksInvalid` is `true`.  The shape heuristic
 *                       in `campaignGrouping.ts` is authoritative in the NEGATIVE direction:
 *                       anything it flags cannot possibly be a VIN, so there is no point
 *                       looking it up in the catalog.  Live example: `VEH-CS-DEMO-0003`.
 *
 * 2. `resolved`      — The target is found in the catalog by `vin` (exact match).
 *                       The catalog entry is returned so the row can display make/model/year.
 *
 * 3. `outside-scope` — Not found in the catalog.  This is NOT the same as invalid: the
 *                       vehicle catalog is `GET /simulate/vehicles`, a producer-filtered
 *                       subset (100 of 155 on staging 2026-09-20).  Absence from a subset
 *                       is not evidence of non-existence.  Live example: `4T1B11HK0LU98765`
 *                       is a real vehicle excluded by the producer filter.
 *
 * ## Why three states, not two
 *
 * Measured 2026-09-20 over 24 distinct assignment targets on staging:
 *   - Shape heuristic wrong on 7 of 24 (false negatives — real VINs it cannot flag).
 *   - Catalog wrong on 1 of 24 (`4T1B11HK0LU98765` — real vehicle outside the 100-subset).
 * Collapsing `outside-scope` into `malformed` would call that one real vehicle invalid,
 * which is the same over-claim as a `SUSPENDED` assignment displaying as active.  Full
 * rationale in `decisions.md` § "Group 3 pre-build".
 *
 * ## Match key
 *
 * Always `vin`.  Never `vehicleId`.  The assignment row key is `vehicle:{vin}` and the
 * catalog entry's `vehicleId` is a different, internal identifier that shares no
 * structure with VINs.
 *
 * Spec: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/group3-contract.md` § 3
 */

import type { SimulationVehicleEntry } from "../../../api/subscriptionsClient";
import type { VehicleAssignment } from "./campaignGrouping";

// ── Exported types ────────────────────────────────────────────────────────────

/**
 * The three states a vehicle assignment target can be in.
 *
 * Ordered by precedence in `resolveVehicleTarget`:
 *   1. `malformed`     — shape heuristic says it cannot be a VIN
 *   2. `resolved`      — found in the catalog by `vin`
 *   3. `outside-scope` — well-formed VIN not in the operator's catalog subset
 *
 * Contract: only `malformed` may be presented as unresolvable.
 * `outside-scope` must show the VIN and note it is outside the simulate scope.
 */
export type VehicleResolutionState = "resolved" | "outside-scope" | "malformed";

/**
 * The result of resolving one `VehicleAssignment` against the vehicle catalog.
 *
 * `entry` is populated only when `state === "resolved"` and is `null` otherwise —
 * do not rely on `entry` being present for `outside-scope` or `malformed`.
 */
export interface ResolvedVehicleTarget {
  /** The raw target string from the assignment (the VIN-suffix of `targetArn`). */
  readonly target: string;
  readonly state: VehicleResolutionState;
  /**
   * The catalog entry when `state === "resolved"`.
   *
   * `null` for `outside-scope` (well-formed VIN, not in this subset of the catalog)
   * and `malformed` (not a valid VIN shape, no lookup attempted).
   */
  readonly entry: SimulationVehicleEntry | null;
}

// ── Resolution function ───────────────────────────────────────────────────────

/**
 * Resolve one vehicle assignment to its three-state representation.
 *
 * Precedence:
 *   1. `malformed`     — if `assignment.targetLooksInvalid` is `true`
 *   2. `resolved`      — if `assignment.target` matches any catalog entry by `vin`
 *   3. `outside-scope` — otherwise (well-formed but not in this catalog subset)
 *
 * Matching is by `vin`, never by `vehicleId`.
 *
 * @param assignment - One per-vehicle assignment from `groupCampaigns`.
 * @param catalog    - The full vehicle catalog from `listVehiclesForSimulation`.
 */
export function resolveVehicleTarget(
  assignment: VehicleAssignment,
  catalog: readonly SimulationVehicleEntry[],
): ResolvedVehicleTarget {
  // Step 1: shape heuristic is authoritative in the negative direction only.
  // Anything it flags is definitely malformed; do not consult the catalog.
  if (assignment.targetLooksInvalid) {
    return { target: assignment.target, state: "malformed", entry: null };
  }

  // Step 2: look up by vin in the catalog.  Match on `vin`, never on `vehicleId`.
  const found = catalog.find((v) => v.vin === assignment.target);
  if (found !== undefined) {
    return { target: assignment.target, state: "resolved", entry: found };
  }

  // Step 3: well-formed VIN not found in this (producer-filtered) subset.
  // The vehicle may exist on the server; we cannot claim otherwise.
  return { target: assignment.target, state: "outside-scope", entry: null };
}
