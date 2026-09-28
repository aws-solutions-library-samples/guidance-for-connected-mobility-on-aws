// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DataProductDetailView — one data product's spec, signals, subscribers,
 * and (subscriber-lens) subscribe CTA.
 *
 * STUB pass — issue `2026-09-14-cs-portal-persona-lenses-stub`.
 * All data mocked. No backend calls.
 *
 * Route: `/sales/data-products/:productId` (parameterized, not in nav —
 * reached by clicking a row in DataProductsView).
 *
 * ## Tabs
 *
 *   - Spec        — product_id, name, data category, delivery profile, source,
 *                   schema version. What the product IS.
 *   - Signals     — the atomic channels this product bundles. Middle leg of the
 *                   Signal → Product → Subscription triangle.
 *   - Subscribers — every consumer holding an active subscription to this product.
 *                   OEM-lens view of the Product → Subscription edge.
 *   - Subscribe   — subscriber-lens CTA to create a new subscription. Stubbed;
 *                   wired version calls POST /subscriptions.
 */

import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import Tabs from "@cloudscape-design/components/tabs";
import React from "react";
import { useNavigate, useParams } from "react-router-dom";

// ── Constants ─────────────────────────────────────────────────────────────────

const SETTLE_MARKER = "cs-settle-data-product-detail-tabs";

// ── Mock data ─────────────────────────────────────────────────────────────────

interface StubProductDetail {
  product_id: string;
  name: string;
  description: string;
  data_category: string;
  delivery_frequency: string;
  delivery_fidelity: string;
  source: string;
  schema_version: string;
  authored_at: string;
  requires_tc_version: string;
}

const STUB_PRODUCT: StubProductDetail = {
  product_id: "telemetry-hifi-v1",
  name: "High-Fidelity Telemetry v1",
  description:
    "Full vehicle telemetry stream at hourly cadence: powertrain, chassis, telematics. Suitable for fleet analytics, insurance telematics, and predictive maintenance.",
  data_category: "telemetry",
  delivery_frequency: "hourly",
  delivery_fidelity: "high",
  source: "telemetry",
  schema_version: "v1.2.0",
  authored_at: "2026-06-14T09:00:00Z",
  requires_tc_version: "tc-v2.1 (deferred to v1.1)",
};

interface StubProductSignal {
  signal_id: string;
  name: string;
  unit: string;
  source_system: string;
}

const STUB_SIGNALS: StubProductSignal[] = [
  { signal_id: "vehicle_speed", name: "Vehicle Speed", unit: "km/h", source_system: "powertrain" },
  { signal_id: "engine_rpm", name: "Engine RPM", unit: "rpm", source_system: "powertrain" },
  { signal_id: "fuel_level_pct", name: "Fuel Level", unit: "%", source_system: "powertrain" },
  { signal_id: "battery_soc_pct", name: "Battery State of Charge", unit: "%", source_system: "hv-battery" },
  { signal_id: "tire_pressure_fl", name: "Tire Pressure Front-Left", unit: "kPa", source_system: "chassis" },
  { signal_id: "tire_pressure_fr", name: "Tire Pressure Front-Right", unit: "kPa", source_system: "chassis" },
  { signal_id: "gps_lat", name: "GPS Latitude", unit: "deg", source_system: "telematics" },
  { signal_id: "gps_lon", name: "GPS Longitude", unit: "deg", source_system: "telematics" },
];

interface StubProductSubscriber {
  consumer_id: string;
  alias: string;
  organization: string;
  vin_count: number;
  active_since: string;
}

const STUB_SUBSCRIBERS: StubProductSubscriber[] = [
  {
    consumer_id: "cust-01H8XYZ",
    alias: "acme-fleet-analytics",
    organization: "Acme Fleet Analytics",
    vin_count: 4,
    active_since: "2026-08-14",
  },
  {
    consumer_id: "cust-01H9ABC",
    alias: "northwind-insurance",
    organization: "Northwind Insurance Group",
    vin_count: 80,
    active_since: "2026-07-02",
  },
  {
    consumer_id: "cust-01HCABC",
    alias: "wayfarer-logistics",
    organization: "Wayfarer Logistics",
    vin_count: 512,
    active_since: "2026-05-11",
  },
];

// ── Tab content ───────────────────────────────────────────────────────────────

const SpecTab: React.FC<{ p: StubProductDetail }> = ({ p }) => (
  <Container header={<Header variant="h2">Product specification</Header>}>
    <ColumnLayout columns={2} variant="text-grid">
      <KeyValuePairs
        columns={1}
        items={[
          { label: "Product ID", value: p.product_id },
          { label: "Name", value: p.name },
          { label: "Description", value: p.description },
          { label: "Schema version", value: p.schema_version },
        ]}
      />
      <KeyValuePairs
        columns={1}
        items={[
          { label: "Data category", value: p.data_category },
          {
            label: "Delivery profile",
            value: `${p.delivery_frequency} · ${p.delivery_fidelity} fidelity`,
          },
          { label: "Source", value: p.source },
          { label: "Authored", value: p.authored_at },
          { label: "T&C required", value: <Badge color="grey">{p.requires_tc_version}</Badge> },
        ]}
      />
    </ColumnLayout>
  </Container>
);

