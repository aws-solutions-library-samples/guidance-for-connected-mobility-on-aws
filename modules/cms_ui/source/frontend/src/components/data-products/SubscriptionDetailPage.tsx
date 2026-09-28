// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// SubscriptionDetailPage — detail view for one subscribed data product.
// UI stub only, no API. Renders six sections in a single scroll:
//   1. Overview             — producer, tier, dates, subscription id
//   2. Feed Health          — status, latency, error rate
//   3. Vehicles Enrolled    — small table of vehicles receiving this feed
//   4. Signal Availability  — paginated + filterable table of CMS canonical
//                              fields this subscription entitles the fleet to
//   5. Consumption          — data volume, cost, quota headroom
//   6. Producer             — description + producer name; NO deep link
//                              (CMS is a standalone portal per user direction)
//
// The "transform manifest" concept is intentionally absent from this view —
// subscribers see the CANONICAL CMS output shape, not the OEM's source
// mapping. That mapping lives in the CS portal, owned by the OEM. If a
// subscriber wants to know the source-side mechanics, they ask the OEM
// (out of band) or their CS portal admin.

import React from 'react';
import {
  Alert,
  Badge,
  Box,
  ColumnLayout,
  Container,
  ExpandableSection,
  Header,
  Pagination,
  SpaceBetween,
  StatusIndicator,
  Table,
  TextFilter,
  Button,
  Popover,
} from '@cloudscape-design/components';
import { useCollection } from '@cloudscape-design/collection-hooks';
import { useNavigate, useParams } from 'react-router-dom';

import {
  getSubscriptionById,
  getEnrolledVehiclesForSub,
  isFleetAssignedToProducer,
  AvailableSignal,
  EnrolledVehicle,
  FeedHealth,
  Invoice,
  InvoiceLineItem,
  SubscriptionStatus,
  Subscription,
} from './mockData';
import { useDataProductFleets } from './useDataProductFleets';
import type { FleetItem } from '@/types/fleet-types';
import EnrollVehiclesModal from './EnrollVehiclesModal';

// ─── Small helpers ─────────────────────────────────────────────────────────

const KV: React.FC<{ label: string; children: React.ReactNode }> = ({
  label,
  children,
}) => (
  <div>
    <Box color="text-label" fontSize="body-s" fontWeight="bold">
      {label}
    </Box>
    <Box>{children}</Box>
  </div>
);

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

function CopyButton({ text, label }: { text: string; label?: string }) {
  return (
    <Popover
      dismissButton={false}
      position="top"
      size="small"
      triggerType="custom"
      content={<StatusIndicator type="success">Copied</StatusIndicator>}
    >
      <Button
        iconName="copy"
        variant="inline-icon"
        ariaLabel={label ?? 'Copy'}
        onClick={() => navigator.clipboard?.writeText(text).catch(() => {})}
      />
    </Popover>
  );
}

const MonoValue: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span
    style={{
      fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
      fontSize: 13,
      wordBreak: 'break-all',
    }}
  >
    {children}
  </span>
);

// ─── Signal-availability table with filter + pagination ─────────────────────

