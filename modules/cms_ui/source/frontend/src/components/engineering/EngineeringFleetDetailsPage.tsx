// Engineering fleet detail page — sibling to FleetDetailsPage. Renders when
// the user is a product engineer AND the fleet has tenantType set
// (internal | external). Driven by useIsEngineerTenant().
//
// Layout:
//   - Header: fleet name + tenant badge + description
//   - KPI strip: SoH degradation, anomaly count, affected cohort, batch/supplier
//   - Tabs:
//       Overview      — KPI summary + active OTA banner
//       Vehicles      — vehicles table with isAffectedCohort badge
//       OTA Rollouts  — Build #4823 stage progression + history (FULLY BUILT)
//       Anomalies     — anomaly list filtered to this fleet
//       Knowledge     — KB docs linked to this fleet's domain
//
// Data sources:
//   - Fleet record:   passed in as a prop (parent fetches from API)
//   - Anomalies:      mock-data-provider/engineering/anomalies.ts
//   - OTA pipelines:  mock-data-provider/engineering/ota-pipelines.ts
//   - KB docs:        mock-data-provider/engineering/kb-corpus.ts
//
// Wiring (in App.tsx, replace the existing /fleets/management/:fleetId route):
//
//   <Route
//     path="/fleets/management/:fleetId"
//     element={<FleetDetailsPageRouter />}
//   />
//
// where FleetDetailsPageRouter fetches the fleet, then:
//
//   const isEng = useIsEngineerTenant(fleet);
//   return isEng
//     ? <EngineeringFleetDetailsPage fleet={fleet} />
//     : <FleetDetailsPage fleetId={fleetId} />;

import React, { useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Box,
  ColumnLayout,
  Container,
  Header,
  KeyValuePairs,
  Link,
  Pagination,
  ProgressBar,
  Select,
  SpaceBetween,
  Spinner,
  StatusIndicator,
  Table,
  Tabs,
  TextFilter,
} from '@cloudscape-design/components';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { FleetItem, VehicleItem } from '@/types/fleet-types';
import { getRuntimeConfig } from '@/config/api';
import { useAuth } from '@/auth/useAuth';
import {
  ENGINEERING_ANOMALIES,
  KB_DOCUMENTS,
  MANUFACTURING_BATCHES,
  BATTERY_SUPPLIERS,
  OPERATING_REGIONS,
  AFFECTED_COHORT_FILTER,
  getPipelinesForFleet,
  getActivePipelinesForFleet,
  pipelineProgress,
  getECUStateForVehicle,
  isBuild4823CanaryRecipient,
  type OTAPipeline,
  type OTAArtifact,
  type PipelineStage,
  type PostFixValidationMetric,
} from '@/mock-data-provider/engineering';

// ============================================================================
// HEADER + KPI STRIP
// ============================================================================

interface EngineeringFleetDetailsPageProps {
  /** Fleet record fetched from the API. */
  fleet: FleetItem;
}

export default function EngineeringFleetDetailsPage({ fleet }: EngineeringFleetDetailsPageProps) {
  const fleetId = fleet.fleetId || fleet.id || '';
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = searchParams.get('tab') || 'overview';
  const [activeTabId, setActiveTabId] = useState<string>(initialTab);

  // Sync activeTabId when URL changes externally (e.g., navigated from Digital Thread CTA)
  useEffect(() => {
    const fromUrl = searchParams.get('tab');
    if (fromUrl && fromUrl !== activeTabId) {
      setActiveTabId(fromUrl);
    }
  }, [searchParams, activeTabId]);

  // Anomalies filtered to this fleet
  const fleetAnomalies = useMemo(
    () => ENGINEERING_ANOMALIES.filter((a) => a.affectedFleets.includes(fleetId)),
    [fleetId]
  );

  // OTA pipelines targeting this fleet
  const allPipelines = useMemo(() => getPipelinesForFleet(fleetId), [fleetId]);
  const activePipelines = useMemo(() => getActivePipelinesForFleet(fleetId), [fleetId]);

  const heroAnomaly = fleetAnomalies[0];
  const isExternal = fleet.tenantType === 'external';

  return (
    <Container>
      <SpaceBetween size="l">
        <Header
          variant="h1"
          description={fleet.description}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              {activePipelines.length > 0 && (
                <Badge color="blue">
                  {activePipelines.length} active OTA rollout
                  {activePipelines.length === 1 ? '' : 's'}
                </Badge>
              )}
            </SpaceBetween>
          }
        >
          {fleet.name}{' '}
          <Badge color={isExternal ? 'red' : 'green'}>
            {isExternal ? 'External · Production' : 'Internal · Validation'}
          </Badge>
        </Header>

        {/* KPI CARD ROW — five engineering KPIs, individual rounded cards */}
        <FleetKPICards
          fleet={fleet}
          fleetAnomalyCount={fleetAnomalies.length}
          activePipelineCount={activePipelines.length}
        />

        <Tabs
          activeTabId={activeTabId}
          onChange={({ detail }) => {
            setActiveTabId(detail.activeTabId);
            // Keep URL in sync for shareable deep-links and CTA-driven navigation.
            const next = new URLSearchParams(searchParams);
            next.set('tab', detail.activeTabId);
            setSearchParams(next, { replace: true });
          }}
          tabs={[
            {
              id: 'overview',
              label: 'Overview',
              content: (
                <OverviewTab
                  fleet={fleet}
                  heroAnomaly={heroAnomaly}
                  activePipelines={activePipelines}
                />
              ),
            },
            {
              id: 'vehicles',
              label: `Vehicles (${fleet.vehicleCount ?? 0})`,
              content: <VehiclesTab fleetId={fleetId} />,
            },
            {
              id: 'ota-rollouts',
              label: 'OTA Rollouts',
              content: <OTARolloutsTab pipelines={allPipelines} activeCount={activePipelines.length} />,
            },
            {
              id: 'anomalies',
              label: `Anomalies (${fleetAnomalies.length})`,
              content: <AnomaliesTab anomalies={fleetAnomalies} />,
            },
            {
              id: 'knowledge',
              label: 'Knowledge',
              content: <KnowledgeTab fleet={fleet} />,
            },
          ]}
        />
      </SpaceBetween>
    </Container>
  );
}