const SignalsTab: React.FC<{ signals: StubProductSignal[] }> = ({ signals }) => {
  const navigate = useNavigate();
  return (
    <Table
      variant="container"
      header={
        <Header
          counter={`(${signals.length})`}
          description="Atomic signals this product bundles. Click a signal to see its spec and which other products include it."
          actions={
            <SpaceBetween size="xs" direction="horizontal">
              <Button variant="normal" disabled>
                Remove signal (stub)
              </Button>
              <Button variant="primary" disabled>
                Add signal (stub)
              </Button>
            </SpaceBetween>
          }
        >
          Signals in this product
        </Header>
      }
      columnDefinitions={[
        {
          id: "signal_id",
          header: "Signal",
          cell: (s: StubProductSignal) => (
            <Link
              onFollow={(e) => {
                e.preventDefault();
                void navigate(`/data-model/signals/${s.signal_id}`);
              }}
              href={`/data-model/signals/${s.signal_id}`}
            >
              {s.signal_id}
            </Link>
          ),
        },
        { id: "name", header: "Name", cell: (s: StubProductSignal) => s.name },
        { id: "unit", header: "Unit", cell: (s: StubProductSignal) => s.unit },
        {
          id: "source_system",
          header: "System",
          cell: (s: StubProductSignal) => <Badge color="grey">{s.source_system}</Badge>,
        },
      ]}
      items={signals}
    />
  );
};

const SubscribersTab: React.FC<{
  productId: string;
  subscribers: StubProductSubscriber[];
}> = ({ productId, subscribers }) => {
  const navigate = useNavigate();
  return (
    <SpaceBetween size="l">
      <Table
        variant="container"
        header={
          <Header
            counter={`(${subscribers.length})`}
            description={`Every 3P subscriber holding an active subscription to ${productId}.`}
          >
            Subscribers to this product
          </Header>
        }
        empty={
          <Box textAlign="center" padding={{ vertical: "l" }}>
            <b>No active subscribers.</b>
          </Box>
        }
        columnDefinitions={[
          {
            id: "alias",
            header: "Subscriber",
            cell: (row: StubProductSubscriber) => (
              <Link
                onFollow={(e) => {
                  e.preventDefault();
                  void navigate(`/sales/subscribers/${row.consumer_id}`);
                }}
                href={`/sales/subscribers/${row.consumer_id}`}
              >
                {row.alias}
              </Link>
            ),
          },
          {
            id: "organization",
            header: "Organization",
            cell: (row: StubProductSubscriber) => row.organization,
          },
          {
            id: "vin_count",
            header: "VINs",
            cell: (row: StubProductSubscriber) => row.vin_count.toLocaleString(),
          },
          {
            id: "active_since",
            header: "Active since",
            cell: (row: StubProductSubscriber) => row.active_since,
          },
        ]}
        items={subscribers}
      />

      <Box>
        <Link
          onFollow={(e) => {
            e.preventDefault();
            void navigate(`/sales/subscribers?product_id=${productId}`);
          }}
          href={`/sales/subscribers?product_id=${productId}`}
        >
          Open Subscribers roster filtered to this product →
        </Link>
      </Box>
    </SpaceBetween>
  );
};

const SubscribeTab: React.FC<{ productId: string }> = ({ productId }) => (
  <Container header={<Header variant="h2">Subscribe to this product</Header>}>
    <SpaceBetween size="m">
      <Alert type="info">
        Wired version will create a new subscription (`POST /subscriptions`) and redirect to the
        new subscription's detail page. This tab is the subscriber-lens entry point to the
        product catalog.
      </Alert>
      <ColumnLayout columns={2} variant="text-grid">
        <KeyValuePairs
          columns={1}
          items={[
            { label: "Product", value: productId },
            {
              label: "T&C acceptance",
              value: <Badge color="grey">click-through modal (v1.1)</Badge>,
            },
          ]}
        />
        <KeyValuePairs
          columns={1}
          items={[
            {
              label: "Initial VIN scope",
              value: "empty — add VINs from the Vehicles tab after subscribing",
            },
            {
              label: "Billing",
              value: <Badge color="grey">deferred to v1.1</Badge>,
            },
          ]}
        />
      </ColumnLayout>
      <Button variant="primary" disabled>
        Create subscription (stub)
      </Button>
    </SpaceBetween>
  </Container>
);

// ── Component ─────────────────────────────────────────────────────────────────

const DataProductDetailView: React.FC = () => {
  const { productId } = useParams<{ productId: string }>();

  const p = { ...STUB_PRODUCT, product_id: productId ?? STUB_PRODUCT.product_id };

  return (
    <SpaceBetween size="l">
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="data-product-detail-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      <Tabs
        data-testid="data-product-detail-tabs"
        tabs={[
          { id: "spec", label: "Spec", content: <SpecTab p={p} /> },
          {
            id: "signals",
            label: `Signals (${STUB_SIGNALS.length})`,
            content: <SignalsTab signals={STUB_SIGNALS} />,
          },
          {
            id: "subscribers",
            label: `Subscribers (${STUB_SUBSCRIBERS.length})`,
            content: (
              <SubscribersTab productId={p.product_id} subscribers={STUB_SUBSCRIBERS} />
            ),
          },
          {
            id: "subscribe",
            label: "Subscribe",
            content: <SubscribeTab productId={p.product_id} />,
          },
        ]}
      />
    </SpaceBetween>
  );
};

export default DataProductDetailView;
