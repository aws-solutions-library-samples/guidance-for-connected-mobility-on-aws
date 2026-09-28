// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// SubscriptionsListPage — subscriber-facing landing page for data-product
// subscriptions. UI stub (mock data only, no API, no CS-portal links).
//
// Layout: four KPI cards up top + table below, matching the pattern used
// across the app's dashboards (see FleetRebalancingDashboard,
// ServiceDashboard). KPI markup is deliberately identical: same font sizes,
// same label styling, same 16px grid gap.
//
// This page intentionally has no way to CREATE a subscription — that
// happens in the CS portal. CMS is the consumer/subscriber view.

import React, { useMemo, useState } from 'react';
import {
  Alert,
  Table,
  Box,
  Container,
  SpaceBetween,
  Header,
  Button,
  StatusIndicator,
  Badge,
} from '@cloudscape-design/components';
import { useNavigate } from 'react-router-dom';
import {
  getAllSubscriptions,
  Subscription,
  FeedHealth,
  SubscriptionStatus,
} from './mockData';
import AddSubscriptionModal from './AddSubscriptionModal';

// ─── Small render helpers ──────────────────────────────────────────────────

function feedHealthIndicator(health: FeedHealth) {
  switch (health) {
    case 'streaming':
      return <StatusIndicator type="success">Streaming</StatusIndicator>;
    case 'delayed':
      return <StatusIndicator type="warning">Delayed</StatusIndicator>;
    case 'down':
      return <StatusIndicator type="error">Down</StatusIndicator>;
    case 'paused':
      return <StatusIndicator type="stopped">Paused</StatusIndicator>;
  }
}

function statusBadge(status: SubscriptionStatus) {
  const color =
    status === 'Active' ? 'green' :
    status === 'Paused' ? 'grey' :
    status === 'Pending' ? 'blue' :
    'red';
  return <Badge color={color}>{status}</Badge>;
}

function tierBadge(tier: 'Premium' | 'Standard' | 'Basic') {
  const color = tier === 'Premium' ? 'blue' : tier === 'Standard' ? 'green' : 'grey';
  return <Badge color={color}>{tier}</Badge>;
}

/** Parse the "$4,200" strings in mock data to a number for KPI totals. */
function parseCurrency(s: string): number {
  const cleaned = s.replace(/[$,]/g, '').trim();
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : 0;
}

// ─── Page ──────────────────────────────────────────────────────────────────

