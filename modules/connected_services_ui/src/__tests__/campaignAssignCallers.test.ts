/**
 * Repo-wide guard: every caller of `POST /campaigns/assign` must inspect `rejected`.
 *
 * Since 2026-09-20 the server resolves each `vehicles[]` entry against `vin-index` and
 * returns `rejected` for anything that does not resolve — with HTTP **200**. So
 * `res.ok` no longer implies a row was written, and a caller that checks only `!ok`
 * reports success for a write that did not happen. Review cycle 1 of Fix Group 10
 * found exactly that in two `cms_ui` callers; cycle 2 found a third and a fourth.
 *
 * Structural rather than per-component render tests, deliberately: the callers live in
 * two separate build targets with heavyweight dependency graphs, and the invariant is
 * cross-cutting. Same philosophy as `scripts/check_trip_simulator_modal_sync.py` and
 * `deployment/stacks/tests/test_cdk_context_key_threading.py` — the value is that a
 * caller added next month is caught without anyone remembering this rule.
 *
 * See `issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/`.
 */

import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

// Both frontend module roots, from this file at
// modules/connected_services_ui/src/__tests__/.
const REPO_ROOT = resolve(__dirname, "..", "..", "..", "..");
const SCAN_ROOTS = [
  join(REPO_ROOT, "modules", "connected_services_ui", "src"),
  join(REPO_ROOT, "modules", "cms_ui", "source", "frontend", "src"),
];

/** The data-processing per-vehicle route. NOT `/api/v1/fleet-campaigns/assign`, which
 *  is a different route in main_api with its own authorization model and is not
 *  covered by the server-side guard — see the issue's sibling-writers section. */
const ROUTE_MARKER = "campaigns/assign";
const FLEET_ROUTE_MARKER = "fleet-campaigns/assign";
/** The typed client wrapper. A SECOND membership marker, not just a window marker.
 *
 *  Review cycle 3: membership ran on `ROUTE_MARKER` against RAW text, and both CS UI
 *  callers contain that string only in COMMENTS — the three `cms_ui` callers have it in
 *  live `authFetch` code. So tidying a comment could drop a caller from the scanned set
 *  entirely and every check would pass vacuously for that file. Not hypothetical: this
 *  very change rewrote the `DataCollectionCampaignsView` docstring that was that file's
 *  only anchor, and the campaigns-screen restructure spec is queued to edit it next.
 *  Matching the function call as well means a caller that actually calls the API stays
 *  in scope regardless of its prose. */
const CLIENT_FN_MARKER = "assignCampaignToVehicle(";

/** Callers known at 2026-09-20, pinned BY PATH.
 *
 *  A bare count floor cannot tell "a caller was removed" from "a caller stopped being
 *  detected". Cycle 3 found the floor set to 4 against a real 5 — the roster comment
 *  had omitted `SimulateVehicleView.tsx`, the file T10.3 fixed. Pinning paths makes a
 *  disappearance name itself. Removing a caller legitimately means editing this list,
 *  which is the point. */
const KNOWN_CALLERS = [
  "modules/cms_ui/source/frontend/src/components/commons/CreateCampaignWizard.tsx",
  "modules/cms_ui/source/frontend/src/components/data-processing/CampaignViewer.tsx",
  "modules/cms_ui/source/frontend/src/components/vehicles/vehicle-detail/VehicleCampaignsTable.tsx",
  "modules/connected_services_ui/src/components/screens/connectivity/SimulateVehicleView.tsx",
  "modules/connected_services_ui/src/components/screens/data-model/DataCollectionCampaignsView.tsx",
  "modules/connected_services_ui/src/components/screens/data-model/CampaignVehiclesPanel.tsx",
];

function walk(dir: string, out: string[] = []): string[] {
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const e of entries) {
    if (e === "node_modules" || e === "dist" || e === "coverage") continue;
    const p = join(dir, e);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, out);
    else if (/\.tsx?$/.test(e) && !/\.test\.tsx?$/.test(e)) out.push(p);
  }
  return out;
}

interface Caller {
  readonly path: string;
  readonly text: string;
}

/**
 * Strip `//` line comments and block comments.
 *
 * Defence-in-depth, and honestly labelled: this WAS load-bearing. The first revision
 * of the `.vehicleId` check matched the whole file, and it flagged two files whose
 * only remaining offence was a COMMENT quoting the forbidden `v.vin || v.vehicleId`
 * shape while explaining why it had been removed — a guard written in terms of the
 * pattern it forbids matches its own documentation, the same trap as a denylist that
 * embeds the secret it guards.
 *
 * After that check was narrowed to a window around the payload, stripping is no
 * longer strictly required: mutation MG5 (disable stripping) is NOT caught, because
 * zero comment lines currently fall inside a window AND contain `.vehicleId`.
 * Verified, not assumed. It is kept because the window is proximity-based, so a
 * future explanatory comment placed next to a payload line would trip the guard and
 * send the next reader chasing a non-defect.
 */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

function findCallers(): Caller[] {
  const out: Caller[] = [];
  for (const root of SCAN_ROOTS) {
    for (const p of walk(root)) {
      const raw = readFileSync(p, "utf8");
      // Strip the fleet-route marker first so a file containing only that route
      // does not register as a caller of the per-vehicle route.
      const withoutFleetRoute = raw.split(FLEET_ROUTE_MARKER).join("«fleet»");
      // Membership on EITHER the route string or the client call — see
      // CLIENT_FN_MARKER for why one marker was not enough.
      const isCaller =
        withoutFleetRoute.includes(ROUTE_MARKER) ||
        withoutFleetRoute.includes(CLIENT_FN_MARKER);
      if (!isCaller) continue;
      // The API-client wrapper itself performs the request but deliberately does not
      // interpret the body — its callers do. Excluded by path, not by name, so a
      // renamed function does not silently drop out of scope.
      if (p.endsWith(join("src", "api", "dataModelClient.ts"))) continue;
      out.push({ path: p.slice(REPO_ROOT.length + 1), text: stripComments(raw) });
    }
  }
  return out;
}

