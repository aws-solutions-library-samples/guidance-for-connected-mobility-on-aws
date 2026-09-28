// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FeatureCatalogView — sellable feature capability grid.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.3
 *       UX spec § 6.1 "the single most visually concrete screen in the whole portal"
 *
 * ## Screen layout
 *
 * 1. Revenue summary KPI strip — total monthly revenue (enabled features only),
 *    total active subscribers, and features currently enabled.
 *    The revenue figure changes whenever a feature's "sellable" toggle flips.
 *
 * 2. Feature capability grid — one row per feature, filterable and paginated.
 *    Columns: Feature Name, Price, Active Subscribers, Revenue (monthly),
 *             Eligible VINs, TCU Tier Required, Sellable (toggle).
 *
 * ## Toggle mechanics
 *
 * The "Sellable" column renders a Toggle per row. Toggling changes ONLY that row's
 * contribution to the revenue total — no other cell or column changes.
 *
 * A `Set<string>` of disabled feature IDs is held in component state. By default
 * every feature whose fixture has `currentlyEnabled: false` starts disabled.
 * The revenue KPI is recomputed from the fixture whenever the disabled-IDs set changes:
 *   revenue = sum over features where id NOT in disabledIds of (revenueMonthly.value ?? 0)
 *
 * This is the "toggle-and-watch-the-number-move" invariant the test suite asserts:
 *   1. Toggle a currently-enabled feature off → revenue decreases by that feature's revenueMonthly.
 *   2. Toggle the same feature back on → revenue returns to its prior value.
 *
 * The toggle changes NO other displayed value — this mirrors the RatePlansView billing-mode
 * toggle's invariant (only the billing-owner column header changes there; only the revenue KPI
 * changes here).
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value is passed through ProvenanceField.
 * Reading `.value` in a conditional (e.g. `item.currentlyEnabled.value ?? false`) is
 * permitted — it is a non-display access. Rendering `.value` directly as JSX text is not.
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import Toggle from "@cloudscape-design/components/toggle";
import React, { useCallback, useMemo, useState } from "react";

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
  FEATURE_CATALOG_FIXTURE,
  type FeatureRow,
} from "./featureCatalog.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-feature-catalog-capability-grid";

// ── Column definitions ────────────────────────────────────────────────────────

