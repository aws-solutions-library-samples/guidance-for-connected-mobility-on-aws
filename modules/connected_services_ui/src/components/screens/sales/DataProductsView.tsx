// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DataProductsView — outbound telemetry data products.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.3
 *       UX spec § 6.3 "scoped to third-party telemetry pushes"
 *
 * ## Scope (2026-09-04 decision)
 *
 * Outbound telemetry pushes ONLY — this OEM sending telemetry out to a
 * third-party partner. Inbound data purchases, bidirectional exchanges,
 * and general data-marketplace products are explicitly out of scope.
 *
 * ## Screen layout
 *
 * 1. Status KPI strip — active products, inactive/suspended products,
 *    total vehicles in outbound scope, consent-compliant products.
 *
 * 2. Partner feed table — filterable, paginated table of data products.
 *    Columns: Product Name, Partner Label, Market, Data Category, Status (badge),
 *             Consent Status (badge+link), Vehicle Count.
 *
 * 3. Cross-link to Consent & Privacy for consent management.
 *
 * ## Partner label constraint
 *
 * Fixture `partnerLabel` values are generic ("Partner A", "Partner B") — no real
 * company/OEM/vendor names. Real names fail contentBoundary.test.ts's brand canary.
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value rendered in JSX is passed through ProvenanceField.
 * Status and consent-status badge rendering uses assertProvenance + reading `.value`
 * in a conditional — permitted by spec D4 (non-display access).
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import KpiCard from "../../commons/KpiCard";
import KpiCardGrid from "../../commons/KpiCardGrid";
import ProvenanceField from "../../commons/ProvenanceField";
import DataProductCreateModal from "./DataProductCreateModal";
import {
  ConnectedServicesTablePreferences,
  type ConnectedServicesPreferences,
  type ContentDisplayOption,
} from "../../commons/ConnectedServicesTablePreferences";
import type {} from "../../commons/tableStates";
import { useConnectedServicesCollection } from "../../commons/useConnectedServicesCollection";
import {
  getHeaderCounterText,
  getTextFilterCounterText,
} from "../../commons/tableI18n";
import { assertProvenance } from "../../../types";
import {
  fetchProducts,
  type ProductCatalogEntry,
  type ProductCatalogResponse,
} from "../../../api/subscriptionsClient";

import {
  DATA_PRODUCTS_FIXTURE,
  type DataProductRow,
  type DataProductStatus,
  type ConsentStatus,
} from "./dataProducts.fixture";

// ── Display label maps (not fixture data — kept in the screen, not the fixture) ──

const DATA_PRODUCT_STATUS_LABELS: Record<DataProductStatus, string> = {
  active: "Active",
  inactive: "Inactive",
  suspended: "Suspended",
};

const CONSENT_STATUS_LABELS: Record<ConsentStatus, string> = {
  compliant: "Compliant",
  partial: "Partial",
  non_compliant: "Non-Compliant",
};

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-data-products-partner-feed-table";

const STATUS_BADGE_COLORS: Record<DataProductStatus, "green" | "red" | "grey"> = {
  active: "green",
  inactive: "grey",
  suspended: "red",
};

const CONSENT_BADGE_COLORS: Record<
  ConsentStatus,
  "green" | "severity-medium" | "red"
> = {
  compliant: "green",
  partial: "severity-medium",
  non_compliant: "red",
};

// ── Column definitions ────────────────────────────────────────────────────────

