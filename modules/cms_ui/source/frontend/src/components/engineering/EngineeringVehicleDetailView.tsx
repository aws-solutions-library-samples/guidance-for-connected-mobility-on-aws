// Engineering vehicle detail view — sibling to VehicleDetailView. Renders when
// the user is a product engineer AND the vehicle has tenantType set. Driven
// by useIsEngineerTenant().
//
// Layout:
//   - Header: VIN/name + isAffectedCohort badge + tenant badge
//   - KPI strip: VIN, batch, supplier, region, ECU config
//   - Tabs:
//       Overview     — vehicle metadata grid + linked anomalies
//       Telemetry    — SoH/thermal trace placeholder (chart wiring to telemetry.ts left as TODO)
//       Anomalies    — anomalies linked by cohort filter
//       ECUs         — 8 ECUs, current/baseline/pending versions, OTA status (FULLY BUILT)
//       Parts        — BOM tree, read-only, "Sourced from Acme Motors PLM (Teamcenter)" (FULLY BUILT)
//       Knowledge    — KB docs
//
// Wiring (in App.tsx, replace existing /vehicles/management/:vehicleId route):
//
//   <Route path="/vehicles/management/:vehicleId" element={<VehicleDetailRouter />} />
//
// where VehicleDetailRouter fetches the vehicle, then:
//
//   const isEng = useIsEngineerTenant(vehicle);
//   return isEng
//     ? <EngineeringVehicleDetailView vehicle={vehicle} />
//     : <VehicleDetailView />;

import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Badge,
  Box,
  Button,
  ColumnLayout,
  Container,
  ContentLayout,
  ExpandableSection,
  Header,
  KeyValuePairs,
  LineChart,
  Link,
  SegmentedControl,
  SpaceBetween,
  StatusIndicator,
  Table,
  Tabs,
} from '@cloudscape-design/components';
import { VehicleItem } from '@/types/fleet-types';
import {
  ENGINEERING_ANOMALIES,
  KB_DOCUMENTS,
  ECU_CATALOG,
  ECU_CONFIGS,
  getECUStateForVehicle,
  getBOMForConfig,
  countParts,
  isBuild4823CanaryRecipient,
  getSignalsByECU,
  getTelemetryFromMetadata,
  type VehicleECUState,
  type PartNode,
  type ECUId,
  type DailyTelemetryAggregate,
} from '@/mock-data-provider/engineering';

interface EngineeringVehicleDetailViewProps {
  vehicle: VehicleItem;
}

export default function EngineeringVehicleDetailView({ vehicle }: EngineeringVehicleDetailViewProps) {
  const vehicleId = vehicle.vehicleId || vehicle.id || '';
  const ecuConfigId = vehicle.ecuConfigId || '';
  const isAffected = !!vehicle.isAffectedCohort;
  const isCanary = isBuild4823CanaryRecipient(vehicleId);
  const isInternal = vehicle.tenantType === 'internal';

  // ECU state (joined from mock data + per-vehicle context)
  const ecuState = useMemo(
    () => getECUStateForVehicle({
      vehicleId,
      ecuConfigId,
      isAffectedCohort: isAffected,
      isCanaryRecipient: isCanary,
    }),
    [vehicleId, ecuConfigId, isAffected, isCanary]
  );

  const bom = useMemo(() => getBOMForConfig(ecuConfigId), [ecuConfigId]);
  const partCount = bom ? countParts(bom) : 0;

  // Anomalies — match by cohort (any anomaly whose cohort intersects this vehicle)
  const linkedAnomalies = useMemo(
    () => ENGINEERING_ANOMALIES.filter((a) =>
      // Approximation: vehicle is in affected fleet AND in cohort
      a.affectedFleets.includes(vehicle.fleetId ?? '') && (isAffected || a.affectedVehicleCount > 100)
    ),
    [vehicle.fleetId, isAffected]
  );

  return (
    <Container>
      <SpaceBetween size="l">
        <Header
          variant="h1"
          description={`VIN ${vehicle.vin ?? '—'} · Fleet ${vehicle.fleetId ?? '—'} · Assembled ${vehicle.assemblyDate ?? '—'}`}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              {isCanary && <Badge color="blue">OTA Build #4823 canary</Badge>}
              {isAffected && <Badge color="red">Affected cohort</Badge>}
              <Badge color={isInternal ? 'green' : 'red'}>
                {isInternal ? 'Internal · Validation' : 'External · Production'}
              </Badge>
            </SpaceBetween>
          }
        >
          {vehicle.name ?? vehicle.vehicleId}
        </Header>

        <Tabs
          tabs={[
            {
              id: 'overview',
              label: 'Overview',
              content: <OverviewTab vehicle={vehicle} ecuState={ecuState} linkedAnomalyCount={linkedAnomalies.length} />,
            },
            {
              id: 'telemetry',
              label: 'Telemetry',
              content: <TelemetryTab vehicle={vehicle} ecuState={ecuState} />,
            },
            {
              id: 'anomalies',
              label: `Anomalies (${linkedAnomalies.length})`,
              content: <AnomaliesTab anomalies={linkedAnomalies} />,
            },
            {
              id: 'ecus',
              label: `ECUs (${ecuState.length})`,
              content: <ECUsTab ecuState={ecuState} ecuConfigId={ecuConfigId} />,
            },
            {
              id: 'parts',
              label: `Parts (${partCount})`,
              content: <PartsTab bom={bom} vehicle={vehicle} />,
            },
            {
              id: 'knowledge',
              label: 'Knowledge',
              content: <KnowledgeTab />,
            },
          ]}
        />
      </SpaceBetween>
    </Container>
  );
}

// ============================================================================
// KPI CARD HELPERS — matches the operational vehicle-detail card aesthetic
// (individual Container per metric, span-styled label/value).
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
const KPI_VALUE_OK_STYLE:   React.CSSProperties = { ...KPI_VALUE_STYLE, color: '#1d7e26' };
const KPI_SUB_STYLE: React.CSSProperties = {
  fontSize: '11px',
  color: '#656871',
};

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

// ============================================================================
// OVERVIEW TAB — KPI cards row + metadata
// ============================================================================

