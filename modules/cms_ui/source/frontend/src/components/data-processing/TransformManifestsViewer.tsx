// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// TransformManifestsViewer — lists transform manifests and shows a demo-ready
// details modal for each one.
//
// The details modal is the demo surface for the cloud-to-cloud integration:
// it exposes the endpoints, the credentials pointer (masked, referenced —
// never embedded), and the signal/event mappings that normalise OEM
// telemetry into the CMS Signal Catalog.
//
// Schema reference: `services/data_processing/transform-manifest-schema.json`
// (v2.2.0). This viewer surfaces the schema's fields as structured sections
// rather than a raw JSON dump — see the details-modal render for the layout.

import React, { useState, useEffect } from 'react';
import {
  Table,
  Box,
  SpaceBetween,
  Header,
  Button,
  Modal,
  StatusIndicator,
  Badge,
  ColumnLayout,
  ExpandableSection,
  Container,
  Pagination,
  TextFilter,
  Popover,
  Alert,
} from '@cloudscape-design/components';
import { useCollection } from '@cloudscape-design/collection-hooks';
import { getDataProcessingApiEndpoint } from '../../config/api';
import { authFetch } from '../../utils/authFetch';

// ─── Types ─────────────────────────────────────────────────────────────────
// Kept close to `services/data_processing/transform-manifest-schema.json`.
// All fields optional because manifest content may be partial or v2.0 (pre
// message_type_routing / event_mappings).

interface Manifest {
  name: string;
  source_type: string;
  last_modified: string;
  size: number;
}

interface SignalMapping {
  source_signal?: string;
  cms_field: string;
  source_path: string;
  data_type?: 'integer' | 'float' | 'string' | 'boolean';
  required?: boolean;
  default_value?: unknown;
  unit_conversion?: string;
  conversion?: {
    type?: string;
    factor?: number;
    offset?: number;
    formula?: string;
  };
  value_map?: Record<string, unknown>;
}

interface EventMapping {
  source_event_type_url: string;
  cms_event_type: string;
  match?: Record<string, string>;
  extraction?: Record<string, string>;
  tag_aliases?: Record<string, string>;
  uniqueness_key?: string[];
}

interface ManifestContent {
  manifest_version?: string;
  transform_type?: string;
  source_name?: string;
  source_format?: string;
  connection?: {
    type?: 'rest_polling' | 'grpc_streaming' | 'websocket_inbound';
    polling_interval_seconds?: number;
  };
  authentication?: {
    type?: 'oauth2' | 'api_key' | 'mtls';
    credentials_secret_arn?: string;
    token_endpoint?: string;
  };
  vehicle_id_extraction?: {
    strategy?: 'json_path' | 'direct';
    path?: string;
    transform?: string | null;
  };
  timestamp_field?: string;
  timestamp_format?: 'iso8601' | 'epoch_seconds' | 'epoch_milliseconds';
  timestamp?: {
    modem_field?: string;
    ingestion_field?: string;
    primary?: 'modem' | 'ingestion';
  };
  message_type_routing?: {
    field?: string;
    telemetry_patterns?: string[];
    event_patterns?: string[];
    discard_patterns?: string[];
  };
  event_mappings?: EventMapping[];
  signal_mappings?: SignalMapping[];
  metadata?: {
    created_by?: string | Record<string, unknown>;
    created_at?: string;
    version?: string;
    description?: string;
    deferred_signals?: Array<{ source_signal: string; reason: string }>;
    subscription_tier?: string;
  };
}

// ─── Utilities ─────────────────────────────────────────────────────────────

/**
 * Mask a Secrets Manager ARN for display. The ARN itself is not sensitive
 * (arns are catalogue metadata), but rendering it with visible masking
 * signals to demo viewers that "credentials are referenced by ARN, not
 * embedded here." The visible cue is intentional — pair with a "Copy full
 * ARN" affordance for anyone who needs the actual value.
 *
 * Input:  arn:aws:secretsmanager:us-west-2:123456789012:secret:OEM1-Prod-a1b2c3
 * Output: arn:aws:secretsmanager:us-west-2:123…012:secret:O…b2c3
 *
 * Note the colon before the secret name — that is the real Secrets Manager ARN
 * shape, and it is what this function requires. An ARN that delimits the name
 * with a slash has only 6 colon-parts, fails the `parts.length < 7` guard, and
 * is returned UNMASKED. See issues/2026-09-15-account-id-in-data-products-mock-arns.
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

/** Best-effort clipboard copy. Some browsers require user-gesture context. */
async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** Copy-icon button with a Popover confirmation on click. */
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