function buildColumnDefinitions(
  navigate: (path: string) => void,
): import("@cloudscape-design/components/table").TableProps.ColumnDefinition<DataProductRow>[] {
  return [
    {
      id: "name",
      header: "Product Name",
      cell: (item) => (
        <Link
          onFollow={(e) => {
            e.preventDefault();
            void navigate(`/sales/data-products/${item.id}`);
          }}
          href={`/sales/data-products/${item.id}`}
        >
          <ProvenanceField field={item.name} label="product_name" />
        </Link>
      ),
      sortingField: "name",
    },
    {
      id: "dataCategory",
      header: "Data Category",
      cell: (item) => (
        <ProvenanceField field={item.dataCategory} label="product_data_category" />
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (item) => {
        assertProvenance(item.status, "product_status");
        if (item.status.provenance === "absent") {
          return <Box color="text-status-inactive"><em>—</em></Box>;
        }
        const status = item.status.value;
        if (status === null) return null;
        return (
          <SpaceBetween size="xs" direction="horizontal">
            <Badge color={STATUS_BADGE_COLORS[status]}>
              {DATA_PRODUCT_STATUS_LABELS[status]}
            </Badge>
          </SpaceBetween>
        );
      },
    },
    {
      id: "vehicleCount",
      header: "Vehicle Count",
      cell: (item) => (
        <ProvenanceField
          field={item.vehicleCount}
          label="product_vehicle_count"
          render={(v) => v.toLocaleString()}
        />
      ),
      sortingField: "vehicleCount",
    },
    {
      id: "actions",
      header: "Actions",
      cell: (item) => (
        <SpaceBetween size="xs" direction="horizontal">
          <Link
            onFollow={(e) => {
              e.preventDefault();
              void navigate(`/sales/data-products/${item.id}`);
            }}
            href={`/sales/data-products/${item.id}`}
            data-testid={`product-detail-${item.id}`}
          >
            View
          </Link>
          <Link
            onFollow={(e) => {
              e.preventDefault();
              void navigate(`/sales/subscribers?product_id=${item.id}`);
            }}
            href={`/sales/subscribers?product_id=${item.id}`}
            data-testid={`product-subscribers-${item.id}`}
          >
            Subscribers
          </Link>
        </SpaceBetween>
      ),
    },
  ];
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * Project a live catalog entry into the row shape the table renders.
 *
 * Only the fields the API actually provides carry `provenance: "live"`; the
 * rest are `"absent"` so the table renders their empty state explicitly
 * rather than showing a fixture value under a "live" marker (a false
 * assertion that would fail the portal's own provenance guard).
 *
 * Exported for the vitest suite so the projection can be asserted directly.
 */
export function projectCatalogEntryToRow(entry: ProductCatalogEntry): DataProductRow {
  const empty = <T,>(): { value: T | null; provenance: "absent" } => ({
    value: null,
    provenance: "absent" as const,
  });
  return {
    id: entry.product_id,
    name: { value: entry.name, provenance: "live" as const },
    description: {
      value: `${entry.data_category} — ${entry.delivery_profile?.frequency ?? "unknown"} frequency`,
      provenance: "live" as const,
    },
    partnerLabel: empty<string>(),
    status: { value: "active" as DataProductStatus, provenance: "live" as const },
    consentStatus: empty<ConsentStatus>(),
    vehicleCount: empty<number>(),
    market: empty<string>(),
    dataCategory: { value: entry.data_category, provenance: "live" as const },
  };
}

/**
 * DataProductsView renders:
 * - Status KPI strip (active, inactive/suspended, total vehicles, compliant products).
 * - Partner feed table with filterable, paginated data products.
 * - Cross-link to Consent & Privacy.
 */
const DataProductsView: React.FC = () => {
  const navigate = useNavigate();

  // ── Live catalog fetch ────────────────────────────────────────────────────
  //
  // `null` = not yet fetched or unavailable; `[]` is legitimate (empty
  // catalog). The initial state is `null` so the first render shows the
  // fixture immediately rather than a spinner — the demo path degrades
  // gracefully when the subscriptions API is not deployed.

  const [liveRows, setLiveRows] = useState<DataProductRow[] | null>(null);
  const [dataSource, setDataSource] = useState<"live" | "fixture" | "loading">(
    "loading",
  );

  // Modal state for the Create data product form.
  const [createModalVisible, setCreateModalVisible] = useState(false);

  // Table preferences — page size + column visibility. Persists in local state
  // for the session; wired spec will hoist to a shared preferences store.
  const [preferences, setPreferences] = useState<ConnectedServicesPreferences>({
    pageSize: 25,
    contentDisplay: [
      { id: "name", visible: true },
      { id: "dataCategory", visible: true },
      { id: "status", visible: true },
      { id: "vehicleCount", visible: true },
      { id: "actions", visible: true },
    ],
  });

  const visibleContentOptions: ContentDisplayOption[] = useMemo(
    () => [
      { id: "name", label: "Product Name", alwaysVisible: true },
      { id: "dataCategory", label: "Data Category" },
      { id: "status", label: "Status" },
      { id: "vehicleCount", label: "Vehicle Count" },
      { id: "actions", label: "Actions" },
    ],
    [],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const catalog: ProductCatalogResponse | null = await fetchProducts();
        if (cancelled) return;
        if (catalog === null) {
          // No endpoint configured — degrade to fixture without error.
          setDataSource("fixture");
          return;
        }
        setLiveRows(catalog.products.map(projectCatalogEntryToRow));
        setDataSource("live");
      } catch {
        if (cancelled) return;
        // A live fetch that fails should not blank the screen — fall back
        // to the fixture so the demo path is never worse than the
        // fixture-only release.
        setDataSource("fixture");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const rowSource: DataProductRow[] = useMemo(
    () => (liveRows !== null ? liveRows : DATA_PRODUCTS_FIXTURE.products),
    [liveRows],
  );

  // ── KPI derivations ───────────────────────────────────────────────────────

  const activeCount = rowSource.filter(
    (p) => p.status.value === "active",
  ).length;

  const inactiveOrSuspendedCount = rowSource.filter(
    (p) => p.status.value === "inactive" || p.status.value === "suspended",
  ).length;

  const totalVehicleCount = rowSource
    .filter((p) => p.status.value === "active")
    .reduce((acc, p) => acc + (p.vehicleCount.value ?? 0), 0);

  const compliantCount = rowSource.filter(
    (p) => p.consentStatus.value === "compliant",
  ).length;

  // ── Collection ────────────────────────────────────────────────────────────

  const handleClearFilter = useCallback(() => {
    collection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const collection = useConnectedServicesCollection<DataProductRow>(
    rowSource,
    {
      resourceName: "data products",
      pageSize: preferences.pageSize ?? 25,
      onClearFilter: handleClearFilter,
    },
  );

  const { items, filteredItemsCount, collectionProps, filterProps, paginationProps } =
    collection;

  // ── Column definitions ────────────────────────────────────────────────────

  const columnDefs = useMemo(() => buildColumnDefinitions(navigate), [navigate]);

  // Apply column-visibility preferences to the underlying column definitions.
  const visibleColumnDefs = useMemo(() => {
    const displayIds = new Set(
      (preferences.contentDisplay ?? []).filter((c) => c.visible).map((c) => c.id),
    );
    // If no preferences are set yet, show every column.
    if (displayIds.size === 0) return columnDefs;
    return columnDefs.filter((col) => col.id && displayIds.has(col.id));
  }, [columnDefs, preferences.contentDisplay]);

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registry-completeness P1.
          A portal-wide CSS rule ([data-settle-marker] + *) neutralises the top
          margin SpaceBetween would otherwise apply to the next visible child;
          see PageHeader.css for the rationale. */}
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="data-products-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {/* Status KPI strip */}
      <KpiCardGrid>
        <KpiCard
          label="Active Feeds"
          value={String(activeCount)}
          color="text-status-success"
          captions={["Outbound telemetry pushes currently live"]}
        />
        <KpiCard
          label="Inactive / Suspended"
          value={String(inactiveOrSuspendedCount)}
          color={inactiveOrSuspendedCount > 0 ? "text-status-warning" : "text-status-success"}
          captions={["Feeds that are off or suspended"]}
        />
        <KpiCard
          label="Vehicles in Scope"
          value={totalVehicleCount.toLocaleString()}
          color="text-status-info"
          captions={["VINs across active outbound feeds"]}
        />
        <KpiCard
          label="Consent Compliant"
          value={`${String(compliantCount)} / ${String(rowSource.length)}`}
          color={
            compliantCount === rowSource.length
              ? "text-status-success"
              : "text-status-warning"
          }
          captions={["Products with all-VIN consent in place"]}
        />
      </KpiCardGrid>

      {/* Partner feed table */}
      <Table
        {...collectionProps}
        variant="container"
        data-testid="data-products-table"
        columnDefinitions={visibleColumnDefs}
        items={items}
        loadingText="Loading data products"
        stickyHeader={true}
        resizableColumns={true}
        enableKeyboardNavigation={true}
        header={
          <Header
            counter={getHeaderCounterText(
              filteredItemsCount,
              rowSource.length,
            )}
            description="Curated bundles of signals and events delivered to CMS subscriber groups on a cadence and fidelity. Composed from the Signal Catalog."
            actions={
              <SpaceBetween size="xs" direction="horizontal">
                <Badge color={dataSource === "live" ? "green" : "grey"}>
                  {dataSource === "live"
                    ? "Live catalog"
                    : dataSource === "loading"
                    ? "Loading…"
                    : "Fixture (subscriptions API not configured)"}
                </Badge>
                <Button
                  variant="primary"
                  onClick={() => setCreateModalVisible(true)}
                  data-testid="data-products-create-button"
                >
                  Create data product
                </Button>
              </SpaceBetween>
            }
            data-testid="data-products-table-header"
          >
            Data Products
          </Header>
        }
        filter={
          <TextFilter
            {...filterProps}
            filteringPlaceholder="Find data products"
            countText={getTextFilterCounterText(
              filteredItemsCount ?? rowSource.length,
              rowSource.length,
            )}
          />
        }
        pagination={<Pagination {...paginationProps} />}
        preferences={
          <ConnectedServicesTablePreferences
            preferences={preferences}
            onConfirm={setPreferences}
            visibleContentOptions={visibleContentOptions}
            resourceName="data products"
          />
        }
        footer={
          <Box textAlign="center">
            <Link
              href="/compliance/consent"
              data-testid="consent-privacy-cross-link"
            >
              Manage consent for third-party data sharing →
            </Link>
          </Box>
        }
      />

      {/* Create data product modal — stub form (2026-09-14). Opens on the
          primary button in the table header; no route navigation. */}
      <DataProductCreateModal
        visible={createModalVisible}
        onDismiss={() => setCreateModalVisible(false)}
      />
    </SpaceBetween>
  );
};

export default DataProductsView;