// ============================================================================
// KPI CARDS — individual rounded cards in a CSS grid (matches operational pattern)
// ============================================================================

const KPI_LABEL_STYLE: React.CSSProperties = {
  fontSize: '11px',
  fontWeight: 700,
  textTransform: 'uppercase',
  color: '#656871',
  letterSpacing: '0.5px',
};
const KPI_VALUE_STYLE: React.CSSProperties = {
  fontSize: '24px',
  fontWeight: 700,
  display: 'block',
  lineHeight: 1.2,
};
const KPI_VALUE_WARN_STYLE: React.CSSProperties = { ...KPI_VALUE_STYLE, color: '#b45309' };
const KPI_VALUE_INFO_STYLE: React.CSSProperties = { ...KPI_VALUE_STYLE, color: '#0972d3' };
const KPI_VALUE_OK_STYLE:   React.CSSProperties = { ...KPI_VALUE_STYLE, color: '#1d7e26' };
const KPI_SUB_STYLE: React.CSSProperties = { fontSize: '11px', color: '#656871' };

function KPICard({ label, value, sub, valueStyle }: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  valueStyle?: React.CSSProperties;
}) {
  return (
    <Container>
      <SpaceBetween size="xxs">
        <span style={KPI_LABEL_STYLE}>{label}</span>
        <span style={valueStyle ?? KPI_VALUE_STYLE}>{value}</span>
        {sub && <span style={KPI_SUB_STYLE}>{sub}</span>}
      </SpaceBetween>
    </Container>
  );
}

function FleetKPICards({
  fleet,
  fleetAnomalyCount,
  activePipelineCount,
}: {
  fleet: FleetItem;
  fleetAnomalyCount: number;
  activePipelineCount: number;
}) {
  const isProdCohort = fleet.tenantType === 'external';
  const affectedCount = isProdCohort ? 40 : 0;
  const totalVehicles = fleet.vehicleCount ?? 0;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '12px' }}>
      <KPICard
        label="SoH degradation rate"
        value={isProdCohort ? '1.01 %/mo' : '0.92 %/mo'}
        sub={isProdCohort ? '+12.2% above 0.9 baseline' : 'Within validation envelope'}
        valueStyle={isProdCohort ? KPI_VALUE_WARN_STYLE : KPI_VALUE_OK_STYLE}
      />
      <KPICard
        label="Affected cohort"
        value={String(affectedCount)}
        sub={`of ${totalVehicles} vehicles`}
        valueStyle={affectedCount > 0 ? KPI_VALUE_WARN_STYLE : KPI_VALUE_STYLE}
      />
      <KPICard
        label="Open anomalies"
        value={String(fleetAnomalyCount)}
        sub={fleetAnomalyCount > 0 ? '1 high-severity' : 'None'}
        valueStyle={fleetAnomalyCount > 0 ? KPI_VALUE_WARN_STYLE : KPI_VALUE_STYLE}
      />
      <KPICard
        label="Active OTA rollouts"
        value={String(activePipelineCount)}
        sub={activePipelineCount > 0 ? 'Build #4823 (BMS v3.3.0)' : 'None in flight'}
        valueStyle={activePipelineCount > 0 ? KPI_VALUE_INFO_STYLE : KPI_VALUE_STYLE}
      />
      <KPICard
        label="Connected"
        value={`${fleet.connectedVehicles ?? 0} / ${totalVehicles}`}
        sub={`${Math.round(((fleet.connectedVehicles ?? 0) / Math.max(totalVehicles, 1)) * 100)}% online`}
        valueStyle={KPI_VALUE_OK_STYLE}
      />
    </div>
  );
}

// ============================================================================
// OVERVIEW TAB — engineer-distinctive content: cohort segmentation,
// manufacturing breakdown, active OTA banner, supplier/region distribution.
// None of this appears on the operational fleet detail page.
// ============================================================================