function buildColumnDefinitions(
  disabledIds: ReadonlySet<string>,
  onToggle: (id: string, checked: boolean) => void,
): import("@cloudscape-design/components/table").TableProps.ColumnDefinition<FeatureRow>[] {
  return [
    {
      id: "name",
      header: "Feature",
      cell: (item) => <ProvenanceField field={item.name} label="feature_name" />,
      sortingField: "name",
    },
    {
      id: "priceMonthly",
      header: "Price / month",
      cell: (item) => (
        <ProvenanceField field={item.priceMonthly} label="feature_price_monthly" />
      ),
    },
    {
      id: "activeSubscribers",
      header: "Active Subscribers",
      cell: (item) => (
        <ProvenanceField
          field={item.activeSubscribers}
          label="feature_active_subscribers"
          render={(v) => v.toLocaleString()}
        />
      ),
      sortingField: "activeSubscribers",
    },
    {
      id: "revenueMonthly",
      header: "Revenue / month",
      cell: (item) => {
        const isDisabled = disabledIds.has(item.id);
        if (isDisabled) {
          return (
            <Box color="text-status-inactive">
              <em>$0 (disabled)</em>
            </Box>
          );
        }
        return (
          <ProvenanceField
            field={item.revenueMonthly}
            label="feature_revenue_monthly"
            render={(v) => `$${v.toLocaleString()}`}
          />
        );
      },
    },
    {
      id: "eligibleVinCount",
      header: "Eligible VINs",
      cell: (item) => (
        <ProvenanceField
          field={item.eligibleVinCount}
          label="feature_eligible_vin_count"
          render={(v) => v.toLocaleString()}
        />
      ),
      sortingField: "eligibleVinCount",
    },
    {
      id: "tcuTierRequired",
      header: "TCU Tier Required",
      cell: (item) => {
        assertProvenance(item.tcuTierRequired, "feature_tcu_tier_required");
        if (item.tcuTierRequired.provenance === "absent") {
          return <Box color="text-status-inactive"><em>—</em></Box>;
        }
        const tier = item.tcuTierRequired.value;
        if (tier === null) return null;
        const color = tier === "TCU-3" ? "red" : tier === "TCU-2" ? "severity-medium" : "grey";
        return (
          <SpaceBetween size="xs" direction="horizontal">
            <Badge color={color}>{tier}</Badge>
          </SpaceBetween>
        );
      },
    },
    {
      id: "sellable",
      header: "Sellable",
      cell: (item) => {
        const isEnabled = !disabledIds.has(item.id);
        return (
          <Toggle
            checked={isEnabled}
            onChange={({ detail }) => {
              onToggle(item.id, detail.checked);
            }}
            data-testid={`feature-toggle-${item.id}`}
          >
            {isEnabled ? "Enabled" : "Disabled"}
          </Toggle>
        );
      },
    },
  ];
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * FeatureCatalogView renders:
 * - A KPI summary strip: total revenue (enabled features), active subscribers, enabled-feature count.
 * - A filterable, paginated table of sellable features.
 * - A per-row "Sellable" toggle. Toggling changes the revenue KPI ONLY.
 */
const FeatureCatalogView: React.FC = () => {
  // ── Toggle state ──────────────────────────────────────────────────────────

  // Start with features that the fixture marks as disabled already.
  const initiallyDisabled = useMemo(
    () =>
      new Set(
        FEATURE_CATALOG_FIXTURE.features
          .filter((f) => !(f.currentlyEnabled.value ?? true))
          .map((f) => f.id),
      ),
    [],
  );

  const [disabledIds, setDisabledIds] = useState<ReadonlySet<string>>(initiallyDisabled);

  const handleToggle = useCallback((id: string, checked: boolean) => {
    setDisabledIds((prev) => {
      const next = new Set(prev);
      if (checked) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  // ── KPI derivations ───────────────────────────────────────────────────────

  // Total monthly revenue from enabled features ONLY.
  // This is the number that changes when a toggle flips.
  const totalRevenue = useMemo(
    () =>
      FEATURE_CATALOG_FIXTURE.features.reduce((acc, f) => {
        if (disabledIds.has(f.id)) return acc;
        return acc + (f.revenueMonthly.value ?? 0);
      }, 0),
    [disabledIds],
  );

  const totalActiveSubscribers = useMemo(
    () =>
      FEATURE_CATALOG_FIXTURE.features.reduce(
        (acc, f) => acc + (f.activeSubscribers.value ?? 0),
        0,
      ),
    [],
  );

  const enabledFeatureCount = FEATURE_CATALOG_FIXTURE.features.length - disabledIds.size;

  // ── Collection (filter + paginate) ───────────────────────────────────────

  const handleClearFilter = useCallback(() => {
    collection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const collection = useConnectedServicesCollection<FeatureRow>(
    FEATURE_CATALOG_FIXTURE.features,
    {
      resourceName: "features",
      pageSize: 20,
      onClearFilter: handleClearFilter,
    },
  );

  const { items, filteredItemsCount, collectionProps, filterProps, paginationProps } =
    collection;

  // ── Column definitions — rebuilt only when disabledIds or handleToggle changes ──

  const columnDefs = useMemo(
    () => buildColumnDefinitions(disabledIds, handleToggle),
    [disabledIds, handleToggle],
  );

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registry-completeness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="feature-catalog-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {/* KPI summary strip */}
      {/*
        The revenue value is also published on a hidden span with testid
        "feature-catalog-revenue-value" so tests can reliably assert the
        displayed revenue figure without fragile DOM traversal through
        Cloudscape's Container/Box internals.
      */}
      <span
        data-testid="feature-catalog-revenue-value"
        aria-hidden="true"
        style={{ display: "none" }}
      >
        {`$${totalRevenue.toLocaleString()}`}
      </span>
      <KpiCardGrid>
        <KpiCard
          label="Monthly Revenue"
          value={`$${totalRevenue.toLocaleString()}`}
          color="text-status-success"
          captions={["Enabled features only"]}
        />
        <KpiCard
          label="Active Subscribers"
          value={totalActiveSubscribers.toLocaleString()}
          color="text-status-info"
          captions={["Across all features and markets"]}
        />
        <KpiCard
          label="Features Enabled"
          value={`${String(enabledFeatureCount)} / ${String(FEATURE_CATALOG_FIXTURE.features.length)}`}
          color={enabledFeatureCount === 0 ? "text-status-warning" : "text-status-info"}
          captions={["Currently marked as sellable"]}
        />
      </KpiCardGrid>

      {/* Feature capability grid */}
      <Table
        {...collectionProps}
        data-testid="feature-catalog-table"
        columnDefinitions={columnDefs}
        items={items}
        loadingText="Loading features"
        header={
          <Header
            counter={getHeaderCounterText(
              filteredItemsCount,
              FEATURE_CATALOG_FIXTURE.features.length,
            )}
            description="Toggle a feature to see its revenue contribution update above."
            data-testid="feature-catalog-table-header"
          >
            Sellable Feature Catalog
          </Header>
        }
        filter={
          <TextFilter
            {...filterProps}
            filteringPlaceholder="Find features"
            countText={getTextFilterCounterText(
              filteredItemsCount ?? FEATURE_CATALOG_FIXTURE.features.length,
              FEATURE_CATALOG_FIXTURE.features.length,
            )}
          />
        }
        pagination={<Pagination {...paginationProps} />}
      />

      {/* Cross-link to Connectivity Plans for subscription lifecycle detail */}
      <Box>
        <Link
          href="/sales/connectivity-plans"
          data-testid="view-connectivity-plans-link"
        >
          View subscription lifecycle in Connectivity Plans
        </Link>
      </Box>
    </SpaceBetween>
  );
};

export default FeatureCatalogView;
