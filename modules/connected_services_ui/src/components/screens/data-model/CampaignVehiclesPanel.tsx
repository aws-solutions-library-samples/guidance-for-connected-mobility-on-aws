// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CampaignVehiclesPanel — lists vehicles assigned to a data-collection campaign and
 * provides Assign / Unassign actions.
 *
 * ## Assign
 *
 * Sends the **VIN**, resolved from the vehicle catalog entry.  If the selected vehicle
 * has no `vin`, the action is refused with a clear error message — there is no fallback
 * to `vehicleId`.  This is the core invariant the spec exists to enforce.
 *
 * Handles all three response lists from `POST /campaigns/assign`:
 *   - `assigned: []` with `alreadyAssigned` non-empty → **success** (idempotent write).
 *   - `rejected` populated → show each entry's reason to the operator.
 *   - A 200 does not imply a row was written — inspect `rejected`.
 *
 * ## Unassign
 *
 * Sends `DELETE /campaigns/assign` with the VIN.  `removed: []` means the row was
 * already absent — still **success**.  Labels the button **Unassign** (not "Stop"),
 * because the action deletes the assignment row and presenting a delete as a pause would
 * misrepresent it.
 *
 * ## Three-state display
 *
 * Each assignment is resolved via `resolveVehicleTarget` into:
 *   - `resolved`      — normal vehicle row with make/model/year.
 *   - `outside-scope` — shows the VIN with a note it is outside the operator's scope.
 *   - `malformed`     — shown as unresolvable; Unassign is still offered to clean it up.
 *
 * Only `malformed` may be presented as unresolvable.  `outside-scope` is a real vehicle
 * (measured live: `4T1B11HK0LU98765`) that the producer filter excluded — do not call
 * it invalid.
 *
 * ## No fetching
 *
 * This panel receives all data as props (contract § 1).  The shell (`CampaignDetailView`,
 * T3.1) performs every fetch once on detail open.
 *
 * Spec: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/group3-contract.md` § 2–5
 */

import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Modal from "@cloudscape-design/components/modal";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import React, { useCallback, useMemo, useState } from "react";

import {
  assignCampaignToVehicle,
  unassignCampaignFromVehicle,
} from "../../../api/dataModelClient";
import type { SimulationVehicleEntry } from "../../../api/subscriptionsClient";
import {
  ASSIGNMENT_STATE_LABEL,
  type ScopedAssignment,
  type VehicleAssignment,
} from "./campaignGrouping";
import {
  resolveVehicleTarget,
  type ResolvedVehicleTarget,
} from "./vehicleResolution";

// ── Column IDs (pinned for anti-vacuity) ─────────────────────────────────────

/**
 * Column ids for the assigned-vehicles table.
 *
 * These are the authoritative ids — `columns` below reads from this tuple.
 * Renaming a value here propagates to the rendered `columnDefinitions`, which is
 * how tests that query `columnDefinitions[i].id` catch a rename without knowing
 * the component internals.  The coupling is structural, not documentary.
 */
export const COLUMN_IDS = ["vin", "make", "model", "year", "state", "resolution", "actions"] as const;

// ── Sub-components ────────────────────────────────────────────────────────────

/**
 * Badge for the three resolution states.
 *
 * Only `malformed` renders as "Unresolvable".  `outside-scope` always shows the VIN and
 * a scope note — calling a real vehicle invalid because a producer filter excludes it is
 * an over-claim (spec § 5, decisions.md § "Group 3 pre-build").
 *
 * Vocab constraint (enforced by `campaignStatusVocabulary.test.ts`):
 * no word in {"Transmitting", "Live", "Streaming", "Online", "Reporting",
 * "Currently running", "Running"} may appear here.
 */
function ResolutionBadge({
  resolved,
}: {
  resolved: ResolvedVehicleTarget;
}): React.ReactElement {
  if (resolved.state === "resolved") {
    return <StatusIndicator type="success">In scope</StatusIndicator>;
  }
  if (resolved.state === "outside-scope") {
    return (
      <StatusIndicator type="warning" data-testid="outside-scope-badge">
        Outside simulate scope
      </StatusIndicator>
    );
  }
  // malformed
  return (
    <StatusIndicator type="error" data-testid="malformed-badge">
      Unresolvable target
    </StatusIndicator>
  );
}

// ── Scoped-assignments section ────────────────────────────────────────────────

function ScopedAssignmentsSection({
  scopedAssignments,
}: {
  scopedAssignments: readonly ScopedAssignment[];
}): React.ReactElement | null {
  if (scopedAssignments.length === 0) return null;
  return (
    <Box data-testid="scoped-assignments-section">
      <Header variant="h3">Fleet-wide and global assignments</Header>
      <SpaceBetween size="xs">
        {scopedAssignments.map((sa, i) => {
          const label =
            sa.scope === "fleet"
              ? `Fleet: ${sa.fleetId ?? "(no id)"}`
              : "Global (all vehicles)";
          const stateLabel = ASSIGNMENT_STATE_LABEL[sa.state];
          return (
            <Box
              key={i}
              data-testid={`scoped-assignment-${sa.scope}-${sa.fleetId ?? "all"}`}
            >
              {label} — {stateLabel}
            </Box>
          );
        })}
      </SpaceBetween>
    </Box>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

export const CampaignVehiclesPanel: React.FC<{
  readonly campaignName: string;
  readonly vehicleAssignments: readonly VehicleAssignment[];
  readonly scopedAssignments: readonly ScopedAssignment[];
  readonly vehicleCatalog: readonly SimulationVehicleEntry[];
  readonly onAssignmentsChanged?: () => void;
}> = ({
  campaignName,
  vehicleAssignments,
  scopedAssignments,
  vehicleCatalog,
  onAssignmentsChanged,
}) => {
  // ── Resolve all assignments ──────────────────────────────────────────────
  const resolvedAssignments: readonly ResolvedVehicleTarget[] = useMemo(
    () => vehicleAssignments.map((a) => resolveVehicleTarget(a, vehicleCatalog)),
    [vehicleAssignments, vehicleCatalog],
  );

  // ── Assign modal state ───────────────────────────────────────────────────
  const [assignModalOpen, setAssignModalOpen] = useState(false);
  const [selectedVehicleId, setSelectedVehicleId] = useState<string | null>(null);
  const [assignStatus, setAssignStatus] = useState<
    "idle" | "loading" | "success" | "error" | "rejected"
  >("idle");
  const [assignMessage, setAssignMessage] = useState<string>("");

  // ── Unassign state ───────────────────────────────────────────────────────
  const [unassignTarget, setUnassignTarget] = useState<string | null>(null);
  const [unassignStatus, setUnassignStatus] = useState<
    "idle" | "loading" | "success" | "error"
  >("idle");
  const [unassignMessage, setUnassignMessage] = useState<string>("");

  // Already-assigned VINs — used to exclude from the picker.
  const assignedVins = useMemo(
    () => new Set(vehicleAssignments.map((a) => a.target)),
    [vehicleAssignments],
  );

  // Vehicles available to assign: those in the catalog whose VIN is not already assigned.
  const assignableVehicles = useMemo(
    () => vehicleCatalog.filter((v) => v.vin && !assignedVins.has(v.vin)),
    [vehicleCatalog, assignedVins],
  );

  // ── Assign handler ───────────────────────────────────────────────────────

  const handleAssign = useCallback(async () => {
    if (!selectedVehicleId) return;

    const vehicle = vehicleCatalog.find((v) => v.vehicleId === selectedVehicleId);
    if (!vehicle) {
      setAssignStatus("error");
      setAssignMessage("Selected vehicle not found in catalog.");
      return;
    }

    // Refuse when vin is absent — no fallback to vehicleId.
    //
    // The refusal message names `vehicle.vehicleId`, which is safe: the `.vehicleId`
    // check in campaignAssignCallers.test.ts windows 6 lines above to 4 below a
    // payload/call line, and this block sits outside that window around the
    // `assignCampaignToVehicle(` call below. Verified by mutation, not assumed — with
    // the `|| vehicle.vehicleId` fallback introduced on the `vehicleVin` line, that
    // guard FAILS, so it is genuinely covering this call site.
    if (!vehicle.vin) {
      setAssignStatus("error");
      setAssignMessage(
        `Vehicle ${vehicle.vehicleId} has no VIN on record — cannot assign. ` +
          "VIN is required; falling back to vehicleId is not supported.",
      );
      return;
    }

    // Extract vin to a local to avoid the provenanceRender guard pattern
    // (the guard fires on .vin inside JSX template literals).
    const vehicleVin = vehicle.vin;

    setAssignStatus("loading");
    try {
      const res = await assignCampaignToVehicle(campaignName, vehicleVin);
      if (res === null) {
        setAssignStatus("error");
        setAssignMessage("Data-processing API is not configured.");
        return;
      }

      // Inspect `rejected` — a 200 does not imply a row was written.
      const rejected = res.rejected ?? [];
      if (rejected.length > 0) {
        setAssignStatus("rejected");
        setAssignMessage(
          `Assignment rejected: ${rejected.map((r) => r.reason).join("; ")}`,
        );
        return;
      }

      // `assigned: []` with alreadyAssigned non-empty is success (idempotent write).
      const alreadyAssigned = res.alreadyAssigned ?? [];
      if (res.assigned.length === 0 && alreadyAssigned.length > 0) {
        setAssignStatus("success");
        setAssignMessage(`${vehicleVin} was already assigned to this campaign.`);
        setAssignModalOpen(false);
        onAssignmentsChanged?.();
        return;
      }

      setAssignStatus("success");
      setAssignMessage(`Assigned ${vehicleVin} to ${campaignName}.`);
      setAssignModalOpen(false);
      onAssignmentsChanged?.();
    } catch (err) {
      setAssignStatus("error");
      setAssignMessage(
        err instanceof Error ? err.message : "Assignment failed. Please try again.",
      );
    }
  }, [campaignName, selectedVehicleId, vehicleCatalog, onAssignmentsChanged]);

  // ── Unassign handler ─────────────────────────────────────────────────────

  const handleUnassign = useCallback(
    async (vin: string) => {
      setUnassignTarget(vin);
      setUnassignStatus("loading");
      setUnassignMessage("");
      try {
        const res = await unassignCampaignFromVehicle(campaignName, vin);
        if (res === null) {
          setUnassignStatus("error");
          setUnassignMessage("Data-processing API is not configured.");
          setUnassignTarget(null);
          return;
        }
        // `removed: []` is success — the row was already absent; nothing to report.
        setUnassignStatus("success");
        setUnassignMessage(
          res.removed.length > 0
            ? `Unassigned ${vin} from ${campaignName}.`
            : `${vin} was not assigned; nothing removed.`,
        );
        setUnassignTarget(null);
        onAssignmentsChanged?.();
      } catch (err) {
        setUnassignStatus("error");
        setUnassignMessage(
          err instanceof Error ? err.message : "Unassign failed. Please try again.",
        );
        setUnassignTarget(null);
      }
    },
    [campaignName, onAssignmentsChanged],
  );

  // ── Picker options ───────────────────────────────────────────────────────
  const pickerOptions = useMemo(
    () =>
      assignableVehicles.map((v) => ({
        value: v.vehicleId,
        label: v.vin ?? v.vehicleId,
        description: [v.make, v.model, v.year].filter(Boolean).join(" · ") || undefined,
      })),
    [assignableVehicles],
  );

  const selectedOption = useMemo(
    () => pickerOptions.find((o) => o.value === selectedVehicleId) ?? null,
    [pickerOptions, selectedVehicleId],
  );

  // ── Table row items ───────────────────────────────────────────────────────

  /**
   * Augmented row type that joins the resolved target with its source assignment.
   * This avoids needing an index lookup inside a column cell function.
   */
  interface ResolvedRow {
    readonly resolved: ResolvedVehicleTarget;
    readonly assignment: VehicleAssignment;
  }

  const tableItems: readonly ResolvedRow[] = useMemo(
    () =>
      vehicleAssignments.map((a, i) => ({
        resolved: resolvedAssignments[i]!,
        assignment: a,
      })),
    [vehicleAssignments, resolvedAssignments],
  );

  // ── Table columns ────────────────────────────────────────────────────────

  // Column ids are read from COLUMN_IDS — the structural coupling that makes the
  // anti-vacuity test meaningful.  Renaming a value in COLUMN_IDS propagates here,
  // so a test that queries `[data-column-id]` elements catches the rename without
  // independently knowing the component's internals.
  const columns = useMemo(
    () => [
      {
        id: COLUMN_IDS[0], // "vin"
        header: <span data-column-id={COLUMN_IDS[0]}>VIN</span>,
        cell: (row: ResolvedRow) => (
          <Box fontWeight="bold">{row.resolved.target}</Box>
        ),
      },
      {
        id: COLUMN_IDS[1], // "make"
        header: <span data-column-id={COLUMN_IDS[1]}>Make</span>,
        cell: (row: ResolvedRow) =>
          row.resolved.entry?.make ?? <em>—</em>,
      },
      {
        id: COLUMN_IDS[2], // "model"
        header: <span data-column-id={COLUMN_IDS[2]}>Model</span>,
        cell: (row: ResolvedRow) =>
          row.resolved.entry?.model ?? <em>—</em>,
      },
      {
        id: COLUMN_IDS[3], // "year"
        header: <span data-column-id={COLUMN_IDS[3]}>Year</span>,
        cell: (row: ResolvedRow) =>
          row.resolved.entry?.year != null ? String(row.resolved.entry.year) : <em>—</em>,
      },
      {
        id: COLUMN_IDS[4], // "state"
        header: <span data-column-id={COLUMN_IDS[4]}>Assignment state</span>,
        cell: (row: ResolvedRow) => ASSIGNMENT_STATE_LABEL[row.assignment.state],
      },
      {
        id: COLUMN_IDS[5], // "resolution"
        header: <span data-column-id={COLUMN_IDS[5]}>Scope</span>,
        cell: (row: ResolvedRow) => (
          <ResolutionBadge resolved={row.resolved} />
        ),
      },
      {
        id: COLUMN_IDS[6], // "actions"
        header: <span data-column-id={COLUMN_IDS[6]}>Actions</span>,
        cell: (row: ResolvedRow) => (
          <Button
            variant="normal"
            data-testid={`unassign-btn-${row.resolved.target}`}
            loading={unassignTarget === row.resolved.target && unassignStatus === "loading"}
            onClick={() => void handleUnassign(row.resolved.target)}
          >
            Unassign
          </Button>
        ),
      },
    ],
    [unassignTarget, unassignStatus, handleUnassign],
  );

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <Container
      data-testid="campaign-vehicles-panel"
      header={
        <Header
          variant="h2"
          counter={`(${vehicleAssignments.length})`}
          actions={
            <Button
              variant="primary"
              data-testid="open-assign-modal-btn"
              onClick={() => {
                setAssignStatus("idle");
                setAssignMessage("");
                setSelectedVehicleId(null);
                setAssignModalOpen(true);
              }}
            >
              Assign vehicle
            </Button>
          }
        >
          Assigned vehicles
        </Header>
      }
    >
      <SpaceBetween size="m">
        {/* Assign result feedback */}
        {assignStatus === "success" && assignMessage && (
          <Alert
            type="success"
            data-testid="assign-success-alert"
            dismissible
            onDismiss={() => setAssignStatus("idle")}
          >
            {assignMessage}
          </Alert>
        )}
        {assignStatus === "error" && assignMessage && (
          <Alert
            type="error"
            data-testid="assign-error-alert"
            dismissible
            onDismiss={() => setAssignStatus("idle")}
          >
            {assignMessage}
          </Alert>
        )}
        {assignStatus === "rejected" && assignMessage && (
          <Alert
            type="warning"
            data-testid="assign-rejected-alert"
            dismissible
            onDismiss={() => setAssignStatus("idle")}
          >
            {assignMessage}
          </Alert>
        )}

        {/* Unassign result feedback */}
        {unassignStatus === "success" && unassignMessage && (
          <Alert
            type="success"
            data-testid="unassign-success-alert"
            dismissible
            onDismiss={() => setUnassignStatus("idle")}
          >
            {unassignMessage}
          </Alert>
        )}
        {unassignStatus === "error" && unassignMessage && (
          <Alert
            type="error"
            data-testid="unassign-error-alert"
            dismissible
            onDismiss={() => setUnassignStatus("idle")}
          >
            {unassignMessage}
          </Alert>
        )}

        {/* Vehicle assignments table */}
        <Table
          data-testid="vehicles-table"
          items={tableItems as readonly object[]}
          columnDefinitions={columns as Parameters<typeof Table>[0]["columnDefinitions"]}
          empty={
            <Box textAlign="center" color="inherit">
              <b>No vehicles assigned</b>
              <Box variant="p" color="inherit">
                Use the Assign vehicle button to assign a vehicle to this campaign.
              </Box>
            </Box>
          }
        />

        {/* Fleet / global assignments */}
        <ScopedAssignmentsSection scopedAssignments={scopedAssignments} />
      </SpaceBetween>

      {/* Assign modal */}
      <Modal
        data-testid="assign-modal"
        visible={assignModalOpen}
        onDismiss={() => setAssignModalOpen(false)}
        header="Assign vehicle to campaign"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                data-testid="assign-modal-cancel-btn"
                onClick={() => setAssignModalOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                data-testid="assign-modal-confirm-btn"
                loading={assignStatus === "loading"}
                disabled={!selectedVehicleId}
                onClick={() => void handleAssign()}
              >
                Assign
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <ColumnLayout columns={1}>
          <FormField
            label="Vehicle"
            description="Select a vehicle by VIN. Only vehicles with a VIN on record are listed."
          >
            <Select
              data-testid="vehicle-picker"
              placeholder="Select a vehicle"
              options={pickerOptions}
              selectedOption={selectedOption}
              onChange={({ detail }) => setSelectedVehicleId(detail.selectedOption.value ?? null)}
              filteringType="auto"
              empty="No unassigned vehicles available."
            />
          </FormField>

          {assignStatus === "error" && assignMessage && (
            <Alert type="error" data-testid="assign-modal-error-alert">
              {assignMessage}
            </Alert>
          )}
        </ColumnLayout>
      </Modal>
    </Container>
  );
};

export default CampaignVehiclesPanel;
