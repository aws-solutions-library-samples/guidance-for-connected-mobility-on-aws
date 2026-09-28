// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * TripSimulatorModal — CS portal duplicate of CMS's `TripSimulatorModal.tsx`.
 *
 * SYNC WITH: modules/cms_ui/source/frontend/src/components/vehicles/vehicle-detail/TripSimulatorModal.tsx
 *
 * Spec: `.kiro/specs/2026-09-19-cs-trip-simulator-parity`, T4.1.
 *
 * ## Why a duplicate, not a shared package
 *
 * `docs/tech.md` § "CS Trip Simulator Parity — T1.1 build-tooling research"
 * (2026-09-19): both modules run Vite 7.x (compatible tooling) but have NO
 * workspace mechanism unifying them — no repo-root `package.json`, and each
 * module's path-aliasing is hardcoded to its own `src/`. Setting up real npm
 * workspaces to share ONE component is out of this spec's reasonable scope;
 * recommended as its own follow-on initiative if a SECOND shared-component
 * need arises.
 *
 * `scripts/check_trip_simulator_modal_sync.py` diffs this file against its
 * CMS counterpart (excluding the injection-point differences documented
 * below) and fails CI if they silently diverge. Run it after editing either
 * file.
 *
 * ## Differences from the CMS original — the injection points the drift
 * check must exclude
 *
 * CMS's original hardcodes three things this duplicate takes as props
 * instead, because CS's request/auth plumbing differs from CMS's:
 *
 *   1. **Event catalog fetch** — CMS calls `authFetch(`${API()}/api/v1/
 *      event-catalog`)` directly, unauthenticated-fallback style. CS calls
 *      the injected `fetchEventCatalog` prop (wired to
 *      `dataModelClient.ts::fetchEventCatalog`, T3.1), which reads a
 *      DIFFERENT documented envelope (`{events, count}` only — no
 *      `Items`-fallback tolerance; see `decisions.md`'s T3.1/T3.2 entry for
 *      why this duplicate does NOT reproduce CMS's `data.events ||
 *      data.Items || data || []` tolerance).
 *   2. **Start request** — CMS POSTs straight to
 *      `getSimulationApiUrl('/start')` with NO authentication and a request
 *      shape including `vehicle_source`/`vehicles`/`interval`/
 *      `driver_selection`/`aws_region` (a fleet-simulation-shaped body, even
 *      though it's used here for a single vehicle). CS calls the injected
 *      `onStart` prop (wired to `subscriptionsClient.ts::startVehicleSimulation`
 *      via `TripSimulationParams`, T4.2), which is authenticated (ID token)
 *      and sends none of those five CMS-only fields — CS's server-side
 *      contract (`simulate_vehicle/handler.py`) doesn't read them, and adding
 *      them would misrepresent this as a fleet-simulation call.
 *   3. **Mode is never sent, and the "Source" selector is HIDDEN, not just
 *      stripped at the call site (T4.3 decision (a))** — see the
 *      `showSourceSelector` section below.
 *
 * Everything else — CITIES, the route-length presets, the safety/maintenance
 * Multiselects, the event-catalog-derived option building, the `severity_hint`
 * pairing, the DTC-code label suffix — is a direct behavioral mirror. If you
 * change one, change the other, then re-run the sync check.
 *
 * ## `showSourceSelector` (T4.3, decision (a))
 *
 * CS's own design principle, stated repeatedly across this codebase's own
 * `simulate_vehicle/handler.py` docstring and `SimulateVehicleView.tsx`'s own
 * header copy, is "the simulation path is derived from the vehicle's
 * dataSource — never chosen by the operator." Showing a "Source" `Select`
 * whose value is then silently discarded (mode is never read from
 * `TripSimulationParams` — see its docstring in `subscriptionsClient.ts`)
 * would contradict that principle in the same screen that states it. CS's
 * usage therefore passes `showSourceSelector={false}` and this component
 * hides the FormField entirely rather than rendering it disabled — decision
 * (a) over decision (b) from T4.3's Accept text, since CS has no concrete
 * need for operators to see the derived value before starting (the dataSource
 * label is already visible on the vehicle picker in `SimulateVehicleView.tsx`,
 * per `vehicleOptionLabel()`'s `path: ${dataSourceLabel(v.dataSource)}` line).
 */

import React, { useEffect, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Modal from "@cloudscape-design/components/modal";
import Multiselect from "@cloudscape-design/components/multiselect";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";

import type { EventCatalogItem, EventCatalogResponse } from "../../../api/dataModelClient";
import type { TripSimulationParams } from "../../../api/subscriptionsClient";

// ── CITIES — SYNC WITH CMS's own const of the same name ──────────────────────
//
// Must exactly match `_ALLOWED_CITIES` in
// `services/connectors/subscriptions/simulate_vehicle/handler.py` (T2.1) —
// that allowlist is the server-side authority; this is the picker mirror.

// SYNC-BLOCK-START: cities
// Must exactly match `_ALLOWED_CITIES` in
// services/connectors/subscriptions/simulate_vehicle/handler.py (T2.1) —
// that allowlist is the server-side authority; this is the picker mirror.

const CITIES = [
  { label: "Atlanta", value: "atlanta" },
  { label: "Chicago", value: "chicago" },
  { label: "Miami", value: "miami" },
  { label: "Munich", value: "munich" },
  { label: "New York", value: "nyc" },
  { label: "San Francisco", value: "sf" },
  { label: "Seattle", value: "seattle" },
];
// SYNC-BLOCK-END: cities

// ── Severity label — inlined (no shared severity.ts module in this repo) ─────
//
// CMS's original imports `severityLabel` from `../../../utils/severity`.
// `connected_services_ui` has no equivalent module — this is a deliberate,
// minimal reproduction of that one function's behavior
// (`docs/tech.md`'s "GET /api/v1/event-catalog" § "Severity scale":
// 4=CRITICAL/P0, 3=HIGH/P1, 2=MEDIUM/P2, 1=LOW/P3), not a new module.
//
// SYNC-BLOCK-START: severity-label
// Keep faithful to CMS's `modules/cms_ui/source/frontend/src/utils/severity.ts`.
// Numeric scale is REVERSE-RANKED (higher = worse): >= 4 → CRITICAL, 3 → HIGH,
// 2 → MEDIUM, <= 1 → LOW. Stringified numerics follow the same path. Already-
// canonical strings are accepted in any case. null/undefined/'' → 'UNKNOWN'.
// MED → MEDIUM.  Any unrecognised value → 'UNKNOWN' (NOT a raw echo).
function severityLabel(raw: number | string | null | undefined): string {
  if (raw === null || raw === undefined || raw === "") return "UNKNOWN";

  // Numeric (or numeric-as-string) path — reverse-ranked scale.
  const asNum = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isNaN(asNum)) {
    if (asNum >= 4) return "CRITICAL";
    if (asNum === 3) return "HIGH";
    if (asNum === 2) return "MEDIUM";
    if (asNum <= 1) return "LOW";
  }

  // String-based paths — normalise case before comparison.
  const s = String(raw).trim().toUpperCase();
  switch (s) {
    case "P0":
    case "CRITICAL":
      return "CRITICAL";
    case "P1":
    case "HIGH":
      return "HIGH";
    case "P2":
    case "MEDIUM":
    case "MED":
      return "MEDIUM";
    case "P3":
    case "LOW":
      return "LOW";
    default:
      return "UNKNOWN";
  }
}
// SYNC-BLOCK-END: severity-label

interface EventOption {
  label: string;
  value: string;
  description?: string;
}

/** Build the safety/maintenance option lists from a real event-catalog response. */
function optionsFromCatalog(
  data: EventCatalogResponse,
): { safety: EventOption[]; maintenance: EventOption[] } {
  const safety: EventOption[] = [];
  const maintenance: EventOption[] = [];
  for (const evt of data.events as readonly EventCatalogItem[]) {
    const baseLabel = evt.description || evt.event_id;
    const label = evt.dtc_code ? `${baseLabel} · ${evt.dtc_code}` : baseLabel;
    const option: EventOption = {
      label,
      value: evt.event_id,
      description: `Signal: ${evt.trigger_signal || "—"} | Severity: ${severityLabel(evt.severity ?? evt.severity_hint)}${evt.dtc_code ? " | Creates DTC" : ""}`,
    };
    if (evt.category === "safety") safety.push(option);
    else if (evt.category === "maintenance") maintenance.push(option);
  }
  return {
    safety: safety.sort((a, b) => a.label.localeCompare(b.label)),
    maintenance: maintenance.sort((a, b) => a.label.localeCompare(b.label)),
  };
}

export interface TripSimulatorModalProps {
  visible: boolean;
  vehicleId: string;
  vin?: string;
  onDismiss: () => void;
  onStarted?: (simId: string) => void;
  /**
   * Fetch the event catalog. Injected so this component has no direct
   * dependency on `dataModelClient.ts` — matches the prop-injection pattern
   * `spec.md`'s Design section calls for (`eventCatalogUrl` was the spec's
   * working name; this takes the fetch FUNCTION rather than a URL because
   * `fetchEventCatalog` already encapsulates base-URL resolution and auth).
   */
  fetchEventCatalog: () => Promise<EventCatalogResponse | null>;
  /**
   * Start the simulation. Injected so this component has no direct
   * dependency on `subscriptionsClient.ts`. Receives the vehicle id and the
   * trip params this modal collected; the caller is responsible for
   * threading through to `startVehicleSimulation(vehicleId, fetch,
   * tripParams)`. Returns the simulation id on success. Errors from this
   * function are caught into the local `error` state (see `handleStart`'s
   * try/catch) — they do NOT propagate to the caller.
   *
   * Consequence for the injected function, and the reason this docstring was
   * wrong before: because nothing reaches the caller, the function MUST set any
   * parent state it needs itself, before throwing. `SimulateVehicleView.tsx`'s
   * wrapper does exactly that — it sets `phase`/`errorMsg`/`canQuickAssign` in
   * its own catch, and for `no_telemetry_campaign` dismisses this modal first so
   * the parent's recovery affordance is reachable. An implementation that relies
   * on a throw reaching the caller silently loses that recovery path; the
   * earlier version of this docstring promised exactly that, and the promise
   * was false.
   */
  onStart: (vehicleId: string, tripParams: TripSimulationParams) => Promise<string>;
  /**
   * Whether to render the "Source" FormField. CS passes `false` (T4.3
   * decision (a) — see this file's own header docstring). Defaults to `true`
   * so a future second consumer that DOES want the selector doesn't need to
   * pass anything.
   */
  showSourceSelector?: boolean;
}

const TripSimulatorModal: React.FC<TripSimulatorModalProps> = ({
  visible,
  vehicleId,
  vin,
  onDismiss,
  onStarted,
  fetchEventCatalog,
  onStart,
  showSourceSelector = true,
}) => {
  const [city, setCity] = useState(CITIES[0]);
  // Mode state is retained even when the selector is hidden (showSourceSelector
  // === false) — it is NEVER read by `handleStart` below (TripSimulationParams
  // has no mode field), so its value is inert either way. Keeping the state
  // avoids a second conditional code path purely for a value nothing reads.
  const [mode, setMode] = useState<any>({ value: "fwe", label: "FWE Agent" });
  const [routeLength, setRouteLength] = useState<any>({
    value: "20",
    label: "Default (~5 min, 20 points)",
  });
  const [selectedSafety, setSelectedSafety] = useState<any[]>([]);
  const [selectedMaintenance, setSelectedMaintenance] = useState<any[]>([]);
  const [safetyOptions, setSafetyOptions] = useState<EventOption[]>([]);
  const [maintenanceOptions, setMaintenanceOptions] = useState<EventOption[]>([]);
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Fetch event catalog on mount — mirrors CMS's own mount-time fetch.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoadingCatalog(true);
      try {
        const data = await fetchEventCatalog();
        if (!cancelled && data) {
          const { safety, maintenance } = optionsFromCatalog(data);
          setSafetyOptions(safety);
          setMaintenanceOptions(maintenance);
        }
      } catch {
        // Degrade to empty option lists — mirrors CMS's own
        // console.warn-and-continue behavior. The Multiselects below already
        // render a "No events in catalog" placeholder for an empty list.
      }
      if (!cancelled) setLoadingCatalog(false);
    };
    void load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (visible) {
      setCity(CITIES[Math.floor(Math.random() * CITIES.length)]);
      setSelectedSafety([]);
      setSelectedMaintenance([]);
      setError("");
    }
  }, [visible]);

  const handleStart = async () => {
    setLoading(true);
    setError("");
    try {
      const tripParams: TripSimulationParams = {
        city: city.value,
        trips: 1,
        route_length: Number(routeLength.value),
        safety_scenarios: selectedSafety.map((s) => s.value),
        maintenance_scenarios: selectedMaintenance.map((s) => s.value),
      };
      const simId = await onStart(vehicleId, tripParams);
      onStarted?.(simId);
      onDismiss();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header="Trip Simulator"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" onClick={() => { void handleStart(); }} loading={loading}>
              Start Trip
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        <Alert type="info">
          Simulate a trip for <strong>{vin || vehicleId}</strong>. Select events from the
          catalog to test during this trip.
        </Alert>
        <FormField label="City">
          <Select
            selectedOption={city}
            onChange={({ detail }) => setCity(detail.selectedOption as typeof city)}
            options={CITIES}
          />
        </FormField>
        {showSourceSelector && (
          <FormField label="Source">
            <Select
              selectedOption={mode}
              onChange={({ detail }) => setMode(detail.selectedOption)}
              options={[
                { value: "mqtt_direct", label: "MQTT Direct" },
                { value: "fwe", label: "FWE Agent" },
              ]}
            />
          </FormField>
        )}
        <FormField
          label="Route Length"
          description="How many GPS waypoints to generate per trip. Each point ≈ 15 seconds of simulated driving. Shorter routes finish faster for quick smoke tests; longer routes exercise longer-duration event triggers."
        >
          {/* SYNC-BLOCK-START: route-length-options */}
          <Select
            selectedOption={routeLength}
            onChange={({ detail }) => setRouteLength(detail.selectedOption)}
            options={[
              { value: "10", label: "Short (~2.5 min, 10 points)" },
              { value: "20", label: "Default (~5 min, 20 points)" },
              { value: "30", label: "Medium (~7.5 min, 30 points)" },
              { value: "45", label: "Long (~11 min, 45 points)" },
              { value: "60", label: "Max (~15 min, 60 points)" },
            ]}
          />
          {/* SYNC-BLOCK-END: route-length-options */}
        </FormField>
        <FormField label="Safety Events" description="Select safety events to simulate during this trip">
          {loadingCatalog ? (
            <Spinner />
          ) : (
            <Multiselect
              selectedOptions={selectedSafety}
              onChange={({ detail }) => setSelectedSafety([...detail.selectedOptions])}
              options={safetyOptions}
              placeholder={safetyOptions.length ? "None (normal driving)" : "No events in catalog"}
              filteringType="auto"
            />
          )}
        </FormField>
        <FormField
          label="Maintenance Events"
          description="Select maintenance conditions to simulate. Events ending with a code (e.g. '· P0520') create an active DTC row."
        >
          {loadingCatalog ? (
            <Spinner />
          ) : (
            <Multiselect
              selectedOptions={selectedMaintenance}
              onChange={({ detail }) => setSelectedMaintenance([...detail.selectedOptions])}
              options={maintenanceOptions}
              placeholder={maintenanceOptions.length ? "None (healthy vehicle)" : "No events in catalog"}
              filteringType="auto"
            />
          )}
        </FormField>
        {error && <Alert type="error">{error}</Alert>}
      </SpaceBetween>
    </Modal>
  );
};

export default TripSimulatorModal;