function OverviewTab({
  vehicle,
  ecuState,
  linkedAnomalyCount,
}: {
  vehicle: VehicleItem;
  ecuState: VehicleECUState[];
  linkedAnomalyCount: number;
}) {
  const bms = ecuState.find((e) => e.ecu === 'BMS');

  // Cheap on-page SoH lookup so the Overview KPI matches Telemetry tab numbers
  // (full series is computed on Telemetry tab; here we just need the latest value).
  const sohSeries = useMemo<DailyTelemetryAggregate[]>(() => {
    if (!vehicle.regionId || !vehicle.manufacturingBatchId || !vehicle.vehicleId) return [];
    return getTelemetryFromMetadata({
      vehicleId: vehicle.vehicleId,
      isAffectedCohort: !!vehicle.isAffectedCohort,
      regionId: vehicle.regionId,
      manufacturingBatchId: vehicle.manufacturingBatchId,
    });
  }, [vehicle.vehicleId, vehicle.regionId, vehicle.manufacturingBatchId, vehicle.isAffectedCohort]);

  const currentSoH = sohSeries.length ? sohSeries[sohSeries.length - 1].batterySoH_pct : null;
  const isAffected = !!vehicle.isAffectedCohort;
  const isCanary = isBuild4823CanaryRecipient(vehicle.vehicleId ?? '');

  const bmsVersion = bms?.currentVersion ?? '—';
  const bmsPending = bms?.pendingVersion;
  const otaLabel =
    bms?.otaStatus === 'older' ? 'Older' :
    bms?.otaStatus === 'pending' ? 'Pending' :
    bms?.otaStatus === 'in-flight' ? 'In flight' :
    bms?.otaStatus === 'failed' ? 'Failed' :
    'Current';
  const isOTAOK = bms?.otaStatus === 'current';

  return (
    <SpaceBetween size="l">
      {/* KPI CARD ROW — six cards, mirrors the operational vehicle detail aesthetic */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: '12px' }}>
        <KPICard
          label="Battery SoH"
          value={currentSoH != null ? `${currentSoH.toFixed(1)}%` : '—'}
          sub={isAffected ? 'Affected cohort' : 'Within baseline'}
          valueStyle={isAffected && currentSoH != null && currentSoH < 95 ? KPI_VALUE_WARN_STYLE : KPI_VALUE_STYLE}
        />
        <KPICard
          label="Mileage"
          value={vehicle.mileage != null ? `${Number(vehicle.mileage).toLocaleString()} km` : '—'}
          sub={vehicle.assemblyDate ? `Since ${vehicle.assemblyDate}` : undefined}
        />
        <KPICard
          label="BMS firmware"
          value={`v${bmsVersion}`}
          sub={bmsPending ? `Pending v${bmsPending}` : undefined}
          valueStyle={bms?.otaStatus === 'older' ? KPI_VALUE_WARN_STYLE : KPI_VALUE_STYLE}
        />
        <KPICard
          label="Affected cohort"
          value={isAffected ? 'Yes' : 'No'}
          sub={isAffected ? 'Q3 2025 Voltrix batch' : undefined}
          valueStyle={isAffected ? KPI_VALUE_WARN_STYLE : KPI_VALUE_STYLE}
        />
        <KPICard
          label="OTA status"
          value={otaLabel}
          sub={isCanary ? 'Build #4823 canary' : undefined}
          valueStyle={isOTAOK ? KPI_VALUE_OK_STYLE : KPI_VALUE_WARN_STYLE}
        />
        <KPICard
          label="Connection"
          value={vehicle.connectionStatus === 'connected' ? 'Connected' : 'Offline'}
          sub={`${linkedAnomalyCount} linked anomal${linkedAnomalyCount === 1 ? 'y' : 'ies'}`}
          valueStyle={vehicle.connectionStatus === 'connected' ? KPI_VALUE_OK_STYLE : KPI_VALUE_STYLE}
        />
      </div>

      {/* Vehicle metadata + manufacturing/PLM block */}
      <Container header={<Header variant="h2">Vehicle metadata</Header>}>
        <KeyValuePairs
          columns={3}
          items={[
            { label: 'VIN',                  value: <span style={{ fontFamily: 'monospace' }}>{vehicle.vin ?? '—'}</span> },
            { label: 'Make / Model',         value: `${vehicle.make ?? '—'} ${vehicle.model ?? ''}`.trim() },
            { label: 'Year',                 value: String(vehicle.year ?? '—') },
            { label: 'Type',                 value: vehicle.vehicleType ?? '—' },
            { label: 'License plate',        value: vehicle.licensePlate ?? '—' },
            { label: 'Color',                value: vehicle.color ?? '—' },
            {
              label: 'Manufacturing batch',
              value: <span style={{ fontFamily: 'monospace' }}>{vehicle.manufacturingBatchId ?? '—'}</span>,
            },
            { label: 'Cell supplier',        value: vehicle.supplierId ?? '—' },
            { label: 'Cell lot',             value: <span style={{ fontFamily: 'monospace' }}>{vehicle.batteryCellLot ?? '—'}</span> },
            { label: 'Assembly plant',       value: vehicle.assemblyPlantId ?? '—' },
            { label: 'Operating region',     value: vehicle.regionId ?? '—' },
            { label: 'ECU configuration',    value: <span style={{ fontFamily: 'monospace' }}>{vehicle.ecuConfigId ?? '—'}</span> },
          ]}
        />
      </Container>
    </SpaceBetween>
  );
}

function TelemetryTab({ vehicle, ecuState }: { vehicle: VehicleItem; ecuState: VehicleECUState[] }) {
  const bms = ecuState.find((e) => e.ecu === 'BMS');
  const isCanary = isBuild4823CanaryRecipient(vehicle.vehicleId ?? '');
  const [activeECU, setActiveECU] = useState<string>('bms');

  const series = useMemo<DailyTelemetryAggregate[]>(() => {
    if (!vehicle.regionId || !vehicle.manufacturingBatchId || !vehicle.vehicleId) return [];
    return getTelemetryFromMetadata({
      vehicleId: vehicle.vehicleId,
      isAffectedCohort: !!vehicle.isAffectedCohort,
      regionId: vehicle.regionId,
      manufacturingBatchId: vehicle.manufacturingBatchId,
    });
  }, [vehicle.vehicleId, vehicle.regionId, vehicle.manufacturingBatchId, vehicle.isAffectedCohort]);

  return (
    <SpaceBetween size="l">
      {/* VITAL SIGNS — DTCs, linked anomalies, recent events on this VIN.
          Engineer's "what's wrong with this car right now" view. */}
      <VitalSignsStrip vehicle={vehicle} ecuState={ecuState} />

      {/* VEHICLE MODEL HEADER — names the FleetWise Model Manifest and Decoder
          Manifest backing this vehicle. Engineers reading the telemetry tab
          should immediately know which signal model is in use. */}
      <VehicleModelHeader vehicle={vehicle} ecuState={ecuState} />

      {/* ECU PICKER — engineering telemetry is organized by producing ECU /
          domain controller, matching the FleetWise Vehicle Model hierarchy. */}
      <Box>
        <SpaceBetween size="xs">
          <span style={KPI_LABEL_STYLE}>Signals by domain controller</span>
          <SegmentedControl
            selectedId={activeECU}
            onChange={({ detail }) => setActiveECU(detail.selectedId)}
            options={[
              { id: 'bms',  text: 'BMS — Battery' },
              { id: 'vcu',  text: 'VCU — Powertrain' },
              { id: 'adas', text: 'ADAS — Driver Assist' },
              { id: 'ccu',  text: 'CCU — Charging' },
              { id: 'tcu',  text: 'TCU — Connectivity' },
              { id: 'bcm',  text: 'BCM — Cabin & HVAC' },
            ]}
          />
        </SpaceBetween>
      </Box>

      {activeECU === 'bms' && (
        <BatteryThermalSection vehicle={vehicle} series={series} bms={bms} isCanary={isCanary} />
      )}
      {activeECU === 'vcu'  && <PowertrainSection   vehicle={vehicle} ecuState={ecuState} />}
      {activeECU === 'adas' && <ADASSection         vehicle={vehicle} ecuState={ecuState} />}
      {activeECU === 'ccu'  && <ChargingSection     vehicle={vehicle} ecuState={ecuState} />}
      {activeECU === 'tcu'  && <ConnectivitySection vehicle={vehicle} ecuState={ecuState} />}
      {activeECU === 'bcm'  && <CabinHVACSection    vehicle={vehicle} ecuState={ecuState} />}
    </SpaceBetween>
  );
}

// ============================================================================
// VEHICLE MODEL HEADER — the FleetWise vocabulary tying telemetry to the
// catalog/manifest concepts. Names the model, shows the decoder manifest in
// use, links to Data Processing.
// ============================================================================