const SignalAvailabilityTable: React.FC<{
  signals: AvailableSignal[];
}> = ({ signals }) => {
  const {
    items,
    filteredItemsCount,
    collectionProps,
    filterProps,
    paginationProps,
  } = useCollection(signals, {
    filtering: {
      empty: (
        <Box textAlign="center" color="inherit">
          <b>No signals</b>
        </Box>
      ),
      noMatch: (
        <Box textAlign="center" color="inherit">
          <b>No matches</b>
        </Box>
      ),
      filteringFunction: (item, filteringText) => {
        const t = filteringText.toLowerCase();
        return (
          item.cmsField.toLowerCase().includes(t) ||
          item.dataType.toLowerCase().includes(t) ||
          (item.unit ?? '').toLowerCase().includes(t)
        );
      },
    },
    pagination: { pageSize: 25 },
    sorting: {},
  });

  return (
    <Table
      {...collectionProps}
      variant="embedded"
      items={items}
      resizableColumns
      stickyHeader
      filter={
        <TextFilter
          {...filterProps}
          filteringPlaceholder="Filter signals"
          countText={`${filteredItemsCount ?? 0} match${(filteredItemsCount ?? 0) === 1 ? '' : 'es'}`}
        />
      }
      pagination={<Pagination {...paginationProps} />}
      columnDefinitions={[
        {
          id: 'cmsField',
          header: 'CMS field',
          cell: (s) => <MonoValue>{s.cmsField}</MonoValue>,
          sortingField: 'cmsField',
        },
        {
          id: 'dataType',
          header: 'Type',
          cell: (s) => <Badge>{s.dataType}</Badge>,
          sortingField: 'dataType',
        },
        {
          id: 'unit',
          header: 'Unit',
          cell: (s) =>
            s.unit ? (
              <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 13 }}>{s.unit}</span>
            ) : (
              <Box color="text-status-inactive">—</Box>
            ),
        },
        {
          id: 'sampleValue',
          header: 'Sample value',
          cell: (s) => <MonoValue>{s.sampleValue}</MonoValue>,
        },
        {
          id: 'guarantee',
          header: 'Guarantee',
          cell: (s) =>
            s.guarantee === 'guaranteed' ? (
              <StatusIndicator type="success">Guaranteed</StatusIndicator>
            ) : (
              <Box color="text-body-secondary" fontSize="body-s">Best-effort</Box>
            ),
        },
      ]}
    />
  );
};

// ─── Vehicles-enrolled table (small, no pagination) ────────────────────────

const VehiclesEnrolledTable: React.FC<{ vehicles: EnrolledVehicle[] }> = ({
  vehicles,
}) => (
  <Table
    variant="embedded"
    items={vehicles}
    empty={
      <Box textAlign="center" color="inherit">
        <b>No vehicles enrolled</b>
        <Box padding={{ bottom: 's' }} variant="p" color="inherit">
          Enroll vehicles to start receiving data from this subscription.
        </Box>
      </Box>
    }
    columnDefinitions={[
      {
        id: 'vehicleId',
        header: 'Vehicle ID',
        cell: (v) => <MonoValue>{v.vehicleId}</MonoValue>,
      },
      {
        id: 'vin',
        header: 'VIN',
        cell: (v) => <MonoValue>{v.vin}</MonoValue>,
      },
      {
        id: 'model',
        header: 'Model',
        cell: (v) => v.model,
      },
      {
        id: 'feedHealth',
        header: 'Feed status',
        cell: (v) => feedHealthIndicator(v.feedHealth),
      },
      {
        id: 'lastPacketAt',
        header: 'Last data',
        cell: (v) => (
          <Box color="text-body-secondary" fontSize="body-s">
            {v.lastPacketAt}
          </Box>
        ),
      },
    ]}
  />
);

// ─── Main page ─────────────────────────────────────────────────────────────

const SubscriptionDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const subscription: Subscription | undefined = id ? getSubscriptionById(id) : undefined;

  // Enrollment overlay state — bumps a counter to force a re-read of
  // getEnrolledVehiclesForSub() after the modal reports success.
  const [enrollModalOpen, setEnrollModalOpen] = React.useState(false);
  const [enrollBump, setEnrollBump] = React.useState(0);
  const [enrollFlash, setEnrollFlash] = React.useState<{ added: number } | null>(null);

  const enrolledVehicles = React.useMemo(
    () => (subscription ? getEnrolledVehiclesForSub(subscription.id) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [subscription?.id, enrollBump],
  );

  if (!subscription) {
    return (
      <SpaceBetween size="l">
        <Alert
          type="error"
          header="Subscription not found"
          action={
            <Button onClick={() => navigate('/data-products')}>Back to subscriptions</Button>
          }
        >
          Subscription <MonoValue>{id ?? '(no id)'}</MonoValue> does not exist or you don't have access.
        </Alert>
      </SpaceBetween>
    );
  }

  const s = subscription;

  return (
    <SpaceBetween size="l">
      {/* Overview */}
      <Container
        header={
          <Header
            variant="h2"
            description={s.description}
          >
            Overview
          </Header>
        }
      >
        <ColumnLayout columns={4} variant="text-grid">
          <KV label="Producer">{s.producer}</KV>
          <KV label="Tier">{tierBadge(s.tier)}</KV>
          <KV label="Status">{statusBadge(s.status)}</KV>
          <KV label="Subscription ID">
            <SpaceBetween direction="horizontal" size="xxs" alignItems="center">
              <MonoValue>{s.id}</MonoValue>
              <CopyButton text={s.id} label="Copy subscription ID" />
            </SpaceBetween>
          </KV>
          <KV label="Contract start">{new Date(s.contractStart).toLocaleDateString()}</KV>
          <KV label="Next renewal">{new Date(s.nextRenewal).toLocaleDateString()}</KV>
          <KV label="Entitled signals">
            {s.entitledSignalCount} of {s.totalOemSignals}
          </KV>
          <KV label="Vehicles">
            {enrolledVehicles.length} of {s.vehiclesCapacity}
          </KV>
        </ColumnLayout>
      </Container>

      {/* Fleets served — the 1:N relationship between subscriptions and
          fleets, viewed from the subscription side. Fleets are assigned to
          a data product (not a subscription directly), so this section
          shows every fleet linked to this subscription's PRODUCER. Each
          fleet may reach this data product through any of the operator's
          subscriptions; this list is best read as "these fleets can enroll
          vehicles under this subscription's tier if the operator chooses". */}
      <FleetsServedSection producerName={s.producer} />

      {/* Feed Health */}
      <Container
        header={
          <Header
            variant="h2"
            description="Real-time flow status of data arriving from this subscription."
          >
            Feed health
          </Header>
        }
      >
        <ColumnLayout columns={4} variant="text-grid">
          <KV label="Status">{feedHealthIndicator(s.feedHealth)}</KV>
          <KV label="Last packet">{s.lastPacketAt}</KV>
          <KV label="p95 latency">
            {s.p95LatencyMs > 0 ? `${s.p95LatencyMs} ms` : <Box color="text-status-inactive">—</Box>}
          </KV>
          <KV label="Error rate (24h)">{s.errorRate24h}</KV>
        </ColumnLayout>
      </Container>

      {/* Vehicles Enrolled */}
      <Container
        header={
          <Header
            variant="h2"
            counter={`(${enrolledVehicles.length} of ${s.vehiclesCapacity})`}
            description="Vehicles in this fleet receiving data from this subscription."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  variant="primary"
                  onClick={() => setEnrollModalOpen(true)}
                  disabled={enrolledVehicles.length >= s.vehiclesCapacity}
                >
                  Enroll vehicles
                </Button>
              </SpaceBetween>
            }
          >
            Vehicles enrolled
          </Header>
        }
      >
        <SpaceBetween size="s">
          {enrollFlash && (
            <Alert
              type="success"
              dismissible
              onDismiss={() => setEnrollFlash(null)}
              statusIconAriaLabel="Success"
            >
              Enrolled {enrollFlash.added} vehicle{enrollFlash.added === 1 ? '' : 's'} on this
              subscription. Data feed is now live for those VINs.
            </Alert>
          )}
          <VehiclesEnrolledTable vehicles={enrolledVehicles} />
        </SpaceBetween>
      </Container>

      {/* Signal Availability */}
      <Container
        header={
          <Header
            variant="h2"
            counter={`(${s.availableSignals.length})`}
            description={
              <>
                CMS canonical fields this subscription populates. Types, units, and a
                recent sample value per field. Source-side mapping is defined by the
                producer — not shown here.
              </>
            }
          >
            Signal availability
          </Header>
        }
      >
        <SpaceBetween size="m">
          <SignalAvailabilityTable signals={s.availableSignals} />
          {s.unavailableSignals.length > 0 && s.upgradeToTier && (
            <Alert
              type="info"
              header={`${s.unavailableSignals.length} additional signals available at ${s.upgradeToTier} tier`}
            >
              Upgrade to unlock:{' '}
              {s.unavailableSignals.slice(0, 6).map((sig, i) => (
                <span key={sig}>
                  <MonoValue>{sig}</MonoValue>
                  {i < Math.min(5, s.unavailableSignals.length - 1) ? ', ' : ''}
                </span>
              ))}
              {s.unavailableSignals.length > 6 &&
                ` and ${s.unavailableSignals.length - 6} more`}
              . Contact your Connected Services administrator to change tier.
            </Alert>
          )}
        </SpaceBetween>
      </Container>

      {/* Consumption */}
      <Container
        header={
          <Header
            variant="h2"
            description="Data volume and estimated cost for the current billing cycle."
          >
            Consumption
          </Header>
        }
      >
        <ColumnLayout columns={4} variant="text-grid">
          <KV label="Data volume this cycle">{s.dataVolumeCycle}</KV>
          <KV label="Estimated cost">{s.estimatedCostCycle}</KV>
          <KV label="Vehicles enrolled">
            {s.enrolledVehicles.length} of {s.vehiclesCapacity}
          </KV>
          <KV label="Quota headroom">{s.quotaHeadroomPct}%</KV>
        </ColumnLayout>
      </Container>

      {/* Billing — producer-issued invoice history. Only rendered when the
          subscription has an invoice trail attached (Ford Pro today; others
          settle differently or haven't been backfilled). Total spent
          rolls up all invoice totalDue across the history so the operator
          has a quick 'what have we paid this vendor?' answer. */}
      {s.invoices && s.invoices.length > 0 && (
        <BillingSection subscription={s} />
      )}

      {/* Producer info — plain description, no outbound link. Kept minimal so
          the surface remains obviously "subscriber view" and not "producer
          view lite." */}
      <ExpandableSection
        headerText={`About ${s.producer}`}
        headerDescription="Producer of this data product."
      >
        <SpaceBetween size="s">
          <Box>{s.description}</Box>
          <Box color="text-body-secondary" fontSize="body-s">
            Contact your Connected Services administrator to change subscription tier,
            adjust vehicle capacity, or resolve feed health issues.
          </Box>
        </SpaceBetween>
      </ExpandableSection>

      {/* Enroll-vehicles modal — rendered inside the page tree so its state
          lives with the page, closes cleanly on navigate. */}
      <EnrollVehiclesModal
        visible={enrollModalOpen}
        subscription={s}
        currentlyEnrolledCount={enrolledVehicles.length}
        onDismiss={() => setEnrollModalOpen(false)}
        onEnrolled={(added) => {
          setEnrollModalOpen(false);
          setEnrollBump((n) => n + 1);
          setEnrollFlash({ added });
        }}
      />
    </SpaceBetween>
  );
};

