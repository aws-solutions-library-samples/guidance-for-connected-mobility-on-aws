// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// CatalogDetailPage — full detail view of one data-product catalog entry.
//
// Surfaces everything the fleet operator defined when adding this data
// product: identity, connection, authentication, credentials reference,
// tier offering. This is the "demo the whole process" surface the user
// asked for — it shows exactly how the platform connects to an OEM, with
// endpoints visible and credentials referenced (never embedded).

import React from 'react';
import {
  Alert,
  Badge,
  Box,
  Button,
  ColumnLayout,
  Container,
  Header,
  Modal,
  Popover,
  Select,
  SpaceBetween,
  StatusIndicator,
  Table,
  TextFilter,
} from '@cloudscape-design/components';
import { useNavigate, useParams } from 'react-router-dom';
import {
  getDataProductById,
  getSupportedTiers,
  getTotalSignalCount,
  getAssignedProductIdsForFleet,
  getSignalMappingsFor,
  setSignalMappingsFor,
  getEventMappingsFor,
  setEventMappingsFor,
  assignDataProductToFleet,
  unassignDataProductFromFleet,
  isFleetAssignedToProduct,
  DataProduct,
  SignalMapping,
  EventMapping,
} from './mockData';
import {
  CMS_CANONICAL_SIGNALS,
  CMS_CANONICAL_EVENTS,
  getProducerSignals,
  getProducerEvents,
  autoMatchSignal,
  autoMatchEvent,
  CmsCanonicalSignal,
  CmsCanonicalEvent,
  ProducerSignal,
  ProducerEvent,
} from './signalMapping';
import { useDataProductFleets } from './useDataProductFleets';
import type { FleetItem } from '@/types/fleet-types';

// ─── Helpers ───────────────────────────────────────────────────────────────

/**
 * Mask a Secrets Manager ARN for display. The ARN itself is not sensitive
 * (arns are catalogue metadata), but visible masking signals that
 * "credentials are referenced, not embedded here." Copy button beside it
 * yields the full unmasked value.
 */
function maskArn(arn: string): string {
  if (!arn || typeof arn !== 'string') return '';
  const parts = arn.split(':');
  if (parts.length < 7 || parts[0] !== 'arn') return arn;
  const [prefix, aws, service, region, account, ...rest] = parts;
  const resource = rest.join(':');
  const maskedAccount =
    account.length > 6
      ? `${account.slice(0, 3)}…${account.slice(-3)}`
      : '••••';
  const maskedResource =
    resource.length > 12
      ? `${resource.slice(0, 8)}…${resource.slice(-4)}`
      : resource;
  return `${prefix}:${aws}:${service}:${region}:${maskedAccount}:${maskedResource}`;
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

const CopyButton: React.FC<{ text: string; label?: string }> = ({ text, label }) => (
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
      onClick={() => copyToClipboard(text)}
    />
  </Popover>
);

const CodeValue: React.FC<{ value: string }> = ({ value }) => (
  <SpaceBetween direction="horizontal" size="xxs" alignItems="center">
    <span
      style={{
        fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
        fontSize: 13,
        wordBreak: 'break-all',
      }}
    >
      {value}
    </span>
    <CopyButton text={value} />
  </SpaceBetween>
);

const KV: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div>
    <Box color="text-label" fontSize="body-s" fontWeight="bold">
      {label}
    </Box>
    <Box>{children}</Box>
  </div>
);

// ─── Page ──────────────────────────────────────────────────────────────────