describe("POST /campaigns/assign callers", () => {
  it("still sees every known caller (no silent membership loss)", () => {
    const found = new Set(findCallers().map((c) => c.path));
    const missing = KNOWN_CALLERS.filter((k) => !found.has(k));
    expect(
      missing,
      "These known callers are no longer detected by the scanner. Either the caller " +
        "was legitimately removed — delete it from KNOWN_CALLERS in the same change — " +
        "or it stopped matching a membership marker, in which case every check below " +
        "is now passing vacuously for it:\n" +
        missing.map((m) => `  - ${m}`).join("\n"),
    ).toHaveLength(0);
    // Floor as well as roster: catches a NEW caller appearing unreviewed only if the
    // roster is kept current, so the roster is the primary control and this is a
    // backstop against the roster itself being emptied.
    expect(found.size).toBeGreaterThanOrEqual(KNOWN_CALLERS.length);
  });

  it("every caller inspects `rejected` — a 200 does not mean a row was written", () => {
    const offenders = findCallers()
      .filter((c) => !c.text.includes("rejected"))
      .map((c) => c.path);
    expect(
      offenders,
      "These files POST /campaigns/assign but never read `rejected`. The server " +
        "returns 200 with `rejected` populated when a value does not resolve to a " +
        "vehicle by VIN, so these report success for a write that did not happen:\n" +
        offenders.map((o) => `  - ${o}`).join("\n"),
    ).toHaveLength(0);
  });

  it("no caller treats an empty `assigned` as failure without checking the other two lists", () => {
    // `assigned: []` is ambiguous on its own: it means "already assigned" (success)
    // as often as it means anything else. A caller may only call it a failure after
    // ruling out `alreadyAssigned` and `rejected`. This is the exact defect that made
    // the CS quick-assign error unclearable.
    const offenders = findCallers()
      .filter((c) => {
        const callsItFailure =
          /assigned(?:\?)?\.length\s*===\s*0/.test(c.text) ||
          /assigned(?:\?)?\.length\s*<\s*1/.test(c.text);
        if (!callsItFailure) return false;
        return !c.text.includes("alreadyAssigned");
      })
      .map((c) => c.path);
    expect(
      offenders,
      "These files branch on an empty `assigned` without consulting " +
        "`alreadyAssigned`, so an idempotent re-assign reads as a failure:\n" +
        offenders.map((o) => `  - ${o}`).join("\n"),
    ).toHaveLength(0);
  });

  it("no caller sends a `.vehicleId` as the assign target", () => {
    // The list takes VINs. `AssignmentVehicle` and the simulation vehicle entry both
    // carry `vehicleId` AND `vin`, so `.vehicleId` as the target is the original defect.
    //
    // Scoped to the PAYLOAD, not the whole file. An earlier revision matched any
    // `.vin || x.vehicleId` anywhere in a calling file and flagged two table column
    // renderers (`cell: (v) => v.vin || v.vehicleId` in the "VIN" column) that never
    // reach the API. That is a display choice — arguably a poor one, since it shows a
    // vehicleId under a VIN heading — but it is not this guard's claim, and letting an
    // over-broad guard demand unrelated edits is how guards get weakened or deleted.
    const offenders = findCallers()
      .filter((c) => {
        const lines = c.text.split("\n");
        // Collect payload-construction lines (plus a small forward window for a
        // multi-line array literal), then judge each LINE on its own.
        //
        // Per-line, not per-window: an earlier revision excluded the whole window if
        // it contained `withoutVin`, and mutation MG1 proved that vacuous — reverting
        // the payload to `v.vin || v.vehicleId` went UNDETECTED, because the adjacent
        // vin-less-vehicle error message put `withoutVin` in the same window. The
        // guard could not catch the one defect it exists for. Caught only by running
        // the mutation, not by reading the code.
        const suspect = new Set<number>();
        lines.forEach((line, i) => {
          if (
            /vehicles\s*[:=]/.test(line) ||
            /assignCampaignToVehicle\s*\(/.test(line)
          ) {
            // Window spans BACKWARD as well as forward. Mutation MG1 showed a
            // forward-only window misses the common shape where the target list is
            // built into a local (`const vins = …map(v => v.vin)`) several lines
            // ABOVE the `vehicles: vins` payload line — the variable name need not
            // contain "vehicles", so the construction itself matches no marker.
            for (
              let j = Math.max(i - 6, 0);
              j < Math.min(i + 4, lines.length);
              j++
            ) {
              suspect.add(j);
            }
          }
        });
        for (const i of suspect) {
          const line = lines[i];
          if (!/\.vehicleId/.test(line)) continue;
          // `withoutVin.map((v) => v.vehicleId)` NAMES the skipped vehicles in an
          // error message — correct handling, not a send. Judged on this line alone.
          if (/withoutVin/.test(line)) continue;
          return true;
        }
        return false;
      })
      .map((c) => c.path);
    expect(
      offenders,
      "These files send a vehicleId where the route requires a VIN. The server keys " +
        "the row `vehicle:{vin}` and the telemetry-campaign check reads it by VIN:\n" +
        offenders.map((o) => `  - ${o}`).join("\n"),
    ).toHaveLength(0);
  });
});