/** Monospace value with an inline Copy button. */
const CodeValue: React.FC<{ value: string; copyLabel?: string }> = ({
  value,
  copyLabel,
}) => (
  <SpaceBetween direction="horizontal" size="xs" alignItems="center">
    <span style={{ fontFamily: 'ui-monospace, Menlo, Consolas, monospace', fontSize: 13, wordBreak: 'break-all' }}>
      {value}
    </span>
    <CopyButton text={value} label={copyLabel} />
  </SpaceBetween>
);

/** Key-value row in a details section — label + value in a two-column layout. */
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

/** Small helper — render a value or a dim "—" fallback. */
const orDash = (v: unknown, fallback: string = '—') =>
  v === null || v === undefined || v === '' ? (
    <Box color="text-status-inactive">{fallback}</Box>
  ) : (
    <>{String(v)}</>
  );

/**
 * Render a signal mapping's "Conversion" cell — could be a named unit
 * conversion, a custom formula/factor/offset, or a value_map. Choose the
 * most specific one available.
 */
function renderConversion(m: SignalMapping): React.ReactNode {
  if (m.unit_conversion) {
    return <Badge color="blue">{m.unit_conversion}</Badge>;
  }
  if (m.conversion) {
    const { type, factor, offset, formula } = m.conversion;
    if (formula) return <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{formula}</span>;
    const bits: string[] = [];
    if (type) bits.push(type);
    if (factor !== undefined) bits.push(`× ${factor}`);
    if (offset !== undefined) bits.push(`+ ${offset}`);
    return <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{bits.join(' ')}</span>;
  }
  if (m.value_map) {
    const entries = Object.entries(m.value_map);
    const shown = entries.slice(0, 4);
    return (
      <SpaceBetween direction="horizontal" size="xxs">
        {shown.map(([k, v]) => (
          <Badge key={k} color="grey">{`${k}:${String(v)}`}</Badge>
        ))}
        {entries.length > shown.length && (
          <Box color="text-status-inactive" fontSize="body-s">
            +{entries.length - shown.length} more
          </Box>
        )}
      </SpaceBetween>
    );
  }
  return <Box color="text-status-inactive">—</Box>;
}

// ─── Signal Mappings Table (paginated, filterable) ─────────────────────────

const SignalMappingsTable: React.FC<{ mappings: SignalMapping[] }> = ({ mappings }) => {
  const {
    items,
    filteredItemsCount,
    collectionProps,
    filterProps,
    paginationProps,
  } = useCollection(mappings, {
    filtering: {
      empty: (
        <Box textAlign="center" color="inherit">
          <b>No signal mappings</b>
        </Box>
      ),
      noMatch: (
        <Box textAlign="center" color="inherit">
          <b>No matches</b>
          <Box variant="p" color="inherit">
            Adjust the filter to find signals.
          </Box>
        </Box>
      ),
      filteringFunction: (item, filteringText) => {
        const t = filteringText.toLowerCase();
        return (
          (item.source_signal ?? '').toLowerCase().includes(t) ||
          (item.source_path ?? '').toLowerCase().includes(t) ||
          (item.cms_field ?? '').toLowerCase().includes(t) ||
          (item.data_type ?? '').toLowerCase().includes(t) ||
          (item.unit_conversion ?? '').toLowerCase().includes(t)
        );
      },
    },
    pagination: { pageSize: 25 },
    sorting: {},
  });

  return (
    <Table
      {...collectionProps}
      items={items}
      variant="embedded"
      resizableColumns
      stickyHeader
      header={
        <Header
          counter={`(${mappings.length})`}
          description="OEM signal → CMS Signal Catalog field. cms_field values must match json_field in the signal catalog."
        >
          Signal mappings
        </Header>
      }
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
          id: 'source_signal',
          header: 'Source signal',
          cell: (m) => orDash(m.source_signal),
          sortingField: 'source_signal',
        },
        {
          id: 'source_path',
          header: 'Source path',
          cell: (m) => (
            <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{m.source_path}</span>
          ),
          sortingField: 'source_path',
        },
        {
          id: 'arrow',
          header: '',
          cell: () => <Box color="text-status-inactive">→</Box>,
        },
        {
          id: 'cms_field',
          header: 'CMS field',
          cell: (m) => (
            <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{m.cms_field}</span>
          ),
          sortingField: 'cms_field',
        },
        {
          id: 'data_type',
          header: 'Type',
          cell: (m) => (m.data_type ? <Badge>{m.data_type}</Badge> : orDash(null)),
          sortingField: 'data_type',
        },
        {
          id: 'conversion',
          header: 'Conversion',
          cell: (m) => renderConversion(m),
        },
        {
          id: 'required',
          header: 'Req.',
          cell: (m) => (m.required ? <StatusIndicator type="success">yes</StatusIndicator> : orDash(null)),
        },
      ]}
    />
  );
};

