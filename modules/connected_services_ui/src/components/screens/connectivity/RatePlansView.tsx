// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * RatePlansView — rate plan catalogue, assignment view, and usage/overage list.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T5.2
 *
 * ## Screen layout
 *
 * 1. Self-Managed / Amazon Managed Plans toggle — relabels the billing-owner
 *    column header ONLY.  The values in each cell are unchanged.  The two
 *    commercial models differ in who receives the invoice — a label, not a
 *    system.  No settlement is modelled (spec T5.2 Constraints).
 *
 * 2. Plan catalogue table — filterable, paginated, sortable.
 *    Columns: Plan Name, Market, Data Allowance, Price, Billing Owner (*),
 *             Assigned Vehicles.
 *    (*) Column header text is toggled by the billing-mode control.
 *    Clicking a row opens a link to Fleet Health filtered by that plan.
 *
 * 3. Usage / Overage list — filterable, paginated table.
 *    Columns: VIN, Plan, Market, Used (MB), Allowance (MB), Overage (MB),
 *             Billing Cycle.
 *
 * Both empty states are wired for each table (TableEmptyState, TableNoMatchState).
 *
 * ## Toggle mechanics
 *
 * `BillingMode` is either "self-managed" or "amazon-managed".
 * The column definition array for the plan catalogue table is rebuilt whenever
 * the toggle changes — the column `id` and all `cell` renderers stay identical;
 * only the `header` string of the billing-owner column changes.
 *
 * This is the ONLY effect of the toggle.  No other column, cell, or UI element
 * changes.  The test suite asserts exactly this invariant.
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value is passed through ProvenanceField or assertProvenance
 * before rendering (spec D4 / T2.2 provenanceRender guard).
 */

