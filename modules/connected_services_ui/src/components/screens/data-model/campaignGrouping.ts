/**
 * Group `GET /campaigns` rows into one entry per campaign.
 *
 * The `cms-{stage}-campaigns` table holds THREE kinds of row under one shape, which is
 * why the flat screen was misleading (spec § Context). Measured on staging 2026-09-20:
 *
 * | `targetArn`        | rows | stored `status`         | what it is                  |
 * |--------------------|------|-------------------------|-----------------------------|
 * | `template`         | 10   | `ACTIVE`                | a campaign DEFINITION       |
 * | `vehicle:<VIN>`    | 28   | `RUNNING`               | an ASSIGNMENT to one vehicle|
 * | `fleet:<id>`/`all` | 2    | `RUNNING`/`SUSPENDED`   | a fleet-wide assignment     |
 *
 * 40 rows for 10 campaigns, growing linearly with assignments.
 *
 * Pure: no fetch, no React. The join, the vehicle count and the three-way split are the
 * interesting logic, so they live here where they can be tested without rendering.
 *
 * Spec: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/spec.md`
 */

import type { DataProcessingCampaignItem } from "../../../api/dataModelClient";

/** `targetArn` value that marks a row as the campaign definition. */
const TEMPLATE_TARGET = "template";
/** `targetArn` prefix for a per-vehicle assignment. */
const VEHICLE_PREFIX = "vehicle:";
/** `targetArn` prefix for a fleet-scoped assignment. */
const FLEET_PREFIX = "fleet:";
/** `targetArn` value for a global assignment. */
const ALL_TARGET = "all";

/**
 * What an assignment row's stored `status` means for the operator.
 *
 * A closed union, so a consumer cannot render a stored token it has not glossed. The
 * stored vocabulary and the displayed vocabulary are deliberately different: `RUNNING`
 * means **assigned**, and nothing reconciles it against telemetry (spec § Context).
 *
 * `unknown` exists because the stored value is not constrained anywhere —
 * `PUT /campaigns/{id}` writes `status` straight from the request body with no allowlist
 * (`data_processing_api.py`), so any string is reachable.
 */
export type AssignmentState = "assigned" | "suspended" | "stopped" | "unknown";

/** Human gloss per state. Never a liveness word — see `AssignmentState`. */
export const ASSIGNMENT_STATE_LABEL: Readonly<Record<AssignmentState, string>> = {
  assigned: "assigned",
  suspended: "suspended",
  stopped: "stopped",
  unknown: "state unknown",
};

/**
 * Map a row's stored `status` onto the displayed vocabulary.
 *
 * Mapping per spec § "Status vocabulary": `RUNNING` → assigned, `SUSPENDED` → suspended,
 * `STOPPED` → stopped. Anything else is `unknown` and is NOT echoed — returning the
 * stored token would put the vocabulary the screen is replacing back on screen, which is
 * the defect review Cycle 2 found in the definition indicator.
 */
export function assignmentState(
  row: Pick<DataProcessingCampaignItem, "status">,
): AssignmentState {
  switch ((row.status || "").toUpperCase()) {
    case "RUNNING":
      return "assigned";
    case "SUSPENDED":
      return "suspended";
    case "STOPPED":
      return "stopped";
    default:
      return "unknown";
  }
}

export interface VehicleAssignment {
  readonly row: DataProcessingCampaignItem;
  /** The `targetArn` suffix — expected to be a VIN. */
  readonly target: string;
  /** This assignment's glossed state. See `assignmentState`. */
  readonly state: AssignmentState;
  /**
   * True when `target` cannot be a VIN.
   *
   * A SHAPE heuristic, deliberately conservative, and NOT the authoritative check — the
   * authoritative one is resolution against the vehicle catalog, which needs a fetch and
   * so belongs in the view, not in this pure function.
   *
   * Flags only values containing a character no VIN can contain: a hyphen, or any
   * lowercase letter. Verified against all 155 staging vehicles — every `vin` is
   * uppercase alphanumeric with no hyphen — so this cannot misfire on a real VIN. It
   * does catch a `vehicleId` (`VEH-CS-DEMO-0003`), which is the case that matters.
   *
   * Deliberately NOT a strict 17-char VIN regex: 8 of 155 staging VINs would fail one
   * (seven `DEMO…` values contain the letter `O`, excluded from the real VIN alphabet,
   * and `4T1B11HK0LU98765` is 16 characters). That trap is recorded in
   * `issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/`.
   *
   * Why flag rather than drop: staging holds `vehicle:VEH-CS-DEMO-0003`, written by the
   * pre-fix defect. It reads as a valid assignment, and dropping it is how it stayed
   * hidden. Surfacing it is the point.
   */
  readonly targetLooksInvalid: boolean;
}

