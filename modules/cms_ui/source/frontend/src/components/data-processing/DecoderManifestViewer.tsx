import React, { useState, useEffect } from 'react';
import { Table, Box, Header, Button, SpaceBetween, StatusIndicator, Modal, ExpandableSection } from '@cloudscape-design/components';

const API = 'https://sel11tei2c.execute-api.us-east-1.amazonaws.com/prod';

interface DecoderManifest {
  decoderManifestName: string;
  decoderManifestVersion: string;
  status: string;
  modelName: string;
  description: string;
  createTimestamp: string;
}

interface SignalDecoder {
  fullyQualifiedName: string;
  signalDecoderType: string;
  interfaceId: string;
  signalDecoderPayloadType: string;
  hasPayload: boolean;
  signalDecoderPayload?: any;
}

interface NetworkInterface {
  interfaceId: string;
  networkInterfaceType: string;
  networkInterfacePayload: string;
}

const DecoderManifestViewer: React.FC = () => {
  const [manifests, setManifests] = useState<DecoderManifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<DecoderManifest | null>(null);
  const [signals, setSignals] = useState<SignalDecoder[]>([]);
  const [interfaces, setInterfaces] = useState<NetworkInterface[]>([]);
  const [signalsLoading, setSignalsLoading] = useState(false);
  const [showDetail, setShowDetail] = useState(false);

  const loadManifests = () => {
    setLoading(true);
    fetch(`${API}/decoder-manifest`)
      .then(res => res.json())
      .then(data => { setManifests(data.manifests || []); setLoading(false); })
      .catch(() => setLoading(false));
  };

  useEffect(() => { loadManifests(); }, []);

  const viewManifest = (manifest: DecoderManifest) => {
    setSelected(manifest);
    setShowDetail(true);
    setSignalsLoading(true);
    Promise.all([
      fetch(`${API}/decoder-manifest/signals?name=${manifest.decoderManifestName}&include_payload=true`).then(r => r.json()),
      fetch(`${API}/decoder-manifest/network-interfaces?name=${manifest.decoderManifestName}`).then(r => r.json()),
    ]).then(([sigData, ifData]) => {
      setSignals(sigData.signals || []);
      setInterfaces(ifData.networkInterfaces || []);
      setSignalsLoading(false);
    }).catch(() => setSignalsLoading(false));
  };

  return (
    <>
      <Table
        loading={loading}
        header={
          <Header counter={`(${manifests.length})`}
            actions={<Button iconName="refresh" onClick={loadManifests}>Refresh</Button>}
          >Decoder Manifests</Header>
        }
        columnDefinitions={[
          { id: 'name', header: 'Name', cell: (item: DecoderManifest) =>
            <Button variant="link" onClick={() => viewManifest(item)}>{item.decoderManifestName}</Button>
          },
          { id: 'version', header: 'Version', cell: (item: DecoderManifest) => item.decoderManifestVersion },
          { id: 'status', header: 'Status', cell: (item: DecoderManifest) =>
            <StatusIndicator type={item.status === 'ACTIVE' ? 'success' : 'info'}>{item.status}</StatusIndicator>
          },
          { id: 'model', header: 'Model', cell: (item: DecoderManifest) => item.modelName },
          { id: 'desc', header: 'Description', cell: (item: DecoderManifest) => item.description },
        ]}
        items={manifests}
        empty={<Box textAlign="center"><b>No decoder manifests</b></Box>}
      />

      <Modal visible={showDetail} onDismiss={() => setShowDetail(false)}
        header={selected?.decoderManifestName} size="max">
        <SpaceBetween size="l">
          <ExpandableSection headerText={`Network Interfaces (${interfaces.length})`} defaultExpanded>
            <Table loading={signalsLoading}
              columnDefinitions={[
                { id: 'id', header: 'Interface ID', cell: (item: NetworkInterface) => item.interfaceId },
                { id: 'type', header: 'Type', cell: (item: NetworkInterface) => item.networkInterfaceType },
                { id: 'payload', header: 'Config', cell: (item: NetworkInterface) => {
                  try { const p = JSON.parse(item.networkInterfacePayload); return `${p.canInterfaceName} (${p.protocolName} ${p.protocolVersion})`; }
                  catch { return item.networkInterfacePayload; }
                }},
              ]}
              items={interfaces}
              empty={<Box textAlign="center">No network interfaces</Box>}
            />
          </ExpandableSection>

          <ExpandableSection headerText={`Signals (${signals.length})`} defaultExpanded>
            <Table loading={signalsLoading}
              columnDefinitions={[
                { id: 'fqn', header: 'Fully Qualified Name', cell: (item: SignalDecoder) => item.fullyQualifiedName, sortingField: 'fullyQualifiedName' },
                { id: 'type', header: 'Decoder Type', cell: (item: SignalDecoder) => item.signalDecoderType },
                { id: 'iface', header: 'Interface', cell: (item: SignalDecoder) => item.interfaceId },
                { id: 'msgId', header: 'Message ID', cell: (item: SignalDecoder) => item.signalDecoderPayload ? `0x${item.signalDecoderPayload.messageId.toString(16).toUpperCase()}` : '-' },
                { id: 'startBit', header: 'Start Bit', cell: (item: SignalDecoder) => item.signalDecoderPayload?.startBit ?? '-' },
                { id: 'length', header: 'Length', cell: (item: SignalDecoder) => item.signalDecoderPayload?.length ?? '-' },
                { id: 'factor', header: 'Factor', cell: (item: SignalDecoder) => item.signalDecoderPayload?.factor ?? '-' },
                { id: 'offset', header: 'Offset', cell: (item: SignalDecoder) => item.signalDecoderPayload?.offset ?? '-' },
                { id: 'endian', header: 'Big Endian', cell: (item: SignalDecoder) => item.signalDecoderPayload ? (item.signalDecoderPayload.isBigEndian ? 'Yes' : 'No') : '-' },
              ]}
              items={signals}
              sortingDisabled={false}
              empty={<Box textAlign="center"><b>No signals</b><br/>Import signal catalog</Box>}
            />
          </ExpandableSection>
        </SpaceBetween>
      </Modal>
    </>
  );
};

export default DecoderManifestViewer;
