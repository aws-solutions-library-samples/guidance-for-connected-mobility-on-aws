// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * HomologationView — per-market, per-model-variant type-approval status table.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.1
 *
 * ## Screen layout
 *
 * A filterable, paginated, sortable table using useConnectedServicesCollection.
 * Columns: Model Variant, Market, Type-Approval Status, RXSWIN Reference,
 *          Last Updated. Both empty states wired.
 *
 * ## RXSWIN
 *
 * RXSWIN is rendered as a plain provenance: 'simulated' field, never inside
 * an evidence[] block (spec T5.4 Constraints: "RXSWIN is a plain
 * provenance: 'simulated' field, never inside an evidence[] block").
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
import StatusIndicator from "@cloudscape-design/components/status-indicator";
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
  HOMOLOGATION_FIXTURE,
  type HomologationRow,
  type TypeApprovalStatus,
} from "./homologation.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-homologation-rxswin-registry";

const STATUS_LABELS: Record<TypeApprovalStatus, string> = {
  approved: "Approved",
  pending: "Pending",
  under_review: "Under Review",
  withdrawn: "Withdrawn",
};

const STATUS_INDICATOR_TYPES: Record<
  TypeApprovalStatus,
  "success" | "pending" | "in-progress" | "stopped"
> = {
  approved: "success",
  pending: "pending",
  under_review: "in-progress",
  withdrawn: "stopped",
};

// ── Column definitions ────────────────────────────────────────────────────────

const COLUMN_DEFINITIONS: Array<{
  id: string;
  header: string;
  cell: (item: HomologationRow) => React.ReactNode;
}> = [
  {
    id: "modelVariant",
    header: "Model Variant",
    cell: (item) => (
      <ProvenanceField field={item.modelVariant} label="hom_model_variant" />
    ),
  },
  {
    id: "market",
    header: "Market",
    cell: (item) => (
      <ProvenanceField field={item.market} label="hom_market" />
    ),
  },
  {
    id: "typeApprovalStatus",
    header: "Type-Approval Status",
    cell: (item) => {
      assertProvenance(item.typeApprovalStatus, "hom_type_approval_status");
      if (item.typeApprovalStatus.provenance === "absent") {
        return (
          <Box color="text-status-inactive">
            <em>—</em>
          </Box>
        );
      }
      const status = item.typeApprovalStatus.value;
      if (status === null) return null;
      return (
        <SpaceBetween size="xs" direction="horizontal">
          <StatusIndicator type={STATUS_INDICATOR_TYPES[status]}>
            {STATUS_LABELS[status]}
          </StatusIndicator>
        </SpaceBetween>
      );
    },
  },
  {
    id: "rxswinReference",
    header: "RXSWIN Reference",
    // RXSWIN is a plain simulated field — not inside evidence[].
    cell: (item) => (
      <ProvenanceField field={item.rxswinReference} label="hom_rxswin" />
    ),
  },
  {
    id: "lastUpdated",
    header: "Last Updated",
    cell: (item) => (
      <ProvenanceField field={item.lastUpdated} label="hom_last_updated" />
    ),
  },
];

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * HomologationView renders a filterable table of per-market, per-model-variant
 * type-approval records including RXSWIN references.
 *
 * RXSWIN fields are plain ProvenanceValue<string> — never wrapped in Tier2Artifact
 * evidence[] (spec T5.4 Constraints).
 */
const HomologationView: React.FC = () => {
  const handleClearFilter = useCallback(() => {
    collection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const collection = useConnectedServicesCollection<HomologationRow>(
    HOMOLOGATION_FIXTURE.rows,
    {
      resourceName: "homologation records",
      pageSize: 25,
      onClearFilter: handleClearFilter,
    },
  );

  const { items, filteredItemsCount, collectionProps, filterProps, paginationProps } =
    collection;

  const totalCount = HOMOLOGATION_FIXTURE.rows.length;

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registry-completeness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="homologation-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      <Table
        {...collectionProps}
        data-testid="homologation-table"
        columnDefinitions={COLUMN_DEFINITIONS}
        items={items}
        loadingText="Loading homologation records"
        header={
          <Header
            counter={getHeaderCounterText(filteredItemsCount, totalCount)}
            data-testid="homologation-table-header"
          >
            Homologation Registry
          </Header>
        }
        filter={
          <TextFilter
            {...filterProps}
            filteringPlaceholder="Find homologation records"
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

export default HomologationView;
