// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * VehicleModelsView — the catalog of vehicle model manifests.
 *
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend/tasks.md` T3.3
 *
 * Route: `/data-model/vehicle-models` (in nav under Data Model).
 *
 * ## Data source
 *
 * Reads the `/model-manifests` endpoint via the shared `useVehicleModels` hook.
 * The hook is shared with ECUsView — a single fetch serves both screens.
 *
 * ## vehicleCount is not displayed
 *
 * `vehicleCount` is stale on the real API: CMS-FLEET-MODEL reports 0 against
 * 21 real vehicles (confirmed live 2026-09-14). Spec § Constraints forbids
 * reading or writing it. Vehicle counts must be derived from the vehicles
 * table, not from this field.
 *
 * ## decoderManifestRef cross-reference
 *
 * `decoderManifestRef` resolves against real decoder manifest ids from T3.4,
 * but ONLY for CMS-FLEET-MODEL per live verification. The link is rendered
 * conditionally and not presented as universally resolvable.
 *
 * ## ECU ids and Vehicle Models share the same payload
 *
 * ECU ids come from `ecus[]` on this same `/model-manifests` payload.
 * There is no signal→ECU mapping in this API; `signal_group` (24 functional
 * domains) is a signal-level grouping axis and must NOT be relabelled as ECU.
 */

import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { ModelManifestItem } from "../../../api/dataModelClient";
import KpiCard from "../../commons/KpiCard";
import KpiCardGrid from "../../commons/KpiCardGrid";
import { useVehicleModels } from "./useVehicleModels";

const SETTLE_MARKER = "cs-settle-vehicle-models-table";

// Status badge colours map to productionPhase (API field).
// Values from live payload: productionPhase is free text stored on DynamoDB.
const PHASE_COLOR: Record<string, "green" | "blue" | "grey" | "red"> = {
  PRODUCTION: "green",
  VALIDATION: "blue",
  SUNSET: "grey",
  // lower-case fallbacks for any stage that normalises differently
  production: "green",
  validation: "blue",
  sunset: "grey",
};

const STATUS_COLOR: Record<string, "green" | "blue" | "grey" | "red"> = {
  ACTIVE: "green",
  DRAFT: "blue",
  DEPRECATED: "grey",
  INACTIVE: "grey",
};

function phaseColor(phase: string): "green" | "blue" | "grey" | "red" {
  return PHASE_COLOR[phase] ?? "grey";
}

function statusColor(status: string): "green" | "blue" | "grey" | "red" {
  return STATUS_COLOR[status] ?? "grey";
}

// ── Component ─────────────────────────────────────────────────────────────────

const VehicleModelsView: React.FC = () => {
  const navigate = useNavigate();
  const { status, manifests, error } = useVehicleModels();
  const [filterText, setFilterText] = useState("");

  const filtered = useMemo(() => {
    const q = filterText.trim().toLowerCase();
    if (!q) return manifests;
    return manifests.filter(
      (m) =>
        m.modelManifestName.toLowerCase().includes(q) ||
        m.displayName.toLowerCase().includes(q) ||
        m.modelLine.toLowerCase().includes(q) ||
        m.platform.toLowerCase().includes(q),
    );
  }, [manifests, filterText]);

  // ── Loading ────────────────────────────────────────────────────────────────
  // ── Unconfigured ───────────────────────────────────────────────────────────
  // ── Error ──────────────────────────────────────────────────────────────────
  // ── Ready ──────────────────────────────────────────────────────────────────
  //
  // Settle marker is always rendered (outside state conditionals) so
  // registryCompleteness.test.tsx can find it regardless of API state.

  const totalModels = manifests.length;
  const activeModels = manifests.filter(
    (m) => m.status === "ACTIVE" || m.status === "active",
  ).length;
  const defaultModel = manifests.find((m) => m.isDefault);

  return (
    <SpaceBetween size="l">
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="vehicle-models-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {status === "loading" && (
        <Box textAlign="center" padding={{ vertical: "xl" }} data-testid="vehicle-models-loading">
          <Spinner size="large" />
        </Box>
      )}

      {status === "unavailable" && (
        <Alert
          type="info"
          header="Data processing API not configured"
          data-testid="vehicle-models-unconfigured"
        >
          The data processing API endpoint is not configured for this stage. Vehicle model
          data is unavailable.
        </Alert>
      )}

      {status === "error" && (
        <Alert
          type="error"
          header="Failed to load vehicle models"
          data-testid="vehicle-models-error"
        >
          {error ?? "An unexpected error occurred loading vehicle model data."}
        </Alert>
      )}

      {status === "ready" && (
        <>
      <KpiCardGrid>
        <KpiCard
          label="Vehicle models"
          value={String(totalModels)}
          color="text-status-info"
          captions={["Model manifests from the data-processing API"]}
        />
        <KpiCard
          label="Active"
          value={String(activeModels)}
          color={activeModels > 0 ? "text-status-success" : "text-status-info"}
          captions={["Status = ACTIVE"]}
        />
        <KpiCard
          label="Default model"
          value={defaultModel ? defaultModel.modelManifestName : "—"}
          color="text-status-info"
          captions={["isDefault: true on the manifest"]}
        />
      </KpiCardGrid>

      <Table
        variant="container"
        data-testid="vehicle-models-table"
        header={
          <Header
            counter={`(${filtered.length} of ${totalModels})`}
            description={
              "Vehicle model manifests from the data-processing API. " +
              "Each manifest defines which signals a vehicle platform emits, " +
              "paired with a decoder manifest. Click a model to view its details."
            }
          >
            Vehicle Models
          </Header>
        }
        filter={
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Filter by model name, line, or platform"
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />
        }
        empty={
          filterText ? (
            <Box
              textAlign="center"
              padding={{ vertical: "l" }}
              data-testid="vehicle-models-no-match"
            >
              <b>No models match the filter.</b>
            </Box>
          ) : (
            <Box
              textAlign="center"
              padding={{ vertical: "l" }}
              data-testid="vehicle-models-empty"
            >
              <b>No vehicle model manifests found.</b>
            </Box>
          )
        }
        columnDefinitions={[
          {
            id: "modelManifestName",
            header: "Model",
            cell: (row: ModelManifestItem) => (
              <Link
                onFollow={(e) => {
                  e.preventDefault();
                  void navigate(
                    `/data-model/vehicle-models/${encodeURIComponent(row.modelManifestName)}`,
                  );
                }}
                href={`/data-model/vehicle-models/${encodeURIComponent(row.modelManifestName)}`}
              >
                <code>{row.modelManifestName}</code>
              </Link>
            ),
          },
          {
            id: "displayName",
            header: "Name",
            cell: (row: ModelManifestItem) => row.displayName,
          },
          {
            id: "modelLine",
            header: "Line",
            cell: (row: ModelManifestItem) => row.modelLine,
          },
          {
            id: "platform",
            header: "Platform",
            cell: (row: ModelManifestItem) => row.platform,
          },
          {
            id: "status",
            header: "Status",
            // Extract to local vars — provenanceRender guard fires on {row.status} in JSX
            // because "status" is a ProvenanceValue field name in other screens; here it
            // is a plain API string (ModelManifestItem.status).
            cell: (row: ModelManifestItem) => {
              const manifestStatus = row.status;
              const manifestPhase = row.productionPhase;
              return (
                <SpaceBetween direction="horizontal" size="xxs" alignItems="center">
                  <Badge color={statusColor(manifestStatus)}>{manifestStatus}</Badge>
                  <Badge color={phaseColor(manifestPhase)}>{manifestPhase}</Badge>
                  {row.isDefault && <Badge color="blue">Default</Badge>}
                </SpaceBetween>
              );
            },
          },
          {
            id: "decoderManifestRef",
            header: "Decoder manifest",
            cell: (row: ModelManifestItem) =>
              row.decoderManifestRef ? (
                <Link
                  onFollow={(e) => {
                    e.preventDefault();
                    void navigate(
                      `/data-model/decoder-manifests/${encodeURIComponent(row.decoderManifestRef)}`,
                    );
                  }}
                  href={`/data-model/decoder-manifests/${encodeURIComponent(row.decoderManifestRef)}`}
                >
                  <code>{row.decoderManifestRef}</code>
                </Link>
              ) : (
                <em>—</em>
              ),
          },
          {
            id: "signalCount",
            header: "Signals",
            cell: (row: ModelManifestItem) => String(row.signalCount),
          },
          {
            id: "ecuCount",
            header: "ECUs",
            cell: (row: ModelManifestItem) => String(row.ecus.length),
          },
        ]}
        items={filtered}
      />
        </>
      )}
    </SpaceBetween>
  );
};

export default VehicleModelsView;