const CatalogDetailPage: React.FC = () => {
  const { productId } = useParams<{ productId: string }>();
  const navigate = useNavigate();
  const product: DataProduct | undefined = productId ? getDataProductById(productId) : undefined;

  if (!product) {
    return (
      <SpaceBetween size="l">
        <Alert
          type="error"
          header="Data product not found"
          action={
            <Button onClick={() => navigate('/data-products/catalog')}>
              Back to catalog
            </Button>
          }
        >
          Data product <b>{productId ?? '(no id)'}</b> is not in the catalog.
        </Alert>
      </SpaceBetween>
    );
  }

  const p = product;

  return (
    <SpaceBetween size="l">
      {/* Overview */}
      <Container
        header={
          <Header
            variant="h2"
            description={p.description}
          >
            Overview
          </Header>
        }
      >
        <ColumnLayout columns={4} variant="text-grid">
          <KV label="Product">{p.productName}</KV>
          <KV label="Producer">{p.producer}</KV>
          <KV label="Status">
            <StatusIndicator
              type={
                p.status === 'Active' ? 'success' :
                p.status === 'Draft' ? 'pending' :
                'stopped'
              }
            >
              {p.status}
            </StatusIndicator>
          </KV>
          <KV label="Product ID">
            <SpaceBetween direction="horizontal" size="xxs" alignItems="center">
              <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}>
                {p.productId}
              </span>
              <CopyButton text={p.productId} label="Copy product ID" />
            </SpaceBetween>
          </KV>
          <KV label="Total signals">{getTotalSignalCount(p)}</KV>
          <KV label="Created">{new Date(p.createdAt).toLocaleDateString()}</KV>
          <KV label="Tiers offered">
            <SpaceBetween direction="horizontal" size="xxs">
              {getSupportedTiers(p).map((t) => (
                <Badge key={t} color={t === 'Premium' ? 'blue' : t === 'Standard' ? 'green' : 'grey'}>
                  {t}
                </Badge>
              ))}
            </SpaceBetween>
          </KV>
          <KV label="Connection type">
            <Badge color="green">{p.connectionType}</Badge>
          </KV>
        </ColumnLayout>
      </Container>

      {/* Connection */}
      <Container
        header={
          <Header
            variant="h2"
            description="How the platform connects to this OEM to receive telemetry."
          >
            Connection
          </Header>
        }
      >
        <SpaceBetween size="m">
          <ColumnLayout columns={2} variant="text-grid">
            <KV label="Connection type">
              <Badge color="green">{p.connectionType}</Badge>
            </KV>
            <KV label="Auth type">
              <Badge color="grey">{p.authType}</Badge>
            </KV>
            <KV
              label={
                p.connectionType === 'kafka'
                  ? 'Bootstrap servers'
                  : p.connectionType === 'grpc_streaming'
                  ? 'gRPC target'
                  : p.connectionType === 'websocket_inbound'
                  ? 'Listen endpoint'
                  : 'Endpoint URL'
              }
            >
              <CodeValue value={p.endpointUrl} />
            </KV>
            {p.authType === 'oauth2' && (
              <KV label="Token endpoint">
                {p.tokenEndpoint ? (
                  <CodeValue value={p.tokenEndpoint} />
                ) : (
                  <Box color="text-status-inactive">— (missing)</Box>
                )}
              </KV>
            )}
            {/* Connection-type-specific fields */}
            {p.connectionType === 'rest_polling' && (
              <KV label="Polling interval">
                {p.pollingIntervalSeconds !== undefined ? (
                  `${p.pollingIntervalSeconds}s`
                ) : (
                  <Box color="text-status-inactive">—</Box>
                )}
              </KV>
            )}
            {p.connectionType === 'grpc_streaming' && (
              <>
                <KV label="gRPC service">
                  {p.grpcServiceName ? (
                    <CodeValue value={p.grpcServiceName} />
                  ) : (
                    <Box color="text-status-inactive">—</Box>
                  )}
                </KV>
                <KV label="RPC method">
                  {p.grpcMethodName ? (
                    <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}>
                      {p.grpcMethodName}
                    </span>
                  ) : (
                    <Box color="text-status-inactive">—</Box>
                  )}
                </KV>
              </>
            )}
            {p.connectionType === 'kafka' && (
              <>
                <KV label="Topic">
                  {p.kafkaTopic ? (
                    <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}>
                      {p.kafkaTopic}
                    </span>
                  ) : (
                    <Box color="text-status-inactive">—</Box>
                  )}
                </KV>
                <KV label="Consumer group">
                  {p.kafkaConsumerGroup ? (
                    <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}>
                      {p.kafkaConsumerGroup}
                    </span>
                  ) : (
                    <Box color="text-status-inactive">—</Box>
                  )}
                </KV>
              </>
            )}
            {p.connectionType === 'websocket_inbound' && (
              <KV label="Allowed origin">
                {p.wsAllowedOrigin ? (
                  <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}>
                    {p.wsAllowedOrigin}
                  </span>
                ) : (
                  <Box color="text-status-inactive">—</Box>
                )}
              </KV>
            )}
            {/* Auth-type-specific fields */}
            {p.authType === 'oauth2' && p.oauthScopes && (
              <KV label="Scopes">
                <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}>
                  {p.oauthScopes}
                </span>
              </KV>
            )}
            {p.authType === 'api_key' && (
              <KV label="Header name">
                {p.apiKeyHeaderName ? (
                  <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}>
                    {p.apiKeyHeaderName}
                  </span>
                ) : (
                  <Box color="text-status-inactive">— (default X-API-Key)</Box>
                )}
              </KV>
            )}
            {p.authType === 'sasl_scram' && (
              <KV label="SCRAM mechanism">
                <Badge color="grey">{p.scramMechanism ?? 'SCRAM-SHA-512'}</Badge>
              </KV>
            )}
          </ColumnLayout>
          <KV label="Credentials Secret ARN">
            <SpaceBetween direction="horizontal" size="xs" alignItems="center">
              <span
                style={{
                  fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
                  fontSize: 13,
                  wordBreak: 'break-all',
                }}
                title="Secret Manager ARN (masked for display)"
              >
                {maskArn(p.credentialsSecretArn)}
              </span>
              <CopyButton text={p.credentialsSecretArn} label="Copy full ARN" />
              <Box color="text-status-inactive" fontSize="body-s">
                Reference only — credentials never leave Secrets Manager.
              </Box>
            </SpaceBetween>
          </KV>
        </SpaceBetween>
      </Container>

      {/* Tier offering — only shown for products that declare tiers. Wizard-
          added products are tier-less and get a single "Default" tier at
          subscription time, so this container is hidden for them. */}
      {p.supportedTiers && p.supportedTiers.length > 0 && (
        <Container
          header={
            <Header
              variant="h2"
              description="Tiers the producer offers for this product. Each subscription picks one."
            >
              Tier offering
            </Header>
          }
        >
          <ColumnLayout columns={3} variant="text-grid">
            {(['Basic', 'Standard', 'Premium'] as const).map((tier) => {
              const offered = (p.supportedTiers ?? []).includes(tier);
              return (
                <KV key={tier} label={tier}>
                  {offered ? (
                    <StatusIndicator type="success">Offered</StatusIndicator>
                  ) : (
                    <Box color="text-status-inactive">Not offered</Box>
                  )}
                </KV>
              );
            })}
          </ColumnLayout>
        </Container>
      )}

      {/* Signal + event mapping — the "two disparate sources" bridge. CMS
          canonical catalog on one side (fixed, internal), producer catalog
          on the other (advertised by the producer's stubbed API), and the
          operator picks pairs. Coverage stats surface how much of what CMS
          needs the producer actually provides. */}
      <SignalMappingSection productId={p.productId} producerName={p.producer} />
      <EventMappingSection productId={p.productId} producerName={p.producer} />

      {/* Fleets using this producer — the 1:N fleet ↔ data-product assignment
          surface. Any fleet in the operator's inventory can be linked to any
          number of data products; this is where those links are managed. */}
      <FleetAssignmentSection productId={p.productId} producerName={p.producer} />

      {/* Actions */}
      <Container header={<Header variant="h2">Actions</Header>}>
        <SpaceBetween direction="horizontal" size="xs">
          <Button onClick={() => navigate('/data-products/catalog')}>Back to catalog</Button>
          <Button onClick={() => navigate('/data-products')} variant="primary">
            Create subscription from this product
          </Button>
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
};

