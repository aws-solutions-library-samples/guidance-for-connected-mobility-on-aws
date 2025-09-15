// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from 'react';
import { getRuntimeConfig } from '../../../config/api';
import {
  Container,
  Header,
  SpaceBetween,
  Button,
  Form,
  FormField,
  Input,
  Select,
  Slider,
  Toggle,
  Alert,
  StatusIndicator,
  Badge,
  Box,
  ColumnLayout,
  Cards,
  Modal,
  Textarea,
  ProgressBar
} from '@cloudscape-design/components';
// Try to import the full modal, fallback to simple version if needed
import SimulationServiceSetupModal from './SimulationServiceSetupModal';
import VehicleSelectionModal from './VehicleSelectionModal';
// import SimpleSetupModal from './SimpleSetupModal'; // Uncomment if needed

interface Vehicle {
  vehicleId: string;
  vin: string;
  make: string;
  model: string;
  year: number;
  name: string;
  has_certificate: boolean;
  certificate_ready: boolean;
  status: string;
  fleetId?: string;
  license_plate?: string;
  fuel_type?: string;
  vehicle_type?: string;
  mileage?: number;
}

interface SimulationConfig {
  trips: number;
  vehicles: number;
  city: string;
  safety_rate: number;
  fleet_prefix: string;
  cleanup: boolean;
  vehicle_source: 'generated' | 'real_vehicles';
  selected_vehicles?: Vehicle[];
  use_real_vehicles?: boolean;
  use_certificates?: boolean;
  certificate_only?: boolean;
  aws_region?: string;
  force_maintenance_alert?: boolean;
}

interface SimulationStatus {
  id: string;
  status: 'running' | 'completed' | 'failed' | 'stopped';
  config: SimulationConfig;
  start_time: string;
  end_time?: string;
  logs?: Array<{ timestamp: string; message: string }>; // API uses 'logs' field
  output?: Array<{ timestamp: string; message: string }>; // Fallback for backward compatibility
  error?: string;
  // Additional fields from API
  trips_per_vehicle?: number;
  trips_completed?: number;
  total_trips?: number;
  ignition_events_sent?: number;
  iot_messages_sent?: number;
  protocol?: string;
  safety_events_generated?: number;
  safety_rate?: number;
  telemetry_messages_sent?: number;
  trips_generated?: number;
  vehicle_details?: any[];
  vehicle_source?: string;
  vehicle_trip_schedules?: any[];
  vehicles?: any[];
}

interface SimulationPreset {
  id: string;
  name: string;
  description: string;
  config: SimulationConfig;
}

const SIMULATION_API_BASE = 'http://localhost:5001/api/simulation';

