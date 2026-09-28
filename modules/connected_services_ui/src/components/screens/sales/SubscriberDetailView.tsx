// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SubscriberDetailView — OEM operator's view of ONE 3P subscriber.
 *
 * STUB pass — issue `2026-09-14-cs-portal-persona-lenses-stub`.
 * All data mocked. No backend calls.
 *
 * Route: `/sales/subscribers/:consumerId` (parameterized, not in nav —
 * reached by clicking a row in SubscribersRosterView).
 *
 * Tabs:
 *   - Overview     — who, contact, product portfolio, quota posture.
 *   - Scope        — every subscription this subscriber holds + the VINs each covers.
 *   - Consumption  — pull history + volume rollup.
 *   - T&C          — placeholder; T&C tracking deferred to v1.1.
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import Tabs from "@cloudscape-design/components/tabs";
import React from "react";
import { useParams } from "react-router-dom";

// ── Constants ─────────────────────────────────────────────────────────────────

const SETTLE_MARKER = "cs-settle-subscriber-detail-tabs";

// ── Mock data ─────────────────────────────────────────────────────────────────

interface StubSubscriberDetail {
  consumer_id: string;
  alias: string;
  organization: string;
  contact_email: string;
  active_since: string;
  quota_state: "ok" | "warning" | "throttled";
  monthly_quota_records: number;
  monthly_used_records: number;
}

const STUB_SUBSCRIBER: StubSubscriberDetail = {
  consumer_id: "cust-01H9ABC",
  alias: "northwind-insurance",
  organization: "Northwind Insurance Group",
  contact_email: "data-ops@northwind-insurance.example",
  active_since: "2026-07-02",
  quota_state: "warning",
  monthly_quota_records: 5_000_000,
  monthly_used_records: 4_120_000,
};

interface StubScopeSubscription {
  subscription_id: string;
  product_id: string;
  state: "active" | "inactive";
  vin_count: number;
  vins: string[];
}

const STUB_SCOPE: StubScopeSubscription[] = [
  {
    subscription_id: "sub_01H9ABCDEFG",
    product_id: "telemetry-hifi-v1",
    state: "active",
    vin_count: 80,
    vins: ["1FA6P8CF1M5100100", "1FA6P8CF1M5100101", "1FA6P8CF1M5100102", "… (77 more)"],
  },
  {
    subscription_id: "sub_01H9XYZLMNO",
    product_id: "diagnostics-v1",
    state: "active",
    vin_count: 42,
    vins: ["1FA6P8CF1M5100200", "1FA6P8CF1M5100201", "… (40 more)"],
  },
];

interface StubConsumptionDay {
  date: string;
  records: number;
  pulls: number;
  throttled_pulls: number;
}

const STUB_CONSUMPTION: StubConsumptionDay[] = [
  { date: "2026-09-14", records: 141_200, pulls: 24, throttled_pulls: 0 },
  { date: "2026-09-13", records: 138_500, pulls: 24, throttled_pulls: 1 },
  { date: "2026-09-12", records: 145_090, pulls: 24, throttled_pulls: 0 },
  { date: "2026-09-11", records: 122_400, pulls: 22, throttled_pulls: 3 },
  { date: "2026-09-10", records: 139_010, pulls: 24, throttled_pulls: 0 },
  { date: "2026-09-09", records: 141_800, pulls: 24, throttled_pulls: 0 },
  { date: "2026-09-08", records: 137_600, pulls: 24, throttled_pulls: 0 },
];

const QUOTA_COLOR: Record<StubSubscriberDetail["quota_state"], "green" | "blue" | "red"> = {
  ok: "green",
  warning: "blue",
  throttled: "red",
};

// ── Tab content ───────────────────────────────────────────────────────────────