function OverviewTab({
  fleet,
  heroAnomaly,
  activePipelines,
}: {
  fleet: FleetItem;
  heroAnomaly?: { anomalyId: string; title: string; severity: string; affectedVehicleCount: number };
  activePipelines: OTAPipeline[];
}) {
  const navigate = useNavigate();
  const modelLine = (fleet.attributes?.modelLine as string | undefined) ?? (fleet.tenantType === 'external' ? 'BE 6' : 'BE.07');
  const fleetBatches = useMemo(
    () => MANUFACTURING_BATCHES.filter((b) => b.modelLine === modelLine),
    [modelLine]
  );
  const totalBatchVehicles = fleetBatches.reduce((s, b) => s + b.vehicleCount, 0);
  const affectedBatchCount = fleetBatches.filter((b) =>
    (AFFECTED_COHORT_FILTER.affectedBatchIds as readonly string[]).includes(b.batchId)
  ).length;
  const activePipeline = activePipelines[0];
  const isProdCohort = fleet.tenantType === 'external';
  const affectedCount = isProdCohort ? 40 : 0;
  const totalVehicles = fleet.vehicleCount ?? 0;
  const cohortPct = totalVehicles ? Math.round((affectedCount / totalVehicles) * 100) : 0;

  return (
    <SpaceBetween size="l">
      {/* DATA FLOW HEADER — establishes the FleetWise → Bedrock → Engineering
          ingestion pipeline that feeds this view. Engineer-only context. */}
      <DataFlowCard fleet={fleet} />

      {/* ACTIVE OTA BANNER — most prominent engineering surface */}
      {activePipeline && (
        <Container
          header={
            <Header
              variant="h2"
              actions={
                <Link
                  onFollow={(e) => { e.preventDefault(); navigate(`/fleets/management/${fleet.fleetId}?tab=ota-rollouts`); }}
                  href="#"
                >
                  Open OTA Rollouts →
                </Link>
              }
            >
              Active OTA rollout — {activePipeline.label}
            </Header>
          }
        >
          <SpaceBetween size="s">
            <Box variant="small">{activePipeline.rationale}</Box>
            <Box>
              <strong>{activePipeline.targetECU} firmware:</strong>{' '}
              <span style={{ fontFamily: 'monospace' }}>v{activePipeline.fromVersion}</span>
              {' → '}
              <span style={{ fontFamily: 'monospace' }}>v{activePipeline.toVersion}</span>
              {' · workbench '}
              <span style={{ fontFamily: 'monospace' }}>{activePipeline.workbench.name}</span>
            </Box>
            <ProgressBar
              value={pipelineProgress(activePipeline)}
              description={
                activePipeline.stages
                  .map((s) =>
                    s.status === 'completed' ? `✓ ${s.name}` :
                    s.status === 'in-progress' ? `▶ ${s.name}` :
                    `○ ${s.name}`
                  )
                  .join('  ·  ')
              }
            />
          </SpaceBetween>
        </Container>
      )}

      {/* COHORT SEGMENTATION — engineer-only visualization */}
      <Container
        header={<Header variant="h2" description="Vehicles split by anomaly cohort membership.">Cohort segmentation</Header>}
      >
        <SpaceBetween size="m">
          <ColumnLayout columns={3} variant="text-grid">
            <div>
              <Box variant="awsui-key-label">Affected cohort</Box>
              <span style={KPI_VALUE_WARN_STYLE}>{affectedCount}</span>
              <Box variant="small">{cohortPct}% of fleet · {affectedBatchCount} contributing batch{affectedBatchCount === 1 ? '' : 'es'}</Box>
            </div>
            <div>
              <Box variant="awsui-key-label">Unaffected (control)</Box>
              <span style={KPI_VALUE_OK_STYLE}>{totalVehicles - affectedCount}</span>
              <Box variant="small">{100 - cohortPct}% of fleet</Box>
            </div>
            <div>
              <Box variant="awsui-key-label">Cohort filter</Box>
              <Box variant="small">
                Region in {`{${(AFFECTED_COHORT_FILTER.affectedRegionIds as readonly string[]).join(', ')}}`}
              </Box>
              <Box variant="small">
                AND batch in {`{${(AFFECTED_COHORT_FILTER.affectedBatchIds as readonly string[]).join(', ')}}`}
              </Box>
            </div>
          </ColumnLayout>
          {/* Visual ratio bar */}
          <div style={{ display: 'flex', height: 24, borderRadius: 4, overflow: 'hidden', border: '1px solid #d5dbdb' }}>
            <div
              style={{
                width: `${cohortPct}%`,
                background: '#b45309',
                color: 'white',
                fontSize: 11,
                lineHeight: '24px',
                textAlign: 'center',
                fontWeight: 600,
              }}
            >
              {affectedCount} affected
            </div>
            <div
              style={{
                width: `${100 - cohortPct}%`,
                background: '#1d7e26',
                color: 'white',
                fontSize: 11,
                lineHeight: '24px',
                textAlign: 'center',
                fontWeight: 600,
              }}
            >
              {totalVehicles - affectedCount} control
            </div>
          </div>
        </SpaceBetween>
      </Container>

      {/* MANUFACTURING BATCHES — read from PLM-style data */}
      <Container
        header={
          <Header
            variant="h2"
            description="Sourced from Acme Motors PLM (Teamcenter). Pass rate < 92% flagged."
            counter={`(${fleetBatches.length})`}
          >
            Manufacturing batches
          </Header>
        }
      >
        <Table
          variant="borderless"
          columnDefinitions={[
            {
              id: 'batchId',
              header: 'Batch ID',
              cell: (b: any) => <span style={{ fontFamily: 'monospace' }}>{b.batchId}</span>,
              minWidth: 220,
            },
            { id: 'plant',    header: 'Plant',     cell: (b: any) => b.assemblyPlantId },
            {
              id: 'count',
              header: 'Vehicles',
              cell: (b: any) => `${b.vehicleCount} (${Math.round((b.vehicleCount / totalBatchVehicles) * 100)}%)`,
            },
            {
              id: 'supplier',
              header: 'Cell supplier',
              cell: (b: any) => {
                const sup = BATTERY_SUPPLIERS.find((s) => s.supplierId === b.batterySupplierId);
                return (
                  <span>
                    {b.batterySupplierId}
                    {sup && (<Box variant="small" display="inline"> ({sup.cellChemistry}, {sup.thermalLimit_C}°C)</Box>)}
                  </span>
                );
              },
            },
            { id: 'cellLot', header: 'Cell lot', cell: (b: any) => <span style={{ fontFamily: 'monospace' }}>{b.batteryCellLot}</span> },
            {
              id: 'pass',
              header: 'Thermal chamber pass rate',
              cell: (b: any) => (
                <StatusIndicator type={b.thermalChamberTestPassRate < 0.92 ? 'warning' : 'success'}>
                  {(b.thermalChamberTestPassRate * 100).toFixed(0)}%
                </StatusIndicator>
              ),
            },
            {
              id: 'affected',
              header: 'Cohort flag',
              cell: (b: any) =>
                (AFFECTED_COHORT_FILTER.affectedBatchIds as readonly string[]).includes(b.batchId)
                  ? <Badge color="red">Affected</Badge>
                  : <Box variant="small" color="text-body-secondary">—</Box>,
            },
          ]}
          items={fleetBatches}
          empty={<Box>No batch records.</Box>}
        />
      </Container>

      {/* SUPPLIER + REGION DISTRIBUTION */}
      <ColumnLayout columns={2}>
        <Container header={<Header variant="h3">Cell suppliers in this fleet</Header>}>
          <SpaceBetween size="xs">
            {Array.from(
              fleetBatches.reduce<Map<string, number>>((m, b) => {
                m.set(b.batterySupplierId, (m.get(b.batterySupplierId) ?? 0) + b.vehicleCount);
                return m;
              }, new Map())
            ).map(([supplierId, count]) => {
              const sup = BATTERY_SUPPLIERS.find((s) => s.supplierId === supplierId);
              const pct = Math.round((count / totalBatchVehicles) * 100);
              const flagged = supplierId === AFFECTED_COHORT_FILTER.supplierId;
              return (
                <Box key={supplierId}>
                  <SpaceBetween size="xxs">
                    <Box>
                      <strong>{sup?.supplierName ?? supplierId}</strong>{' '}
                      <Box variant="small" display="inline">
                        ({count} vehicles, {pct}%, {sup?.cellChemistry}, thermal limit {sup?.thermalLimit_C}°C)
                      </Box>
                      {flagged && <> <Badge color="red">Cohort supplier</Badge></>}
                    </Box>
                    {sup?.notes && <Box variant="small" color="text-body-secondary">{sup.notes}</Box>}
                  </SpaceBetween>
                </Box>
              );
            })}
          </SpaceBetween>
        </Container>
        <Container header={<Header variant="h3">Operating regions</Header>}>
          <SpaceBetween size="xs">
            {OPERATING_REGIONS.map((r) => {
              const isAffectedRegion = (AFFECTED_COHORT_FILTER.affectedRegionIds as readonly string[]).includes(r.regionId);
              return (
                <Box key={r.regionId}>
                  <strong>{r.regionName}</strong>{' '}
                  <Box variant="small" display="inline">
                    ({r.climateClass}, ~{r.avgAmbientTemp_C}°C avg, peak {r.avgSummerPeak_C}°C)
                  </Box>
                  {isAffectedRegion && <> <Badge color="red">Cohort region</Badge></>}
                </Box>
              );
            })}
          </SpaceBetween>
        </Container>
      </ColumnLayout>

      {/* HERO ANOMALY CALLOUT */}
      {heroAnomaly && (
        <Container
          header={
            <Header
              variant="h2"
              actions={
                <Link
                  onFollow={(e) => { e.preventDefault(); navigate(`/engineering/investigate/${heroAnomaly.anomalyId}`); }}
                  href="#"
                >
                  Open in Investigation Workspace →
                </Link>
              }
            >
              Open anomaly
            </Header>
          }
        >
          <SpaceBetween size="s">
            <Box>
              <StatusIndicator type={heroAnomaly.severity === 'high' || heroAnomaly.severity === 'critical' ? 'warning' : 'info'}>
                {heroAnomaly.title}
              </StatusIndicator>
            </Box>
            <Box variant="small">{heroAnomaly.affectedVehicleCount} vehicles in cohort.</Box>
          </SpaceBetween>
        </Container>
      )}

      {/* FLEET METADATA */}
      <Container header={<Header variant="h3">Fleet metadata</Header>}>
        <KeyValuePairs
          columns={3}
          items={[
            { label: 'Tenant type',      value: <Badge color={fleet.tenantType === 'external' ? 'red' : 'green'}>{fleet.tenantType ?? '—'}</Badge> },
            { label: 'Fleet type',       value: fleet.fleetType ?? '—' },
            { label: 'Operational base', value: fleet.operationalCity ?? '—' },
            { label: 'Vehicle count',    value: String(fleet.vehicleCount ?? 0) },
            { label: 'Connected',        value: String(fleet.connectedVehicles ?? 0) },
            { label: 'Active campaigns', value: String(fleet.numActiveCampaigns ?? 0) },
          ]}
        />
      </Container>
    </SpaceBetween>
  );
}

