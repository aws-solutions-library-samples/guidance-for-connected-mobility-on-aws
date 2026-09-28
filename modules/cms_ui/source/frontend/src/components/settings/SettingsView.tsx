// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useContext, useState } from "react";
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  FormField,
  Toggle,
  Flashbar,
  StatusIndicator,
  ColumnLayout,
} from "@cloudscape-design/components";
import { UserContext } from "../commons/UserContext";
import { Mode } from "@cloudscape-design/global-styles";
import { getSimulationMode, setSimulationMode, isCloudSimAvailable, SimulationMode } from "../../utils/simulation-config";
import { useAuth } from "../../auth/useAuth";

const SettingsView: React.FC = () => {
  const uc = useContext(UserContext);
  const auth = useAuth();
  const isDarkMode = uc.theme.currentThemeMode === Mode.Dark;
  const [flashbarItems, setFlashbarItems] = useState<any[]>([]);
  const [simMode, setSimMode] = useState<SimulationMode>(getSimulationMode());
  const cloudAvailable = isCloudSimAvailable();

  return (
    <SpaceBetween size="l">
      {flashbarItems.length > 0 && <Flashbar items={flashbarItems} />}

      {/* Appearance */}
      <Container header={<Header variant="h2" description="Customize your user interface">Appearance</Header>}>
        <FormField label="Theme">
          <Toggle onChange={() => uc.theme.switchThemeMode()} checked={isDarkMode}>
            Dark mode
          </Toggle>
        </FormField>
      </Container>

      {/* Simulation */}
      <Container header={<Header variant="h2" description="Configure how simulations are executed">Simulation</Header>}>
        <SpaceBetween size="m">
          <FormField
            label="Simulator mode"
            description="Local mode connects to a simulator running on your machine (localhost:5001). Cloud mode uses the ECS Fargate service deployed in your AWS account."
          >
            <Toggle
              checked={simMode === 'cloud'}
              disabled={!cloudAvailable && simMode === 'local'}
              onChange={({ detail }) => {
                const mode = detail.checked ? 'cloud' : 'local';
                setSimulationMode(mode);
                setSimMode(mode);
                setFlashbarItems([{
                  type: "success",
                  content: `Switched to ${mode === 'cloud' ? 'Cloud (ECS Fargate)' : 'Local (localhost:5001)'} simulator.`,
                  dismissible: true,
                  onDismiss: () => setFlashbarItems([]),
                }]);
              }}
            >
              Cloud simulator
            </Toggle>
          </FormField>
          <ColumnLayout columns={2} variant="text-grid">
            <div>
              <Box variant="awsui-key-label">Current mode</Box>
              <StatusIndicator type={simMode === 'cloud' ? 'success' : 'info'}>
                {simMode === 'cloud' ? 'Cloud (ECS Fargate)' : 'Local (localhost:5001)'}
              </StatusIndicator>
            </div>
            <div>
              <Box variant="awsui-key-label">Cloud endpoint</Box>
              <Box variant="p">{cloudAvailable ? 'Configured' : 'Not configured — deploy the simulation stack and add simulationApiEndpoint to runtimeConfig.json'}</Box>
            </div>
          </ColumnLayout>
        </SpaceBetween>
      </Container>

      {/* Account */}
      <Container header={<Header variant="h2" description="Your account information">Account</Header>}>
        <ColumnLayout columns={2} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Email</Box>
            <Box variant="p">{auth.user?.email || auth.user?.username || '—'}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Region</Box>
            <Box variant="p">{(window as any).runtimeConfig?.awsRegion || '—'}</Box>
          </div>
        </ColumnLayout>
      </Container>
    </SpaceBetween>
  );
};

export default SettingsView;
