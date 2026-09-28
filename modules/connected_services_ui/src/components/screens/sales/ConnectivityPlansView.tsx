// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ConnectivityPlansView — subscription lifecycle view.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.3
 *       UX spec § 6.2
 *
 * ## Purpose
 *
 * This is the subscription LIFECYCLE view: active/expired/pending-renewal/cancelled
 * subscriptions per VIN, plus a renewal-rate trend strip.
 *
 * It is DISTINCT from Rate Plans (/connectivity/rate-plans), which is the CMP pricing
 * table (data allowances, plan prices). This screen shows WHO has WHICH plan and
 * WHAT STATE the subscription is in. The underlying plan detail (price, allowance,
 * billing owner) lives in Rate Plans and is cross-linked from here.
 *
 * ## Screen layout
 *
 * 1. Lifecycle KPI strip — active count, pending renewal count, expired count,
 *    and renewal rate for the most recent month in the trend.
 *
 * 2. Renewal rate trend table — one row per recent month, showing renewal rate %,
 *    renewed count, and expired count.
 *
 * 3. Subscription lifecycle table — filterable, paginated table of per-VIN
 *    subscription records.
 *    Columns: VIN, Plan, Market, Status (badge), Start Date, Renewal Date,
 *             Auto-Renew.
 *
 * 4. Cross-link to Rate Plans for plan pricing detail.
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value rendered in JSX is passed through ProvenanceField.
 * Status badge rendering uses assertProvenance + reading `.value` in a conditional —
 * permitted by spec D4 (non-display access).
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useCallback, useMemo } from "react";

import KpiCard from "../../commons/KpiCard";
import KpiCardGrid from "../../commons/KpiCardGrid";
import ProvenanceField from "../../commons/ProvenanceField";
import type {} from "../../commons/tableStates";
import { useConnectedServicesCollection } from "../../commons/useConnectedServicesCollection";
import {
  getHeaderCounterText,
  getTextFilterCounterText,
} from "../../commons/tableI18n";
import { assertProvenance } from "../../../types";

import {
  CONNECTIVITY_PLANS_FIXTURE,
  type RenewalTrendPoint,
  type SubscriptionRow,
  type SubscriptionStatus,
} from "./connectivityPlans.fixture";

// ── Display label maps (not fixture data — kept in the screen, not the fixture) ──

const SUBSCRIPTION_STATUS_LABELS: Record<SubscriptionStatus, string> = {
  active: "Active",
  expired: "Expired",
  pending_renewal: "Pending Renewal",
  cancelled: "Cancelled",
};

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-connectivity-plans-entitlement-table";

const STATUS_BADGE_COLORS: Record<
  SubscriptionStatus,
  "green" | "red" | "severity-medium" | "grey" | "blue"
> = {
  active: "green",
  expired: "red",
  pending_renewal: "severity-medium",
  cancelled: "grey",
};

// ── Column definitions ────────────────────────────────────────────────────────

function buildSubscriptionColumnDefinitions(): import("@cloudscape-design/components/table").TableProps.ColumnDefinition<SubscriptionRow>[] {
  return [
    {
      id: "vin",
      header: "VIN",
      cell: (item) => (
        <ProvenanceField field={item.vin} label="subscription_vin" />
      ),
    },
    {
      id: "planName",
      header: "Plan",
      cell: (item) => (
        <ProvenanceField field={item.planName} label="subscription_plan_name" />
      ),
      sortingField: "planName",
    },
    {
      id: "market",
      header: "Market",
      cell: (item) => (
        <ProvenanceField field={item.market} label="subscription_market" />
      ),
      sortingField: "market",
    },
    {
      id: "status",
      header: "Status",
      cell: (item) => {
        assertProvenance(item.status, "subscription_status");
        if (item.status.provenance === "absent") {
          return <Box color="text-status-inactive"><em>—</em></Box>;
        }
        const status = item.status.value;
        if (status === null) return null;
        return (
          <SpaceBetween size="xs" direction="horizontal">
            <Badge color={STATUS_BADGE_COLORS[status]}>
              {SUBSCRIPTION_STATUS_LABELS[status]}
            </Badge>
          </SpaceBetween>
        );
      },
    },
    {
      id: "startDate",
      header: "Start Date",
      cell: (item) => (
        <ProvenanceField field={item.startDate} label="subscription_start_date" />
      ),
      sortingField: "startDate",
    },
    {
      id: "renewalDate",
      header: "Renewal Date",
      cell: (item) => (
        <ProvenanceField field={item.renewalDate} label="subscription_renewal_date" />
      ),
      sortingField: "renewalDate",
    },
    {
      id: "autoRenew",
      header: "Auto-Renew",
      cell: (item) => {
        assertProvenance(item.autoRenew, "subscription_auto_renew");
        if (item.autoRenew.provenance === "absent") {
          return <Box color="text-status-inactive"><em>—</em></Box>;
        }
        const enabled = item.autoRenew.value;
        if (enabled === null) return null;
        return (
          <SpaceBetween size="xs" direction="horizontal">
            <Badge color={enabled ? "green" : "grey"}>{enabled ? "Yes" : "No"}</Badge>
          </SpaceBetween>
        );
      },
    },
  ];
}

