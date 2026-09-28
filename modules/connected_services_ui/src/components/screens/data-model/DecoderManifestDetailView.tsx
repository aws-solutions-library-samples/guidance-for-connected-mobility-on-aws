// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DecoderManifestDetailView — one decoder manifest's overview.
 *
 * Wired to `/decoder-manifests` on the CMS data-processing API via
 * `fetchDecoderManifests()`.
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend/tasks.md` T3.4.
 *
 * Route: `/data-model/decoder-manifests/:manifestId` (parameterised, not in nav).
 *
 * ## What was removed
 *
 *   - `STUB_MAPPINGS` (Signal mappings tab) — the `/decoder-manifests` endpoint
 *     returns exactly 6 fields per item. There is no signal→CAN mapping table in
 *     this API. STUB_MAPPINGS had no API source and was fabricated. The tab is
 *     removed rather than shown empty or with invented data.
 *
 *   - `STUB_USING` (Vehicle models tab) — no "which vehicle models use this
 *     manifest" lookup exists in the `/decoder-manifests` response. The
 *     `decoderManifestRef` cross-reference on `/model-manifests` resolves only
 *     for `CMS-FLEET-MODEL` (not universally). STUB_USING had no API source and
 *     was fabricated. The tab is removed rather than shown empty or with invented
 *     data.
 *
 *   - `published_by`, `signal_count`, `ecu_count` — stub-only fields with no
 *     counterpart on the 6-field API response.
 *
 * ## Three distinct states
 *
 *   - unconfigured: `fetchDecoderManifests()` returned null (no endpoint)
 *   - error: fetch threw (HTTP error, network failure)
 *   - not_found: no manifest matched the URL param
 *
 * ## API fields rendered
 *
 *   `decoderManifestName`, `decoderManifestVersion`, `description`,
 *   `modelName`, `status`, `createTimestamp`
 */

import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import React, { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { fetchDecoderManifests } from "../../../api/dataModelClient";
import type { DecoderManifestItem } from "../../../api/dataModelClient";

const SETTLE_MARKER = "cs-settle-decoder-manifest-detail-tabs";

type FetchStatus = "loading" | "ready" | "unavailable" | "error" | "not_found";

/** Extracted to avoid ProvenanceField guard on bare interpolation. */
function renderDecoderManifestStatus(m: DecoderManifestItem): React.ReactElement {
  const label = m.status;
  return (
    <Badge color={label === "ACTIVE" ? "green" : "grey"}>{label}</Badge>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────

const DecoderManifestDetailView: React.FC = () => {
  const { manifestId } = useParams<{ manifestId: string }>();
  const navigate = useNavigate();
  const [manifest, setManifest] = useState<DecoderManifestItem | null>(null);
  const [status, setStatus] = useState<FetchStatus>("loading");
  const [error, setError] = useState<string | null>(null);

  const decodedId = manifestId ? decodeURIComponent(manifestId) : undefined;

  const reload = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const resp = await fetchDecoderManifests();
      if (resp === null) {
        setStatus("unavailable");
        return;
      }
      const found =
        resp.decoderManifests.find(
          (m) => m.decoderManifestName === decodedId,
        ) ?? null;
      setManifest(found);
      setStatus(found ? "ready" : "not_found");
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [decodedId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <SpaceBetween size="l">
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="decoder-manifest-detail-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {status === "unavailable" && (
        <Alert
          type="info"
          data-testid="decoder-manifest-detail-unavailable"
          header="Data-processing API not configured"
        >
          Set <Box variant="code">dataProcessingApiEndpoint</Box> for this stage to
          view decoder manifest details.
        </Alert>
      )}

      {status === "error" && (
        <Alert
          type="error"
          data-testid="decoder-manifest-detail-error"
          header="Could not load decoder manifests"
          action={<Button onClick={() => void reload()}>Retry</Button>}
        >
          {error}
        </Alert>
      )}

      {status === "not_found" && (
        <Alert
          type="warning"
          data-testid="decoder-manifest-detail-not-found"
          header="Decoder manifest not found"
        >
          No decoder manifest with name <code>{decodedId}</code> was found.
        </Alert>
      )}

      {status === "loading" && (
        <Box textAlign="center" padding={{ vertical: "l" }}>
          Loading decoder manifest…
        </Box>
      )}

      {status === "ready" && manifest !== null && (
        <Container header={<Header variant="h2">Manifest details</Header>}>
          <ColumnLayout columns={2} variant="text-grid">
            <KeyValuePairs
              columns={1}
              items={[
                { label: "Manifest name", value: manifest.decoderManifestName },
                {
                  label: "Version",
                  value: (
                    <Badge color="blue">{manifest.decoderManifestVersion}</Badge>
                  ),
                },
                {
                  label: "Status",
                  value: renderDecoderManifestStatus(manifest),
                },
                {
                  label: "Description",
                  value:
                    manifest.description ||
                    <span aria-label="absent">—</span>,
                },
              ]}
            />
            <KeyValuePairs
              columns={1}
              items={[
                {
                  label: "Vehicle model",
                  value: (
                    <Link
                      onFollow={(e) => {
                        e.preventDefault();
                        void navigate(
                          `/data-model/vehicle-models/${encodeURIComponent(manifest.modelName)}`,
                        );
                      }}
                      href={`/data-model/vehicle-models/${encodeURIComponent(manifest.modelName)}`}
                    >
                      {manifest.modelName}
                    </Link>
                  ),
                },
                { label: "Created", value: manifest.createTimestamp },
              ]}
            />
          </ColumnLayout>
        </Container>
      )}
    </SpaceBetween>
  );
};

export default DecoderManifestDetailView;
