import React, { useState, useEffect, useMemo } from 'react';
import { Table, Box, Header, Container, StatusIndicator, Select, SpaceBetween, ExpandableSection, Badge } from '@cloudscape-design/components';
import { useSearchParams } from 'react-router-dom';
import { getRuntimeConfig } from '../../config/api';
import { useUserRole } from '@/auth/useUserRole';
import {
  ECU_CATALOG,
  ECU_LIST,
  getSignalECUMapping,
  getVehicleModel,
  type ECUId,
  type VehicleModel,
} from '@/mock-data-provider/engineering';

interface Signal {
  signal_name: string;
  signal_group: string;
  fullyQualifiedName?: string;
  jsonField?: string;
  json_field?: string;
  vss_path?: string;
  data_type: string;
  unit: string;
  min_value: number;
  max_value: number;
  can_id?: string;
  cycle_ms?: number;
  status: string;
  // Engineering-derived (joined client-side from signal-ecu-map.ts):
  _producingECU?: ECUId;
  _ecuMinVersion?: string;
  _isNewInLatestBuild?: boolean;
  _introducedByPipeline?: string;
  _ecuDescription?: string;
}

interface SignalTreeNode {
  name: string;
  path: string;
  children: Map<string, SignalTreeNode>;
  signals: Signal[];
}

function getVssPath(s: Signal): string {
  return s.fullyQualifiedName || s.vss_path || '';
}

function buildSignalTree(signals: Signal[]): SignalTreeNode {
  const root: SignalTreeNode = { name: 'Vehicle', path: '', children: new Map(), signals: [] };
  for (const sig of signals) {
    const fqn = getVssPath(sig);
    if (!fqn) { root.signals.push(sig); continue; }
    const parts = fqn.split('.');
    let node = root;
    for (let i = 1; i < parts.length - 1; i++) {
      const key = parts[i];
      if (!node.children.has(key)) {
        node.children.set(key, { name: key, path: parts.slice(0, i + 1).join('.'), children: new Map(), signals: [] });
      }
      node = node.children.get(key)!;
    }
    node.signals.push(sig);
  }
  return root;
}

function countSignals(node: SignalTreeNode): number {
  let count = node.signals.length;
  for (const child of node.children.values()) count += countSignals(child);
  return count;
}

// Annotates each signal with engineering metadata from the signal-ECU map.
// Falls back to the signal_group → ECU mapping for signals not in the
// explicit override list. Result: every signal with a known group is labeled.
function annotateSignalsWithECU(signals: Signal[]): Signal[] {
  return signals.map((sig) => {
    const fqn = getVssPath(sig);
    const mapping = getSignalECUMapping(fqn, sig.signal_group);
    if (!mapping) return sig;
    return {
      ...sig,
      _producingECU:          mapping.producingECU,
      _ecuMinVersion:         mapping.ecuMinVersion,
      _isNewInLatestBuild:    mapping.isNewInLatestBuild,
      _introducedByPipeline:  mapping.introducedByPipeline,
      _ecuDescription:        mapping.description,
    };
  });
}

// Operational column set (visible to all users)
const operationalColumns = [
  {
    id: 'signal_name',
    header: 'Signal',
    cell: (item: Signal) => (
      <span>
        {item.signal_name}
        {item._isNewInLatestBuild && (
          <>
            {' '}
            <Badge color="green">NEW {item._introducedByPipeline ?? ''}</Badge>
          </>
        )}
      </span>
    ),
    sortingField: 'signal_name',
  },
  { id: 'signal_group', header: 'Group',     cell: (item: Signal) => item.signal_group },
  { id: 'jsonField',    header: 'JSON Field', cell: (item: Signal) => <code>{item.jsonField || item.json_field || '-'}</code> },
  { id: 'fqn',          header: 'VSS Path',   cell: (item: Signal) => <code>{getVssPath(item) || '-'}</code> },
  { id: 'data_type',    header: 'Type',       cell: (item: Signal) => item.data_type },
  { id: 'unit',         header: 'Unit',       cell: (item: Signal) => item.unit || '-' },
  { id: 'range',        header: 'Range',      cell: (item: Signal) => `${item.min_value} – ${item.max_value}` },
  { id: 'can_id',       header: 'CAN ID',     cell: (item: Signal) => item.can_id || 'N/A' },
  { id: 'cycle_ms',     header: 'Cycle (ms)', cell: (item: Signal) => item.cycle_ms ?? 'N/A' },
  {
    id: 'status', header: 'Status',
    cell: (item: Signal) => (
      <StatusIndicator type={item.status === 'active' ? 'success' : 'stopped'}>{item.status}</StatusIndicator>
    ),
  },
];