export interface ScopedAssignment {
  readonly row: DataProcessingCampaignItem;
  /** `"fleet"` for `fleet:<id>`, `"all"` for a global row. */
  readonly scope: "fleet" | "all";
  /** The fleet id for `fleet:<id>`; `null` for `all`. */
  readonly fleetId: string | null;
  /** This assignment's glossed state. See `assignmentState`. */
  readonly state: AssignmentState;
}

/**
 * One campaign and its assignments.
 *
 * Named `DataCollectionCampaignGroup`, not `CampaignGroup`, to satisfy the repo's
 * software-campaign naming discipline (`src/__tests__/contentBoundary.test.ts`
 * Suite 3, "CMS collision risk"). That guard's message suggests `SoftwareCampaign*`;
 * using it here would be a LIE — these are data-collection campaigns governing which
 * telemetry signals a vehicle collects, not OTA software campaigns. The guard's real
 * requirement is that a bare `Campaign` identifier be disambiguated, and this name
 * disambiguates in the accurate direction.
 */
export interface DataCollectionCampaignGroup {
  readonly campaignName: string;
  /**
   * The definition row, or `null` when the campaign has assignments but no template.
   *
   * Reachable: the template is a separate record and nothing enforces its existence.
   * Consumers must degrade rather than hide the assignments (spec § Risks).
   */
  readonly template: DataProcessingCampaignItem | null;
  readonly vehicleAssignments: readonly VehicleAssignment[];
  /**
   * Fleet-wide and global assignments, kept OUT of `vehicleCount`.
   *
   * A `fleet:` row is not a vehicle. Counting it as one is the same class of error as
   * the flat screen counting a definition as a campaign.
   */
  readonly scopedAssignments: readonly ScopedAssignment[];
  /** Count of per-vehicle assignments only. */
  readonly vehicleCount: number;
  /** Rows whose `targetArn` matched none of the three known shapes. */
  readonly unrecognizedRows: readonly DataProcessingCampaignItem[];
}

function targetLooksInvalid(target: string): boolean {
  if (target.length === 0) return true;
  return target.includes("-") || target !== target.toUpperCase();
}

/**
 * Group campaign rows by `campaignName`.
 *
 * Keyed on `campaignName`, NOT `campaignId`. `campaignId` is `{campaignName}-{vin}` on an
 * assignment and `{campaignName}` on the template, so grouping by it would put every
 * assignment in its own group and defeat the entire restructure.
 *
 * Groups are returned sorted by `campaignName` for a stable render order — the API's
 * order is a DynamoDB scan order and is not stable.
 */
export function groupCampaigns(
  rows: readonly DataProcessingCampaignItem[],
): readonly DataCollectionCampaignGroup[] {
  const byName = new Map<
    string,
    {
      template: DataProcessingCampaignItem | null;
      vehicles: VehicleAssignment[];
      scoped: ScopedAssignment[];
      unrecognized: DataProcessingCampaignItem[];
    }
  >();

  for (const row of rows) {
    const name = row.campaignName;
    // A row with no campaignName cannot be grouped or displayed under a campaign.
    // Counting it somewhere arbitrary would inflate a number the screen exists to make
    // trustworthy, so it is skipped.
    if (!name) continue;

    let entry = byName.get(name);
    if (!entry) {
      entry = { template: null, vehicles: [], scoped: [], unrecognized: [] };
      byName.set(name, entry);
    }

    const target = row.targetArn ?? "";
    if (target === TEMPLATE_TARGET) {
      // Last template wins if a campaign somehow has two. Not expected; not worth
      // failing over, and picking one deterministically beats an undefined state.
      entry.template = row;
    } else if (target.startsWith(VEHICLE_PREFIX)) {
      const suffix = target.slice(VEHICLE_PREFIX.length);
      entry.vehicles.push({
        row,
        target: suffix,
        targetLooksInvalid: targetLooksInvalid(suffix),
        state: assignmentState(row),
      });
    } else if (target.startsWith(FLEET_PREFIX)) {
      entry.scoped.push({
        row,
        scope: "fleet",
        fleetId: target.slice(FLEET_PREFIX.length) || null,
        state: assignmentState(row),
      });
    } else if (target === ALL_TARGET) {
      entry.scoped.push({
        row,
        scope: "all",
        fleetId: null,
        state: assignmentState(row),
      });
    } else {
      entry.unrecognized.push(row);
    }
  }

  return [...byName.entries()]
    .map(([campaignName, e]) => ({
      campaignName,
      template: e.template,
      vehicleAssignments: e.vehicles,
      scopedAssignments: e.scoped,
      vehicleCount: e.vehicles.length,
      unrecognizedRows: e.unrecognized,
    }))
    .sort((a, b) => a.campaignName.localeCompare(b.campaignName));
}