export default SubscriptionDetailPage;


/**
 * FleetsServedSection — shows which of the operator's fleets are linked to
 * this subscription's producer. Read-only here: assignments are edited from
 * the Catalog detail page (Fleets using this producer section), which is
 * the "producer configuration" surface. Empty state gently nudges the
 * user to go make an assignment if none exist yet.
 */
const FleetsServedSection: React.FC<{ producerName: string }> = ({ producerName }) => {
  const navigate = useNavigate();
  const { fleets, loading, error } = useDataProductFleets();

  const servedFleets = React.useMemo(
    () =>
      fleets.filter((f) => {
        const id = (f.id ?? f.fleetId ?? '') as string;
        return id && isFleetAssignedToProducer(id, producerName);
      }),
    [fleets, producerName],
  );

  const idOf = (f: FleetItem) => (f.id ?? f.fleetId ?? '') as string;
  const nameOf = (f: FleetItem) => (f.name ?? f.fleetId ?? f.id ?? '(unnamed)') as string;
  const countOf = (f: FleetItem) =>
    f.vehicleCount ?? f.totalVehicles ?? f.numTotalVehicles ?? 0;

  return (
    <Container
      header={
        <Header
          variant="h2"
          counter={servedFleets.length ? `(${servedFleets.length})` : undefined}
          description={`Real fleets from your CMS inventory that are assigned to ${producerName}. Any of these can enroll vehicles under this subscription. Manage assignments from the data source's catalog page.`}
        >
          Fleets served
        </Header>
      }
    >
      {error ? (
        <Alert type="error" header="Could not load fleets">
          {error}
        </Alert>
      ) : loading ? (
        <Box padding="s" color="text-body-secondary">
          Loading fleets…
        </Box>
      ) : servedFleets.length === 0 ? (
        <Alert
          type="info"
          action={
            <Button
              onClick={() => navigate('/data-products/catalog')}
              iconName="external"
            >
              Go to catalog
            </Button>
          }
        >
          No fleets are assigned to {producerName} yet. Assign at least one of your fleets from
          the data source's catalog page to enable vehicle enrollment against this subscription.
        </Alert>
      ) : (
        <Table
          variant="embedded"
          items={servedFleets}
          columnDefinitions={[
            {
              id: 'fleetName',
              header: 'Fleet',
              cell: (f: FleetItem) => <Box fontWeight="bold">{nameOf(f)}</Box>,
              minWidth: 200,
            },
            {
              id: 'fleetId',
              header: 'Fleet ID',
              cell: (f: FleetItem) => (
                <Box fontFamily="monospace" fontSize="body-s" color="text-body-secondary">
                  {idOf(f)}
                </Box>
              ),
            },
            {
              id: 'vehicleCount',
              header: 'Vehicles',
              cell: (f: FleetItem) => countOf(f),
              width: 100,
            },
          ]}
        />
      )}
    </Container>
  );
};


