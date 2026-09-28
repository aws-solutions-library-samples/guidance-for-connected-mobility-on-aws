// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FleetHealthView — fleet connectivity health, market summary + filterable table.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T4.2
 *
 * ## Screen layout
 *
 * 1. Market summary card row — KpiCard per market (US / Germany / India),
 *    each showing connected / degraded / NTN-fallback / unreachable counts.
 * 2. Filterable, paginated, sortable table using useConnectedServicesCollection.
 *    Columns: VIN, Market, Connectivity State (badge), Bearer, Signal,
 *    Last Session, TCU Tier.
 * 3. Both empty states wired: TableEmptyState and TableNoMatchState.
 *
 * ## ?state= query-param filter contract
 *
 * The Command Center tile and any other screen may drill into this view with:
 *   /connectivity/fleet-health?state=<ConnectivityState>
 *
 * Valid values: connected | degraded | ntn_fallback | unreachable
 *
 * On mount, useSearchParams() reads the `state` param. When present, it
 * pre-populates the connectivity-state filter chip. The chip is removable —
 * dismissing it clears `state` from the URL (via setSearchParams).
 *
 * The filter is applied client-side over the fixture rows. The text filter
 * (useConnectedServicesCollection) and the `state` chip filter compose as AND:
 * a row must pass both to appear. When the text filter is empty and the state
 * chip is absent all rows appear (normal empty state).
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value is passed to ProvenanceField — no direct
 * .value dereferences in JSX (spec D4 / T2.2 provenanceRender guard).
 *
 * ## No location field
 *
 * No trip / GPS / driver-identity / cell-location field is rendered here
 * (spec T4.2 Constraints). Market attribution is the only geographic field.
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useCallback, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";


import KpiCard from "../../commons/KpiCard";
import KpiCardGrid from "../../commons/KpiCardGrid";
import ProvenanceField from "../../commons/ProvenanceField";
import { useConnectedServicesCollection } from "../../commons/useConnectedServicesCollection";
import {
  getHeaderCounterText,
  getTextFilterCounterText,
} from "../../commons/tableI18n";
import { assertProvenance } from "../../../types";

import {
  FLEET_HEALTH_FIXTURE,
  type ConnectivityState,
  type FleetHealthRow,
} from "./fleetHealth.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-fleet-health-market-summary";

/**
 * Valid values for the ?state= query parameter.
 * This is the public contract for the Command Center drill-down tile.
 *
 * Shape: /connectivity/fleet-health?state=<ConnectivityStateParam>
 *   connected    — vehicles with full connectivity
 *   degraded     — vehicles with degraded connectivity (Command Center tile default)
 *   ntn_fallback — vehicles on NTN satellite fallback
 *   unreachable  — vehicles that cannot be reached
 */
export type ConnectivityStateParam = ConnectivityState;

const VALID_STATE_PARAMS = new Set<string>([
  "connected",
  "degraded",
  "ntn_fallback",
  "unreachable",
]);

const STATE_LABELS: Record<ConnectivityState, string> = {
  connected: "Connected",
  degraded: "Degraded",
  ntn_fallback: "NTN Fallback",
  unreachable: "Unreachable",
};

const STATE_BADGE_COLORS: Record<
  ConnectivityState,
  "green" | "red" | "severity-medium" | "grey"
> = {
  connected: "green",
  degraded: "severity-medium",
  ntn_fallback: "grey",
  unreachable: "red",
};

import type { TableProps } from "@cloudscape-design/components/table";

// ── Column definitions ────────────────────────────────────────────────────────

/**
 * buildColumnDefinitions — returns column definitions for the fleet table.
 *
 * The `pageItems` parameter is the post-filter, post-sort, post-paginate slice
 * currently rendered.  The VIN cell embeds a hidden span with testid
 * `cs-fleet-health-vin-row-<index>` (0-based within `pageItems`) so the
 * Goal-3 click-path walk can locate and click individual rows without
 * coupling to VIN values.
 *
 * Clicking the span bubbles to the Cloudscape Table row, triggering onRowClick
 * → handleRowClick → navigate to subscriber-lookup detail.
 */