// ─── Event Mappings Table (only rendered if present) ───────────────────────

const EventMappingsTable: React.FC<{ mappings: EventMapping[] }> = ({ mappings }) => (
  <Table
    variant="embedded"
    items={mappings}
    header={
      <Header
        counter={`(${mappings.length})`}
        description="OEM event → CMS canonical event type."
      >
        Event mappings
      </Header>
    }
    columnDefinitions={[
      {
        id: 'source_event_type_url',
        header: 'Source event type',
        cell: (m: EventMapping) => (
          <span style={{ fontFamily: 'monospace', fontSize: 12, wordBreak: 'break-all' }}>
            {m.source_event_type_url}
          </span>
        ),
      },
      {
        id: 'arrow',
        header: '',
        cell: () => <Box color="text-status-inactive">→</Box>,
      },
      {
        id: 'cms_event_type',
        header: 'CMS event type',
        cell: (m: EventMapping) => (
          <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{m.cms_event_type}</span>
        ),
      },
      {
        id: 'match',
        header: 'Match conditions',
        cell: (m: EventMapping) =>
          m.match && Object.keys(m.match).length > 0 ? (
            <SpaceBetween direction="horizontal" size="xxs">
              {Object.entries(m.match).map(([k, v]) => (
                <Badge key={k} color="grey">{`${k}=${v}`}</Badge>
              ))}
            </SpaceBetween>
          ) : (
            orDash(null)
          ),
      },
      {
        id: 'extraction',
        header: 'Extraction paths',
        cell: (m: EventMapping) =>
          m.extraction && Object.keys(m.extraction).length > 0 ? (
            <Box color="text-body-secondary" fontSize="body-s">
              {Object.keys(m.extraction).length} field
              {Object.keys(m.extraction).length === 1 ? '' : 's'}
            </Box>
          ) : (
            orDash(null)
          ),
      },
    ]}
  />
);

// ─── Details Modal Body ────────────────────────────────────────────────────
//
// Structured, section-based view over the raw manifest JSON. Sections are
// only rendered when their underlying schema block is present.