function VehicleModelHeader({ vehicle, ecuState }: { vehicle: VehicleItem; ecuState: VehicleECUState[] }) {
  const navigate = useNavigate();
  const isExternal = vehicle.tenantType === 'external';
  // Vehicle model name derives from ecuConfigId — engineering convention.
  const modelId = isExternal ? 'BE6-V12-PROD' : 'BE07-V13-DEV';
  const decoderManifestVersion = isExternal ? 'cms-prod-decoder-manifest-v17' : 'cms-be07-decoder-manifest-v23';
  const totalSignals = ecuState.reduce((sum, e) => sum + e.signalCount, 0);
  const ecuCount = ecuState.length;

  return (
    <div
      style={{
        background: '#f4f6fa',
        border: '1px solid #d5dbdb',
        borderLeft: '3px solid #0972d3',
        borderRadius: 6,
        padding: '12px 16px',
      }}
    >
      <SpaceBetween size="xs">
        <SpaceBetween direction="horizontal" size="xs" alignItems="center">
          <span style={KPI_LABEL_STYLE}>Vehicle model</span>
          <Badge color={isExternal ? 'red' : 'green'}>{isExternal ? 'Production' : 'Validation'}</Badge>
        </SpaceBetween>
        <SpaceBetween direction="horizontal" size="xs" alignItems="center">
          <Link
            onFollow={(e) => { e.preventDefault(); navigate('/data-processing'); }}
            href="#"
          >
            <span style={{ fontFamily: 'monospace', fontSize: 14, fontWeight: 700 }}>
              {modelId}
            </span>
          </Link>
          <Box variant="small" color="text-body-secondary">
            {ecuCount} domain controllers · {totalSignals} signals in catalog
          </Box>
        </SpaceBetween>
        <SpaceBetween direction="horizontal" size="xs" alignItems="center">
          <Box variant="small">
            <strong>Decoder manifest:</strong>{' '}
            <span style={{ fontFamily: 'monospace' }}>{decoderManifestVersion}</span>
          </Box>
          <Link
            onFollow={(e) => { e.preventDefault(); navigate(`/data-processing?tab=signal-catalog&model=${modelId}`); }}
            href="#"
          >
            View {totalSignals} signals →
          </Link>
          <Link
            onFollow={(e) => { e.preventDefault(); navigate('/data-processing'); }}
            href="#"
          >
            Open in Vehicle Models →
          </Link>
        </SpaceBetween>
      </SpaceBetween>
    </div>
  );
}

// (DomainNavigator chip-strip removed; SegmentedControl above replaces it.)

// ============================================================================
// BATTERY & THERMAL SECTION — extracted from the previous TelemetryTab body
// (charts + KPIs + BMS banner). The rich one because this drives the demo's
// SoH investigation closed loop.
// ============================================================================

function BatteryThermalSection({
  vehicle,
  series,
  bms,
  isCanary,
}: {
  vehicle: VehicleItem;
  series: DailyTelemetryAggregate[];
  bms: VehicleECUState | undefined;
  isCanary: boolean;
}) {
  const navigate = useNavigate();
  // Compute KPIs from the series
  const kpis = useMemo(() => {
    if (series.length < 30) return null;
    const recent90 = series.slice(-90);
    const startSoH = recent90[0].batterySoH_pct;
    const endSoH = recent90[recent90.length - 1].batterySoH_pct;
    const months = recent90.length / 30;
    const degradationRate = (startSoH - endSoH) / months;
    const peakTempLast30 = Math.max(...series.slice(-30).map((d) => d.batteryTempPeak_C));
    const thermalEvents30 = series.slice(-30).reduce((a, b) => a + b.thermalEventsCount, 0);
    return {
      currentSoH: endSoH,
      degradationRate,
      peakTempLast30,
      thermalEvents30,
    };
  }, [series]);

  const sohSeries = useMemo(() => {
    if (series.length === 0) return [];
    const result: any[] = [
      {
        title: 'Battery SoH',
        type: 'line' as const,
        valueFormatter: (v: number) => `${v.toFixed(2)}%`,
        data: series.map((d) => ({ x: new Date(d.date), y: d.batterySoH_pct })),
      },
    ];
    if (isCanary) {
      result.push({
        title: 'BMS v3.3.0 applied',
        type: 'threshold' as const,
        x: new Date('2026-05-19'),
      });
    }
    return result;
  }, [series, isCanary]);

  const thermalSeries = useMemo(() => {
    if (series.length === 0) return [];
    return [
      {
        title: 'Battery peak temperature',
        type: 'line' as const,
        valueFormatter: (v: number) => `${v.toFixed(1)}°C`,
        data: series.map((d) => ({ x: new Date(d.date), y: d.batteryTempPeak_C })),
      },
      {
        title: 'Ambient temperature',
        type: 'line' as const,
        valueFormatter: (v: number) => `${v.toFixed(1)}°C`,
        data: series.map((d) => ({ x: new Date(d.date), y: d.ambientTempAvg_C })),
      },
      {
        title: 'Voltrix thermal limit (42°C)',
        type: 'threshold' as const,
        y: 42,
      },
    ];
  }, [series]);

  if (series.length === 0) {
    return (
      <Container header={<Header variant="h2" actions={<Badge color="blue">BMS</Badge>}>Battery & Thermal</Header>}>
        <Box padding="l">
          Telemetry not available — vehicle is missing engineering metadata
          (region or manufacturing batch). Check{' '}
          <code>regionId</code>, <code>manufacturingBatchId</code> on the vehicle record.
        </Box>
      </Container>
    );
  }

  const bmsCov = bms ? getECUCoverage(bms) : { total: 64, emitting: 64, missing: [] };

  return (
    <SpaceBetween size="m">
      <Container>
        <Header
          variant="h2"
          description={`BMS firmware v${bms?.currentVersion ?? '—'} · ${bmsCov.emitting}/${bmsCov.total} signals emitting${bmsCov.missing.length ? ` · missing: ${bmsCov.missing.join(', ')} (require BMS ≥ v3.3.0)` : ''}`}
          actions={
            <Link
              onFollow={(e) => { e.preventDefault(); navigate('/data-processing?tab=signal-catalog&ecu=BMS'); }}
              href="#"
            >
              View {ECU_CATALOG.BMS.signalCount} signals in catalog →
            </Link>
          }
        >
          Battery — BMS
        </Header>
      </Container>

      {/* KPIs */}
      {kpis && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
          <KPICard
            label="Current SoH"
            value={`${kpis.currentSoH.toFixed(1)}%`}
            sub="of 100% nominal"
          />
          <KPICard
            label="Degradation rate"
            value={`${kpis.degradationRate.toFixed(2)} %/mo`}
            sub={kpis.degradationRate > 0.95 ? 'Above 0.9 %/mo baseline' : 'Within baseline'}
            valueStyle={kpis.degradationRate > 0.95 ? KPI_VALUE_WARN_STYLE : KPI_VALUE_OK_STYLE}
          />
          <KPICard
            label="Peak battery temp (30d)"
            value={`${kpis.peakTempLast30.toFixed(1)}°C`}
            sub="Voltrix datasheet rev 2.3 limit: 42°C"
            valueStyle={kpis.peakTempLast30 > 42 ? KPI_VALUE_WARN_STYLE : KPI_VALUE_OK_STYLE}
          />
          <KPICard
            label="Thermal events (30d)"
            value={String(kpis.thermalEvents30)}
            sub="cells exceeding limit"
            valueStyle={kpis.thermalEvents30 > 10 ? KPI_VALUE_WARN_STYLE : KPI_VALUE_STYLE}
          />
        </div>
      )}

      {/* SoH chart */}
      <Container
        header={
          <Header
            variant="h3"
            description={
              isCanary
                ? 'BMS firmware v3.3.0 applied today via OTA Build #4823 — post-fix telemetry will accumulate over the next days.'
                : `Battery State-of-Health, daily aggregate, last ${series.length} days. Affected cohort vehicles show planted +12.2% degradation vs 0.9 %/mo baseline.`
            }
            actions={isCanary ? <Badge color="green">Canary recipient</Badge> : undefined}
          >
            Battery SoH trend
          </Header>
        }
      >
        <LineChart
          series={sohSeries}
          xScaleType="time"
          xTitle="Date"
          yTitle="State of Health (%)"
          height={280}
          hideFilter
          loadingText="Loading telemetry"
          errorText="Error loading telemetry"
          recoveryText="Retry"
          empty={<Box>No telemetry available.</Box>}
          i18nStrings={{
            xTickFormatter: (d: Date) => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
            yTickFormatter: (v: number) => `${v.toFixed(0)}%`,
          }}
        />
      </Container>

      {/* Thermal chart */}
      <Container
        header={
          <Header
            variant="h3"
            description="Daily peak battery cell temperature vs ambient. Threshold line is the Voltrix datasheet rev 2.3 thermal limit (42°C, lowered from the 45°C the BE 6 pack thermal envelope assumed)."
          >
            Thermal envelope
          </Header>
        }
      >
        <LineChart
          series={thermalSeries}
          xScaleType="time"
          xTitle="Date"
          yTitle="Temperature (°C)"
          height={280}
          hideFilter
          loadingText="Loading thermal data"
          errorText="Error loading thermal data"
          recoveryText="Retry"
          empty={<Box>No thermal data available.</Box>}
          i18nStrings={{
            xTickFormatter: (d: Date) => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
            yTickFormatter: (v: number) => `${v.toFixed(0)}°C`,
          }}
        />
      </Container>

      {bms && (bms.otaStatus === 'older' || bms.otaStatus === 'pending') && (
        <Container>
          <SpaceBetween size="s">
            <StatusIndicator type="warning">
              BMS firmware is {bms.otaStatus === 'older' ? 'behind baseline' : 'pending OTA update'} —
              Build #4823 will ship v{bms.pendingVersion ?? '3.3.0'}.
            </StatusIndicator>
            <Box variant="small">{ECU_CATALOG.BMS.description}</Box>
          </SpaceBetween>
        </Container>
      )}
    </SpaceBetween>
  );
}

