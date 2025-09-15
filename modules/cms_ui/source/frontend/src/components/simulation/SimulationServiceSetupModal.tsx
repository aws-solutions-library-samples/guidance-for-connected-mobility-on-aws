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
  StatusIndicator,
  Link,
  ExpandableSection,
  Container,
  ColumnLayout
} from '@cloudscape-design/components';

// Simple code block component as fallback
const SimpleCodeView = ({ content, actions }: { content: string; actions?: React.ReactNode }) => (
  <Box>
    <pre style={{ 
      backgroundColor: '#f8f9fa', 
      padding: '12px', 
      borderRadius: '4px', 
      border: '1px solid #e1e4e8',
      fontSize: '14px',
      fontFamily: 'Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
      overflow: 'auto',
      margin: 0
    }}>
      <code>{content}</code>
    </pre>
    {actions && (
      <Box float="right" margin={{ top: 'xs' }}>
        {actions}
      </Box>
    )}
  </Box>
);

interface SimulationServiceSetupModalProps {
  visible: boolean;
  onDismiss: () => void;
  onRetry: () => void;
  isRetrying?: boolean;
}

export function SimulationServiceSetupModal({ 
  visible, 
  onDismiss, 
  onRetry, 
  isRetrying = false 
}: SimulationServiceSetupModalProps) {
  const [activeStep, setActiveStep] = useState<number | null>(null);

  const setupSteps = [
    {
      title: "Navigate to Simulation Service Directory",
      description: "Open terminal and navigate to the simulation service directory",
      code: "cd /Users/givenand/connected-mobility-workspace/services/simulation"
    },
    {
      title: "Start the Simulation Service",
      description: "Use the management script to start the service",
      code: "./manage_simulation.sh start"
    },
    {
      title: "Verify Service is Running",
      description: "Check that the service started successfully",
      code: "./manage_simulation.sh status"
    },
    {
      title: "Test Safety Events (Optional)",
      description: "Start a safety-focused simulation for testing",
      code: "./manage_simulation.sh test-safety"
    }
  ];

  const troubleshootingSteps = [
    {
      issue: "Permission denied when running scripts",
      solution: "Make scripts executable",
      code: "chmod +x *.sh"
    },
    {
      issue: "Port 5001 already in use",
      solution: "Check what's using the port and stop it",
      code: "lsof -i :5001\n# Kill the process if needed\nkill -9 <PID>"
    },
    {
      issue: "Python dependencies missing",
      solution: "Install required packages",
      code: "pip install flask flask-cors boto3 requests"
    },
    {
      issue: "Service starts but API doesn't respond",
      solution: "Check the service logs",
      code: "tail -f simulation_service.log"
    }
  ];

  return (
    <Modal
      onDismiss={onDismiss}
      visible={visible}
      size="large"
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
          The Fleet Simulation API service is not running on <code>localhost:5001</code>. 
          Please follow the setup instructions below to start the service.
        </Alert>

        <Container header={<Header variant="h2">Quick Setup Guide</Header>}>
          <SpaceBetween size="m">
            <Box variant="p">
              The simulation service provides realistic fleet telemetry data with safety events 
              for testing and demonstration purposes. Follow these steps to get it running:
            </Box>

            {setupSteps.map((step, index) => (
              <ExpandableSection
                key={index}
                headerText={`${index + 1}. ${step.title}`}
                headerDescription={step.description}
                expanded={activeStep === index}
                onChange={({ detail }) => setActiveStep(detail.expanded ? index : null)}
              >
                <SpaceBetween size="s">
                  <Box variant="p">{step.description}</Box>
                  <SimpleCodeView
                    content={step.code}
                    actions={
                      <Button
                        iconName="copy"
                        variant="inline-icon"
                        ariaLabel="Copy code"
                        onClick={() => navigator.clipboard?.writeText(step.code)}
                      />
                    }
                  />
                  {index === 1 && (
                    <Box variant="small" color="text-status-success">
                      ✅ Expected output: "Service started successfully!"
                    </Box>
                  )}
                  {index === 2 && (
                    <Box variant="small" color="text-status-info">
                      ℹ️ Should show: "Service is running" and "API is responding"
                    </Box>
                  )}
                </SpaceBetween>
              </ExpandableSection>
            ))}
          </SpaceBetween>
        </Container>

        <Container header={<Header variant="h2">Service Features</Header>}>
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

        <ExpandableSection
          headerText="Troubleshooting"
          headerDescription="Common issues and solutions"
        >
          <SpaceBetween size="m">
            {troubleshootingSteps.map((item, index) => (
              <Container key={index}>
                <SpaceBetween size="s">
                  <Box variant="strong">❌ {item.issue}</Box>
                  <Box variant="p">💡 {item.solution}</Box>
                  <SimpleCodeView
                    content={item.code}
                    actions={
                      <Button
                        iconName="copy"
                        variant="inline-icon"
                        ariaLabel="Copy code"
                        onClick={() => navigator.clipboard?.writeText(item.code)}
                      />
                    }
                  />
                </SpaceBetween>
              </Container>
            ))}
          </SpaceBetween>
        </ExpandableSection>

        <Container header={<Header variant="h3">Service Management Commands</Header>}>
          <SpaceBetween size="s">
            <ColumnLayout columns={2} variant="text-grid">
              <div>
                <Box variant="code">./manage_simulation.sh start</Box>
                <Box variant="small">Start the simulation service</Box>
              </div>
              <div>
                <Box variant="code">./manage_simulation.sh stop</Box>
                <Box variant="small">Stop the simulation service</Box>
              </div>
              <div>
                <Box variant="code">./manage_simulation.sh status</Box>
                <Box variant="small">Check service status</Box>
              </div>
              <div>
                <Box variant="code">./manage_simulation.sh test-safety</Box>
                <Box variant="small">Start safety events simulation</Box>
              </div>
            </ColumnLayout>
          </SpaceBetween>
        </Container>

        <Alert
          statusIconAriaLabel="Info"
          type="info"
          header="Need Help?"
        >
          <SpaceBetween size="s">
            <Box variant="p">
              For detailed documentation, check the README file in the simulation service directory:
            </Box>
            <SimpleCodeView
              content="cat /Users/givenand/connected-mobility-workspace/services/simulation/README.md"
              actions={
                <Button
                  iconName="copy"
                  variant="inline-icon"
                  ariaLabel="Copy code"
                  onClick={() => navigator.clipboard?.writeText("cat /Users/givenand/connected-mobility-workspace/services/simulation/README.md")}
                />
              }
            />
          </SpaceBetween>
        </Alert>
      </SpaceBetween>
    </Modal>
  );
}

export default SimulationServiceSetupModal;
