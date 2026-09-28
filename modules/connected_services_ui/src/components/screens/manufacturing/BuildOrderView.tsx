// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * BuildOrderView — generic production-sequencing table.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.1
 *
 * ## Screen layout
 *
 * A filterable, paginated, sortable table of production build orders using
 * useConnectedServicesCollection. Columns: Order ID, Configuration, Plant,
 * Planned Build Date, Status, Assigned VIN. Both empty states wired.
 *
 * ## Generic by design (OQ1)
 *
 * No OEM-specific production data shapes. The columns and row model are
 * intentionally generic; per-OEM specialisation is out of scope for this pass.
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value is passed through ProvenanceField or assertProvenance
 * before rendering (spec D4 / T2.2 provenanceRender guard).
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useCallback } from "react";

import ProvenanceField from "../../commons/ProvenanceField";
import { useConnectedServicesCollection } from "../../commons/useConnectedServicesCollection";
import {
  getHeaderCounterText,
  getTextFilterCounterText,
} from "../../commons/tableI18n";
import { assertProvenance } from "../../../types";

import {
  BUILD_ORDER_FIXTURE,
  type BuildOrderRow,
  type BuildOrderStatus,
} from "./buildOrder.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-build-order-configuration-summary";

const STATUS_LABELS: Record<BuildOrderStatus, string> = {
  scheduled: "Scheduled",
  in_production: "In Production",
  quality_hold: "Quality Hold",
  completed: "Completed",
  shipped: "Shipped",
};

const STATUS_BADGE_COLORS: Record<
  BuildOrderStatus,
  "blue" | "green" | "red" | "severity-medium" | "grey"
> = {
  scheduled: "blue",
  in_production: "severity-medium",
  quality_hold: "red",
  completed: "green",
  shipped: "grey",
};

// ── Column definitions ────────────────────────────────────────────────────────

const COLUMN_DEFINITIONS: Array<{
  id: string;
  header: string;
  cell: (item: BuildOrderRow) => React.ReactNode;
}> = [
  {
    id: "orderId",
    header: "Order ID",
    cell: (item) => (
      <ProvenanceField field={item.orderId} label="build_order_id" />
    ),
  },
  {
    id: "configuration",
    header: "Configuration",
    cell: (item) => (
      <ProvenanceField field={item.configuration} label="build_configuration" />
    ),
  },
  {
    id: "plant",
    header: "Plant",
    cell: (item) => (
      <ProvenanceField field={item.plant} label="build_plant" />
    ),
  },
  {
    id: "plannedBuildDate",
    header: "Planned Build Date",
    cell: (item) => (
      <ProvenanceField field={item.plannedBuildDate} label="build_planned_date" />
    ),
  },
  {
    id: "status",
    header: "Status",
    cell: (item) => {
      assertProvenance(item.status, "build_status");
      if (item.status.provenance === "absent") {
        return (
          <Box color="text-status-inactive">
            <em>—</em>
          </Box>
        );
      }
      const status = item.status.value;
      if (status === null) return null;
      return (
        <SpaceBetween size="xs" direction="horizontal">
          <Badge color={STATUS_BADGE_COLORS[status]}>
            {STATUS_LABELS[status]}
          </Badge>
        </SpaceBetween>
      );
    },
  },
  {
    id: "assignedVin",
    header: "Assigned VIN",
    cell: (item) => (
      <ProvenanceField
        field={item.assignedVin}
        label="build_assigned_vin"
        render={(v) => v ?? "Not assigned"}
      />
    ),
  },
];

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * BuildOrderView renders a filterable, paginated table of production build orders.
 *
 * All data is fixture-backed. No OEM-specific shapes.
 */
const BuildOrderView: React.FC = () => {
  const handleClearFilter = useCallback(() => {
    collection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const collection = useConnectedServicesCollection<BuildOrderRow>(
    BUILD_ORDER_FIXTURE.rows,
    {
      resourceName: "build orders",
      pageSize: 25,
      onClearFilter: handleClearFilter,
    },
  );

  const { items, filteredItemsCount, collectionProps, filterProps, paginationProps } =
    collection;

  const totalCount = BUILD_ORDER_FIXTURE.rows.length;

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registry-completeness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="build-order-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      <Table
        {...collectionProps}
        data-testid="build-order-table"
        columnDefinitions={COLUMN_DEFINITIONS}
        items={items}
        loadingText="Loading build orders"
        header={
          <Header
            counter={getHeaderCounterText(filteredItemsCount, totalCount)}
            data-testid="build-order-table-header"
          >
            Build Orders
          </Header>
        }
        filter={
          <TextFilter
            {...filterProps}
            filteringPlaceholder="Find build orders"
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

export default BuildOrderView;