// ============================================================================
// SECONDARY DOMAIN SECTIONS — KPI strips per subsystem. Values are derived
// from the vehicle metadata where realistic, otherwise stubbed at sensible
// magnitudes for an EV in the BE 6 / BE.07 platform envelope. Each section
// is gated to the relevant ECU.
// ============================================================================

function PowertrainSection({ vehicle, ecuState }: { vehicle: VehicleItem; ecuState: VehicleECUState[] }) {
  const navigate = useNavigate();
  const vcu = ecuState.find((e) => e.ecu === 'VCU');
  // Derive realistic-looking numbers from totalTrips/mileage so values vary per vehicle.
  const trips = (vehicle as any).totalTrips ?? 200;
  const peakTorque = 360 + (trips % 30);
  const driveEfficiencyWhKm = Math.round(165 + (trips % 18));
  const regen30d = Math.round(180 + (trips % 90));
  const inverterPeakC = 72 + ((trips * 3) % 12);

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={`VCU firmware v${vcu?.currentVersion ?? '—'} · ${ECU_CATALOG.VCU.signalCount}/${ECU_CATALOG.VCU.signalCount} signals emitting`}
          actions={
            <Link
              onFollow={(e) => { e.preventDefault(); navigate('/data-processing?tab=signal-catalog&ecu=VCU'); }}
              href="#"
            >
              View {ECU_CATALOG.VCU.signalCount} signals in catalog →
            </Link>
          }
        >
          Powertrain — VCU
        </Header>
      }
    >
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
        <KPICard label="Peak motor torque (30d)" value={`${peakTorque} Nm`} sub="bound 380 Nm spec" />
        <KPICard
          label="Drive efficiency"
          value={`${driveEfficiencyWhKm} Wh/km`}
          sub={driveEfficiencyWhKm < 175 ? '−5% vs baseline 175' : 'Within baseline'}
          valueStyle={driveEfficiencyWhKm < 175 ? KPI_VALUE_OK_STYLE : KPI_VALUE_STYLE}
        />
        <KPICard
          label="Regen energy recovered"
          value={`${regen30d} kWh`}
          sub="last 30 days"
        />
        <KPICard
          label="Inverter peak temp"
          value={`${inverterPeakC}°C`}
          sub="Bosch SiC, limit 110°C"
          valueStyle={inverterPeakC > 95 ? KPI_VALUE_WARN_STYLE : KPI_VALUE_OK_STYLE}
        />
      </div>
    </Container>
  );
}

function ADASSection({ vehicle, ecuState }: { vehicle: VehicleItem; ecuState: VehicleECUState[] }) {
  const navigate = useNavigate();
  const adas = ecuState.find((e) => e.ecu === 'ADAS');
  // For BE.07 (validation fleet running v3.0.0-rc1 firmware), false-positive
  // AEB rate is elevated. For BE 6 production fleet, normal levels.
  const isBE07 = vehicle.tenantType === 'internal';
  const trips = (vehicle as any).totalTrips ?? 200;
  const accEngagementsPer100 = 38 + (trips % 18);
  const laneKeepPer100 = 92 + (trips % 32);
  const aebPer1000 = isBE07 ? 1.8 : 0.2;
  const sensorConfidence = 0.91 + ((trips % 9) / 100);

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={`ADAS firmware v${adas?.currentVersion ?? '—'} · ${ECU_CATALOG.ADAS.signalCount}/${ECU_CATALOG.ADAS.signalCount} signals emitting`}
          actions={
            <Link
              onFollow={(e) => { e.preventDefault(); navigate('/data-processing?tab=signal-catalog&ecu=ADAS'); }}
              href="#"
            >
              View {ECU_CATALOG.ADAS.signalCount} signals in catalog →
            </Link>
          }
        >
          Driver Assistance — ADAS
        </Header>
      }
    >
      <SpaceBetween size="m">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
          <KPICard label="ACC engagements" value={`${accEngagementsPer100}`} sub="per 100 km, last 30d" />
          <KPICard label="Lane-keep activations" value={`${laneKeepPer100}`} sub="per 100 km, last 30d" />
          <KPICard
            label="AEB triggers (false-pos)"
            value={`${aebPer1000.toFixed(1)}`}
            sub={isBE07 ? 'per 1000 km · pre-prod anomaly' : 'per 1000 km'}
            valueStyle={isBE07 ? KPI_VALUE_WARN_STYLE : KPI_VALUE_OK_STYLE}
          />
          <KPICard
            label="Sensor confidence avg"
            value={sensorConfidence.toFixed(2)}
            sub="0.0 - 1.0 range"
          />
        </div>
        {isBE07 && (
          <Box variant="small">
            <StatusIndicator type="warning">
              Elevated false-positive AEB rate on BE.07 ADAS firmware v3.0.0-rc1 — see anom-be07-adas-001.
            </StatusIndicator>
          </Box>
        )}
      </SpaceBetween>
    </Container>
  );
}

