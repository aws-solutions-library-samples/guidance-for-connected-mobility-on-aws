import React, { useState, useCallback } from 'react';
import {
  Wizard, FormField, Input, Select, SpaceBetween, Container, Header,
  Table, Box, Button, FileUpload, Alert, Textarea, ColumnLayout, Badge
} from '@cloudscape-design/components';
import { getDataProcessingApiEndpoint } from '../../config/api';
import { authFetch } from '../../utils/authFetch';

const API = () => getDataProcessingApiEndpoint().replace(/\/$/, '');

interface ParsedSignal {
  fullyQualifiedName: string;
  messageId: number;
  startBit: number;
  length: number;
  factor: number;
  offset: number;
  isSigned: boolean;
  isBigEndian: boolean;
  messageName: string;
}

interface Props {
  onDismiss: () => void;
  onCreated: () => void;
}

// Lightweight DBC parser — extracts messages + signals
function parseDbc(content: string): ParsedSignal[] {
  const signals: ParsedSignal[] = [];
  const lines = content.split('\n');
  let currentMsg: { id: number; name: string } | null = null;

  for (const line of lines) {
    const trimmed = line.trim();

    // BO_ <id> <name>: <dlc> <transmitter>
    const msgMatch = trimmed.match(/^BO_\s+(\d+)\s+(\w+)\s*:/);
    if (msgMatch) {
      currentMsg = { id: parseInt(msgMatch[1], 10), name: msgMatch[2] };
      continue;
    }

    // SG_ <name> : <startBit>|<length>@<byteOrder><sign> (<factor>,<offset>) [<min>|<max>] "<unit>" <receivers>
    const sigMatch = trimmed.match(
      /^SG_\s+(\w+)\s*(?:(\w+)\s+)?:\s*(\d+)\|(\d+)@([01])([+-])\s*\(([^,]+),([^)]+)\)/
    );
    if (sigMatch && currentMsg) {
      const byteOrder = sigMatch[5]; // 0 = big endian (Motorola), 1 = little endian (Intel)
      const sign = sigMatch[6];      // + = unsigned, - = signed
      signals.push({
        fullyQualifiedName: `Vehicle.${sigMatch[1]}`,
        messageId: currentMsg.id,
        startBit: parseInt(sigMatch[3], 10),
        length: parseInt(sigMatch[4], 10),
        factor: parseFloat(sigMatch[7]),
        offset: parseFloat(sigMatch[8]),
        isSigned: sign === '-',
        isBigEndian: byteOrder === '0',
        messageName: currentMsg.name,
      });
    }

    // Empty line or new section resets current message context
    if (trimmed === '' || /^(CM_|BA_|VAL_|SIG_|EV_|BO_TX)/.test(trimmed)) {
      currentMsg = null;
    }
  }
  return signals.sort((a, b) => a.fullyQualifiedName.localeCompare(b.fullyQualifiedName));
}

function generateId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(12)),
    b => b.toString(36)).join('').slice(0, 21);
}