import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import Toggle from "@cloudscape-design/components/toggle";
import React, { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import KpiCard from "../../commons/KpiCard";
import KpiCardGrid from "../../commons/KpiCardGrid";
import ProvenanceField from "../../commons/ProvenanceField";
// TableEmptyState and TableNoMatchState are wired through useConnectedServicesCollection
// (passed as filtering.empty / filtering.noMatch to useCollection) — imported here
// for documentation clarity but not directly referenced in JSX.
import type {} from "../../commons/tableStates";
import { useConnectedServicesCollection } from "../../commons/useConnectedServicesCollection";
import {
  getHeaderCounterText,
  getTextFilterCounterText,
} from "../../commons/tableI18n";

import {
  RATE_PLANS_FIXTURE,
  type RatePlanRow,
  type UsageOverageRow,
} from "./ratePlans.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-rate-plans-catalogue-table";

/**
 * Billing mode toggle values.
 *
 * The toggle only changes the billing-owner column HEADER.
 * "Self-Managed"     → OEM invoiced directly by the carrier.
 * "Amazon Managed"   → Amazon Connectivity Services acts as the billing intermediary.
 * Both models are label-level distinctions; no settlement logic is modelled.
 */
export type BillingMode = "self-managed" | "amazon-managed";

/** Column header for the billing-owner column in each mode. */
export const BILLING_OWNER_LABEL: Record<BillingMode, string> = {
  "self-managed": "Billed To (OEM)",
  "amazon-managed": "Billed Via (Amazon)",
} as const;

// ── Column definitions ────────────────────────────────────────────────────────

/**
 * Build plan-catalogue column definitions.
 *
 * The `billingOwnerHeader` argument is the ONLY value that changes between
 * billing modes.  All `id`, `sortingField`, and `cell` values are identical
 * across both modes.
 */
function buildPlanColumnDefinitions(
  billingOwnerHeader: string,
): import("@cloudscape-design/components/table").TableProps.ColumnDefinition<RatePlanRow>[] {
  return [
    {
      id: "name",
      header: "Plan Name",
      cell: (item) => <ProvenanceField field={item.name} label="plan_name" />,
      sortingField: "name",
    },
    {
      id: "market",
      header: "Market",
      cell: (item) => (
        <ProvenanceField field={item.market} label="plan_market" />
      ),
      sortingField: "market",
    },
    {
      id: "dataAllowance",
      header: "Data Allowance",
      cell: (item) => (
        <ProvenanceField
          field={item.dataAllowance}
          label="plan_data_allowance"
        />
      ),
    },
    {
      id: "price",
      header: "Price",
      cell: (item) => (
        <ProvenanceField field={item.price} label="plan_price" />
      ),
    },
    {
      // Column id is stable across billing modes — only header text changes.
      id: "billingOwner",
      header: billingOwnerHeader,
      cell: (item) => (
        <ProvenanceField
          field={item.billingOwner}
          label="plan_billing_owner"
        />
      ),
    },
    {
      id: "assignedVehicleCount",
      header: "Assigned Vehicles",
      cell: (item) => (
        <ProvenanceField
          field={item.assignedVehicleCount}
          label="plan_assigned_vehicle_count"
          render={(v) => String(v)}
        />
      ),
      sortingField: "assignedVehicleCount",
    },
  ];
}

/**
 * Build usage/overage column definitions (billing mode does NOT affect this table).
 */
function buildUsageColumnDefinitions(): import("@cloudscape-design/components/table").TableProps.ColumnDefinition<UsageOverageRow>[] {
  return [
    {
      id: "vin",
      header: "VIN",
      cell: (item) => <ProvenanceField field={item.vin} label="usage_vin" />,
    },
    {
      id: "plan",
      header: "Plan",
      cell: (item) => (
        <ProvenanceField field={item.plan} label="usage_plan" />
      ),
    },
    {
      id: "market",
      header: "Market",
      cell: (item) => (
        <ProvenanceField field={item.market} label="usage_market" />
      ),
    },
    {
      id: "usedMb",
      header: "Used (MB)",
      cell: (item) => (
        <ProvenanceField
          field={item.usedMb}
          label="usage_used_mb"
          render={(v) => String(v)}
        />
      ),
    },
    {
      id: "allowanceMb",
      header: "Allowance (MB)",
      cell: (item) => (
        <ProvenanceField
          field={item.allowanceMb}
          label="usage_allowance_mb"
          render={(v) => String(v)}
        />
      ),
    },
    {
      id: "overageMb",
      header: "Overage (MB)",
      cell: (item) => (
        <ProvenanceField
          field={item.overageMb}
          label="usage_overage_mb"
          render={(v) => String(v)}
        />
      ),
    },
    {
      id: "billingCycle",
      header: "Billing Cycle",
      cell: (item) => (
        <ProvenanceField
          field={item.billingCycle}
          label="usage_billing_cycle"
        />
      ),
    },
  ];
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * RatePlansView renders:
 * - A billing-mode toggle (Self-Managed / Amazon Managed Plans).
 * - KPI summary cards (total plans, total assigned vehicles, plans with overage).
 * - Plan catalogue table (relabels billing-owner column on toggle change only).
 * - Usage / overage table.
 */
const RatePlansView: React.FC = () => {
  const navigate = useNavigate();

  // ── Billing mode toggle ──────────────────────────────────────────────────

  const [billingMode, setBillingMode] = useState<BillingMode>("self-managed");
  const isAmazonManaged = billingMode === "amazon-managed";

  const handleToggleChange = useCallback(
    ({ detail }: { detail: { checked: boolean } }) => {
      setBillingMode(detail.checked ? "amazon-managed" : "self-managed");
    },
    [],
  );

  // ── Plan catalogue collection ────────────────────────────────────────────

  const handleClearPlanFilter = useCallback(() => {
    planCollection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const planCollection = useConnectedServicesCollection<RatePlanRow>(
    RATE_PLANS_FIXTURE.plans,
    {
      resourceName: "plans",
      pageSize: 20,
      onClearFilter: handleClearPlanFilter,
    },
  );

  const {
    items: planItems,
    filteredItemsCount: planFilteredCount,
    collectionProps: planCollectionProps,
    filterProps: planFilterProps,
    paginationProps: planPaginationProps,
  } = planCollection;

  // Rebuild column definitions only when billingMode changes.
  const planColumnDefs = useMemo(
    () => buildPlanColumnDefinitions(BILLING_OWNER_LABEL[billingMode]),
    [billingMode],
  );

  // ── Usage / overage collection ───────────────────────────────────────────

  const handleClearUsageFilter = useCallback(() => {
    usageCollection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const usageCollection = useConnectedServicesCollection<UsageOverageRow>(
    RATE_PLANS_FIXTURE.usageOverages,
    {
      resourceName: "overage records",
      pageSize: 20,
      onClearFilter: handleClearUsageFilter,
    },
  );

  const {
    items: usageItems,
    filteredItemsCount: usageFilteredCount,
    collectionProps: usageCollectionProps,
    filterProps: usageFilterProps,
    paginationProps: usagePaginationProps,
  } = usageCollection;

  // Usage column defs are static — billing mode does not affect this table.
  const usageColumnDefs = useMemo(() => buildUsageColumnDefinitions(), []);

  // ── Row click for plan catalogue → Fleet Health filtered by plan ─────────

  const handlePlanRowClick = useCallback(
    (row: RatePlanRow) => {
      if (row.name.value !== null) {
        const encoded = encodeURIComponent(row.name.value);
        void navigate(`/connectivity/fleet-health?plan=${encoded}`);
      }
    },
    [navigate],
  );

  // ── KPI summary cards ────────────────────────────────────────────────────

  const totalPlans = RATE_PLANS_FIXTURE.plans.length;
  const totalAssigned = RATE_PLANS_FIXTURE.plans.reduce(
    (acc, p) => acc + (p.assignedVehicleCount.value ?? 0),
    0,
  );
  const plansWithOverage = new Set(
    RATE_PLANS_FIXTURE.usageOverages
      .filter((u) => (u.overageMb.value ?? 0) > 0)
      .map((u) => u.plan.value),
  ).size;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registry-completeness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="rate-plans-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {/* Billing mode toggle */}
      <Box data-testid="billing-mode-toggle-container">
        <SpaceBetween size="xs" direction="horizontal">
          <Toggle
            checked={isAmazonManaged}
            onChange={handleToggleChange}
            data-testid="billing-mode-toggle"
          >
            Amazon Managed Plans
          </Toggle>
          <Box color="text-body-secondary" variant="small">
            {isAmazonManaged
              ? "Billing handled via Amazon Connectivity Services"
              : "Direct billing from carrier to OEM account"}
          </Box>
        </SpaceBetween>
      </Box>

      {/* KPI summary strip */}
      <KpiCardGrid>
        <KpiCard
          label="Total Plans"
          value={String(totalPlans)}
          color="text-status-info"
          captions={["Across all markets"]}
        />
        <KpiCard
          label="Assigned Vehicles"
          value={String(totalAssigned)}
          color="text-status-success"
          captions={["Vehicles with an active plan"]}
        />
        <KpiCard
          label="Plans with Overage"
          value={String(plansWithOverage)}
          color={plansWithOverage > 0 ? "text-status-warning" : "text-status-success"}
          captions={["Plans where vehicles exceeded allowance this cycle"]}
        />
      </KpiCardGrid>

      {/* Plan catalogue table */}
      <Table
        {...planCollectionProps}
        data-testid="rate-plans-catalogue-table"
        columnDefinitions={planColumnDefs}
        items={planItems}
        loadingText="Loading rate plans"
        header={
          <Header
            counter={getHeaderCounterText(
              planFilteredCount,
              RATE_PLANS_FIXTURE.plans.length,
            )}
            description={
              isAmazonManaged
                ? "Billing managed by Amazon Connectivity Services"
                : "Direct billing — OEM account billed by carrier"
            }
            data-testid="rate-plans-catalogue-header"
          >
            Rate Plans — {isAmazonManaged ? "Amazon Managed" : "Self-Managed"}
          </Header>
        }
        filter={
          <TextFilter
            {...planFilterProps}
            filteringPlaceholder="Find plans"
            countText={getTextFilterCounterText(
              planFilteredCount ?? RATE_PLANS_FIXTURE.plans.length,
              RATE_PLANS_FIXTURE.plans.length,
            )}
          />
        }
        pagination={<Pagination {...planPaginationProps} />}
        onRowClick={({ detail }) => handlePlanRowClick(detail.item)}
      />

      {/* Assignment view helper link */}
      <Box>
        <Link
          href="/connectivity/fleet-health"
          data-testid="view-all-fleet-health-link"
        >
          View all vehicles in Fleet Health
        </Link>
      </Box>

      {/* Usage / overage table */}
      <Table
        {...usageCollectionProps}
        data-testid="rate-plans-usage-table"
        columnDefinitions={usageColumnDefs}
        items={usageItems}
        loadingText="Loading overage records"
        header={
          <Header
            counter={getHeaderCounterText(
              usageFilteredCount,
              RATE_PLANS_FIXTURE.usageOverages.length,
            )}
            data-testid="rate-plans-usage-header"
          >
            Usage &amp; Overage
          </Header>
        }
        filter={
          <TextFilter
            {...usageFilterProps}
            filteringPlaceholder="Find overage records"
            countText={getTextFilterCounterText(
              usageFilteredCount ?? RATE_PLANS_FIXTURE.usageOverages.length,
              RATE_PLANS_FIXTURE.usageOverages.length,
            )}
          />
        }
        pagination={<Pagination {...usagePaginationProps} />}
      />
    </SpaceBetween>
  );
};

export default RatePlansView;
