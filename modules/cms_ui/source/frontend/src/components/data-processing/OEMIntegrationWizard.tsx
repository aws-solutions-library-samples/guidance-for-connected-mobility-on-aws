import React, { useState } from 'react';
import { 
  Modal, 
  Box, 
  SpaceBetween, 
  FormField, 
  Input, 
  Select, 
  Textarea,
  Button,
  Table,
  StatusIndicator,
  Alert
} from '@cloudscape-design/components';

interface OEMIntegrationWizardProps {
  visible: boolean;
  onDismiss: () => void;
  onComplete: () => void;
}

interface SignalMapping {
  oem_signal?: string;
  cms_signal: string;
  oem_field: string;
  transform: string;
  status: 'mapped' | 'unmapped';
}

const OEMIntegrationWizard: React.FC<OEMIntegrationWizardProps> = ({ visible, onDismiss, onComplete }) => {
  const [step, setStep] = useState(1);
  const [oemName, setOemName] = useState('');
  const [connectionType, setConnectionType] = useState({ label: 'REST API (Polling)', value: 'rest' });
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
      
      const response = await fetch('https://5oux6cw3ef.execute-api.us-east-1.amazonaws.com/prod/generate-oem-transform', {
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
              oem_signal: mapping.source_signal || mapping.cms_signal.toUpperCase(),
              cms_signal: mapping.cms_signal || '(unknown)',
              oem_field: mapping.source_path || '(not found)',
              transform: mapping.transform || (mapping.unit_conversion ? `${mapping.unit_conversion.operation}` : 'Direct'),
              status: mapping.source_path && !mapping.source_path.includes('not found') ? 'mapped' : 'unmapped'
            }))
          : Object.entries(signalMappings).map(([signal, mapping]: [string, any]) => ({
              oem_signal: mapping.source_signal || signal.toUpperCase(),
              cms_signal: signal,
              oem_field: mapping.source_field || mapping.source_path || '(not found)',
              transform: mapping.transform || (mapping.unit_conversion ? `${mapping.unit_conversion.operation}` : 'Direct'),
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
      
      if (connectionType.value === 'rest') {
        config.api_endpoint = apiEndpoint;
      } else if (connectionType.value === 'streaming') {
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
      
      await fetch('https://5oux6cw3ef.execute-api.us-east-1.amazonaws.com/prod/data-sources', {
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
      await fetch('https://5oux6cw3ef.execute-api.us-east-1.amazonaws.com/prod/manifests', {
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
    setConnectionType({ label: 'REST API (Polling)', value: 'rest' });
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
    <Modal
      visible={visible}
      onDismiss={handleDismiss}
      header={`Add OEM Integration - Step ${step} of 4`}
      size="max"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button key="cancel" onClick={handleDismiss}>Cancel</Button>
            {step > 1 && <Button key="back" onClick={() => setStep(step - 1)}>Back</Button>}
            {step === 1 && (
              <Button 
                key="next-auth"
                variant="primary" 
                onClick={() => setStep(2)} 
                disabled={!oemName || !apiEndpoint || (connectionType.value === 'streaming' && !flowName)}
              >
                Next: Authentication
              </Button>
            )}
            {step === 2 && (
              <Button 
                key="next-sample"
                variant="primary" 
                onClick={() => setStep(3)} 
                disabled={authType.value === 'oauth2' && (!tokenEndpoint || !clientId || !clientSecret || !resourceId)}
              >
                Next: Sample Data
              </Button>
            )}
            {step === 3 && (
              <Button 
                key="generate"
                variant="primary" 
                onClick={generateTransform} 
                loading={loading} 
                disabled={!sampleData || !sampleEvent || !dataDictionary}
              >
                Generate Transform
              </Button>
            )}
            {step === 4 && (
              <Button key="save" variant="primary" onClick={saveTransform} loading={loading}>
                Save & Deploy
              </Button>
            )}
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="l">
        {error && <Alert type="error">{error}</Alert>}

        {step === 1 && (
          <SpaceBetween size="m">
            <FormField key="oem-name" label="OEM Name">
              <Input value={oemName} onChange={e => setOemName(e.detail.value)} placeholder="e.g., OEM Commercial Solutions" />
            </FormField>
            
            <FormField key="connection-type" label="Connection Type" description="How data is received from the OEM">
              <Select
                selectedOption={connectionType}
                onChange={e => setConnectionType(e.detail.selectedOption as any)}
                options={[
                  { label: 'REST API (Polling)', value: 'rest' },
                  { label: 'Streaming (gRPC/Pub-Sub)', value: 'streaming' }
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
                  { label: 'Apache Avro', value: 'avro' },
                  { label: 'Raw/Custom', value: 'raw' }
                ]}
              />
            </FormField>
            
            {connectionType.value === 'rest' && (
              <FormField key="rest-endpoint" label="Data API Endpoint" description="REST endpoint for fetching vehicle telemetry">
                <Input value={apiEndpoint} onChange={e => setApiEndpoint(e.detail.value)} placeholder="https://api.example.com/v1/vehicles" />
              </FormField>
            )}
            
            {connectionType.value === 'streaming' && (
              <>
                <FormField key="grpc-endpoint" label="gRPC Endpoint" description="gRPC service endpoint">
                  <Input value={apiEndpoint} onChange={e => setApiEndpoint(e.detail.value)} placeholder="grpc.oem-api.example.com:443" />
                </FormField>
                <FormField key="flow-name" label="Flow Name" description="Feed flow identifier (e.g., aui:flow:feed/vendor/region)">
                  <Input value={flowName} onChange={e => setFlowName(e.detail.value)} placeholder="aui:flow:feed/oem/region" />
                </FormField>
                <FormField key="shard-count" label="Shard Count" description="Number of shards to consume (default: 24)">
                  <Input value={shardCount} onChange={e => setShardCount(e.detail.value)} type="number" />
                </FormField>
              </>
            )}
          </SpaceBetween>
        )}

        {step === 2 && (
          <SpaceBetween size="m">
            <FormField key="auth-type" label="Authentication Type">
              <Select
                selectedOption={authType}
                onChange={e => setAuthType(e.detail.selectedOption as any)}
                options={[
                  { label: 'OAuth 2.0', value: 'oauth2' },
                  { label: 'API Key', value: 'api_key' },
                  { label: 'Basic Auth', value: 'basic' }
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
                <FormField key="resource-id" label="Resource ID (Required)" description="Application resource ID for token scope. Dev/Stage: 1d0b95d3-c64f-4a2b-8816-d855412062c0, Prod: f9c1c210-682c-4ec5-a73b-d12d8fa143f6">
                  <Input value={resourceId} onChange={e => setResourceId(e.detail.value)} placeholder="1d0b95d3-c64f-4a2b-8816-d855412062c0" />
                </FormField>
                <FormField key="tenant" label="Tenant (Optional)" description="Azure AD tenant identifier">
                  <Input value={tenant} onChange={e => setTenant(e.detail.value)} placeholder="fcsfleet.onmicrosoft.com" />
                </FormField>
              </>
            )}
          </SpaceBetween>
        )}

        {step === 3 && (
          <SpaceBetween size="m">
            <Alert key="info-alert" type="info">
              Upload sample files from your OEM. These will be used to generate the transform manifest.
            </Alert>
            
            <FormField 
              key="sample-telemetry"
              label="1. Sample Telemetry (Required)" 
              description="Upload a JSON file with sample vehicle telemetry data"
            >
              <input 
                type="file" 
                accept=".json"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    const text = await file.text();
                    setSampleData(text);
                  }
                }}
                style={{ padding: '8px', border: '1px solid #ccc', borderRadius: '4px', width: '100%' }}
              />
              {sampleData && <div style={{ marginTop: '8px', fontSize: '12px', color: '#16ab39' }}>
                ✓ File loaded ({(sampleData.length / 1024).toFixed(1)} KB)
              </div>}
            </FormField>
            
            <FormField 
              key="sample-event"
              label="2. Sample Event (Required)" 
              description="Upload a JSON file with sample event data"
            >
              <input 
                type="file" 
                accept=".json"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    const text = await file.text();
                    setSampleEvent(text);
                  }
                }}
                style={{ padding: '8px', border: '1px solid #ccc', borderRadius: '4px', width: '100%' }}
              />
              {sampleEvent && <div style={{ marginTop: '8px', fontSize: '12px', color: '#16ab39' }}>
                ✓ File loaded ({(sampleEvent.length / 1024).toFixed(1)} KB)
              </div>}
            </FormField>

            {encodingType.value === 'protobuf' && (
              <FormField 
                key="proto-schema"
                label="3. Protobuf Schema (Required for Protobuf)" 
                description="Upload a single .proto file with all message definitions"
              >
                <input 
                  type="file" 
                  accept=".proto"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      const text = await file.text();
                      setProtoSchema(text);
                    }
                  }}
                  style={{ padding: '8px', border: '1px solid #ccc', borderRadius: '4px', width: '100%' }}
                />
                {protoSchema && <div style={{ marginTop: '8px', fontSize: '12px', color: '#16ab39' }}>
                  ✓ File loaded ({(protoSchema.length / 1024).toFixed(1)} KB)
                </div>}
              </FormField>
            )}
            
            <FormField 
              key="data-dictionary"
              label={encodingType.value === 'protobuf' ? "4. Data Dictionary (Required)" : "3. Data Dictionary (Required)"}
              description="Upload the OEM's complete signal and event catalog (JSON)"
            >
              <input 
                type="file" 
                accept=".json"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    const text = await file.text();
                    setDataDictionary(text);
                  }
                }}
                style={{ padding: '8px', border: '1px solid #ccc', borderRadius: '4px', width: '100%' }}
              />
              {dataDictionary && <div style={{ marginTop: '8px', fontSize: '12px', color: '#16ab39' }}>
                ✓ File loaded ({(dataDictionary.length / 1024).toFixed(1)} KB)
              </div>}
            </FormField>
          </SpaceBetween>
        )}

        {step === 4 && (
          <SpaceBetween size="m">
            <Alert key="mappings-alert" type="info">
              {mappings.filter(m => m.status === 'mapped').length} of {mappings.length} signals mapped
            </Alert>
            <Table
              columnDefinitions={[
                { id: 'oem_signal', header: 'OEM Signal Name', cell: (item: SignalMapping) => item.oem_signal || item.cms_signal.toUpperCase() },
                { id: 'oem_field', header: 'OEM Field Path', cell: (item: SignalMapping) => item.oem_field },
                { id: 'cms_signal', header: 'Connected Mobility Signal', cell: (item: SignalMapping) => item.cms_signal },
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
        )}
      </SpaceBetween>
    </Modal>
  );
};

export default OEMIntegrationWizard;
