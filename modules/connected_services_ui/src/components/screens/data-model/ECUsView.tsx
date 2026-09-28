// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ECUsView — the catalog of Electronic Control Units.
 *
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend/tasks.md` T3.2
 *
 * Route: `/data-model/ecus` (in nav under Data Model).
 *
 * ## Data source
 *
 * ECUs are read from `ecus[]` on the `/model-manifests` response via the
 * shared `useVehicleModels` hook. They are de-duplicated by `ecu` code
 * (first-seen wins) across the 8 model manifests, reducing 65 raw entries
 * to a smaller unique set.
 *
 * ## Why no signal→ECU join
 *
 * The `source_ecu` field existed only in the fixture (`vehicleModelsData.ts`)
 * and was never present in the real `/signals` payload (302 signals, 18 field
 * keys, none ECU-related). No signal names an ECU and no ECU lists its signals,
 * so any such join would be fabricated data. See `docs/tech.md` §
 * "⚠ /signals carries NO ECU field".
 *
 * ## ECU `signalCount` is manifest-declared, not computed
 *
 * Present on 8 of 65 entries (CMS-FLEET-MODEL's ECUs only). The Meridian
 * manifests' ECU entries carry `ecu` + `displayName` only. This field is
 * labelled "Manifest-declared signals" and rendered absent (not 0) when
 * missing, because 0 would misrepresent a Meridian ECU as having no signals.
 *
 * ## ECU ids and Vehicle Models share the same payload
 *
 * ECU ids (`ecu` codes) come from `ecus[]` on this same `/model-manifests`
 * payload. Vehicle Models and ECUs cannot drift relative to each other because
 * they are derived from the same API response. There is no signal→ECU mapping
 * anywhere in this API; do NOT attempt that join.
 */

import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useMemo, useState } from "react";

import type { EcuEntry } from "../../../api/dataModelClient";
import KpiCard from "../../commons/KpiCard";
import KpiCardGrid from "../../commons/KpiCardGrid";
import { useVehicleModels } from "./useVehicleModels";

const SETTLE_MARKER = "cs-settle-ecus-catalog-table";

/**
 * De-duplicate `ecus[]` entries across all model manifests by `ecu` code.
 *
 * First-seen wins. 65 raw entries across 8 manifests → ≤ 65 unique rows
 * (fewer in practice because the same ECU code appears in multiple manifests).
 *
 * NOTE: This is derived data. Tests MUST verify that the same `ecu` code
 * appearing in two manifests yields ONE row, not two. Mutation check: changing
 * the de-dup key from `ecu` to any other field must cause the de-dup test to fail.
 */
export function deduplicateEcus(
  manifests: readonly import("./useVehicleModels").ModelManifestItem[],
): readonly EcuEntry[] {
  const seen = new Set<string>();
  const result: EcuEntry[] = [];
  for (const manifest of manifests) {
    for (const ecu of manifest.ecus) {
      if (!seen.has(ecu.ecu)) {
        seen.add(ecu.ecu);
        result.push(ecu);
      }
    }
  }
  return result;
}

// ── Component ─────────────────────────────────────────────────────────────────

const ECUsView: React.FC = () => {
  const { status, manifests, error } = useVehicleModels();
  const [filterText, setFilterText] = useState("");

  const ecus = useMemo(() => deduplicateEcus(manifests), [manifests]);

  const filtered = useMemo(() => {
    const q = filterText.trim().toLowerCase();
    if (!q) return ecus;
    return ecus.filter(
      (e) =>
        e.ecu.toLowerCase().includes(q) ||
        e.displayName.toLowerCase().includes(q),
    );
  }, [ecus, filterText]);

  // Count ECUs that have signalCount declared (8/65 per live verification).
  const withSignalCount = ecus.filter((e) => e.signalCount !== undefined).length;

  return (
    <SpaceBetween size="l">
      {/* Settle marker is always rendered so registryCompleteness test can find it. */}
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="ecus-catalog-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {/* ── Loading ──────────────────────────────────────────────────────── */}
      {status === "loading" && (
        <Box textAlign="center" padding={{ vertical: "xl" }} data-testid="ecus-loading">
          <Spinner size="large" />
        </Box>
      )}

      {/* ── Unconfigured ─────────────────────────────────────────────────── */}
      {status === "unavailable" && (
        <Alert
          type="info"
          header="Data processing API not configured"
          data-testid="ecus-unconfigured"
        >
          The data processing API endpoint is not configured for this stage. ECU catalog
          data is unavailable.
        </Alert>
      )}

      {/* ── Error ────────────────────────────────────────────────────────── */}
      {status === "error" && (
        <Alert
          type="error"
          header="Failed to load ECU catalog"
          data-testid="ecus-error"
        >
          {error ?? "An unexpected error occurred loading ECU data."}
        </Alert>
      )}

      {/* ── Ready ────────────────────────────────────────────────────────── */}
      {status === "ready" && (
        <>
      <KpiCardGrid>
        <KpiCard
          label="ECUs in catalog"
          value={String(ecus.length)}
          color="text-status-info"
          captions={["Unique ECU codes across all vehicle models (de-duplicated)"]}
        />
        <KpiCard
          label="With declared signal count"
          value={String(withSignalCount)}
          color="text-status-info"
          captions={["ECUs whose manifest declares a signal count"]}
        />
        <KpiCard
          label="Vehicle models"
          value={String(manifests.length)}
          color="text-status-info"
          captions={["Model manifests contributing ECU entries"]}
        />
      </KpiCardGrid>

      <Table
        variant="container"
        data-testid="ecus-catalog-table"
        header={
          <Header
            counter={`(${filtered.length})`}
            description={
              "Electronic Control Units drawn from ecus[] on the model manifests. " +
              "De-duplicated by ECU code across all manifests. " +
              "\u201cManifest-declared signals\u201d is a stored count present on a subset of ECUs " +
              "\u2014 no signal\u2192ECU mapping exists in this API."
            }
          >
            ECUs
          </Header>
        }
        filter={
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Filter by ECU code or display name"
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />
        }
        empty={
          filterText ? (
            <Box textAlign="center" padding={{ vertical: "l" }} data-testid="ecus-no-match">
              <b>No ECUs match the filter.</b>
            </Box>
          ) : (
            <Box textAlign="center" padding={{ vertical: "l" }} data-testid="ecus-empty">
              <b>No ECU entries found in the model manifests.</b>
            </Box>
          )
        }
        columnDefinitions={[
          {
            id: "ecu",
            header: "ECU code",
            cell: (row: EcuEntry) => <code>{row.ecu}</code>,
          },
          {
            id: "displayName",
            header: "Display name",
            cell: (row: EcuEntry) => row.displayName,
          },
          {
            id: "signalCount",
            header: "Manifest-declared signals",
            cell: (row: EcuEntry) =>
              row.signalCount !== undefined ? String(row.signalCount) : <em>—</em>,
          },
          {
            id: "baselineVersion",
            header: "Baseline version",
            cell: (row: EcuEntry) =>
              row.baselineVersion !== undefined ? row.baselineVersion : <em>—</em>,
          },
        ]}
        items={filtered}
      />
        </>
      )}
    </SpaceBetween>
  );
};

export default ECUsView;
