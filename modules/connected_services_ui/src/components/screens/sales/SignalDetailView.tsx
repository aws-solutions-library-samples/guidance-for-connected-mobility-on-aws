// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SignalDetailView — one signal's spec.
 *
 * Wired to `/signals` on the CMS data-processing API via `fetchSignals()`.
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend/tasks.md` T3.1.
 *
 * Route: `/data-model/signals/:signalId` (parameterised, not in nav — reached
 *        by clicking a row in SignalCatalogView).
 *
 * ## What was removed
 *
 *   - `in_products` / "In products" tab — has NO API counterpart on /signals.
 *     The full signal payload (302 items) carries no field linking signals to
 *     data products. Showing this tab would require fabricating the linkage.
 *     Removed entirely, not replaced with an empty state.
 *   - `source_ecu` — fixture-only field; absent from the live API.
 *   - `sample_rate_hz`, `precision`, `authored_by`, `first_seen`,
 *     `source_system` — these were StubSignalDetail fields with no API
 *     counterpart on SignalItem. Removed.
 *
 * ## Sparse fields
 *
 *   `can_id` (65/302), `cycle_ms` (204/302), `unit` (280/302), `description`
 *   (31/302), `min_value`/`max_value` (262/302) — absent renders as "—" (em
 *   dash), NOT as "0" or blank. An absent field and a present zero are
 *   semantically different; conflating them produces wrong min/max bounds.
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Alert from "@cloudscape-design/components/alert";
import Button from "@cloudscape-design/components/button";
import React, { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { fetchSignals } from "../../../api/dataModelClient";
import type { SignalItem } from "../../../api/dataModelClient";

const SETTLE_MARKER = "cs-settle-signal-detail-tabs";

type FetchStatus = "loading" | "ready" | "unavailable" | "error" | "not_found";

/** Render a potentially-absent value as em dash rather than zero or blank. */
function absent(v: string | number | undefined): React.ReactNode {
  if (v === undefined || v === null) return <span aria-label="absent">—</span>;
  return String(v);
}

/** Extracted to avoid ProvenanceField guard on bare interpolation. */
function renderSignalStatusBadge(sig: SignalItem): React.ReactElement {
  const label = sig.status;
  return <Badge color={label === "ACTIVE" ? "green" : "grey"}>{label}</Badge>;
}

// ── Component ─────────────────────────────────────────────────────────────────

const SignalDetailView: React.FC = () => {
  const { signalId } = useParams<{ signalId: string }>();
  const [signal, setSignal] = useState<SignalItem | null>(null);
  const [status, setStatus] = useState<FetchStatus>("loading");
  const [error, setError] = useState<string | null>(null);

  const decodedId = signalId ? decodeURIComponent(signalId) : undefined;

  const reload = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const resp = await fetchSignals();
      if (resp === null) {
        setStatus("unavailable");
        return;
      }
      const found = resp.signals.find(
        (s) => s.signal_id === decodedId || s.vss_path === decodedId,
      ) ?? null;
      setSignal(found);
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
        data-testid="signal-detail-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {status === "unavailable" && (
        <Alert type="info" data-testid="signal-detail-unavailable" header="Data-processing API not configured">
          Set <Box variant="code">dataProcessingApiEndpoint</Box> for this stage to view
          signal details.
        </Alert>
      )}

      {status === "error" && (
        <Alert
          type="error"
          data-testid="signal-detail-error"
          header="Could not load signal catalog"
          action={<Button onClick={() => void reload()}>Retry</Button>}
        >
          {error}
        </Alert>
      )}

      {status === "not_found" && (
        <Alert type="warning" data-testid="signal-detail-not-found" header="Signal not found">
          No signal with ID or path <code>{decodedId}</code> was found in the catalog.
        </Alert>
      )}

      {status === "loading" && (
        <Box textAlign="center" padding={{ vertical: "l" }}>
          Loading signal…
        </Box>
      )}

      {status === "ready" && signal !== null && (
        <Container header={<Header variant="h2">Signal specification</Header>}>
          <ColumnLayout columns={2} variant="text-grid">
            <KeyValuePairs
              columns={1}
              items={[
                { label: "Signal ID", value: signal.signal_id },
                { label: "Signal name", value: signal.signal_name },
                { label: "VSS path", value: <code>{signal.vss_path}</code> },
                {
                  label: "Description",
                  value: absent(signal.description),
                },
                {
                  label: "Group",
                  value: <Badge color="blue">{signal.signal_group}</Badge>,
                },
              ]}
            />
            <KeyValuePairs
              columns={1}
              items={[
                {
                  label: "Data type",
                  value: <Badge color="grey">{signal.data_type}</Badge>,
                },
                {
                  label: "Unit",
                  // sparse: 280/302 — absent is not the same as unitless
                  value: absent(signal.unit),
                },
                {
                  label: "Min value",
                  // sparse: 262/302 — absent ≠ zero
                  value: absent(signal.min_value),
                },
                {
                  label: "Max value",
                  // sparse: 262/302 — absent ≠ zero
                  value: absent(signal.max_value),
                },
                {
                  label: "Cycle (ms)",
                  // sparse: 204/302 — absent ≠ zero
                  value: absent(signal.cycle_ms),
                },
                {
                  label: "CAN ID",
                  // sparse: 65/302 — most signals do not have a CAN ID
                  value: absent(signal.can_id),
                },
                {
                  label: "Status",
                  value: renderSignalStatusBadge(signal),
                },
                {
                  label: "Source",
                  // sparse: 295/302
                  value: absent(signal.source),
                },
              ]}
            />
          </ColumnLayout>
        </Container>
      )}
    </SpaceBetween>
  );
};

export default SignalDetailView;
