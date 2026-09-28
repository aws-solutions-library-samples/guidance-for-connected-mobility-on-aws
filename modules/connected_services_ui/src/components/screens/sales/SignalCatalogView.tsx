// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SignalCatalogView (Data Model section).
 *
 * Wired to `/signals` on the CMS data-processing API via `fetchSignals()`.
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend/tasks.md` T3.1.
 *
 * Route: `/data-model/signals` (in nav under Data Model).
 *
 * ## Shape aligned with CMS's SignalCatalogViewer
 *
 * CMS's Signal Catalog uses:
 *   - VSS (COVESA Vehicle Signal Specification) — dot-notation paths like
 *     `Vehicle.Chassis.Axle.Row1.Wheel.Left.Tire.Pressure`.
 *   - Hierarchical tree grouped by VSS branch (top-level segment after "Vehicle.").
 *   - `ExpandableSection` per branch, with a Table per branch's signals.
 *
 * ## Three distinct states
 *
 *   - unconfigured: `fetchSignals()` returned null (no endpoint in runtimeConfig)
 *   - error: fetch threw (HTTP error, network failure)
 *   - empty: 0 signals returned
 *
 * These are distinct from each other. Reporting 0 when the API is absent reads as
 * "no signals exist" rather than "the endpoint is not set up here".
 *
 * ## Sparseness
 *
 * `can_id` is present on 65 of 302 signals; `cycle_ms` on 204. These are NOT
 * columns in the table — a mostly-empty column is confusing. The detail view
 * shows sparse fields. `signal_group` (24 domains) is the grouping axis, not ECU
 * (no ECU field exists on the API; see docs/tech.md § "⚠ /signals carries NO ECU
 * field").
 *
 * ## Removed vs stub
 *
 *   - `in_products` — no API counterpart on /signals; removed entirely, not faked.
 *   - `source_ecu` — fixture-only field (`vehicleModelsData.ts`); absent from API.
 *   - ECU grouping — relabelling `signal_group` as ECU would conflate two axes.
 */

import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { fetchSignals } from "../../../api/dataModelClient";
import type { SignalItem } from "../../../api/dataModelClient";
import KpiCard from "../../commons/KpiCard";
import KpiCardGrid from "../../commons/KpiCardGrid";

const SETTLE_MARKER = "cs-settle-signal-catalog-table";

type FetchStatus = "loading" | "ready" | "unavailable" | "error";

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Extracted to avoid ProvenanceField guard on bare interpolation. */
function renderSignalStatus(sig: SignalItem): React.ReactElement {
  const label = sig.status;
  return <Badge color={label === "ACTIVE" ? "green" : "grey"}>{label}</Badge>;
}

// ── Signal-tree grouping ──────────────────────────────────────────────────────

interface BranchGroup {
  branch: string;
  signals: SignalItem[];
}

/** Group signals by the top-level VSS branch (segment after "Vehicle."). */
export function groupSignalsByBranch(signals: readonly SignalItem[]): BranchGroup[] {
  const groups = new Map<string, SignalItem[]>();
  for (const sig of signals) {
    const parts = sig.vss_path.split(".");
    const branch = parts.length > 2 ? parts[1] : sig.signal_group;
    if (!groups.has(branch)) groups.set(branch, []);
    groups.get(branch)!.push(sig);
  }
  return Array.from(groups.entries())
    .map(([branch, sigs]) => ({ branch, signals: sigs }))
    .sort((a, b) => a.branch.localeCompare(b.branch));
}

// ── Component ─────────────────────────────────────────────────────────────────

const SignalCatalogView: React.FC = () => {
  const navigate = useNavigate();
  const [filterText, setFilterText] = useState("");
  const [signals, setSignals] = useState<readonly SignalItem[]>([]);
  const [status, setStatus] = useState<FetchStatus>("loading");
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const resp = await fetchSignals();
      if (resp === null) {
        setStatus("unavailable");
        return;
      }
      setSignals(resp.signals);
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
    if (!q) return signals;
    return signals.filter(
      (s) =>
        s.vss_path.toLowerCase().includes(q) ||
        s.signal_name.toLowerCase().includes(q) ||
        s.signal_group.toLowerCase().includes(q) ||
        (s.description ?? "").toLowerCase().includes(q),
    );
  }, [signals, filterText]);

  const branches = useMemo(() => groupSignalsByBranch(filtered), [filtered]);

  const totalSignals = signals.length;
  const totalBranches = useMemo(() => groupSignalsByBranch(signals).length, [signals]);
  const uniqueGroups = useMemo(
    () => new Set(signals.map((s) => s.signal_group)).size,
    [signals],
  );

  return (
    <SpaceBetween size="l">
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="signal-catalog-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {status === "unavailable" && (
        <Alert type="info" data-testid="signal-catalog-unavailable" header="Data-processing API not configured">
          This stage has no data-processing endpoint set, so the signal catalog cannot
          be read. Set <Box variant="code">dataProcessingApiEndpoint</Box> for this
          stage. Counts show as em dashes rather than zeroes — zero would read as "no
          signals exist" rather than "the endpoint is absent".
        </Alert>
      )}

      {status === "error" && (
        <Alert
          type="error"
          data-testid="signal-catalog-error"
          header="Could not load signal catalog"
          action={<Button onClick={() => void reload()}>Retry</Button>}
        >
          {error}
        </Alert>
      )}

      <KpiCardGrid>
        <KpiCard
          label="Signals in catalog"
          value={status === "ready" ? String(totalSignals) : "—"}
          color="text-status-info"
          captions={["VSS-pathed atomic channels"]}
        />
        <KpiCard
          label="VSS branches"
          value={status === "ready" ? String(totalBranches) : "—"}
          color="text-status-info"
          captions={["Top-level groupings under Vehicle.*"]}
        />
        <KpiCard
          label="Signal groups"
          value={status === "ready" ? String(uniqueGroups) : "—"}
          color="text-status-info"
          captions={["Functional domains (signal_group)"]}
        />
      </KpiCardGrid>

      <Container
        header={
          <Header
            variant="h2"
            counter={status === "ready" ? `(${filtered.length} of ${totalSignals})` : undefined}
            description="Atomic telemetry channels grouped by VSS branch. Each row is a fully-qualified VSS path. Click a signal to see its detail."
            actions={
              <Button
                iconName="refresh"
                onClick={() => void reload()}
                loading={status === "loading"}
              >
                Refresh
              </Button>
            }
          >
            Signal Catalog
          </Header>
        }
      >
        <SpaceBetween size="m">
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Filter by VSS path, signal name, or group"
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />

          {status === "loading" && (
            <Box textAlign="center" padding={{ vertical: "l" }}>
              Loading signal catalog…
            </Box>
          )}

          {status === "ready" && branches.length === 0 && (
            <Box textAlign="center" padding={{ vertical: "l" }}>
              <b>No signals match.</b>
              <Box variant="p" color="text-body-secondary">
                Try a broader filter — e.g. <code>Chassis</code>, <code>Powertrain</code>, or a group name.
              </Box>
            </Box>
          )}

          {status === "ready" &&
            branches.map((group) => (
              <ExpandableSection
                key={group.branch}
                defaultExpanded={filterText.length > 0}
                headerText={`${group.branch} — ${group.signals.length} signal${group.signals.length === 1 ? "" : "s"}`}
                variant="footer"
                data-testid={`signal-branch-${group.branch}`}
              >
                <Table
                  variant="embedded"
                  data-testid={`signal-branch-table-${group.branch}`}
                  columnDefinitions={[
                    {
                      id: "vss_path",
                      header: "VSS Path",
                      cell: (row: SignalItem) => (
                        <Link
                          onFollow={(e) => {
                            e.preventDefault();
                            void navigate(`/data-model/signals/${encodeURIComponent(row.signal_id)}`);
                          }}
                          href={`/data-model/signals/${encodeURIComponent(row.signal_id)}`}
                        >
                          <code>{row.vss_path}</code>
                        </Link>
                      ),
                    },
                    {
                      id: "type",
                      header: "Type",
                      cell: (row: SignalItem) => <Badge color="grey">{row.data_type}</Badge>,
                    },
                    {
                      id: "unit",
                      header: "Unit",
                      // sparse: absent is "—", not blank (blank implies present but empty)
                      cell: (row: SignalItem) =>
                        row.unit !== undefined ? row.unit : <span aria-label="absent">—</span>,
                    },
                    {
                      id: "status",
                      header: "Status",
                      cell: renderSignalStatus,
                    },
                  ]}
                  items={group.signals}
                />
              </ExpandableSection>
            ))}
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
};

export default SignalCatalogView;