export default CatalogDetailPage;

// ─── Sub-components ────────────────────────────────────────────────────────


/**
 * FleetAssignmentSection — shows the operator's REAL fleets (fetched from
 * /api/v1/fleets) that are currently assigned to this data product, and
 * lets the operator assign new fleets or unassign existing ones.
 *
 * The relationship is 1:N: a fleet can be linked to multiple data products,
 * and a data product can serve multiple fleets. Assignments themselves are
 * session-scoped mutations on the fleetAssignments overlay in mockData.ts —
 * refresh loses them. Fleet identity comes from the real backend.
 *
 * Empty state guides the user: assigning a fleet is what makes off-board
 * vehicle enrollment possible from that fleet's "Add vehicle" flow (future
 * work — for now, enrollment happens from the subscription detail page).
 */
const FleetAssignmentSection: React.FC<{
  productId: string;
  producerName: string;
}> = ({ productId, producerName }) => {
  // Fetch real fleets. Bump forces a re-partition after Assign/Unassign
  // mutations, since the fleetAssignments overlay doesn't emit events.
  const { fleets, loading, error } = useDataProductFleets();
  const [bump, setBump] = React.useState(0);
  const [assignModalOpen, setAssignModalOpen] = React.useState(false);
  const [selectedToAssign, setSelectedToAssign] = React.useState<Set<string>>(new Set());

  const [assignedFleets, unassignedFleets] = React.useMemo(() => {
    const assigned: FleetItem[] = [];
    const unassigned: FleetItem[] = [];
    for (const f of fleets) {
      const id = (f.id ?? f.fleetId) as string;
      if (!id) continue;
      if (isFleetAssignedToProduct(id, productId)) assigned.push(f);
      else unassigned.push(f);
    }
    return [assigned, unassigned];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fleets, productId, bump]);

  const handleAssign = () => {
    let added = 0;
    selectedToAssign.forEach((fleetId) => {
      if (assignDataProductToFleet(fleetId, productId)) added += 1;
    });
    setSelectedToAssign(new Set());
    setAssignModalOpen(false);
    if (added > 0) setBump((n) => n + 1);
  };

  const handleUnassign = (fleetId: string) => {
    unassignDataProductFromFleet(fleetId, productId);
    setBump((n) => n + 1);
  };

  const idOf = (f: FleetItem) => (f.id ?? f.fleetId ?? '') as string;
  const nameOf = (f: FleetItem) => (f.name ?? f.fleetId ?? f.id ?? '(unnamed)') as string;
  const countOf = (f: FleetItem) =>
    f.vehicleCount ?? f.totalVehicles ?? f.numTotalVehicles ?? 0;

  return (
    <Container
      header={
        <Header
          variant="h2"
          counter={assignedFleets.length ? `(${assignedFleets.length})` : undefined}
          description={`Fleets currently receiving data from ${producerName}. Assigning a fleet enables off-board vehicle enrollment for that fleet. Fleets are read live from CMS's fleet management (${fleets.length} total).`}
          actions={
            <Button
              onClick={() => setAssignModalOpen(true)}
              disabled={loading || unassignedFleets.length === 0}
              iconName="add-plus"
            >
              Assign fleet
            </Button>
          }
        >
          Assigned fleets
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
      ) : assignedFleets.length === 0 ? (
        <Alert type="info">
          No fleets are assigned to {producerName} yet. Click <b>Assign fleet</b> to link one
          of your existing fleets to this data product.
        </Alert>
      ) : (
        <Table
          variant="embedded"
          items={assignedFleets}
          columnDefinitions={[
            {
              id: 'fleetName',
              header: 'Fleet',
              cell: (f: FleetItem) => <Box fontWeight="bold">{nameOf(f)}</Box>,
              minWidth: 180,
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
            {
              id: 'otherProducts',
              header: 'Other data products',
              cell: (f: FleetItem) => {
                const others = getAssignedProductIdsForFleet(idOf(f)).filter(
                  (id) => id !== productId,
                );
                if (others.length === 0) return <Box color="text-body-secondary">—</Box>;
                return (
                  <SpaceBetween direction="horizontal" size="xxs">
                    {others.map((id) => {
                      const p = getDataProductById(id);
                      return <Badge key={id}>{p?.producer ?? id}</Badge>;
                    })}
                  </SpaceBetween>
                );
              },
            },
            {
              id: 'actions',
              header: '',
              cell: (f: FleetItem) => (
                <Button
                  variant="inline-link"
                  onClick={() => handleUnassign(idOf(f))}
                  ariaLabel={`Unassign ${producerName} from ${nameOf(f)}`}
                >
                  Unassign
                </Button>
              ),
              width: 110,
            },
          ]}
        />
      )}

      {assignModalOpen && (
        <Modal
          visible={assignModalOpen}
          onDismiss={() => {
            setAssignModalOpen(false);
            setSelectedToAssign(new Set());
          }}
          header={`Assign ${producerName} to fleets`}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  variant="link"
                  onClick={() => {
                    setAssignModalOpen(false);
                    setSelectedToAssign(new Set());
                  }}
                >
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  disabled={selectedToAssign.size === 0}
                  onClick={handleAssign}
                >
                  Assign {selectedToAssign.size} fleet{selectedToAssign.size === 1 ? '' : 's'}
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            <Box>
              Select fleets to assign to <b>{producerName}</b>. Each selected fleet will be able
              to receive {producerName} data feeds. Fleets can be assigned to multiple data
              products.
            </Box>
            {unassignedFleets.length === 0 ? (
              <Alert type="info">All fleets are already assigned to {producerName}.</Alert>
            ) : (
              <Table
                variant="embedded"
                selectionType="multi"
                trackBy="id"
                items={unassignedFleets}
                selectedItems={unassignedFleets.filter((f) => selectedToAssign.has(idOf(f)))}
                onSelectionChange={({ detail }) => {
                  setSelectedToAssign(new Set(detail.selectedItems.map((f) => idOf(f))));
                }}
                columnDefinitions={[
                  {
                    id: 'fleetName',
                    header: 'Fleet',
                    cell: (f: FleetItem) => <Box fontWeight="bold">{nameOf(f)}</Box>,
                  },
                  {
                    id: 'vehicleCount',
                    header: 'Vehicles',
                    cell: (f: FleetItem) => countOf(f),
                    width: 100,
                  },
                  {
                    id: 'currentProducts',
                    header: 'Currently assigned',
                    cell: (f: FleetItem) => {
                      const ids = getAssignedProductIdsForFleet(idOf(f));
                      if (ids.length === 0)
                        return <Box color="text-body-secondary">On-board only</Box>;
                      return (
                        <SpaceBetween direction="horizontal" size="xxs">
                          {ids.map((id) => {
                            const p = getDataProductById(id);
                            return <Badge key={id}>{p?.producer ?? id}</Badge>;
                          })}
                        </SpaceBetween>
                      );
                    },
                  },
                ]}
              />
            )}
          </SpaceBetween>
        </Modal>
      )}
    </Container>
  );
};


