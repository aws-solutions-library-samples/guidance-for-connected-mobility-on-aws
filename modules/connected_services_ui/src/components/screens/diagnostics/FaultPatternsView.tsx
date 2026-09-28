// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FaultPatternsView — fault signature table across the vehicle population (T6.5).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.5
 *
 * ## Screen layout
 *
 * A filterable, paginated table of fault signatures across the vehicle population,
 * using useConnectedServicesCollection. Columns: Fault Signature, Affected VINs,
 * First Seen, Trend, Affected Model, Markets. Both empty states wired.
 *
 * ## Aggregate data only
 *
 * No trip, GPS, driver-identity, or vehicle-location field appears anywhere.
 * Every displayed value is an aggregate population figure (T6.5 Constraints).
 *
 * ## Click-through
 *
 * Clicking a row navigates to /software/workbench/:vin where :vin is the
 * representative VIN from the row's fixture entry. The Diagnosis Workbench
 * accepts this shape via useParams<{ signalId?: string }>.
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value is passed through ProvenanceField
 * before rendering (spec D4 / T2.2 provenanceRender guard).
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useCallback } from "react";
import { useNavigate } from "react-router-dom";

import ProvenanceField from "../../commons/ProvenanceField";
import { useConnectedServicesCollection } from "../../commons/useConnectedServicesCollection";
import {
  getHeaderCounterText,
  getTextFilterCounterText,
} from "../../commons/tableI18n";
import { assertProvenance } from "../../../types";
import type { TrendDirection, FaultPatternRow } from "./faultPatterns.fixture";
import { FAULT_PATTERNS_FIXTURE } from "./faultPatterns.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-fault-patterns-signature-table";

const TREND_LABELS: Record<TrendDirection, string> = {
  rising: "Rising",
  stable: "Stable",
  declining: "Declining",
};

const TREND_STATUS_TYPES: Record<
  TrendDirection,
  "error" | "warning" | "success"
> = {
  rising: "error",
  stable: "warning",
  declining: "success",
};

// ── Column definitions ────────────────────────────────────────────────────────

const COLUMN_DEFINITIONS: Array<{
  id: string;
  header: string;
  cell: (item: FaultPatternRow) => React.ReactNode;
}> = [
  {
    id: "faultSignature",
    header: "Fault Signature",
    cell: (item) => (
      <ProvenanceField
        field={item.faultSignature}
        label="fault_signature"
        testId="cs-fault-signature"
      />
    ),
  },
  {
    id: "affectedVinCount",
    header: "Affected VINs",
    cell: (item) => (
      <ProvenanceField
        field={item.affectedVinCount}
        label="affected_vin_count"
        testId="cs-fault-affected-count"
        render={(v) => String(v)}
      />
    ),
  },
  {
    id: "firstSeen",
    header: "First Seen",
    cell: (item) => (
      <ProvenanceField
        field={item.firstSeen}
        label="fault_first_seen"
        testId="cs-fault-first-seen"
      />
    ),
  },
  {
    id: "trend",
    header: "Trend",
    cell: (item) => {
      assertProvenance(item.trend, "fault_trend");
      if (item.trend.provenance === "absent") {
        return (
          <Box color="text-status-inactive">
            <em>—</em>
          </Box>
        );
      }
      const trend = item.trend.value;
      if (trend === null) return null;
      return (
        <SpaceBetween size="xs" direction="horizontal">
          <StatusIndicator
            type={TREND_STATUS_TYPES[trend]}
            data-testid="cs-fault-trend-indicator"
          >
            {TREND_LABELS[trend]}
          </StatusIndicator>
        </SpaceBetween>
      );
    },
  },
  {
    id: "affectedModel",
    header: "Affected Model",
    cell: (item) => (
      <ProvenanceField
        field={item.affectedModel}
        label="fault_affected_model"
        testId="cs-fault-affected-model"
      />
    ),
  },
  {
    id: "affectedMarkets",
    header: "Markets",
    cell: (item) => (
      <ProvenanceField
        field={item.affectedMarkets}
        label="fault_affected_markets"
        testId="cs-fault-affected-markets"
      />
    ),
  },
];

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * FaultPatternsView renders a filterable, paginated table of fault signatures
 * across the vehicle population.
 *
 * Clicking a row navigates to the Diagnosis Workbench pre-loaded with a
 * representative VIN for that fault pattern.
 *
 * All data is aggregate fixture-backed. No trip/GPS/location fields.
 */
const FaultPatternsView: React.FC = () => {
  const navigate = useNavigate();

  const handleClearFilter = useCallback(() => {
    collection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const collection = useConnectedServicesCollection<FaultPatternRow>(
    FAULT_PATTERNS_FIXTURE.rows,
    {
      resourceName: "fault patterns",
      pageSize: 25,
      onClearFilter: handleClearFilter,
    },
  );

  const { items, filteredItemsCount, collectionProps, filterProps, paginationProps } =
    collection;

  const totalCount = FAULT_PATTERNS_FIXTURE.rows.length;

  const handleRowClick = (row: FaultPatternRow): void => {
    // Use the representative VIN from the fixture — not a GPS field.
    const vin = row.representativeVin.value;
    if (vin != null && vin.length > 0) {
      navigate(`/software/workbench/${encodeURIComponent(vin)}`);
    }
  };

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registry-completeness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="fault-patterns-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      <Table
        {...collectionProps}
        data-testid="fault-patterns-table"
        columnDefinitions={COLUMN_DEFINITIONS}
        items={items}
        loadingText="Loading fault patterns"
        onRowClick={({ detail }) => { handleRowClick(detail.item); }}
        header={
          <Header
            counter={getHeaderCounterText(filteredItemsCount, totalCount)}
            description="Aggregate fault signatures across the vehicle population. Clicking a row opens the Diagnosis Workbench."
            data-testid="fault-patterns-table-header"
          >
            Fault Patterns
          </Header>
        }
        filter={
          <TextFilter
            {...filterProps}
            filteringPlaceholder="Find fault patterns"
            countText={getTextFilterCounterText(
              filteredItemsCount ?? totalCount,
              totalCount,
            )}
          />
        }
        pagination={<Pagination {...paginationProps} />}
      />
    </SpaceBetween>
  );
};

export default FaultPatternsView;