const CreateDecoderManifestWizard: React.FC<Props> = ({ onDismiss, onCreated }) => {
  const [activeStep, setActiveStep] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  // Step 1: Manifest info
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  // Step 2: Network interface
  const [interfaceName, setInterfaceName] = useState('vcan0');
  const [interfaceId] = useState(generateId());
  const [interfaceType] = useState({ label: 'CAN_INTERFACE', value: 'CAN_INTERFACE' });
  const [protocolName] = useState('CAN');
  const [protocolVersion] = useState('2.0A');

  // Step 3: DBC upload + signals
  const [dbcFiles, setDbcFiles] = useState<File[]>([]);
  const [parsedSignals, setParsedSignals] = useState<ParsedSignal[]>([]);
  const [parseError, setParseError] = useState('');

  const handleDbcUpload = useCallback((files: File[]) => {
    setDbcFiles(files);
    setParseError('');
    setParsedSignals([]);
    if (!files.length) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const content = e.target?.result as string;
        const sigs = parseDbc(content);
        if (!sigs.length) {
          setParseError('No signals found in DBC file. Verify the file format.');
          return;
        }
        setParsedSignals(sigs);
      } catch (err: any) {
        setParseError(`Failed to parse DBC: ${err.message}`);
      }
    };
    reader.readAsText(files[0]);
  }, []);

  const handleSubmit = async () => {
    setSubmitting(true);
    setError('');
    try {
      const body = {
        name,
        description,
        networkInterfaces: [{
          interfaceId,
          type: 'CAN_INTERFACE',
          canInterface: {
            canInterfaceName: interfaceName,
            protocolName,
            protocolVersion,
          }
        }],
        signalDecoders: parsedSignals.map(s => ({
          fullyQualifiedName: s.fullyQualifiedName,
          type: 'CAN_SIGNAL_DECODER',
          interfaceId,
          canSignal: {
            messageId: s.messageId,
            startBit: s.startBit,
            length: s.length,
            factor: s.factor,
            offset: s.offset,
            isSigned: s.isSigned,
            isBigEndian: s.isBigEndian,
          }
        }))
      };

      const res = await authFetch(`${API()}/decoder-manifests`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Create failed');
      onCreated();
    } catch (err: any) {
      setError(err.message);
      setSubmitting(false);
    }
  };

  const uniqueMessages = [...new Set(parsedSignals.map(s => s.messageName))];

  return (
    <Wizard
      i18nStrings={{
        stepNumberLabel: n => `Step ${n}`,
        collapsedStepsLabel: (step, total) => `Step ${step} of ${total}`,
        submitButton: 'Create decoder manifest',
        previousButton: 'Previous',
        nextButton: 'Next',
        cancelButton: 'Cancel',
        optional: 'optional',
      }}
      activeStepIndex={activeStep}
      onNavigate={({ detail }) => setActiveStep(detail.requestedStepIndex)}
      onCancel={onDismiss}
      onSubmit={handleSubmit}
      isLoadingNextStep={submitting}
      steps={[
        {
          title: 'Decoder manifest details',
          description: 'A decoder manifest maps raw CAN bus signals to human-readable vehicle signal names.',
          content: (
            <Container header={<Header variant="h2">Manifest information</Header>}>
              <SpaceBetween size="l">
                <FormField label="Name" description="A unique name for this decoder manifest (1-100 characters)."
                  errorText={!name && activeStep > 0 ? 'Name is required' : ''}>
                  <Input value={name} onChange={({ detail }) => setName(detail.value)}
                    placeholder="e.g. my-fleet-decoder-v1" />
                </FormField>
                <FormField label="Description" description="Optional description of this decoder manifest.">
                  <Textarea value={description} onChange={({ detail }) => setDescription(detail.value)}
                    placeholder="e.g. CAN decoder for Model X 2024 fleet" rows={2} />
                </FormField>
              </SpaceBetween>
            </Container>
          ),
          isOptional: false,
        },
        {
          title: 'Network interface',
          description: 'Define the CAN network interface that connects signals to the vehicle bus.',
          content: (
            <Container header={<Header variant="h2">Network interface configuration</Header>}>
              <SpaceBetween size="l">
                <ColumnLayout columns={2}>
                  <FormField label="Network interface type">
                    <Select selectedOption={interfaceType} options={[
                      { label: 'CAN_INTERFACE', value: 'CAN_INTERFACE' },
                      { label: 'OBD_INTERFACE', value: 'OBD_INTERFACE' },
                    ]} disabled />
                  </FormField>
                  <FormField label="Network interface ID" description="Auto-generated unique identifier.">
                    <Input value={interfaceId} disabled />
                  </FormField>
                </ColumnLayout>
                <ColumnLayout columns={2}>
                  <FormField label="Network interface name" description="The CAN interface name on the vehicle (1-50 characters).">
                    <Input value={interfaceName} onChange={({ detail }) => setInterfaceName(detail.value)}
                      placeholder="vcan0" />
                  </FormField>
                  <FormField label="Protocol">
                    <Input value={`${protocolName} ${protocolVersion}`} disabled />
                  </FormField>
                </ColumnLayout>
              </SpaceBetween>
            </Container>
          ),
        },
        {
          title: 'Signals',
          description: 'Upload a DBC file to import CAN signal definitions, or add signals manually.',
          content: (
            <SpaceBetween size="l">
              <Container header={<Header variant="h2">Import signals from DBC file</Header>}>
                <SpaceBetween size="m">
                  <FormField label="DBC file" description="Upload a CAN database (.dbc) file to automatically extract signal definitions.">
                    <FileUpload
                      value={dbcFiles}
                      onChange={({ detail }) => handleDbcUpload(detail.value)}
                      accept=".dbc"
                      i18nStrings={{
                        uploadButtonText: e => e ? 'Choose different file' : 'Choose DBC file',
                        dropzoneText: e => e ? 'Drop file to replace' : 'Drop DBC file here',
                        removeFileAriaLabel: e => `Remove file ${e + 1}`,
                        limitShowFewer: 'Show fewer',
                        limitShowMore: 'Show more',
                        errorIconAriaLabel: 'Error',
                      }}
                      constraintText="Accepted format: .dbc (CAN database)"
                      showFileSize
                      showFileThumbnail={false}
                    />
                  </FormField>
                  {parseError && <Alert type="error">{parseError}</Alert>}
                  {parsedSignals.length > 0 && (
                    <Alert type="success">
                      Parsed {parsedSignals.length} signals from {uniqueMessages.length} CAN messages
                    </Alert>
                  )}
                </SpaceBetween>
              </Container>

              {parsedSignals.length > 0 && (
                <Container header={
                  <Header variant="h2" counter={`(${parsedSignals.length})`}
                    description={`${uniqueMessages.length} CAN messages`}>
                    Parsed signals
                  </Header>
                }>
                  <Table
                    columnDefinitions={[
                      { id: 'fqn', header: 'Signal Name', cell: (item: ParsedSignal) => item.fullyQualifiedName, sortingField: 'fullyQualifiedName' },
                      { id: 'msg', header: 'Message', cell: (item: ParsedSignal) => (
                        <SpaceBetween direction="horizontal" size="xs">
                          <span>{item.messageName}</span>
                          <Badge color="blue">{`0x${item.messageId.toString(16).toUpperCase()}`}</Badge>
                        </SpaceBetween>
                      )},
                      { id: 'startBit', header: 'Start Bit', cell: (item: ParsedSignal) => item.startBit },
                      { id: 'length', header: 'Length', cell: (item: ParsedSignal) => item.length },
                      { id: 'factor', header: 'Factor', cell: (item: ParsedSignal) => item.factor },
                      { id: 'offset', header: 'Offset', cell: (item: ParsedSignal) => item.offset },
                      { id: 'endian', header: 'Byte Order', cell: (item: ParsedSignal) => item.isBigEndian ? 'Big Endian' : 'Little Endian' },
                      { id: 'signed', header: 'Signed', cell: (item: ParsedSignal) => item.isSigned ? 'Yes' : 'No' },
                    ]}
                    items={parsedSignals}
                    variant="embedded"
                    stickyHeader
                    empty={<Box textAlign="center">No signals parsed</Box>}
                  />
                </Container>
              )}
              {error && <Alert type="error">{error}</Alert>}
            </SpaceBetween>
          ),
        },
        {
          title: 'Review and create',
          content: (
            <SpaceBetween size="l">
              <Container header={<Header variant="h2">Review</Header>}>
                <ColumnLayout columns={2} variant="text-grid">
                  <div>
                    <Box variant="awsui-key-label">Name</Box>
                    <div>{name}</div>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Description</Box>
                    <div>{description || '—'}</div>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Network interface</Box>
                    <div>{interfaceName} ({interfaceType.value})</div>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Interface ID</Box>
                    <div>{interfaceId}</div>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Signals</Box>
                    <div>{parsedSignals.length} signals from {uniqueMessages.length} CAN messages</div>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">DBC file</Box>
                    <div>{dbcFiles[0]?.name || '—'}</div>
                  </div>
                </ColumnLayout>
              </Container>
              {error && <Alert type="error">{error}</Alert>}
              {!name && <Alert type="warning">Name is required</Alert>}
              {!parsedSignals.length && <Alert type="warning">No signals — upload a DBC file in the previous step</Alert>}
            </SpaceBetween>
          ),
        },
      ]}
    />
  );
};

export default CreateDecoderManifestWizard;
