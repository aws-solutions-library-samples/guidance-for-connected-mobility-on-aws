// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * TelemetryCampaignDetailModal — what the selected vehicle will actually collect.
 *
 * The Simulate page's coverage line answers "which campaigns" and "how many
 * distinct signals". This answers "which signals", per campaign, by reusing
 * `screens/data-model/CampaignSignalsPanel` rather than reimplementing the
 * signal-catalog join. That panel takes `signalsToCollect` plus a `signalCatalog`
 * and owns all the grouping, the collapse-by-default behaviour (293 rows expanded
 * is not useful), and the entries-vs-distinct reconciliation.
 *
 * ## Why this fetches and the readiness endpoint does not
 *
 * `signalsToCollect` is up to 293 ids per campaign, and a vehicle can carry four
 * campaigns. Returning them from `GET /simulate/vehicles` would multiply that by 99
 * vehicles for data only ever read one vehicle at a time. So the readiness response
 * carries counts, and the ids are fetched here, on open — the same split
 * `CampaignDetailView` uses.
 *
 * ## Matching
 *
 * The readiness entry gives `campaignId`; the full record comes from
 * `fetchDataProcessingCampaigns()`. A readiness entry whose `campaignId` is null
 * (set-shaped coverage, which carries no identity) cannot be matched and is
 * reported as such rather than silently dropped — a missing row would read as "this
 * campaign collects nothing".
 *
 * ## Guard compliance
 *
 * No bare `Campaign*` identifier and no bare `campaign:` property key, per
 * `contentBoundary.test.ts` Suite 3 — see `telemetryCampaignSummary.ts` for why the
 * prefix is `TelemetryCampaign*` and not the guard's suggested `SoftwareCampaign*`.
 */

import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import React, { useEffect, useState } from "react";

import type {
  DataProcessingCampaignItem,
  SignalItem,
} from "../../../api/dataModelClient";
import {
  fetchDataProcessingCampaigns,
  fetchSignals,
} from "../../../api/dataModelClient";
import type { SimulationVehicleEntry } from "../../../api/subscriptionsClient";
import CampaignSignalsPanel from "../data-model/CampaignSignalsPanel";

/** Four load states, kept distinct — "not configured" is not an error. */
type LoadState =
  | { readonly kind: "loading" }
  | { readonly kind: "not_configured" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "loaded";
      readonly records: readonly DataProcessingCampaignItem[];
      readonly signalCatalog: readonly SignalItem[];
    };

const SCOPE_WORDING: Record<string, string> = {
  vehicle: "assigned to this vehicle",
  fleet: "assigned to this vehicle's fleet",
  broadcast: "a fleet-wide campaign",
};

const TelemetryCampaignDetailModal: React.FC<{
  readonly visible: boolean;
  readonly onDismiss: () => void;
  readonly vehicle: SimulationVehicleEntry | null | undefined;
}> = ({ visible, onDismiss, vehicle }) => {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    setState({ kind: "loading" });
    void Promise.all([fetchDataProcessingCampaigns(), fetchSignals()])
      .then(([campaignsResp, signalsResp]) => {
        if (cancelled) return;
        // Either endpoint returning null means the data-processing API is not
        // configured for this environment. That is a deployment fact, not a
        // failure, and must not be reported as one.
        if (!campaignsResp || !signalsResp) {
          setState({ kind: "not_configured" });
          return;
        }
        setState({
          kind: "loaded",
          records: campaignsResp.dcCampaigns,
          signalCatalog: signalsResp.signals,
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Failed to load campaigns",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [visible]);

  const covering = vehicle?.telemetry_campaigns ?? [];
  const total = vehicle?.telemetry_signal_total;

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header="Signals this vehicle will collect"
      size="large"
      data-testid="simulate-vehicle-campaign-modal"
      footer={
        <Box float="right">
          <Button
            variant="primary"
            onClick={onDismiss}
            data-testid="simulate-vehicle-campaign-modal-close"
          >
            Close
          </Button>
        </Box>
      }
    >
      <SpaceBetween size="m">
        <Box data-testid="simulate-vehicle-campaign-modal-summary">
          {covering.length === 0
            ? "No RUNNING campaign covers this vehicle."
            : `${covering.length} campaign${covering.length === 1 ? "" : "s"} ` +
              `cover${covering.length === 1 ? "s" : ""} this vehicle` +
              (typeof total === "number"
                ? `, collecting ${total} distinct signal${total === 1 ? "" : "s"} ` +
                  "between them."
                : ".")}
        </Box>

        {vehicle?.telemetry_campaign_applicable === false && covering.length > 0 ? (
          <Alert
            type="info"
            data-testid="simulate-vehicle-campaign-modal-inert"
            header="These campaigns will not collect on this vehicle"
          >
            This vehicle sends cloud telemetry, which reaches the platform via the
            rule path. No onboard agent runs a FleetWise campaign for it, so the
            signals below describe what these campaigns would collect on an onboard
            vehicle — not what this one sends.
          </Alert>
        ) : null}

        {state.kind === "loading" ? (
          <Spinner data-testid="simulate-vehicle-campaign-modal-loading" />
        ) : state.kind === "not_configured" ? (
          <Alert type="info" data-testid="simulate-vehicle-campaign-modal-unconfigured">
            The data-processing API is not configured for this environment, so the
            per-signal breakdown is unavailable. The campaign names and counts above
            come from the readiness check and are unaffected.
          </Alert>
        ) : state.kind === "error" ? (
          <Alert type="error" data-testid="simulate-vehicle-campaign-modal-error">
            Could not load the signal catalog: {state.message}
          </Alert>
        ) : (
          <SpaceBetween size="s">
            {covering.map((entry, idx) => {
              const record = entry.campaignId
                ? state.records.find((r) => r.campaignId === entry.campaignId)
                : undefined;
              const label =
                entry.campaignName ||
                entry.campaignId ||
                SCOPE_WORDING[entry.scope] ||
                entry.target;
              const how = SCOPE_WORDING[entry.scope] ?? entry.target;
              return (
                <ExpandableSection
                  key={entry.campaignId ?? `${entry.target}-${idx}`}
                  headerText={`${label} — ${how}`}
                  data-testid={`simulate-vehicle-campaign-modal-section-${idx}`}
                  defaultExpanded={covering.length === 1}
                >
                  {record ? (
                    <CampaignSignalsPanel
                      signalsToCollect={record.signalsToCollect}
                      signalCatalog={state.signalCatalog}
                    />
                  ) : (
                    <Box
                      color="text-status-inactive"
                      data-testid={`simulate-vehicle-campaign-modal-unmatched-${idx}`}
                    >
                      {entry.campaignId
                        ? `This campaign (${entry.campaignId}) was not returned by the ` +
                          "campaigns endpoint, so its signal list cannot be shown. It " +
                          "still covers this vehicle" +
                          (typeof entry.signalCount === "number"
                            ? ` and collects ${entry.signalCount} signal entr` +
                              `${entry.signalCount === 1 ? "y" : "ies"}.`
                            : ".")
                        : "The readiness check reported coverage at this scope but " +
                          "carried no campaign identity, so the signal list cannot " +
                          "be looked up."}
                    </Box>
                  )}
                </ExpandableSection>
              );
            })}
          </SpaceBetween>
        )}
      </SpaceBetween>
    </Modal>
  );
};

export default TelemetryCampaignDetailModal;
