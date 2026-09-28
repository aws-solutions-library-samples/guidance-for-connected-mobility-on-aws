import React, { useState } from 'react';
import { getDataProcessingApiEndpoint } from '../../config/api';
import { authFetch } from '../../utils/authFetch';
import { 
  Wizard,
  Box, 
  SpaceBetween, 
  FormField, 
  Input, 
  Select, 
  Textarea,
  Button,
  Table,
  StatusIndicator,
  Alert,
  Link,
  Popover,
  Container,
  Header
} from '@cloudscape-design/components';

interface OEMIntegrationWizardProps {
  visible: boolean;
  onDismiss: () => void;
  onComplete: () => void;
}

interface SignalMapping {
  oem_signal?: string;
  cms_field: string;
  oem_field: string;
  transform: string;
  status: 'mapped' | 'unmapped';
}

const OEMIntegrationWizard: React.FC<OEMIntegrationWizardProps> = ({ visible, onDismiss, onComplete }) => {
  const [step, setStep] = useState(1);
  const [oemName, setOemName] = useState('');
  const [connectionType, setConnectionType] = useState({ label: 'REST API (Polling)', value: 'rest_polling' });
  const [encodingType, setEncodingType] = useState({ label: 'JSON', value: 'json' });
  const [schemaContent, setSchemaContent] = useState('');
  const [apiEndpoint, setApiEndpoint] = useState('');
  
  // OAuth 2.0 fields
  const [authType, setAuthType] = useState({ label: 'OAuth 2.0', value: 'oauth2' });
  const [tokenEndpoint, setTokenEndpoint] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [resourceId, setResourceId] = useState('');
  const [tenant, setTenant] = useState('');
  
  // Streaming fields
  const [flowName, setFlowName] = useState('');
  const [shardCount, setShardCount] = useState('24');
  
  // Sample data fields
  const [sampleData, setSampleData] = useState('');
  const [sampleEvent, setSampleEvent] = useState('');
  const [dataDictionary, setDataDictionary] = useState('');
  
  // Schema field (single proto file for both telemetry and events)
  const [protoSchema, setProtoSchema] = useState('');
  
  const [mappings, setMappings] = useState<SignalMapping[]>([]);
  const [generatedManifest, setGeneratedManifest] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const generateTransform = async () => {
    setLoading(true);
    setError('');
    
    try {
      const payload: any = {
        oem_name: oemName,
        sample_data: JSON.parse(sampleData),
        sample_event: JSON.parse(sampleEvent),
        data_dictionary: JSON.parse(dataDictionary)
      };
      
      const response = await authFetch(`${getDataProcessingApiEndpoint()}generate-oem-transform`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      const data = await response.json();
      
      if (data.manifest) {
        // Store the full manifest
        setGeneratedManifest(data.manifest);
        
        // Convert manifest to table format
        const signalMappings = data.manifest.signal_mappings || [];
        const generatedMappings: SignalMapping[] = Array.isArray(signalMappings)
          ? signalMappings.map((mapping: any) => ({
              oem_signal: mapping.source_signal || mapping.cms_field.toUpperCase(),
              cms_field: mapping.cms_field || '(unknown)',
              oem_field: mapping.source_path || '(not found)',
              transform: mapping.unit_conversion || (mapping.value_map ? 'value_map' : 'Direct'),
              status: mapping.source_path && !mapping.source_path.includes('not found') ? 'mapped' : 'unmapped'
            }))
          : Object.entries(signalMappings).map(([signal, mapping]: [string, any]) => ({
              oem_signal: mapping.source_signal || signal.toUpperCase(),
              cms_field: signal,
              oem_field: mapping.source_field || mapping.source_path || '(not found)',
              transform: mapping.unit_conversion || (mapping.value_map ? 'value_map' : 'Direct'),
              status: (mapping.source_field || mapping.source_path) ? 'mapped' : 'unmapped'
            }));
        
        setMappings(generatedMappings);
        setStep(4);
      } else {
        setError('Failed to generate transform manifest');
      }
    } catch (e) {
      setError('Invalid JSON or API error');
    } finally {
      setLoading(false);
    }
  };

  const saveTransform = async () => {
    setLoading(true);
    
    try {
      // Register data source with connection type
      const config: any = {
        connection_type: connectionType.value,
        auth_type: authType.value
      };
      
      if (connectionType.value === 'rest_polling') {
        config.api_endpoint = apiEndpoint;
      } else if (connectionType.value === 'grpc_streaming') {
        config.grpc_endpoint = apiEndpoint;
        config.flow_name = flowName;
        config.shard_count = parseInt(shardCount);
      }
      
      if (authType.value === 'oauth2') {
        config.oauth2 = {
          token_endpoint: tokenEndpoint,
          client_id: clientId,
          client_secret: clientSecret,
          grant_type: 'client_credentials'
        };
        if (resourceId) config.oauth2.resource_id = resourceId;
        if (tenant) config.oauth2.tenant = tenant;
      }
      
      await authFetch(`${getDataProcessingApiEndpoint()}data-sources`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_name: oemName,
          source_type: 'oem',
          config
        })
      });

      // Upload manifest using the generated manifest
      const manifestName = `${oemName.toLowerCase().replace(/\s+/g, '-')}-transform.json`;
      await authFetch(`${getDataProcessingApiEndpoint()}manifests`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: manifestName,
          manifest: generatedManifest
        })
      });

      onComplete();
      resetWizard();
    } catch (e) {
      setError('Failed to save integration');
    } finally {
      setLoading(false);
    }
  };

  const resetWizard = () => {
    setStep(1);
    setOemName('');
    setConnectionType({ label: 'REST API (Polling)', value: 'rest_polling' });
    setApiEndpoint('');
    setAuthType({ label: 'OAuth 2.0', value: 'oauth2' });
    setTokenEndpoint('');
    setClientId('');
    setClientSecret('');
    setResourceId('');
    setTenant('');
    setFlowName('');
    setShardCount('24');
    setSampleData('');
    setSampleEvent('');
    setDataDictionary('');
    setMappings([]);
    setGeneratedManifest(null);
    setError('');
  };

  const handleDismiss = () => {
    resetWizard();
    onDismiss();
  };

  return (
    <Wizard
      i18nStrings={{
        stepNumberLabel: n => `Step ${n}`,
        collapsedStepsLabel: (step, total) => `Step ${step} of ${total}`,
        submitButton: 'Save & Deploy',
        cancelButton: 'Cancel',
        previousButton: 'Previous',
        nextButton: step === 3 ? 'Generate Transform' : 'Next',
      }}
      onCancel={handleDismiss}
      onSubmit={step === 3 ? generateTransform : saveTransform}
      activeStepIndex={step - 1}
      onNavigate={({ detail }) => setStep(detail.requestedStepIndex + 1)}
      isLoadingNextStep={loading}
      steps={[
        {
          title: 'Connection Details',
          description: 'Configure OEM connection type and endpoint',
          content: (
          <SpaceBetween size="m">
            <FormField key="oem-name" label="OEM Name">
              <Input value={oemName} onChange={e => setOemName(e.detail.value)} placeholder="e.g., OEM Commercial Solutions" />
            </FormField>
            
            <FormField key="connection-type" label="Connection Type" description="How data is received from the OEM">
              <Select
                selectedOption={connectionType}
                onChange={e => setConnectionType(e.detail.selectedOption as any)}
                options={[
                  { label: 'REST API (Polling)', value: 'rest_polling', description: 'Poll OEM endpoints in a loop via ECS Fargate' },
                  { label: 'gRPC Streaming', value: 'grpc_streaming', description: 'Long-lived gRPC stream with checkpointing via ECS Fargate' },
                ]}
              />
            </FormField>

            <FormField key="encoding-type" label="Data Encoding" description="Message format used by the OEM">
              <Select
                selectedOption={encodingType}
                onChange={e => setEncodingType(e.detail.selectedOption as any)}
                options={[
                  { label: 'JSON', value: 'json' },
                  { label: 'Protocol Buffers (Protobuf)', value: 'protobuf' },
                ]}
              />
            </FormField>
            
            {connectionType.value === 'rest_polling' && (
              <FormField key="rest-endpoint" label="Data API Endpoint" description="REST endpoint for fetching vehicle telemetry">
                <Input value={apiEndpoint} onChange={e => setApiEndpoint(e.detail.value)} placeholder="https://api.example.com/v1/vehicles" />
              </FormField>
            )}
            
            {connectionType.value === 'grpc_streaming' && (
              <>
                <FormField key="grpc-endpoint" label="gRPC Endpoint" description="gRPC service endpoint">
                  <Input value={apiEndpoint} onChange={e => setApiEndpoint(e.detail.value)} placeholder="grpc.oem-api.example.com:443" />
                </FormField>
                <FormField key="flow-name" label="Flow Name" description="Feed flow identifier">
                  <Input value={flowName} onChange={e => setFlowName(e.detail.value)} placeholder="aui:flow:feed/oem/region" />
                </FormField>
                <FormField key="shard-count" label="Shard Count" description="Number of shards to consume (default: 24)">
                  <Input value={shardCount} onChange={e => setShardCount(e.detail.value)} type="number" />
                </FormField>
              </>
            )}
          </SpaceBetween>
          ),
        },
        {
          title: 'Authentication',
          description: 'Configure OEM authentication credentials',
          content: (
          <SpaceBetween size="m">
            <FormField key="auth-type" label="Authentication Type">
              <Select
                selectedOption={authType}
                onChange={e => setAuthType(e.detail.selectedOption as any)}
                options={[
                  { label: 'OAuth 2.0', value: 'oauth2' },
                  { label: 'API Key', value: 'api_key' },
                  { label: 'mTLS', value: 'mtls' }
                ]}
              />
            </FormField>
            
            {authType.value === 'oauth2' && (
              <>
                <FormField key="token-endpoint" label="Token Endpoint" description="OAuth 2.0 token generation endpoint">
                  <Input 
                    value={tokenEndpoint} 
                    onChange={e => setTokenEndpoint(e.detail.value)} 
                    placeholder="https://login.microsoftonline.com/{tenant}/oauth2/token" 
                  />
                </FormField>
                <FormField key="client-id" label="Client ID" description="OAuth client ID provided by OEM">
                  <Input value={clientId} onChange={e => setClientId(e.detail.value)} type="password" />
                </FormField>
                <FormField key="client-secret" label="Client Secret" description="OAuth client secret provided by OEM">
                  <Input value={clientSecret} onChange={e => setClientSecret(e.detail.value)} type="password" />
                </FormField>
                <FormField key="resource-id" label="Resource ID (Required)" description="Application resource ID for token scope. Provided by your OEM during integration.">
                  <Input value={resourceId} onChange={e => setResourceId(e.detail.value)} placeholder="00000000-0000-0000-0000-000000000000" />
                </FormField>
                <FormField key="tenant" label="Tenant (Optional)" description="Azure AD tenant identifier">
                  <Input value={tenant} onChange={e => setTenant(e.detail.value)} placeholder="example.onmicrosoft.example" />
                </FormField>
              </>
            )}
          </SpaceBetween>
          ),
        },
        {
          title: 'Sample Data',
          description: 'Upload sample telemetry, events, and data dictionary',
          content: (
          <SpaceBetween size="m">
            <Alert key="info-alert" type="info">
              Upload sample files from your OEM. These will be used to generate the transform manifest.
            </Alert>
            
            <FormField 
              key="sample-telemetry"
              label="1. Sample Telemetry (Required)" 
              description="Upload a JSON file with sample vehicle telemetry data"
              info={
                <Link variant="info">Info</Link>
              }
              constraintText={
                <Popover
                  dismissButton={false}
                  position="top"
                  size="large"
                  triggerType="custom"
                  content={
                    <div style={{ padding: '8px' }}>
                      <strong>Example structure:</strong>
                      <pre style={{ fontSize: '11px', marginTop: '8px', background: 'var(--color-background-code-editor-default, #f4f4f4)', padding: '8px', borderRadius: '4px' }}>
{`{
  "vehicleId": "VIN123",
  "timestamp": "2025-11-03T12:00:00Z",
  "speed": 65.5,
  "fuelLevel": 75.2,
  "location": {
    "latitude": 42.3,
    "longitude": -83.2
  }
}`}
                      </pre>
                    </div>
                  }
                >
                  <Link variant="info">View example</Link>
                </Popover>
              }
            >
              <input 
                type="file" 
                accept=".json"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    try {
                      const text = await file.text();
                      JSON.parse(text); // Validate JSON
                      setSampleData(text);
                      setError(''); // Clear any previous errors
                    } catch (err: any) {
                      setError(`Invalid JSON in sample telemetry file: ${err.message}`);
                    }
                  }
                }}
                style={{ padding: '8px', border: '1px solid #ccc', borderRadius: '4px', width: '100%' }}
              />
              {sampleData && <div style={{ marginTop: '8px', fontSize: '12px', color: '#16ab39' }}>
                ✓ Valid JSON loaded ({(sampleData.length / 1024).toFixed(1)} KB)
              </div>}
            </FormField>
            
            <FormField 
              key="sample-event"
              label="2. Sample Event (Required)" 
              description="Upload a JSON file with sample event data"
              constraintText={
                <Popover
                  dismissButton={false}
                  position="top"
                  size="large"
                  triggerType="custom"
                  content={
                    <div style={{ padding: '8px' }}>
                      <strong>Example structure:</strong>
                      <pre style={{ fontSize: '11px', marginTop: '8px', background: 'var(--color-background-code-editor-default, #f4f4f4)', padding: '8px', borderRadius: '4px' }}>
{`{
  "eventType": "harsh_braking",
  "vehicleId": "VIN123",
  "timestamp": "2025-11-03T12:00:00Z",
  "severity": "high",
  "deceleration": -0.85,
  "location": {
    "latitude": 42.3,
    "longitude": -83.2
  }
}`}
                      </pre>
                    </div>
                  }
                >
                  <Link variant="info">View example</Link>
                </Popover>
              }
            >
              <input 
                type="file" 
                accept=".json"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    try {
                      const text = await file.text();
                      JSON.parse(text); // Validate JSON
                      setSampleEvent(text);
                      setError(''); // Clear any previous errors
                    } catch (err: any) {
                      setError(`Invalid JSON in sample event file: ${err.message}`);
                    }
                  }
                }}
                style={{ padding: '8px', border: '1px solid #ccc', borderRadius: '4px', width: '100%' }}
              />
              {sampleEvent && <div style={{ marginTop: '8px', fontSize: '12px', color: '#16ab39' }}>
                ✓ Valid JSON loaded ({(sampleEvent.length / 1024).toFixed(1)} KB)
              </div>}
            </FormField>

            {encodingType.value === 'protobuf' && (
              <FormField 
                key="proto-schema"
                label="3. Protobuf Schema (Required for Protobuf)" 
                description="Upload a single .proto file with all message definitions"
                constraintText={
                  <Popover
                    dismissButton={false}
                    position="top"
                    size="large"
                    triggerType="custom"
                    content={
                      <div style={{ padding: '8px' }}>
                        <strong>Example structure:</strong>
                        <pre style={{ fontSize: '11px', marginTop: '8px', background: 'var(--color-background-code-editor-default, #f4f4f4)', padding: '8px', borderRadius: '4px' }}>
{`syntax = "proto3";

message Metric {
  string vehicleId = 1;
  double speed = 2;
  Location location = 3;
}

message Event {
  string eventType = 1;
  string vehicleId = 2;
}

message Location {
  double latitude = 1;
  double longitude = 2;
}`}
                        </pre>
                      </div>
                    }
                  >
                    <Link variant="info">View example</Link>
                  </Popover>
                }
              >
                <input 
                  type="file" 
                  accept=".proto"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      try {
                        const text = await file.text();
                        if (!text.includes('syntax = "proto3"') && !text.includes("syntax = 'proto3'")) {
                          setError('Proto file must specify syntax = "proto3"');
                          return;
                        }
                        setProtoSchema(text);
                      } catch (err) {
                        setError('Failed to read proto file');
                      }
                    }
                  }}
                  style={{ padding: '8px', border: '1px solid #ccc', borderRadius: '4px', width: '100%' }}
                />
                {protoSchema && <div style={{ marginTop: '8px', fontSize: '12px', color: '#16ab39' }}>
                  ✓ Proto file loaded ({(protoSchema.length / 1024).toFixed(1)} KB)
                </div>}
              </FormField>
            )}
            
            <FormField 
              key="data-dictionary"
              label={encodingType.value === 'protobuf' ? "4. Data Dictionary (Required)" : "3. Data Dictionary (Required)"}
              description="Upload the OEM's complete signal and event catalog (JSON)"
              constraintText={
                <Popover
                  dismissButton={false}
                  position="top"
                  size="large"
                  triggerType="custom"
                  content={
                    <div style={{ padding: '8px' }}>
                      <strong>Example structure:</strong>
                      <pre style={{ fontSize: '11px', marginTop: '8px', background: 'var(--color-background-code-editor-default, #f4f4f4)', padding: '8px', borderRadius: '4px' }}>
{`{
  "signals": {
    "SPEED": {
      "description": "Vehicle speed",
      "unit": "km/h",
      "valueType": "Double",
      "valueField": "speed"
    },
    "FUEL_LEVEL": {
      "description": "Fuel level",
      "unit": "%",
      "valueType": "Double",
      "valueField": "fuelLevel"
    }
  },
  "events": {
    "HARSH_BRAKING": {
      "description": "Harsh braking event",
      "eventType": "harsh_braking"
    }
  }
}`}
                      </pre>
                    </div>
                  }
                >
                  <Link variant="info">View example</Link>
                </Popover>
              }
            >
              <input 
                type="file" 
                accept=".json"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    try {
                      const text = await file.text();
                      const parsed = JSON.parse(text); // Validate JSON
                      if (!parsed.signals && !parsed.events) {
                        setError('Data dictionary must contain "signals" or "events" field');
                        return;
                      }
                      setDataDictionary(text);
                      setError(''); // Clear any previous errors
                    } catch (err: any) {
                      setError(`Invalid JSON in data dictionary file: ${err.message}`);
                    }
                  }
                }}
                style={{ padding: '8px', border: '1px solid #ccc', borderRadius: '4px', width: '100%' }}
              />
              {dataDictionary && <div style={{ marginTop: '8px', fontSize: '12px', color: '#16ab39' }}>
                ✓ Valid JSON loaded ({(dataDictionary.length / 1024).toFixed(1)} KB)
              </div>}
            </FormField>
          </SpaceBetween>
          ),
        },
        {
          title: 'Review & Deploy',
          description: 'Review signal mappings and deploy the transform',
          content: (
          <SpaceBetween size="m">
            <Alert key="mappings-alert" type="info">
              {mappings.filter(m => m.status === 'mapped').length} of {mappings.length} signals mapped
            </Alert>
            <Table
              columnDefinitions={[
                { id: 'oem_signal', header: 'OEM Signal Name', cell: (item: SignalMapping) => item.oem_signal || item.cms_field.toUpperCase() },
                { id: 'oem_field', header: 'OEM Field Path', cell: (item: SignalMapping) => item.oem_field },
                { id: 'cms_field', header: 'Connected Mobility Signal', cell: (item: SignalMapping) => item.cms_field },
                { id: 'transform', header: 'Transform', cell: (item: SignalMapping) => item.transform },
                { 
                  id: 'status', 
                  header: 'Status', 
                  cell: (item: SignalMapping) => (
                    <StatusIndicator type={item.status === 'mapped' ? 'success' : 'warning'}>
                      {item.status}
                    </StatusIndicator>
                  )
                }
              ]}
              items={mappings}
            />
          </SpaceBetween>
          ),
        },
      ]}
    />
  );
};

export default OEMIntegrationWizard;