function ChargingSection({ vehicle, ecuState }: { vehicle: VehicleItem; ecuState: VehicleECUState[] }) {
  const navigate = useNavigate();
  const ccu = ecuState.find((e) => e.ecu === 'CCU');
  const trips = (vehicle as any).totalTrips ?? 200;
  const charges = Math.round(trips * 0.32); // ~1 charge per 3 trips
  const dcSessions = Math.round(charges * 0.18);
  const acSessions = charges - dcSessions;
  const avgDcKwh = 31 + (trips % 6);
  const avgDcMinutes = 26 + (trips % 8);
  const lastSessionDelta = 32 + (trips % 28);

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={`CCU firmware v${ccu?.currentVersion ?? '—'} · ${ECU_CATALOG.CCU.signalCount}/${ECU_CATALOG.CCU.signalCount} signals emitting`}
          actions={
            <Link
              onFollow={(e) => { e.preventDefault(); navigate('/data-processing?tab=signal-catalog&ecu=CCU'); }}
              href="#"
            >
              View {ECU_CATALOG.CCU.signalCount} signals in catalog →
            </Link>
          }
        >
          Charging — CCU
        </Header>
      }
    >
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
        <KPICard
          label="Total charge cycles"
          value={`${charges}`}
          sub={`${dcSessions} DC fast · ${acSessions} AC L2`}
        />
        <KPICard
          label="Avg DC fast session"
          value={`${avgDcKwh} kWh`}
          sub={`${avgDcMinutes} min, 80→peak SoC`}
        />
        <KPICard
          label="Last session"
          value={`+${lastSessionDelta}% SoC`}
          sub="ISO 15118 plug-and-charge"
        />
        <KPICard
          label="PnC handshake success"
          value="99%"
          sub="last 30 days"
          valueStyle={KPI_VALUE_OK_STYLE}
        />
      </div>
    </Container>
  );
}

function ConnectivitySection({ vehicle, ecuState }: { vehicle: VehicleItem; ecuState: VehicleECUState[] }) {
  const navigate = useNavigate();
  const tcu = ecuState.find((e) => e.ecu === 'TCU');
  const isConnected = vehicle.connectionStatus === 'connected';
  const trips = (vehicle as any).totalTrips ?? 200;
  const signalStrength = -68 - (trips % 18);
  const signalsPerDay = 5800 + ((trips * 17) % 1200);
  const lastSeen = vehicle.lastSeenAt
    ? new Date(vehicle.lastSeenAt).toLocaleString()
    : 'Unknown';

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={`TCU firmware v${tcu?.currentVersion ?? '—'} · ${ECU_CATALOG.TCU.signalCount}/${ECU_CATALOG.TCU.signalCount} signals emitting`}
          actions={
            <Link
              onFollow={(e) => { e.preventDefault(); navigate('/data-processing?tab=signal-catalog&ecu=TCU'); }}
              href="#"
            >
              View {ECU_CATALOG.TCU.signalCount} signals in catalog →
            </Link>
          }
        >
          Connectivity — TCU
        </Header>
      }
    >
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
        <KPICard
          label="Connection status"
          value={isConnected ? 'Connected' : 'Offline'}
          sub={`Last seen: ${lastSeen}`}
          valueStyle={isConnected ? KPI_VALUE_OK_STYLE : KPI_VALUE_STYLE}
        />
        <KPICard
          label="Cellular signal avg"
          value={`${signalStrength} dBm`}
          sub="4G LTE-CAT4, India"
        />
        <KPICard
          label="Signals/day"
          value={signalsPerDay.toLocaleString()}
          sub={`TCU v${tcu?.currentVersion ?? '—'}, manifest v17`}
        />
        <KPICard
          label="Last OTA campaign"
          value={tcu?.pipelineId ?? 'None active'}
          sub={tcu?.otaStatus === 'current' ? 'Up to date' : tcu?.otaStatus ?? '—'}
        />
      </div>
    </Container>
  );
}

function CabinHVACSection({ vehicle, ecuState }: { vehicle: VehicleItem; ecuState: VehicleECUState[] }) {
  const navigate = useNavigate();
  const bcm = ecuState.find((e) => e.ecu === 'BCM');
  const trips = (vehicle as any).totalTrips ?? 200;
  const compressorCycles = 30 + (trips % 6);
  const heatPumpCOP = 3.0 + ((trips % 8) / 10);
  const setTempAvg = 22 + (trips % 4);
  const cabinStartTempAvg = 28 + (trips % 8);
  const isBE07Cold = vehicle.tenantType === 'internal' && (vehicle.regionId === 'Punjab-Cool');

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={`BCM firmware v${bcm?.currentVersion ?? '—'} · ${ECU_CATALOG.BCM.signalCount}/${ECU_CATALOG.BCM.signalCount} signals emitting`}
          actions={
            <Link
              onFollow={(e) => { e.preventDefault(); navigate('/data-processing?tab=signal-catalog&ecu=BCM'); }}
              href="#"
            >
              View {ECU_CATALOG.BCM.signalCount} signals in catalog →
            </Link>
          }
        >
          Cabin & HVAC — BCM
        </Header>
      }
    >
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
        <KPICard
          label="Compressor cycles/hr"
          value={String(compressorCycles)}
          sub="last 30d, baseline 32"
        />
        <KPICard
          label="Heat pump COP"
          value={heatPumpCOP.toFixed(1)}
          sub={isBE07Cold ? 'Below 3.4 sim — see anom-be07-hvac-004' : 'Within baseline'}
          valueStyle={isBE07Cold ? KPI_VALUE_WARN_STYLE : KPI_VALUE_OK_STYLE}
        />
        <KPICard
          label="Cabin temp setpoint avg"
          value={`${setTempAvg}°C`}
          sub="user-selected, cool dominance"
        />
        <KPICard
          label="Cabin temp at start"
          value={`${cabinStartTempAvg}°C`}
          sub="ambient + solar load"
        />
      </div>
    </Container>
  );
}

function AnomaliesTab({ anomalies }: { anomalies: typeof ENGINEERING_ANOMALIES }) {
  return (
    <Table
      columnDefinitions={[
        { id: 'title',    header: 'Title',    cell: (a) => <Link href={`/engineering/investigate/${a.anomalyId}`}>{a.title}</Link> },
        { id: 'severity', header: 'Severity', cell: (a) => (
          <StatusIndicator type={a.severity === 'high' || a.severity === 'critical' ? 'warning' : 'info'}>
            {a.severity}
          </StatusIndicator>
        )},
        { id: 'count',    header: 'Cohort size', cell: (a) => a.affectedVehicleCount },
        { id: 'detected', header: 'Detected',    cell: (a) => new Date(a.detectedAt).toLocaleString() },
      ]}
      items={anomalies}
      empty={<Box>No linked anomalies.</Box>}
    />
  );
}

// ============================================================================
// ECUs TAB — fully built, joins ECU mock data with per-vehicle ECU state
// ============================================================================