const ManifestDetails: React.FC<{ content: ManifestContent }> = ({ content }) => {
  const c = content;
  const conn = c.connection ?? {};
  const auth = c.authentication ?? {};
  const vid = c.vehicle_id_extraction ?? {};
  const ts = c.timestamp ?? {};
  const routing = c.message_type_routing ?? {};
  const meta = c.metadata ?? {};

  const hasRouting =
    (routing.telemetry_patterns?.length ?? 0) > 0 ||
    (routing.event_patterns?.length ?? 0) > 0 ||
    (routing.discard_patterns?.length ?? 0) > 0;

  const events = c.event_mappings ?? [];
  const signals = c.signal_mappings ?? [];

  return (
    <SpaceBetween size="l">
      {/* Overview */}
      <Container header={<Header variant="h3">Overview</Header>}>
        <ColumnLayout columns={4} variant="text-grid">
          <KV label="Source name">{orDash(c.source_name)}</KV>
          <KV label="Transform type">
            {c.transform_type ? <Badge color="blue">{c.transform_type}</Badge> : orDash(null)}
          </KV>
          <KV label="Schema version">{orDash(c.manifest_version)}</KV>
          <KV label="Source format">{orDash(c.source_format)}</KV>
        </ColumnLayout>
      </Container>

      {/* Connection — the demo surface */}
      <Container
        header={
          <Header
            variant="h3"
            description="How CMS pulls or receives OEM telemetry. Credentials are referenced by ARN — never embedded in the manifest."
          >
            Connection
          </Header>
        }
      >
        <ColumnLayout columns={2} variant="text-grid">
          <KV label="Connection type">
            {conn.type ? <Badge color="green">{conn.type}</Badge> : orDash(null)}
          </KV>
          <KV label="Polling interval">
            {conn.polling_interval_seconds !== undefined
              ? `${conn.polling_interval_seconds}s`
              : orDash(null)}
          </KV>
          <KV label="Auth type">
            {auth.type ? <Badge color="green">{auth.type}</Badge> : orDash(null)}
          </KV>
          <KV label="Token endpoint">
            {auth.token_endpoint ? (
              <CodeValue value={auth.token_endpoint} copyLabel="Copy token endpoint" />
            ) : (
              orDash(null)
            )}
          </KV>
        </ColumnLayout>
        <Box padding={{ top: 'm' }}>
          <KV label="Credentials Secret ARN">
            {auth.credentials_secret_arn ? (
              <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                <span
                  style={{
                    fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
                    fontSize: 13,
                    wordBreak: 'break-all',
                  }}
                  title="Secret Manager ARN (masked for display)"
                >
                  {maskArn(auth.credentials_secret_arn)}
                </span>
                <CopyButton
                  text={auth.credentials_secret_arn}
                  label="Copy full ARN"
                />
                <Box color="text-status-inactive" fontSize="body-s">
                  Reference only — credentials never leave Secrets Manager.
                </Box>
              </SpaceBetween>
            ) : (
              orDash(null)
            )}
          </KV>
        </Box>
      </Container>

      {/* Vehicle ID extraction */}
      <Container header={<Header variant="h3">Vehicle ID extraction</Header>}>
        <ColumnLayout columns={3} variant="text-grid">
          <KV label="Strategy">
            {vid.strategy ? <Badge>{vid.strategy}</Badge> : orDash(null)}
          </KV>
          <KV label="Path">
            {vid.path ? (
              <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{vid.path}</span>
            ) : (
              orDash(null)
            )}
          </KV>
          <KV label="Transform">{orDash(vid.transform)}</KV>
        </ColumnLayout>
      </Container>

      {/* Timestamp */}
      <Container header={<Header variant="h3">Timestamp</Header>}>
        <ColumnLayout columns={3} variant="text-grid">
          <KV label="Primary source">
            {ts.primary ? <Badge color="grey">{ts.primary}</Badge> : orDash(c.timestamp_field ?? null)}
          </KV>
          <KV label="Modem field">
            {ts.modem_field ? (
              <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{ts.modem_field}</span>
            ) : (
              orDash(null)
            )}
          </KV>
          <KV label="Ingestion field">
            {ts.ingestion_field ? (
              <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{ts.ingestion_field}</span>
            ) : (
              orDash(c.timestamp_field ?? null)
            )}
          </KV>
          <KV label="Format">{orDash(c.timestamp_format)}</KV>
        </ColumnLayout>
      </Container>

      {/* Message routing — only if present */}
      {hasRouting && (
        <Container
          header={
            <Header
              variant="h3"
              description={`Routes each message based on the value at ${routing.field ?? '(field)'}.`}
            >
              Message routing
            </Header>
          }
        >
          <SpaceBetween size="s">
            {routing.telemetry_patterns && routing.telemetry_patterns.length > 0 && (
              <KV label={`Telemetry patterns (${routing.telemetry_patterns.length})`}>
                <SpaceBetween direction="horizontal" size="xxs">
                  {routing.telemetry_patterns.map((p) => (
                    <Badge key={`t-${p}`} color="green">{p}</Badge>
                  ))}
                </SpaceBetween>
              </KV>
            )}
            {routing.event_patterns && routing.event_patterns.length > 0 && (
              <KV label={`Event patterns (${routing.event_patterns.length})`}>
                <SpaceBetween direction="horizontal" size="xxs">
                  {routing.event_patterns.map((p) => (
                    <Badge key={`e-${p}`} color="blue">{p}</Badge>
                  ))}
                </SpaceBetween>
              </KV>
            )}
            {routing.discard_patterns && routing.discard_patterns.length > 0 && (
              <KV label={`Discard patterns (${routing.discard_patterns.length})`}>
                <SpaceBetween direction="horizontal" size="xxs">
                  {routing.discard_patterns.map((p) => (
                    <Badge key={`d-${p}`} color="red">{p}</Badge>
                  ))}
                </SpaceBetween>
              </KV>
            )}
          </SpaceBetween>
        </Container>
      )}

      {/* Signal mappings — always rendered, even if empty (schema requires it) */}
      <SignalMappingsTable mappings={signals} />

      {/* Event mappings — only if present */}
      {events.length > 0 && <EventMappingsTable mappings={events} />}

      {/* Metadata */}
      <Container header={<Header variant="h3">Metadata</Header>}>
        <ColumnLayout columns={2} variant="text-grid">
          <KV label="Created by">
            {typeof meta.created_by === 'string'
              ? meta.created_by
              : meta.created_by
                ? JSON.stringify(meta.created_by)
                : orDash(null)}
          </KV>
          <KV label="Created at">
            {meta.created_at ? new Date(meta.created_at).toLocaleString() : orDash(null)}
          </KV>
          <KV label="Version">{orDash(meta.version)}</KV>
          <KV label="Subscription tier">
            {meta.subscription_tier ? <Badge color="grey">{meta.subscription_tier}</Badge> : orDash(null)}
          </KV>
        </ColumnLayout>
        {meta.description && (
          <Box padding={{ top: 'm' }}>
            <KV label="Description">{meta.description}</KV>
          </Box>
        )}
        {meta.deferred_signals && meta.deferred_signals.length > 0 && (
          <Box padding={{ top: 'm' }}>
            <ExpandableSection
              headerText={`Deferred signals (${meta.deferred_signals.length})`}
              headerDescription="OEM signals with no current catalog match — explicitly tracked rather than silently dropped."
            >
              <Table
                variant="embedded"
                items={meta.deferred_signals}
                columnDefinitions={[
                  {
                    id: 'source_signal',
                    header: 'Source signal',
                    cell: (d: { source_signal: string; reason: string }) => (
                      <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{d.source_signal}</span>
                    ),
                  },
                  {
                    id: 'reason',
                    header: 'Reason',
                    cell: (d) => d.reason,
                  },
                ]}
              />
            </ExpandableSection>
          </Box>
        )}
      </Container>

      {/* Raw JSON — the engineer's escape hatch */}
      <ExpandableSection
        headerText="Raw manifest JSON"
        headerDescription="The full manifest as returned by the API — for engineers verifying the exact shape."
      >
        <Box padding="s" variant="code">
          <pre
            style={{
              fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
              fontSize: 12,
              overflow: 'auto',
              maxHeight: 500,
              margin: 0,
            }}
          >
            {JSON.stringify(content, null, 2)}
          </pre>
        </Box>
      </ExpandableSection>
    </SpaceBetween>
  );
};

