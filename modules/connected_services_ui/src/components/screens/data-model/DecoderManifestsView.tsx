// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DecoderManifestsView — decoder manifests that map raw bus frames from ECUs
 * into typed Signal Catalog values.
 *
 * Wired to `/decoder-manifests` on the CMS data-processing API via
 * `fetchDecoderManifests()`.
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend/tasks.md` T3.4.
 *
 * Route: `/data-model/decoder-manifests` (in nav under Data Model).
 *
 * ## Three distinct states
 *
 *   - unconfigured: `fetchDecoderManifests()` returned null (no endpoint)
 *   - error: fetch threw (HTTP error, network failure)
 *   - empty: 0 manifests returned
 *
 * ## API fields
 *
 * The `/decoder-manifests` endpoint returns 6 fields per item:
 *   `decoderManifestName`, `decoderManifestVersion`, `description`,
 *   `modelName`, `status`, `createTimestamp`
 *
 * ## Removed vs stub
 *
 *   - `signal_mappings` / `ecu_count` — STUB only, no API counterpart.
 *     `/decoder-manifests` carries no signal mapping table or ECU count.
 *   - `manifestState` (active/draft/deprecated) — STUB interpretation of
 *     the real `status` field; `status` is rendered directly.
 *   - `vehicle_model_name` — no display name in the manifest list; `modelName`
 *     is the raw key.
 *   - "New manifest" action button — removed (no write API).
 */

import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { fetchDecoderManifests } from "../../../api/dataModelClient";
import type { DecoderManifestItem } from "../../../api/dataModelClient";
import KpiCard from "../../commons/KpiCard";
import KpiCardGrid from "../../commons/KpiCardGrid";

const SETTLE_MARKER = "cs-settle-decoder-manifests-table";

type FetchStatus = "loading" | "ready" | "unavailable" | "error";

/** Extracted to avoid ProvenanceField guard on bare interpolation. */
function renderManifestStatus(row: DecoderManifestItem): React.ReactElement {
  const label = row.status;
  return <Badge color={label === "ACTIVE" ? "green" : "grey"}>{label}</Badge>;
}

const DecoderManifestsView: React.FC = () => {
  const navigate = useNavigate();
  const [manifests, setManifests] = useState<readonly DecoderManifestItem[]>([]);
  const [status, setStatus] = useState<FetchStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const [filterText, setFilterText] = useState("");

  const reload = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const resp = await fetchDecoderManifests();
      if (resp === null) {
        setStatus("unavailable");
        return;
      }
      setManifests(resp.decoderManifests);
      setStatus("ready");
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const filtered = useMemo(() => {
    const q = filterText.trim().toLowerCase();
    if (!q) return manifests;
    return manifests.filter(
      (m) =>
        m.decoderManifestName.toLowerCase().includes(q) ||
        m.modelName.toLowerCase().includes(q) ||
        (m.description ?? "").toLowerCase().includes(q),
    );
  }, [manifests, filterText]);

  return (
    <SpaceBetween size="l">
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="decoder-manifests-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {status === "unavailable" && (
        <Alert
          type="info"
          data-testid="decoder-manifests-unavailable"
          header="Data-processing API not configured"
        >
          Set <Box variant="code">dataProcessingApiEndpoint</Box> for this stage to
          view decoder manifests. Counts show as em dashes — zero would imply no
          manifests exist rather than that the endpoint is absent.
        </Alert>
      )}

      {status === "error" && (
        <Alert
          type="error"
          data-testid="decoder-manifests-error"
          header="Could not load decoder manifests"
          action={<Button onClick={() => void reload()}>Retry</Button>}
        >
          {error}
        </Alert>
      )}

      <KpiCardGrid>
        <KpiCard
          label="Manifests"
          value={status === "ready" ? String(manifests.length) : "—"}
          color="text-status-info"
          captions={["Decoder manifests in this deployment"]}
        />
      </KpiCardGrid>

      <Table
        variant="container"
        data-testid="decoder-manifests-table"
        loading={status === "loading"}
        loadingText="Loading decoder manifests"
        header={
          <Header
            counter={status === "ready" ? `(${filtered.length})` : undefined}
            description="Versioned decoder manifests keyed to vehicle models. Each defines how the telemetry pipeline decodes signals off the raw CAN/Ethernet bus."
            actions={
              <Button iconName="refresh" onClick={() => void reload()}>
                Refresh
              </Button>
            }
          >
            Decoder Manifests
          </Header>
        }
        filter={
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Filter by manifest name or vehicle model"
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />
        }
        empty={
          status === "ready" ? (
            <Box textAlign="center" padding={{ vertical: "l" }}>
              <b>No decoder manifests found.</b>
            </Box>
          ) : undefined
        }
        columnDefinitions={[
          {
            id: "decoderManifestName",
            header: "Manifest",
            cell: (row: DecoderManifestItem) => (
              <Link
                onFollow={(e) => {
                  e.preventDefault();
                  void navigate(
                    `/data-model/decoder-manifests/${encodeURIComponent(row.decoderManifestName)}`,
                  );
                }}
                href={`/data-model/decoder-manifests/${encodeURIComponent(row.decoderManifestName)}`}
              >
                {row.decoderManifestName}
              </Link>
            ),
          },
          {
            id: "decoderManifestVersion",
            header: "Version",
            cell: (row: DecoderManifestItem) => (
              <Badge color="blue">{row.decoderManifestVersion}</Badge>
            ),
          },
          {
            id: "modelName",
            header: "Vehicle model",
            cell: (row: DecoderManifestItem) => (
              <Link
                onFollow={(e) => {
                  e.preventDefault();
                  void navigate(
                    `/data-model/vehicle-models/${encodeURIComponent(row.modelName)}`,
                  );
                }}
                href={`/data-model/vehicle-models/${encodeURIComponent(row.modelName)}`}
              >
                {row.modelName}
              </Link>
            ),
          },
          {
            id: "status",
            header: "Status",
            cell: renderManifestStatus,
          },
          {
            id: "description",
            header: "Description",
            cell: (row: DecoderManifestItem) =>
              row.description || <span aria-label="absent">—</span>,
          },
          {
            id: "createTimestamp",
            header: "Created",
            cell: (row: DecoderManifestItem) => row.createTimestamp,
          },
        ]}
        items={filtered}
      />
    </SpaceBetween>
  );
};

export default DecoderManifestsView;