function ECUsTab({ ecuState, ecuConfigId }: { ecuState: VehicleECUState[]; ecuConfigId: string }) {
  const config = ECU_CONFIGS[ecuConfigId];
  const [expandedECU, setExpandedECU] = useState<ECUId | null>(null);

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h2"
            description={
              config
                ? `Configuration: ${config.configId} · ${config.description}`
                : 'No ECU configuration found.'
            }
            counter={`(${ecuState.length})`}
          >
            Electronic Control Units
          </Header>
        }
      >
        <Table
          variant="borderless"
          columnDefinitions={[
            {
              id: 'ecu',
              header: 'ECU',
              cell: (e: VehicleECUState) => (
                <Box>
                  <Box display="inline" fontWeight="bold">
                    {e.ecu}
                  </Box>{' '}
                  <Box display="inline" variant="small">— {e.displayName}</Box>
                </Box>
              ),
              minWidth: 200,
            },
            {
              id: 'domain',
              header: 'Domain',
              cell: (e: VehicleECUState) => <Badge>{e.domain}</Badge>,
              minWidth: 120,
            },
            {
              id: 'vendor',
              header: 'Vendor',
              cell: (e: VehicleECUState) => e.vendor,
              minWidth: 180,
            },
            {
              id: 'partNumber',
              header: 'Part number',
              cell: (e: VehicleECUState) => <Box fontFamily="monospace">{e.partNumberFamily}</Box>,
              minWidth: 180,
            },
            {
              id: 'currentVersion',
              header: 'Current version',
              cell: (e: VehicleECUState) => (
                <Box>
                  <Box fontFamily="monospace" fontWeight="bold">
                    {e.currentVersion}
                  </Box>
                  {e.currentVersion !== e.baselineVersion && (
                    <Box variant="small">baseline {e.baselineVersion}</Box>
                  )}
                </Box>
              ),
              minWidth: 140,
            },
            {
              id: 'pendingVersion',
              header: 'Pending',
              cell: (e: VehicleECUState) =>
                e.pendingVersion ? <Box fontFamily="monospace">{e.pendingVersion}</Box> : <Box color="text-body-secondary">—</Box>,
              minWidth: 110,
            },
            {
              id: 'otaStatus',
              header: 'OTA status',
              cell: (e: VehicleECUState) => <ECUStatusIndicator status={e.otaStatus} />,
              minWidth: 140,
            },
            {
              id: 'signals',
              header: 'Signals',
              cell: (e: VehicleECUState) => (
                <Link href={`/data-processing?tab=signal-catalog&ecu=${e.ecu}`}>
                  {e.signalCount} signals →
                </Link>
              ),
              minWidth: 110,
            },
            {
              id: 'lastUpdated',
              header: 'Last updated',
              cell: (e: VehicleECUState) =>
                <Box variant="small">{new Date(e.lastUpdatedAt).toLocaleDateString()}</Box>,
              minWidth: 130,
            },
          ]}
          items={ecuState}
          empty={<Box>No ECUs registered for this vehicle.</Box>}
        />
      </Container>
      <Container header={<Header variant="h3">Signals by ECU (preview)</Header>}>
        <SpaceBetween size="s">
          {ecuState.map((e) => {
            const signals = getSignalsByECU(e.ecu);
            const newSignals = signals.filter((s) => s.isNewInLatestBuild);
            if (signals.length === 0) return null;
            return (
              <ExpandableSection
                key={e.ecu}
                headerText={`${e.ecu} — ${e.displayName} (${signals.length} signals${newSignals.length > 0 ? `, ${newSignals.length} NEW` : ''})`}
              >
                <Table
                  variant="embedded"
                  columnDefinitions={[
                    { id: 'name', header: 'Signal', cell: (s: any) => <Box fontFamily="monospace">{s.fullyQualifiedName}</Box> },
                    { id: 'min',  header: 'Min ECU version', cell: (s: any) => <Box fontFamily="monospace">≥ v{s.ecuMinVersion}</Box> },
                    { id: 'desc', header: 'Description', cell: (s: any) => s.description ?? '—' },
                    { id: 'new',  header: '', cell: (s: any) => (s.isNewInLatestBuild ? <Badge color="green">NEW in {s.introducedByPipeline}</Badge> : null) },
                  ]}
                  items={signals}
                />
              </ExpandableSection>
            );
          })}
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
}

function ECUStatusIndicator({ status }: { status: VehicleECUState['otaStatus'] }) {
  switch (status) {
    case 'current':    return <StatusIndicator type="success">Current</StatusIndicator>;
    case 'pending':    return <StatusIndicator type="pending">Pending</StatusIndicator>;
    case 'in-flight':  return <StatusIndicator type="in-progress">In flight</StatusIndicator>;
    case 'failed':     return <StatusIndicator type="error">Failed</StatusIndicator>;
    case 'older':      return <StatusIndicator type="warning">Older</StatusIndicator>;
  }
}

// ============================================================================
// PARTS TAB — hierarchical BOM. Root = vehicle assembly (top-level header
// card). Children of root = subsystems (rendered as a grid of cards, each
// expandable to its own parts tree). Depth ≥ 2 = nested ExpandableSections.
// Per-vehicle batch/lot fields enrich the HV battery cell branch.
// ============================================================================

interface BomStats {
  totalParts: number;
  uniqueSuppliers: Set<string>;
  linkedECUs: Set<string>;
}

function computeBomStats(node: PartNode): BomStats {
  const stats: BomStats = { totalParts: 0, uniqueSuppliers: new Set(), linkedECUs: new Set() };
  function visit(n: PartNode) {
    stats.totalParts += 1;
    if (n.supplierId) stats.uniqueSuppliers.add(n.supplierId);
    if (n.linkedECU) stats.linkedECUs.add(n.linkedECU);
    (n.children ?? []).forEach(visit);
  }
  visit(node);
  return stats;
}

function PartsTab({ bom, vehicle }: { bom: PartNode | undefined; vehicle: VehicleItem }) {
  if (!bom) {
    return <Box padding="l">No BOM available for ECU config <code>{vehicle.ecuConfigId ?? '—'}</code>.</Box>;
  }
  const rootStats = computeBomStats(bom);
  const subsystems = bom.children ?? [];

  return (
    <SpaceBetween size="l">
      {/* SOURCE BANNER */}
      <Box variant="small" color="text-body-secondary">
        Sourced read-only from <strong>Acme Motors PLM (Teamcenter)</strong>.
        Per-vehicle batch and cell-lot fields are enriched from this vehicle&apos;s
        manufacturing record.
      </Box>

      {/* VEHICLE BOM ROOT — distinct header card */}
      <Container
        header={
          <Header
            variant="h2"
            description={
              <SpaceBetween direction="horizontal" size="xs">
                <span style={{ fontFamily: 'monospace' }}>{bom.partNumber}</span>
                <Badge color="grey">{bom.designRev}</Badge>
              </SpaceBetween>
            }
          >
            {bom.name}
          </Header>
        }
      >
        <SpaceBetween size="m">
          <ColumnLayout columns={4} variant="text-grid">
            <div>
              <Box variant="awsui-key-label">Total parts</Box>
              <Box fontSize="display-l" fontWeight="bold">{rootStats.totalParts}</Box>
            </div>
            <div>
              <Box variant="awsui-key-label">Subsystems</Box>
              <Box fontSize="display-l" fontWeight="bold">{subsystems.length}</Box>
            </div>
            <div>
              <Box variant="awsui-key-label">Unique suppliers</Box>
              <Box fontSize="display-l" fontWeight="bold">{rootStats.uniqueSuppliers.size}</Box>
            </div>
            <div>
              <Box variant="awsui-key-label">Domain controllers</Box>
              <Box fontSize="display-l" fontWeight="bold">{rootStats.linkedECUs.size}</Box>
              <Box variant="small" color="text-body-secondary">
                {Array.from(rootStats.linkedECUs).join(' · ')}
              </Box>
            </div>
          </ColumnLayout>

          {(bom.requirements.length > 0 || bom.testReports.length > 0) && (
            <ColumnLayout columns={2} variant="text-grid">
              {bom.requirements.length > 0 && (
                <div>
                  <Box variant="awsui-key-label">Vehicle-level requirements</Box>
                  <SpaceBetween size="xxs">
                    {bom.requirements.map((r) => (
                      <Box key={r.reqId} variant="small">
                        <Link href="#"><span style={{ fontFamily: 'monospace' }}>{r.reqId}</span></Link>
                        {' '}{r.title} <Box variant="small" color="text-body-secondary" display="inline">(rev {r.rev})</Box>
                      </Box>
                    ))}
                  </SpaceBetween>
                </div>
              )}
              {bom.testReports.length > 0 && (
                <div>
                  <Box variant="awsui-key-label">Vehicle-level test reports</Box>
                  <SpaceBetween size="xxs">
                    {bom.testReports.map((t) => (
                      <Box key={t.reportId} variant="small">
                        <Link href="#"><span style={{ fontFamily: 'monospace' }}>{t.reportId}</span></Link>
                        {' '}{t.title}{' '}
                        <StatusIndicator type={t.status === 'passed' ? 'success' : t.status === 'failed' ? 'error' : t.status === 'open' ? 'pending' : 'warning'}>
                          {t.status}
                        </StatusIndicator>
                      </Box>
                    ))}
                  </SpaceBetween>
                </div>
              )}
            </ColumnLayout>
          )}
        </SpaceBetween>
      </Container>

      {/* SUBSYSTEMS — grid of cards, each expandable to its own tree */}
      <Box>
        <Box variant="awsui-key-label" margin={{ bottom: 's' }}>
          Subsystems ({subsystems.length})
        </Box>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(440px, 1fr))', gap: '12px' }}>
          {subsystems.map((sub) => (
            <SubsystemCard key={sub.partNumber} subsystem={sub} vehicle={vehicle} />
          ))}
        </div>
      </Box>
    </SpaceBetween>
  );
}