// ─── Top-level TransformManifestsViewer ────────────────────────────────────

interface TransformManifestsViewerProps {
  onAddIntegration: () => void;
}

const TransformManifestsViewer: React.FC<TransformManifestsViewerProps> = ({ onAddIntegration }) => {
  const [manifests, setManifests] = useState<Manifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedManifest, setSelectedManifest] = useState<Manifest | null>(null);
  const [manifestContent, setManifestContent] = useState<ManifestContent | null>(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [contentError, setContentError] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(false);

  const loadManifests = () => {
    setLoading(true);
    setError(null);
    authFetch(`${getDataProcessingApiEndpoint()}manifests`)
      .then((res) => res.json())
      .then((data) => {
        setManifests(data.manifests || []);
        setLoading(false);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : 'Failed to load manifests');
        setLoading(false);
      });
  };

  useEffect(() => {
    loadManifests();
  }, []);

  const viewDetails = (manifest: Manifest) => {
    setSelectedManifest(manifest);
    setShowDetails(true);
    setManifestContent(null);
    setContentLoading(true);
    setContentError(null);
    authFetch(`${getDataProcessingApiEndpoint()}manifests?name=${manifest.name}`)
      .then((res) => res.json())
      .then((data) => {
        // Response may be either the manifest directly OR wrapped under `.manifest`.
        // Handle both defensively so the viewer works regardless of the backend shape.
        const content: ManifestContent =
          data?.manifest && typeof data.manifest === 'object'
            ? data.manifest
            : (data as ManifestContent);
        setManifestContent(content ?? {});
        setContentLoading(false);
      })
      .catch((e: unknown) => {
        setContentError(e instanceof Error ? e.message : 'Failed to load manifest content');
        setContentLoading(false);
      });
  };

  return (
    <>
      <Table
        loading={loading}
        header={
          <Header
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadManifests}>Refresh</Button>
                <Button variant="primary" onClick={onAddIntegration}>
                  Add OEM Integration
                </Button>
              </SpaceBetween>
            }
            description="Normalise cloud-to-cloud OEM telemetry into the CMS Signal Catalog. Click a manifest to see its endpoints, mappings, and credentials reference."
          >
            Transform Manifests
          </Header>
        }
        columnDefinitions={[
          {
            id: 'name',
            header: 'Manifest name',
            cell: (item: Manifest) => (
              <Button variant="inline-link" onClick={() => viewDetails(item)}>
                {item.name}
              </Button>
            ),
          },
          {
            id: 'source_type',
            header: 'Source type',
            cell: (item: Manifest) => (
              <StatusIndicator type="success">{item.source_type ?? 'unknown'}</StatusIndicator>
            ),
          },
          {
            id: 'last_modified',
            header: 'Last modified',
            cell: (item: Manifest) => new Date(item.last_modified).toLocaleString(),
          },
          {
            id: 'actions',
            header: 'Actions',
            cell: (item: Manifest) => (
              <Button onClick={() => viewDetails(item)}>View details</Button>
            ),
          },
        ]}
        items={manifests}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No transform manifests</b>
            <Box padding={{ bottom: 's' }} variant="p" color="inherit">
              Add an OEM integration to create your first transform manifest.
            </Box>
          </Box>
        }
      />
      {error && (
        <Box padding={{ top: 's' }}>
          <Alert type="error" header="Failed to load manifests">
            {error}
          </Alert>
        </Box>
      )}

      <Modal
        visible={showDetails}
        onDismiss={() => setShowDetails(false)}
        header={
          selectedManifest ? (
            <SpaceBetween direction="horizontal" size="xs" alignItems="center">
              <span>{selectedManifest.name}</span>
              {manifestContent?.transform_type && (
                <Badge color="blue">{manifestContent.transform_type}</Badge>
              )}
            </SpaceBetween>
          ) : (
            'Manifest details'
          )
        }
        size="max"
      >
        {contentLoading ? (
          <Box padding="l" textAlign="center">
            <StatusIndicator type="loading">Loading manifest…</StatusIndicator>
          </Box>
        ) : contentError ? (
          <Alert type="error" header="Failed to load manifest">
            {contentError}
          </Alert>
        ) : manifestContent ? (
          <ManifestDetails content={manifestContent} />
        ) : (
          <Box padding="l" color="text-status-inactive">
            No content.
          </Box>
        )}
      </Modal>
    </>
  );
};

export default TransformManifestsViewer;
