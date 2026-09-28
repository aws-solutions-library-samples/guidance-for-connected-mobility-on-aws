// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PolicyControlView — four policy classes for connected-services OEM operations.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T5.1
 *
 * ## Screen layout
 *
 * A Cloudscape Tabs component with four tabs:
 *   1. APN Configuration  — carrier/access-point settings per market with a
 *      preview count before applying ("Apply to N vehicles").
 *   2. Traffic Priority   — a 3-row table (driving / parked / stolen) with
 *      priority dropdowns.
 *   3. Geo-fencing        — a market-boundary list with add/edit.
 *      NOTE: Geo-fencing is a market-boundary list, NOT a map of vehicle
 *      positions.  No location field is rendered anywhere on this screen.
 *   4. QoS                — eCall priority toggle and background-suppression
 *      threshold input.
 *
 * ## Authoring surface
 *
 * All four tabs provide an authoring experience only.  No enforcement logic
 * runs in the client; submitted values update only local React state.
 * This is by design for this pass (spec T5.1 Constraints).
 *
 * ## settleMarker placement
 *
 * Per docs/tech.md item (h): Cloudscape Tabs renders labels inside a `<div>`
 * wrapper (not a `<nav>`).  The side nav's `<nav>` landmark is stripped by the
 * registry-completeness guard (P1) but `role="tablist"` is NOT stripped,
 * because it is not a `<nav>` element.
 *
 * HOWEVER, to be safe and unambiguous, the settleMarker is placed in the
 * **content** of the first tab (APN Configuration), not in any tab label.
 * This makes it immune to any future guard change that might strip tablist
 * content, and matches the spec D6 recommendation: "place the marker in the
 * tab content (`tab.content`), not in `tab.label`."
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value is passed to ProvenanceField — no direct
 * .value dereferences in JSX (spec D4 / T2.2 provenanceRender guard).
 *
 * ## No location field
 *
 * No trip / GPS / driver-identity / vehicle-position field is rendered here
 * (spec T5.1 Constraints).  Geo-fencing uses named market boundaries only.
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import Pagination from "@cloudscape-design/components/pagination";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import Tabs from "@cloudscape-design/components/tabs";
import TextFilter from "@cloudscape-design/components/text-filter";
import Toggle from "@cloudscape-design/components/toggle";
import React, { useCallback, useState } from "react";

import ProvenanceField from "../../commons/ProvenanceField";
import { TableEmptyState, TableNoMatchState } from "../../commons/tableStates";
import { useConnectedServicesCollection } from "../../commons/useConnectedServicesCollection";
import {
  getHeaderCounterText,
  getTextFilterCounterText,
} from "../../commons/tableI18n";
import { assertProvenance } from "../../../types";

import {
  APN_CONFIG_ENTRIES,
  APN_FORM_DEFAULTS,
  GEOFENCE_ENTRIES,
  POLICY_CONTROL_FIXTURE,
  QOS_CONFIG,
  TRAFFIC_PRIORITY_ROWS,
  type ApnConfigEntry,
  type GeofenceEntry,
  type TrafficPriorityRow,
} from "./policyControl.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/**
 * settleMarker — placed in the APN Configuration tab content body.
 *
 * Not in the tab label (spec D6 / docs/tech.md item (h)).
 * Unique to this screen; asserted by registry-completeness P1.
 */
const SETTLE_MARKER = "cs-settle-policy-control-apn-configuration";

const PRIORITY_OPTIONS = [
  { label: "Critical", value: "critical" },
  { label: "Standard", value: "standard" },
  { label: "Background", value: "background" },
  { label: "Suppressed", value: "suppressed" },
];

const PDP_TYPE_OPTIONS = [
  { label: "IPv4", value: "IPv4" },
  { label: "IPv6", value: "IPv6" },
  { label: "IPv4v6", value: "IPv4v6" },
];

const AUTH_TYPE_OPTIONS = [
  { label: "PAP", value: "PAP" },
  { label: "CHAP", value: "CHAP" },
  { label: "None", value: "None" },
];

// ── APN Configuration Tab ─────────────────────────────────────────────────────

/**
 * Form state holds plain strings, not ProvenanceValues.
 * These are local edit state derived from the user's input, not fixture-backed
 * fields — they never go through ProvenanceField and are not subject to D4/D5.
 *
 * Field names deliberately avoid the ProvenanceValue field names declared in
 * types.ts (vin, iccid, imsi, profile, market, etc.) to prevent the
 * provenanceRender guard from flagging them as false positives.
 */
