// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CampaignDetailView — detail shell for one data-collection campaign.
 *
 * Spec: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/tasks.md` T3.1
 * Contract: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/group3-contract.md`
 *
 * Route: `/data-model/data-collection-campaigns/:campaignName`
 * (parameterised, not in nav — reached only by clicking a row in
 * DataCollectionCampaignsView).
 *
 * ## What this shell owns
 *
 * Every fetch happens ONCE here on detail open, then passed as props to the three
 * panels. No panel fetches.  Four calls:
 *
 *   - `fetchDataProcessingCampaigns()` → campaigns (to find the group)
 *   - `fetchSignals()`                  → signal catalog
 *   - `fetchVehicleModels()`            → model manifests
 *   - `listVehiclesForSimulation()`     → vehicle catalog
 *
 * A `null` return from any client means the API is unconfigured — that is the
 * degradation contract, not an error.  The shell degrades gracefully rather than
 * showing an error.
 *
 * ## Security boundary
 *
 * `filterOemOwnedGroups` is applied BEFORE the campaign is looked up by name.
 * Reaching a non-entitled campaign by typing its name into the URL would bypass
 * the only tenant boundary this screen has.  The order is:
 *
 *   1. Group all raw campaigns with `groupCampaigns`
 *   2. Filter to OEM-entitled groups with `filterOemOwnedGroups`
 *   3. Find the group whose `campaignName` matches `useParams().campaignName`
 *
 * A test asserts this order, and a mutation (look up before filtering) confirms
 * the test FAILS when the order is reversed.
 *
 * ## Null-template degradation
 *
 * `cms-fleet-telemetry-30s` is live on staging with assignments and no template
 * row.  When `template` is null the Definition section renders
 * "Definition unavailable" and assignments are still listed.
 *
 * ## `signalsToFetch`
 *
 * `DataProcessingCampaignItem` has no `signalsToFetch` field — only UDS templates
 * carry it (9 entries on `uds-dtc-polling`, absent on every telemetry campaign).
 * The shell does the cast once, so the three panels do not each invent their own.
 * Contract § 4 specifies the exact form.
 *
 * ## Guard compliance
 *
 * - No `interface Campaign<X>` — props are inline per § 6(a).
 * - No liveness vocabulary per § 6(b) — `campaignStatusVocabulary.test.ts` scans
 *   this file.
 * - Not a lazy import from `App.tsx`, so `campaignAssignCallers.test.ts` § 6(c)
 *   does not apply here (CampaignVehiclesPanel.tsx is the caller).
 * - This file IS a lazy import from `App.tsx` (§ 6(d)) — `placeholderInventory`
 *   and `scaffoldingExpiry` counts are updated in T3.1.
 */

import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  fetchDataProcessingCampaigns,
  fetchSignals,
  fetchVehicleModels,
  type DataProcessingCampaignItem,
  type ModelManifestItem,
  type SignalItem,
} from "../../../api/dataModelClient";
import { listVehiclesForSimulation, type SimulationVehicleEntry } from "../../../api/subscriptionsClient";
import {
  filterOemOwnedGroups,
  groupCampaigns,
  type DataCollectionCampaignGroup,
} from "./campaignGrouping";
import type { SignalToFetch } from "./signalResolution";
import CampaignSignalsPanel from "./CampaignSignalsPanel";
import CampaignCoveragePanel from "./CampaignCoveragePanel";
import { CampaignVehiclesPanel } from "./CampaignVehiclesPanel";

// ── Load-state type ───────────────────────────────────────────────────────────

/**
 * "loading"    — initial load only; the ready tree is NOT mounted.
 * "refreshing" — a background refetch triggered by onAssignmentsChanged; the
 *                ready tree STAYS mounted so the panel's success alert survives.
 * "ready"      — data is current; ready tree is mounted.
 * "not-found"  — entitlement filter excluded this campaign by name.
 * "error"      — a fetch threw.
 */
type LoadStatus = "loading" | "refreshing" | "ready" | "not-found" | "error";

// ── CampaignDetailView ────────────────────────────────────────────────────────

/**
 * CampaignDetailView
 *
 * Shell: fetches all data once, passes as props to the three panels.
 * No panel fetches; every API call lives here.
 */