// ─── Billing section ─────────────────────────────────────────────────────
//
// Renders the producer-issued invoice history for a subscription. Modelled
// after the shape of a real Ford Pro invoice (SKU-based line items, unit
// cost × qty = amount, sales tax, total due). Each row is expandable to
// show the line items.
//
// Kept below Consumption on purpose — Consumption is the running spend
// forecast for the current cycle; Billing is the closed-book invoice
// history from prior cycles.

const fmtUsd = (n: number): string =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);

const InvoiceStatusBadge: React.FC<{ status: Invoice['status'] }> = ({ status }) => {
  if (status === 'paid') return <StatusIndicator type="success">Paid</StatusIndicator>;
  if (status === 'overdue') return <StatusIndicator type="error">Overdue</StatusIndicator>;
  return <StatusIndicator type="warning">Unpaid</StatusIndicator>;
};

const InvoiceLineItemsTable: React.FC<{ invoice: Invoice }> = ({ invoice }) => (
  <Table
    variant="embedded"
    items={invoice.lineItems}
    columnDefinitions={[
      {
        id: 'sku',
        header: 'SKU',
        cell: (li: InvoiceLineItem) => (
          <Box fontFamily="monospace" fontSize="body-s" color="text-body-secondary">
            {li.sku}
          </Box>
        ),
        minWidth: 140,
      },
      {
        id: 'description',
        header: 'Description',
        cell: (li: InvoiceLineItem) => li.description,
        minWidth: 260,
      },
      {
        id: 'unitCost',
        header: 'Unit cost',
        cell: (li: InvoiceLineItem) =>
          li.unitCost !== undefined ? (
            fmtUsd(li.unitCost)
          ) : (
            <Box color="text-body-secondary">—</Box>
          ),
        width: 110,
      },
      {
        id: 'qty',
        header: 'Qty',
        cell: (li: InvoiceLineItem) => li.qty,
        width: 70,
      },
      {
        id: 'amount',
        header: 'Amount',
        cell: (li: InvoiceLineItem) => <Box fontWeight="bold">{fmtUsd(li.amount)}</Box>,
        width: 110,
      },
    ]}
    footer={
      <Box padding={{ top: 's' }}>
        <ColumnLayout columns={3} variant="text-grid">
          <KV label="Subtotal">{fmtUsd(invoice.subtotal)}</KV>
          <KV label="Sales tax">{fmtUsd(invoice.salesTax)}</KV>
          <KV label="Total due">
            <Box fontWeight="bold">{fmtUsd(invoice.totalDue)}</Box>
          </KV>
        </ColumnLayout>
      </Box>
    }
  />
);