function SubsystemCard({ subsystem, vehicle }: { subsystem: PartNode; vehicle: VehicleItem }) {
  const stats = computeBomStats(subsystem);
  // Subsystem stats exclude the subsystem node itself from "parts"
  const partsBelow = stats.totalParts - 1;

  return (
    <Container
      header={
        <Header
          variant="h3"
          description={
            <SpaceBetween direction="horizontal" size="xxs">
              <span style={{ fontFamily: 'monospace' }}>{subsystem.partNumber}</span>
              <Badge color="grey">{subsystem.designRev}</Badge>
              {Array.from(stats.linkedECUs).map((ecu) => (
                <Badge key={ecu} color="blue">{ecu}</Badge>
              ))}
            </SpaceBetween>
          }
        >
          {subsystem.name}
        </Header>
      }
    >
      <SpaceBetween size="s">
        <Box variant="small" color="text-body-secondary">
          <strong>{partsBelow}</strong> parts · <strong>{stats.uniqueSuppliers.size}</strong> suppliers
          {stats.linkedECUs.size > 0 && (<> · runs <strong>{stats.linkedECUs.size}</strong> ECU{stats.linkedECUs.size === 1 ? '' : 's'}</>)}
        </Box>

        {subsystem.requirements.length > 0 && (
          <Box variant="small">
            <strong>Requirements:</strong>{' '}
            {subsystem.requirements.map((r, i) => (
              <span key={r.reqId}>
                {i > 0 && '; '}<Link href="#">{r.reqId}</Link>
              </span>
            ))}
          </Box>
        )}

        <ExpandableSection
          headerText={`Components (${(subsystem.children ?? []).length})`}
          variant="footer"
        >
          <SpaceBetween size="xs">
            {(subsystem.children ?? []).map((child) => (
              <PartTreeNode key={child.partNumber} node={child} vehicle={vehicle} depth={2} />
            ))}
          </SpaceBetween>
        </ExpandableSection>
      </SpaceBetween>
    </Container>
  );
}

function PartTreeNode({ node, vehicle, depth }: { node: PartNode; vehicle: VehicleItem; depth: number }) {
  const isLeaf = !node.children || node.children.length === 0;

  // Per-vehicle override: HV battery cell uses the vehicle's actual supplier + batch + lot,
  // not the BOM default. This is the BOM smoking gun for the affected cohort.
  const supplierLabel =
    node.perVehicleBatch
      ? (vehicle.supplierId ?? node.supplierId ?? '—')
      : (node.supplierId ?? '—');
  const batchLabel = node.perVehicleBatch ? (vehicle.manufacturingBatchId ?? '—') : null;
  const cellLot = node.perVehicleBatch ? (vehicle.batteryCellLot ?? '—') : null;

  const headerText = (
    <Box>
      <Box display="inline" fontWeight="bold">
        {node.name}
      </Box>{' '}
      <Box display="inline" variant="small" fontFamily="monospace" color="text-body-secondary">
        ({node.partNumber})
      </Box>
      {node.linkedECU && (
        <>
          {' '}
          <Badge color="blue">runs {node.linkedECU}</Badge>
        </>
      )}
      {node.perVehicleBatch && batchLabel && (
        <>
          {' '}
          <Badge color={vehicle.isAffectedCohort ? 'red' : 'grey'}>{batchLabel}</Badge>
        </>
      )}
    </Box>
  );

  const detail = (
    <SpaceBetween size="xs">
      <Box variant="small">
        <strong>Supplier:</strong> {supplierLabel} · <strong>Design rev:</strong> {node.designRev}
        {cellLot && (<> · <strong>Cell lot:</strong> {cellLot}</>)}
      </Box>
      {node.requirements.length > 0 && (
        <Box variant="small">
          <strong>Requirements: </strong>
          {node.requirements.map((r, i) => (
            <span key={r.reqId}>
              {i > 0 && '; '}
              <Link href="#">{r.reqId}</Link> {r.title} (rev {r.rev})
            </span>
          ))}
        </Box>
      )}
      {node.testReports.length > 0 && (
        <Box variant="small">
          <strong>Test reports: </strong>
          {node.testReports.map((t, i) => (
            <span key={t.reportId}>
              {i > 0 && '; '}
              <Link href="#">{t.reportId}</Link> — {t.title}{' '}
              <StatusIndicator type={t.status === 'passed' ? 'success' : t.status === 'failed' ? 'error' : t.status === 'open' ? 'pending' : 'warning'}>
                {t.status}
              </StatusIndicator>
            </span>
          ))}
        </Box>
      )}
    </SpaceBetween>
  );

  if (isLeaf) {
    return (
      <Container>
        <SpaceBetween size="xs">
          {headerText}
          {detail}
        </SpaceBetween>
      </Container>
    );
  }

  return (
    <ExpandableSection
      headerText={headerText as unknown as string}
      defaultExpanded={depth < 2}
      variant={depth === 0 ? 'container' : 'default'}
    >
      <SpaceBetween size="s">
        {detail}
        {node.children!.map((child) => (
          <PartTreeNode key={child.partNumber} node={child} vehicle={vehicle} depth={depth + 1} />
        ))}
      </SpaceBetween>
    </ExpandableSection>
  );
}

// ============================================================================
// KNOWLEDGE TAB
// ============================================================================