// ─── Signal mapping (pick-and-match) ──────────────────────────────────────
//
// Replaces the old free-form Signal Mapping table. The two catalogs — CMS
// canonical (curated demo subset of services/data_processing/signal-catalog.json)
// and producer advertisement (stubbed per producer from a hypothetical
// CS-side /available-signals API) — meet here. The operator sees every CMS
// signal grouped by category, and for each one picks a matching producer
// signal from a dropdown (or leaves it Unmapped). Coverage stats live in
// the section header, and an Auto-map button seeds obvious matches by
// name similarity.
//
// State model: signalMappings is stored on the data product (session
// overlay in mockData.ts). Each row is (cmsField, sourceSignal, ...); the
// pick-and-match UI writes the pair and derives sourcePath/dataType/unit
// from the producer's advertised metadata. Mapping is 1:1 CMS → producer;
// changing a CMS row's producer signal replaces whichever mapping was
// there before.

const SignalMappingSection: React.FC<{
  productId: string;
  producerName: string;
}> = ({ productId, producerName }) => {
  const [bump, setBump] = React.useState(0);
  const mappings = React.useMemo(
    () => getSignalMappingsFor(productId),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [productId, bump],
  );
  const producerSignals = React.useMemo(
    () => getProducerSignals(producerName),
    [producerName],
  );

  // Fast lookup: CMS field name → mapping.
  const mappingByCmsField = React.useMemo(() => {
    const m = new Map<string, SignalMapping>();
    for (const row of mappings) m.set(row.cmsField, row);
    return m;
  }, [mappings]);

  // Group CMS signals by their category (location, tires, safety_systems,
  // etc.) so the section reads like the underlying signal catalog rather
  // than an alphabetical flat list.
  const grouped = React.useMemo(() => {
    const g: Record<string, CmsCanonicalSignal[]> = {};
    for (const s of CMS_CANONICAL_SIGNALS) {
      if (!g[s.group]) g[s.group] = [];
      g[s.group].push(s);
    }
    return g;
  }, []);

  const setMapping = (cms: CmsCanonicalSignal, producer: ProducerSignal | undefined) => {
    const existing = mappings.filter((m) => m.cmsField !== cms.name);
    if (!producer) {
      setSignalMappingsFor(productId, existing);
    } else {
      const row: SignalMapping = {
        cmsField: cms.name,
        sourceSignal: producer.name,
        sourcePath: producer.path,
        dataType: producer.dataType,
        unit: cms.unit ?? producer.unit,
        required: cms.required,
      };
      setSignalMappingsFor(productId, [...existing, row]);
    }
    setBump((n) => n + 1);
  };

  const autoMap = () => {
    const next: SignalMapping[] = [];
    for (const cms of CMS_CANONICAL_SIGNALS) {
      const existing = mappingByCmsField.get(cms.name);
      if (existing) {
        next.push(existing);
        continue;
      }
      const guess = autoMatchSignal(cms.name, producerSignals);
      if (!guess) continue;
      next.push({
        cmsField: cms.name,
        sourceSignal: guess.name,
        sourcePath: guess.path,
        dataType: guess.dataType,
        unit: cms.unit ?? guess.unit,
        required: cms.required,
      });
    }
    setSignalMappingsFor(productId, next);
    setBump((n) => n + 1);
  };

  const clearAll = () => {
    setSignalMappingsFor(productId, []);
    setBump((n) => n + 1);
  };

  const mappedCount = mappings.length;
  const totalCms = CMS_CANONICAL_SIGNALS.length;
  const requiredMapped = CMS_CANONICAL_SIGNALS.filter(
    (s) => s.required && mappingByCmsField.has(s.name),
  ).length;
  const requiredTotal = CMS_CANONICAL_SIGNALS.filter((s) => s.required).length;
  const producerTotal = producerSignals.length;

  return (
    <Container
      header={
        <Header
          variant="h2"
          counter={`(${mappedCount} of ${totalCms})`}
          description={`CMS canonical signals on the left, ${producerName}'s advertised signals on the right. Pick pairs to define the transform manifest — this is the schema CMS will apply to inbound telemetry from ${producerName}.`}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                onClick={clearAll}
                disabled={mappedCount === 0}
              >
                Clear all
              </Button>
              <Button
                onClick={autoMap}
                variant="primary"
                iconName="external"
                disabled={producerTotal === 0}
              >
                Auto-map by name
              </Button>
            </SpaceBetween>
          }
        >
          Signal mapping
        </Header>
      }
    >
      <SpaceBetween size="l">
        {/* Coverage stats — quick health check on the mapping's completeness. */}
        <ColumnLayout columns={4} variant="text-grid">
          <Box>
            <Box color="text-label" fontSize="body-s" fontWeight="bold">
              CMS signals mapped
            </Box>
            <Box>
              {mappedCount} of {totalCms}
            </Box>
          </Box>
          <Box>
            <Box color="text-label" fontSize="body-s" fontWeight="bold">
              Required signals mapped
            </Box>
            <Box>
              {requiredMapped === requiredTotal ? (
                <StatusIndicator type="success">
                  {requiredMapped} of {requiredTotal}
                </StatusIndicator>
              ) : (
                <StatusIndicator type="warning">
                  {requiredMapped} of {requiredTotal}
                </StatusIndicator>
              )}
            </Box>
          </Box>
          <Box>
            <Box color="text-label" fontSize="body-s" fontWeight="bold">
              Producer signals available
            </Box>
            <Box>{producerTotal}</Box>
          </Box>
          <Box>
            <Box color="text-label" fontSize="body-s" fontWeight="bold">
              Producer
            </Box>
            <Box>
              <Badge color="green">{producerName}</Badge>
            </Box>
          </Box>
        </ColumnLayout>

        {producerTotal === 0 && (
          <Alert type="warning">
            {producerName} hasn't advertised any signals in this demo. Mapping is disabled
            until the producer's /available-signals API returns entries.
          </Alert>
        )}

        {/* One table per CMS signal group, each with pick-and-match rows.
            Each row's Select is populated from the full producer catalog +
            an 'Unmapped' sentinel. Rows highlight when a required CMS
            signal is still unmapped — that's a red flag on the manifest's
            completeness. */}
        {Object.keys(grouped)
          .sort()
          .map((group) => (
            <Container
              key={group}
              header={
                <Header
                  variant="h3"
                  counter={`(${grouped[group].filter((s) => mappingByCmsField.has(s.name)).length} of ${grouped[group].length})`}
                >
                  {group.replace(/_/g, ' ')}
                </Header>
              }
            >
              <Table
                variant="embedded"
                items={grouped[group]}
                columnDefinitions={[
                  {
                    id: 'cmsSignal',
                    header: 'CMS canonical signal',
                    cell: (s: CmsCanonicalSignal) => (
                      <SpaceBetween direction="horizontal" size="xxs" alignItems="center">
                        <Box fontFamily="monospace" fontWeight="bold">
                          {s.name}
                        </Box>
                        {s.required && <Badge color="red">required</Badge>}
                      </SpaceBetween>
                    ),
                    minWidth: 200,
                  },
                  {
                    id: 'cmsMeta',
                    header: 'Type / unit',
                    cell: (s: CmsCanonicalSignal) => (
                      <SpaceBetween direction="horizontal" size="xxs">
                        <Badge>{s.dataType}</Badge>
                        {s.unit && <Box color="text-body-secondary">{s.unit}</Box>}
                      </SpaceBetween>
                    ),
                    width: 130,
                  },
                  {
                    id: 'description',
                    header: 'Description',
                    cell: (s: CmsCanonicalSignal) => (
                      <Box color="text-body-secondary" fontSize="body-s">
                        {s.description}
                      </Box>
                    ),
                  },
                  {
                    id: 'producer',
                    header: `${producerName} signal`,
                    cell: (s: CmsCanonicalSignal) => {
                      const mapped = mappingByCmsField.get(s.name);
                      const selected = mapped
                        ? producerSignals.find((p) => p.name === mapped.sourceSignal)
                        : undefined;
                      return (
                        <Select
                          selectedOption={
                            selected
                              ? {
                                  label: selected.name,
                                  value: selected.name,
                                  description: selected.path,
                                }
                              : { label: 'Unmapped', value: '' }
                          }
                          options={[
                            { label: 'Unmapped', value: '' },
                            ...producerSignals.map((p) => ({
                              label: p.name,
                              value: p.name,
                              description: p.path,
                            })),
                          ]}
                          onChange={({ detail }) => {
                            const val = detail.selectedOption.value;
                            if (!val) {
                              setMapping(s, undefined);
                            } else {
                              const p = producerSignals.find((x) => x.name === val);
                              setMapping(s, p);
                            }
                          }}
                          disabled={producerTotal === 0}
                          placeholder="Pick a signal"
                          expandToViewport
                        />
                      );
                    },
                    minWidth: 260,
                  },
                ]}
              />
            </Container>
          ))}
      </SpaceBetween>
    </Container>
  );
};