function OTARolloutsTab({ pipelines, activeCount }: { pipelines: OTAPipeline[]; activeCount: number }) {
  return (
    <SpaceBetween size="l">
      <Container header={<Header variant="h2" counter={`(${activeCount} active, ${pipelines.length - activeCount} historical)`}>Software rollouts</Header>}>
        <SpaceBetween size="m">
          {pipelines.map((p) => (
            <SpaceBetween size="s" key={p.pipelineId}>
              <PipelineDetailCard pipeline={p} />
              {p.postFixValidation && <PostFixValidationCard pipeline={p} />}
            </SpaceBetween>
          ))}
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
}

// ============================================================================
// POST-FIX VALIDATION CARD — closes the loop. Shows that the design change
// actually moved the metric it set out to fix, and that the new signals the
// firmware introduced are arriving from canary recipients.
// ============================================================================

function PostFixValidationCard({ pipeline }: { pipeline: OTAPipeline }) {
  const validation = pipeline.postFixValidation!;
  const navigate = useNavigate();
  const newSignalCount = validation.metrics.filter((m) => m.isNewSignal).length;
  const improvedCount = validation.metrics.filter((m) => m.direction === 'improved').length;

  return (
    <Container
      header={
        <Header
          variant="h3"
          description={validation.windowDescription}
          counter={`(${validation.metrics.length})`}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Badge color="green">{improvedCount} metrics improved</Badge>
              {newSignalCount > 0 && (
                <Badge color="blue">{newSignalCount} NEW signals arriving</Badge>
              )}
            </SpaceBetween>
          }
        >
          Canary post-fix validation — {pipeline.label}
        </Header>
      }
    >
      <SpaceBetween size="m">
        <Box variant="small">
          The design change is observable in the field.{' '}
          <strong>{validation.reportingCount}</strong> of {validation.totalCanaryCount} canary recipients
          are reporting telemetry. Two new signals introduced by{' '}
          <span style={{ fontFamily: 'monospace' }}>BMS v{pipeline.toVersion}</span> are now arriving;
          metrics targeted by the fix have moved within the new firmware's envelope.
        </Box>

        {/* METRICS GRID — 2 columns of pre→post comparisons */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: '12px',
          }}
        >
          {validation.metrics.map((metric) => (
            <ValidationMetricCard key={metric.label} metric={metric} />
          ))}
        </div>

        {/* CTA — jump to signal catalog filtered to the new signals */}
        <SpaceBetween direction="horizontal" size="xs">
          <Link
            onFollow={(e) => {
              e.preventDefault();
              navigate('/data-processing?tab=signal-catalog&ecu=BMS');
            }}
            href="#"
          >
            View {pipeline.targetECU} signals in catalog →
          </Link>
        </SpaceBetween>
      </SpaceBetween>
    </Container>
  );
}