// Engineering columns appended when isEngineer
const engineeringExtraColumns = [
  {
    id: 'producingECU',
    header: 'Producing ECU',
    cell: (item: Signal) =>
      item._producingECU
        ? <Badge color="blue">{item._producingECU}</Badge>
        : <Box variant="small" color="text-body-secondary">—</Box>,
  },
  {
    id: 'ecuMinVersion',
    header: 'Min ECU version',
    cell: (item: Signal) =>
      item._ecuMinVersion
        ? <code>≥ v{item._ecuMinVersion}</code>
        : <Box variant="small" color="text-body-secondary">—</Box>,
  },
];

const SignalBranch: React.FC<{
  node: SignalTreeNode;
  depth?: number;
  columns: typeof operationalColumns;
}> = ({ node, depth = 0, columns }) => {
  const sortedChildren = Array.from(node.children.values()).sort((a, b) => a.name.localeCompare(b.name));
  return (
    <div style={{ paddingLeft: depth > 0 ? 24 : 0 }}>
      <SpaceBetween size="xs">
        {sortedChildren.map(child => {
          const total = countSignals(child);
          return (
            <ExpandableSection key={child.path} headerText={`${child.name} (${total})`} variant={depth === 0 ? 'default' : 'footer'}>
              {child.signals.length > 0 && (
                <Table
                  columnDefinitions={columns}
                  items={child.signals.sort((a, b) => a.signal_name.localeCompare(b.signal_name))}
                  variant="embedded"
                  empty={<Box textAlign="center">No signals</Box>}
                />
              )}
              {child.children.size > 0 && <SignalBranch node={child} depth={depth + 1} columns={columns} />}
            </ExpandableSection>
          );
        })}
      </SpaceBetween>
    </div>
  );
};

const GROUPS = [
  { value: '', label: 'All Groups' },
  { value: 'adas', label: 'ADAS' },
  { value: 'cabin_climate', label: 'Cabin & Climate' },
  { value: 'connectivity', label: 'Connectivity' },
  { value: 'core_telemetry', label: 'Core Telemetry' },
  { value: 'doors', label: 'Doors' },
  { value: 'environment', label: 'Environment' },
  { value: 'ev_charging', label: 'EV Charging' },
  { value: 'ev_specific', label: 'EV Specific' },
  { value: 'geofence', label: 'Geofence' },
  { value: 'gps', label: 'GPS' },
  { value: 'lighting', label: 'Lighting' },
  { value: 'maintenance', label: 'Maintenance' },
  { value: 'mirrors', label: 'Mirrors' },
  { value: 'powertrain', label: 'Powertrain' },
  { value: 'safety', label: 'Safety' },
  { value: 'security', label: 'Security' },
  { value: 'tpms', label: 'TPMS' },
  { value: 'vehicle_control', label: 'Vehicle Control' },
  { value: 'windows', label: 'Windows' },
  { value: 'wipers', label: 'Wipers' },
];

const ECU_FILTER_OPTIONS = [
  { value: '', label: 'All ECUs' },
  ...ECU_LIST.map((e) => ({ value: e.id, label: `${e.id} — ${e.displayName}` })),
];

