import React, { useState, useEffect } from 'react';
import { Table, Box, Header, Container, StatusIndicator, Select, SpaceBetween } from '@cloudscape-design/components';
import { getRuntimeConfig } from '../../config/api';

interface Signal {
  signal_name: string;
  signal_group: string;
  fullyQualifiedName: string;
  jsonField?: string;
  data_type: string;
  unit: string;
  min_value: number;
  max_value: number;
  can_id?: string;
  cycle_ms?: number;
  status: string;
  signalCatalog?: string;
  modelName?: string;
  resourceType?: string;
}

const GROUPS = [
  { value: '', label: 'All Groups' },
  { value: 'core_telemetry', label: 'Core Telemetry' },
  { value: 'safety', label: 'Safety' },
  { value: 'maintenance', label: 'Maintenance' },
  { value: 'vehicle_control', label: 'Vehicle Control' },
  { value: 'ev_specific', label: 'EV Specific' },
  { value: 'connectivity', label: 'Connectivity' },
  { value: 'gps', label: 'GPS' },
];

const SignalCatalogViewer: React.FC = () => {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [group, setGroup] = useState('');

  useEffect(() => {
    setLoading(true);
    const api = getRuntimeConfig().apiEndpoint;
    const url = group
      ? `${api}api/v1/signal-catalog?group=${group}`
      : `${api}api/v1/signal-catalog`;
    fetch(url)
      .then(res => res.json())
      .then(data => { setSignals(data.signals || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, [group]);

  return (
    <Container header={
      <Header variant="h2" counter={`(${signals.length})`}
        actions={
          <Select
            selectedOption={GROUPS.find(g => g.value === group) || GROUPS[0]}
            onChange={({ detail }) => setGroup(detail.selectedOption.value || '')}
            options={GROUPS}
            placeholder="Filter by group"
          />
        }
      >Signal Catalog</Header>
    }>
      <Table
        loading={loading}
        columnDefinitions={[
          { id: 'signal_name', header: 'Signal', cell: (item: Signal) => item.signal_name, sortingField: 'signal_name' },
          { id: 'signal_group', header: 'Group', cell: (item: Signal) => item.signal_group },
          { id: 'jsonField', header: 'JSON Field', cell: (item: Signal) => <code>{item.jsonField || '-'}</code> },
          { id: 'fqn', header: 'VSS Path', cell: (item: Signal) => <code>{item.fullyQualifiedName}</code> },
          { id: 'data_type', header: 'Type', cell: (item: Signal) => item.data_type },
          { id: 'unit', header: 'Unit', cell: (item: Signal) => item.unit || '-' },
          { id: 'range', header: 'Range', cell: (item: Signal) => `${item.min_value} – ${item.max_value}` },
          { id: 'can_id', header: 'CAN ID', cell: (item: Signal) => item.can_id || 'N/A' },
          { id: 'cycle_ms', header: 'Cycle (ms)', cell: (item: Signal) => item.cycle_ms ?? 'N/A' },
          { id: 'status', header: 'Status', cell: (item: Signal) =>
            <StatusIndicator type={item.status === 'active' ? 'success' : 'stopped'}>{item.status}</StatusIndicator>
          },
        ]}
        items={signals}
        sortingDisabled={false}
        empty={<Box textAlign="center" color="inherit"><b>No signals found</b></Box>}
      />
    </Container>
  );
};

export default SignalCatalogViewer;
