import React, { useState, useEffect } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  KeyValuePairs,
  StatusIndicator,
  Box,
  Grid,
  Button,
} from '@cloudscape-design/components';
import iotMetricsService from '../../services/iotMetricsService';

export default function DeviceStatusOverview() {
  const [statistics, setStatistics] = useState([]);
  const [connections, setConnections] = useState([]);
  const [loading, setLoading] = useState(true);

  const loadData = async () => {
    try {
      setLoading(true);
      const [stats, conns] = await Promise.all([
        iotMetricsService.getDeviceOverview(),
        iotMetricsService.listConnections()
      ]);
      setStatistics(stats);
      setConnections(conns.items || []);
    } catch (error) {
      console.error('Failed to load overview data:', error);
      // Fallback to mock data
      setStatistics([
        { metric_name: 'TotalConnections', value: 2 },
        { metric_name: 'ActiveConnections', value: 1 },
        { metric_name: 'TotalTopics', value: 1 },
        { metric_name: 'ActiveSubscriptions', value: 1 },
      ]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const connectedDevices = connections.filter(c => c.status === 'CONNECTED');
  const disconnectedDevices = connections.filter(c => c.status === 'DISCONNECTED');

  return (
    <Container
      header={
        <Header
          variant="h1"
          actions={
            <Button onClick={loadData} loading={loading}>
              Refresh
            </Button>
          }
        >
          Device Status Overview
        </Header>
      }
    >
      <SpaceBetween size="l">
        {/* High-level metrics */}
        <KeyValuePairs
          columns={4}
          items={[
            { label: 'Total Connections', value: connections.length.toLocaleString() },
            { label: 'Active Connections', value: connectedDevices.length.toLocaleString() },
            { label: 'Disconnected', value: disconnectedDevices.length.toLocaleString() },
            { label: 'Connection Rate', value: connections.length > 0 ? `${Math.round((connectedDevices.length / connections.length) * 100)}%` : '0%' },
          ]}
        />

        {/* Device status breakdown */}
        <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
          <Container header={<Header variant="h3">Connected Devices</Header>}>
            {connectedDevices.length > 0 ? (
              <SpaceBetween size="s">
                {connectedDevices.slice(0, 5).map(device => (
                  <Box key={device.client_id}>
                    <StatusIndicator type="success">
                      {device.client_id} ({device.ip_address})
                    </StatusIndicator>
                  </Box>
                ))}
                {connectedDevices.length > 5 && (
                  <Box color="text-body-secondary">
                    +{connectedDevices.length - 5} more devices
                  </Box>
                )}
              </SpaceBetween>
            ) : (
              <Box color="text-body-secondary">No connected devices</Box>
            )}
          </Container>

          <Container header={<Header variant="h3">Recent Disconnections</Header>}>
            {disconnectedDevices.length > 0 ? (
              <SpaceBetween size="s">
                {disconnectedDevices.slice(0, 5).map(device => (
                  <Box key={device.client_id}>
                    <StatusIndicator type="stopped">
                      {device.client_id} ({device.ip_address})
                    </StatusIndicator>
                  </Box>
                ))}
                {disconnectedDevices.length > 5 && (
                  <Box color="text-body-secondary">
                    +{disconnectedDevices.length - 5} more devices
                  </Box>
                )}
              </SpaceBetween>
            ) : (
              <Box color="text-body-secondary">No disconnected devices</Box>
            )}
          </Container>
        </Grid>
      </SpaceBetween>
    </Container>
  );
}
