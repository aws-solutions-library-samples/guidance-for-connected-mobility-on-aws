// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DataCollectionCampaignsView — data-collection campaigns screen.
 *
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend/tasks.md` T5.2, T5.3
 *       `.kiro/specs/2026-09-15-cms-cs-campaign-ownership/tasks.md` T5.1, T5.2
 *
 * ## What this screen shows
 *
 * One row per data-collection campaign owned by the OEM. These are NOT OTA software
 * campaigns. Since the 2026-09-20 restructure the row is a `DataCollectionCampaignGroup`
 * (template joined with its assignments), not a raw API record — the flat screen listed
 * one row per record and so counted assignments as campaigns.
 *
 * Columns, all either derived from the group or read off its template:
 *
 *   - campaignName      — the campaign name (the group key), linking to the detail view, with
 *                         an inline "No definition" marker on the one campaign that has no
 *                         template row
 *   - assignments       — rollup: vehicle count, fleet/global scope, non-assigned states
 *   - decoderManifestId — decoder manifest reference (the decoding recipe)
 *   - signalsToCollect  — entry count of the template's declared list
 *   - collectionScheme  — e.g. "TIME_BASED · 10000 ms"
 *   - createdAt         — the template's creation DATE (day only; the full timestamp is in the
 *                         detail view)
 *
 * Six columns, not eight. `definitionState` and `owner` were dropped at user UAT: the first
 * because its header did not communicate and it reported an anomaly's absence on every row,
 * the second because `filterOemOwnedGroups` guarantees every VISIBLE group is OEM-entitled, so
 * the column read "oem" on all ten template-backed rows and "—" on the eleventh. A column with
 * one value carries no information and costs width. Both were contributing to a horizontal
 * scroll, which user UAT called out as unacceptable.
 *
 * Column visibility is operator-controlled via `ConnectedServicesTablePreferences`, but that
 * applies only to the SIX columns below. `definitionState` and `owner` are genuinely gone —
 * they are absent from `buildCampaignColumns` and from `visibleContentOptions`, so no
 * preference state can restore them. An earlier version of this note claimed they were
 * "recoverable per-session", which was false: exactly the claim-without-behaviour shape this
 * file has been corrected for twice already.
 *
 * What replaces each, so the information is not simply lost:
 *   - `definitionState` → the inline "No definition" marker on the name cell, plus
 *     `CampaignDetailView`'s `DefinitionStatusBadge` for the full glossed state. Both
 *     test-backed.
 *   - `owner` → nothing, and nothing is needed: `filterOemOwnedGroups` makes it constant across
 *     every visible row, so the column could not have told an operator anything.
 *
 * There is deliberately no `targetArn` column (it is the grouping discriminator, not a
 * row field) and no raw `status` column (it carried two vocabularies at once — see
 * `CampaignDetailView`'s `DefinitionStatusBadge`, which now owns that vocabulary).
 *
 * Entitlement is applied to GROUPS by `filterOemOwnedGroups`, not to rows, under two
 * clauses: a campaign WITH a template is visible iff that template is `owner == "oem"`;
 * a campaign with NO template is visible iff at least one of its assignment rows is
 * `oem`-attributed. The second clause matters in practice — `cms-fleet-telemetry-30s` is
 * live, has no template, and is on screen through it.
 *
 * What must NOT happen is filtering the raw rows by `owner` before grouping: on an
 * assignment row `owner` is attribution for that assignment, so a row filter hides part of
 * a visible campaign's coverage and under-reports the vehicle count. That is the only
 * sense in which assignment `owner` is unusable for filtering; the fallback clause above
 * reads it to decide whether a whole group is visible, which is a different question.
 *
 * ## Campaign assignment (T5.1, T5.2)
 *
 * Assignment goes through the data-processing `POST /campaigns/assign` endpoint,
 * which takes a `vehicles` array (NOT `vins` or `vinList`) of **VINs, not
 * vehicleIds** — the server keys the row `vehicle:{vin}`.
 *
 * Do NOT "always assert on `assigned.length`" — that was the T5.1 constraint and it
 * is RETRACTED. The write is conditional, so a repeat assignment legitimately
 * returns `assigned: []` with the VIN in `alreadyAssigned` (success), and a refused
 * entry returns it in `rejected` (failure). Branching on `assigned.length` alone
 * cannot tell those apart, which is the defect in
 * `issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/`.
 *
 * The `canAssignCampaignToVehicle` guard validates the vehicle has a
 * `modelManifestName` before enabling assignment. The 48 OEM1 vehicles without
 * this field are blocked (spec Decision 4, Group 5.2). The guard is called in
 * `handleAssignConfirm` before any API call — deleting the call site causes the
 * guard-wired test to fail (T5.2 mutation-verified).
 *
 * ## settleMarker
 *
 * "cs-settle-data-collection-campaigns-table" must appear in this component's
 * rendered body. Required by registryCompleteness.test.tsx P1.
 */

import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import Modal from "@cloudscape-design/components/modal";
import Pagination from "@cloudscape-design/components/pagination";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getHeaderCounterText, getTextFilterCounterText } from "../../commons/tableI18n";
import { useConnectedServicesCollection } from "../../commons/useConnectedServicesCollection";
import {
  ConnectedServicesTablePreferences,
  type ConnectedServicesPreferences,
  type ContentDisplayOption,
} from "../../commons/ConnectedServicesTablePreferences";
import {
  assignCampaignToVehicle,
  fetchDataProcessingCampaigns,
  getDataProcessingApiBase,
} from "../../../api/dataModelClient";
import type { FleetCampaignItem, DataProcessingCampaignItem } from "../../../api/dataModelClient";
import {
  ASSIGNMENT_STATE_LABEL,
  groupCampaigns,
  filterOemOwnedGroups,
  type AssignmentState,
  type DataCollectionCampaignGroup,
} from "./campaignGrouping";

// ── settleMarker ──────────────────────────────────────────────────────────────

/** Unique to this screen. Asserted by registryCompleteness.test.tsx P1. */
export const SETTLE_MARKER = "cs-settle-data-collection-campaigns-table";

// ── Status types ──────────────────────────────────────────────────────────────

type LoadStatus = "loading" | "unavailable" | "error" | "ready";

// ── Model-match guard (T5.3) ──────────────────────────────────────────────────

/**
 * A vehicle entry used for campaign assignment validation.
 *
 * Comes from the simulation vehicle picker (simulationClient / subscriptionsClient).
 * `modelManifestName` is the binding that links a vehicle to its decoder manifest.
 * 101 of 149 staging vehicles carry it; the 48 OEM1 vehicles do not — those
 * must be blocked from assignment because the model cannot be validated.
 */
export interface AssignmentVehicle {
  readonly vehicleId: string;
  readonly vin?: string;
  readonly modelManifestName?: string;
  readonly producer?: string;
}

/**
 * Check whether a vehicle can be assigned to a campaign.
 *
 * Guard: the vehicle MUST have a `modelManifestName` set. A missing binding means
 * the ECUs' signal coverage cannot be verified, so assignment is blocked.
 *
 * Note: the current CMS campaign records carry `decoderManifestId` (the decoding
 * recipe) rather than a model manifest name. The guard verifies that BOTH the
 * vehicle binding AND the campaign decoder reference are present — an absent
 * decoder manifest id on the campaign side also blocks assignment since the
 * model→manifest→decoder chain cannot be established.
 *
 * @param vehicle   - The vehicle to test.
 * @param campaign  - The campaign to assign.
 * @returns true only when the vehicle has a modelManifestName AND the campaign
 *          has a decoderManifestId, indicating both ends of the binding exist.
 *          Returns false in all other cases (absent binding, absent decoder ref).
 */
export function canAssignCampaignToVehicle(
  vehicle: AssignmentVehicle,
  dcCampaign: FleetCampaignItem,
): boolean {
  // Vehicle must have a model manifest binding.
  if (!vehicle.modelManifestName) return false;
  // Campaign must have a decoder manifest reference to validate against.
  if (!dcCampaign.decoderManifestId) return false;
  return true;
}

// ── Status badge ──────────────────────────────────────────────────────────────

/*
 * `CampaignDefinitionIndicator` was removed here at user UAT (2026-09-20).
 *
 * It rendered the "Definition" column, which the operator could not interpret ("I'm not sure
 * what 'definition' means… we probably don't need that as a column header") and which read
 * "Available" on every row but one — a permanently-wide column reporting the absence of an
 * anomaly.
 *
 * The property it carried is NOT dropped. Two things replaced it:
 *   - the missing-definition case is an inline exception marker on the campaign-name cell,
 *     rendered only for the one live campaign that has no template row; and
 *   - the closed five-gloss vocabulary, and the rule that the stored token is never echoed,
 *     moved to `CampaignDetailView`'s `DefinitionStatusBadge`, which is now the only place
 *     that vocabulary reaches a screen. `src/__tests__/campaignStatusVocabulary.test.ts`
 *     follows it there.
 *
 * Deleted rather than left unused: a dead function that a guard is anchored to is how a guard
 * comes to assert something no operator can see.
 */

/**
 * Assignment rollup — how many vehicles, at what scope, in what state.
 *
 * Says "Assigned", never "Running", and never reports an assignment's existence without
 * its state. A `SUSPENDED` assignment presented as "Assigned to all fleets" is the same
 * over-claim as rendering `RUNNING` as "Running", one column over — which is what this
 * cell did until review Cycle 2 caught it against live `cms-fleet-telemetry-30s`.
 */
function CampaignAssignmentSummary({
  group,
}: {
  group: DataCollectionCampaignGroup;
}): React.ReactElement {
  const name = group.campaignName;
  const unrecognized = group.unrecognizedRows.length;

  if (
    group.vehicleCount === 0 &&
    group.scopedAssignments.length === 0 &&
    unrecognized === 0
  ) {
    return (
      <Box color="text-status-inactive" data-testid={`dc-campaign-unassigned-${name}`}>
        Not assigned to any vehicle
      </Box>
    );
  }

  const parts: string[] = [];

  if (group.vehicleCount > 0) {
    // Non-`assigned` states are named. Folding them into the count is what made a
    // suspended assignment read as an active one.
    const byState = new Map<AssignmentState, number>();
    for (const v of group.vehicleAssignments) {
      byState.set(v.state, (byState.get(v.state) ?? 0) + 1);
    }
    const noted = [...byState.entries()]
      .filter(([state]) => state !== "assigned")
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([state, n]) => `${String(n)} ${ASSIGNMENT_STATE_LABEL[state]}`);
    const vehicles = `${String(group.vehicleCount)} vehicle${group.vehicleCount === 1 ? "" : "s"}`;
    parts.push(noted.length > 0 ? `${vehicles} (${noted.join(", ")})` : vehicles);
  }

  // Fleet-wide and global assignments are NOT vehicles and are reported separately, so
  // the vehicle number stays trustworthy.
  for (const s of group.scopedAssignments) {
    const scope = s.scope === "all" ? "all fleets" : `fleet ${s.fleetId ?? "?"}`;
    parts.push(
      s.state === "assigned"
        ? scope
        : `${scope} (${ASSIGNMENT_STATE_LABEL[s.state]})`,
    );
  }

  // `unrecognizedRows` was computed and discarded, so a campaign whose only row had an
  // unrecognised `targetArn` read "Not assigned to any vehicle" — a campaign with an
  // assignment reported as having none. Unfired today: all five `targetArn` writers in the
  // repo emit `template`, `vehicle:{vin}` or `fleet:{id}`. Surfacing it is the bucket's
  // whole purpose, and the field is named `targetArn`, which is an invitation.
  if (unrecognized > 0) {
    parts.push(
      `${String(unrecognized)} assignment${unrecognized === 1 ? "" : "s"} with an unrecognized target`,
    );
  }

  return (
    <Box data-testid={`dc-campaign-assigned-summary-${name}`}>{`Assigned to ${parts.join(", ")}`}</Box>
  );
}

// ── Column definitions ─────────────────────────────────────────────────────────
//
// One row per CAMPAIGN. Columns are either derived from the group (`assignments`) or read off
// the group's template — which may be absent, so every template-backed column has an explicit
// "—" branch.
//
// Column ids are pinned by `COLUMN_IDS` in the view's test file. Two columns the flat
// screen had are deliberately gone: `targetArn` (now the grouping discriminator, not a row
// field) and `status` (it carried two vocabularies at once — the glossed definition state now
// lives only in `CampaignDetailView`'s `DefinitionStatusBadge`).
//
// NOT included: rolloutPct, canary stages, recall completion — those are OTA fields.
//
// No `sortingField` anywhere: the `Table` has no `sortingColumn`/`onSortingChange` and no
// `useCollection`, so nothing sorts. The two that were here claimed otherwise, and one of
// them (`createdAt`) named a field that does not exist on the group type at all. Rows
// arrive sorted by `campaignName` from `groupCampaigns`.
//
// Row click: navigates to `/data-model/data-collection-campaigns/:campaignName`
// (T2.1 Accept 3, deferred from Group 2 until CampaignDetailView existed — T3.1).
// The navigate() call is hoisted out of the column definition so it can close over
// the view's `navigate` instance, which requires this factory to be used inside the
// component rather than at module scope. See DataCollectionCampaignsView JSX.

function buildCampaignColumns(
  navigate: (path: string) => void,
): Parameters<typeof Table>[0]["columnDefinitions"] {
  return [
  {
    id: "campaignName",
    header: "Campaign name",
    sortingField: "campaignName",
    cell: (row: DataCollectionCampaignGroup) => (
      <SpaceBetween size="xxs" direction="horizontal">
        <Link
          onFollow={(e) => {
            e.preventDefault();
            void navigate(
              `/data-model/data-collection-campaigns/${encodeURIComponent(row.campaignName)}`,
            );
          }}
          href={`/data-model/data-collection-campaigns/${encodeURIComponent(row.campaignName)}`}
          data-testid={`dc-campaign-name-${row.campaignName}`}
        >
          {row.campaignName}
        </Link>
        {/*
          The missing-definition signal, inline and only when it occurs.
          
          This replaces the former "Definition" column, dropped at user UAT: the header did not
          say what it meant, and every campaign is EXPECTED to have a definition — so a column
          that is "Available" on every row but one is a wide, permanent reminder of a condition
          that is actually an anomaly. Live, exactly one campaign has no template row
          (`cms-fleet-telemetry-30s`), so the information still has to reach the operator; it
          belongs next to the name as an exception marker, not in a column of its own.
          The detail view states it fully.
        */}
        {row.template === null && (
          <StatusIndicator
            type="warning"
            data-testid={`dc-campaign-definition-missing-${row.campaignName}`}
          >
            No definition
          </StatusIndicator>
        )}
      </SpaceBetween>
    ),
  },
  {
    id: "assignments",
    header: "Assigned to",
    cell: (row: DataCollectionCampaignGroup) => (
      <CampaignAssignmentSummary group={row} />
    ),
  },
  {
    id: "decoderManifestId",
    header: "Decoder manifest",
    cell: (row: DataCollectionCampaignGroup) =>
      row.template?.decoderManifestId ? (
        <Box data-testid={`dc-campaign-decoder-${row.campaignName}`}>
          {row.template.decoderManifestId}
        </Box>
      ) : (
        <Box color="text-status-inactive" data-testid={`dc-campaign-decoder-absent-${row.campaignName}`}>
          —
        </Box>
      ),
  },
  {
    id: "signalsToCollect",
    header: "Signals",
    cell: (row: DataCollectionCampaignGroup) => {
      // Entry count, not distinct — this is the definition's declared list, and
      // `signalCount` on the record agrees with it. The detail view (Group 3) reports
      // DISTINCT resolved signals, which is lower: 293 entries -> 286 distinct on
      // `cms-fleet-gps-10s`, because 7 ids repeat. The two numbers differ legitimately
      // and the detail view says so rather than silently disagreeing with this column.
      const count = row.template?.signalsToCollect?.length ?? null;
      if (count === null) {
        return (
          <Box color="text-status-inactive" data-testid={`dc-campaign-signals-absent-${row.campaignName}`}>
            —
          </Box>
        );
      }
      return (
        <Box data-testid={`dc-campaign-signals-${row.campaignName}`}>
          {String(count)}
        </Box>
      );
    },
  },
  {
    id: "collectionScheme",
    header: "Collection scheme",
    cell: (row: DataCollectionCampaignGroup) => {
      const cs = row.template?.collectionScheme;
      if (!cs) {
        return (
          <Box color="text-status-inactive" data-testid={`dc-campaign-scheme-absent-${row.campaignName}`}>
            —
          </Box>
        );
      }
      const type = typeof cs.type === "string" ? cs.type : "";
      const periodMs = typeof cs.periodMs === "number" ? cs.periodMs : null;
      const label = periodMs !== null ? `${type} · ${String(periodMs)} ms` : type || "—";
      return (
        <Box data-testid={`dc-campaign-scheme-${row.campaignName}`}>{label}</Box>
      );
    },
  },
  {
    id: "createdAt",
    header: "Created",
    // Deliberately NOT sortable. `createdAt` lives on `group.template`, not on
    // `DataCollectionCampaignGroup`, so `sortingField: "createdAt"` would render a sortable
    // header that does nothing when clicked. That exact claim-without-behaviour was a review
    // Cycle 1 finding on this file; it was reintroduced here while wiring sorting for user UAT
    // and caught by the guard that finding produced.
    cell: (row: DataCollectionCampaignGroup) => {
      // Date only. The stored value is a full ISO-8601 timestamp
      // ("2026-01-01T00:00:00Z") — 20 unbreakable characters in every row, which was a
      // material contributor to the horizontal scroll this column set had to lose. The
      // time-of-day of a campaign definition is not information an operator reads a list for;
      // the detail view carries the full value.
      const raw = row.template?.createdAt;
      if (!raw) {
        return (
          <Box color="text-status-inactive" data-testid={`dc-campaign-created-${row.campaignName}`}>
            —
          </Box>
        );
      }
      // Split rather than parse: a Date round-trip would apply the viewer's timezone and
      // could shift the displayed day, which is a worse failure than showing the stored day.
      const dayOnly = raw.split("T")[0];
      return (
        <Box data-testid={`dc-campaign-created-${row.campaignName}`}>{dayOnly}</Box>
      );
    },
  },
  ];
}

// ── DataCollectionCampaignsView ────────────────────────────────────────────────

/**
 * DataCollectionCampaignsView
 *
 * Lists OEM-owned data-collection campaigns, one row per campaign, from the
 * data-processing campaigns API. Entitlement is applied per group by
 * `filterOemOwnedGroups` — see the file docstring for why not per row.
 *
 * Provides per-vehicle campaign assignment via `POST /campaigns/assign`, gated by
 * the `canAssignCampaignToVehicle` guard.
 *
 * @param vehiclesForAssignment - Optional list of vehicles for the assignment picker.
 *   When provided, the modal shows each vehicle as a Select option with the guard
 *   reflected in the Confirm button's disabled state. When absent — which is the case for
 *   the deployed view, since `App.tsx` passes no such prop — the modal has nothing to
 *   choose from and Confirm stays disabled. There is NO text-entry fallback; an earlier
 *   version of this doc claimed the operator could type a vehicle ID, and no such input
 *   exists. The dead end is spec § Context Defect 3; the picker is wired in Group 3.
 */
const DataCollectionCampaignsView: React.FC<{
  vehiclesForAssignment?: readonly AssignmentVehicle[];
}> = ({ vehiclesForAssignment }) => {
  const navigate = useNavigate();
  const [loadStatus, setLoadStatus] = useState<LoadStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const [campaigns, setCampaigns] = useState<readonly FleetCampaignItem[]>([]);

  // Assignment modal state
  const [assignModalOpen, setAssignModalOpen] = useState(false);
  const [selectedCampaign, setSelectedCampaign] = useState<FleetCampaignItem | null>(null);
  const [selectedVehicle, setSelectedVehicle] = useState<AssignmentVehicle | null>(null);
  const [assignStatus, setAssignStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [assignError, setAssignError] = useState<string | null>(null);
  const [assignedCount, setAssignedCount] = useState<number>(0);

  // Load campaigns on mount
  useEffect(() => {
    const base = getDataProcessingApiBase();
    if (!base) {
      setLoadStatus("unavailable");
      return;
    }

    let cancelled = false;
    setLoadStatus("loading");
    setError(null);

    fetchDataProcessingCampaigns()
      .then((resp) => {
        if (cancelled) return;
        if (resp === null) {
          setLoadStatus("unavailable");
        } else {
          // The response is stored UNFILTERED and entitlement is applied to the grouped
          // campaigns instead (`filterOemOwnedGroups`). Filtering rows here was the
          // Cycle 2 defect: `owner` on an assignment row is attribution for that
          // assignment, not campaign ownership, so a row filter silently dropped 10 of
          // `cms-fleet-gps-10s`'s 22 assignments and reported 12. The rule and the live
          // numbers are documented on `filterOemOwnedGroups`.
          setCampaigns(resp.dcCampaigns);
          setLoadStatus("ready");
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoadStatus("error");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const handleAssignClick = useCallback((selectedItem: FleetCampaignItem) => {
    setSelectedCampaign(selectedItem);
    setSelectedVehicle(null);
    setAssignStatus("idle");
    setAssignError(null);
    setAssignedCount(0);
    setAssignModalOpen(true);
  }, []);

  const handleAssignConfirm = useCallback(async () => {
    if (!selectedCampaign || !selectedVehicle) return;

    // Guard (T5.2): validate model binding before any API call.
    // This call is LOAD-BEARING — deleting it means vehicles without
    // modelManifestName can be assigned, bypassing model validation.
    // The guard-wired test in DataCollectionCampaignsView.test.tsx
    // (T5.2 mutation) will FAIL if this call is removed.
    if (!canAssignCampaignToVehicle(selectedVehicle, selectedCampaign)) {
      setAssignError(
        "This vehicle cannot be assigned: it has no model manifest binding (modelManifestName is absent). " +
        "Assignment is blocked because the campaign's signal coverage cannot be validated against an unbound vehicle."
      );
      setAssignStatus("error");
      return;
    }

    setAssignStatus("loading");
    setAssignError(null);
    try {
      // The field name is `vehicles` (plural), NOT `vins` or `vinList` — and the list
      // takes the **VIN**, not the vehicleId. The server keys the row
      // `vehicle:{vin}` and the telemetry-campaign check reads it by VIN, so a
      // vehicleId writes a row nothing can find; since 2026-09-20 the server rejects
      // values that do not resolve. `AssignmentVehicle` carries both fields, and this
      // call site passed `.vehicleId` until review cycle 2 of Fix Group 10 caught it.
      // It was latent only because `App.tsx` supplies no `vehiclesForAssignment`, so
      // `selectedVehicle` is always null here — it would have armed the moment anyone
      // wired that prop. See
      // issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/.
      if (!selectedVehicle.vin) {
        setAssignError(
          `No VIN on record for ${selectedVehicle.vehicleId} — cannot assign. ` +
          "A campaign is keyed by VIN; assigning without one would create a record " +
          "the simulator cannot see."
        );
        setAssignStatus("error");
        return;
      }
      const result = await assignCampaignToVehicle(
        selectedCampaign.campaignName,
        selectedVehicle.vin,
      );
      if (result === null) {
        setAssignError("Data-processing API is not configured.");
        setAssignStatus("error");
        return;
      }
      // Three outcomes, three lists. `assigned: []` alone is NOT a failure and NOT
      // evidence of an idempotent skip — the previous message here asserted the skip
      // as the likely cause, which is the reading T10.2 retracted. `rejected` is the
      // real failure; `alreadyAssigned` is success.
      const refused = result.rejected ?? [];
      const already = result.alreadyAssigned ?? [];
      setAssignedCount(result.assigned.length);
      if (refused.length > 0) {
        setAssignError(`Assignment refused: ${refused[0].reason}`);
        setAssignStatus("error");
      } else if (result.assigned.length === 0 && already.length === 0) {
        setAssignError(
          "The API returned 200 but wrote nothing and refused nothing. " +
          "Check the data-processing API logs before retrying."
        );
        setAssignStatus("error");
      } else {
        setAssignStatus("success");
      }
    } catch (err: unknown) {
      setAssignError(err instanceof Error ? err.message : String(err));
      setAssignStatus("error");
    }
  }, [selectedCampaign, selectedVehicle]);

  // Whether the selected vehicle passes the guard (for disabling the Confirm button
  // and showing a stated reason).
  const canAssignSelected = useMemo(
    () =>
      selectedVehicle && selectedCampaign
        ? canAssignCampaignToVehicle(selectedVehicle, selectedCampaign)
        : false,
    [selectedVehicle, selectedCampaign],
  );

  // One row per campaign, not per record. `groupCampaigns` joins the template with its
  // assignments and keeps `fleet:`/`all` rows out of the vehicle count;
  // `filterOemOwnedGroups` then applies Decision 4 entitlement
  // (spec `2026-09-15-cms-cs-campaign-ownership`) at the GROUP level, so a visible
  // campaign always shows its full assignment set.
  //
  // This replaced `activeCampaigns` (rows where `targetArn !== "template"`), which is
  // what made the screen misleading: it listed ASSIGNMENTS and counted them as
  // campaigns, so assigning one campaign to 8 vehicles added 8 top-level rows. 40 rows
  // for 11 campaigns on staging. The template is now the row, not an exclusion.
  const campaignGroups = useMemo(
    () => filterOemOwnedGroups(groupCampaigns(campaigns)),
    [campaigns],
  );

  // Column definitions — built with the `navigate` function so the row click target
  // is set once per render, not per cell. T2.1 Accept 3 (deferred to T3.1):
  //   row click → navigate(`/data-model/data-collection-campaigns/${campaignName}`)
  const campaignColumns = useMemo(
    () => buildCampaignColumns(navigate),
    [navigate],
  );

  // ── Table preferences, filtering, pagination ──────────────────────────────
  //
  // Matches the established CS-portal table treatment (`DataProductsView`,
  // `AvailableVehiclesView`): text filter, pagination, per-column visibility and page size.
  // Added at user UAT — this table previously had none of it, and was the only live-data
  // table in the portal rendering a bare `<Table>`.
  const [preferences, setPreferences] = useState<ConnectedServicesPreferences>({
    pageSize: 25,
    contentDisplay: [
      { id: "campaignName", visible: true },
      { id: "assignments", visible: true },
      { id: "decoderManifestId", visible: true },
      { id: "signalsToCollect", visible: true },
      { id: "collectionScheme", visible: true },
      { id: "createdAt", visible: true },
    ],
  });

  const visibleContentOptions: ContentDisplayOption[] = useMemo(
    () => [
      { id: "campaignName", label: "Campaign name", alwaysVisible: true },
      { id: "assignments", label: "Assigned to" },
      { id: "decoderManifestId", label: "Decoder manifest" },
      { id: "signalsToCollect", label: "Signals" },
      { id: "collectionScheme", label: "Collection scheme" },
      { id: "createdAt", label: "Created" },
    ],
    [],
  );

  const handleClearFilter = useCallback(() => {
    collection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const collection = useConnectedServicesCollection<DataCollectionCampaignGroup>(
    campaignGroups,
    {
      resourceName: "data-collection campaigns",
      pageSize: preferences.pageSize ?? 25,
      onClearFilter: handleClearFilter,
    },
  );

  const { items, filteredItemsCount, collectionProps, filterProps, paginationProps } =
    collection;

  // Apply column-visibility preferences.
  const visibleCampaignColumns = useMemo(() => {
    const displayIds = new Set(
      (preferences.contentDisplay ?? []).filter((c) => c.visible).map((c) => c.id),
    );
    if (displayIds.size === 0) return campaignColumns;
    return campaignColumns.filter((col) => col.id && displayIds.has(col.id));
  }, [campaignColumns, preferences.contentDisplay]);

  // Templates available for assignment, taken from the VISIBLE groups — never from the
  // raw response, which now carries fleet-owned campaigns too. Derived this way, the list
  // is also sorted by `campaignName` (groups are), so the Assign button's
  // `templateCampaigns[0]` is deterministic rather than DynamoDB scan order.
  const templateCampaigns = useMemo(
    () =>
      campaignGroups
        .map((g) => g.template)
        .filter((t): t is DataProcessingCampaignItem => t !== null),
    [campaignGroups],
  );

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registryCompleteness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="dc-campaigns-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {loadStatus === "loading" && (
        <Box textAlign="center" padding={{ vertical: "xl" }} data-testid="dc-campaigns-loading">
          <Spinner size="large" />
        </Box>
      )}

      {loadStatus === "unavailable" && (
        <Alert
          type="info"
          header="Data-processing API not configured"
          data-testid="dc-campaigns-unconfigured"
        >
          The data-processing API endpoint is not configured for this stage.
          Data-collection campaign data is unavailable.
        </Alert>
      )}

      {loadStatus === "error" && (
        <Alert
          type="error"
          header="Failed to load campaigns"
          data-testid="dc-campaigns-error"
        >
          {error ?? "An unexpected error occurred loading campaign data."}
        </Alert>
      )}

      {loadStatus === "ready" && (
        <Container
          header={
            <Header
              variant="h1"
              description="OEM-owned data-collection campaigns — configurations that govern what telemetry signals each vehicle collects. Not OTA software campaigns."
              counter={getHeaderCounterText(filteredItemsCount, campaignGroups.length)}
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  {templateCampaigns.length > 0 && (
                    <Button
                      variant="primary"
                      onClick={() => handleAssignClick(templateCampaigns[0])}
                      data-testid="dc-campaigns-assign-button"
                    >
                      Assign campaign
                    </Button>
                  )}
                </SpaceBetween>
              }
            >
              Data Collection Campaigns
            </Header>
          }
          data-testid="dc-campaigns-container"
        >
          <Table
            {...collectionProps}
            columnDefinitions={visibleCampaignColumns}
            items={items}
            // NO `empty=` prop. `collectionProps` already carries it, and the hook resolves it
            // to the CORRECT ONE OF TWO states — `TableEmptyState` when there is no data at
            // all, `TableNoMatchState` (with a "Clear filter" button) when a filter matched
            // nothing.
            //
            // Passing `empty=` after the spread overrode that, so a filter matching nothing
            // rendered "No data-collection campaigns to display." beside a counter reading
            // "0 matches out of 3" — the screen contradicting itself, and the Clear-filter
            // recovery never appearing. It also left `handleClearFilter` unreachable.
            //
            // The line was correct before this table used a collection hook and survived the
            // F9.1 rewrite as an unchanged context line. Neither sibling this table was
            // modelled on (DataProductsView, AvailableVehiclesView) passes `empty=`; of the 11
            // collection tables in the portal, this was the only one that did.
            data-testid="dc-campaigns-table"
            // `wrapLines` and `resizableColumns` together are what keep this table inside the
            // viewport. The "Assigned to" rollup is open-ended text ("Assigned to 24 vehicles
            // (2 suspended), fleet FLEET-1780002982"), so without wrapping it widens the table
            // until it scrolls horizontally — which is what user UAT reported.
            wrapLines={true}
            resizableColumns={true}
            stickyHeader={true}
            enableKeyboardNavigation={true}
            ariaLabels={{
              tableLabel: "Data-collection campaigns table",
            }}
            filter={
              <TextFilter
                {...filterProps}
                filteringPlaceholder="Find a campaign"
                countText={getTextFilterCounterText(
                  filteredItemsCount ?? campaignGroups.length,
                  campaignGroups.length,
                )}
              />
            }
            pagination={<Pagination {...paginationProps} />}
            preferences={
              <ConnectedServicesTablePreferences
                preferences={preferences}
                onConfirm={setPreferences}
                visibleContentOptions={visibleContentOptions}
                resourceName="data-collection campaigns"
              />
            }
          />
        </Container>
      )}

      {/* Assignment modal — per-vehicle assign via data-processing campaigns/assign */}
      <Modal
        visible={assignModalOpen}
        onDismiss={() => setAssignModalOpen(false)}
        header="Assign data-collection campaign"
        data-testid="dc-campaigns-assign-modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setAssignModalOpen(false)}
                data-testid="dc-campaigns-modal-cancel"
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={assignStatus === "loading"}
                disabled={
                  assignStatus === "loading" ||
                  assignStatus === "success" ||
                  !selectedVehicle ||
                  !canAssignSelected
                }
                onClick={() => void handleAssignConfirm()}
                data-testid="dc-campaigns-modal-confirm"
              >
                {assignStatus === "success" ? "Assigned" : "Assign"}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {selectedCampaign && (
            <Box data-testid="dc-campaigns-modal-campaign-name">
              Campaign: <strong>{selectedCampaign.campaignName}</strong>
              {selectedCampaign.decoderManifestId && (
                <Box variant="p" data-testid="dc-campaigns-modal-decoder-ref">
                  Decoder manifest: {selectedCampaign.decoderManifestId}
                </Box>
              )}
            </Box>
          )}
          <Box>
            <Box variant="awsui-key-label">Vehicle</Box>
            {vehiclesForAssignment && vehiclesForAssignment.length > 0 ? (
              <Select
                selectedOption={
                  selectedVehicle
                    ? { label: selectedVehicle.vehicleId, value: selectedVehicle.vehicleId }
                    : null
                }
                options={vehiclesForAssignment.map((v) => ({
                  label: v.vehicleId,
                  value: v.vehicleId,
                  description: v.modelManifestName
                    ? `Model: ${v.modelManifestName}`
                    : "No model manifest — cannot assign",
                  disabled: !v.modelManifestName,
                }))}
                onChange={({ detail }) => {
                  const found = vehiclesForAssignment.find(
                    (v) => v.vehicleId === detail.selectedOption.value,
                  );
                  setSelectedVehicle(found ?? null);
                }}
                placeholder="Select a vehicle"
                data-testid="dc-campaigns-modal-vehicle-select"
              />
            ) : (
              <Box color="text-status-inactive" data-testid="dc-campaigns-modal-no-vehicles">
                No vehicles available for assignment.
              </Box>
            )}
          </Box>
          {/* Guard feedback: show a stated reason when the vehicle cannot be assigned */}
          {selectedVehicle && !canAssignSelected && (
            <Alert type="warning" data-testid="dc-campaigns-guard-blocked">
              This vehicle has no model manifest binding (
              <code>modelManifestName</code> is absent). Assignment is blocked —
              the campaign&apos;s signal coverage cannot be validated against an
              unbound vehicle.
            </Alert>
          )}
          {assignStatus === "error" && assignError && (
            <Alert type="error" data-testid="dc-campaigns-assign-error">
              {assignError}
            </Alert>
          )}
          {assignStatus === "success" && (
            <Alert type="success" data-testid="dc-campaigns-assign-success">
              Campaign assigned successfully. {assignedCount} vehicle(s) assigned.
            </Alert>
          )}
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};

export default DataCollectionCampaignsView;
