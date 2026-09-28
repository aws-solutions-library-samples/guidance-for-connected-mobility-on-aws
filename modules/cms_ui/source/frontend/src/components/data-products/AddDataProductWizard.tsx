// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// AddDataProductWizard — the "add Meridian / add another producer" flow.
//
// Cloudscape Wizard component with 4 steps:
//   1. Basic info         — product name, producer, description
//   2. Connection         — type, endpoint URL, auth type, token endpoint (if OAuth),
//                           credentials Secret ARN, and connection-type-specific fields
//   3. Signal mapping     — the transform manifest: which OEM signals map to which
//                           CMS canonical fields, unit conversions, required flags.
//                           Zero mappings is a valid save; more can be added later.
//   4. Review             — read-only summary of the above before submit
//
// Design notes:
//   - Tiers are NOT collected here. Not every producer thinks in tiers, and
//     forcing the fleet operator to declare tiers up-front was presumptuous.
//     `supportedTiers` remains optional on DataProduct so seed products keep
//     working; wizard-added products get `undefined` and the Subscription
//     modal handles the tier-less case.
//   - The signal-mapping step is where the transform manifest comes to life.
//     Fleet operator adds rows inline (no nested modal — Cloudscape nesting is
//     fussy). Each row captures source signal, source path, CMS field, type,
//     unit, and an optional named unit conversion.
//
// Submit calls addDataProduct() with a session-scoped Draft->Active entry.
// No backend. Refresh loses added entries.

import React, { useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Badge,
  Button,
  ColumnLayout,
  Container,
  FormField,
  Header,
  Input,
  Modal,
  Select,
  SpaceBetween,
  Table,
  Textarea,
  Wizard,
} from '@cloudscape-design/components';
import {
  addDataProduct,
  AuthType,
  ConnectionType,
  DataProduct,
  EventMapping,
  SignalMapping,
} from './mockData';
import {
  CMS_CANONICAL_SIGNALS,
  CMS_CANONICAL_EVENTS,
  getProducerSignalsOrGeneric,
  getProducerEventsOrGeneric,
  isProducerStubbed,
  autoMatchSignal,
  autoMatchEvent,
  CmsCanonicalSignal,
  CmsCanonicalEvent,
  ProducerSignal,
  ProducerEvent,
} from './signalMapping';

interface Props {
  visible: boolean;
  onDismiss: () => void;
  onAdded: (name: string) => void;
}

const CONNECTION_OPTIONS = [
  { label: 'REST polling', value: 'rest_polling', description: 'Poll the producer\'s REST API on an interval. Best for low-frequency or basic-tier feeds.' },
  { label: 'gRPC streaming', value: 'grpc_streaming', description: 'Long-lived bidirectional gRPC stream. Low latency, high throughput. Common for real-time fleet feeds.' },
  { label: 'Kafka', value: 'kafka', description: 'Consume from Kafka topics on the producer\'s cluster. Scales to very high fan-out, standard for large OEM telemetry.' },
  { label: 'WebSocket inbound', value: 'websocket_inbound', description: 'Accept inbound WebSocket connections from the producer. Producer initiates.' },
];

const AUTH_OPTIONS = [
  { label: 'OAuth 2.0', value: 'oauth2', description: 'Client-credentials flow. Standard for REST and gRPC APIs.' },
  { label: 'API key', value: 'api_key', description: 'Long-lived key in a header. Simple integrations, entry-tier feeds.' },
  { label: 'mTLS', value: 'mtls', description: 'Mutual-TLS client certificates. Typical for inbound WebSocket, sometimes for gRPC and Kafka.' },
  { label: 'SASL/SCRAM', value: 'sasl_scram', description: 'Salted challenge-response username/password over TLS. Standard for cloud-deployed Kafka.' },
];

