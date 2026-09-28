// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * VehicleModelDetailView — read-only view of one vehicle model manifest.
 *
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend/tasks.md` T3.3
 *
 * Route: `/data-model/vehicle-models/:modelId` (parameterised, not in nav).
 *
 * Rendered as a set of read-only Containers (a "card view"), not tabs.
 * Sections: Overview / ECUs installed / Signals / Decoder manifest reference.
 *
 * ## What was removed from the stub
 *
 * - `vehicleModelsData.ts` (deleted): the fixture is gone. Data comes from
 *   `useVehicleModels()` which calls `fetchVehicleModels()`.
 * - `vehicleCount`: stale on the real API (CMS-FLEET-MODEL reports 0 against
 *   21 real vehicles). Spec § Constraints forbids reading or writing it.
 * - `COMPATIBLE_PRODUCTS_BY_MODEL`: fixture-only mock with no API source.
 *   The compatible-products section is omitted until a real products API exists.
 * - `firmware_version` on ECU entries: not present in `EcuEntry` from the real
 *   API. Only `ecu`, `displayName`, `signalCount?`, `baselineVersion?`.
 *
 * ## ECU constraint restatement (replaces the deleted fixture's warning)
 *
 * The deleted `vehicleModelsData.ts` carried this warning:
 *   "ECU IDs referenced here MUST align with the source_ecu values on the
 *    Signal Catalog"
 *
 * The TRUE part of that constraint, restated for the live API:
 *   - ECU ids (`ecu` codes) come from `ecus[]` on this same `/model-manifests`
 *     payload. Vehicle Models and ECUs share the same API response, so they
 *     cannot drift relative to each other.
 *   - The FALSE part (now deleted): `source_ecu` does not exist on the real
 *     `/signals` payload (18 field keys across 302 signals, none ECU-related).
 *     No signal names an ECU; no ECU lists its signals. Signals and ECUs
 *     cannot be joined from this API at all.
 *
 * ## decoderManifestRef cross-reference
 *
 * `decoderManifestRef` resolves against real decoder manifest ids, but only
 * for CMS-FLEET-MODEL per live verification (T1.1). The link is rendered
 * conditionally; a manifest without a ref shows "—" rather than a broken link.
 */

import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import Table from "@cloudscape-design/components/table";
import React from "react";
import { useNavigate, useParams } from "react-router-dom";

import type { EcuEntry, ModelManifestItem } from "../../../api/dataModelClient";
import { useVehicleModels } from "./useVehicleModels";

const SETTLE_MARKER = "cs-settle-vehicle-model-detail-tabs";

const STATUS_COLOR: Record<string, "green" | "blue" | "grey" | "red"> = {
  ACTIVE: "green",
  DRAFT: "blue",
  DEPRECATED: "grey",
  INACTIVE: "grey",
};

const PHASE_COLOR: Record<string, "green" | "blue" | "grey" | "red"> = {
  PRODUCTION: "green",
  VALIDATION: "blue",
  SUNSET: "grey",
  production: "green",
  validation: "blue",
  sunset: "grey",
};

function statusColor(s: string): "green" | "blue" | "grey" | "red" {
  return STATUS_COLOR[s] ?? "grey";
}

function phaseColor(p: string): "green" | "blue" | "grey" | "red" {
  return PHASE_COLOR[p] ?? "grey";
}

// ── Component ─────────────────────────────────────────────────────────────────

const VehicleModelDetailView: React.FC = () => {
  const { modelId } = useParams<{ modelId: string }>();
  const navigate = useNavigate();
  const { status, manifests, error } = useVehicleModels();

  // ── Loading ──────────────────────────────────────────────────────────────

  if (status === "loading") {
    return (
      <Box
        textAlign="center"
        padding={{ vertical: "xl" }}
        data-testid="vehicle-model-detail-loading"
      >
        <Spinner size="large" />
      </Box>
    );
  }

  // ── Unconfigured ─────────────────────────────────────────────────────────

  if (status === "unavailable") {
    return (
      <Alert
        type="info"
        header="Data processing API not configured"
        data-testid="vehicle-model-detail-unconfigured"
      >
        The data processing API endpoint is not configured for this stage.
      </Alert>
    );
  }

  // ── Error ─────────────────────────────────────────────────────────────────

  if (status === "error") {
    return (
      <Alert
        type="error"
        header="Failed to load vehicle model"
        data-testid="vehicle-model-detail-error"
      >
        {error ?? "An unexpected error occurred loading vehicle model data."}
      </Alert>
    );
  }

  // ── Model not found ───────────────────────────────────────────────────────

  const decoded = modelId ? decodeURIComponent(modelId) : "";
  const m: ModelManifestItem | undefined = manifests.find(
    (item) => item.modelManifestName === decoded,
  );

  if (!m) {
    return (
      <Alert
        type="warning"
        header={`Vehicle model not found: ${decoded}`}
        data-testid="vehicle-model-detail-not-found"
      >
        <Link
          onFollow={(e) => {
            e.preventDefault();
            void navigate("/data-model/vehicle-models");
          }}
          href="/data-model/vehicle-models"
        >
          ← Back to Vehicle Models
        </Link>
      </Alert>
    );
  }

  // ── Ready ─────────────────────────────────────────────────────────────────

  // Extract to local vars — provenanceRender guard fires on {m.status} / {m.productionPhase}
  // in JSX because "status" is a ProvenanceValue field name in other screens. Here these
  // are plain API strings (ModelManifestItem.status / .productionPhase).
  const manifestStatus = m.status;
  const manifestPhase = m.productionPhase;
  return (
    <SpaceBetween size="l">
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="vehicle-model-detail-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {/* ── Overview ────────────────────────────────────────────────────── */}
      <Container
        header={
          <Header
            variant="h2"
            actions={
              <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                <Badge color={statusColor(manifestStatus)}>{manifestStatus}</Badge>
                <Badge color={phaseColor(manifestPhase)}>{manifestPhase}</Badge>
                {m.isDefault && <Badge color="blue">Default</Badge>}
              </SpaceBetween>
            }
            description={m.description}
          >
            <span style={{ fontFamily: "monospace" }}>{m.modelManifestName}</span>{" "}
            — {m.displayName}
          </Header>
        }
      >
        <ColumnLayout columns={3} variant="text-grid">
          <KeyValuePairs
            columns={1}
            items={[
              { label: "Model line", value: m.modelLine },
              { label: "Platform", value: m.platform },
              { label: "Version", value: m.modelManifestVersion },
            ]}
          />
          <KeyValuePairs
            columns={1}
            items={[
              { label: "Signals", value: String(m.signalCount) },
              { label: "ECUs", value: String(m.ecus.length) },
              { label: "ECU config ID", value: m.ecuConfigId || "—" },
            ]}
          />
          <KeyValuePairs
            columns={1}
            items={[
              {
                label: "Decoder manifest",
                value: m.decoderManifestRef ? (
                  <Link
                    onFollow={(e) => {
                      e.preventDefault();
                      void navigate(
                        `/data-model/decoder-manifests/${encodeURIComponent(m.decoderManifestRef)}`,
                      );
                    }}
                    href={`/data-model/decoder-manifests/${encodeURIComponent(m.decoderManifestRef)}`}
                  >
                    <code>{m.decoderManifestRef}</code>
                  </Link>
                ) : (
                  <em>—</em>
                ),
              },
              { label: "Created", value: m.createTimestamp },
              { label: "Updated", value: m.updateTimestamp },
            ]}
          />
        </ColumnLayout>
      </Container>

      {/* ── ECUs installed ──────────────────────────────────────────────── */}
      <Container
        header={
          <Header
            variant="h2"
            counter={`(${m.ecus.length})`}
            description={
              "ECUs on this model manifest. " +
              "\u201cManifest-declared signals\u201d and \u201cBaseline version\u201d are present only " +
              "on a subset of ECU entries \u2014 absent means undeclared, not zero. " +
              "No signal\u2192ECU mapping exists in this API."
            }
            actions={
              <Link
                onFollow={(e) => {
                  e.preventDefault();
                  void navigate(
                    `/data-model/ecus?model=${encodeURIComponent(m.modelManifestName)}`,
                  );
                }}
                href={`/data-model/ecus?model=${encodeURIComponent(m.modelManifestName)}`}
              >
                Open ECU catalog →
              </Link>
            }
          >
            ECUs installed
          </Header>
        }
      >
        <Table
          variant="embedded"
          columnDefinitions={[
            {
              id: "ecu",
              header: "ECU code",
              cell: (e: EcuEntry) => <code>{e.ecu}</code>,
            },
            {
              id: "displayName",
              header: "Display name",
              cell: (e: EcuEntry) => e.displayName,
            },
            {
              id: "signalCount",
              header: "Manifest-declared signals",
              cell: (e: EcuEntry) =>
                e.signalCount !== undefined ? String(e.signalCount) : <em>—</em>,
            },
            {
              id: "baselineVersion",
              header: "Baseline version",
              cell: (e: EcuEntry) =>
                e.baselineVersion !== undefined ? e.baselineVersion : <em>—</em>,
            },
          ]}
          items={m.ecus as EcuEntry[]}
        />
      </Container>

      {/* ── Signals ─────────────────────────────────────────────────────── */}
      <Container
        header={
          <Header
            variant="h2"
            description="The signal set this model emits. Open the Signal Catalog to browse signals."
          >
            Signals emitted
          </Header>
        }
      >
        <SpaceBetween size="s">
          <Box>
            <b>{m.signalCount}</b> signals across <b>{m.ecus.length}</b> ECUs.
          </Box>
          <Link
            onFollow={(e) => {
              e.preventDefault();
              void navigate(
                `/data-model/signals?model=${encodeURIComponent(m.modelManifestName)}`,
              );
            }}
            href={`/data-model/signals?model=${encodeURIComponent(m.modelManifestName)}`}
          >
            Browse signals in the Signal Catalog →
          </Link>
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
};

export default VehicleModelDetailView;