const BillingSection: React.FC<{ subscription: Subscription }> = ({ subscription }) => {
  const invoices = subscription.invoices ?? [];
  const totalSpent = invoices.reduce((sum, inv) => sum + inv.totalDue, 0);
  const unpaid = invoices.filter((i) => i.status === 'unpaid' || i.status === 'overdue');
  const unpaidTotal = unpaid.reduce((sum, inv) => sum + inv.totalDue, 0);
  const latestInvoice = invoices[0];

  return (
    <Container
      header={
        <Header
          variant="h2"
          counter={`(${invoices.length})`}
          description={`Producer-issued invoices for this subscription. Account ${subscription.accountNumber ?? '—'}.`}
        >
          Billing
        </Header>
      }
    >
      <SpaceBetween size="l">
        {/* Top-line rollups. */}
        <ColumnLayout columns={4} variant="text-grid">
          <KV label="Account number">
            {subscription.accountNumber ? (
              <MonoValue>{subscription.accountNumber}</MonoValue>
            ) : (
              <Box color="text-body-secondary">—</Box>
            )}
          </KV>
          <KV label="Total invoiced">{fmtUsd(totalSpent)}</KV>
          <KV label="Unpaid balance">
            {unpaidTotal > 0 ? (
              <Box fontWeight="bold" color="text-status-warning">
                {fmtUsd(unpaidTotal)}
              </Box>
            ) : (
              <Box color="text-status-success">{fmtUsd(0)}</Box>
            )}
          </KV>
          <KV label="Latest invoice">
            {latestInvoice ? (
              <span>
                <b>{fmtUsd(latestInvoice.totalDue)}</b> — {latestInvoice.billingPeriod}
              </span>
            ) : (
              <Box color="text-body-secondary">—</Box>
            )}
          </KV>
        </ColumnLayout>

        {/* Per-invoice list with expandable line items. */}
        <SpaceBetween size="s">
          {invoices.map((inv) => (
            <ExpandableSection
              key={inv.invoiceNumber}
              defaultExpanded={inv.status !== 'paid'}
              headerText={
                <SpaceBetween direction="horizontal" size="m" alignItems="center">
                  <MonoValue>{inv.invoiceNumber}</MonoValue>
                  <Box>{inv.billingPeriod}</Box>
                  <InvoiceStatusBadge status={inv.status} />
                  <Box fontWeight="bold">{fmtUsd(inv.totalDue)}</Box>
                  <Box color="text-body-secondary" fontSize="body-s">
                    Due {new Date(inv.dueDate).toLocaleDateString()}
                  </Box>
                </SpaceBetween>
              }
            >
              <SpaceBetween size="m">
                <ColumnLayout columns={4} variant="text-grid">
                  <KV label="Invoice date">
                    {new Date(inv.invoiceDate).toLocaleDateString()}
                  </KV>
                  <KV label="Billing period">{inv.billingPeriod}</KV>
                  <KV label="Account">
                    <MonoValue>{inv.accountNumber}</MonoValue>
                  </KV>
                  <KV label="Due date">
                    {new Date(inv.dueDate).toLocaleDateString()}
                  </KV>
                </ColumnLayout>
                <InvoiceLineItemsTable invoice={inv} />
              </SpaceBetween>
            </ExpandableSection>
          ))}
        </SpaceBetween>
      </SpaceBetween>
    </Container>
  );
};