function buildColumnDefinitions(
  pageItems: readonly FleetHealthRow[],
): TableProps.ColumnDefinition<FleetHealthRow>[] {
  return [
    {
      id: "vin",
      header: "VIN",
      cell: (item) => {
        const rowIndex = pageItems.indexOf(item);
        return (
          <span style={{ display: "contents" }}>
            {/* Per-row testid for the Goal-3 click-path walk (T2.3).
                Index is 0-based within the current post-filter post-sort page. */}
            <span
              data-testid={`cs-fleet-health-vin-row-${rowIndex}`}
              aria-hidden="true"
              style={{ display: "none" }}
            />
            <ProvenanceField field={item.vin} label="fleet_vin" />
          </span>
        );
      },
    },
    {
      id: "market",
      header: "Market",
      cell: (item) => (
        <ProvenanceField field={item.market} label="fleet_market" />
      ),
    },
    {
      id: "connectivityState",
      header: "Connectivity State",
      // assertProvenance is called here, so any missing marker throws at render time (D5).
      // The badge is rendered inline because ProvenanceField.render returns string only.
      cell: (item) => {
        assertProvenance(item.connectivityState, "fleet_connectivity_state");
        if (item.connectivityState.provenance === "absent") {
          return (
            <Box color="text-status-inactive">
              <em>—</em>
            </Box>
          );
        }
        const state = item.connectivityState.value;
        if (state === null) return null;
        return (
          <SpaceBetween size="xs" direction="horizontal">
            <Badge color={STATE_BADGE_COLORS[state]}>{STATE_LABELS[state]}</Badge>
          </SpaceBetween>
        );
      },
    },
    {
      id: "bearer",
      header: "Bearer",
      cell: (item) => (
        <ProvenanceField field={item.bearer} label="fleet_bearer" />
      ),
    },
    {
      id: "signal",
      header: "Signal",
      cell: (item) => (
        <ProvenanceField field={item.signal} label="fleet_signal" />
      ),
    },
    {
      id: "lastSession",
      header: "Last Session",
      cell: (item) => (
        <ProvenanceField field={item.lastSession} label="fleet_last_session" />
      ),
    },
    {
      id: "tcuTier",
      header: "TCU Tier",
      cell: (item) => (
        <ProvenanceField field={item.tcuTier} label="fleet_tcu_tier" />
      ),
    },
  ];
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * FleetHealthView renders:
 * - Market summary KPI cards (US, Germany, India)
 * - A filterable table of fleet vehicles
 *
 * Reads ?state=<ConnectivityState> on mount to pre-apply the state filter.
 * Row click navigates to subscriber lookup for that VIN.
 */
const FleetHealthView: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // Read the state chip from the URL
  const stateParam = searchParams.get("state");
  const activeStateFilter =
    stateParam !== null && VALID_STATE_PARAMS.has(stateParam)
      ? (stateParam as ConnectivityState)
      : null;

  // Clear the ?state= filter by removing it from the URL
  const handleClearStateFilter = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("state");
      return next;
    });
  }, [setSearchParams]);

  // Apply the connectivity-state chip filter on top of the text filter
  const stateFilteredRows = useMemo<FleetHealthRow[]>(() => {
    if (activeStateFilter === null) {
      return FLEET_HEALTH_FIXTURE.rows;
    }
    return FLEET_HEALTH_FIXTURE.rows.filter(
      (row) => row.connectivityState.value === activeStateFilter,
    );
  }, [activeStateFilter]);

  // Text filter + pagination + sorting via the commons hook
  const handleClearFilter = useCallback(() => {
    collection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const collection = useConnectedServicesCollection<FleetHealthRow>(
    stateFilteredRows,
    {
      resourceName: "vehicles",
      pageSize: 25,
      onClearFilter: handleClearFilter,
    },
  );

  const { items, filteredItemsCount, collectionProps, filterProps, paginationProps } =
    collection;

  // Build column definitions with per-row testids keyed on the current page items.
  // Re-memoized whenever `items` reference changes (post-filter, post-sort, post-paginate).
  const columnDefinitions = useMemo(
    () => buildColumnDefinitions(items),
    [items],
  );

  // Row click → subscriber lookup
  const handleRowClick = useCallback(
    (row: FleetHealthRow) => {
      if (row.vin.value !== null) {
        void navigate(
          `/connectivity/subscriber-lookup/${encodeURIComponent(row.vin.value)}`,
        );
      }
    },
    [navigate],
  );

  // ── Market summary cards ─────────────────────────────────────────────────

  const marketCards = FLEET_HEALTH_FIXTURE.marketSummaries.map((summary) => {
    const connected = summary.connected.value ?? 0;
    const degraded = summary.degraded.value ?? 0;
    const ntpFallback = summary.ntpFallback.value ?? 0;
    const unreachable = summary.unreachable.value ?? 0;
    const total = connected + degraded + ntpFallback + unreachable;
    const label = summary.market.value ?? "—";
    return (
      <KpiCard
        key={summary.id}
        label={label}
        value={String(total)}
        color={
          degraded > 0 || unreachable > 0
            ? "text-status-warning"
            : "text-status-success"
        }
        captions={[
          `${String(connected)} connected`,
          `${String(degraded)} degraded`,
          `${String(ntpFallback)} NTN fallback`,
          `${String(unreachable)} unreachable`,
        ]}
      />
    );
  });

  // ── State filter chip ────────────────────────────────────────────────────

  const stateFilterChip =
    activeStateFilter !== null ? (
      <SpaceBetween size="xs" direction="horizontal">
        <span>
          Connectivity State:{" "}
          <strong>{STATE_LABELS[activeStateFilter]}</strong>
        </span>
        <Button
          variant="inline-link"
          onClick={handleClearStateFilter}
          ariaLabel="Clear connectivity state filter"
          data-testid="clear-state-filter-btn"
        >
          Clear
        </Button>
      </SpaceBetween>
    ) : null;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registry-completeness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="fleet-health-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      {/* Market summary cards */}
      <KpiCardGrid>{marketCards}</KpiCardGrid>

      {/* State filter chip (shown only when ?state= is active) */}
      {stateFilterChip !== null && (
        <div data-testid="state-filter-chip">{stateFilterChip}</div>
      )}

      {/* Fleet table */}
      <Table
        {...collectionProps}
        data-testid="fleet-health-table"
        columnDefinitions={columnDefinitions}
        items={items}
        loadingText="Loading vehicles"
        header={
          <Header
            counter={getHeaderCounterText(
              filteredItemsCount,
              stateFilteredRows.length,
            )}
            data-testid="fleet-health-table-header"
          >
            Fleet Vehicles
          </Header>
        }
        filter={
          <SpaceBetween size="xs">
            <TextFilter
              {...filterProps}
              filteringPlaceholder="Find vehicles"
              countText={getTextFilterCounterText(
                filteredItemsCount ?? stateFilteredRows.length,
                stateFilteredRows.length,
              )}
            />
          </SpaceBetween>
        }
        pagination={<Pagination {...paginationProps} />}
        onRowClick={({ detail }) => handleRowClick(detail.item)}
      />
    </SpaceBetween>
  );
};

export default FleetHealthView;