const AddDataProductWizard: React.FC<Props> = ({ visible, onDismiss, onAdded }) => {
  const [activeStep, setActiveStep] = useState(0);

  // Step 1 — Basic info
  const [productName, setProductName] = useState('');
  const [producer, setProducer] = useState('');
  const [description, setDescription] = useState('');

  // Step 2 — Connection
  const [connectionType, setConnectionType] = useState<ConnectionType>('rest_polling');
  const [endpointUrl, setEndpointUrl] = useState('');
  const [authType, setAuthType] = useState<AuthType>('oauth2');
  const [tokenEndpoint, setTokenEndpoint] = useState('');
  const [credentialsSecretArn, setCredentialsSecretArn] = useState('');

  // Connection-type-specific
  const [pollingIntervalSeconds, setPollingIntervalSeconds] = useState('30');
  const [grpcServiceName, setGrpcServiceName] = useState('');
  const [grpcMethodName, setGrpcMethodName] = useState('StreamTelemetry');
  const [kafkaTopic, setKafkaTopic] = useState('');
  const [kafkaConsumerGroup, setKafkaConsumerGroup] = useState('');
  const [wsListenEndpoint, setWsListenEndpoint] = useState('');
  const [wsAllowedOrigin, setWsAllowedOrigin] = useState('');

  // Auth-type-specific
  const [oauthScopes, setOauthScopes] = useState('');
  const [apiKeyHeaderName, setApiKeyHeaderName] = useState('X-API-Key');
  const [scramMechanism, setScramMechanism] = useState<'SCRAM-SHA-256' | 'SCRAM-SHA-512'>('SCRAM-SHA-512');

  // Step 3 — Signal mapping (transform manifest)
  //
  // Pick-and-match model: for each CMS canonical signal / event, the
  // operator picks a producer-advertised signal / event to bind to it (or
  // leaves it Unmapped). The wizard collects only the pairing; sourcePath,
  // dataType, and unit are looked up from the two catalogs at save time.
  // Producer isn't known until step 3 is entered — but for the WIZARD the
  // producer name comes from step 1's `producer` field, which the operator
  // typed. Auto-match only fires when that producer matches a stubbed
  // catalog (Meridian / Ford / Tesla); otherwise the pickers are empty and
  // the operator can still save with zero mappings.
  const [signalMappings, setSignalMappings] = useState<SignalMapping[]>([]);
  const [eventMappings, setEventMappings] = useState<EventMapping[]>([]);

  const producerSignals = useMemo(
    () => (producer.trim() ? getProducerSignalsOrGeneric(producer.trim()) : []),
    [producer],
  );
  const producerEventsList = useMemo(
    () => (producer.trim() ? getProducerEventsOrGeneric(producer.trim()) : []),
    [producer],
  );
  const producerIsStubbed = useMemo(
    () => (producer.trim() ? isProducerStubbed(producer.trim()) : false),
    [producer],
  );

  const signalMappingByCmsField = useMemo(() => {
    const m = new Map<string, SignalMapping>();
    for (const r of signalMappings) m.set(r.cmsField, r);
    return m;
  }, [signalMappings]);
  const eventMappingByCmsEvent = useMemo(() => {
    const m = new Map<string, EventMapping>();
    for (const r of eventMappings) m.set(r.cmsEvent, r);
    return m;
  }, [eventMappings]);

  const setSignalMapping = (cms: CmsCanonicalSignal, p: ProducerSignal | undefined) => {
    const rest = signalMappings.filter((m) => m.cmsField !== cms.name);
    if (!p) return setSignalMappings(rest);
    setSignalMappings([
      ...rest,
      {
        cmsField: cms.name,
        sourceSignal: p.name,
        sourcePath: p.path,
        dataType: p.dataType,
        unit: cms.unit ?? p.unit,
        required: cms.required,
      },
    ]);
  };
  const setEventMapping = (cms: CmsCanonicalEvent, p: ProducerEvent | undefined) => {
    const rest = eventMappings.filter((m) => m.cmsEvent !== cms.name);
    if (!p) return setEventMappings(rest);
    setEventMappings([...rest, { cmsEvent: cms.name, sourceEvent: p.name }]);
  };

  const autoMapSignals = () => {
    const next: SignalMapping[] = [];
    for (const cms of CMS_CANONICAL_SIGNALS) {
      const existing = signalMappingByCmsField.get(cms.name);
      if (existing) {
        next.push(existing);
        continue;
      }
      const guess = autoMatchSignal(cms.name, producerSignals);
      if (!guess) continue;
      next.push({
        cmsField: cms.name,
        sourceSignal: guess.name,
        sourcePath: guess.path,
        dataType: guess.dataType,
        unit: cms.unit ?? guess.unit,
        required: cms.required,
      });
    }
    setSignalMappings(next);
  };
  const autoMapEvents = () => {
    const next: EventMapping[] = [];
    for (const cms of CMS_CANONICAL_EVENTS) {
      const existing = eventMappingByCmsEvent.get(cms.name);
      if (existing) {
        next.push(existing);
        continue;
      }
      const guess = autoMatchEvent(cms.name, producerEventsList);
      if (!guess) continue;
      next.push({ cmsEvent: cms.name, sourceEvent: guess.name });
    }
    setEventMappings(next);
  };

  const groupedCmsSignals = useMemo(() => {
    const g: Record<string, CmsCanonicalSignal[]> = {};
    for (const s of CMS_CANONICAL_SIGNALS) {
      if (!g[s.group]) g[s.group] = [];
      g[s.group].push(s);
    }
    return g;
  }, []);
  const groupedCmsEvents = useMemo(() => {
    const g: Record<string, CmsCanonicalEvent[]> = {};
    for (const e of CMS_CANONICAL_EVENTS) {
      if (!g[e.group]) g[e.group] = [];
      g[e.group].push(e);
    }
    return g;
  }, []);

  // Step validation
  const step1Valid = productName.trim().length > 0 && producer.trim().length > 0;
  const pollingIntervalNum = Number(pollingIntervalSeconds);
  const pollingIntervalValid =
    connectionType !== 'rest_polling' ||
    (Number.isFinite(pollingIntervalNum) && pollingIntervalNum >= 1 && pollingIntervalNum <= 3600);
  const connectionSpecificValid =
    (connectionType !== 'grpc_streaming' || grpcServiceName.trim().length > 0) &&
    (connectionType !== 'kafka' || (kafkaTopic.trim().length > 0 && kafkaConsumerGroup.trim().length > 0)) &&
    (connectionType !== 'websocket_inbound' || wsListenEndpoint.trim().length > 0) &&
    pollingIntervalValid;
  const step2Valid =
    endpointUrl.trim().length > 0 &&
    credentialsSecretArn.trim().length > 0 &&
    (authType !== 'oauth2' || tokenEndpoint.trim().length > 0) &&
    connectionSpecificValid;
  // Step 3 is always valid — zero mappings is a legitimate save (more can be
  // added later from the catalog detail page). The pending-row form has its
  // own separate valid flag used only to enable the Add button.
  const step3Valid = true;
  const allValid = step1Valid && step2Valid && step3Valid;

  const reset = () => {
    setActiveStep(0);
    setProductName('');
    setProducer('');
    setDescription('');
    setConnectionType('rest_polling');
    setEndpointUrl('');
    setAuthType('oauth2');
    setTokenEndpoint('');
    setCredentialsSecretArn('');
    setPollingIntervalSeconds('30');
    setGrpcServiceName('');
    setGrpcMethodName('StreamTelemetry');
    setKafkaTopic('');
    setKafkaConsumerGroup('');
    setWsListenEndpoint('');
    setWsAllowedOrigin('');
    setOauthScopes('');
    setApiKeyHeaderName('X-API-Key');
    setScramMechanism('SCRAM-SHA-512');
    setSignalMappings([]);
    setEventMappings([]);
  };

  const handleSubmit = () => {
    if (!allValid) return;
    const product: DataProduct = {
      productId: `prd_new_${Date.now().toString(36)}`,
      productName: productName.trim(),
      producer: producer.trim(),
      description: description.trim() || `Fleet telemetry from ${producer.trim()}.`,
      connectionType,
      endpointUrl: endpointUrl.trim(),
      authType,
      tokenEndpoint: authType === 'oauth2' ? tokenEndpoint.trim() : undefined,
      credentialsSecretArn: credentialsSecretArn.trim(),
      // supportedTiers intentionally undefined — wizard-added products are
      // tier-less by default. The subscription flow handles this by falling
      // back to a single "Default" tier when the product has no tiers.
      // totalSignals is derived from the signal-mapping count.
      totalSignals: signalMappings.length,
      signalMappings,
      eventMappings,
      status: 'Active',
      createdAt: new Date().toISOString().slice(0, 10),
      // Connection-type-specific — only populated for the matching type
      pollingIntervalSeconds: connectionType === 'rest_polling' ? pollingIntervalNum : undefined,
      grpcServiceName: connectionType === 'grpc_streaming' ? grpcServiceName.trim() : undefined,
      grpcMethodName: connectionType === 'grpc_streaming' ? grpcMethodName.trim() : undefined,
      kafkaTopic: connectionType === 'kafka' ? kafkaTopic.trim() : undefined,
      kafkaConsumerGroup: connectionType === 'kafka' ? kafkaConsumerGroup.trim() : undefined,
      wsListenEndpoint: connectionType === 'websocket_inbound' ? wsListenEndpoint.trim() : undefined,
      wsAllowedOrigin: connectionType === 'websocket_inbound' ? wsAllowedOrigin.trim() : undefined,
      // Auth-type-specific
      oauthScopes: authType === 'oauth2' ? oauthScopes.trim() || undefined : undefined,
      apiKeyHeaderName: authType === 'api_key' ? apiKeyHeaderName.trim() : undefined,
      scramMechanism: authType === 'sasl_scram' ? scramMechanism : undefined,
    };
    addDataProduct(product);
    onAdded(product.productName);
    reset();
  };

  const handleDismiss = () => {
    reset();
    onDismiss();
  };

  if (!visible) return null;

  return (
    <Modal
      visible={visible}
      onDismiss={handleDismiss}
      header="Add data product"
      size="max"
      footer={null}
    >
      <Wizard
        activeStepIndex={activeStep}
        onNavigate={({ detail }) => {
          if (detail.reason === 'next') {
            // Only advance if the current step is valid
            const okToAdvance =
              (activeStep === 0 && step1Valid) ||
              (activeStep === 1 && step2Valid) ||
              (activeStep === 2 && step3Valid) ||
              activeStep === 3;
            if (okToAdvance) setActiveStep(detail.requestedStepIndex);
          } else {
            setActiveStep(detail.requestedStepIndex);
          }
        }}
        onCancel={handleDismiss}
        onSubmit={handleSubmit}
        isLoadingNextStep={false}
        i18nStrings={{
          stepNumberLabel: (n) => `Step ${n}`,
          collapsedStepsLabel: (a, b) => `Step ${a} of ${b}`,
          cancelButton: 'Cancel',
          previousButton: 'Previous',
          nextButton: 'Next',
          submitButton: 'Add data product',
          optional: 'optional',
        }}
        allowSkipTo={false}
        steps={[
          // ── Step 1: Basic info ────────────────────────────────────────
          {
            title: 'Basic info',
            description: 'Identity of the data product and its producer.',
            content: (
              <Container header={<Header variant="h2">Product identity</Header>}>
                <SpaceBetween size="l">
                  <FormField
                    label="Product name"
                    description="Displayed everywhere in the UI. E.g. 'Meridian Fleet Telemetry'."
                  >
                    <Input
                      value={productName}
                      onChange={({ detail }) => setProductName(detail.value)}
                      placeholder="Meridian Fleet Telemetry"
                    />
                  </FormField>
                  <FormField
                    label="Producer"
                    description="The OEM or data-source name. E.g. 'Meridian'."
                  >
                    <Input
                      value={producer}
                      onChange={({ detail }) => setProducer(detail.value)}
                      placeholder="Meridian"
                    />
                  </FormField>
                  <FormField
                    label="Description"
                    description="Short summary of what this product provides — signals, cadence, vehicle coverage."
                  >
                    <Textarea
                      value={description}
                      onChange={({ detail }) => setDescription(detail.value)}
                      rows={3}
                      placeholder="High-frequency fleet telemetry from Meridian production vehicles. Battery pack cell diagnostics, thermal management, per-second cadence."
                    />
                  </FormField>
                </SpaceBetween>
              </Container>
            ),
            isOptional: false,
          },

          // ── Step 2: Connection ────────────────────────────────────────
          {
            title: 'Connection',
            description: 'How the platform receives telemetry from this OEM.',
            content: (
              <SpaceBetween size="l">
                <Container header={<Header variant="h2">Transport</Header>}>
                  <SpaceBetween size="l">
                    <FormField
                      label="Connection type"
                      description="How data arrives. Different transports need different parameters — the fields below adapt to what you pick."
                    >
                      <Select
                        selectedOption={
                          CONNECTION_OPTIONS.find((o) => o.value === connectionType) ??
                          CONNECTION_OPTIONS[0]
                        }
                        options={CONNECTION_OPTIONS}
                        onChange={({ detail }) =>
                          setConnectionType(detail.selectedOption.value as ConnectionType)
                        }
                        expandToViewport
                      />
                    </FormField>

                    <FormField
                      label={
                        connectionType === 'kafka'
                          ? 'Bootstrap servers'
                          : connectionType === 'grpc_streaming'
                          ? 'gRPC target'
                          : connectionType === 'websocket_inbound'
                          ? 'Listen endpoint (CMS side)'
                          : 'Endpoint URL'
                      }
                      description={
                        connectionType === 'kafka'
                          ? <>Comma-separated Kafka broker list. <code>host:port,host:port,...</code></>
                          : connectionType === 'grpc_streaming'
                          ? <>gRPC target with scheme, host, and port. <code>grpc+tls://host:443</code></>
                          : connectionType === 'websocket_inbound'
                          ? <>URL the producer connects TO. Owned and exposed by CMS.</>
                          : <>Producer's HTTPS REST endpoint.</>
                      }
                    >
                      <Input
                        value={endpointUrl}
                        onChange={({ detail }) => setEndpointUrl(detail.value)}
                        placeholder={
                          connectionType === 'kafka'
                            ? 'broker1.example.com:9093,broker2.example.com:9093'
                            : connectionType === 'grpc_streaming'
                            ? 'grpc+tls://fleet.example.com:443'
                            : connectionType === 'websocket_inbound'
                            ? 'wss://cms-fleet.example.com/oem-webhooks/producer-abc'
                            : 'https://api.example.com/fleet/v1/telemetry'
                        }
                      />
                    </FormField>

                    {/* Transport-specific: REST polling */}
                    {connectionType === 'rest_polling' && (
                      <FormField
                        label="Polling interval"
                        description="How often to poll the endpoint. Producer's rate-limits may cap this."
                        constraintText="Whole number 1–3600 seconds."
                        errorText={pollingIntervalValid ? undefined : 'Must be 1–3600 seconds.'}
                      >
                        <Input
                          value={pollingIntervalSeconds}
                          onChange={({ detail }) => setPollingIntervalSeconds(detail.value)}
                          type="number"
                          inputMode="numeric"
                          placeholder="30"
                        />
                      </FormField>
                    )}

                    {/* Transport-specific: gRPC streaming */}
                    {connectionType === 'grpc_streaming' && (
                      <>
                        <FormField
                          label="gRPC service name"
                          description="Fully-qualified service from the producer's .proto file."
                        >
                          <Input
                            value={grpcServiceName}
                            onChange={({ detail }) => setGrpcServiceName(detail.value)}
                            placeholder="com.oem.fleet.v1.TelemetryService"
                          />
                        </FormField>
                        <FormField
                          label="RPC method"
                          description="Server-streaming RPC method that emits telemetry frames."
                        >
                          <Input
                            value={grpcMethodName}
                            onChange={({ detail }) => setGrpcMethodName(detail.value)}
                            placeholder="StreamTelemetry"
                          />
                        </FormField>
                      </>
                    )}

                    {/* Transport-specific: Kafka */}
                    {connectionType === 'kafka' && (
                      <>
                        <FormField
                          label="Topic"
                          description="Kafka topic to consume from."
                        >
                          <Input
                            value={kafkaTopic}
                            onChange={({ detail }) => setKafkaTopic(detail.value)}
                            placeholder="fleet-telemetry-v1"
                          />
                        </FormField>
                        <FormField
                          label="Consumer group"
                          description="CMS-side identity for offset tracking. Must be unique per subscribing environment."
                        >
                          <Input
                            value={kafkaConsumerGroup}
                            onChange={({ detail }) => setKafkaConsumerGroup(detail.value)}
                            placeholder="cms-fleet-consumer-prod"
                          />
                        </FormField>
                      </>
                    )}

                    {/* Transport-specific: WebSocket inbound */}
                    {connectionType === 'websocket_inbound' && (
                      <FormField
                        label="Allowed origin / SNI"
                        description="Expected origin header or TLS SNI value from the producer. CMS rejects connections that don't match."
                      >
                        <Input
                          value={wsAllowedOrigin}
                          onChange={({ detail }) => setWsAllowedOrigin(detail.value)}
                          placeholder="producer.example.com"
                        />
                      </FormField>
                    )}
                  </SpaceBetween>
                </Container>

                <Container header={<Header variant="h2">Authentication</Header>}>
                  <SpaceBetween size="l">
                    <FormField
                      label="Authentication type"
                      description="How the platform authenticates to the producer."
                    >
                      <Select
                        selectedOption={
                          AUTH_OPTIONS.find((o) => o.value === authType) ?? AUTH_OPTIONS[0]
                        }
                        options={AUTH_OPTIONS}
                        onChange={({ detail }) => setAuthType(detail.selectedOption.value as AuthType)}
                        expandToViewport
                      />
                    </FormField>

                    {/* Auth-specific: OAuth2 */}
                    {authType === 'oauth2' && (
                      <>
                        <FormField
                          label="Token endpoint"
                          description="OAuth2 token endpoint (client-credentials flow)."
                        >
                          <Input
                            value={tokenEndpoint}
                            onChange={({ detail }) => setTokenEndpoint(detail.value)}
                            placeholder="https://auth.example.com/oauth/token"
                          />
                        </FormField>
                        <FormField
                          label="Scopes (optional)"
                          description="Space-separated OAuth2 scopes requested with the token."
                        >
                          <Input
                            value={oauthScopes}
                            onChange={({ detail }) => setOauthScopes(detail.value)}
                            placeholder="fleet.read telemetry.read"
                          />
                        </FormField>
                      </>
                    )}

                    {/* Auth-specific: API key */}
                    {authType === 'api_key' && (
                      <FormField
                        label="Header name"
                        description="HTTP header that carries the key. The value comes from the Secrets Manager secret below."
                      >
                        <Input
                          value={apiKeyHeaderName}
                          onChange={({ detail }) => setApiKeyHeaderName(detail.value)}
                          placeholder="X-API-Key"
                        />
                      </FormField>
                    )}

                    {/* Auth-specific: mTLS */}
                    {authType === 'mtls' && (
                      <Alert type="info">
                        The Secrets Manager secret below must contain two fields:{' '}
                        <code>cert_pem</code> and <code>key_pem</code> (client certificate + private key).
                      </Alert>
                    )}

                    {/* Auth-specific: SASL/SCRAM */}
                    {authType === 'sasl_scram' && (
                      <>
                        <FormField
                          label="SCRAM mechanism"
                          description="Which SASL/SCRAM variant the producer's Kafka cluster requires."
                        >
                          <Select
                            selectedOption={{ label: scramMechanism, value: scramMechanism }}
                            options={[
                              { label: 'SCRAM-SHA-256', value: 'SCRAM-SHA-256' },
                              { label: 'SCRAM-SHA-512', value: 'SCRAM-SHA-512' },
                            ]}
                            onChange={({ detail }) =>
                              setScramMechanism(detail.selectedOption.value as 'SCRAM-SHA-256' | 'SCRAM-SHA-512')
                            }
                            expandToViewport
                          />
                        </FormField>
                        <Alert type="info">
                          The Secrets Manager secret below must contain <code>username</code> and{' '}
                          <code>password</code> fields.
                        </Alert>
                      </>
                    )}

                    <FormField
                      label="Credentials Secret ARN"
                      description={
                        <>
                          ARN of the AWS Secrets Manager secret holding the credentials. The
                          secret's value never leaves Secrets Manager — we just reference it here.
                          Shape depends on auth type (see notes above).
                        </>
                      }
                    >
                      <Input
                        value={credentialsSecretArn}
                        onChange={({ detail }) => setCredentialsSecretArn(detail.value)}
                        placeholder="arn:aws:secretsmanager:us-west-2:123456789012:secret/my-oem-credentials-abc123"
                      />
                    </FormField>
                  </SpaceBetween>
                </Container>
              </SpaceBetween>
            ),
            isOptional: false,
          },

          // ── Step 3: Signal mapping (transform manifest) ───────────────
          //
          // This is where the transform manifest comes to life. Producers
          // publish signals under their own names/paths, so CMS needs a
          // per-product mapping table to normalise them into the canonical
          // Signal Catalog. Rows added here are shown on the catalog
          // detail page; zero rows is a valid save (mappings can be added
          // later, and many demo products may only ever be stubbed out).
          {
            title: 'Signal & event mapping',
            description:
              "Pick pairs — CMS canonical signals + events on the left, the producer's advertised catalog on the right. Can be left empty and filled in later from the catalog detail page.",
            content: (
              <SpaceBetween size="l">
                <Alert type="info" statusIconAriaLabel="Info">
                  {producer.trim() ? (
                    <>
                      Every producer publishes signals + events under their own schema. Pick
                      which of <b>{producer.trim()}</b>'s advertised signals/events map to which
                      CMS canonical field. Auto-map suggests obvious matches by name similarity;
                      you can adjust every row.
                    </>
                  ) : (
                    <>
                      Go back to Basic info and fill in the <b>Producer</b> field first — the
                      mapping picker needs to know which producer's catalog to load.
                    </>
                  )}
                </Alert>

                {producer.trim() && !producerIsStubbed && (
                  <Alert type="info">
                    <b>{producer.trim()}</b> doesn't have a stubbed{' '}
                    <code>/available-signals</code> catalog in this demo (only Meridian, Ford,
                    and Tesla do). The pickers below are showing a <b>generic example
                    catalog</b> — realistic-shaped signal + event names so you can still
                    demonstrate the pick-and-match flow. In production, these would come from{' '}
                    {producer.trim()}'s own /available-signals + /available-events APIs.
                  </Alert>
                )}

                {/* Signal mapping — pick-and-match, grouped by CMS category. */}
                <Container
                  header={
                    <Header
                      variant="h2"
                      counter={`(${signalMappings.length} of ${CMS_CANONICAL_SIGNALS.length})`}
                      description={
                        producer.trim()
                          ? `CMS canonical signals ← ${producer.trim()}'s advertised signals. Pick a producer signal for each CMS row, or leave Unmapped.`
                          : 'Fill in the Producer field on step 1 to enable this picker.'
                      }
                      actions={
                        <Button
                          variant="primary"
                          onClick={autoMapSignals}
                          disabled={producerSignals.length === 0}
                          iconName="external"
                        >
                          Auto-map by name
                        </Button>
                      }
                    >
                      Signal mapping
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    {Object.keys(groupedCmsSignals)
                      .sort()
                      .map((group) => (
                        <Container
                          key={group}
                          header={
                            <Header
                              variant="h3"
                              counter={`(${groupedCmsSignals[group].filter((s) => signalMappingByCmsField.has(s.name)).length} of ${groupedCmsSignals[group].length})`}
                            >
                              {group.replace(/_/g, ' ')}
                            </Header>
                          }
                        >
                          <Table
                            variant="embedded"
                            items={groupedCmsSignals[group]}
                            columnDefinitions={[
                              {
                                id: 'cmsSignal',
                                header: 'CMS canonical signal',
                                cell: (s: CmsCanonicalSignal) => (
                                  <SpaceBetween direction="horizontal" size="xxs" alignItems="center">
                                    <Box fontFamily="monospace" fontWeight="bold">
                                      {s.name}
                                    </Box>
                                    {s.required && <Badge color="red">required</Badge>}
                                  </SpaceBetween>
                                ),
                                minWidth: 200,
                              },
                              {
                                id: 'cmsMeta',
                                header: 'Type / unit',
                                cell: (s: CmsCanonicalSignal) => (
                                  <SpaceBetween direction="horizontal" size="xxs">
                                    <Badge>{s.dataType}</Badge>
                                    {s.unit && <Box color="text-body-secondary">{s.unit}</Box>}
                                  </SpaceBetween>
                                ),
                                width: 130,
                              },
                              {
                                id: 'producer',
                                header: `${producer.trim() || 'Producer'} signal`,
                                cell: (s: CmsCanonicalSignal) => {
                                  const mapped = signalMappingByCmsField.get(s.name);
                                  const selected = mapped
                                    ? producerSignals.find((p) => p.name === mapped.sourceSignal)
                                    : undefined;
                                  return (
                                    <Select
                                      selectedOption={
                                        selected
                                          ? {
                                              label: selected.name,
                                              value: selected.name,
                                              description: selected.path,
                                            }
                                          : { label: 'Unmapped', value: '' }
                                      }
                                      options={[
                                        { label: 'Unmapped', value: '' },
                                        ...producerSignals.map((p) => ({
                                          label: p.name,
                                          value: p.name,
                                          description: p.path,
                                        })),
                                      ]}
                                      onChange={({ detail }) => {
                                        const val = detail.selectedOption.value;
                                        if (!val) return setSignalMapping(s, undefined);
                                        const p = producerSignals.find((x) => x.name === val);
                                        setSignalMapping(s, p);
                                      }}
                                      disabled={producerSignals.length === 0}
                                      placeholder="Pick a signal"
                                      expandToViewport
                                    />
                                  );
                                },
                                minWidth: 260,
                              },
                            ]}
                          />
                        </Container>
                      ))}
                  </SpaceBetween>
                </Container>

                {/* Event mapping — pick-and-match, grouped by CMS category. */}
                <Container
                  header={
                    <Header
                      variant="h2"
                      counter={`(${eventMappings.length} of ${CMS_CANONICAL_EVENTS.length})`}
                      description={
                        producer.trim()
                          ? `CMS canonical events ← ${producer.trim()}'s advertised events.`
                          : 'Fill in the Producer field on step 1 to enable this picker.'
                      }
                      actions={
                        <Button
                          variant="primary"
                          onClick={autoMapEvents}
                          disabled={producerEventsList.length === 0}
                          iconName="external"
                        >
                          Auto-map by name
                        </Button>
                      }
                    >
                      Event mapping
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    {Object.keys(groupedCmsEvents)
                      .sort()
                      .map((group) => (
                        <Container
                          key={group}
                          header={
                            <Header
                              variant="h3"
                              counter={`(${groupedCmsEvents[group].filter((e) => eventMappingByCmsEvent.has(e.name)).length} of ${groupedCmsEvents[group].length})`}
                            >
                              {group}
                            </Header>
                          }
                        >
                          <Table
                            variant="embedded"
                            items={groupedCmsEvents[group]}
                            columnDefinitions={[
                              {
                                id: 'cmsEvent',
                                header: 'CMS canonical event',
                                cell: (e: CmsCanonicalEvent) => (
                                  <Box fontFamily="monospace" fontWeight="bold">
                                    {e.name}
                                  </Box>
                                ),
                                minWidth: 200,
                              },
                              {
                                id: 'severity',
                                header: 'Severity',
                                cell: (e: CmsCanonicalEvent) => (
                                  <Badge
                                    color={
                                      e.severity === 'critical'
                                        ? 'red'
                                        : e.severity === 'warning'
                                        ? 'grey'
                                        : 'blue'
                                    }
                                  >
                                    {e.severity}
                                  </Badge>
                                ),
                                width: 110,
                              },
                              {
                                id: 'producerEvent',
                                header: `${producer.trim() || 'Producer'} event`,
                                cell: (e: CmsCanonicalEvent) => {
                                  const mapped = eventMappingByCmsEvent.get(e.name);
                                  const selected = mapped
                                    ? producerEventsList.find((p) => p.name === mapped.sourceEvent)
                                    : undefined;
                                  return (
                                    <Select
                                      selectedOption={
                                        selected
                                          ? {
                                              label: selected.name,
                                              value: selected.name,
                                              description: `severity: ${selected.severity}`,
                                            }
                                          : { label: 'Unmapped', value: '' }
                                      }
                                      options={[
                                        { label: 'Unmapped', value: '' },
                                        ...producerEventsList.map((p) => ({
                                          label: p.name,
                                          value: p.name,
                                          description: `severity: ${p.severity}`,
                                        })),
                                      ]}
                                      onChange={({ detail }) => {
                                        const val = detail.selectedOption.value;
                                        if (!val) return setEventMapping(e, undefined);
                                        const p = producerEventsList.find((x) => x.name === val);
                                        setEventMapping(e, p);
                                      }}
                                      disabled={producerEventsList.length === 0}
                                      placeholder="Pick an event"
                                      expandToViewport
                                    />
                                  );
                                },
                                minWidth: 260,
                              },
                            ]}
                          />
                        </Container>
                      ))}
                  </SpaceBetween>
                </Container>
              </SpaceBetween>
            ),
            isOptional: true,
          },

          // ── Step 4: Review ────────────────────────────────────────────
          {
            title: 'Review',
            description: 'Confirm the data product configuration.',
            content: (
              <SpaceBetween size="l">
                {!allValid && (
                  <Alert type="warning" header="Some fields are incomplete">
                    Go back and fill in the missing fields before submitting.
                  </Alert>
                )}
                <Container header={<Header variant="h2">Basic info</Header>}>
                  <ColumnLayout columns={2} variant="text-grid">
                    <Box>
                      <Box color="text-label" fontSize="body-s" fontWeight="bold">Product name</Box>
                      <Box>{productName || <Box color="text-status-inactive">(empty)</Box>}</Box>
                    </Box>
                    <Box>
                      <Box color="text-label" fontSize="body-s" fontWeight="bold">Producer</Box>
                      <Box>{producer || <Box color="text-status-inactive">(empty)</Box>}</Box>
                    </Box>
                    <Box>
                      <Box color="text-label" fontSize="body-s" fontWeight="bold">Description</Box>
                      <Box>{description || <Box color="text-status-inactive">(empty)</Box>}</Box>
                    </Box>
                  </ColumnLayout>
                </Container>
                <Container header={<Header variant="h2">Connection</Header>}>
                  <ColumnLayout columns={2} variant="text-grid">
                    <Box>
                      <Box color="text-label" fontSize="body-s" fontWeight="bold">Connection type</Box>
                      <Box><Badge color="green">{connectionType}</Badge></Box>
                    </Box>
                    <Box>
                      <Box color="text-label" fontSize="body-s" fontWeight="bold">Auth type</Box>
                      <Box><Badge color="grey">{authType}</Badge></Box>
                    </Box>
                    <Box>
                      <Box color="text-label" fontSize="body-s" fontWeight="bold">Endpoint URL</Box>
                      <Box>
                        <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12, wordBreak: 'break-all' }}>
                          {endpointUrl || '(empty)'}
                        </span>
                      </Box>
                    </Box>
                    {authType === 'oauth2' && (
                      <Box>
                        <Box color="text-label" fontSize="body-s" fontWeight="bold">Token endpoint</Box>
                        <Box>
                          <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12, wordBreak: 'break-all' }}>
                            {tokenEndpoint || '(empty)'}
                          </span>
                        </Box>
                      </Box>
                    )}
                    <Box>
                      <Box color="text-label" fontSize="body-s" fontWeight="bold">Credentials Secret ARN</Box>
                      <Box>
                        <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12, wordBreak: 'break-all' }}>
                          {credentialsSecretArn || '(empty)'}
                        </span>
                      </Box>
                    </Box>
                  </ColumnLayout>
                </Container>
                <Container
                  header={
                    <Header
                      variant="h2"
                      counter={`(${signalMappings.length} signals / ${eventMappings.length} events)`}
                    >
                      Signal & event mapping
                    </Header>
                  }
                >
                  {signalMappings.length === 0 && eventMappings.length === 0 ? (
                    <Alert type="info">
                      No signal or event mappings defined. The product will save with an empty
                      transform manifest; you can pick-and-match later from the catalog detail
                      page.
                    </Alert>
                  ) : (
                    <ColumnLayout columns={4} variant="text-grid">
                      <Box>
                        <Box color="text-label" fontSize="body-s" fontWeight="bold">
                          Signals mapped
                        </Box>
                        <Box>
                          {signalMappings.length} of {CMS_CANONICAL_SIGNALS.length}
                        </Box>
                      </Box>
                      <Box>
                        <Box color="text-label" fontSize="body-s" fontWeight="bold">
                          Required signals
                        </Box>
                        <Box>{signalMappings.filter((m) => m.required).length}</Box>
                      </Box>
                      <Box>
                        <Box color="text-label" fontSize="body-s" fontWeight="bold">
                          Events mapped
                        </Box>
                        <Box>
                          {eventMappings.length} of {CMS_CANONICAL_EVENTS.length}
                        </Box>
                      </Box>
                      <Box>
                        <Box color="text-label" fontSize="body-s" fontWeight="bold">
                          Producer
                        </Box>
                        <Box>
                          {producer.trim() ? (
                            <Badge color="green">{producer.trim()}</Badge>
                          ) : (
                            <Box color="text-status-inactive">(not set)</Box>
                          )}
                        </Box>
                      </Box>
                    </ColumnLayout>
                  )}
                </Container>
              </SpaceBetween>
            ),
            isOptional: false,
          },
        ]}
      />
    </Modal>
  );
};

export default AddDataProductWizard;