// ─── Event mapping (pick-and-match) ───────────────────────────────────────

const EventMappingSection: React.FC<{
  productId: string;
  producerName: string;
}> = ({ productId, producerName }) => {
  const [bump, setBump] = React.useState(0);
  const mappings = React.useMemo(
    () => getEventMappingsFor(productId),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [productId, bump],
  );
  const producerEvents = React.useMemo(
    () => getProducerEvents(producerName),
    [producerName],
  );

  const mappingByCmsEvent = React.useMemo(() => {
    const m = new Map<string, EventMapping>();
    for (const row of mappings) m.set(row.cmsEvent, row);
    return m;
  }, [mappings]);

  const groupedEvents = React.useMemo(() => {
    const g: Record<string, CmsCanonicalEvent[]> = {};
    for (const e of CMS_CANONICAL_EVENTS) {
      if (!g[e.group]) g[e.group] = [];
      g[e.group].push(e);
    }
    return g;
  }, []);

  const setEventMapping = (cms: CmsCanonicalEvent, producer: ProducerEvent | undefined) => {
    const existing = mappings.filter((m) => m.cmsEvent !== cms.name);
    if (!producer) {
      setEventMappingsFor(productId, existing);
    } else {
      setEventMappingsFor(productId, [
        ...existing,
        { cmsEvent: cms.name, sourceEvent: producer.name },
      ]);
    }
    setBump((n) => n + 1);
  };

  const autoMap = () => {
    const next: EventMapping[] = [];
    for (const cms of CMS_CANONICAL_EVENTS) {
      const existing = mappingByCmsEvent.get(cms.name);
      if (existing) {
        next.push(existing);
        continue;
      }
      const guess = autoMatchEvent(cms.name, producerEvents);
      if (!guess) continue;
      next.push({ cmsEvent: cms.name, sourceEvent: guess.name });
    }
    setEventMappingsFor(productId, next);
    setBump((n) => n + 1);
  };

  const clearAll = () => {
    setEventMappingsFor(productId, []);
    setBump((n) => n + 1);
  };

  const totalCms = CMS_CANONICAL_EVENTS.length;
  const mappedCount = mappings.length;
  const producerTotal = producerEvents.length;

  return (
    <Container
      header={
        <Header
          variant="h2"
          counter={`(${mappedCount} of ${totalCms})`}
          description={`CMS canonical events on the left, ${producerName}'s advertised events on the right. Producers rarely use CMS's naming — pick pairs so downstream fleet-safety, maintenance, and reporting flows see events under CMS's canonical names.`}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={clearAll} disabled={mappedCount === 0}>
                Clear all
              </Button>
              <Button
                onClick={autoMap}
                variant="primary"
                iconName="external"
                disabled={producerTotal === 0}
              >
                Auto-map by name
              </Button>
            </SpaceBetween>
          }
        >
          Event mapping
        </Header>
      }
    >
      <SpaceBetween size="l">
        <ColumnLayout columns={3} variant="text-grid">
          <Box>
            <Box color="text-label" fontSize="body-s" fontWeight="bold">
              CMS events mapped
            </Box>
            <Box>
              {mappedCount} of {totalCms}
            </Box>
          </Box>
          <Box>
            <Box color="text-label" fontSize="body-s" fontWeight="bold">
              Producer events available
            </Box>
            <Box>{producerTotal}</Box>
          </Box>
          <Box>
            <Box color="text-label" fontSize="body-s" fontWeight="bold">
              Producer
            </Box>
            <Box>
              <Badge color="green">{producerName}</Badge>
            </Box>
          </Box>
        </ColumnLayout>

        {producerTotal === 0 && (
          <Alert type="warning">
            {producerName} hasn't advertised any events in this demo. Event mapping is
            disabled until the producer's /available-events API returns entries.
          </Alert>
        )}

        {Object.keys(groupedEvents)
          .sort()
          .map((group) => (
            <Container
              key={group}
              header={
                <Header
                  variant="h3"
                  counter={`(${groupedEvents[group].filter((e) => mappingByCmsEvent.has(e.name)).length} of ${groupedEvents[group].length})`}
                >
                  {group}
                </Header>
              }
            >
              <Table
                variant="embedded"
                items={groupedEvents[group]}
                columnDefinitions={[
                  {
                    id: 'cmsEvent',
                    header: 'CMS canonical event',
                    cell: (e: CmsCanonicalEvent) => (
                      <Box fontFamily="monospace" fontWeight="bold">
                        {e.name}
                      </Box>
                    ),
                    minWidth: 200,
                  },
                  {
                    id: 'severity',
                    header: 'Severity',
                    cell: (e: CmsCanonicalEvent) => (
                      <Badge
                        color={
                          e.severity === 'critical'
                            ? 'red'
                            : e.severity === 'warning'
                            ? 'grey'
                            : 'blue'
                        }
                      >
                        {e.severity}
                      </Badge>
                    ),
                    width: 110,
                  },
                  {
                    id: 'description',
                    header: 'Description',
                    cell: (e: CmsCanonicalEvent) => (
                      <Box color="text-body-secondary" fontSize="body-s">
                        {e.description}
                      </Box>
                    ),
                  },
                  {
                    id: 'producerEvent',
                    header: `${producerName} event`,
                    cell: (e: CmsCanonicalEvent) => {
                      const mapped = mappingByCmsEvent.get(e.name);
                      const selected = mapped
                        ? producerEvents.find((p) => p.name === mapped.sourceEvent)
                        : undefined;
                      return (
                        <Select
                          selectedOption={
                            selected
                              ? {
                                  label: selected.name,
                                  value: selected.name,
                                  description: `severity: ${selected.severity}`,
                                }
                              : { label: 'Unmapped', value: '' }
                          }
                          options={[
                            { label: 'Unmapped', value: '' },
                            ...producerEvents.map((p) => ({
                              label: p.name,
                              value: p.name,
                              description: `severity: ${p.severity}`,
                            })),
                          ]}
                          onChange={({ detail }) => {
                            const val = detail.selectedOption.value;
                            if (!val) {
                              setEventMapping(e, undefined);
                            } else {
                              const p = producerEvents.find((x) => x.name === val);
                              setEventMapping(e, p);
                            }
                          }}
                          disabled={producerTotal === 0}
                          placeholder="Pick an event"
                          expandToViewport
                        />
                      );
                    },
                    minWidth: 260,
                  },
                ]}
              />
            </Container>
          ))}
      </SpaceBetween>
    </Container>
  );
};
