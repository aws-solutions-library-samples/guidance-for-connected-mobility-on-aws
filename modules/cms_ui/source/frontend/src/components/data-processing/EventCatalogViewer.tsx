import React, { useState, useEffect, useMemo } from 'react';
import { Table, Box, Header, Container, StatusIndicator, Badge, Tabs, Select, SpaceBetween, Link } from '@cloudscape-design/components';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { getRuntimeConfig } from '../../config/api';

interface Event {
  event_id: string;
  category: string;
  severity: number;
  description: string;
  trigger_signal: string;
  threshold_operator: string;
  threshold_value: number;
  dtc_code?: string;
  /** Vehicle models that can emit this event — the FleetWise tie. */
  applicableModels?: string[];
}

const severityMap: Record<number, { type: 'info' | 'warning' | 'error'; label: string }> = {
  1: { type: 'info', label: 'Low' },
  2: { type: 'warning', label: 'Medium' },
  3: { type: 'error', label: 'High' },
  4: { type: 'error', label: 'Critical' },
};

// Model display label for compact badges in the events table.
const MODEL_LABEL: Record<string, string> = {
  'CMS-FLEET-MODEL': 'CMS',
  'BE6-V12-PROD':    'BE 6',
  'BE07-V13-DEV':    'BE.07',
};
const MODEL_COLOR: Record<string, 'blue' | 'green' | 'red' | 'grey'> = {
  'CMS-FLEET-MODEL': 'blue',
  'BE6-V12-PROD':    'red',
  'BE07-V13-DEV':    'green',
};

const MODEL_FILTER_OPTIONS = [
  { value: '',                 label: 'All models' },
  { value: 'CMS-FLEET-MODEL',  label: 'CMS-FLEET-MODEL' },
  { value: 'BE6-V12-PROD',     label: 'BE6-V12-PROD' },
  { value: 'BE07-V13-DEV',     label: 'BE07-V13-DEV' },
];

const EventCatalogViewer: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [events, setEvents] = useState<Event[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('all');
  const [modelFilter, setModelFilter] = useState<string>(searchParams.get('model') ?? '');

  useEffect(() => {
    const api = getRuntimeConfig().apiEndpoint;
    fetch(`${api}api/v1/event-catalog`)
      .then(res => res.json())
      .then(data => { setEvents(data.events || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  // Sync URL → state
  useEffect(() => { setModelFilter(searchParams.get('model') ?? ''); }, [searchParams]);

  const filtered = useMemo(() => {
    let rows = events;
    if (activeTab !== 'all') rows = rows.filter(e => e.category === activeTab);
    if (modelFilter) rows = rows.filter(e => (e.applicableModels ?? []).includes(modelFilter));
    return rows;
  }, [events, activeTab, modelFilter]);

  const filterDescription = modelFilter
    ? `Filtered to events available in vehicle model ${modelFilter}.`
    : `${events.length} events across all vehicle models. Click an event row to see which models can emit it.`;

  const columns = [
    { id: 'event_id', header: 'Event ID', cell: (item: Event) => <code>{item.event_id}</code> },
    {
      id: 'category', header: 'Category',
      cell: (item: Event) => <Badge color={item.category === 'safety' ? 'red' : 'blue'}>{item.category}</Badge>,
    },
    {
      id: 'severity', header: 'Severity',
      cell: (item: Event) => {
        const s = severityMap[item.severity] || severityMap[1];
        return <StatusIndicator type={s.type}>{s.label}</StatusIndicator>;
      },
    },
    { id: 'description', header: 'Description', cell: (item: Event) => item.description },
    {
      id: 'trigger', header: 'Trigger',
      cell: (item: Event) => <code>{item.trigger_signal} {item.threshold_operator} {item.threshold_value}</code>,
    },
    { id: 'dtc', header: 'DTC Code', cell: (item: Event) => item.dtc_code || '-' },
    {
      id: 'applicableModels', header: 'Available in',
      cell: (item: Event) => {
        const models = item.applicableModels ?? [];
        if (models.length === 0) {
          return <Box variant="small" color="text-body-secondary">—</Box>;
        }
        return (
          <SpaceBetween direction="horizontal" size="xxs">
            {models.map((m) => (
              <Link
                key={m}
                onFollow={(e) => { e.preventDefault(); navigate(`/data-processing?tab=event-catalog&model=${m}`); }}
                href="#"
              >
                <Badge color={MODEL_COLOR[m] ?? 'grey'}>{MODEL_LABEL[m] ?? m}</Badge>
              </Link>
            ))}
          </SpaceBetween>
        );
      },
    },
  ];

  const headerActions = (
    <SpaceBetween direction="horizontal" size="xs" alignItems="center">
      {modelFilter && (
        <Link
          onFollow={(e) => { e.preventDefault(); navigate('/data-processing?tab=event-catalog'); }}
          href="#"
        >
          Clear model filter
        </Link>
      )}
      <Select
        selectedOption={MODEL_FILTER_OPTIONS.find(o => o.value === modelFilter) ?? MODEL_FILTER_OPTIONS[0]}
        onChange={({ detail }) => {
          const v = detail.selectedOption.value || '';
          setModelFilter(v);
          if (v) navigate(`/data-processing?tab=event-catalog&model=${v}`);
          else   navigate('/data-processing?tab=event-catalog');
        }}
        options={MODEL_FILTER_OPTIONS}
      />
    </SpaceBetween>
  );

  return (
    <Container header={
      <Header
        variant="h2"
        counter={`(${filtered.length})`}
        description={filterDescription}
        actions={headerActions}
      >
        Event Catalog
      </Header>
    }>
      <Tabs
        activeTabId={activeTab}
        onChange={({ detail }) => setActiveTab(detail.activeTabId)}
        tabs={[
          { id: 'all',         label: `All (${(modelFilter ? events.filter(e => (e.applicableModels ?? []).includes(modelFilter)) : events).length})`, content: null },
          { id: 'safety',      label: `Safety (${(modelFilter ? events.filter(e => e.category === 'safety' && (e.applicableModels ?? []).includes(modelFilter)) : events.filter(e => e.category === 'safety')).length})`, content: null },
          { id: 'maintenance', label: `Maintenance (${(modelFilter ? events.filter(e => e.category === 'maintenance' && (e.applicableModels ?? []).includes(modelFilter)) : events.filter(e => e.category === 'maintenance')).length})`, content: null },
        ].map(tab => ({
          ...tab,
          content: (
            <Table
              loading={loading}
              columnDefinitions={columns}
              items={filtered}
              empty={<Box textAlign="center" color="inherit"><b>No events found</b></Box>}
              variant="borderless"
            />
          ),
        }))}
      />
    </Container>
  );
};

export default EventCatalogViewer;
