import React, { useState, useEffect } from 'react';
import { Table, Box, Header, Container, StatusIndicator, Badge, Tabs } from '@cloudscape-design/components';
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
}

const severityMap: Record<number, { type: 'info' | 'warning' | 'error'; label: string }> = {
  1: { type: 'info', label: 'Low' },
  2: { type: 'warning', label: 'Medium' },
  3: { type: 'error', label: 'High' },
};

const EventCatalogViewer: React.FC = () => {
  const [events, setEvents] = useState<Event[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('all');

  useEffect(() => {
    const api = getRuntimeConfig().apiEndpoint;
    fetch(`${api}api/v1/event-catalog`)
      .then(res => res.json())
      .then(data => { setEvents(data.events || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const filtered = activeTab === 'all' ? events : events.filter(e => e.category === activeTab);

  const columns = [
    { id: 'event_id', header: 'Event ID', cell: (item: Event) => item.event_id },
    { id: 'category', header: 'Category', cell: (item: Event) => <Badge color={item.category === 'safety' ? 'red' : 'blue'}>{item.category}</Badge> },
    { id: 'severity', header: 'Severity', cell: (item: Event) => {
      const s = severityMap[item.severity] || severityMap[1];
      return <StatusIndicator type={s.type}>{s.label}</StatusIndicator>;
    }},
    { id: 'description', header: 'Description', cell: (item: Event) => item.description },
    { id: 'trigger', header: 'Trigger', cell: (item: Event) => <code>{item.trigger_signal} {item.threshold_operator} {item.threshold_value}</code> },
    { id: 'dtc', header: 'DTC Code', cell: (item: Event) => item.dtc_code || '-' },
  ];

  return (
    <Container header={<Header variant="h2" counter={`(${filtered.length})`}>Event Catalog</Header>}>
      <Tabs
        activeTabId={activeTab}
        onChange={({ detail }) => setActiveTab(detail.activeTabId)}
        tabs={[
          { id: 'all', label: `All (${events.length})`, content: null },
          { id: 'safety', label: `Safety (${events.filter(e => e.category === 'safety').length})`, content: null },
          { id: 'maintenance', label: `Maintenance (${events.filter(e => e.category === 'maintenance').length})`, content: null },
        ].map(tab => ({ ...tab, content: (
          <Table loading={loading} columnDefinitions={columns} items={filtered}
            empty={<Box textAlign="center" color="inherit"><b>No events found</b></Box>}
          />
        )}))}
      />
    </Container>
  );
};

export default EventCatalogViewer;