function KnowledgeTab() {
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
// VITAL SIGNS STRIP — engineer's "what's wrong with this car right now" view.
// Active DTCs, linked anomalies, recent events. Surfaces engineering forensics
// without overwhelming the page.
// ============================================================================

interface DTC {
  code: string;
  description: string;
  firstSeen: string;
  lastSeen: string;
  occurrences: number;
}

interface RecentEvent {
  eventId: string;
  category: 'safety' | 'maintenance';
  severity: 'low' | 'medium' | 'high' | 'critical';
  firedAt: string;
  occurrences: number;
}

function VitalSignsStrip({ vehicle, ecuState }: { vehicle: VehicleItem; ecuState: VehicleECUState[] }) {
  const navigate = useNavigate();
  const isAffected = !!vehicle.isAffectedCohort;
  const isExternal = vehicle.tenantType === 'external';
  const isInternal = vehicle.tenantType === 'internal';
  const isConnected = vehicle.connectionStatus === 'connected';
  const lastSeen = vehicle.lastSeenAt
    ? new Date(vehicle.lastSeenAt).toLocaleString()
    : 'Unknown';

  // Active DTCs — affected cohort vehicles emit P0AA6 per anom-be6-safety-002.
  const activeDTCs: DTC[] = useMemo(() => {
    if (isExternal && isAffected) {
      return [{
        code: 'P0AA6',
        description: 'High Voltage System Isolation Fault — DC isolation resistance below threshold',
        firstSeen: '2026-05-12T09:00:00Z',
        lastSeen: '2026-05-20T08:14:00Z',
        occurrences: 14,
      }];
    }
    return [];
  }, [isExternal, isAffected]);

  // Linked anomalies — pull from anomaly catalog, scoped to this vehicle's fleet/cohort.
  const linkedAnomalies = useMemo(() => {
    return ENGINEERING_ANOMALIES.filter((a) => {
      if (!a.affectedFleets.includes(vehicle.fleetId ?? '')) return false;
      // Match severity: only show high/medium that actually map to this vehicle
      if (a.severity === 'low') return false;
      // Affected cohort matches battery + safety anomalies; everyone matches fleet-level
      return isAffected || a.affectedVehicleCount >= 100;
    }).slice(0, 2);
  }, [vehicle.fleetId, isAffected]);

  // Recent events fired on this VIN — synthesized for demo.
  const recentEvents: RecentEvent[] = useMemo(() => {
    const events: RecentEvent[] = [];
    if (isExternal && isAffected) {
      events.push({
        eventId: 'maintenance.thermal_runaway',
        category: 'maintenance',
        severity: 'critical',
        firedAt: '3 days ago',
        occurrences: 1,
      });
    }
    if (isInternal && (vehicle as any).ecuConfigId === 'ECU-CONFIG-BE07-V13-DEV') {
      // BE.07 with the ADAS firmware that has the false-positive AEB issue
      events.push({
        eventId: 'safety.aeb_activation',
        category: 'safety',
        severity: 'high',
        firedAt: '4 hours ago',
        occurrences: 3,
      });
    }
    events.push({
      eventId: 'safety.harsh_braking',
      category: 'safety',
      severity: 'low',
      firedAt: '14 hours ago',
      occurrences: 2,
    });
    events.push({
      eventId: 'safety.harsh_cornering',
      category: 'safety',
      severity: 'low',
      firedAt: '2 days ago',
      occurrences: 1,
    });
    return events;
  }, [isExternal, isInternal, isAffected, (vehicle as any).ecuConfigId]);

  const totalIssues = activeDTCs.length + linkedAnomalies.length;

  return (
    <Container>
      <SpaceBetween size="s">
        <SpaceBetween direction="horizontal" size="xs" alignItems="center">
          <span style={KPI_LABEL_STYLE}>Vehicle state</span>
          <StatusIndicator type={isConnected ? 'success' : 'stopped'}>
            {isConnected ? 'Connected' : 'Offline'}
          </StatusIndicator>
          <Box variant="small" color="text-body-secondary">
            Last seen {lastSeen}
          </Box>
          {totalIssues > 0 && (
            <Badge color="red">{totalIssues} engineering flag{totalIssues === 1 ? '' : 's'}</Badge>
          )}
        </SpaceBetween>

        <ColumnLayout columns={3} variant="text-grid">
          {/* Active DTCs */}
          <div>
            <Box variant="awsui-key-label">
              Active DTCs ({activeDTCs.length})
            </Box>
            {activeDTCs.length === 0 ? (
              <Box variant="small" color="text-body-secondary">No active fault codes.</Box>
            ) : (
              <SpaceBetween size="xxs">
                {activeDTCs.map((dtc) => (
                  <Box key={dtc.code}>
                    <Box>
                      <span style={{ fontFamily: 'monospace', fontWeight: 700, color: '#b45309' }}>
                        {dtc.code}
                      </span>{' '}
                      <Box display="inline" variant="small">{dtc.description}</Box>
                    </Box>
                    <Box variant="small" color="text-body-secondary">
                      {dtc.occurrences} events · last fired {new Date(dtc.lastSeen).toLocaleString()}
                    </Box>
                  </Box>
                ))}
              </SpaceBetween>
            )}
          </div>

          {/* Linked anomalies */}
          <div>
            <Box variant="awsui-key-label">
              Linked anomalies ({linkedAnomalies.length})
            </Box>
            {linkedAnomalies.length === 0 ? (
              <Box variant="small" color="text-body-secondary">No active anomaly cohorts.</Box>
            ) : (
              <SpaceBetween size="xxs">
                {linkedAnomalies.map((a) => (
                  <Box key={a.anomalyId}>
                    <Link
                      onFollow={(e) => { e.preventDefault(); navigate(`/engineering/investigate/${a.anomalyId}`); }}
                      href="#"
                    >
                      <Box variant="small" fontWeight="bold">{a.title}</Box>
                    </Link>
                    <Box variant="small" color="text-body-secondary">
                      n={a.affectedVehicleCount} · {a.metricDeltaPercent > 0 ? '+' : ''}{a.metricDeltaPercent}% vs baseline
                    </Box>
                  </Box>
                ))}
              </SpaceBetween>
            )}
          </div>

          {/* Recent events */}
          <div>
            <Box variant="awsui-key-label">
              Recent events ({recentEvents.length}, last 7d)
            </Box>
            <SpaceBetween size="xxs">
              {recentEvents.slice(0, 4).map((ev, i) => (
                <Box key={`${ev.eventId}-${i}`}>
                  <Box>
                    <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{ev.eventId}</span>
                    {ev.occurrences > 1 && (
                      <Box display="inline" variant="small" color="text-body-secondary">
                        {' '}× {ev.occurrences}
                      </Box>
                    )}
                  </Box>
                  <Box variant="small" color="text-body-secondary">
                    {ev.severity} · {ev.firedAt}
                  </Box>
                </Box>
              ))}
            </SpaceBetween>
          </div>
        </ColumnLayout>
      </SpaceBetween>
    </Container>
  );
}

// Coverage helper — how many of the ECU's expected signals are actually emitting?
// For BMS: the new v3.3.0 signals (ThermalCompensationFactor, DerateActiveSeconds)
// only emit on firmware ≥ v3.3.0. Other ECUs have full coverage in this demo.
function getECUCoverage(ecu: VehicleECUState): { total: number; emitting: number; missing: string[] } {
  const total = ecu.signalCount;
  let emitting = total;
  const missing: string[] = [];

  if (ecu.ecu === 'BMS') {
    const hasV330 = ecu.currentVersion.startsWith('3.3.');
    if (!hasV330) {
      emitting -= 2;
      missing.push('ThermalCompensationFactor', 'DerateActiveSeconds');
    }
  }
  return { total, emitting, missing };
}