function ValidationMetricCard({ metric }: { metric: PostFixValidationMetric }) {
  const isNewSignal = !!metric.isNewSignal;
  const direction = metric.direction ?? 'neutral';
  const accentColor =
    direction === 'improved' ? '#1d7e26' :
    direction === 'regressed' ? '#b45309' :
    '#5f6b7a';

  return (
    <Container>
      <SpaceBetween size="xxs">
        <SpaceBetween direction="horizontal" size="xxs">
          <span style={{ ...KPI_LABEL_STYLE, fontFamily: isNewSignal ? 'monospace' : undefined, textTransform: isNewSignal ? 'none' : 'uppercase' }}>
            {metric.label}
          </span>
          {isNewSignal && <Badge color="blue">NEW</Badge>}
        </SpaceBetween>

        {isNewSignal ? (
          // New signals don't have a pre-fix value — show post-fix prominently
          <>
            <span style={{ fontSize: '20px', fontWeight: 700, color: accentColor, lineHeight: 1.2 }}>
              {metric.postFix}
            </span>
            {metric.coverage && (
              <span style={KPI_SUB_STYLE}>{metric.coverage}</span>
            )}
          </>
        ) : (
          // Existing metrics: pre → post with delta
          <>
            <SpaceBetween direction="horizontal" size="xs" alignItems="center">
              <span style={{ fontSize: '14px', color: '#5f6b7a', textDecoration: 'line-through' }}>
                {metric.preFix}
              </span>
              <span style={{ color: accentColor, fontWeight: 600 }}>→</span>
              <span style={{ fontSize: '20px', fontWeight: 700, color: accentColor, lineHeight: 1.2 }}>
                {metric.postFix}
              </span>
            </SpaceBetween>
            {metric.delta && (
              <span style={{ ...KPI_SUB_STYLE, color: accentColor, fontWeight: 600 }}>
                {metric.delta}
              </span>
            )}
          </>
        )}
      </SpaceBetween>
    </Container>
  );
}

function AnomaliesTab({ anomalies }: { anomalies: typeof ENGINEERING_ANOMALIES }) {
  return (
    <Table
      columnDefinitions={[
        {
          id: 'title',
          header: 'Title',
          cell: (a) => <Link href={`/engineering/investigate/${a.anomalyId}`}>{a.title}</Link>,
        },
        {
          id: 'severity',
          header: 'Severity',
          cell: (a) => (
            <StatusIndicator type={a.severity === 'high' || a.severity === 'critical' ? 'warning' : 'info'}>
              {a.severity}
            </StatusIndicator>
          ),
        },
        { id: 'count',     header: 'Affected vehicles', cell: (a) => a.affectedVehicleCount },
        { id: 'detected',  header: 'Detected',          cell: (a) => new Date(a.detectedAt).toLocaleString() },
        { id: 'status',    header: 'Status',            cell: (a) => a.status },
      ]}
      items={anomalies}
      empty={<Box>No anomalies for this fleet.</Box>}
    />
  );
}

function KnowledgeTab({ fleet }: { fleet: FleetItem }) {
  // KB_DOCUMENTS doesn't filter by fleet currently — show all engineering KB
  // until a domain mapping is added. Reasonable for the demo.
  return (
    <Table
      columnDefinitions={[
        { id: 'title', header: 'Title',  cell: (d: any) => <Link href="#">{d.title}</Link> },
        { id: 'type',  header: 'Type',   cell: (d: any) => d.docType ?? d.type ?? '—' },
        { id: 'date',  header: 'Updated', cell: (d: any) => d.updatedAt ?? d.date ?? '—' },
      ]}
      items={KB_DOCUMENTS as any[]}
      empty={<Box>No KB documents.</Box>}
    />
  );
}

// ============================================================================
// PIPELINE VISUALIZATIONS
// ============================================================================

function PipelineCompactCard({ pipeline }: { pipeline: OTAPipeline }) {
  return (
    <Container>
      <SpaceBetween size="xs">
        <Box>
          <Box display="inline" fontWeight="bold">
            {pipeline.label}
          </Box>{' '}
          <Box display="inline" variant="small">
            — {pipeline.targetECU} v{pipeline.fromVersion} → v{pipeline.toVersion}
          </Box>
        </Box>
        <ProgressBar value={pipelineProgress(pipeline)} description={pipeline.rationale} />
      </SpaceBetween>
    </Container>
  );
}

function PipelineDetailCard({ pipeline }: { pipeline: OTAPipeline }) {
  return (
    <Container
      header={
        <Header
          variant="h3"
          description={`${pipeline.targetECU} v${pipeline.fromVersion} → v${pipeline.toVersion}  ·  triggered by ${pipeline.triggeredBy}`}
          actions={
            <Badge color={pipeline.status === 'in-progress' ? 'blue' : pipeline.status === 'completed' ? 'green' : 'red'}>
              {pipeline.status}
            </Badge>
          }
        >
          {pipeline.label}
        </Header>
      }
    >
      <SpaceBetween size="m">
        <Box variant="small">{pipeline.rationale}</Box>
        <WorkbenchCard pipeline={pipeline} />
        <ArtifactsPanel pipeline={pipeline} />
        <PipelineStageRow stages={pipeline.stages} />
      </SpaceBetween>
    </Container>
  );
}

// ============================================================================
// ARTIFACTS PANEL — surfaces ALL artifacts in a build, not just firmware.
// Decoder manifests and FleetWise campaigns ride alongside the firmware so
// that the new signals introduced by the firmware are decoded and collected
// automatically.
// ============================================================================

const ARTIFACT_TYPE_LABEL: Record<NonNullable<OTAArtifact['type']>, string> = {
  'firmware':            'Firmware',
  'decoder-manifest':    'Decoder manifest',
  'fleetwise-campaign':  'Campaign config',
};

const ARTIFACT_TYPE_ICON: Record<NonNullable<OTAArtifact['type']>, string> = {
  'firmware':            '🔥',
  'decoder-manifest':    '📋',
  'fleetwise-campaign':  '📡',
};

