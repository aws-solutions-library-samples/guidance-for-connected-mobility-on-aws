// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CampaignCoveragePanel — answers the ECU question honestly.
 *
 * Spec: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/tasks.md` T3.3
 *
 * ## Two modes, decided by `signalsToFetch`
 *
 * **UDS** (`signalsToFetch` present and non-empty): shows "ECUs polled" — the
 * per-campaign ECU indices from `params[0]`, with resolved signal names and
 * `executionFrequencyMs`. This is the ONLY per-campaign ECU data that exists.
 *
 * **Telemetry** (`signalsToFetch` absent or empty): shows "ECUs available on
 * vehicles running <decoderManifestId>" — the ECU roster from model manifests
 * whose `decoderManifestRef` matches the campaign's `decoderManifestId`,
 * deduplicated by `ecu` code.
 *
 * ## What the telemetry panel MUST NOT claim
 *
 * The telemetry ECU list is the VEHICLE's hardware roster, NOT an attribution
 * of this campaign's signals to those ECUs. There is no signal→ECU mapping in
 * any reachable surface — verified across all 302 signal records (no `ecu` field)
 * and all 21 campaign attributes. The panel's heading and caption must make this
 * clear so an operator does not mistake the vehicle's ECU inventory for a
 * description of what the campaign's signals measure.
 *
 * ## Roster filter
 *
 * Telemetry ECU roster = manifests whose `decoderManifestRef === decoderManifestId`,
 * deduplicated by `ecu` code. Measured 2026-09-20: `cms-fleet-v3` yields
 * CMS-FLEET-MODEL's **8** ECUs; the 9-code union across ALL manifests is wrong
 * because `ECM` is not on `cms-fleet-v3`. An unfiltered union would over-claim
 * the vehicle's hardware. See `group3-contract.md` § 2.
 *
 * ## No fetch
 *
 * Props only. The shell (`CampaignDetailView.tsx`) fetches all data once on
 * detail open and passes it down.
 */

import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import React from "react";

import type { ModelManifestItem, SignalItem } from "../../../api/dataModelClient";
import {
  resolveEcuPolls,
  type EcuPoll,
  type SignalToFetch,
} from "./signalResolution";

// ── Absent-value marker ───────────────────────────────────────────────────────

function Absent(): React.JSX.Element {
  return <em>—</em>;
}

// ── UDS panel ─────────────────────────────────────────────────────────────────

function UdsCoveragePanel(props: {
  readonly signalsToFetch: readonly SignalToFetch[];
  readonly signalCatalog: readonly SignalItem[];
}): React.JSX.Element {
  const polls: readonly EcuPoll[] = resolveEcuPolls(
    props.signalsToFetch,
    props.signalCatalog,
  );

  return (
    <Container
      header={<Header variant="h2">ECUs polled</Header>}
      data-testid="coverage-panel-uds"
    >
      <Table
        data-testid="coverage-panel-uds-table"
        columnDefinitions={[
          {
            id: "ecuIndex",
            header: "ECU index",
            cell: (item: EcuPoll) => item.ecuIndex,
          },
          {
            id: "signalName",
            header: "Signal",
            cell: (item: EcuPoll) =>
              item.signalName !== null ? (
                item.signalName
              ) : (
                <Absent />
              ),
          },
          {
            id: "functionName",
            header: "Function",
            cell: (item: EcuPoll) => item.functionName,
          },
          {
            id: "executionFrequencyMs",
            header: "Frequency (ms)",
            cell: (item: EcuPoll) =>
              item.executionFrequencyMs !== null ? (
                item.executionFrequencyMs
              ) : (
                <Absent />
              ),
          },
        ]}
        items={polls as EcuPoll[]}
        empty={
          <Box textAlign="center">
            No ECU poll entries in this campaign.
          </Box>
        }
      />
    </Container>
  );
}

// ── Telemetry panel ───────────────────────────────────────────────────────────

/**
 * A unique ECU entry, carrying the first-seen manifest's data for that ECU code.
 * Deduplication is by `ecu` code — if two manifests referencing the same
 * decoder manifest carry the same ECU code, keep the first occurrence.
 */
interface UniqueEcuEntry {
  readonly ecu: string;
  readonly displayName: string;
  readonly signalCount?: number;
  readonly baselineVersion?: string;
}

/**
 * Derive the ECU roster for a telemetry campaign.
 *
 * Filters manifests by `decoderManifestRef === decoderManifestId`, then
 * deduplicates by `ecu` code (first seen wins). The union across ALL manifests
 * is intentionally not taken — see module JSDoc for the measured reason.
 */
function resolveEcuRoster(
  decoderManifestId: string | undefined,
  modelManifests: readonly ModelManifestItem[],
): readonly UniqueEcuEntry[] {
  if (!decoderManifestId) return [];

  const seen = new Set<string>();
  const out: UniqueEcuEntry[] = [];

  for (const manifest of modelManifests) {
    if (manifest.decoderManifestRef !== decoderManifestId) continue;
    for (const ecu of manifest.ecus) {
      if (seen.has(ecu.ecu)) continue;
      seen.add(ecu.ecu);
      out.push({
        ecu: ecu.ecu,
        displayName: ecu.displayName,
        signalCount: ecu.signalCount,
        baselineVersion: ecu.baselineVersion,
      });
    }
  }

  return out;
}

function TelemetryCoveragePanel(props: {
  readonly decoderManifestId: string | undefined;
  readonly modelManifests: readonly ModelManifestItem[];
}): React.JSX.Element {
  const roster = resolveEcuRoster(
    props.decoderManifestId,
    props.modelManifests,
  );

  const manifestLabel = props.decoderManifestId ?? "—";

  /**
   * CRITICAL: this heading and caption describe the VEHICLE's ECU roster.
   * They MUST NOT claim that the campaign collects from these ECUs — no such
   * mapping exists. The phrase "available on vehicles running" is deliberate:
   * it names the vehicle configuration, not the campaign's data collection.
   *
   * The mutation guard in the test file checks that the non-attribution phrasing
   * is present and that attribution phrasing is absent. Do not change the
   * data-testid values; the tests key on them.
   */
  return (
    <Container
      header={
        <Header
          variant="h2"
          description={
            <span data-testid="coverage-panel-telemetry-caption">
              {/* The roster is the vehicle's hardware — not a signal attribution.
                  "available on vehicles running" states a vehicle property, not a
                  campaign data-collection claim. */}
              ECUs available on vehicles running{" "}
              <Box variant="code" display="inline" data-testid="coverage-panel-decoder-id">
                {manifestLabel}
              </Box>
              {". This is the vehicle\u2019s ECU roster \u2014 not a list of ECUs this campaign collects data from."}
            </span>
          }
        >
          <span data-testid="coverage-panel-telemetry-heading">
            ECU roster
          </span>
        </Header>
      }
      data-testid="coverage-panel-telemetry"
    >
      <Table
        data-testid="coverage-panel-telemetry-table"
        columnDefinitions={[
          {
            id: "ecu",
            header: "ECU",
            cell: (item: UniqueEcuEntry) => item.ecu,
          },
          {
            id: "displayName",
            header: "Display name",
            cell: (item: UniqueEcuEntry) => item.displayName,
          },
          {
            id: "signalCount",
            header: "Signal count",
            cell: (item: UniqueEcuEntry) =>
              item.signalCount !== undefined ? item.signalCount : <Absent />,
          },
          {
            id: "baselineVersion",
            header: "Baseline version",
            cell: (item: UniqueEcuEntry) =>
              item.baselineVersion !== undefined ? (
                item.baselineVersion
              ) : (
                <Absent />
              ),
          },
        ]}
        items={roster as UniqueEcuEntry[]}
        empty={
          <Box textAlign="center">
            No model manifests reference decoder manifest{" "}
            <Box variant="code" display="inline">
              {manifestLabel}
            </Box>
            .
          </Box>
        }
      />
    </Container>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

/**
 * CampaignCoveragePanel
 *
 * Mode is decided by `signalsToFetch` presence and non-emptiness:
 * - Present and non-empty → UDS mode ("ECUs polled")
 * - Absent or empty → telemetry mode ("ECU roster")
 */
const CampaignCoveragePanel: React.FC<{
  readonly signalsToFetch: readonly SignalToFetch[] | undefined;
  readonly signalCatalog: readonly SignalItem[];
  readonly decoderManifestId: string | undefined;
  readonly modelManifests: readonly ModelManifestItem[];
}> = (props) => {
  const isUds =
    props.signalsToFetch !== undefined && props.signalsToFetch.length > 0;

  return (
    <SpaceBetween size="l">
      {isUds ? (
        <UdsCoveragePanel
          signalsToFetch={props.signalsToFetch!}
          signalCatalog={props.signalCatalog}
        />
      ) : (
        <TelemetryCoveragePanel
          decoderManifestId={props.decoderManifestId}
          modelManifests={props.modelManifests}
        />
      )}
    </SpaceBetween>
  );
};

export default CampaignCoveragePanel;
