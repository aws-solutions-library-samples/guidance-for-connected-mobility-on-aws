import React, { useState, useEffect } from 'react';
import { Table, Box, SpaceBetween, Header, Container, StatusIndicator } from '@cloudscape-design/components';

interface Signal {
  signal_id: string;
  signal_name: string;
  signal_group: string;
  data_type: string;
  unit: string;
  description: string;
  status: string;
}

const SignalCatalogViewer: React.FC = () => {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('https://5oux6cw3ef.execute-api.us-east-1.amazonaws.com/prod/signals')
      .then(res => res.json())
      .then(data => {
        setSignals(data.signals || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  return (
    <Container header={<Header variant="h2">Signal Catalog</Header>}>
      <Table
        loading={loading}
        columnDefinitions={[
          { id: 'signal_name', header: 'Signal Name', cell: (item: Signal) => item.signal_name },
          { id: 'signal_group', header: 'Group', cell: (item: Signal) => item.signal_group },
          { id: 'data_type', header: 'Type', cell: (item: Signal) => item.data_type },
          { id: 'unit', header: 'Unit', cell: (item: Signal) => item.unit || '-' },
          { id: 'status', header: 'Status', cell: (item: Signal) => 
            <StatusIndicator type={item.status === 'active' ? 'success' : 'stopped'}>
              {item.status}
            </StatusIndicator>
          },
          { id: 'description', header: 'Description', cell: (item: Signal) => item.description }
        ]}
        items={signals}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No signals</b>
            <Box padding={{ bottom: 's' }} variant="p" color="inherit">
              No signals found in catalog.
            </Box>
          </Box>
        }
      />
    </Container>
  );
};

export default SignalCatalogViewer;