function ArtifactsPanel({ pipeline }: { pipeline: OTAPipeline }) {
  if (!pipeline.artifacts.length) return null;
  const fmtSize = (b: number) =>
    b >= 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB`
    : b >= 1024 ? `${(b / 1024).toFixed(0)} KB`
    : `${b} B`;

  return (
    <div style={{ background: '#fbfcfd', border: '1px solid #e9ebed', borderRadius: 4, padding: '8px 12px' }}>
      <SpaceBetween size="xxs">
        <span style={KPI_LABEL_STYLE}>
          Artifacts deployed in this build ({pipeline.artifacts.length})
        </span>
        <SpaceBetween size="xs">
          {pipeline.artifacts.map((a) => {
            const label = a.type ? ARTIFACT_TYPE_LABEL[a.type] : 'Artifact';
            const icon = a.type ? ARTIFACT_TYPE_ICON[a.type] : '📦';
            return (
              <Box key={a.artifactId}>
                <SpaceBetween size="xxs">
                  <SpaceBetween direction="horizontal" size="xs">
                    <span style={{ fontSize: 14 }}>{icon}</span>
                    <Box display="inline" fontWeight="bold" fontSize="body-s">{label}</Box>
                    <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#5f6b7a' }}>
                      {a.filename}
                    </span>
                    <Box display="inline" variant="small" color="text-body-secondary">
                      {fmtSize(a.sizeBytes)} · signed by {a.signedBy}
                    </Box>
                  </SpaceBetween>
                  {a.description && (
                    <Box variant="small" color="text-body-secondary" margin={{ left: 'l' }}>
                      {a.description}
                    </Box>
                  )}
                </SpaceBetween>
              </Box>
            );
          })}
        </SpaceBetween>
      </SpaceBetween>
    </div>
  );
}

// ============================================================================
// WORKBENCH CARD — surfaces the upstream Virtual Engineering Workbench (VEW)
// concept inline. No deploy; this is an inline representation of where the
// build came from. Visually distinct (left accent bar) so it reads as
// "another system / another tool" — the engineering build environment.
// ============================================================================

function WorkbenchCard({ pipeline }: { pipeline: OTAPipeline }) {
  const wb = pipeline.workbench;
  const fmtTimestamp = (iso?: string) => {
    if (!iso) return '';
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  };

  return (
    <div
      style={{
        background: '#f5f7fa',
        borderLeft: '3px solid #6b7280',
        borderRadius: 4,
        padding: '12px 16px',
      }}
    >
      <SpaceBetween size="xs">
        <SpaceBetween direction="horizontal" size="xs" alignItems="center">
          <span style={{ ...KPI_LABEL_STYLE, color: '#374151' }}>
            Source workbench (Virtual Engineering Workbench)
          </span>
          <Badge color="grey">{wb.type}</Badge>
        </SpaceBetween>

        <Box>
          <Box display="inline" fontWeight="bold" fontSize="body-m">{wb.name}</Box>
          {wb.amiImage && wb.amiVersion && (
            <Box display="inline" variant="small" color="text-body-secondary">
              {' '}— AMI image{' '}
              <span style={{ fontFamily: 'monospace' }}>{wb.amiImage}@{wb.amiVersion}</span>
            </Box>
          )}
        </Box>

        <ColumnLayout columns={2} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Lead engineer</Box>
            <Box>{wb.leadEngineer}</Box>
          </div>
          {wb.virtualTarget && (
            <div>
              <Box variant="awsui-key-label">Virtual target</Box>
              <Box variant="small">{wb.virtualTarget}</Box>
            </div>
          )}
          {wb.silCoverage && (
            <div>
              <Box variant="awsui-key-label">SiL coverage</Box>
              <Box variant="small">{wb.silCoverage}</Box>
            </div>
          )}
          {wb.summary && (
            <div>
              <Box variant="awsui-key-label">Workbench engagement</Box>
              <Box variant="small">
                {wb.summary.totalCommits} commits · {wb.summary.activeContributors} contributors
                · {wb.summary.simRunsLast7Days} SiL runs in 7d
              </Box>
            </div>
          )}
        </ColumnLayout>

        {wb.lastCommit && (
          <Box>
            <Box variant="awsui-key-label">Latest commit</Box>
            <Box>
              <span style={{ fontFamily: 'monospace', color: '#374151' }}>
                {wb.lastCommit.sha}
              </span>{' '}
              <Box display="inline" variant="small" color="text-body-secondary">
                — {wb.lastCommit.author} · {fmtTimestamp(wb.lastCommit.timestamp)}
              </Box>
            </Box>
            <Box variant="small" margin={{ top: 'xxs' }}>
              "{wb.lastCommit.message}"
            </Box>
          </Box>
        )}
      </SpaceBetween>
    </div>
  );
}

function PipelineStageRow({ stages }: { stages: PipelineStage[] }) {
  return (
    <ColumnLayout columns={stages.length} variant="text-grid" minColumnWidth={140}>
      {stages.map((s) => (
        <div key={s.id}>
          <Box variant="awsui-key-label">{s.name}</Box>
          <Box>
            <StageStatusIndicator status={s.status} />
          </Box>
          {s.metrics && (
            <SpaceBetween size="xxs">
              {s.metrics.slice(0, 3).map((m) => (
                <Box key={m.label} variant="small">
                  <strong>{m.label}:</strong> {m.value}
                </Box>
              ))}
            </SpaceBetween>
          )}
        </div>
      ))}
    </ColumnLayout>
  );
}

function StageStatusIndicator({ status }: { status: PipelineStage['status'] }) {
  switch (status) {
    case 'completed':   return <StatusIndicator type="success">Completed</StatusIndicator>;
    case 'in-progress': return <StatusIndicator type="in-progress">In progress</StatusIndicator>;
    case 'pending':     return <StatusIndicator type="pending">Pending</StatusIndicator>;
    case 'failed':      return <StatusIndicator type="error">Failed</StatusIndicator>;
    case 'skipped':     return <StatusIndicator type="stopped">Skipped</StatusIndicator>;
  }
}

// ============================================================================
// VEHICLES TAB — fetches vehicles for this fleet, joins ECU state, renders
// engineering-relevant columns including Affected cohort and BMS version.
// ============================================================================

interface FleetVehicleRow extends VehicleItem {
  // additional convenience fields used by the table
  bmsVersion?: string;
  isCanaryRecipient?: boolean;
}

function VehiclesTab({ fleetId }: { fleetId: string }) {
  const navigate = useNavigate();
  const { getAuthHeaders } = useAuth();
  const [vehicles, setVehicles] = useState<FleetVehicleRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filterText, setFilterText] = useState('');
  const [affectedFilter, setAffectedFilter] = useState<'all' | 'affected' | 'unaffected'>('all');
  const [pageIndex, setPageIndex] = useState(1);
  const PAGE_SIZE = 25;

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        setError(null);
        const apiEndpoint = getRuntimeConfig().apiEndpoint;
        const url = `${apiEndpoint}api/v1/vehicles?fleetId=${encodeURIComponent(fleetId)}&limit=500`;
        const res = await fetch(url, {
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        });
        if (!res.ok) throw new Error(`API ${res.status}`);
        const data = await res.json();
        const raw: VehicleItem[] = data.vehicles || [];
        if (cancelled) return;
        // Enrich each vehicle with BMS version + canary flag (joined from mock ECU state)
        const enriched: FleetVehicleRow[] = raw.map((v) => {
          const isCanary = isBuild4823CanaryRecipient(v.vehicleId ?? '');
          const ecus = getECUStateForVehicle({
            vehicleId:        v.vehicleId ?? '',
            ecuConfigId:      v.ecuConfigId ?? '',
            isAffectedCohort: !!v.isAffectedCohort,
            isCanaryRecipient: isCanary,
          });
          const bms = ecus.find((e) => e.ecu === 'BMS');
          return { ...v, bmsVersion: bms?.currentVersion, isCanaryRecipient: isCanary };
        });
        setVehicles(enriched);
      } catch (e: any) {
        if (!cancelled) setError(e.message ?? 'Error loading vehicles');
      }
    }
    load();
    return () => { cancelled = true; };
  }, [fleetId, getAuthHeaders]);

  const filtered = useMemo(() => {
    if (!vehicles) return [];
    let rows = vehicles;
    if (affectedFilter === 'affected')   rows = rows.filter((v) => v.isAffectedCohort);
    if (affectedFilter === 'unaffected') rows = rows.filter((v) => !v.isAffectedCohort);
    if (filterText.trim()) {
      const q = filterText.trim().toLowerCase();
      rows = rows.filter((v) =>
        (v.vin ?? '').toLowerCase().includes(q) ||
        (v.vehicleId ?? '').toLowerCase().includes(q) ||
        (v.name ?? '').toLowerCase().includes(q) ||
        (v.manufacturingBatchId ?? '').toLowerCase().includes(q) ||
        (v.regionId ?? '').toLowerCase().includes(q)
      );
    }
    return rows;
  }, [vehicles, filterText, affectedFilter]);

  const pagedRows = useMemo(() => {
    const start = (pageIndex - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [filtered, pageIndex]);

  if (vehicles === null && !error) {
    return <Box padding="l"><Spinner /> Loading vehicles…</Box>;
  }
  if (error) {
    return (
      <Box padding="l">
        <StatusIndicator type="error">Couldn't load vehicles for this fleet: {error}</StatusIndicator>
      </Box>
    );
  }

  const affectedCount = (vehicles ?? []).filter((v) => v.isAffectedCohort).length;
  const canaryCount = (vehicles ?? []).filter((v) => v.isCanaryRecipient).length;

  return (
    <SpaceBetween size="m">
      <Container>
        <ColumnLayout columns={4} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Total vehicles</Box>
            <Box fontSize="display-l" fontWeight="bold">{vehicles?.length ?? 0}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Affected cohort</Box>
            <Box fontSize="display-l" fontWeight="bold" color={affectedCount > 0 ? 'text-status-warning' : 'text-body-secondary'}>
              {affectedCount}
            </Box>
          </div>
          <div>
            <Box variant="awsui-key-label">OTA canary recipients</Box>
            <Box fontSize="display-l" fontWeight="bold" color={canaryCount > 0 ? 'text-status-info' : 'text-body-secondary'}>
              {canaryCount}
            </Box>
            <Box variant="small">running BMS v3.3.0</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Connected</Box>
            <Box fontSize="display-l" fontWeight="bold" color="text-status-success">
              {(vehicles ?? []).filter((v) => v.connectionStatus === 'connected').length}
            </Box>
          </div>
        </ColumnLayout>
      </Container>

      <Table
        header={
          <Header counter={`(${filtered.length} of ${vehicles?.length ?? 0})`}>Vehicles</Header>
        }
        filter={
          <SpaceBetween direction="horizontal" size="s">
            <TextFilter
              filteringText={filterText}
              filteringPlaceholder="Search VIN, name, batch, region"
              filteringAriaLabel="Filter vehicles"
              onChange={({ detail }) => { setFilterText(detail.filteringText); setPageIndex(1); }}
            />
            <Select
              selectedOption={{ label: affectedFilter === 'all' ? 'All vehicles' : affectedFilter === 'affected' ? 'Affected cohort only' : 'Unaffected only', value: affectedFilter }}
              options={[
                { label: 'All vehicles',          value: 'all' },
                { label: 'Affected cohort only',  value: 'affected' },
                { label: 'Unaffected only',       value: 'unaffected' },
              ]}
              onChange={({ detail }) => { setAffectedFilter(detail.selectedOption.value as any); setPageIndex(1); }}
            />
          </SpaceBetween>
        }
        pagination={
          <Pagination
            currentPageIndex={pageIndex}
            pagesCount={Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))}
            onChange={({ detail }) => setPageIndex(detail.currentPageIndex)}
          />
        }
        columnDefinitions={[
          {
            id: 'vin',
            header: 'VIN',
            cell: (v: FleetVehicleRow) => (
              <Link
                onFollow={(e) => { e.preventDefault(); navigate(`/vehicles/management/${v.vehicleId}`); }}
                href={`/vehicles/management/${v.vehicleId}`}
              >
                <span style={{ fontFamily: 'monospace' }}>{v.vin ?? v.vehicleId}</span>
              </Link>
            ),
            minWidth: 200,
          },
          { id: 'name',     header: 'Name',     cell: (v: FleetVehicleRow) => v.name ?? '—', minWidth: 160 },
          {
            id: 'connection', header: 'Status',
            cell: (v: FleetVehicleRow) => (
              <StatusIndicator type={v.connectionStatus === 'connected' ? 'success' : 'stopped'}>
                {v.connectionStatus ?? '—'}
              </StatusIndicator>
            ),
            minWidth: 110,
          },
          {
            id: 'batch', header: 'Manufacturing batch',
            cell: (v: FleetVehicleRow) => <Box fontFamily="monospace">{v.manufacturingBatchId ?? '—'}</Box>,
            minWidth: 200,
          },
          { id: 'supplier', header: 'Cell supplier', cell: (v: FleetVehicleRow) => v.supplierId ?? '—', minWidth: 140 },
          { id: 'region',   header: 'Region',        cell: (v: FleetVehicleRow) => v.regionId ?? '—',   minWidth: 160 },
          {
            id: 'bms', header: 'BMS version',
            cell: (v: FleetVehicleRow) => (
              <Box fontFamily="monospace">{v.bmsVersion ?? '—'}</Box>
            ),
            minWidth: 120,
          },
          {
            id: 'affected', header: 'Affected cohort',
            cell: (v: FleetVehicleRow) =>
              v.isAffectedCohort
                ? <Badge color="red">Affected</Badge>
                : <Box color="text-body-secondary" variant="small">—</Box>,
            minWidth: 140,
          },
          {
            id: 'canary', header: 'OTA',
            cell: (v: FleetVehicleRow) =>
              v.isCanaryRecipient
                ? <Badge color="blue">Canary v3.3.0</Badge>
                : <Box color="text-body-secondary" variant="small">—</Box>,
            minWidth: 140,
          },
        ]}
        items={pagedRows}
        empty={<Box padding="m">No vehicles match the current filter.</Box>}
        variant="borderless"
      />
    </SpaceBetween>
  );
}

// ============================================================================
// DATA FLOW CARD — surfaces the AWS IoT FleetWise → Bedrock → Engineering
// ingestion pipeline that feeds the engineering view. Engineer-only context;
// makes the platform dependency chain visible.
// ============================================================================

function DataFlowCard({ fleet }: { fleet: FleetItem }) {
  const isExternal = fleet.tenantType === 'external';
  const totalVehicles = fleet.vehicleCount ?? 0;
  // Demo numbers — would be real metrics in production via FleetWise/CW.
  const signalsPerDay = isExternal ? '1.4M' : '180K';
  const decoderManifest = isExternal ? 'cms-prod-decoder-manifest-v17' : 'cms-be07-decoder-manifest-v23';
  const baselineCampaign = isExternal ? 'cmpgn-be6-soh-baseline' : 'cmpgn-be07-validation-full';
  const lastIngest = '23s ago';

  return (
    <div
      style={{
        background: 'linear-gradient(180deg, #f4f6fa 0%, #f9fafc 100%)',
        border: '1px solid #d5dbdb',
        borderRadius: 8,
        padding: '14px 18px',
      }}
    >
      <SpaceBetween size="s">
        <SpaceBetween direction="horizontal" size="xs" alignItems="center">
          <span style={{ ...KPI_LABEL_STYLE, color: '#374151' }}>
            Production telemetry pipeline
          </span>
          <Badge color="green">healthy · last ingest {lastIngest}</Badge>
        </SpaceBetween>

        {/* Visual flow */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr auto 1fr auto 1fr auto 1fr',
            gap: '8px',
            alignItems: 'center',
          }}
        >
          <DataFlowNode
            title={`${totalVehicles} vehicles`}
            sub={isExternal ? 'In-market production cohort' : 'Pre-production validation fleet'}
          />
          <DataFlowArrow />
          <DataFlowNode
            title="CMS Telemetry"
            sub={
              <>
                <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{decoderManifest}</span>
                <br />
                <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{baselineCampaign}</span>
              </>
            }
          />
          <DataFlowArrow />
          <DataFlowNode
            title="Bedrock Agent"
            sub={<>Anomaly detection<br />+ cohort traversal</>}
          />
          <DataFlowArrow />
          <DataFlowNode
            title="Engineering view"
            sub={<>This page<br />+ Insights, Investigation</>}
            highlight
          />
        </div>

        <Box variant="small" color="text-body-secondary">
          <strong>{signalsPerDay} signals/day</strong> · 6-month retention ·{' '}
          telemetry anonymized at the data governance layer before reaching engineering surfaces ·{' '}
          decoder + campaign updates ride along with firmware OTA (see Build artifacts above).
        </Box>
      </SpaceBetween>
    </div>
  );
}

function DataFlowNode({
  title,
  sub,
  highlight,
}: {
  title: string;
  sub: React.ReactNode;
  highlight?: boolean;
}) {
  return (
    <div
      style={{
        background: highlight ? '#eaf3ff' : 'white',
        border: highlight ? '1px solid #0972d3' : '1px solid #d5dbdb',
        borderRadius: 6,
        padding: '8px 10px',
        textAlign: 'center',
        minHeight: 60,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
      }}
    >
      <span style={{ fontWeight: 700, fontSize: 13, color: highlight ? '#0972d3' : '#16191f' }}>
        {title}
      </span>
      <span style={{ fontSize: 11, color: '#5f6b7a', marginTop: 2 }}>{sub}</span>
    </div>
  );
}

function DataFlowArrow() {
  return (
    <span style={{ fontSize: 18, color: '#5f6b7a', textAlign: 'center', fontWeight: 700 }}>
      →
    </span>
  );
}