/** The `owner` value that marks a campaign as belonging to the OEM. */
const OEM_OWNER = "oem";

/**
 * Keep only campaigns the CS operator is entitled to see.
 *
 * Applied to **groups**, never to rows, and that distinction is the whole point.
 *
 * `owner` means two different things depending on the row it sits on. On a `template` it
 * is campaign ownership — the thing spec Decision 4 (`2026-09-15-cms-cs-campaign-ownership`)
 * intends to filter. On an **assignment** it is attribution: who created *that
 * assignment*. Filtering rows before grouping conflates the two, and the result is a
 * silent under-count rather than a visibility rule. Measured on staging 2026-09-20:
 * `cms-fleet-gps-10s` has 22 `vehicle:` assignments, 12 written with `owner: oem` and 10
 * with `owner: platform` by `_ensure_telemetry_campaign` / `deploy_vehicle_campaign.py`.
 * Those 10 are assignments OF THE OEM'S OWN CAMPAIGN, so dropping them reported 12 where
 * spec § Context asks for 22 — the single number this restructure exists to produce.
 * Its `fleet:FLEET-1780002982` row is `platform` too, so the campaign's fleet scope
 * vanished as well. Found by review Cycle 2.
 *
 * So a campaign is shown with its FULL assignment set, or not shown at all. Never partly.
 *
 * Re-derive the counts above (read-only):
 * <!-- verify: aws dynamodb scan --table-name cms-staging-campaigns --region us-west-2
 *      --output json | python3 -c "import json,sys,collections; its=json.load(sys.stdin)['Items'];
 *      g=lambda i,k: (list(i[k].values())[0] if k in i else None);
 *      print(collections.Counter((g(i,'campaignName'),g(i,'owner')) for i in its
 *      if (g(i,'targetArn') or '').startswith('vehicle:')))" -->
 *
 * Visibility rule:
 *   - has a template → visible iff the TEMPLATE is oem-owned;
 *   - no template    → visible iff at least one assignment row is oem-owned.
 *
 * The second clause is a deliberate decision, not a fallthrough. A template-less group has
 * no ownership signal other than its assignments, and one exists live:
 * `cms-fleet-telemetry-30s` has a single `all` row with `owner: oem` and no template.
 * Hiding it would remove a campaign the operator can see today; letting the pre-grouping
 * filter decide it by accident is what Cycle 2 objected to. A fleet-owned campaign has
 * fleet-owned assignments and stays hidden under both clauses.
 *
 * Note what this does NOT do: it does not widen visibility to fleet-owned campaigns, and
 * it does not make a non-oem *campaign* reachable. It only stops an oem campaign's own
 * coverage from being partly hidden by the attribution on its assignment rows.
 *
 * ## What a CS operator can see through this, and what secures it
 *
 * Named explicitly because this function IS the tenant boundary for this screen, and a
 * downstream deployer with a different tenant model needs to find it. Including a
 * non-`oem`-attributed assignment discloses, for a campaign the operator can already see:
 * the assignment's `owner`, its `targetArn` (hence the VIN or fleet id), its status and its
 * `createdAt`. Before the group-level filter those rows were hidden.
 *
 * Accepted because the OEM sits above fleet operators in this model — the operator is
 * asking "how many vehicles run MY campaign", and a number that silently excludes
 * assignments someone else created is the defect. Verified by security review cycle 1: no
 * code path lets a non-OEM caller author `owner: "oem"`. `_derive_owner` in
 * `data_processing_api.py` derives it from server-validated Cognito group membership,
 * `update_campaign` deliberately omits `owner` from its update expression, and
 * `_ensure_telemetry_campaign` / `simulation_api::_assign_campaigns` hard-code
 * `"platform"`. So the template-less clause below cannot be induced by a fleet operator
 * forging an `oem`-attributed row.
 *
 * If you are adapting this for a model where fleets must NOT be visible to the OEM, this
 * is the function to change — not the row filter it replaced, which under-reported counts.
 */
export function filterOemOwnedGroups(
  groups: readonly DataCollectionCampaignGroup[],
): readonly DataCollectionCampaignGroup[] {
  return groups.filter((g) => {
    if (g.template !== null) return g.template.owner === OEM_OWNER;
    const assignmentRows = [
      ...g.vehicleAssignments.map((v) => v.row),
      ...g.scopedAssignments.map((s) => s.row),
      ...g.unrecognizedRows,
    ];
    return assignmentRows.some((r) => r.owner === OEM_OWNER);
  });
}