const SubscriptionsListPage: React.FC = () => {
  const navigate = useNavigate();
  const [addModalVisible, setAddModalVisible] = useState(false);
  // Bump this state to force a re-read from mockData's session store after a
  // successful add. The store is module-scope, not React state, so we need a
  // trigger to make the list re-render.
  const [refreshTick, setRefreshTick] = useState(0);
  const [justAddedAlert, setJustAddedAlert] = useState<string | null>(null);

  const subscriptions = useMemo(
    () => getAllSubscriptions(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [refreshTick],
  );

  // KPI totals derived from the current subscription set (mock + session-added).
  // When the real API lands, these become server-computed roll-up fields; the
  // JSX below is shape-stable.
  const kpis = useMemo(() => {
    const subs = subscriptions;
    const active = subs.filter((s) => s.status === 'Active');
    const streaming = subs.filter((s) => s.feedHealth === 'streaming');
    const delayed   = subs.filter((s) => s.feedHealth === 'delayed');
    const down      = subs.filter((s) => s.feedHealth === 'down');
    const paused    = subs.filter((s) => s.feedHealth === 'paused');
    const vehiclesEnrolled = subs.reduce(
      (sum, s) => sum + s.enrolledVehicles.length,
      0,
    );
    const vehiclesCapacity = subs.reduce(
      (sum, s) => sum + s.vehiclesCapacity,
      0,
    );
    const monthlyCost = subs
      .filter((s) => s.status === 'Active')
      .reduce((sum, s) => sum + parseCurrency(s.estimatedCostCycle), 0);
    // Real invoice totals across every subscription that carries an invoice
    // history. Different from `monthlyCost` above — that's a forward-looking
    // estimate parsed from a display string; this is closed-book actuals.
    const invoicedTotal = subs.reduce(
      (sum, s) => sum + (s.invoices ?? []).reduce((a, i) => a + i.totalDue, 0),
      0,
    );
    const unpaidBalance = subs.reduce(
      (sum, s) =>
        sum +
        (s.invoices ?? [])
          .filter((i) => i.status !== 'paid')
          .reduce((a, i) => a + i.totalDue, 0),
      0,
    );
    return {
      totalSubs: subs.length,
      activeSubs: active.length,
      streaming: streaming.length,
      delayed: delayed.length,
      down: down.length,
      paused: paused.length,
      vehiclesEnrolled,
      vehiclesCapacity,
      monthlyCost,
      invoicedTotal,
      unpaidBalance,
    };
  }, [subscriptions]);

  const feedHealthStatusType: 'success' | 'warning' | 'error' =
    kpis.down > 0 ? 'error' :
    kpis.delayed > 0 || kpis.paused > 0 ? 'warning' :
    'success';

  const feedHealthLabel =
    kpis.down > 0 ? `${kpis.down} feed${kpis.down === 1 ? '' : 's'} down`
    : kpis.delayed > 0 ? `${kpis.delayed} delayed, ${kpis.paused} paused`
    : kpis.paused > 0 ? `${kpis.paused} paused`
    : 'All feeds healthy';

  return (
    <SpaceBetween size="l">
      {justAddedAlert && (
        <Alert
          type="success"
          dismissible
          onDismiss={() => setJustAddedAlert(null)}
          header="Subscription added"
        >
          {justAddedAlert}
        </Alert>
      )}

      {/* KPI Cards — matching FleetRebalancingDashboard / ServiceDashboard */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px' }}>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>
              Active subscriptions
            </span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>
              {kpis.activeSubs}
            </span>
            <Box color="text-body-secondary" fontSize="body-s">
              of {kpis.totalSubs} total
            </Box>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>
              Vehicles enrolled
            </span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>
              {kpis.vehiclesEnrolled}
            </span>
            <Box color="text-body-secondary" fontSize="body-s">
              of {kpis.vehiclesCapacity} capacity
            </Box>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>
              Feeds streaming
            </span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>
              {kpis.streaming}
            </span>
            <StatusIndicator type={feedHealthStatusType}>
              {feedHealthLabel}
            </StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>
              Est. monthly cost
            </span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>
              ${kpis.monthlyCost.toLocaleString()}
            </span>
            <Box color="text-body-secondary" fontSize="body-s">
              across {kpis.activeSubs} active feed{kpis.activeSubs === 1 ? '' : 's'}
            </Box>
          </SpaceBetween>
        </Container>
      </div>

      {/* Subscriptions table */}
      <Table
        items={subscriptions}
        header={
          <Header
            counter={`(${subscriptions.length})`}
            description="Data products this fleet consumes."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={() => setRefreshTick((n) => n + 1)}>Refresh</Button>
                <Button variant="primary" onClick={() => setAddModalVisible(true)}>
                  Add subscription
                </Button>
              </SpaceBetween>
            }
          >
            Subscriptions
          </Header>
        }
        columnDefinitions={[
          {
            id: 'productName',
            header: 'Data product',
            cell: (s: Subscription) => (
              <Button
                variant="inline-link"
                onClick={() => navigate(`/data-products/${s.id}`)}
              >
                {s.productName}
              </Button>
            ),
            sortingField: 'productName',
            isRowHeader: true,
          },
          {
            id: 'producer',
            header: 'Producer',
            cell: (s: Subscription) => s.producer,
            sortingField: 'producer',
          },
          {
            id: 'tier',
            header: 'Tier',
            cell: (s: Subscription) => tierBadge(s.tier),
          },
          {
            id: 'status',
            header: 'Status',
            cell: (s: Subscription) => statusBadge(s.status),
          },
          {
            id: 'vehicles',
            header: 'Vehicles',
            cell: (s: Subscription) => (
              <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}>
                {s.enrolledVehicles.length} <span style={{ color: '#888' }}>of {s.vehiclesCapacity}</span>
              </span>
            ),
          },
          {
            id: 'feedHealth',
            header: 'Feed health',
            cell: (s: Subscription) => feedHealthIndicator(s.feedHealth),
          },
          {
            id: 'lastPacketAt',
            header: 'Last data',
            cell: (s: Subscription) => (
              <Box color="text-body-secondary" fontSize="body-s">
                {s.lastPacketAt}
              </Box>
            ),
          },
          {
            id: 'latestInvoice',
            header: 'Latest invoice',
            cell: (s: Subscription) => {
              const latest = s.invoices?.[0];
              if (!latest) {
                return (
                  <Box color="text-body-secondary" fontSize="body-s">
                    —
                  </Box>
                );
              }
              const amount = new Intl.NumberFormat('en-US', {
                style: 'currency',
                currency: 'USD',
              }).format(latest.totalDue);
              return (
                <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                  <Box fontWeight="bold">{amount}</Box>
                  {latest.status === 'paid' && (
                    <StatusIndicator type="success">Paid</StatusIndicator>
                  )}
                  {latest.status === 'unpaid' && (
                    <StatusIndicator type="warning">Unpaid</StatusIndicator>
                  )}
                  {latest.status === 'overdue' && (
                    <StatusIndicator type="error">Overdue</StatusIndicator>
                  )}
                </SpaceBetween>
              );
            },
          },
          {
            id: 'actions',
            header: 'Actions',
            cell: (s: Subscription) => (
              <Button
                variant="inline-link"
                onClick={() => navigate(`/data-products/${s.id}`)}
              >
                View details
              </Button>
            ),
          },
        ]}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No subscriptions</b>
            <Box padding={{ bottom: 's' }} variant="p" color="inherit">
              This fleet is not yet subscribed to any data products.
            </Box>
            <Button variant="primary" onClick={() => setAddModalVisible(true)}>
              Add your first subscription
            </Button>
          </Box>
        }
      />

      <AddSubscriptionModal
        visible={addModalVisible}
        onDismiss={() => setAddModalVisible(false)}
        onAdded={() => {
          setAddModalVisible(false);
          setRefreshTick((n) => n + 1);
          setJustAddedAlert(
            'New subscription added in Pending status. It will move to Active once the producer confirms.',
          );
        }}
      />
    </SpaceBetween>
  );
};

export default SubscriptionsListPage;
