// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * GrantsView — OEM operator's view of which VINs are marked available for
 * enrollment, and by which subscriber (if any) each is currently held.
 *
 * STUB pass — issue `2026-09-14-cs-portal-persona-lenses-stub`.
 * All data mocked. No backend calls.
 *
 * Route: `/sales/grants` (in nav under Sales & Subscriptions).
 *
 * ## Model
 *
 * The `VehicleAvailability` DynamoDB table is a **grant registry**: a VIN listed
 * there is eligible for a subscriber to enroll into their own subscription.
 * `admin_mark_available` writes rows; nothing reads them from a UI today. This
 * view fills that gap so an OEM operator can see:
 *
 *   - Which VINs are available for enrollment.
 *   - Whether a subscriber has picked them up (enrolled) or they're unclaimed.
 *   - Since when each grant has been open.
 *   - Which product family the grant is scoped to (if any — some grants are
 *     product-agnostic; product-specific grants come in v1.1).
 */

import Badge from "@cloudscape-design/components/badge";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useMemo, useState } from "react";

// ── Constants ─────────────────────────────────────────────────────────────────

const SETTLE_MARKER = "cs-settle-grants-availability-table";

// ── Mock data ─────────────────────────────────────────────────────────────────

interface StubGrantRow {
  vinCode: string;
  vehicleId: string;
  granted_at: string;
  granted_by: string;
  grantState: "available" | "enrolled" | "revoked";
  held_by: string | null;
  telemetry_signal: "green" | "grey";
}

const STUB_GRANTS: StubGrantRow[] = [
  {
    vinCode: "1FA6P8CF1M5100001",
    vehicleId: "veh-001",
    granted_at: "2026-08-14T09:00:00Z",
    granted_by: "operator.alice",
    grantState: "enrolled",
    held_by: "acme-fleet-analytics",
    telemetry_signal: "green",
  },
  {
    vinCode: "1FA6P8CF1M5100002",
    vehicleId: "veh-002",
    granted_at: "2026-08-14T09:00:00Z",
    granted_by: "operator.alice",
    grantState: "enrolled",
    held_by: "acme-fleet-analytics",
    telemetry_signal: "green",
  },
  {
    vinCode: "1FA6P8CF1M5100100",
    vehicleId: "veh-100",
    granted_at: "2026-07-02T10:15:00Z",
    granted_by: "operator.bob",
    grantState: "enrolled",
    held_by: "northwind-insurance",
    telemetry_signal: "green",
  },
  {
    vinCode: "MRD1TIRE0N5100014",
    vehicleId: "veh-114",
    granted_at: "2026-09-01T14:00:00Z",
    granted_by: "operator.chloe",
    grantState: "enrolled",
    held_by: "meridian-research",
    telemetry_signal: "green",
  },
  {
    vinCode: "MRD1TIRE0N5100015",
    vehicleId: "veh-115",
    granted_at: "2026-09-01T14:00:00Z",
    granted_by: "operator.chloe",
    grantState: "available",
    held_by: null,
    telemetry_signal: "green",
  },
  {
    vinCode: "MRD1TIRE0N5100016",
    vehicleId: "veh-116",
    granted_at: "2026-09-02T11:30:00Z",
    granted_by: "operator.chloe",
    grantState: "available",
    held_by: null,
    telemetry_signal: "green",
  },
  {
    vinCode: "1FA6P8CF1M5100020",
    vehicleId: "veh-020",
    granted_at: "2026-09-05T14:45:00Z",
    granted_by: "operator.alice",
    grantState: "available",
    held_by: null,
    telemetry_signal: "green",
  },
  {
    vinCode: "1FA6P8CF1M5100021",
    vehicleId: "veh-021",
    granted_at: "2026-09-08T08:15:00Z",
    granted_by: "operator.alice",
    grantState: "available",
    held_by: null,
    telemetry_signal: "green",
  },
  {
    vinCode: "1FA6P8CF1M5100099",
    vehicleId: "veh-099",
    granted_at: "2026-06-01T12:00:00Z",
    granted_by: "operator.bob",
    grantState: "revoked",
    held_by: null,
    telemetry_signal: "grey",
  },
];

const STATUS_COLOR: Record<StubGrantRow["grantState"], "green" | "blue" | "grey"> = {
  available: "blue",
  enrolled: "green",
  revoked: "grey",
};

// ── Component ─────────────────────────────────────────────────────────────────

const GrantsView: React.FC = () => {
  const [filterText, setFilterText] = useState("");

  const filtered = useMemo(() => {
    const q = filterText.trim().toLowerCase();
    if (!q) return STUB_GRANTS;
    return STUB_GRANTS.filter(
      (g) =>
        g.vinCode.toLowerCase().includes(q) ||
        g.vehicleId.toLowerCase().includes(q) ||
        (g.held_by ?? "").toLowerCase().includes(q) ||
        g.granted_by.toLowerCase().includes(q) ||
        g.grantState.toLowerCase().includes(q),
    );
  }, [filterText]);

  const availableCount = STUB_GRANTS.filter((g) => g.grantState === "available").length;
  const enrolledCount = STUB_GRANTS.filter((g) => g.grantState === "enrolled").length;

  return (
    <SpaceBetween size="l">
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="grants-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      <Table
        variant="container"
        data-testid="grants-table"
        header={
          <Header
            counter={`(${filtered.length})`}
            description={`Every VIN the OEM has marked available for subscriber enrollment. ${availableCount} unclaimed, ${enrolledCount} currently held by a subscriber.`}
            actions={
              <SpaceBetween size="xs" direction="horizontal">
                <Button variant="normal" disabled>
                  Bulk import (stub)
                </Button>
                <Button variant="primary" disabled>
                  Mark VIN available (stub)
                </Button>
              </SpaceBetween>
            }
          >
            Grants
          </Header>
        }
        filter={
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Filter by VIN, subscriber, or status"
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />
        }
        columnDefinitions={[
          { id: "vin", header: "VIN", cell: (g: StubGrantRow) => g.vinCode },
          { id: "vehicleId", header: "Vehicle ID", cell: (g: StubGrantRow) => g.vehicleId },
          {
            id: "status",
            header: "Status",
            cell: (g: StubGrantRow) => <Badge color={STATUS_COLOR[g.grantState]}>{g.grantState}</Badge>,
          },
          {
            id: "held_by",
            header: "Held by",
            cell: (g: StubGrantRow) => g.held_by ?? "—",
          },
          { id: "granted_at", header: "Granted at", cell: (g: StubGrantRow) => g.granted_at },
          { id: "granted_by", header: "Granted by", cell: (g: StubGrantRow) => g.granted_by },
          {
            id: "signal",
            header: "Telemetry",
            cell: (g: StubGrantRow) => (
              <Badge color={g.telemetry_signal === "green" ? "green" : "grey"}>
                {g.telemetry_signal === "green" ? "live" : "quiet"}
              </Badge>
            ),
          },
        ]}
        items={filtered}
      />
    </SpaceBetween>
  );
};

export default GrantsView;
