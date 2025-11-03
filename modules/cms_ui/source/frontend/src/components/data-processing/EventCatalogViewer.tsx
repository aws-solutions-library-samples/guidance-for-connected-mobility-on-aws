import React, { useState, useEffect } from 'react';
import { Table, Box, SpaceBetween, Header, Container, StatusIndicator, Badge, Tabs } from '@cloudscape-design/components';

interface Event {
  event_id: string;
  category: string;
  event_name: string;
  severity: string;
  description: string;
  required_signals: string[];
  status: string;
}

const EventCatalogViewer: React.FC = () => {
  const [events, setEvents] = useState<Event[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('all');

  useEffect(() => {
    // TODO: Replace with actual API endpoint
    fetch('https://5oux6cw3ef.execute-api.us-east-1.amazonaws.com/prod/events')
      .then(res => res.json())
      .then(data => {
        setEvents(data.events || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const getSeverityType = (severity: string) => {
    switch (severity) {
      case 'critical': return 'error';
      case 'warning': return 'warning';
      case 'info': return 'info';
      default: return 'info';
    }
  };

  const filterByCategory = (category: string) => {
    if (category === 'all') return events;
    return events.filter(e => e.category === category);
  };

  const categories = ['all', 'safety', 'maintenance', 'trip', 'geofence', 'diagnostic', 'fuel_energy', 'connectivity'];

  return (
    <Container header={<Header variant="h2">Event Catalog</Header>}>
      <Tabs
        activeTabId={activeTab}
        onChange={({ detail }) => setActiveTab(detail.activeTabId)}
        tabs={categories.map(cat => ({
          id: cat,
          label: cat === 'all' ? 'All Events' : cat.replace('_', '/').split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' '),
          content: (
            <Table
              loading={loading}
              columnDefinitions={[
                { id: 'event_id', header: 'Event ID', cell: (item: Event) => item.event_id },
                { 
                  id: 'category', 
                  header: 'Category', 
                  cell: (item: Event) => (
                    <Badge color="blue">{item.category}</Badge>
                  )
                },
                { 
                  id: 'severity', 
                  header: 'Severity', 
                  cell: (item: Event) => (
                    <StatusIndicator type={getSeverityType(item.severity)}>
                      {item.severity}
                    </StatusIndicator>
                  )
                },
                { id: 'description', header: 'Description', cell: (item: Event) => item.description },
                { 
                  id: 'signals', 
                  header: 'Required Signals', 
                  cell: (item: Event) => item.required_signals?.join(', ') || '-'
                }
              ]}
              items={filterByCategory(cat)}
              empty={
                <Box textAlign="center" color="inherit">
                  <b>No events</b>
                  <Box padding={{ bottom: 's' }} variant="p" color="inherit">
                    No events found in this category.
                  </Box>
                </Box>
              }
            />
          )
        }))}
      />
    </Container>
  );
};

export default EventCatalogViewer;