function buildTrendColumnDefinitions(): import("@cloudscape-design/components/table").TableProps.ColumnDefinition<RenewalTrendPoint>[] {
  return [
    {
      id: "month",
      header: "Month",
      cell: (item) => (
        <ProvenanceField field={item.month} label="trend_month" />
      ),
      sortingField: "month",
    },
    {
      id: "renewalRate",
      header: "Renewal Rate",
      cell: (item) => (
        <ProvenanceField
          field={item.renewalRate}
          label="trend_renewal_rate"
          render={(v) => `${String(v)}%`}
        />
      ),
    },
    {
      id: "renewedCount",
      header: "Renewed",
      cell: (item) => (
        <ProvenanceField
          field={item.renewedCount}
          label="trend_renewed_count"
          render={(v) => v.toLocaleString()}
        />
      ),
    },
    {
      id: "expiredCount",
      header: "Expired",
      cell: (item) => (
        <ProvenanceField
          field={item.expiredCount}
          label="trend_expired_count"
          render={(v) => v.toLocaleString()}
        />
      ),
    },
  ];
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * ConnectivityPlansView renders:
 * - Lifecycle KPI strip (active, pending renewal, expired, renewal rate).
 * - Renewal rate trend table (last 6 months).
 * - Subscription lifecycle table (per-VIN status).
 * - Cross-link to Rate Plans for plan pricing detail.
 */
const ConnectivityPlansView: React.FC = () => {
  // ── KPI derivations ───────────────────────────────────────────────────────

  const activeCount = CONNECTIVITY_PLANS_FIXTURE.subscriptions.filter(
    (s) => s.status.value === "active",
  ).length;

  const pendingCount = CONNECTIVITY_PLANS_FIXTURE.subscriptions.filter(
    (s) => s.status.value === "pending_renewal",
  ).length;

  const expiredCount = CONNECTIVITY_PLANS_FIXTURE.subscriptions.filter(
    (s) => s.status.value === "expired",
  ).length;

  // Most recent renewal rate from the trend (last entry in the array).
  const latestTrend =
    CONNECTIVITY_PLANS_FIXTURE.renewalTrend[
      CONNECTIVITY_PLANS_FIXTURE.renewalTrend.length - 1
    ];
  const latestRenewalRate = latestTrend?.renewalRate.value ?? 0;

  // ── Subscription collection ───────────────────────────────────────────────

  const handleClearSubFilter = useCallback(() => {
    subCollection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const subCollection = useConnectedServicesCollection<SubscriptionRow>(
    CONNECTIVITY_PLANS_FIXTURE.subscriptions,
    {
      resourceName: "subscriptions",
      pageSize: 20,
      onClearFilter: handleClearSubFilter,
    },
  );

  const {
    items: subItems,
    filteredItemsCount: subFilteredCount,
    collectionProps: subCollectionProps,
    filterProps: subFilterProps,
    paginationProps: subPaginationProps,
  } = subCollection;

  // ── Trend collection ──────────────────────────────────────────────────────

  const handleClearTrendFilter = useCallback(() => {
    trendCollection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const trendCollection = useConnectedServicesCollection<RenewalTrendPoint>(
    CONNECTIVITY_PLANS_FIXTURE.renewalTrend,
    {
      resourceName: "trend months",
      pageSize: 12,
      onClearFilter: handleClearTrendFilter,
    },
  );

  const {
    items: trendItems,
    filteredItemsCount: trendFilteredCount,
    collectionProps: trendCollectionProps,
    filterProps: trendFilterProps,
    paginationProps: trendPaginationProps,
  } = trendCollection;

  // ── Column definitions ────────────────────────────────────────────────────

  const subColumnDefs = useMemo(() => buildSubscriptionColumnDefinitions(), []);
  const trendColumnDefs = useMemo(() => buildTrendColumnDefinitions(), []);

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registry-completeness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="connectivity-plans-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {/* Lifecycle KPI strip */}
      <KpiCardGrid>
        <KpiCard
          label="Active"
          value={String(activeCount)}
          color="text-status-success"
          captions={["Subscriptions currently active"]}
        />
        <KpiCard
          label="Pending Renewal"
          value={String(pendingCount)}
          color={pendingCount > 0 ? "text-status-warning" : "text-status-success"}
          captions={["Subscriptions due for renewal"]}
        />
        <KpiCard
          label="Expired"
          value={String(expiredCount)}
          color={expiredCount > 0 ? "text-status-error" : "text-status-success"}
          captions={["Subscriptions that have lapsed"]}
        />
        <KpiCard
          label="Renewal Rate"
          value={`${String(latestRenewalRate)}%`}
          color={latestRenewalRate >= 90 ? "text-status-success" : "text-status-warning"}
          captions={[`Most recent month (${latestTrend?.month.value ?? "—"})`]}
        />
      </KpiCardGrid>

      {/* Cross-link to Rate Plans for pricing detail */}
      <Box data-testid="rate-plans-link-container">
        For plan pricing, allowances, and billing details, see{" "}
        <Link
          href="/connectivity/rate-plans"
          data-testid="rate-plans-cross-link"
        >
          Rate Plans
        </Link>
        .
      </Box>

      {/* Renewal rate trend table */}
      <Table
        {...trendCollectionProps}
        data-testid="renewal-trend-table"
        columnDefinitions={trendColumnDefs}
        items={trendItems}
        loadingText="Loading renewal trend"
        header={
          <Header
            counter={getHeaderCounterText(
              trendFilteredCount,
              CONNECTIVITY_PLANS_FIXTURE.renewalTrend.length,
            )}
            data-testid="renewal-trend-table-header"
          >
            Renewal Rate Trend
          </Header>
        }
        filter={
          <TextFilter
            {...trendFilterProps}
            filteringPlaceholder="Find months"
            countText={getTextFilterCounterText(
              trendFilteredCount ?? CONNECTIVITY_PLANS_FIXTURE.renewalTrend.length,
              CONNECTIVITY_PLANS_FIXTURE.renewalTrend.length,
            )}
          />
        }
        pagination={<Pagination {...trendPaginationProps} />}
      />

      {/* Subscription lifecycle table */}
      <Table
        {...subCollectionProps}
        data-testid="connectivity-plans-table"
        columnDefinitions={subColumnDefs}
        items={subItems}
        loadingText="Loading subscriptions"
        header={
          <Header
            counter={getHeaderCounterText(
              subFilteredCount,
              CONNECTIVITY_PLANS_FIXTURE.subscriptions.length,
            )}
            description="Active, expired, and pending-renewal subscriptions per vehicle."
            data-testid="connectivity-plans-table-header"
          >
            Subscription Lifecycle
          </Header>
        }
        filter={
          <TextFilter
            {...subFilterProps}
            filteringPlaceholder="Find subscriptions"
            countText={getTextFilterCounterText(
              subFilteredCount ?? CONNECTIVITY_PLANS_FIXTURE.subscriptions.length,
              CONNECTIVITY_PLANS_FIXTURE.subscriptions.length,
            )}
          />
        }
        pagination={<Pagination {...subPaginationProps} />}
      />
    </SpaceBetween>
  );
};

export default ConnectivityPlansView;
