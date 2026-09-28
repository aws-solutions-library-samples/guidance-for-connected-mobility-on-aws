// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react';
import {
  Modal,
  Box,
  SpaceBetween,
  Header,
  Button,
  Alert,
  Container,
  ColumnLayout
} from '@cloudscape-design/components';

interface SimpleSetupModalProps {
  visible: boolean;
  onDismiss: () => void;
  onRetry: () => void;
  isRetrying?: boolean;
}

export function SimpleSetupModal({ 
  visible, 
  onDismiss, 
  onRetry, 
  isRetrying = false 
}: SimpleSetupModalProps) {
  const setupCommands = [
    "cd /path/to/workspace/services/simulation",
    "./manage_simulation.sh start",
    "./manage_simulation.sh status"
  ];

  return (
    <Modal
      onDismiss={onDismiss}
      visible={visible}
      size="medium"
      header="Simulation Service Setup Required"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onDismiss}>
              Cancel
            </Button>
            <Button 
              variant="primary" 
              onClick={onRetry}
              loading={isRetrying}
              iconName="refresh"
            >
              {isRetrying ? 'Checking...' : 'Retry Connection'}
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="l">
        <Alert
          statusIconAriaLabel="Warning"
          type="warning"
          header="Simulation Service Not Available"
        >
          The Fleet Simulation API service is not reachable. Switch to Cloud mode or start the local service. 
          Please follow the setup instructions below to start the service.
        </Alert>

        <Container header={<Header variant="h2">Quick Setup</Header>}>
          <SpaceBetween size="m">
            <Box variant="p">
              Run these commands in your terminal to start the simulation service:
            </Box>
            
            {setupCommands.map((command, index) => (
              <Box key={index}>
                <Box variant="strong">{index + 1}. </Box>
                <Box 
                  variant="code" 
                  fontSize="body-s"
                  padding={{ vertical: 'xs', horizontal: 's' }}
                  backgroundColor="grey-50"
                >
                  {command}
                </Box>
              </Box>
            ))}
            
            <Alert
              statusIconAriaLabel="Info"
              type="info"
            >
              After running these commands, click "Retry Connection" to test the service.
            </Alert>
          </SpaceBetween>
        </Container>

        <Container header={<Header variant="h3">Service Features</Header>}>
          <ColumnLayout columns={2} variant="text-grid">
            <div>
              <Box variant="awsui-key-label">🚗 Fleet Simulation</Box>
              <Box variant="small">Generate realistic vehicle telemetry data</Box>
            </div>
            <div>
              <Box variant="awsui-key-label">🚨 Safety Events</Box>
              <Box variant="small">Hard braking, lane departures, speeding</Box>
            </div>
            <div>
              <Box variant="awsui-key-label">📊 Real-time Data</Box>
              <Box variant="small">Live updates every 20-30 seconds</Box>
            </div>
            <div>
              <Box variant="awsui-key-label">🎯 Testing Presets</Box>
              <Box variant="small">Pre-configured scenarios for different use cases</Box>
            </div>
          </ColumnLayout>
        </Container>
      </SpaceBetween>
    </Modal>
  );
}

export default SimpleSetupModal;