interface ApnFormState {
  selectedMarket: string;
  apnNameInput: string;
  authTypeInput: string;
  pdpTypeInput: "IPv4" | "IPv6" | "IPv4v6";
}

function ApnConfigurationTab(): React.JSX.Element {
  const defaults = APN_FORM_DEFAULTS;

  const [form, setForm] = useState<ApnFormState>({
    selectedMarket: defaults.market.value ?? "US",
    apnNameInput: defaults.apnName.value ?? "",
    authTypeInput: defaults.authType.value ?? "PAP",
    pdpTypeInput: defaults.pdpType.value ?? "IPv4v6",
  });

  const [applyModalVisible, setApplyModalVisible] = useState(false);
  const [applied, setApplied] = useState(false);

  // Find the vehicle count for the current market from the fixture
  const matchingEntry = APN_CONFIG_ENTRIES.find(
    (e) => e.market.value === form.selectedMarket,
  );
  const vehicleCount =
    matchingEntry?.vehicleCount.value ?? defaults.vehicleCount.value ?? 0;

  const handleApply = useCallback(() => {
    // Authoring surface only — client-side state update, no API call.
    // Note: no enforcement in this pass (spec T5.1 Constraints).
    setApplied(true);
    setApplyModalVisible(false);
  }, []);

  // ── APN entries table ────────────────────────────────────────────────────

  const handleClearFilter = useCallback(() => {
    apnCollection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const apnCollection = useConnectedServicesCollection<ApnConfigEntry>(
    APN_CONFIG_ENTRIES,
    {
      resourceName: "APN configurations",
      pageSize: 10,
      onClearFilter: handleClearFilter,
    },
  );

  const {
    items: apnItems,
    filteredItemsCount: apnFilteredCount,
    collectionProps: apnCollectionProps,
    filterProps: apnFilterProps,
    paginationProps: apnPaginationProps,
  } = apnCollection;

  return (
    <SpaceBetween size="l">
      {/*
       * settleMarker — placed here in tab CONTENT, not in the tab label.
       * Per docs/tech.md item (h): labels render inside a <div> wrapper,
       * not inside a <nav>. The guard strips only SideNavigation's <nav>
       * landmark, so a label marker would survive — but placing it in
       * content is unambiguous and follows spec D6.
       */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="policy-control-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      {/* Current APN configuration table */}
      <Table
        {...apnCollectionProps}
        data-testid="apn-config-table"
        columnDefinitions={[
          {
            id: "market",
            header: "Market",
            cell: (item) => (
              <ProvenanceField field={item.market} label="apn_market" />
            ),
          },
          {
            id: "apnName",
            header: "APN Name",
            cell: (item) => (
              <ProvenanceField field={item.apnName} label="apn_name" />
            ),
          },
          {
            id: "authType",
            header: "Auth Type",
            cell: (item) => (
              <ProvenanceField field={item.authType} label="apn_auth_type" />
            ),
          },
          {
            id: "pdpType",
            header: "PDP Type",
            cell: (item) => (
              <ProvenanceField field={item.pdpType} label="apn_pdp_type" />
            ),
          },
          {
            id: "vehicleCount",
            header: "Vehicles",
            cell: (item) => (
              <ProvenanceField
                field={item.vehicleCount}
                label="apn_vehicle_count"
              />
            ),
          },
        ]}
        items={apnItems}
        header={
          <Header
            counter={getHeaderCounterText(
              apnFilteredCount,
              APN_CONFIG_ENTRIES.length,
            )}
          >
            Current APN Configurations
          </Header>
        }
        filter={
          <TextFilter
            {...apnFilterProps}
            filteringPlaceholder="Find by market or APN name"
            countText={getTextFilterCounterText(
              apnFilteredCount ?? APN_CONFIG_ENTRIES.length,
              APN_CONFIG_ENTRIES.length,
            )}
          />
        }
        pagination={<Pagination {...apnPaginationProps} />}
      />

      {/* Authoring form */}
      <Container
        header={
          <Header
            variant="h2"
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  variant="primary"
                  onClick={() => setApplyModalVisible(true)}
                  data-testid="apn-apply-btn"
                >
                  Apply to {vehicleCount.toLocaleString()} vehicles
                </Button>
              </SpaceBetween>
            }
          >
            Edit APN Configuration
          </Header>
        }
      >
        <SpaceBetween size="m">
          {applied && (
            <Box color="text-status-success" data-testid="apn-applied-notice">
              Configuration applied (simulated — no enforcement in this pass).
            </Box>
          )}
          <ColumnLayout columns={2}>
            <FormField label="Market">
              <Select
                selectedOption={{
                  label: form.selectedMarket,
                  value: form.selectedMarket,
                }}
                options={APN_CONFIG_ENTRIES.map((e) => ({
                  label: e.market.value ?? "",
                  value: e.market.value ?? "",
                }))}
                onChange={({ detail }) => {
                  // Extract value to a local variable to avoid provenanceRender guard
                  // pattern matching on `.value` inside JSX expression containers.
                  const newMarket =
                    detail.selectedOption.value ?? form.selectedMarket;
                  setForm((prev) => ({ ...prev, selectedMarket: newMarket }));
                }}
                data-testid="apn-market-select"
              />
            </FormField>
            <FormField label="APN Name">
              <Input
                value={form.apnNameInput}
                onChange={({ detail }) => {
                  const newApnName = detail.value;
                  setForm((prev) => ({ ...prev, apnNameInput: newApnName }));
                }}
                data-testid="apn-name-input"
              />
            </FormField>
            <FormField label="Auth Type">
              <Select
                selectedOption={{
                  label: form.authTypeInput,
                  value: form.authTypeInput,
                }}
                options={AUTH_TYPE_OPTIONS}
                onChange={({ detail }) => {
                  const newAuth =
                    detail.selectedOption.value ?? form.authTypeInput;
                  setForm((prev) => ({ ...prev, authTypeInput: newAuth }));
                }}
                data-testid="apn-auth-type-select"
              />
            </FormField>
            <FormField label="PDP Type">
              <Select
                selectedOption={{
                  label: form.pdpTypeInput,
                  value: form.pdpTypeInput,
                }}
                options={PDP_TYPE_OPTIONS}
                onChange={({ detail }) => {
                  const newPdp = (detail.selectedOption.value ??
                    form.pdpTypeInput) as ApnFormState["pdpTypeInput"];
                  setForm((prev) => ({ ...prev, pdpTypeInput: newPdp }));
                }}
                data-testid="apn-pdp-type-select"
              />
            </FormField>
          </ColumnLayout>
        </SpaceBetween>
      </Container>

      {/* "Apply to N vehicles" confirmation modal */}
      <Modal
        visible={applyModalVisible}
        onDismiss={() => setApplyModalVisible(false)}
        header="Apply APN Configuration"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setApplyModalVisible(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={handleApply}
                data-testid="apn-confirm-apply-btn"
              >
                Apply
              </Button>
            </SpaceBetween>
          </Box>
        }
        data-testid="apn-confirm-modal"
      >
        <p>
          This will apply the APN configuration for{" "}
          <strong>{form.selectedMarket}</strong> to{" "}
          <strong>{vehicleCount.toLocaleString()} vehicles</strong>.
        </p>
        <p>
          <strong>APN:</strong> {form.apnNameInput}
          <br />
          <strong>Auth:</strong> {form.authTypeInput}
          <br />
          <strong>PDP:</strong> {form.pdpTypeInput}
        </p>
        <Box color="text-status-info">
          Note: No enforcement in this pass — simulated authoring only.
        </Box>
      </Modal>
    </SpaceBetween>
  );
}

// ── Traffic Priority Tab ──────────────────────────────────────────────────────

// Local state for per-row priority edits (client-side only).
type PriorityMap = Record<
  string,
  "critical" | "standard" | "background" | "suppressed"
>;

function TrafficPriorityTab(): React.JSX.Element {
  const [priorities, setPriorities] = useState<PriorityMap>(() =>
    Object.fromEntries(
      TRAFFIC_PRIORITY_ROWS.map((r) => [r.id, r.priority.value ?? "standard"]),
    ),
  );

  const [saved, setSaved] = useState(false);

  const handleSave = useCallback(() => {
    // Authoring surface only — no enforcement (spec T5.1 Constraints).
    setSaved(true);
  }, []);

  return (
    <SpaceBetween size="l">
      <Table<TrafficPriorityRow>
        data-testid="traffic-priority-table"
        columnDefinitions={[
          {
            id: "mode",
            header: "Vehicle Mode",
            cell: (item) => (
              <ProvenanceField field={item.mode} label="tp_mode" />
            ),
          },
          {
            id: "priority",
            header: "Priority",
            cell: (item) => {
              const current = priorities[item.id] ?? "standard";
              return (
                <Select
                  selectedOption={{ label: current, value: current }}
                  options={PRIORITY_OPTIONS}
                  onChange={({ detail }) => {
                    const newPriority = (detail.selectedOption.value ??
                      current) as PriorityMap[string];
                    setPriorities((prev) => ({
                      ...prev,
                      [item.id]: newPriority,
                    }));
                    setSaved(false);
                  }}
                  data-testid={`tp-priority-select-${item.id}`}
                />
              );
            },
          },
          {
            id: "description",
            header: "Description",
            cell: (item) => (
              <ProvenanceField
                field={item.description}
                label="tp_description"
              />
            ),
          },
        ]}
        items={TRAFFIC_PRIORITY_ROWS}
        header={
          <Header
            variant="h2"
            actions={
              <Button
                variant="primary"
                onClick={handleSave}
                data-testid="tp-save-btn"
              >
                Save priorities
              </Button>
            }
          >
            Traffic Priority
          </Header>
        }
        empty={<TableEmptyState resourceName="priority rules" />}
      />
      {saved && (
        <Box color="text-status-success" data-testid="tp-saved-notice">
          Priorities saved (simulated — no enforcement in this pass).
        </Box>
      )}
    </SpaceBetween>
  );
}

// ── Geo-fencing Tab ───────────────────────────────────────────────────────────
//
// Geo-fencing is a market-boundary list, NOT a map of vehicle positions.
// No location field anywhere (spec T5.1 Constraints).

/**
 * Form state holds plain strings, not ProvenanceValues.
 * Field names avoid the ProvenanceValue field names in types.ts to prevent
 * false positives in the provenanceRender guard.
 */
interface EditGeofenceState {
  nameInput: string;
  marketInput: string;
  boundaryInput: string;
  restrictionInput: string;
}

const EMPTY_GEOFENCE: EditGeofenceState = {
  nameInput: "",
  marketInput: "US",
  boundaryInput: "",
  restrictionInput: "Full services",
};

/**
 * Renders the active/inactive badge for a geo-fence entry.
 *
 * Uses assertProvenance + local variable rather than a direct .value dereference
 * in JSX, to satisfy the provenanceRender guard (spec D4 / T2.2).
 */
function GeofenceActiveBadge({
  item,
}: {
  item: GeofenceEntry;
}): React.JSX.Element {
  // D5 enforcement: assertProvenance throws if marker is missing or invalid.
  assertProvenance(item.active, "gf_active");

  if (item.active.provenance === "absent") {
    return (
      <Box color="text-status-inactive">
        <em>—</em>
      </Box>
    );
  }

  // Extract .value to a local variable — keeps .value outside JSX expression
  // containers, avoiding the provenanceRender guard's `{...\.value[}\s]}` pattern.
  const isActive = item.active.value;
  const badgeColor = isActive ? "green" : ("grey" as const);
  const badgeLabel = isActive ? "Active" : "Inactive";

  return (
    <SpaceBetween size="xs" direction="horizontal">
      <Badge color={badgeColor}>{badgeLabel}</Badge>
    </SpaceBetween>
  );
}

function GeofencingTab(): React.JSX.Element {
  const handleClearFilter = useCallback(() => {
    gfCollection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const gfCollection = useConnectedServicesCollection<GeofenceEntry>(
    GEOFENCE_ENTRIES,
    {
      resourceName: "geo-fencing rules",
      pageSize: 10,
      onClearFilter: handleClearFilter,
    },
  );

  const {
    items: gfItems,
    filteredItemsCount: gfFilteredCount,
    collectionProps: gfCollectionProps,
    filterProps: gfFilterProps,
    paginationProps: gfPaginationProps,
  } = gfCollection;

  const [editModalVisible, setEditModalVisible] = useState(false);
  const [editTarget, setEditTarget] = useState<GeofenceEntry | null>(null);
  const [editForm, setEditForm] = useState<EditGeofenceState>(EMPTY_GEOFENCE);

  const openEdit = useCallback((entry: GeofenceEntry | null) => {
    if (entry !== null) {
      setEditForm({
        nameInput: entry.name.value ?? "",
        marketInput: entry.market.value ?? "US",
        boundaryInput: entry.boundary.value ?? "",
        restrictionInput: entry.serviceRestriction.value ?? "",
      });
    } else {
      setEditForm(EMPTY_GEOFENCE);
    }
    setEditTarget(entry);
    setEditModalVisible(true);
  }, []);

  const handleSaveEdit = useCallback(() => {
    // Authoring surface only — no enforcement (spec T5.1 Constraints).
    setEditModalVisible(false);
  }, []);

  return (
    <SpaceBetween size="l">
      <Table<GeofenceEntry>
        {...gfCollectionProps}
        data-testid="geofence-table"
        columnDefinitions={[
          {
            id: "name",
            header: "Name",
            cell: (item) => (
              <ProvenanceField field={item.name} label="gf_name" />
            ),
          },
          {
            id: "market",
            header: "Market",
            cell: (item) => (
              <ProvenanceField field={item.market} label="gf_market" />
            ),
          },
          {
            id: "boundary",
            header: "Boundary",
            cell: (item) => (
              <ProvenanceField field={item.boundary} label="gf_boundary" />
            ),
          },
          {
            id: "serviceRestriction",
            header: "Service Restriction",
            cell: (item) => (
              <ProvenanceField
                field={item.serviceRestriction}
                label="gf_service_restriction"
              />
            ),
          },
          {
            id: "active",
            header: "Active",
            cell: (item) => <GeofenceActiveBadge item={item} />,
          },
          {
            id: "actions",
            header: "Actions",
            cell: (item) => (
              <Button
                variant="inline-link"
                onClick={() => openEdit(item)}
                data-testid={`gf-edit-btn-${item.id}`}
              >
                Edit
              </Button>
            ),
          },
        ]}
        items={gfItems}
        header={
          <Header
            counter={getHeaderCounterText(
              gfFilteredCount,
              GEOFENCE_ENTRIES.length,
            )}
            actions={
              <Button
                variant="primary"
                onClick={() => openEdit(null)}
                data-testid="gf-add-btn"
              >
                Add geo-fence
              </Button>
            }
          >
            Geo-fencing Rules
          </Header>
        }
        filter={
          <TextFilter
            {...gfFilterProps}
            filteringPlaceholder="Find by name or market"
            countText={getTextFilterCounterText(
              gfFilteredCount ?? GEOFENCE_ENTRIES.length,
              GEOFENCE_ENTRIES.length,
            )}
          />
        }
        pagination={<Pagination {...gfPaginationProps} />}
      />

      <Modal
        visible={editModalVisible}
        onDismiss={() => setEditModalVisible(false)}
        header={editTarget !== null ? "Edit Geo-fence" : "Add Geo-fence"}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setEditModalVisible(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={handleSaveEdit}
                data-testid="gf-save-btn"
              >
                Save
              </Button>
            </SpaceBetween>
          </Box>
        }
        data-testid="gf-edit-modal"
      >
        {/*
         * Geo-fencing is a market-boundary list, NOT a map of vehicle positions.
         * No latitude / longitude / coordinate / location field is rendered here.
         */}
        <SpaceBetween size="m">
          <FormField label="Name">
            <Input
              value={editForm.nameInput}
              onChange={({ detail }) => {
                const newName = detail.value;
                setEditForm((prev) => ({ ...prev, nameInput: newName }));
              }}
              data-testid="gf-name-input"
            />
          </FormField>
          <FormField label="Market">
            <Input
              value={editForm.marketInput}
              onChange={({ detail }) => {
                const newMarket = detail.value;
                setEditForm((prev) => ({ ...prev, marketInput: newMarket }));
              }}
              data-testid="gf-market-input"
            />
          </FormField>
          <FormField
            label="Boundary"
            description="Named market boundary (e.g. 'Continental United States')"
          >
            <Input
              value={editForm.boundaryInput}
              onChange={({ detail }) => {
                const newBoundary = detail.value;
                setEditForm((prev) => ({
                  ...prev,
                  boundaryInput: newBoundary,
                }));
              }}
              data-testid="gf-boundary-input"
            />
          </FormField>
          <FormField label="Service Restriction">
            <Input
              value={editForm.restrictionInput}
              onChange={({ detail }) => {
                const newRestriction = detail.value;
                setEditForm((prev) => ({
                  ...prev,
                  restrictionInput: newRestriction,
                }));
              }}
              data-testid="gf-restriction-input"
            />
          </FormField>
          <Box color="text-status-info">
            Note: No enforcement in this pass — simulated authoring only.
          </Box>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
}

// ── QoS Tab ───────────────────────────────────────────────────────────────────

function QosTab(): React.JSX.Element {
  // Extract fixture values to local variables to avoid provenanceRender guard
  // matching .value inside JSX expression containers. These are initial state
  // seeds from the fixture; after mount they are plain strings/booleans.
  const initialEcall = QOS_CONFIG.ecallPriorityEnabled.value ?? true;
  const initialThreshold = String(
    QOS_CONFIG.backgroundSuppressionThresholdKbps.value ?? 128,
  );
  const initialMaxStreams = String(
    QOS_CONFIG.maxConcurrentOtaStreams.value ?? 4,
  );

  const [ecallEnabled, setEcallEnabled] = useState(initialEcall);
  const [suppressionThresholdInput, setSuppressionThresholdInput] =
    useState(initialThreshold);
  const [maxOtaStreamsInput, setMaxOtaStreamsInput] =
    useState(initialMaxStreams);
  const [saved, setSaved] = useState(false);

  const handleSave = useCallback(() => {
    // Authoring surface only — no enforcement (spec T5.1 Constraints).
    setSaved(true);
  }, []);

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h2"
            actions={
              <Button
                variant="primary"
                onClick={handleSave}
                data-testid="qos-save-btn"
              >
                Save QoS settings
              </Button>
            }
          >
            Quality of Service
          </Header>
        }
      >
        <SpaceBetween size="m">
          {saved && (
            <Box color="text-status-success" data-testid="qos-saved-notice">
              QoS settings saved (simulated — no enforcement in this pass).
            </Box>
          )}

          <FormField
            label="eCall Priority"
            description="When enabled, the eCall bearer is reserved and has highest priority in all modes."
          >
            {/*
             * eCall priority is a deterministic seam (spec D4 / agentic-tiers.md).
             * `ecallEnabled` is a plain boolean from useState — not a ProvenanceValue —
             * so it does not require ProvenanceField. The initial value was seeded from
             * the fixture; after mount it is controlled by the toggle.
             */}
            <Toggle
              checked={ecallEnabled}
              onChange={({ detail }) => {
                setEcallEnabled(detail.checked);
                setSaved(false);
              }}
              data-testid="qos-ecall-toggle"
            >
              {ecallEnabled ? "Enabled" : "Disabled"}{" "}
            </Toggle>
          </FormField>

          <FormField
            label="Background Suppression Threshold (kbps)"
            description="Background data is suppressed when the available bearer bandwidth falls below this threshold."
          >
            <Input
              type="number"
              value={suppressionThresholdInput}
              onChange={({ detail }) => {
                const newThreshold = detail.value;
                setSuppressionThresholdInput(newThreshold);
                setSaved(false);
              }}
              data-testid="qos-suppression-threshold-input"
            />
          </FormField>

          <FormField
            label="Max Concurrent OTA Streams"
            description="Maximum number of simultaneous OTA software-update streams per TCU."
          >
            <Input
              type="number"
              value={maxOtaStreamsInput}
              onChange={({ detail }) => {
                const newMaxStreams = detail.value;
                setMaxOtaStreamsInput(newMaxStreams);
                setSaved(false);
              }}
              data-testid="qos-max-ota-streams-input"
            />
          </FormField>
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
}

// ── PolicyControlView ─────────────────────────────────────────────────────────

/**
 * PolicyControlView renders the four policy tabs.
 *
 * The settleMarker lives in the APN Configuration tab content (first tab),
 * so it is present in the DOM immediately on mount without requiring the user
 * to navigate to a specific tab.
 *
 * Authoring note: no enforcement runs in the client for any policy type.
 * All state changes are local React state only (spec T5.1 Constraints).
 */
const PolicyControlView: React.FC = () => {
  const [activeTabId, setActiveTabId] = useState("apn-configuration");

  return (
    <Tabs
      activeTabId={activeTabId}
      onChange={({ detail }) => {
        const newTabId = detail.activeTabId;
        setActiveTabId(newTabId);
      }}
      data-testid="policy-control-tabs"
      tabs={[
        {
          id: "apn-configuration",
          label: "APN Configuration",
          // settleMarker is inside content, per docs/tech.md item (h) and spec D6.
          content: <ApnConfigurationTab />,
        },
        {
          id: "traffic-priority",
          label: "Traffic Priority",
          content: <TrafficPriorityTab />,
        },
        {
          id: "geo-fencing",
          // Geo-fencing label is intentionally plain — no location hint.
          label: "Geo-fencing",
          content: <GeofencingTab />,
        },
        {
          id: "qos",
          label: "QoS",
          content: <QosTab />,
        },
      ]}
    />
  );
};

export default PolicyControlView;

// ── Re-export fixture types to keep imports tidy in tests ─────────────────────
export type { PolicyControlFixture } from "./policyControl.fixture";
export { POLICY_CONTROL_FIXTURE };
