// Engineer persona — mock data provider barrel exports.
// In production these are wired to DynamoDB / Bedrock Knowledge Base / Bedrock Agent.

export * from './fleet-data';
export * from './vehicles';
export * from './telemetry';
export * from './anomalies';
export * from './kb-corpus';
export * from './design-options';
export * from './agent-script';

// SDV layer — software, parts, OTA. Joined client-side with DynamoDB vehicles.
export * from './ecus';
export * from './parts-bom';
export * from './ota-pipelines';
export * from './signal-ecu-map';
export * from './vehicle-models';