const OverviewTab: React.FC<{ s: StubSubscriberDetail }> = ({ s }) => {
  const usedPct = ((s.monthly_used_records / s.monthly_quota_records) * 100).toFixed(1);
  return (
    <Container header={<Header variant="h2">Subscriber details</Header>}>
      <ColumnLayout columns={2} variant="text-grid">
        <KeyValuePairs
          columns={1}
          items={[
            { label: "Consumer ID", value: s.consumer_id },
            { label: "Alias", value: s.alias },
            { label: "Organization", value: s.organization },
            { label: "Contact", value: s.contact_email },
          ]}
        />
        <KeyValuePairs
          columns={1}
          items={[
            { label: "Active since", value: s.active_since },
            {
              label: "Quota state",
              value: <Badge color={QUOTA_COLOR[s.quota_state]}>{s.quota_state}</Badge>,
            },
            {
              label: "This month",
              value: `${s.monthly_used_records.toLocaleString()} / ${s.monthly_quota_records.toLocaleString()} records (${usedPct}%)`,
            },
            {
              label: "T&C acceptance",
              value: <Badge color="grey">deferred to v1.1</Badge>,
            },
          ]}
        />
      </ColumnLayout>
    </Container>
  );
};

const ScopeTab: React.FC<{ scope: StubScopeSubscription[] }> = ({ scope }) => (
  <SpaceBetween size="l">
    {scope.map((sub) => (
      <Table
        key={sub.subscription_id}
        variant="container"
        header={
          <Header
            counter={`(${sub.vin_count} VINs)`}
            description={`Subscription ${sub.subscription_id} — ${sub.product_id}`}
            actions={
              <Button variant="normal" disabled>
                Revoke access (stub)
              </Button>
            }
          >
            <Badge color={sub.state === "active" ? "green" : "grey"}>{sub.state}</Badge>{" "}
            {sub.product_id}
          </Header>
        }
        columnDefinitions={[
          { id: "vin", header: "VIN in scope", cell: (v: string) => v },
        ]}
        items={sub.vins.map((v) => v)}
      />
    ))}
  </SpaceBetween>
);

const ConsumptionTab: React.FC<{ days: StubConsumptionDay[] }> = ({ days }) => (
  <Table
    variant="container"
    header={
      <Header
        counter={`(${days.length} days)`}
        description="Rolling 7-day pull history. Each row aggregates one day's GET /subscriptions/{id}/records activity."
      >
        Consumption history
      </Header>
    }
    columnDefinitions={[
      { id: "date", header: "Date", cell: (d: StubConsumptionDay) => d.date },
      {
        id: "records",
        header: "Records delivered",
        cell: (d: StubConsumptionDay) => d.records.toLocaleString(),
      },
      { id: "pulls", header: "Pulls", cell: (d: StubConsumptionDay) => String(d.pulls) },
      {
        id: "throttled",
        header: "Throttled",
        cell: (d: StubConsumptionDay) =>
          d.throttled_pulls === 0 ? (
            "0"
          ) : (
            <Badge color="red">{d.throttled_pulls}</Badge>
          ),
      },
    ]}
    items={days}
  />
);

const TermsTab: React.FC = () => (
  <Container header={<Header variant="h2">Terms & Conditions</Header>}>
    <Box>
      <p>
        T&amp;C acceptance tracking is deferred to v1.1. In v1.1 this tab will show:
      </p>
      <ul>
        <li>Which version of the T&amp;C this subscriber accepted</li>
        <li>When they accepted it (timestamp + IP + user agent)</li>
        <li>Whether re-acceptance is required (T&amp;C version bumped since acceptance)</li>
        <li>History of past acceptances across versions</li>
      </ul>
      <p>
        <b>Current model:</b> paper contract signed offline before the OEM operator
        provisions the subscriber's Cognito account. Not tracked in-platform.
      </p>
    </Box>
  </Container>
);

// ── Component ─────────────────────────────────────────────────────────────────

const SubscriberDetailView: React.FC = () => {
  const { consumerId } = useParams<{ consumerId: string }>();

  const s = { ...STUB_SUBSCRIBER, consumer_id: consumerId ?? STUB_SUBSCRIBER.consumer_id };

  return (
    <SpaceBetween size="l">
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="subscriber-detail-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      <Tabs
        data-testid="subscriber-detail-tabs"
        tabs={[
          { id: "overview", label: "Overview", content: <OverviewTab s={s} /> },
          { id: "scope", label: `Scope (${STUB_SCOPE.length})`, content: <ScopeTab scope={STUB_SCOPE} /> },
          { id: "consumption", label: "Consumption", content: <ConsumptionTab days={STUB_CONSUMPTION} /> },
          { id: "terms", label: "T&C", content: <TermsTab /> },
        ]}
      />
    </SpaceBetween>
  );
};

export default SubscriberDetailView;