const CampaignDetailView: React.FC = () => {
  const { campaignName: rawCampaignName } = useParams<{ campaignName: string }>();
  const navigate = useNavigate();

  /**
   * The campaign name from the URL, percent-decoded.
   *
   * `decodeURIComponent` THROWS `URIError` on a malformed sequence — `"cms%fleet"` is enough
   * (verified). There is no `ErrorBoundary` anywhere in `src/`, so an uncaught throw here
   * blanks the entire SPA rather than showing anything.
   *
   * That matters specifically on THIS route. The entitlement rationale for the detail view
   * assumes someone may hand-type or hand-edit a campaign name into the URL — that is why
   * `filterOemOwnedGroups` runs before the lookup — so a hand-mangled percent-sequence is a
   * reachable input, not a theoretical one.
   *
   * A name that cannot be decoded cannot match any campaign, so it falls through to the
   * `not-found` alert this same path already renders for an unknown name. Degrading to the
   * existing state beats inventing a new one, and beats a blank page by a wide margin.
   */
  const campaignName = React.useMemo(() => {
    if (!rawCampaignName) return "";
    try {
      return decodeURIComponent(rawCampaignName);
    } catch {
      // Malformed percent-encoding. Use the raw value: it will not match a campaign, so the
      // screen renders "not found" instead of throwing out of render.
      return rawCampaignName;
    }
  }, [rawCampaignName]);

  // ── Fetched state ─────────────────────────────────────────────────────────

  const [loadStatus, setLoadStatus] = useState<LoadStatus>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  /**
   * Set when a BACKGROUND REFRESH fails, instead of tearing the screen down.
   *
   * A refresh failing is a different event from an initial load failing, and conflating
   * them was a defect: an operator who had just unassigned a vehicle saw the whole screen
   * replaced by "Failed to load campaign", which reads as though the unassign failed when
   * it had in fact succeeded. Misreporting a completed destructive action is worse than
   * showing nothing. See the Fix Group 5 note in `tasks.md`.
   */
  const [refreshError, setRefreshError] = useState<string | null>(null);
  /**
   * Monotonic request generation, so a slow earlier response cannot overwrite a newer one.
   *
   * `loadAll` awaits four fetches; two invocations can be in flight at once (an assign and
   * an unassign in quick succession, or a re-render that re-runs the effect). Without this,
   * whichever response lands LAST wins regardless of which was issued last — observed: a
   * vehicle the operator had just unassigned reappeared as still assigned because the
   * pre-unassign response resolved after the post-unassign one.
   */
  const requestGeneration = useRef(0);

  // Resolved campaign group (after entitlement filter and lookup by name)
  const [group, setGroup] = useState<DataCollectionCampaignGroup | null>(null);

  // Four separate data sources — each degrades to empty when its API returns null.
  const [signalCatalog, setSignalCatalog] = useState<readonly SignalItem[]>([]);
  const [modelManifests, setModelManifests] = useState<readonly ModelManifestItem[]>([]);
  const [vehicleCatalog, setVehicleCatalog] = useState<readonly SimulationVehicleEntry[]>([]);

  // ── Fetch on mount / triggered refresh ───────────────────────────────────

  /**
   * loadAll — fetches all four data sources.
   *
   * `isRefresh=true` is set by onAssignmentsChanged. In that case the status
   * transitions to "refreshing" rather than "loading" so the ready tree stays
   * mounted and the panel's success alert is not destroyed.
   *
   * The initial mount always uses `isRefresh=false` (the default).
   */
  const loadAll = useCallback(async (isRefresh = false) => {
    // Claim a generation. Any response from an older generation is discarded below.
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;

    // If already ready and this is a triggered refresh, leave the tree mounted.
    setLoadStatus((prev) =>
      isRefresh && (prev === "ready" || prev === "refreshing") ? "refreshing" : "loading",
    );
    setErrorMessage(null);
    setRefreshError(null);

    try {
      // All four fetches in parallel.
      const [campaignsResp, signalsResp, modelsResp, vehiclesResp] = await Promise.all([
        fetchDataProcessingCampaigns(),
        fetchSignals(),
        fetchVehicleModels(),
        listVehiclesForSimulation(),
      ]);

      // A newer request was issued while these were in flight — discard this response
      // entirely rather than letting stale data win. Must come before ANY setState.
      if (generation !== requestGeneration.current) return;

      // null = API unconfigured — degrade, do not error.
      const rawCampaigns = campaignsResp?.dcCampaigns ?? [];
      const rawSignals = signalsResp?.signals ?? [];
      const rawModels = modelsResp?.modelManifests ?? [];
      const rawVehicles = vehiclesResp?.vehicles ?? [];

      // SECURITY: apply filterOemOwnedGroups BEFORE looking up by campaignName.
      // Reversing this order would let an operator reach a non-entitled campaign
      // by typing its name into the URL.  A test in CampaignDetailView.test.tsx
      // mutation-verifies this order is load-bearing.
      const entitledGroups = filterOemOwnedGroups(groupCampaigns(rawCampaigns));
      const found = entitledGroups.find((g) => g.campaignName === campaignName);

      if (!found) {
        // On a REFRESH, keep what is on screen. The campaign was present moments ago, so a
        // refresh that cannot find it is far more likely a transient or unconfigured read
        // than a real disappearance — and replacing a completed unassign's confirmation
        // with "campaign not found" misreports the action the operator just took.
        if (isRefresh) {
          setRefreshError(
            "Could not refresh this campaign's data. The information below may be out of date.",
          );
          setLoadStatus("ready");
          return;
        }
        setLoadStatus("not-found");
        return;
      }

      // Catalogs are written only once the campaign itself resolved, so a refresh that
      // fails to find it cannot leave the panels rendering against a half-updated set.
      setSignalCatalog(rawSignals);
      setModelManifests(rawModels);
      setVehicleCatalog(rawVehicles);
      setGroup(found);
      setLoadStatus("ready");
    } catch (err: unknown) {
      if (generation !== requestGeneration.current) return;
      const message = err instanceof Error ? err.message : String(err);
      // A failed REFRESH must not tear down the ready tree. Doing so destroys the panel's
      // confirmation alert and tells the operator the campaign failed to load, seconds
      // after their assign or unassign actually succeeded.
      if (isRefresh) {
        setRefreshError(
          `Could not refresh this campaign's data (${message}). The information below may be out of date.`,
        );
        setLoadStatus("ready");
        return;
      }
      setErrorMessage(message);
      setLoadStatus("error");
    }
  }, [campaignName]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // ── Loading — initial only ──────────────────────────────────────────────────

  if (loadStatus === "loading") {
    return (
      <Box
        textAlign="center"
        padding={{ vertical: "xl" }}
        data-testid="campaign-detail-loading"
      >
        <Spinner size="large" />
      </Box>
    );
  }

  // ── Error ─────────────────────────────────────────────────────────────────

  if (loadStatus === "error") {
    return (
      <Alert
        type="error"
        header="Failed to load campaign"
        data-testid="campaign-detail-error"
      >
        {errorMessage ?? "An unexpected error occurred loading campaign data."}
      </Alert>
    );
  }

  // ── Not found ─────────────────────────────────────────────────────────────

  if (loadStatus === "not-found" || group === null) {
    return (
      <Alert
        type="warning"
        header={`Campaign not found: ${campaignName}`}
        data-testid="campaign-detail-not-found"
      >
        <Link
          onFollow={(e) => {
            e.preventDefault();
            void navigate("/data-model/data-collection-campaigns");
          }}
          href="/data-model/data-collection-campaigns"
        >
          ← Back to Data Collection Campaigns
        </Link>
      </Alert>
    );
  }

  // ── Ready — extract template and compute signalsToFetch ───────────────────

  const { template } = group;

  // `signalsToFetch` lives only on UDS templates. Contract § 4 specifies the
  // exact cast so every consumer uses the same form.
  const signalsToFetch = (
    template as unknown as { signalsToFetch?: readonly SignalToFetch[] } | null
  )?.signalsToFetch;

  // ── Definition section ────────────────────────────────────────────────────

  // Hoisted to locals — provenanceRender guard fires on template.status in JSX.
  const templateStatus = template?.status ?? "";
  const templateOwner = template?.owner ?? "—";
  const templateDecoderManifestId = template?.decoderManifestId ?? "";
  const templateDescription = template?.description ?? "—";
  const templateCreatedAt = template?.createdAt ?? "—";
  // category is optional server-side; absent from most templates. 
  const templateCategory = template?.category;

  const definitionContent =
    template === null ? (
      // Live on staging: `cms-fleet-telemetry-30s` has assignments and no template.
      // Degrade to "Definition unavailable" while still showing assignments below.
      <Alert
        type="warning"
        header="Definition unavailable"
        data-testid="campaign-detail-definition-unavailable"
      >
        No template row exists for this campaign. Assignments are listed below.
      </Alert>
    ) : (
      <ColumnLayout columns={3} variant="text-grid" data-testid="campaign-detail-definition-fields">
        <KeyValuePairs
          columns={1}
          items={[
            { label: "Decoder manifest", value: templateDecoderManifestId || "—" },
            {
              label: "Collection scheme",
              value: (() => {
                const cs = template.collectionScheme;
                if (!cs) return "—";
                const t = typeof cs.type === "string" ? cs.type : "";
                const p = typeof cs.periodMs === "number" ? ` · ${String(cs.periodMs)} ms` : "";
                return t ? `${t}${p}` : "—";
              })(),
            },
            {
              label: "Category",
              value: templateCategory != null
                ? String(templateCategory)
                : <Box color="text-status-inactive" data-testid="campaign-detail-category-absent">—</Box>,
            },
          ]}
        />
        <KeyValuePairs
          columns={1}
          items={[
            { label: "Owner", value: templateOwner },
          ]}
        />
        <KeyValuePairs
          columns={1}
          items={[
            { label: "Description", value: templateDescription },
            { label: "Created", value: templateCreatedAt },
            {
              label: "Status",
              // Never render the stored status token as a liveness word.
              // The raw token (`templateStatus`) is diagnostic, not operator, information.
              // Guard: campaignStatusVocabulary.test.ts.
              value: (
                <DefinitionStatusBadge status={templateStatus} />
              ),
            },
          ]}
        />
      </ColumnLayout>
    );

  return (
    <SpaceBetween size="l" data-testid="campaign-detail-ready">
      {/* A failed background refresh is reported HERE, inline and dismissible, rather than
          by replacing the tree with a full-page error. The operator's assign/unassign
          confirmation stays on screen alongside it, because that action did succeed — the
          refresh is what failed. */}
      {refreshError !== null && (
        <Alert
          type="warning"
          header="Showing data that may be out of date"
          dismissible
          onDismiss={() => setRefreshError(null)}
          data-testid="campaign-detail-refresh-warning"
        >
          {refreshError}
        </Alert>
      )}
      {/* ── Definition ──────────────────────────────────────────────────── */}
      <Container
        header={
          <Header variant="h1" description={`Campaign: ${campaignName}`}>
            {campaignName}
          </Header>
        }
        data-testid="campaign-detail-definition-container"
      >
        {definitionContent}
      </Container>

      {/* ── Signals panel ───────────────────────────────────────────────── */}
      <Container
        header={<Header variant="h2">Signals</Header>}
        data-testid="campaign-detail-signals-container"
      >
        <CampaignSignalsPanel
          signalsToCollect={template?.signalsToCollect}
          signalCatalog={signalCatalog}
        />
      </Container>

      {/* ── Coverage panel ──────────────────────────────────────────────── */}
      <CampaignCoveragePanel
        signalsToFetch={signalsToFetch}
        signalCatalog={signalCatalog}
        decoderManifestId={templateDecoderManifestId || undefined}
        modelManifests={modelManifests}
      />

      {/* ── Vehicles panel ──────────────────────────────────────────────── */}
      <CampaignVehiclesPanel
        campaignName={campaignName}
        vehicleAssignments={group.vehicleAssignments}
        scopedAssignments={group.scopedAssignments}
        vehicleCatalog={vehicleCatalog}
        onAssignmentsChanged={() => void loadAll(true)}
      />
    </SpaceBetween>
  );
};

// ── DefinitionStatusBadge ─────────────────────────────────────────────────────

/**
 * Renders a status-neutral gloss for the template's stored status.
 *
 * Deliberately NEVER renders "Running", "Live", "Streaming", "Online",
 * "Reporting", "Currently running", or "Running" delimited — the vocabulary
 * forbidden by `campaignStatusVocabulary.test.ts`.
 *
 * `RUNNING` on a template row means the campaign definition is active (assigned),
 * which is a stored token, not a claim about telemetry. Rendering it as a liveness
 * word is an unsupported claim because no reconciliation with actual transmission
 * exists in any reachable surface.
 */
/**
 * Definition-state badge for the detail view.
 *
 * **This is now the ONLY place the definition-state vocabulary reaches a screen.** The list
 * view carried a `CampaignDefinitionIndicator` column until user UAT removed it ("I'm not sure
 * what 'definition' means… we probably don't need that as a column header"), so the closed-gloss
 * property and its guard moved here rather than being dropped.
 *
 * Never renders the stored token. A template carrying `RUNNING` — reachable, because
 * `PUT /campaigns/{id}` writes `status` straight from the request body with no allowlist —
 * must not put "RUNNING" on a screen an operator reads for liveness. The five branches below
 * are the permitted vocabulary; `src/__tests__/campaignStatusVocabulary.test.ts` pins the set
 * by testid so a sixth cannot ship without a fixture.
 */
function DefinitionStatusBadge({ status }: { status: string }): React.ReactElement {
  const stored = status.toUpperCase();
  if (stored === "ACTIVE") {
    return (
      <StatusIndicator type="success" data-testid="dc-campaign-definition-available">
        Available
      </StatusIndicator>
    );
  }
  if (stored === "SUSPENDED") {
    return (
      <StatusIndicator type="warning" data-testid="dc-campaign-definition-suspended">
        Suspended
      </StatusIndicator>
    );
  }
  if (stored === "STOPPED") {
    return (
      <StatusIndicator type="stopped" data-testid="dc-campaign-definition-stopped">
        Stopped
      </StatusIndicator>
    );
  }
  if (stored === "") {
    return (
      <Box color="text-status-inactive" data-testid="dc-campaign-definition-absent">
        —
      </Box>
    );
  }
  // Fixed string — never the stored token (could be "RUNNING" or any future value).
  return (
    <StatusIndicator type="info" data-testid="dc-campaign-definition-other">
      Unrecognized state
    </StatusIndicator>
  );
}

export default CampaignDetailView;