export default function FleetSimulationPanel() {
  const [config, setConfig] = useState<SimulationConfig>({
    trips: 3,
    vehicles: 10,
    city: 'nyc',
    safety_rate: 0.15,
    fleet_prefix: 'SIM',
    cleanup: true,
    vehicle_source: 'real_vehicles',
    aws_region: 'us-east-1',
    force_maintenance_alert: false
  });

  const [activeSimulations, setActiveSimulations] = useState<SimulationStatus[]>([]);
  const [availableVehicles, setAvailableVehicles] = useState<{
    real_vehicles: number;
  }>({ real_vehicles: 0 });
  const [availableVehiclesList, setAvailableVehiclesList] = useState<Vehicle[]>([]);
  const [selectedVehicles, setSelectedVehicles] = useState<Vehicle[]>([]);
  const [presets, setPresets] = useState<SimulationPreset[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [showOutput, setShowOutput] = useState<string | null>(null);
  const [streamingLogs, setStreamingLogs] = useState<{[key: string]: Array<{ timestamp: string; message: string }>}>({});
  const [eventSources, setEventSources] = useState<{[key: string]: EventSource}>({});
  const [serviceAvailable, setServiceAvailable] = useState<boolean | null>(null);
  const [showSetupModal, setShowSetupModal] = useState(false);
  const [showVehicleSelectionModal, setShowVehicleSelectionModal] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);

  // Fetch presets on component mount
  useEffect(() => {
    checkServiceAvailability();
    
    // Poll for simulation updates every 5 seconds
    const interval = setInterval(() => {
      if (serviceAvailable) {
        fetchSimulations();
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [serviceAvailable]);

  const checkServiceAvailability = async () => {
    try {
      const response = await fetch(`${SIMULATION_API_BASE}/presets`, {
        method: 'GET',
        signal: AbortSignal.timeout(5000) // 5 second timeout
      });
      
      if (response.ok) {
        setServiceAvailable(true);
        setError(null);
        // If service is available, fetch initial data
        fetchPresets();
        fetchSimulations();
        fetchAvailableVehicles();
      } else {
        setServiceAvailable(false);
        setShowSetupModal(true);
      }
    } catch (error) {
      console.error('Service availability check failed:', error);
      setServiceAvailable(false);
      setShowSetupModal(true);
      setError('Simulation service is not available. Please start the service to use simulations.');
    }
  };

  const retryConnection = async () => {
    setIsRetrying(true);
    await checkServiceAvailability();
    setIsRetrying(false);
    
    if (serviceAvailable) {
      setShowSetupModal(false);
    }
  };

  const fetchPresets = async () => {
    if (!serviceAvailable) return;
    
    try {
      const response = await fetch(`${SIMULATION_API_BASE}/presets`);
      const data = await response.json();
      setPresets(data.presets || []);
    } catch (error) {
      console.error('Failed to fetch presets:', error);
      setServiceAvailable(false);
      setShowSetupModal(true);
    }
  };

  const fetchSimulations = async () => {
    if (!serviceAvailable) return;
    
    try {
      const response = await fetch(`${SIMULATION_API_BASE}/list`);
      const data = await response.json();
      setActiveSimulations(data.simulations || []);
    } catch (error) {
      console.error('Failed to fetch simulations:', error);
      setServiceAvailable(false);
      setShowSetupModal(true);
    }
  };

  const fetchAvailableVehicles = React.useCallback(async (searchTerm?: string, page: number = 1, limit: number = 20) => {
    if (!serviceAvailable) return { vehicles: [], totalCount: 0 };
    
    try {
      // Get API endpoint from runtime config
      const runtimeConfig = (window as any).runtimeConfig;
      const apiEndpoint = runtimeConfig?.apiEndpoint || 'getApiEndpoint()/';
      
      // Build query parameters
      const params = new URLSearchParams({
        has_certificate: 'true',
        limit: limit.toString(),
        page: page.toString()
      });
      
      // Add search parameter if provided
      if (searchTerm && searchTerm.trim()) {
        params.append('search', searchTerm.trim());
      }
      
      // Fetch vehicles with certificates from the API (server-side filtering)
      const vehiclesResponse = await fetch(`${apiEndpoint}api/v1/vehicles?${params.toString()}`);
      
      if (!vehiclesResponse.ok) {
        throw new Error(`Vehicles API returned ${vehiclesResponse.status}: ${vehiclesResponse.statusText}`);
      }
      
      const vehiclesData = await vehiclesResponse.json();
      const vehiclesWithCertificates = vehiclesData.vehicles || vehiclesData.items || [];
      
      // Transform vehicles to match the expected format for simulation
      const transformedVehicles = vehiclesWithCertificates.map((vehicle: any) => ({
        vehicleId: vehicle.vehicleId || vehicle.vin,
        vin: vehicle.vin,
        make: vehicle.make || vehicle.attributes?.make,
        model: vehicle.model || vehicle.attributes?.model,
        year: vehicle.year || vehicle.attributes?.year,
        name: vehicle.name || `${vehicle.make} ${vehicle.model} (${vehicle.vin})`,
        has_certificate: true,
        certificate_ready: true,
        status: vehicle.status || 'ACTIVE',
        fleetId: vehicle.fleetId,
        license_plate: vehicle.licensePlate || vehicle.license_plate,
        fuel_type: vehicle.fuelType || vehicle.fuel_type,
        vehicle_type: vehicle.type || vehicle.vehicle_type,
        mileage: vehicle.mileage
      }));
      
      // Only update the full list if this is the first page and no search
      if (page === 1 && !searchTerm) {
        setAvailableVehicles({
          real_vehicles: vehiclesData.totalCount || transformedVehicles.length
        });
        setAvailableVehiclesList(transformedVehicles);
      }
      
      return {
        vehicles: transformedVehicles,
        totalCount: vehiclesData.totalCount || transformedVehicles.length
      };
      
    } catch (error) {
      console.error('Failed to fetch available vehicles:', error);
      setError(`Failed to fetch vehicles from API: ${error.message}`);
      return { vehicles: [], totalCount: 0 };
    }
  }, [serviceAvailable]);

  const startLogStreaming = (simulationId: string) => {
    // Close existing stream if any
    if (eventSources[simulationId]) {
      eventSources[simulationId].close();
    }

    const eventSource = new EventSource(`http://localhost:5001/api/simulation/logs/${simulationId}/stream`);
    
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.log) {
          setStreamingLogs(prev => ({
            ...prev,
            [simulationId]: [...(prev[simulationId] || []), data.log]
          }));
        } else if (data.status === 'completed') {
          eventSource.close();
          setEventSources(prev => {
            const newSources = { ...prev };
            delete newSources[simulationId];
            return newSources;
          });
        }
      } catch (error) {
        console.error('Error parsing streaming log data:', error);
      }
    };

    eventSource.onerror = (error) => {
      console.error('EventSource error:', error);
      eventSource.close();
    };

    setEventSources(prev => ({
      ...prev,
      [simulationId]: eventSource
    }));
  };

  const stopLogStreaming = (simulationId: string) => {
    if (eventSources[simulationId]) {
      eventSources[simulationId].close();
      setEventSources(prev => {
        const newSources = { ...prev };
        delete newSources[simulationId];
        return newSources;
      });
    }
  };

  // Cleanup event sources on unmount
  useEffect(() => {
    return () => {
      Object.values(eventSources).forEach(source => source.close());
    };
  }, []);

  const fetchSimulationLogs = async (simulationId: string) => {
    try {
      const response = await fetch(`${SIMULATION_API_BASE}/status/${simulationId}`);
      if (response.ok) {
        const data = await response.json();
        // Update the simulation in the list with fresh logs
        setActiveSimulations(prev => 
          prev.map(sim => 
            sim.id === simulationId 
              ? { ...sim, logs: data.logs || data.output || [] }
              : sim
          )
        );
      }
    } catch (error) {
      console.error('Failed to fetch simulation logs:', error);
    }
  };

  const [accountValidation, setAccountValidation] = useState<{
    uiAccount?: string;
    simulationAccount?: string;
    isValid?: boolean;
  }>({});

  const startSimulation = async () => {
    if (!serviceAvailable) {
      setShowSetupModal(true);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      // Get UI account from runtime config
      const uiAccount = (window as any).runtimeConfig?.awsCredentials?.account;
      
      // Prepare simulation config with UI account for validation
      const simulationConfig = {
        trips: config.trips,
        safety_event_probability: config.safety_rate,
        aws_region: config.aws_region,
        ui_account: uiAccount, // Pass UI account for validation
        certificates_table_name: 'cms-631ca2-591631-vehicle-certificates', // Pass as env var
        ...config
      };

      // Add vehicle source specific options
      if (config.vehicle_source === 'real_vehicles') {
        // Use selected vehicles for real vehicle simulations
        simulationConfig.vehicles = selectedVehicles;
        simulationConfig.use_real_vehicles = true;
      } else {
        // Use vehicle count for generated vehicles
        simulationConfig.vehicles = config.vehicles;
      }

      const response = await fetch(`${SIMULATION_API_BASE}/start`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(simulationConfig),
      });

      const data = await response.json();

      if (response.ok) {
        // Show success message
        setSuccessMessage(`Simulation started successfully! ID: ${data.simulation_id}`);
        // Clear success message after 5 seconds
        setTimeout(() => setSuccessMessage(null), 5000);
        // Refresh simulations list
        fetchSimulations();
      } else {
        setError(data.error || 'Failed to start simulation');
      }
    } catch (error) {
      console.error('Network error:', error);
      setError('Network error: Unable to start simulation');
      setServiceAvailable(false);
      setShowSetupModal(true);
    } finally {
      setLoading(false);
    }
  };

  const stopSimulation = async (simulationId: string) => {
    try {
      const response = await fetch(`${SIMULATION_API_BASE}/stop/${simulationId}`, {
        method: 'POST',
      });

      if (response.ok) {
        fetchSimulations();
      }
    } catch (error) {
      console.error('Failed to stop simulation:', error);
    }
  };

  const loadPreset = (preset: SimulationPreset) => {
    setConfig(preset.config);
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'running': return 'blue';
      case 'completed': return 'green';
      case 'failed': return 'red';
      case 'stopped': return 'grey';
      default: return 'grey';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'running': return 'loading';
      case 'completed': return 'success';
      case 'failed': return 'error';
      case 'stopped': return 'stopped';
      default: return 'pending';
    }
  };

  const formatDuration = (startTime: string, endTime?: string) => {
    const start = new Date(startTime);
    const end = endTime ? new Date(endTime) : new Date();
    const duration = Math.round((end.getTime() - start.getTime()) / 1000 / 60);
    return `${duration} min`;
  };

  return (
    <SpaceBetween size="l">
      {/* Welcome Section */}
      <Container>
        <SpaceBetween size="m">
          <Box variant="h2">Welcome to Fleet Simulation</Box>
          <Box variant="p">
            Generate realistic fleet telemetry data with safety events to test and demonstrate your fleet management system. 
            Configure simulations with custom parameters or use preset configurations for common scenarios.
          </Box>
          <ColumnLayout columns={3} variant="text-grid">
            <div>
              <Box variant="awsui-key-label">🚗 Realistic Data</Box>
              <Box variant="small">70+ telemetry fields following real-world patterns</Box>
            </div>
            <div>
              <Box variant="awsui-key-label">⚠️ Safety Events</Box>
              <Box variant="small">Hard braking, lane departures, speeding violations</Box>
            </div>
            <div>
              <Box variant="awsui-key-label">🗺️ Real Routes</Box>
              <Box variant="small">Vehicles follow actual city roads and speed limits</Box>
            </div>
          </ColumnLayout>
        </SpaceBetween>
      </Container>

      {/* Simulation Configuration */}
      <Container
        header={
          <Header
            variant="h2"
            description="Configure and start fleet simulations to generate realistic telemetry data with safety events"
            actions={
              <Button
                variant="primary"
                loading={loading}
                onClick={startSimulation}
                disabled={
                  loading || 
                  !serviceAvailable || 
                  selectedVehicles.length === 0
                }
                iconName="play"
              >
                Start Simulation
              </Button>
            }
          >
            Fleet Simulation Control
          </Header>
        }
      >
        <Form>
          <SpaceBetween size="m">
            {/* Service Status Indicator */}
            {serviceAvailable === false && (
              <Alert
                statusIconAriaLabel="Warning"
                type="warning"
                header="Simulation Service Unavailable"
                action={
                  <Button 
                    onClick={() => setShowSetupModal(true)}
                    iconName="settings"
                  >
                    Setup Instructions
                  </Button>
                }
              >
                The simulation service is not running. Click "Setup Instructions" to learn how to start it.
              </Alert>
            )}
            
            {serviceAvailable === true && (
              <Alert
                statusIconAriaLabel="Success"
                type="success"
                header="Simulation Service Connected"
                dismissible
              >
                Successfully connected to simulation service at <code>localhost:5001</code>
              </Alert>
            )}

            {successMessage && (
              <Alert
                statusIconAriaLabel="Success"
                type="success"
                header="Simulation Started"
                dismissible
                onDismiss={() => setSuccessMessage(null)}
              >
                {successMessage}
              </Alert>
            )}

            {error && (
              <Alert
                statusIconAriaLabel="Error"
                type="error"
                header="Simulation Error"
                dismissible
                onDismiss={() => setError(null)}
              >
                {error}
              </Alert>
            )}

            {/* Vehicle Source Selection - Disabled */}
            <FormField 
              label="Vehicle Source" 
              description="Using real vehicles from DynamoDB (generated vehicles feature coming soon)"
            >
              <Select
                selectedOption={{
                  label: 'Real Vehicles (From DynamoDB)',
                  value: 'real_vehicles'
                }}
                onChange={() => {}} // Disabled
                disabled={true}
                options={[
                  { 
                    label: `Real Vehicles (From DynamoDB) - ${availableVehicles.real_vehicles} available`, 
                    value: 'real_vehicles',
                    description: 'Use real vehicles from DynamoDB with automatic certificate creation'
                  }
                ]}
              />
            </FormField>

            {/* Vehicle Selection - Always enabled */}
            {availableVehiclesList.length > 0 && (
              <FormField
                label="Select Vehicles"
                description={`Choose which vehicles to include in the simulation (${selectedVehicles.length} of ${availableVehiclesList.length} selected)`}
              >
                <SpaceBetween size="s">
                  <Button
                    variant="normal"
                    onClick={() => setShowVehicleSelectionModal(true)}
                    iconName="search"
                  >
                    {selectedVehicles.length === 0 
                      ? `Select Vehicles (${availableVehiclesList.length} available)`
                      : `Modify Selection (${selectedVehicles.length} selected)`
                    }
                  </Button>
                  
                  {selectedVehicles.length > 0 && (
                    <Box>
                      <Box variant="awsui-key-label">Selected Vehicles:</Box>
                      <SpaceBetween size="xs">
                        {selectedVehicles.slice(0, 3).map((vehicle, index) => (
                          <Box key={vehicle.vehicleId} variant="small">
                            • {vehicle.name} ({vehicle.vin})
                          </Box>
                        ))}
                        {selectedVehicles.length > 3 && (
                          <Box variant="small" color="text-body-secondary">
                            ... and {selectedVehicles.length - 3} more vehicles
                          </Box>
                        )}
                      </SpaceBetween>
                    </Box>
                  )}
                </SpaceBetween>
              </FormField>
            )}

            {/* Simulation Parameters */}
            <ColumnLayout columns={2}>
              <FormField label="Number of Trips" description="How many trips each vehicle should complete">
                <Slider
                  value={config.trips}
                  onChange={({ detail }) => setConfig({ ...config, trips: detail.value })}
                  min={1}
                  max={10}
                  step={1}
                  tickMarks
                  hideFillLine={false}
                />
              </FormField>

              <FormField 
                label="Number of Vehicles" 
                description={`Vehicles determined by selection above (${selectedVehicles.length} selected)`}
              >
                <Slider
                  value={selectedVehicles.length}
                  onChange={() => {}} // Disabled - controlled by vehicle selection
                  disabled={true}
                  min={1}
                  max={Math.max(selectedVehicles.length || 1, 50)}
                  step={1}
                  tickMarks
                  hideFillLine={true}
                />
              </FormField>
            </ColumnLayout>

            <ColumnLayout columns={3}>
              <FormField label="City" description="City for route generation">
                <Select
                  selectedOption={{ label: config.city, value: config.city }}
                  onChange={({ detail }) => setConfig({ ...config, city: detail.selectedOption.value! })}
                  options={[
                    { label: 'New York City', value: 'nyc' },
                    { label: 'San Francisco', value: 'sf' },
                    { label: 'Chicago', value: 'chicago' },
                    { label: 'Miami', value: 'miami' },
                    { label: 'Seattle', value: 'seattle' },
                    { label: 'Munich', value: 'munich' },
                    { label: 'Atlanta', value: 'atlanta' }
                  ]}
                />
              </FormField>

              <FormField label="Safety Event Rate" description="Probability of safety events (0-100%)">
                <Slider
                  value={config.safety_rate * 100}
                  onChange={({ detail }) => setConfig({ ...config, safety_rate: detail.value / 100 })}
                  min={0}
                  max={100}
                  step={1}
                  tickMarks
                  hideFillLine={false}
                />
              </FormField>

              <FormField label="Force Maintenance Alerts" description="Generate maintenance alert for each trip">
                <Toggle
                  checked={config.force_maintenance_alert || false}
                  onChange={({ detail }) => setConfig({ ...config, force_maintenance_alert: detail.checked })}
                >
                  Force maintenance alert per trip
                </Toggle>
              </FormField>
            </ColumnLayout>


          </SpaceBetween>
        </Form>
      </Container>

      {/* Preset Configurations */}
      <Container
        header={
          <Header variant="h3" description="Quick-start configurations for common scenarios">
            Simulation Presets
          </Header>
        }
      >
        <Cards
          cardDefinition={{
            header: item => item.name,
            sections: [
              {
                content: item => (
                  <SpaceBetween size="xs">
                    <Box variant="small">{item.description}</Box>
                    <ColumnLayout columns={2} variant="text-grid">
                      <div>
                        <Box variant="awsui-key-label">Trips per Vehicle</Box>
                        <div>{item.config.trips}</div>
                      </div>
                      <div>
                        <Box variant="awsui-key-label">Vehicles</Box>
                        <div>{item.config.vehicles}</div>
                      </div>
                      <div>
                        <Box variant="awsui-key-label">Safety Rate</Box>
                        <div>{(item.config.safety_rate * 100).toFixed(0)}%</div>
                      </div>
                      <div>
                        <Box variant="awsui-key-label">City</Box>
                        <div>{item.config.city}</div>
                      </div>
                    </ColumnLayout>
                    <Button
                      size="small"
                      onClick={() => loadPreset(item)}
                      iconName="upload"
                    >
                      Load Configuration
                    </Button>
                  </SpaceBetween>
                )
              }
            ]
          }}
          cardsPerRow={[
            { cards: 1 },
            { minWidth: 500, cards: 2 },
            { minWidth: 800, cards: 4 }
          ]}
          items={presets}
          loadingText="Loading presets..."
          empty={
            <Box textAlign="center" color="inherit">
              <b>No presets available</b>
            </Box>
          }
        />
      </Container>

      {/* Active Simulations */}
      <Container
        header={
          <Header
            variant="h3"
            description="Monitor and control running simulations"
            actions={
              <Button iconName="refresh" onClick={fetchSimulations}>
                Refresh
              </Button>
            }
          >
            Active Simulations
          </Header>
        }
      >
        <Cards
          cardDefinition={{
            header: item => (
              <SpaceBetween direction="horizontal" size="xs">
                <Box variant="h4">{item.config.fleet_prefix} Simulation</Box>
                <Badge color={getStatusColor(item.status)}>
                  {item.status.toUpperCase()}
                </Badge>
              </SpaceBetween>
            ),
            sections: [
              {
                content: item => (
                  <SpaceBetween size="s">
                    <ColumnLayout columns={3} variant="text-grid">
                      <div>
                        <Box variant="awsui-key-label">Trip Progress</Box>
                        <div>
                          {item.trips_completed || 0} / {
                            item.total_trips || 
                            ((item.config?.trips || 3) * (item.config?.vehicles || 10))
                          } trips
                        </div>
                      </div>
                      <div>
                        <Box variant="awsui-key-label">Vehicles</Box>
                        <div>{Array.isArray(item.config.vehicles) ? item.config.vehicles.length : item.config.vehicles}</div>
                      </div>
                      <div>
                        <Box variant="awsui-key-label">Safety Rate</Box>
                        <div>{(item.config.safety_rate * 100).toFixed(0)}%</div>
                      </div>
                    </ColumnLayout>

                    {item.status === 'running' && (
                      <ProgressBar
                        value={
                          item.total_trips && item.trips_completed 
                            ? Math.round((item.trips_completed / item.total_trips) * 100)
                            : 0
                        }
                        additionalInfo={`${item.trips_completed || 0} / ${
                          item.total_trips || 
                          ((item.config?.trips || 3) * (item.config?.vehicles || 10))
                        } trips completed`}
                        description="Trip completion progress"
                      />
                    )}

                    <SpaceBetween direction="horizontal" size="xs">
                      {item.status === 'running' && (
                        <Button
                          size="small"
                          onClick={() => stopSimulation(item.id)}
                          iconName="close"
                        >
                          Stop
                        </Button>
                      )}
                      <Button
                        size="small"
                        onClick={() => {
                          setShowOutput(item.id);
                          startLogStreaming(item.id);
                        }}
                        iconName="view-horizontal"
                      >
                        View Output
                      </Button>
                    </SpaceBetween>

                    {item.error && (
                      <Alert type="error" statusIconAriaLabel="Error">
                        {item.error}
                      </Alert>
                    )}
                  </SpaceBetween>
                )
              }
            ]
          }}
          cardsPerRow={[
            { cards: 1 },
            { minWidth: 600, cards: 2 }
          ]}
          items={activeSimulations}
          loadingText="Loading simulations..."
          empty={
            <Box textAlign="center" color="inherit">
              <SpaceBetween size="m">
                <b>No active simulations</b>
                <Box variant="p" color="inherit">
                  Start a simulation to generate fleet telemetry data with safety events
                </Box>
              </SpaceBetween>
            </Box>
          }
        />
      </Container>

      {/* Output Modal */}
      {showOutput && (
        <Modal
          onDismiss={() => {
            if (showOutput) stopLogStreaming(showOutput);
            setShowOutput(null);
          }}
          visible={true}
          size="large"
          header={
            <Header
              variant="h3"
              actions={
                <Box>
                  {eventSources[showOutput] ? (
                    <Button
                      iconName="status-positive"
                      variant="primary"
                      onClick={() => showOutput && stopLogStreaming(showOutput)}
                    >
                      Streaming Live - Click to Stop
                    </Button>
                  ) : (
                    <Button
                      iconName="refresh"
                      onClick={() => showOutput && startLogStreaming(showOutput)}
                    >
                      Start Live Stream
                    </Button>
                  )}
                </Box>
              }
            >
              Simulation Output - {showOutput}
            </Header>
          }
        >
          <SpaceBetween size="m">
            {(() => {
              const simulation = activeSimulations.find(s => s.id === showOutput);
              if (!simulation) {
                return <Box>Simulation not found</Box>;
              }

              let logContent = 'No output available for this simulation.';
              
              // Use streaming logs first, then fall back to static logs
              const logs = streamingLogs[showOutput] || simulation.logs || simulation.output || [];
              
              if (logs.length > 0) {
                logContent = logs
                  .map(log => `[${new Date(log.timestamp).toLocaleTimeString()}] ${log.message}`)
                  .join('\n');
              } else if (simulation.status === 'running') {
                if (eventSources[showOutput]) {
                  logContent = '🔴 Live streaming... Logs will appear here in real-time as they are generated.';
                } else {
                  logContent = 'Simulation is running... Click "Start Live Stream" to see logs in real-time.';
                }
              }

              return (
                <Textarea
                  value={logContent}
                  rows={20}
                  readOnly
                  placeholder="Simulation logs will appear here..."
                />
              );
            })()}
          </SpaceBetween>
        </Modal>
      )}

      {/* Simulation Service Setup Modal */}
      <SimulationServiceSetupModal
        visible={showSetupModal}
        onDismiss={() => setShowSetupModal(false)}
        onRetry={retryConnection}
        isRetrying={isRetrying}
      />

      {/* Vehicle Selection Modal */}
      <VehicleSelectionModal
        visible={showVehicleSelectionModal}
        onDismiss={() => setShowVehicleSelectionModal(false)}
        onConfirm={(selectedVehicles) => {
          setSelectedVehicles(selectedVehicles);
          setConfig({ ...config, vehicles: selectedVehicles.length });
        }}
        availableVehicles={availableVehiclesList}
        currentSelection={selectedVehicles}
        onSearch={fetchAvailableVehicles}
      />
    </SpaceBetween>
  );
}