const SignalCatalogViewer: React.FC = () => {
  const [allSignals, setAllSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [group, setGroup] = useState('');
  const { isEngineer } = useUserRole();
  const [searchParams] = useSearchParams();

  // Read ECU + vehicle model from URL on first render. Both support deep-links:
  //   /data-processing/signals?ecu=BMS               (from vehicle detail ECU table)
  //   /data-processing/signals?model=BE6-V12-PROD    (from Vehicle Models tab)
  const initialECU = (searchParams.get('ecu') ?? '') as string;
  const initialModelId = (searchParams.get('model') ?? '') as string;
  const [ecuFilter, setEcuFilter] = useState<string>(initialECU);
  const [modelFilter, setModelFilter] = useState<string>(initialModelId);

  // Sync URL changes back to state
  useEffect(() => {
    setEcuFilter(searchParams.get('ecu') ?? '');
    setModelFilter(searchParams.get('model') ?? '');
  }, [searchParams]);

  const activeModel: VehicleModel | undefined = useMemo(
    () => (modelFilter ? getVehicleModel(modelFilter) : undefined),
    [modelFilter],
  );
  // ECU set permitted by the active vehicle model (if any).
  const modelECUSet = useMemo(
    () => (activeModel ? new Set<string>(activeModel.ecus.map((e) => e.ecu))  : null),
    [activeModel],
  );

  const annotated = useMemo(() => annotateSignalsWithECU(allSignals), [allSignals]);

  const filteredSignals = useMemo(() => {
    let rows = annotated;
    if (group) rows = rows.filter((s) => s.signal_group === group);
    if (modelECUSet) {
      // Vehicle model filter: only include signals from ECUs in this model.
      // Signals without ECU annotation pass through (unannotated VSS nodes).
      rows = rows.filter((s) => !s._producingECU || modelECUSet.has(s._producingECU));
    }
    if (isEngineer && ecuFilter) {
      rows = rows.filter((s) => s._producingECU === ecuFilter);
    }
    return rows;
  }, [annotated, group, ecuFilter, isEngineer, modelECUSet]);

  const signalTree = useMemo(() => buildSignalTree(filteredSignals), [filteredSignals]);
  const newCount = filteredSignals.filter((s) => s._isNewInLatestBuild).length;

  // Build columns based on user role
  const columns = useMemo(
    () => isEngineer ? [...operationalColumns, ...engineeringExtraColumns] : operationalColumns,
    [isEngineer]
  );

  useEffect(() => {
    setLoading(true);
    const api = getRuntimeConfig().apiEndpoint;
    fetch(`${api}api/v1/signal-catalog`)
      .then(res => res.json())
      .then(data => { setAllSignals(data.signals || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  return (
    <Container header={
      <Header
        variant="h2"
        counter={`(${filteredSignals.length})`}
        description={
          activeModel
            ? `Filtered to vehicle model ${activeModel.modelId} (${activeModel.ecus.length} domain controllers, decoder manifest ${activeModel.decoderManifestRef}).`
            : isEngineer
              ? 'Signals annotated with their producing ECU and minimum ECU firmware version. Filter by ECU or vehicle model to see what each control unit emits.'
              : undefined
        }
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            {activeModel && (
              <Badge color="blue">Model: {activeModel.modelId}</Badge>
            )}
            {isEngineer && newCount > 0 && (
              <Badge color="green">{newCount} NEW from latest build</Badge>
            )}
            {isEngineer && (
              <Select
                selectedOption={ECU_FILTER_OPTIONS.find(o => o.value === ecuFilter) || ECU_FILTER_OPTIONS[0]}
                onChange={({ detail }) => setEcuFilter(detail.selectedOption.value || '')}
                options={ECU_FILTER_OPTIONS}
                placeholder="Filter by ECU"
              />
            )}
            <Select
              selectedOption={GROUPS.find(g => g.value === group) || GROUPS[0]}
              onChange={({ detail }) => setGroup(detail.selectedOption.value || '')}
              options={GROUPS}
              placeholder="Filter by group"
            />
          </SpaceBetween>
        }
      >Signal Catalog</Header>
    }>
      {loading ? (
        <Box textAlign="center" padding="l"><StatusIndicator type="loading">Loading signals...</StatusIndicator></Box>
      ) : filteredSignals.length === 0 ? (
        <Box textAlign="center" color="inherit"><b>No signals found</b></Box>
      ) : (
        <SignalBranch node={signalTree} columns={columns} />
      )}
    </Container>
  );
};

export default SignalCatalogViewer;
